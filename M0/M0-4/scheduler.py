"""TaskScheduler —— M0-4 任务调度模拟器"""
import argparse
import json
import sys
from pathlib import Path
import yaml
import random
import time

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


def topo_order(tasks):
    """返回合法执行顺序(任务名字列表);有环则 raise ValueError"""
    # 1. 建两张表
    by_name = {}          # 任务名 -> 任务字典
    pending = {}          # 任务名 -> 还欠几个前置
    for t in tasks:
        name = t["name"]
        by_name[name] = t
        deps = t.get("dependencies", [])
        pending[name] = len(deps)

    # 2. 一开始一个前置都不欠的,放进 ready 队列
    ready = []
    for name in pending:
        if pending[name] == 0:
            ready.append(name)

    # 3. 反复"取一个 -> 记账 -> 放出下游"
    order = []
    while len(ready) > 0:
        name = ready.pop(0)
        order.append(name)

        # 谁在等 name?
        for t in tasks:
            deps = t.get("dependencies", [])
            if name in deps:
                pending[t["name"]] = pending[t["name"]] - 1
                if pending[t["name"]] == 0:
                    ready.append(t["name"])

    # 4. 排不完 = 有环
    if len(order) != len(tasks):
        remaining = []
        for name in by_name:
            if name not in order:
                remaining.append(name)
        remaining.sort()
        raise ValueError("依赖成环，无法调度，涉及任务: " + ", ".join(remaining))

    return order


def run(tasks, timeout, seed):
    """执行所有任务,返回报告字典"""
    random.seed(seed)
    order = topo_order(tasks)
    by_name = {t["name"]: t for t in tasks}
    status = {}                      # name -> 最终状态
    attempts = {}                    # name -> 尝试次数
    start = time.monotonic()

    def elapsed():
        return time.monotonic() - start

    def timed_out():
        return elapsed() >= timeout

    for name in order:
        t = by_name[name]

        # ① 连坐检查:任一前置不是 SUCCESS 就不跑
        deps = t.get("dependencies", [])
        if any(status.get(d) != "SUCCESS" for d in deps):
            status[name] = "SKIPPED"
            attempts[name] = 0
            print(f"[SKIPPED] {name} (前置未成功)")
            continue

        # ② 执行前查超时
        if timed_out():
            status[name] = "TIMEOUT"
            attempts[name] = 0
            print(f"[TIMEOUT] {name} (总耗时 {elapsed():.1f}s 已到上限)")
            continue

        # ③ 最多尝试 3 次
        ok = False
        for attempt in range(1, 4):
            attempts[name] = attempt
            print(f"[RUN     ] {name} 第 {attempt} 次尝试")
            time.sleep(t["duration"])
            # 睡醒后再查一次超时(题目点名要求"执行前后各查一次")
            if timed_out():
                status[name] = "TIMEOUT"
                print(f"[TIMEOUT] {name} (睡眠期间总耗时到达上限)")
                break

            if random.random() < t["success_rate"]:
                ok = True
                break
            if attempt < 3:
                print(f"[RETRY   ] {name} 第 {attempt} 次失败,重试")

        # ④ 落最终状态
        if status.get(name) != "TIMEOUT":      # 别覆盖 TIMEOUT
            status[name] = "SUCCESS" if ok else "FAILED"

    return {
        "timeout_occurred": any(s == "TIMEOUT" for s in status.values()),
        "total_duration": round(elapsed(), 2),
        "tasks": [
            {"name": n, "status": status[n], "attempts": attempts.get(n, 0)}
            for n in by_name           # 按配置顺序输出,保证每个任务都在
        ],
    }




if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="M0-4 任务调度模拟器")
    parser.add_argument("--config", required=True, help="配置文件路径 (.yaml/.json)")
    parser.add_argument("--timeout", type=float, default=None, help="覆盖配置中的超时秒数")
    parser.add_argument("--report", default="report.json", help="报告输出路径")
    parser.add_argument("--seed", type=int, default=None, help="随机种子(复现用)")
    args = parser.parse_args()

    try:
        cfg_timeout, tasks = load_config(args.config)
    except ValueError as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)

    if args.timeout is not None:
        cfg_timeout = args.timeout          # 命令行覆盖配置文件

    report = run(tasks, cfg_timeout, args.seed)

    with open(args.report, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n报告已写入 {args.report}  总耗时 {report['total_duration']}s")
