"""M5-1 单元测试:每个核心机制至少 3 类用例(正常/超时/失败重试/降级)。

运行:在 M5-1 目录下执行  python3 -m pytest tests/ -v
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from task_fsm import (FAILED, SKIPPED, SUCCESS, TIMEOUT, DetectTarget,
                      NavigateTo, Parallel, SpeakTTS, StateMachine,
                      TaskContext, TaskNode, WaitForTrigger)
from task_fsm.nodes import MockNode


class Dummy(MockNode):
    """最小可测节点:行为完全由参数控制(run_once 来自 MockNode)。"""

    def execute(self, ctx):
        return self.run_once(ctx)


# ---------- 节点层 ----------

def test_node_normal_success():
    node = Dummy("t", duration=0.01)
    ctx = TaskContext()
    assert node.run_in_thread(ctx) == SUCCESS
    assert node.attempts == 1


def test_node_fail_times_requires_retry():
    """前 2 次故意失败,第 3 次成功 -> 需要重试 2 次。"""
    node = Dummy("t", fail_times=2, retry=2, retry_interval=0.01)
    machine = StateMachine([node])
    assert machine.run() == SUCCESS
    assert node.attempts == 3
    assert machine.records["t"]["retries"] == 2


def test_node_timeout_returns_timeout():
    """duration 超过 timeout -> 框架判 TIMEOUT,且不消耗重试。"""
    node = Dummy("t", duration=0.5, timeout=0.1, retry=3, retry_interval=0.01)
    machine = StateMachine([node])
    assert machine.run() == FAILED          # 无 fallback -> 最终 FAILED
    assert node.attempts == 1               # 超时不重试
    assert machine.records["t"]["status"] == TIMEOUT   # 报告里要能区分超时与失败


# ---------- 状态机层 ----------

def test_fallback_success_continues():
    """主节点失败 -> 降级节点成功 -> 后续继续执行。"""
    main = Dummy("main", fail_times=99, fallback="backup")
    backup = Dummy("backup")
    after = Dummy("after")
    machine = StateMachine([main, backup, after])
    assert machine.run() == SUCCESS
    assert machine.records["main"]["status"] == SKIPPED   # 降级成功标 SKIPPED
    assert machine.ctx.fallback_used == {"main": "backup"}
    assert machine.records["after"]["status"] == SUCCESS


def test_fallback_fail_skips_rest():
    """主节点与降级节点都失败 -> 后续全部 SKIPPED。"""
    main = Dummy("main", fail_times=99, fallback="backup")
    backup = Dummy("backup", fail_times=99)
    after = Dummy("after")
    machine = StateMachine([main, backup, after])
    assert machine.run() == FAILED
    assert machine.records["after"]["status"] == SKIPPED


def test_fallback_target_not_reexecuted():
    """降级成功后,降级目标节点在主序列中不重复执行。"""
    main = Dummy("main", fail_times=99, fallback="backup")
    backup = Dummy("backup")
    machine = StateMachine([main, backup])
    assert machine.run() == SUCCESS
    assert backup.attempts == 1


def test_fallback_target_missing_raises():
    """fallback 指向不存在的节点 -> 构造时即报错。"""
    try:
        StateMachine([Dummy("t", fallback="ghost")])
    except ValueError as e:
        assert "ghost" in str(e)
        return
    raise AssertionError("fallback 指向不存在的节点应抛 ValueError")


def test_downstream_skipped_after_final_failure():
    """无 fallback 的节点最终失败 -> 后续连坐跳过。"""
    first = Dummy("first", fail_times=99)
    second = Dummy("second")
    machine = StateMachine([first, second])
    assert machine.run() == FAILED
    assert machine.records["second"]["status"] == SKIPPED


def test_retry_budget_exhaustion():
    """全局重试预算耗尽 -> 立即判失败,不再重试。"""
    node = Dummy("t", fail_times=99, retry=5, retry_interval=0.01)
    machine = StateMachine([node])
    machine.ctx.retry_budget = 1
    assert machine.run() == FAILED
    assert node.attempts == 2               # 首次 + 1 次预算内重试


# ---------- Parallel 层 ----------

def test_parallel_all_success():
    node = Parallel("p", [Dummy("a", duration=0.01), Dummy("b", duration=0.01)])
    assert node.run_in_thread(TaskContext()) == SUCCESS


def test_parallel_one_fail():
    node = Parallel("p", [Dummy("a", duration=0.01),
                          Dummy("b", fail_times=99)])
    assert node.run_in_thread(TaskContext()) == FAILED


def test_parallel_branch_timeout():
    """分支自带 timeout:超时分支返回 TIMEOUT -> 整体 FAILED。"""
    node = Parallel("p", [Dummy("a", duration=0.01),
                          Dummy("b", duration=0.5, timeout=0.1)])
    assert node.run_in_thread(TaskContext()) == FAILED


def test_parallel_whole_timeout():
    """节点整体 timeout:分支没配 timeout,整体由外层框架判 TIMEOUT。"""
    node = Parallel("p", [Dummy("a", duration=0.01),
                          Dummy("b", duration=0.5)], timeout=0.1)
    assert node.run_in_thread(TaskContext()) == TIMEOUT


# ---------- 上下文流转 ----------

def test_context_passing_detect_to_navigate():
    """DetectTarget 写 target_pose -> NavigateTo 用 pose_from_context 读。"""
    detect = DetectTarget("detect")
    nav = NavigateTo("nav", pose_from_context="target_pose")
    machine = StateMachine([detect, nav])
    assert machine.run() == SUCCESS
    assert machine.ctx.shared["target_pose"] == [1.0, 2.0, 0.0]
    assert machine.ctx.shared["current_pose"] == [1.0, 2.0, 0.0]


def test_navigate_fails_without_context():
    """上游没产出目标位姿 -> NavigateTo 立刻 FAILED。"""
    nav = NavigateTo("nav", pose_from_context="target_pose")
    assert nav.run_in_thread(TaskContext()) == FAILED


# ---------- 报告 ----------

def test_report_structure():
    """报告字段按官方 README §2.4:task_id / result / total_elapsed_s / steps。"""
    from run_task import build_report
    detect = DetectTarget("detect")
    nav = NavigateTo("nav", pose_from_context="target_pose")
    machine = StateMachine([detect, nav], mission_id="unit_test")
    machine.run()
    data = build_report(machine, 1.0)
    assert data["task_id"] == "unit_test"
    assert data["result"] == "SUCCESS"
    assert [s["name"] for s in data["steps"]] == ["detect", "nav"]
    for step in data["steps"]:
        assert {"name", "type", "status", "elapsed_s",
                "retries", "degraded"} <= set(step)
    assert data["final_context"]["target_pose"] == [1.0, 2.0, 0.0]


if __name__ == "__main__":
    passed = failed = 0
    for fname, fn in sorted(globals().items()):
        if fname.startswith("test_") and callable(fn):
            try:
                fn()
                passed += 1
                print(f"  PASS  {fname}")
            except AssertionError as e:
                failed += 1
                print(f"  FAIL  {fname}  {e}")
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
