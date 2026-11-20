"""状态机桥:M5-1 的状态机跑在后台线程,状态实时镜像给仪表盘。

复用 M5-1 的节点接口和上下文结构(题目明确要求 M5-2/3/4 复用 M5-1)。
故障联动:拔相机 -> DetectTarget 失败 -> 重试 -> 降级(仪表盘节点变橙)。
"""
import os
import sys
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "..", "M5-1"))

from task_fsm import (FAILED, StateMachine, build_node,    # noqa: E402
                      load_config)


class StateBridge:
    """在后台线程里跑一次 M5-1 任务,持续暴露每个节点的当前状态。"""

    def __init__(self, mission_path, sim, time_scale=20.0):
        self.sim = sim
        self.scale = time_scale        # mock 耗时放大倍数(演示用)
        self.mission_path = mission_path
        self.machine = None
        self.thread = None
        self.lock = threading.Lock()
        self._build(mission_path)

    def _build(self, mission_path):
        tasks, mission_id, _ = load_config(mission_path)
        nodes = [build_node(t) for t in tasks]
        for node in nodes:             # 演示:把 mock 耗时放大,肉眼可见
            node.duration *= self.scale
            node.retry_interval = min(node.retry_interval, 0.5)
            node._orig_execute = node.execute
            node.execute = self._guarded(node)   # 故障/急停联动
        self.machine = StateMachine(nodes, mission_id=mission_id)

    def _guarded(self, node):
        """包装节点:急停立即失败;拔相机让感知类节点失败(触发降级演示)。"""
        orig = node._orig_execute

        def execute(ctx):
            if self.sim.estopped:
                return FAILED
            if self.sim.faults["camera_off"] and \
                    type(node).__name__ == "DetectTarget":
                node.attempts += 1
                time.sleep(node.duration)
                return FAILED
            return orig(ctx)
        return execute

    # ---------- 生命周期 ----------
    def start_mission(self):
        if self.thread and self.thread.is_alive():
            return False                # 已在跑
        self.thread = threading.Thread(target=self._worker, daemon=True)
        self.thread.start()
        return True

    def restart(self):
        """软复位:重建状态机与上下文,再起线程,不重启节点进程。"""
        self._build(self.mission_path)
        return self.start_mission()

    def _worker(self):
        try:
            self.machine.run()
        except Exception as e:            # 后台线程不允许静默死掉
            print(f"[state_bridge] 状态机异常: {e}")

    # ---------- 给聚合器的镜像 ----------
    def task_state(self):
        if self.machine is None:
            return {}
        with self.lock:
            recs = {name: dict(rec) for name, rec in
                    self.machine.records.items()}
        running = self.thread is not None and self.thread.is_alive()
        return {
            "mission_id": self.machine.ctx.mission_id,
            "final": ("RUNNING" if running else
                      (self.machine.result if recs else "IDLE")),
            "running": running,
            "nodes": {name: rec["status"] or "IDLE"
                      for name, rec in recs.items()},
            "fallback_used": dict(self.machine.ctx.fallback_used),
        }
