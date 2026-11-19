"""M5-2 主机端 Web 服务(纯标准库:HTTP + SSE 实时推送)。

推送技术选型(如实披露):题目原文允许 "FastAPI + WebSocket + Plotly Dash
任选其一"。本实现用标准库 http.server + SSE(Server-Sent Events):
- 与 WebSocket 同为"服务端到浏览器"的单向实时推送(仪表盘正是单向场景);
- EventSource 是浏览器原生 API,零第三方依赖,断网现场可跑;
- 如需浏览器→服务端高频上行(本页用普通 POST 已够),再升级 rosbridge/WS。

运行:python3 server.py --no-open   (--open 默认自动打开浏览器)
"""
import argparse
import json
import threading
import time
import os
import sys
import webbrowser
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from aggregator import Aggregator                 # noqa: E402
from mock_source import CarSim                    # noqa: E402
from state_bridge import StateBridge              # noqa: E402

MISSION_YAML = os.path.join(_HERE, "mission_demo.yaml")
INDEX_HTML = os.path.join(_HERE, "static", "index.html")
TICK = 0.1                                        # 10Hz


class Match:
    """把仿真源 / 聚合器 / 状态机桥拧在一起,驱动 10Hz 主循环。"""

    def __init__(self, mission_yaml=MISSION_YAML, time_scale=20.0):
        mission = mission_yaml
        if mission.endswith(".yaml"):
            try:
                import yaml                       # noqa: F401
            except ImportError:
                mission = mission[:-5] + ".json"  # 无 pyyaml 环境降级用 JSON
        self.sim = CarSim()
        self.agg = Aggregator()
        self.bridge = StateBridge(mission, self.sim,
                                  time_scale=time_scale)
        self.history = deque(maxlen=1800)         # 保留 180s 快照
        self.lock = threading.Lock()
        self.started = time.monotonic()
        self._stop = threading.Event()

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.is_set():
            self.sim.tick(TICK)
            for topic, stamp, conf in self.sim.messages():
                self.agg.on_topic(topic, stamp=stamp, conf=conf)
            self.agg.on_hw(battery=self.sim.battery, cpu=self.sim.cpu,
                           temp=self.sim.temp)
            if self.sim.faults["camera_off"]:
                pass                              # 故障已由 messages() 体现
            snap = self.agg.snapshot(
                task_state=self.bridge.task_state(),
                referee=dict(self.sim.referee),
                system_ready=self.sim.system_ready and not self.sim.estopped)
            with self.lock:
                self.history.append(snap)
            self._stop.wait(TICK)

    def snapshot_now(self):
        with self.lock:
            return self.history[-1] if self.history else None

    def history_list(self):
        with self.lock:
            return list(self.history)


MATCH = None                                       # 全局单例(server 启动时建)


def export_snapshot_html(history):
    """赛后一键导出:把全程数据嵌进一个自包含 HTML(可离线回放)。"""
    data = json.dumps(history, ensure_ascii=False)
    return f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>Dashboard Snapshot</title></head><body style="font-family:monospace">
<h2>dashboard_snapshot(离线回放)</h2>
<p>快照数:{len(history)} | 由 M5-2 导出</p>
<div id="log"></div>
<script>
const H = {data};
document.getElementById('log').innerHTML = H.map(s =>
  `[t=${{s.ts}}] final=${{s.task_state.final||'IDLE'}} ` +
  `faults=${{s.active_faults.map(f=>f.type).join(',')||'none'}} ` +
  `battery=${{s.metrics.battery}}V cpu=${{s.metrics.cpu}}% ` +
  `referee=${{s.referee.state}} ${{s.referee.countdown}}s`
).join('<br>');
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"                  # 每请求一连接,SSE 友好

    def log_message(self, fmt, *args):             # 静默访问日志
        pass

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def do_HEAD(self):
        """浏览器/工具的 HEAD 探测(gio open 会先探测),返回 200 即可。"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            with open(INDEX_HTML, "rb") as f:
                self._send(200, f.read(), "text/html; charset=utf-8")
        elif path == "/api/stream":                # SSE:10Hz 快照流
            self._sse()
        elif path == "/api/export":                # 赛后一键导出
            html = export_snapshot_html(MATCH.history_list()).encode("utf-8")
            self._send(200, html, "text/html; charset=utf-8")
        elif path == "/api/latest":
            snap = MATCH.snapshot_now()
            self._json(snap if snap else {}, 200)
        else:
            self._json({"error": f"未知路径 {path}"}, 404)

    def _sse(self):
        """每个客户端一个循环:100ms 推一张快照,断开即止。"""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        try:
            while True:
                snap = MATCH.snapshot_now()
                if snap is not None:
                    payload = json.dumps(snap, ensure_ascii=False)
                    self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                    self.wfile.flush()
                time.sleep(TICK)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return                                 # 客户端断开,正常

    def do_POST(self):
        path = urlparse(self.path).path
        sim = MATCH.sim
        try:
            if path == "/api/referee/start":
                sim.start()
                MATCH.bridge.start_mission()
            elif path == "/api/referee/pause":
                sim.pause()
            elif path == "/api/referee/resume":
                sim.resume()
            elif path == "/api/referee/foul":
                sim.foul()
            elif path.startswith("/api/fault/"):
                name = path.rsplit("/", 1)[-1]
                on = sim.toggle_fault(name)
                self._json({"fault": name, "active": on})
                return
            elif path == "/api/estop":
                sim.emergency_stop()
            elif path == "/api/reset":             # 软复位
                sim.__init__(seed=42)
                MATCH.bridge.restart()
            else:
                self._json({"error": f"未知操作 {path}"}, 404)
                return
            self._json({"ok": True, "path": path})
        except ValueError as e:
            self._json({"error": str(e)}, 400)


def main():
    global MATCH
    parser = argparse.ArgumentParser(description="M5-2 仪表盘服务")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--open", dest="open_browser", action="store_true",
                        default=True, help="启动后自动打开浏览器(零配置)")
    parser.add_argument("--no-open", dest="open_browser", action="store_false")
    args = parser.parse_args()

    MATCH = Match()
    MATCH.start()
    server = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}"
    print(f"[M5-2] 仪表盘服务已启动: {url}  (Ctrl+C 停止)")
    if args.open_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[M5-2] 已停止")


if __name__ == "__main__":
    main()
