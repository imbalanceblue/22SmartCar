"""M5-3 操作员终端(Web 页面):比赛现场唯一入口。

启动:
    python3 host_side/operator_terminal.py            # 自动打开浏览器,端口 8081
    python3 host_side/operator_terminal.py --no-open  # 不自动开(VM 里用 epiphany 手动开)
    python3 host_side/operator_terminal.py --port 8081 --race-seconds 180

端口固定 8081:M5-2 的仪表盘默认占 8080,现场两者会同时跑,
端口冲突会导致其中一个起不来(题面明确要求错开)。

只依赖标准库(HTTP + SSE),现场断网也能跑 —— 与 M5-2 同一套取舍。
"""
import argparse
import json
import os
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from terminal_app import TerminalApp                  # noqa: E402

_ROOT = os.path.dirname(_HERE)
MISSION_YAML = os.path.join(_ROOT, "config", "mission_race.yaml")
INDEX_HTML = os.path.join(_HERE, "templates", "terminal.html")
TICK = 0.1

APP = None                                            # 全局单例
EXIT_ON_ESTOP = False                                 # 仅自测用


def _mission_path():
    """无 pyyaml 环境自动降级用 JSON 孪生文件(本地开发常见)。"""
    return _resolve(MISSION_YAML)


def _resolve(mission):
    if not mission.endswith(".yaml"):
        return mission
    try:
        import yaml                                    # noqa: F401
        return mission
    except ImportError:
        alt = mission[:-5] + ".json"
        return alt if os.path.exists(alt) else mission


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, fmt, *args):
        pass

    # ---------- 基础发送 ----------
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
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ---------- GET ----------
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            with open(INDEX_HTML, "rb") as f:
                self._send(200, f.read(), "text/html; charset=utf-8")
        elif path == "/api/stream":
            self._sse()
        elif path == "/api/latest":
            self._json(APP.latest() or {})
        elif path == "/api/audit/verify":
            self._json(APP.audit.verify())
        elif path == "/api/audit/export":
            body = APP.audit.export_text().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.send_header("Content-Disposition",
                             'attachment; filename="audit_log.jsonl"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/audit":
            self._json({"count": APP.audit.seq, "verify": APP.audit.verify(),
                        "tail": APP.audit.tail(50), "path": APP.audit.path})
        elif path == "/api/switch_log":
            self._json({"rows": APP.hotswap.log_tail(50), "state":
                        APP.hotswap.state()})
        elif path == "/api/report":
            self._json(APP.save_report())
        else:
            self._json({"error": f"未知路径 {path}"}, 404)

    def _sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        try:
            while True:
                snap = APP.latest()
                if snap is not None:
                    self.wfile.write(
                        f"data: {json.dumps(snap, ensure_ascii=False)}\n\n"
                        .encode("utf-8"))
                    self.wfile.flush()
                threading.Event().wait(TICK)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    # ---------- POST ----------
    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except ValueError:
            self._json({"error": "请求体不是合法 JSON"}, 400)
            return
        try:
            if path.startswith("/api/action/"):
                action = path.rsplit("/", 1)[-1]
                result = APP.do_action(action, payload)
                self._json(result)
            elif path.startswith("/api/referee/"):
                cmd = path.rsplit("/", 1)[-1].upper()
                self._json(APP.do_action("referee", {"cmd": cmd,
                                                     "args": payload}))
            else:
                self._json({"error": f"未知操作 {path}"}, 404)
        except ValueError as e:
            self._json({"error": str(e)}, 400)
        except Exception as e:                     # 兜底:不返回 Traceback
            self._json({"error": f"内部错误: {e}"}, 500)


def main():
    global APP
    parser = argparse.ArgumentParser(description="M5-3 操作员终端")
    parser.add_argument("--port", type=int, default=8081)
    parser.add_argument("--mission", default=MISSION_YAML)
    parser.add_argument("--race-seconds", type=float, default=180.0)
    parser.add_argument("--time-scale", type=float, default=4.0,
                        help="mock 耗时放大倍数(演示用)")
    parser.add_argument("--no-open", dest="open_browser", action="store_false",
                        default=True)
    parser.add_argument("--no-tts", dest="tts", action="store_false",
                        default=True)
    parser.add_argument("--audit", default=None, help="审计日志路径")
    parser.add_argument("--switch-log", default=None, help="切换日志路径")
    parser.add_argument("--exit-on-estop", action="store_true",
                        help="自测用:急停后退出进程")
    args = parser.parse_args()

    global EXIT_ON_ESTOP
    EXIT_ON_ESTOP = args.exit_on_estop

    APP = TerminalApp(_resolve(args.mission), time_scale=args.time_scale,
                      race_seconds=args.race_seconds,
                      audit_path=args.audit, switch_log=args.switch_log,
                      enable_tts=args.tts)
    APP.audit.append("TERMINAL_START",
                     {"mission": os.path.basename(args.mission),
                      "port": args.port,
                      "pid": os.getpid()}, source="system")
    APP.start()

    server = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}"
    print(f"[M5-3] 操作员终端已启动: {url}")
    print(f"       审计日志: {APP.audit.path}")
    print(f"       切换日志: {APP.hotswap.log_path}")
    print("       键盘模拟裁判: S=发车 P=暂停 R=继续 E=急停(仅真终端)")
    if args.open_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        APP.save_report()
        print("\n[M5-3] 已停止,报告已写入 task_report.json")


if __name__ == "__main__":
    main()
