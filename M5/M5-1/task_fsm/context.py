"""TaskContext:一次任务全程共享的黑板。

六个字段对应题面要求:
    mission_id    任务 ID
    deadline      截止时刻(time.monotonic 基准),到点后整个任务判 TIMEOUT
    shared        共享数据字典,节点之间靠它传 target_pose / grasp_result 等
    retry_budget  全局重试预算,防止单节点把重试次数吃光
    fallback_used 节点名 -> 降级到的目标,写进报告便于复盘
    metadata      杂项(如 mock 的目标位姿)
"""
from dataclasses import dataclass, field


@dataclass
class TaskContext:
    mission_id: str = "mission"
    deadline: float | None = None
    shared: dict = field(default_factory=dict)
    retry_budget: int = 10
    fallback_used: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)

    def snapshot(self):
        """给报告用的上下文快照(只留用户关心的,不吐内部对象)。"""
        return {
            "mission_id": self.mission_id,
            "retry_budget_left": self.retry_budget,
            "fallback_used": dict(self.fallback_used),
            "shared": dict(self.shared),
        }
