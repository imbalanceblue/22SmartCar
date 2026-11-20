"""M5-1 任务状态机框架包。

对外只暴露这几样:状态常量、TaskNode/Parallel/StateMachine、5 个标准节点、
注册表与节点工厂。上层(M5-2/3/4)按需 import,不要穿透到子模块。
"""
from .context import TaskContext
from .core import (ABORTED, FAILED, IDLE, Parallel, RETRYING, RUNNING,
                   SKIPPED, SUCCESS, TIMEOUT, StateMachine, TaskNode)
from .logger import log
from .nodes import (DetectTarget, ExecuteGrasp, NavigateTo, SpeakTTS,
                    WaitForTrigger)
from .registry import BASE_PARAMS, REGISTRY, build_node, load_config
from .report import build_report

__all__ = [
    "TaskContext", "TaskNode", "Parallel", "StateMachine",
    "IDLE", "RUNNING", "SUCCESS", "FAILED", "SKIPPED", "RETRYING",
    "TIMEOUT", "ABORTED",
    "WaitForTrigger", "NavigateTo", "DetectTarget", "ExecuteGrasp", "SpeakTTS",
    "REGISTRY", "BASE_PARAMS", "build_node", "load_config", "build_report", "log",
]
