"""框架级用例:重试、降级、超时、并发、中止、配置校验。

不依赖 ROS2,也不依赖 pytest 的 fixture —— 直接 pytest 或
`python tests/test_fsm.py` 都能跑。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from task_fsm import (ABORTED, FAILED, SKIPPED, SUCCESS, TIMEOUT,  # noqa: E402
                      StateMachine, build_node)


def _spec(name, **kw):
    """造一个 SpeakTTS 任务(最轻的节点,适合测调度)。"""
    spec = {"name": name, "type": "SpeakTTS", "duration": 0.01}
    spec.update(kw)
    return spec


def _run(specs, **kw):
    machine = StateMachine([build_node(s) for s in specs], **kw)
    result = machine.run()
    status = {n.name: machine.records.get(n.name, {}).get("status")
              for n in machine.nodes}
    return result, status, machine


def test_retry_then_success():
    """前 2 次失败,retry=2 → 第 3 次成功。"""
    result, status, machine = _run([_spec("a", fail_times=2, retry=2,
                                          retry_interval=0.01)])
    assert result == SUCCESS
    assert status["a"] == SUCCESS
    assert machine.records["a"]["retries"] == 2


def test_retry_exhausted_goes_fallback():
    """重试耗尽 → 走 fallback,fallback 成功则主流程继续。"""
    result, status, machine = _run([
        _spec("a", fail_times=9, retry=1, retry_interval=0.01, fallback="b"),
        _spec("b"),
        _spec("c"),
    ])
    assert result == SUCCESS
    assert status["a"] == SKIPPED          # 降级过的不算 FAILED
    assert status["c"] == SUCCESS
    assert machine.ctx.fallback_used == {"a": "b"}


def test_fallback_also_fails_no_loop():
    """fallback 也失败:必须终止,不能 a->b->a 无限跳。"""
    result, status, _ = _run([
        _spec("a", fail_times=9, fallback="b"),
        _spec("b", fail_times=9, fallback="a"),
        _spec("c"),
    ])
    assert result == FAILED
    assert status["a"] == FAILED
    assert status["c"] == SKIPPED


def test_step_timeout():
    """步骤级 timeout 触发 → 该步判 TIMEOUT。"""
    result, status, _ = _run([_spec("slow", duration=0.5, timeout=0.1)])
    assert result == FAILED
    assert status["slow"] == TIMEOUT


def test_timeout_goes_fallback():
    """超时不重试,直接降级。"""
    result, status, machine = _run([
        _spec("a", duration=0.5, timeout=0.1, retry=3, fallback="b"),
        _spec("b"),
    ])
    assert result == SUCCESS
    assert status["a"] == SKIPPED
    assert machine.records["a"]["retries"] == 0     # 超时没消耗重试次数


def test_task_deadline():
    """任务级 deadline 到了 → result=TIMEOUT,后续不再执行。"""
    import time
    machine = StateMachine([build_node(_spec("a", duration=0.2)),
                            build_node(_spec("b"))],
                           deadline=time.monotonic() + 0.1)
    result = machine.run()
    assert result == TIMEOUT
    assert machine.records.get("b", {}).get("status") == TIMEOUT


def test_parallel_all_success():
    result, status, _ = _run([{
        "name": "p", "parallel": {"fail_policy": "all_must_succeed",
                                  "branches": [_spec("x"), _spec("y")]}}])
    assert result == SUCCESS
    assert status["p"] == SUCCESS


def test_parallel_any_fail():
    """任一分支失败 → 整体失败(官方示例写法 parallel: 子键)。"""
    result, status, _ = _run([{
        "name": "p", "parallel": {"fail_policy": "any_fail",
                                  "branches": [_spec("x"),
                                               _spec("y", fail_times=9)]}},
        _spec("after")])
    assert result == FAILED
    assert status["p"] == FAILED
    assert status["after"] == SKIPPED


def test_on_fail_skip():
    """on_fail=skip:该步标 SKIPPED,主流程继续。"""
    result, status, _ = _run([_spec("a", fail_times=9, on_fail="skip"),
                              _spec("b")])
    assert result == SUCCESS
    assert status["a"] == SKIPPED
    assert status["b"] == SUCCESS


def test_abort():
    """外部中止 → result=ABORTED(急停/裁判 STOP 走这条路)。"""
    machine = StateMachine([build_node(_spec("a")), build_node(_spec("b"))])
    machine.abort()
    assert machine.run() == ABORTED


def test_duplicate_name_rejected():
    try:
        StateMachine([build_node(_spec("a")), build_node(_spec("a"))])
    except ValueError as e:
        assert "重复" in str(e)
        return
    raise AssertionError("重复节点名应抛 ValueError")


def test_unknown_fallback_rejected():
    try:
        StateMachine([build_node(_spec("a", fallback="ghost"))])
    except ValueError as e:
        assert "ghost" in str(e)
        return
    raise AssertionError("fallback 指向不存在的节点应抛 ValueError")


def test_unknown_type_rejected():
    try:
        build_node({"name": "x", "type": "NoSuchNode"})
    except ValueError as e:
        assert "NoSuchNode" in str(e)
        return
    raise AssertionError("未知 type 应抛 ValueError")


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
