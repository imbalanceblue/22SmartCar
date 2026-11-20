#!/usr/bin/env python3
"""裁判模拟器:发 START / PAUSE / RESUME / STOP / PENALTY / SCORE_QUERY。

两种模式:
    python tools/referee_sim.py --mode keyboard          # 按 S/P/R/E/Q 发指令
    python tools/referee_sim.py --script "START,PAUSE,RESUME" --delay 2

它走的是终端暴露的 HTTP 接口 /api/referee/<CMD>,
与"裁判从话题下发"在终端里汇入同一条 feed() 通道 ——
所以演练时看到的行为和真裁判一致。

键盘映射(题面要求:S/P/R/E):
    S=START  P=PAUSE  R=RESUME  E=STOP  Q=SCORE_QUERY
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request

KEYS = {"s": "START", "p": "PAUSE", "r": "RESUME", "e": "STOP",
        "q": "SCORE_QUERY"}


def send(base, cmd, args=None):
    """POST /api/referee/<CMD>,返回终端回执。"""
    body = json.dumps(args or {}).encode("utf-8")
    req = urllib.request.Request(f"{base}/api/referee/{cmd}", data=body,
                                 method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": e.read().decode("utf-8", "ignore")}
    except urllib.error.URLError as e:
        return {"ok": False, "error": f"连不上终端: {e.reason}"}


def main():
    ap = argparse.ArgumentParser(description="M5-3 裁判模拟器")
    ap.add_argument("--base", default="http://127.0.0.1:8081",
                    help="终端地址(默认 8081,与 M5-2 的 8080 错开)")
    ap.add_argument("--mode", choices=["keyboard", "script"], default="keyboard")
    ap.add_argument("--script", default="START,PAUSE,RESUME,STOP",
                    help="script 模式的指令序列,逗号分隔")
    ap.add_argument("--delay", type=float, default=2.0, help="script 模式间隔秒")
    args = ap.parse_args()

    if args.mode == "script":
        for cmd in [c.strip().upper() for c in args.script.split(",") if c.strip()]:
            print(f"[发] {cmd} -> {send(args.base, cmd)}")
            time.sleep(args.delay)
        return 0

    print(f"裁判模拟器已连 {args.base}  (S=START P=PAUSE R=RESUME E=STOP Q=查询, Ctrl+C 退出)")
    try:
        while True:
            key = input("指令> ").strip().lower()[:1]
            if not key:
                continue
            if key not in KEYS:
                print(f"  未知按键 {key},可用: S/P/R/E/Q")
                continue
            cmd = KEYS[key]
            print(f"  [发] {cmd} -> {send(args.base, cmd)}")
    except (KeyboardInterrupt, EOFError):
        print("\n退出")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
