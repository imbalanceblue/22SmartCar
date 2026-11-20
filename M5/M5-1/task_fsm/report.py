"""报告生成:把状态机的运行记录整理成官方 README §2.4 要求的 task_report.json。

为什么独立成模块而不是塞进 core?
- core.py 要守在 <200 行(官方硬性要求),报告是 IO/展示层的事;
- M5-2 仪表盘、M5-3 裁判台、M5-1 命令行入口都要出同一份报告,抽出来才能共用。
"""

from .core import FAILED, IDLE


def build_report(machine, elapsed=None):
    """按官方字段名产出报告字典。

    elapsed 不传时用各节点耗时之和近似(不含重试间隔与并行重叠),
    适合从别的任务(M5-2/M5-3)导出时随手拿一份可读的成绩单。
    """
    steps = []
    for node in machine.nodes:
        rec = machine.records.get(node.name, {})
        steps.append({
            "name": node.name,
            "type": type(node).__name__,
            "status": rec.get("status") or IDLE,
            "elapsed_s": rec.get("duration", 0.0),
            "retries": rec.get("retries", 0),
            "degraded": node.name in machine.ctx.fallback_used,
        })
    if elapsed is None:
        elapsed = sum(s["elapsed_s"] for s in steps)
    return {
        "task_id": machine.ctx.mission_id,
        "result": machine.result or FAILED,
        "total_elapsed_s": round(elapsed, 3),
        "steps": steps,
        "final_context": dict(machine.ctx.shared),
    }
