"""M5-3 操作员终端核心:把六个部件拧成一个 10Hz 快照。

       裁判指令(话题/键盘/HTTP)
                 │
        referee_bridge ── 状态发布 ──┬─→ 状态机(race_bridge)
                 │                    └─→ 审计 + 界面
       操作员(Web/语音/按键) ─→ 本类 do_action() ─→ 热切/急停/复位
                 │
       看门狗(相机断了) ─→ 降级 + 语音播报 + 审计
"""
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))       # M5-3/host_side
_ROOT = os.path.dirname(_HERE)                           # M5-3
_M5 = os.path.dirname(_ROOT)                             # M5
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_ROOT, "robot_side"))                  # 急停节点
sys.path.insert(0, os.path.join(_M5, "M5-2", "host_side"))             # mock_source
sys.path.insert(0, os.path.join(_M5, "M5-2", "robot_side"))            # 聚合器
sys.path.insert(0, os.path.join(_M5, "M5-1"))                          # task_fsm

from dashboard_aggregator import Aggregator            # noqa: E402
from audit import AuditLog                             # noqa: E402
from emergency_stop_node import CarSideStub            # noqa: E402
from hot_switch import HotSwapManager                  # noqa: E402
from mock_source import CarSim                         # noqa: E402
from race_bridge import RaceBridge                     # noqa: E402
from referee_bridge import (FAULT, PAUSED, READY,      # noqa: E402
                            RUNNING, STOPPED, RefereeBridge)
from safety import layered_estop                       # noqa: E402
from task_fsm import build_report                      # noqa: E402
from watchdog import Watchdog                          # noqa: E402

TICK = 0.1

# 语音/物理按键 -> 动作(离线唤醒词场景:只做关键词匹配)
VOICE_ACTIONS = [
    (("开始", "发车"), "start"),
    (("暂停",), "pause"),
    (("继续", "恢复"), "resume"),
    (("停车", "急停", "停下"), "estop"),
    (("求助", "人工"), "help"),
    (("复位", "重置"), "soft_reset"),
]

# 物理按键(GPIO/USB HID 上报的键码)-> 动作
BUTTON_ACTIONS = {
    "BTN_START": "start",
    "BTN_PAUSE": "pause",
    "BTN_RESUME": "resume",
    "BTN_ESTOP": "estop",
    "BTN_HELP": "help",
}


class TerminalApp:
    def __init__(self, mission_path, *, time_scale=4.0, race_seconds=180.0,
                 audit_path=None, switch_log=None, blackbox=None,
                 enable_tts=True, state_file=None):
        self.sim = CarSim()
        self.agg = Aggregator()
        self.referee = RefereeBridge(race_seconds=race_seconds, prestart=3.0)
        self.bridge = RaceBridge(mission_path, self.sim, time_scale=time_scale)
        self.hotswap = HotSwapManager(log_path=switch_log)
        self.audit = AuditLog(path=audit_path)
        self.car = CarSideStub(blackbox_path=blackbox)
        self.watchdog = Watchdog(degrade_after=1.0)
        self.enable_tts = enable_tts
        # 报告落盘路径(M5-1 的 report 结构,赛后直接看)
        self.state_file = state_file or os.path.join(_HERE, "task_report.json")

        self.events = deque(maxlen=200)
        self.history = deque(maxlen=1800)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._faults = []
        self._hs_cache = self.hotswap.state()
        self._verify = self.audit.verify()
        self._last_verify = 0.0
        self._last_voice = ""
        self._wire()

    # ---------- 事件接线 ----------
    def _wire(self):
        self.referee.on("start", self._on_start)
        self.referee.on("pause", self._on_pause)
        self.referee.on("resume", self._on_resume)
        self.referee.on("stop", lambda p: self._stop_race(p, "REFEREE_STOP"))
        self.referee.on("timeup", lambda p: self._stop_race(p, "MISSION_TIMEUP"))
        self.referee.on("penalty", self._on_penalty)

    def _on_start(self, payload):
        started = self.bridge.start_mission()
        self.audit.append("MISSION_START", {"started": started},
                          source=payload.get("source", "referee"))
        self.announce("比赛开始")

    def _on_pause(self, payload):
        self.bridge.pause()
        self.audit.append("TASK_PAUSE", {}, source=payload.get("source",
                                                             "operator"))
        self.announce("任务已暂停")

    def _on_resume(self, payload):
        self.bridge.resume()
        self.audit.append("TASK_RESUME", {}, source=payload.get("source",
                                                              "operator"))
        self.announce("任务继续")

    def _stop_race(self, payload, op):
        self.bridge.pause()
        self.audit.append(op, {}, source=payload.get("source", "referee"))
        self.announce("比赛结束")

    def _on_penalty(self, payload):
        self.audit.append("PENALTY", {"seconds": payload.get("seconds"),
                                      "fouls": payload.get("fouls")},
                          source=payload.get("source", "referee"))

    # ---------- 主循环 ----------
    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.is_set():
            self.sim.tick(TICK)
            for topic, stamp, conf in self.sim.messages():
                self.agg.on_topic(topic, stamp=stamp, conf=conf)
            self.agg.on_hw(battery=self.sim.battery, cpu=self.sim.cpu,
                           temp=self.sim.temp)
            self.referee.tick(TICK)
            self._faults = self.agg._check_faults()
            self._feed_watchdog()
            snap = self.snapshot()
            with self._lock:
                self.history.append(snap)
            if time.monotonic() - self._last_verify > 2.0:
                self._verify = self.audit.verify()
                self._last_verify = time.monotonic()
            self._stop.wait(TICK)

    def _feed_watchdog(self):
        """看门狗:连续 1s 感知频率低于期望 50% -> 判视觉降级。"""
        stat = self.agg.metrics()["topics"].get("/perception/targets")
        bad = bool(stat and stat["ever_received"]
                   and stat["freq"] < stat["expected_hz"] * 0.5)
        ev = self.watchdog.feed(bad, time.monotonic())
        if not ev:
            return
        if ev["event"] == "DEGRADED":
            self.audit.append("WATCHDOG_DEGRADE",
                              {"after_s": ev["after_s"],
                               "action": "fallback_to_lidar"},
                              source="watchdog")
            self.announce(ev["text"])
        else:
            self.audit.append("WATCHDOG_RECOVER", {}, source="watchdog")
            self.announce(ev["text"])

    # ---------- 快照 ----------
    def snapshot(self):
        with self._lock:
            events = list(self.events)[-30:]
        return {
            "ts": round(time.monotonic(), 2),
            "metrics": self.agg.metrics(),
            "active_faults": self._faults,
            "task_state": self.bridge.task_state(),
            "referee": self.referee.status(),
            "system_ready": self.sim.system_ready and not self.sim.estopped,
            "terminal": {
                "paused": self.bridge.paused,
                "watchdog": self.watchdog.state(),
                "hotswap": self._hs_cache,
                "audit": {"count": self.audit.seq,
                          "ok": self._verify["ok"],
                          "message": self._verify["message"],
                          "tail": self.audit.tail(12)},
                "events": events,
                "estop": {"active": self.sim.estopped,
                          "count": self.car.estop_count,
                          "blackbox": self.car.blackbox_tail(3)},
                "faults": dict(self.sim.faults),
                "voice": self._last_voice,
            },
        }

    def latest(self):
        """当前状态 -> 实时生成,不读 history 缓存。

        history 是 10Hz 主循环写的缓存,最多滞后一个 TICK(0.1s);急停、裁判
        判罚这类瞬时事件必须立刻对外可见,否则 HTTP 快照与内存状态不一致。
        history 保留给趋势回放用,不作为"当前值"的来源。
        """
        return self.snapshot()

    def announce(self, text):
        """语音播报:写事件流 + 尽力调用系统 TTS(没有就静默降级)。"""
        self._last_voice = text
        self.events.append({"ts": datetime.now().strftime("%H:%M:%S"),
                            "level": "voice", "text": text})
        if not self.enable_tts:
            return
        exe = shutil.which("espeak") or shutil.which("espeak-ng")
        if not exe:
            return
        try:
            subprocess.Popen([exe, "-v", "cmn", text],
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        except OSError:
            pass

    def note(self, text, level="info"):
        self.events.append({"ts": datetime.now().strftime("%H:%M:%S"),
                            "level": level, "text": text})

    # ---------- 操作入口(Web/语音/按键共用) ----------
    def do_action(self, action, payload=None, source="operator"):
        payload = payload or {}
        handler = getattr(self, f"_act_{action}", None)
        if handler is None:
            raise ValueError(f"未知操作: {action}")
        return handler(payload, source)

    # 任务控制
    def _act_start(self, payload, source):
        if self.referee.state in (READY, STOPPED):
            r = self.referee.feed("START", source=source)
        elif self.referee.state == RUNNING and not self.bridge.is_running():
            self.bridge.start_mission()
            r = {"result": "直接启动任务"}
        else:
            raise ValueError(f"当前状态 {self.referee.state} 不能启动")
        return {"ok": True, "action": "start", **r}

    def _act_pause(self, payload, source):
        return {"ok": True, **self.referee.feed("PAUSE", source=source)}

    def _act_resume(self, payload, source):
        return {"ok": True, **self.referee.feed("RESUME", source=source)}

    def _act_stop(self, payload, source):
        return {"ok": True, **self.referee.feed("STOP", source=source)}

    def _act_skip(self, payload, source):
        name = self.bridge.skip_current()
        if not name:
            raise ValueError("当前没有正在执行的节点可跳过")
        node = next((n for n in self.bridge.machine.nodes
                     if n.name == name), None)
        self.audit.append("SKIP_NODE",
                          {"node": name,
                           "fallback": node.fallback if node else None},
                          source=source)
        self.note(f"操作员跳过节点 {name}", "warn")
        return {"ok": True, "skipped": name,
                "fallback": node.fallback if node else None}

    def _act_estop(self, payload, source):
        return self._estop(source, payload.get("reason", "manual"))

    def _estop(self, source, reason="manual"):
        """四层急停见 host_side/safety.py(指令/话题/控制器/记录)。"""
        cmd = layered_estop(self.sim, self.bridge, self.car, self.referee,
                            self.audit, self.announce, source, reason)
        return {"ok": True, "action": "estop", "cmd_vel": cmd}

    def _act_help(self, payload, source):
        self.referee.set_fault("求助裁判")
        self.audit.append("HELP", {"channel": "/referee/help"}, source=source)
        self.announce("请求人工介入")
        return {"ok": True, "action": "help"}

    def _act_soft_reset(self, payload, source):
        self.bridge.restart()
        self.referee.reset_race()
        self.audit.append("SOFT_RESET",
                          {"node_pid_unchanged": True,
                           "reloads": self.hotswap.reloads}, source=source)
        self.note("软复位完成:状态机上下文重建,节点未重启", "warn")
        return {"ok": True, "action": "soft_reset"}

    def _act_hard_reset(self, payload, source):
        if self.referee.state in (RUNNING, PAUSED):
            raise ValueError("比赛中禁止硬复位(题面要求)")
        self.bridge.restart()
        self.referee.reset_race()
        self.sim.__init__(seed=42)          # 全栈重建(仅赛前/赛后)
        self.agg = Aggregator()
        self.audit.append("HARD_RESET", {"full_stack": True}, source=source)
        self.note("硬复位完成:全栈重建", "warn")
        return {"ok": True, "action": "hard_reset"}

    # 热切换
    def _act_switch(self, payload, source):
        kind = payload.get("kind")
        name = payload.get("name")
        value = payload.get("value")
        result = self.hotswap.apply(kind, name, value,
                                    operator=self.audit.operator,
                                    exp_name=payload.get("exp_name", ""))
        self.audit.append(f"HOT_SWAP_{(kind or '').upper()}",
                          {"name": name, "old": result["old"],
                           "new": result["new"], "ros2_cmd":
                           result["ros2_cmd"],
                           "reloads": result["reloads"]},
                          source=source)
        self._hs_cache = self.hotswap.state()
        self.note(f"热切换 {kind}:{result['old']} -> {result['new']}", "warn")
        return {"ok": True, "switch": result}

    # 裁判指令透传(话题模拟)
    def _act_referee(self, payload, source):
        return {"ok": True,
                **self.referee.feed(payload.get("cmd"),
                                    source=source,
                                    args=payload.get("args"))}

    # 故障注入(演示用,替代真拔相机)
    def _act_fault(self, payload, source):
        name = payload.get("name", "camera_off")
        on = self.sim.toggle_fault(name)
        self.audit.append("FAULT_INJECT", {"fault": name, "active": on},
                          source=source)
        self.note(f"故障注入 {name} -> {'开启' if on else '关闭'}", "warn")
        return {"ok": True, "fault": name, "active": on}

    # 语音/物理按键
    def _act_voice(self, payload, source):
        text = (payload.get("text") or "").strip()
        action = self.route_voice(text)
        self.audit.append("VOICE_TRIGGER", {"text": text, "action": action},
                          source=source, result="ok" if action else "ignored")
        if not action:
            return {"ok": False, "text": text, "reason": "未匹配到唤醒词"}
        return self.do_action(action, payload, source="voice")

    def _act_button(self, payload, source):
        code = payload.get("code", "")
        action = BUTTON_ACTIONS.get(code)
        self.audit.append("BUTTON_TRIGGER", {"code": code, "action": action},
                          source=source, result="ok" if action else "ignored")
        if not action:
            raise ValueError(f"未知按键: {code}")
        return self.do_action(action, payload, source="button")

    @staticmethod
    def route_voice(text):
        for words, action in VOICE_ACTIONS:
            if any(w in text for w in words):
                return action
        return None

    # ---------- 赛后导出 ----------
    def save_report(self):
        state = self.bridge.task_state()
        report = build_report(self.bridge.machine)
        report["operator"] = {
            "referee": self.referee.status(),
            "hotswap": self.hotswap.state(),
            "audit": {"path": self.audit.path, "count": self.audit.seq,
                      "verify": self.audit.verify()},
            "estop": {"active": self.sim.estopped,
                      "count": self.car.estop_count},
        }
        report["task_state"] = state
        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        return report
