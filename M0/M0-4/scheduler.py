"""TaskScheduler —— M0-4 任务调度模拟器"""
import argparse
import json
import sys
from pathlib import Path
import yaml

def load_config(path):
    """读取 yaml/json 配置，校验后返回 (全局 timeout, 任务列表)"""
    p = Path(path)
    if not p.exists():
        raise ValueError(f"配置文件不存在: {path}")

    text = p.read_text(encoding="utf-8")

    if p.suffix == ".json":
        data = json.loads(text)
    elif p.suffix in (".yaml", ".yml"):
        data = yaml.safe_load(text)
    else:
        raise ValueError(f"不支持的配置格式: {p.suffix}（只支持 .yaml/.yml/.json）")

    if not isinstance(data, dict):
        raise ValueError("配置文件顶层必须是字典（含 timeout / tasks）")

    tasks = data.get("tasks", [])
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("tasks 必须是非空列表")

    timeout = data.get("timeout", 15)
    validate_tasks(tasks)
    return timeout, tasks


def validate_tasks(tasks):
    """逐项校验任务字段，不合法就 raise ValueError"""
    names = set()

    # 第一遍：校验单任务字段，并收集所有名字
    for i, t in enumerate(tasks):
        if not isinstance(t, dict):
            raise ValueError(f"第 {i+1} 个任务必须是字典")

        # name：必填、非空、唯一
        name = t.get("name")
        if not name:
            raise ValueError(f"第 {i+1} 个任务缺少 name")
        if name in names:
            raise ValueError(f"任务名重复: {name}")
        names.add(name)

        # duration：必填，数字（排除 bool）且 >= 0
        if t.get("duration") is None:
            raise ValueError(f"任务 {name} 缺少 duration")
        duration = t["duration"]
        if isinstance(duration, bool) or not isinstance(duration, (int, float)):
            raise ValueError(f"任务 {name} 的 duration 必须是数字，当前为 {duration!r}")
        if duration < 0:
            raise ValueError(f"任务 {name} 的 duration 不能为负: {duration}")
        
        # success_rate：必填，数字且 0 <= x <= 1
        if t.get("success_rate") is None:
            raise ValueError(f"任务 {name} 缺少 success_rate")
        rate = t["success_rate"]
        if isinstance(rate, bool) or not isinstance(rate, (int, float)):
            raise ValueError(f"任务 {name} 的 success_rate 必须是数字，当前为 {rate!r}")
        if not (0 <= rate <= 1):
            raise ValueError(f"任务 {name} 的 success_rate 越界: {rate}（应为 0~1）")

        # dependencies：缺省当 []，但给了就必须是列表
        deps = t.get("dependencies", [])
        if not isinstance(deps, list):
            raise ValueError(f"任务 {name} 的 dependencies 必须是列表")

    # 第二遍：依赖的名字必须都存在（必须两遍，因为可能依赖写在后面）
    for t in tasks:
        for d in t.get("dependencies", []):
            if d not in names:
                raise ValueError(f"任务 {t['name']} 依赖不存在的任务: {d}")


if __name__ == "__main__":
    cfg = sys.argv[1] if len(sys.argv) > 1 else "tasks_demo.yaml"
    try:
        timeout, tasks = load_config(cfg)
        print(f"timeout = {timeout}")
        for t in tasks:
            print(f"  {t['name']:14s} duration={t['duration']:<5} "
                  f"rate={t['success_rate']:<5} deps={t.get('dependencies', [])}")
    except ValueError as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)