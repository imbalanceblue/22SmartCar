"""Mock ROS2 接口桩:只定义 M5-1 用到的接口形状,不发真实通信。

真实实现要换成 rclpy 的 ActionClient / ServiceClient;
Mock 版让状态机能在没有 ROS2 环境的机器上跑通和单测。
"""
from dataclasses import dataclass, field


@dataclass
class MockActionGoal:
    """对应 NavigateTo / ExecuteGrasp 的 Action 目标。"""
    action_name: str
    goal: dict = field(default_factory=dict)


@dataclass
class MockActionResult:
    """Action 结果:STATUS 同状态机常量。"""
    status: str
    result: dict = field(default_factory=dict)


@dataclass
class MockServiceResponse:
    """对应 SpeakTTS(/tts/speak)的 Service 响应。"""
    service_name: str
    ok: bool


def call_action(goal: MockActionGoal, duration=0.0) -> MockActionResult:
    """模拟一次 Action 调用(不阻塞真实硬件)。"""
    import time
    time.sleep(duration)
    return MockActionResult(status="SUCCESS", result=goal.goal)


def call_service(service_name: str) -> MockServiceResponse:
    """模拟一次 Service 调用。"""
    return MockServiceResponse(service_name=service_name, ok=True)
