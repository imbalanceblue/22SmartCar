#!/usr/bin/env python3
"""审计链校验:逐行复算哈希并检查 prev_hash 是否首尾相接。

用法:
    python tools/verify_audit.py --log audit_log_20260922.jsonl

输出 PASS / FAIL(指出第几条被篡改或断链),退出码 0 / 1。

能防什么:改动任意一行的字段 -> 该行 hash 对不上 -> 立刻检出;
         删掉中间某行 -> 下一行的 prev_hash 对不上 -> 检出。
不能防什么:删掉末尾若干行。链尾没有"后一行"来指认它,
         所以需要另存一个"预期条数"或把最新 hash 定期外发(本工具不覆盖)。
"""
import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "host_side"))

from audit import AuditLog                            # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="校验审计日志哈希链")
    ap.add_argument("--log", required=True, help="audit_log_<date>.jsonl 路径")
    args = ap.parse_args()

    if not os.path.exists(args.log):
        print(f"[FAIL] 文件不存在: {args.log}")
        return 2

    log = AuditLog(path=args.log)                     # 构造时会重建链状态
    result = log.verify()
    if result["ok"]:
        print(f"PASS  {args.log}  共 {log.seq} 条,链完整")
        return 0
    print(f"FAIL  {args.log}  {result['message']}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
