"""裁判适配器单测:3-2-1 发车、暂停/继续、判罚、时间到、非法指令。"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "host_side"))

from referee_bridge import RefereeBridge              # noqa: E402


def test_prestart_countdown_then_running():
    rb = RefereeBridge(race_seconds=180, prestart=3.0)
    events = []
    rb.on("start", lambda p: events.append(p))
    rb.feed("START", source="referee")
    st = rb.status()
    assert st["state"] == "READY" and abs(st["prestart"] - 3.0) < 0.01, st
    for _ in range(31):                    # 3.1s
        rb.tick(0.1)
    st = rb.status()
    assert st["state"] == "RUNNING", st
    assert len(events) == 1 and events[0]["source"] == "referee"
    assert st["countdown"] < 180.0


def test_pause_resume_and_illegal():
    rb = RefereeBridge(prestart=0)
    rb.feed("START")
    assert rb.state == "RUNNING"
    rb.feed("PAUSE")
    assert rb.state == "PAUSED"
    rb.feed("RESUME")
    assert rb.state == "RUNNING"
    try:
        rb.feed("RESUME")                  # 非 PAUSED 状态继续 -> 报错
        raise AssertionError("应当抛 ValueError")
    except ValueError as e:
        assert "不能继续" in str(e)


def test_unknown_command():
    rb = RefereeBridge()
    try:
        rb.feed("LAUNCH")
        raise AssertionError("应当抛 ValueError")
    except ValueError as e:
        assert "未知裁判指令" in str(e)


def test_penalty_adds_time_and_counts_foul():
    rb = RefereeBridge(prestart=0)
    rb.feed("START")
    before = rb.status()["countdown"]
    rb.feed("PENALTY", args={"seconds": 10})
    st = rb.status()
    assert st["fouls"] == 1
    assert st["countdown"] == before + 10


def test_timeup_stops_race():
    rb = RefereeBridge(race_seconds=0.5, prestart=0)
    events = []
    rb.on("timeup", lambda p: events.append(p))
    rb.feed("START")
    for _ in range(8):
        rb.tick(0.1)
    assert rb.state == "STOPPED"
    assert len(events) == 1


def test_fault_state():
    rb = RefereeBridge(prestart=0)
    rb.feed("START")
    rb.set_fault("紧急停车")
    assert rb.state == "FAULT"


def test_reset_race():
    rb = RefereeBridge(prestart=0)
    rb.feed("START")
    rb.feed("PENALTY", args={"seconds": 10})
    rb.reset_race()
    st = rb.status()
    assert st["state"] == "READY" and st["fouls"] == 0
    assert abs(st["countdown"] - 180.0) < 0.01
