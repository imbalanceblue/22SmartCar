"""入口:读 YAML/JSON 编排 DSL -> 实例化节点 -> 跑状态机 -> 写报告。

用法:
    python3 runner.py --config mission.yaml
    python3 runner.py --config mission.yaml --report task_report.json --deadline 60
"""
import argparse
import json
import sys
import time

from mock_nodes import (DetectTarget, ExecuteGrasp, NavigateTo,
                        SpeakTTS, WaitForTrigger)
from state_machine import FAILED, Parallel, StateMachine

REGISTRY = {
    "WaitForTrigger": WaitForTrigger,
    "NavigateTo": NavigateTo,
    "DetectTarget": DetectTarget,
    "ExecuteGrasp": ExecuteGrasp,
    "SpeakTTS": SpeakTTS,
    "Parallel": Parallel,
}

# 这几个字段是框架参数,由 TaskNode 基类消化,其余透传给子类
BASE_PARAMS = {"timeout", "retry", "retry_interval", "fallback",
               "fail_times", "duration", "branches"}


def load_config(path):
    """按后缀解析 YAML/JSON,并做基础校验。"""
    with open(path, "r", encoding="utf-8") as f:
        suffix = path.rsplit(".", 1)[-1].lower()
        if suffix == "yaml":
            import yaml              # 懒加载:JSON 配置无需 pyyaml
            try:
                cfg = yaml.safe_load(f)
            except yaml.YAMLError as e:
                raise ValueError(f"YAML 解析失败: {e}")
        elif suffix == "json":
            cfg = json.load(f)
        else:
            raise ValueError(f"不支持的配置格式: {path}(只支持 .yaml/.json)")
    tasks = cfg.get("tasks")
    if not tasks or not isinstance(tasks, list):
        raise ValueError("配置缺少 tasks 列表")
    return tasks, cfg.get("mission_id", "mission"), cfg.get("deadline")


def build_node(spec):
    """把一个任务字典实例化成 TaskNode 对象(Parallel 递归处理分支)。"""
    t = spec.get("type")
    if t not in REGISTRY:
        raise ValueError(f"未知节点类型: {t}(可选: {sorted(REGISTRY)})")
    if "name" not in spec:
        raise ValueError("任务缺少 name 字段")
    cls = REGISTRY[t]
    base = {k: spec[k] for k in BASE_PARAMS if k in spec}
    extra = {k: v for k, v in spec.items()
             if k not in BASE_PARAMS and k not in ("name", "type")}
    if cls is Parallel:
        branches = spec.get("branches")
        if not branches or not isinstance(branches, list):
            raise ValueError(f"Parallel 节点 {spec['name']} 缺少 branches 列表")
        base.pop("branches", None)   # branches 单独传,避免重复参数
        return Parallel(spec["name"], [build_node(b) for b in branches],
                        **base)
    return cls(spec["name"], **base, **extra)


def main():
    parser = argparse.ArgumentParser(description="M5-1 任务状态机运行器")
    parser.add_argument("--config", required=True, help="YAML/JSON 编排文件")
    parser.add_argument("--report", default="task_report.json",
                        help="报告输出路径")
    parser.add_argument("--deadline", type=float, default=None,
                        help="全局截止时间(秒,从启动算起)")
    args = parser.parse_args()

    tasks, mission_id, _ = load_config(args.config)
    nodes = [build_node(t) for t in tasks]
    # 节点名唯一性 / fallback 目标存在性由 StateMachine 构造时校验

    deadline = None
    if args.deadline is not None:
        deadline = time.monotonic() + args.deadline

    machine = StateMachine(nodes, mission_id=mission_id, deadline=deadline)
    final = machine.run()
    machine.save_report(args.report)
    sys.exit(0 if final == "SUCCESS" else 1)


if __name__ == "__main__":
    try:
        main()
    except ValueError as e:
        print(f"[ERROR] 配置错误: {e}")
        sys.exit(1)
    except FileNotFoundError as e:
        print(f"[ERROR] 文件不存在: {e}")
        sys.exit(1)
    except ImportError as e:
        print(f"[ERROR] 缺少依赖: {e}(pip install pyyaml)")
        sys.exit(1)
