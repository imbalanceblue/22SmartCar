"""5 个标准任务节点(Mock 实现)。

真实场景每个节点对应一个 ROS2 Action/Service;Mock 版用
"可中断计时 + 前 N 次故意失败"替代硬件交互,使流程可离线测、结果可复现。

MockNode 把"计时与故意失败"这类测试辅助能力单独收在一层,
避免污染 core.py 的调度内核。
"""
import time

from .context import TaskContext
from .core import FAILED, SUCCESS, TaskNode
from .logger import log


class MockNode(TaskNode):
    """Mock 节点公共能力:可被 cancel() 打断的计时 + 前 N 次故意失败。"""

    def sleep_interruptible(self, seconds, tick=0.01):
        """分片 sleep 并检查取消标志,确保 cancel() 后能及时退出。"""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self._cancelled:
                return False
            time.sleep(min(tick, max(0.0, end - time.monotonic())))
        return not self._cancelled

    def run_once(self, ctx: TaskContext) -> str:
        """计时 + 前 fail_times 次返回 FAILED,之后成功。"""
        self.attempts += 1
        if not self.sleep_interruptible(self.duration):
            return FAILED
        return FAILED if self.attempts <= self.fail_times else SUCCESS


class WaitForTrigger(MockNode):
    """等发车口令。真实版:等 /referee/start_cmd 或键盘空格,超时 30s。"""

    def execute(self, ctx: TaskContext) -> str:
        status = self.run_once(ctx)
        if status == SUCCESS:
            ctx.shared["start_time"] = self.name
        return status


class NavigateTo(MockNode):
    """导航到目标位姿。真实版:调 /navigate_to_pose Action。

    pose:固定坐标;pose_from_context:从上下文读(如识别出的目标位置)。
    """

    def __init__(self, name, *, pose=None, pose_from_context=None, **kwargs):
        super().__init__(name, **kwargs)
        self.pose = pose
        self.pose_from_context = pose_from_context

    def execute(self, ctx: TaskContext) -> str:
        target = self.pose
        if self.pose_from_context:
            target = ctx.shared.get(self.pose_from_context)
            if target is None:              # 上游没产出目标位姿
                log("FAILED", f"{self.name}: 上下文缺少 {self.pose_from_context}")
                return FAILED
        status = self.run_once(ctx)
        if status == SUCCESS:
            log("INFO", f"{self.name}: 已到达 {target}")
            ctx.shared["current_pose"] = target
            ctx.shared["last_nav_result"] = "arrived"
        return status


class DetectTarget(MockNode):
    """识别目标。真实版:订阅 /perception/targets,等类别+置信度达标。"""

    def __init__(self, name, *, target_class="qrcode",
                 min_confidence=0.7, **kwargs):
        super().__init__(name, **kwargs)
        self.target_class = target_class
        self.min_confidence = min_confidence

    def execute(self, ctx: TaskContext) -> str:
        status = self.run_once(ctx)
        if status == SUCCESS:
            ctx.shared["target_id"] = self.target_class
            ctx.shared["target_pose"] = ctx.metadata.get(
                "mock_target_pose", [1.0, 2.0, 0.0])
            ctx.shared["confidence"] = self.min_confidence
            log("INFO", f"{self.name}: 识别到 {self.target_class},"
                        f"置信度 {self.min_confidence}")
        return status


class ExecuteGrasp(MockNode):
    """视觉伺服抓取。真实版:调 /manipulation/grasp Action。"""

    def __init__(self, name, *, grasp_type="top_down", **kwargs):
        super().__init__(name, **kwargs)
        self.grasp_type = grasp_type

    def execute(self, ctx: TaskContext) -> str:
        if "grasp_pose" not in ctx.shared:  # 依赖 DetectTarget 的输出
            ctx.shared["grasp_pose"] = ctx.shared.get("target_pose")
        status = self.run_once(ctx)
        if status == SUCCESS:
            ctx.shared["grasp_result"] = "held"
            ctx.shared["object_pose"] = ctx.shared.get("grasp_pose")
        return status


class SpeakTTS(MockNode):
    """语音播报。真实版:调 /tts/speak Service。"""

    def __init__(self, name, *, text="任务完成", **kwargs):
        super().__init__(name, **kwargs)
        self.text = text

    def execute(self, ctx: TaskContext) -> str:
        status = self.run_once(ctx)
        if status == SUCCESS:
            log("INFO", f"{self.name}: 播报「{self.text}」")
        return status
