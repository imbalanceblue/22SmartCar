"""M5-3 裁判系统适配器:协议解析 + 状态发布 + 3-2-1 发车倒计时。

题面协议:
  订阅 /referee/cmd   : START PAUSE RESUME STOP PENALTY SCORE_QUERY
  发布 /referee/status: READY RUNNING PAUSED STOPPED FAULT

真实部署时这两个话题由 ROS2 DDS 收发;考核/练习环境用
  (a) 键盘模拟(S/P/R/E)  (b) HTTP /api/referee/<cmd>  (c) feed()
三种入口都走同一条 _apply 逻辑,保证行为一致。
"""
import threading
import time
from collections import deque

READY = "READY"
RUNNING = "RUNNING"
PAUSED = "PAUSED"
STOPPED = "STOPPED"
FAULT = "FAULT"

VALID_CMDS = ("START", "PAUSE", "RESUME", "STOP", "PENALTY", "SCORE_QUERY")


class RefereeBridge:
    """裁判指令状态机。tick() 由主循环 10Hz 驱动(不自己开线程)。"""

    def __init__(self, race_seconds=180.0, prestart=3.0):
        self.race_total = float(race_seconds)
        self.race_left = float(race_seconds)
        self.prestart_total = float(prestart)
        self.prestart_left = 0.0
        self.state = READY
        self.phase = "准备"
        self.fouls = 0
        self.penalty_s = 0.0
        self.history = deque(maxlen=50)      # 指令流水(给界面)
        self._hooks = {}
        self._lock = threading.Lock()

    # ---------- 事件订阅(终端/状态机桥接) ----------
    def on(self, name, fn):
        self._hooks.setdefault(name, []).append(fn)

    def _emit(self, name, payload=None):
        for fn in list(self._hooks.get(name, [])):
            try:
                fn(payload or {})
            except Exception as e:           # 回调异常不能拖垮裁判线程
                print(f"[referee_bridge] 回调 {name} 异常: {e}")

    # ---------- 指令入口 ----------
    def feed(self, cmd, source="topic", args=None):
        """喂一条裁判指令;非法指令/状态非法都抛 ValueError(调用方转中文错误)。"""
        cmd = (cmd or "").strip().upper()
        if cmd not in VALID_CMDS:
            raise ValueError(f"未知裁判指令: {cmd}(合法: {list(VALID_CMDS)})")
        args = args or {}
        with self._lock:
            result, events = self._apply(cmd, args)
            self.history.append({"t": round(time.time(), 3), "cmd": cmd,
                                 "source": source, "result": result})
        for name, payload in events:          # 锁外发事件,避免回调里再进锁
            payload = dict(payload)
            payload["source"] = source        # 审计要知道是操作员/键盘/裁判
            self._emit(name, payload)
        return {"cmd": cmd, "source": source, "result": result,
                "state": self.state}

    def _apply(self, cmd, args):
        events = []
        if cmd == "START":
            if self.state not in (READY, STOPPED):
                raise ValueError(f"当前状态 {self.state} 不接受 START")
            self.race_left = self.race_total
            self.state = READY
            self.prestart_left = self.prestart_total
            self.phase = "发车倒计时"
            result = f"倒计时 {self.prestart_total:.0f}s"
            if self.prestart_left <= 0:
                self.state = RUNNING
                self.phase = "比赛进行中"
                events.append(("start", {}))
        elif cmd == "PAUSE":
            if self.state != RUNNING:
                raise ValueError(f"当前状态 {self.state} 不能暂停")
            self.state = PAUSED
            self.phase = "暂停"
            result = "已暂停"
            events.append(("pause", {}))
        elif cmd == "RESUME":
            if self.state != PAUSED:
                raise ValueError(f"当前状态 {self.state} 不能继续")
            self.state = RUNNING
            self.phase = "比赛进行中"
            result = "已继续"
            events.append(("resume", {}))
        elif cmd == "STOP":
            if self.state not in (RUNNING, PAUSED, READY):
                raise ValueError(f"当前状态 {self.state} 不能停止")
            self.state = STOPPED
            self.phase = "已停止"
            self.prestart_left = 0.0
            result = "已停止"
            events.append(("stop", {}))
        elif cmd == "PENALTY":
            seconds = float(args.get("seconds", 10))
            self.fouls += 1
            self.penalty_s += seconds
            self.race_left += seconds        # 加罚:补时给对手,我方多计时
            result = f"犯规 {self.fouls} 次,加罚 {seconds:.0f}s"
            events.append(("penalty", {"seconds": seconds,
                                       "fouls": self.fouls}))
        else:                                # SCORE_QUERY
            result = self.score()
        return result, events

    # ---------- 主循环驱动 ----------
    def tick(self, dt):
        if self.prestart_left > 0:
            self.prestart_left = max(0.0, self.prestart_left - dt)
            if self.prestart_left > 0:
                self.phase = f"发车倒计时 {int(self.prestart_left) + 1}"
            else:
                self.state = RUNNING
                self.phase = "比赛进行中"
                self._emit("start", {"source": "referee"})
            return
        if self.state == RUNNING:
            self.race_left = max(0.0, self.race_left - dt)
            if self.race_left <= 0:
                self.state = STOPPED
                self.phase = "时间到"
                self._emit("timeup", {"source": "referee"})

    # ---------- 对外发布 ----------
    def set_fault(self, reason="急停"):
        with self._lock:
            self.state = FAULT
            self.phase = reason
        self._emit("fault", {"reason": reason, "source": "system"})

    def reset_race(self):
        """软/硬复位用:回到 READY,倒计时与犯规清零。"""
        with self._lock:
            self.state = READY
            self.phase = "准备"
            self.race_left = self.race_total
            self.prestart_left = 0.0
            self.fouls = 0
            self.penalty_s = 0.0
        self._emit("reset", {"source": "system"})

    def score(self):
        return {"state": self.state, "fouls": self.fouls,
                "penalty_s": self.penalty_s,
                "race_left": round(self.race_left, 1)}

    def status(self):
        with self._lock:
            return {
                "state": self.state,
                "countdown": round(self.race_left, 1),
                "prestart": round(self.prestart_left, 2),
                "phase": self.phase,
                "fouls": self.fouls,
                "penalty_s": round(self.penalty_s, 1),
                "history": list(self.history)[-8:],
            }


class KeyboardSim:
    """本地键盘模拟裁判:S=START P=PAUSE R=RESUME E=ESTOP(急停)。

    只在真终端里生效(非 tty 环境直接跳过,避免测试/重定向卡死)。
    """

    KEYMAP = {"s": "START", "p": "PAUSE", "r": "RESUME"}

    def __init__(self, bridge, on_estop=None, stream=None):
        import sys
        self.bridge = bridge
        self.on_estop = on_estop
        self.stream = stream or sys.stdin
        self._stop = threading.Event()
        self._thread = None
        self.rows = []

    def start(self):
        if not self.stream.isatty():
            return False                    # 非交互环境:静默禁用
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return True

    def stop(self):
        self._stop.set()

    def _loop(self):
        import select
        import sys
        import termios
        import tty
        fd = self.stream.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setcbreak(fd)               # 单字符读取,不回车
            while not self._stop.is_set():
                r, _, _ = select.select([self.stream], [], [], 0.2)
                if not r:
                    continue
                ch = sys.stdin.read(1).lower()
                if ch in self.KEYMAP:
                    try:
                        self.bridge.feed(self.KEYMAP[ch], source="keyboard")
                    except ValueError as e:
                        print(f"\n[键盘] {e}")
                elif ch == "e" and self.on_estop:
                    self.on_estop("keyboard")
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
