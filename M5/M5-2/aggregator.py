"""M5-2 聚合指标引擎(纯 Python,无 ROS / 无第三方依赖)。

职责:把原始话题流降频聚合成"关键指标 + 故障事件"。
设计要点:小车端算力宝贵,原始数据不外传,只传算好的摘要 —— 这就是
"数据降频聚合"(题目核心考点之一)。
"""
import time
from collections import deque

# 每个话题的期望频率(Hz)。真实部署时由参数配置,零硬编码。
DEFAULT_TOPICS = {
    "/perception/targets": 10.0,
    "/navigation/pose": 2.0,
    "/referee/status": 1.0,
}

WINDOW = 3.0            # 指标统计窗口(秒)
FAULT_RULES = {
    # 只有"收到过数据后频率骤降"才算相机故障;从未收到过 = 未知,不误报
    "CAMERA_DEGRADED": lambda m: m["topics"]["/perception/targets"]["ever_received"]
                and m["topics"]["/perception/targets"]["freq"]
                < m["topics"]["/perception/targets"]["expected_hz"] * 0.5,
    "LOW_BATTERY": lambda m: m["battery"] < 11.5,
    "CPU_OVERLOAD": lambda m: m["cpu"] > 90.0,
    "THERMAL_WARNING": lambda m: m["temp"] > 85.0,
}


class TopicStat:
    """单个话题的滑动窗口统计。"""

    def __init__(self, expected_hz):
        self.expected_hz = expected_hz
        self.window = deque()           # (recv_time, stamp)
        self.confs = deque()            # 置信度样本
        self.recv_count = 0             # 累计收到数(区分"没数据"和"断了")

    def on_msg(self, now, stamp, conf=None):
        self.window.append((now, stamp))
        self.recv_count += 1
        if conf is not None:
            self.confs.append(conf)

    def stats(self, now):
        """丢弃窗口外样本后计算:频率/延迟/丢包率/置信度均值。"""
        while self.window and now - self.window[0][0] > WINDOW:
            self.window.popleft()
        while self.confs and len(self.confs) > len(self.window) + 100:
            self.confs.popleft()
        n = len(self.window)
        freq = n / WINDOW if n else 0.0
        latency = (now - self.window[-1][1]) if self.window else None
        expected = self.expected_hz * WINDOW
        loss = max(0.0, 1.0 - n / expected) if expected > 0 else 0.0
        conf = (sum(self.confs) / len(self.confs)) if self.confs else None
        return {"freq": round(freq, 2), "expected_hz": self.expected_hz,
                "latency": (round(latency, 3) if latency is not None else None),
                "loss": round(loss, 3),
                "confidence": (round(conf, 3) if conf is not None else None),
                "ever_received": self.recv_count > 0}


class Aggregator:
    """聚合器:喂话题消息 -> 周期性产出指标快照 + 故障事件。"""

    def __init__(self, topics=None):
        self.topics = {name: TopicStat(hz)
                       for name, hz in (topics or DEFAULT_TOPICS).items()}
        self.battery = 12.0
        self.cpu = 20.0
        self.temp = 45.0
        self.faults = {}                # type -> fault 记录
        self.event_log = []             # 故障事件流(只追加)

    # ---------- 数据入口 ----------
    def on_topic(self, name, stamp=None, conf=None):
        stat = self.topics.get(name)
        if stat is None:                # 未注册话题自动发现
            stat = self.topics[name] = TopicStat(1.0)
        now = time.monotonic()
        stat.on_msg(now, stamp if stamp is not None else now, conf)

    def on_hw(self, battery=None, cpu=None, temp=None):
        if battery is not None:
            self.battery = battery
        if cpu is not None:
            self.cpu = cpu
        if temp is not None:
            self.temp = temp

    # ---------- 指标与故障 ----------
    def metrics(self):
        now = time.monotonic()
        return {
            "battery": round(self.battery, 2),
            "cpu": round(self.cpu, 1),
            "temp": round(self.temp, 1),
            "topics": {name: stat.stats(now)
                       for name, stat in self.topics.items()},
        }

    def _check_faults(self):
        """按规则判定故障激活/自愈,事件流只追加。"""
        active_now = set()
        m = self.metrics()
        for ftype, rule in FAULT_RULES.items():
            try:
                if rule(m):
                    active_now.add(ftype)
            except (TypeError, KeyError, ZeroDivisionError):
                pass
        now = time.monotonic()
        for ftype in set(self.faults) | active_now:
            rec = self.faults.setdefault(ftype, {
                "type": ftype, "first_seen": None, "last_seen": None,
                "duration": 0.0, "active": False, "self_healed": False})
            if ftype in active_now and not rec["active"]:
                rec["active"] = True
                rec["first_seen"] = now
                self.event_log.append({"t": round(now, 2), "event": "FAULT",
                                       "type": ftype})
            elif ftype not in active_now and rec["active"]:
                rec["active"] = False
                rec["self_healed"] = True
                rec["duration"] = round(now - rec["first_seen"], 2)
                self.event_log.append({"t": round(now, 2),
                                       "event": "HEALED", "type": ftype,
                                       "duration": rec["duration"]})
            if rec["active"]:
                rec["last_seen"] = now
                rec["duration"] = round(now - rec["first_seen"], 2)
        return [f for f in self.faults.values() if f["active"]]

    def snapshot(self, task_state=None, referee=None, system_ready=True):
        """一张完整仪表盘快照(10Hz 推给浏览器的就是它)。"""
        return {
            "ts": round(time.monotonic(), 2),
            "metrics": self.metrics(),
            "active_faults": self._check_faults(),
            "event_log": self.event_log[-50:],
            "task_state": task_state or {},
            "referee": referee or {},
            "system_ready": system_ready,
        }
