"""M5-1 入口:读 YAML/JSON 编排 DSL -> 建节点 -> 跑状态机 -> 写 task_report.json。

用法:
    python run_task.py tasks/task_demo.yaml
    python run_task.py tasks/task_demo.yaml --report out.json --deadline 60

退出码: 0=全部成功;非 0=有失败/超时/中止(便于 CI 与脚本判断)。
"""
import argparse
import json
import sys
import time

from task_fsm import StateMachine, build_node, build_report, load_config


def main():
    parser = argparse.ArgumentParser(description="M5-1 任务状态机运行器")
    parser.add_argument("config", help="YAML/JSON 编排文件,如 tasks/task_demo.yaml")
    parser.add_argument("--report", default="task_report.json", help="报告输出路径")
    parser.add_argument("--deadline", type=float, default=None,
                        help="任务级截止时间(秒,从启动算起)")
    args = parser.parse_args()

    tasks, mission_id, _ = load_config(args.config)
    nodes = [build_node(t) for t in tasks]
    # 节点名唯一性 / fallback 目标存在性:StateMachine 构造时校验
    deadline = time.monotonic() + args.deadline if args.deadline else None

    machine = StateMachine(nodes, mission_id=mission_id, deadline=deadline)
    t0 = time.monotonic()
    final = machine.run()
    report = build_report(machine, time.monotonic() - t0)

    with open(args.report, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"[    INFO] 报告已写入 {args.report}  (result={report['result']})")

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
