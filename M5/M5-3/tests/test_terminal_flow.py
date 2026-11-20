"""M5-3 全流程集成测试:赛制流程 + 热切 + 降级 + 急停 + 审计 + HTTP 入口。

覆盖题面的"完成判断":
裁判 START -> 3-2-1 -> 状态机自动开跑;热切模型不重启;拔相机 -> 看门狗
降级;急停 -> 零速度 + 黑匣子 + 审计;审计链可校验;HTTP 页面/接口可用。
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, "host_side"))
sys.path.insert(0, os.path.join(_ROOT, "robot_side"))

import operator_terminal as OT                        # noqa: E402
from terminal_app import TerminalApp                  # noqa: E402

MISSION = os.path.join(_ROOT, "config", "mission_race.json")


def _wait(cond, timeout, step=0.1):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(step)
    return False


def _make_app(d):
    return TerminalApp(MISSION, time_scale=5.0, race_seconds=30,
                       audit_path=os.path.join(d, "audit.jsonl"),
                       switch_log=os.path.join(d, "switch_log.csv"),
                       blackbox=os.path.join(d, "blackbox.jsonl"),
                       enable_tts=False,
                       state_file=os.path.join(d, "task_report.json"))


def test_full_match_flow():
    d = tempfile.mkdtemp(prefix="m53_flow_")
    app = _make_app(d)
    app.start()
    try:
        # 0. 系统自检就绪
        assert _wait(lambda: app.sim.system_ready, 4.0), "仿真未就绪"

        # 1. 裁判 START -> 3-2-1 -> 状态机自动开跑
        app.do_action("referee", {"cmd": "START"}, source="referee")
        st = app.referee.status()
        assert st["state"] == "READY" and st["prestart"] > 0
        assert _wait(lambda: app.referee.state == "RUNNING", 5.0), "发车未生效"
        assert _wait(lambda: app.bridge.is_running(), 2.0), "状态机未开跑"

        # 2. 热切模型:不重启节点(pid 不变),FPS 变化,日志留痕
        pid_before = app.hotswap.pid
        r = app.do_action("switch", {"kind": "model",
                                     "value": "yolov8n_int8"}, source="test")
        assert r["switch"]["pid"] == pid_before
        assert app.hotswap.state()["fps"] == 32.0
        assert _wait(lambda: len(app.hotswap.log_tail(5)) >= 1, 2.0)

        # 3. 操作员跳过当前节点 -> 记录 SKIPPED
        skipped = app.bridge.skip_current()
        assert skipped, "跳过的节点名不能为空"
        assert _wait(lambda: skipped in app.bridge.task_state()["skipped"], 2.0)

        # 4. 拔相机 -> 看门狗降级 -> 审计留痕
        app.do_action("fault", {"name": "camera_off"}, source="test")
        degraded = _wait(
            lambda: any(e["op"] == "WATCHDOG_DEGRADE" for e in app.audit.tail(50)),
            8.0)
        assert degraded, "看门狗未触发视觉降级"

        # 5. 急停 -> 零速度 + 黑匣子 + 裁判 FAULT + 审计
        app.do_action("estop", {"reason": "test"}, source="operator")
        assert app.referee.state == "FAULT"
        assert app.car.estop_count == 1
        bb = app.car.blackbox_tail(1)[0]
        assert bb["linear"] == 0.0 and bb["angular"] == 0.0
        ops = [e["op"] for e in app.audit.tail(50)]
        assert "EMERGENCY_STOP" in ops

        # 6. 审计链完整 + 报告落盘
        v = app.audit.verify()
        assert v["ok"], v
        report = app.save_report()
        assert report["operator"]["audit"]["verify"]["ok"]
        assert os.path.exists(os.path.join(d, "task_report.json"))

        # 7. HTTP 入口:页面 + 快照 + 动作 + 错误码
        OT.APP = app
        srv = ThreadingHTTPServer(("127.0.0.1", 0), OT.Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            with urllib.request.urlopen(base + "/", timeout=3) as resp:
                html = resp.read().decode("utf-8")
            assert "M5-3" in html and "EventSource" in html
            with urllib.request.urlopen(base + "/api/latest", timeout=3) as resp:
                snap = json.loads(resp.read().decode("utf-8"))
            assert snap["referee"]["state"] == "FAULT"
            # 非法动作 -> 400 + 中文错误,无 Traceback
            req = urllib.request.Request(
                base + "/api/action/nope", data=b"{}", method="POST",
                headers={"Content-Type": "application/json"})
            try:
                urllib.request.urlopen(req, timeout=3)
                raise AssertionError("应当返回 400")
            except urllib.error.HTTPError as e:
                body = json.loads(e.read().decode("utf-8"))
                assert e.code == 400 and "未知操作" in body["error"]
        finally:
            srv.shutdown()
            srv.server_close()
    finally:
        app.stop()
        shutil.rmtree(d, ignore_errors=True)


def test_voice_routing():
    assert TerminalApp.route_voice("开始") == "start"
    assert TerminalApp.route_voice("请暂停一下") == "pause"
    assert TerminalApp.route_voice("快停车") == "estop"
    assert TerminalApp.route_voice("求助裁判") == "help"
    assert TerminalApp.route_voice("今天天气不错") is None


def test_hard_reset_forbidden_during_race():
    d = tempfile.mkdtemp(prefix="m53_reset_")
    app = _make_app(d)
    try:
        app.referee.race_left = 100
        app.referee.state = "RUNNING"
        try:
            app.do_action("hard_reset")
            raise AssertionError("比赛中硬复位应当被拒绝")
        except ValueError as e:
            assert "禁止硬复位" in str(e)
    finally:
        shutil.rmtree(d, ignore_errors=True)
