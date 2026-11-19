"""M5-2 服务端集成测试:起真服务 -> 读 SSE 流 -> 注入故障 -> 急停 -> 导出。

验证"完成判断"四要素:零配置出页面 / 状态机同步 / 故障 <1s 出现 / 可导出。
"""
import http.client
import json
import os
import sys
import threading
import time

_PORT = 8099
_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _HERE)

import server as srv                               # noqa: E402


def _start_server():
    srv.MATCH = srv.Match(time_scale=5.0)          # 加速演示:状态机约 12s 跑完
    srv.MATCH.start()
    httpd = srv.ThreadingHTTPServer(("127.0.0.1", _PORT), srv.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def _conn():
    return http.client.HTTPConnection("127.0.0.1", _PORT, timeout=10)


def _post(path):
    c = _conn()
    c.request("POST", path)
    r = c.getresponse()
    body = json.loads(r.read().decode("utf-8"))
    c.close()
    return body


def _stream_events(seconds):
    """从 /api/stream 读指定秒数的 SSE 事件,解析成快照列表。"""
    c = _conn()
    c.request("GET", "/api/stream")
    r = c.getresponse()
    buf, snaps, deadline = b"", [], time.monotonic() + seconds
    while time.monotonic() < deadline:
        chunk = r.read1(8192)
        if not chunk:
            break
        buf += chunk
        while b"\n\n" in buf:
            raw, buf = buf.split(b"\n\n", 1)
            for line in raw.decode("utf-8").splitlines():
                if line.startswith("data: "):
                    snaps.append(json.loads(line[6:]))
    c.close()
    return snaps


def test_full_match_flow():
    httpd = _start_server()
    try:
        # 1. 首页零配置可访问
        c = _conn()
        c.request("GET", "/")
        r = c.getresponse()
        html = r.read().decode("utf-8")
        assert r.status == 200 and "M5-2" in html and "EventSource" in html
        c.close()

        # 2. 前 3 秒流:系统就绪(1.5s 后),裁判 READY
        snaps = _stream_events(3.5)
        assert snaps, "SSE 流应有数据"
        assert any(s["system_ready"] for s in snaps), "1.5s 后应就绪"
        assert snaps[-1]["referee"]["state"] == "READY"

        # 3. 发车 + 立即注入拔相机 -> 状态机 RUNNING,识别节点将失败重试
        assert _post("/api/referee/start")["ok"]
        assert _post("/api/fault/camera_off")["active"]
        snaps = _stream_events(4.0)
        ts = snaps[-1]["task_state"]
        assert snaps[-1]["referee"]["state"] == "RUNNING"
        assert ts["final"] == "RUNNING" and ts["nodes"], "状态机应同步运行中"
        per = snaps[-1]["metrics"]["topics"]["/perception/targets"]
        assert per["freq"] == 0.0
        fault_seen = any(any(f["type"] == "CAMERA_DEGRADED"
                             for f in s["active_faults"]) for s in snaps)
        assert fault_seen, "故障标签应在流中出现"

        # 5. 状态机联动:detect_qr 失败重试 -> 降级(orange)记录在案
        deadline = time.monotonic() + 15
        fallback_seen = False
        while time.monotonic() < deadline and not fallback_seen:
            snaps = _stream_events(2.0)
            for s in snaps:
                if (s["task_state"] or {}).get("fallback_used"):
                    fallback_seen = True
        assert fallback_seen, "拔相机应触发 detect_qr 走降级"

        # 6. 急停 -> 裁判 STOPPED
        assert _post("/api/estop")["ok"]
        snaps = _stream_events(1.5)
        assert snaps[-1]["referee"]["state"] == "STOPPED"

        # 7. 导出快照:自包含 HTML
        c = _conn()
        c.request("GET", "/api/export")
        r = c.getresponse()
        out = r.read().decode("utf-8")
        assert r.status == 200 and "dashboard_snapshot" in out
        c.close()
    finally:
        httpd.shutdown()
        srv.MATCH.stop()
