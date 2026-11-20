"""M5-3 任务桥:在 M5-1 状态机外面加三个"操作员干预点"。

为什么不改 M5-1?
- M5-1 的内核是"节点只管执行,状态机只管调度",这条边界不能破;
- 操作员的暂停/跳过/急停属于"外部干预",用节点包装(guard)插入,
  不改一行内核代码 —— 答辩讲"复用而非复制"就落在这里。

三个干预点:
1. 暂停/继续:闸门只在节点边界生效(原子级),不会把正在跑的节点切断;
2. 跳过当前节点:标记 SKIPPED;若该节点有 fallback 就走 fallback,
   没有 fallback 则让流程继续(人工跳过不应导致整场判负);
3. 急停:所有节点立即返回 FAILED,后续节点连坐 SKIPPED。
"""
import os
import sys
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "..", "M5-1"))

from task_fsm import (FAILED, SKIPPED, SUCCESS, StateMachine,  # noqa: E402
                      build_node, load_config)


class RaceBridge:
    def __init__(self, mission_path, sim, time_scale=1.0):
        self.sim = sim
        self.scale = float(time_scale)
        self.mission_path = mission_path
        self.machine = None
        self.thread = None
        self.current = None                 # 当前正在执行的节点名(界面高亮)
        self.paused = False
        self.skipped = set()                # 操作员跳过的节点
        self._skip_now = None
        self._abort = False                 # 复位时让旧线程尽快收尾
        self._resume = threading.Event()
        self._resume.set()
        self.lock = threading.RLock()
        self._build(mission_path)

    # ---------- 构建 ----------
    def _build(self, mission_path):
        tasks, mission_id, _ = load_config(mission_path)
        nodes = [build_node(t) for t in tasks]
        for node in nodes:
            node.duration *= self.scale      # 演示加速:缩短 mock 耗时
            node.retry_interval = min(node.retry_interval, 0.3)
            node._orig_execute = node.execute
            node.execute = self._guard(node)
        self.machine = StateMachine(nodes, mission_id=mission_id)

    def _guard(self, node):
        orig = node._orig_execute

        def execute(ctx):
            with self.lock:
                self.current = node.name
            if self.sim.estopped:             # 急停最高优先级
                return FAILED
            self._resume.wait()               # 暂停闸门(节点边界)
            if self.sim.estopped or self._abort:
                return FAILED
            with self.lock:
                skipped = node.name in self.skipped
            if skipped:                       # 操作员跳过
                node.attempts += 1
                time.sleep(node.duration)
                return FAILED if node.fallback else SUCCESS
            if self.sim.faults.get("camera_off") and \
                    type(node).__name__ == "DetectTarget":
                node.attempts += 1            # 拔相机:感知节点失败 -> 重试/降级
                time.sleep(node.duration)
                return FAILED
            return orig(ctx)
        return execute

    # ---------- 生命周期 ----------
    def start_mission(self):
        if self.thread and self.thread.is_alive():
            return False
        self._resume.set()
        self.paused = False
        self.thread = threading.Thread(target=self._worker, daemon=True)
        self.thread.start()
        return True

    def pause(self):
        with self.lock:
            self.paused = True
        self._resume.clear()

    def resume(self):
        with self.lock:
            self.paused = False
        self._resume.set()

    def skip_current(self):
        with self.lock:
            name = self.current
            if not name or not self.is_running():
                return None
            self.skipped.add(name)
            self._skip_now = name
            return name

    def restart(self):
        """软复位:重建状态机与上下文,不重启进程(节点 mock 无需重启)。"""
        self._abort = True                  # 让旧线程在下一个节点边界立刻收尾
        self._resume.set()                  # 解除暂停闸门,否则旧线程永远等在这
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=3.0)
        self._abort = False
        self._build(self.mission_path)
        self.current = None
        self.skipped.clear()
        self._skip_now = None
        return self.start_mission()

    def is_running(self):
        return bool(self.thread and self.thread.is_alive())

    def _worker(self):
        machine = self.machine               # 固定本线程要跑的机器(复位时不串台)
        try:
            machine.run()
        except Exception as e:
            print(f"[race_bridge] 状态机异常: {e}")

    # ---------- 镜像给界面 ----------
    def task_state(self):
        if self.machine is None:
            return {}
        with self.lock:
            recs = {name: dict(rec) for name, rec in
                    self.machine.records.items()}
            skipped = set(self.skipped)
            current = self.current
            paused = self.paused
        for name in skipped:                  # 跳过的节点在界面上显示 SKIPPED
            rec = recs.get(name)
            if rec and rec.get("status") in (FAILED, SUCCESS):
                rec["status"] = SKIPPED
                rec["skipped_by"] = "operator"
        running = self.is_running()
        final = ("RUNNING" if running else
                 (self.machine.result if recs else "IDLE"))
        if paused and running:
            final = "PAUSED"
        return {
            "mission_id": self.machine.ctx.mission_id,
            "final": final,
            "running": running,
            "paused": paused,
            "current": current,
            "nodes": {name: rec.get("status") or "IDLE"
                      for name, rec in recs.items()},
            "records": recs,
            "fallback_used": dict(self.machine.ctx.fallback_used),
            "skipped": sorted(skipped),
        }
