"""审计日志单测:哈希链完整性 / 篡改检测 / 重启续链。"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audit import AuditLog                            # noqa: E402


def _tmpdir():
    return tempfile.mkdtemp(prefix="m53_audit_")


def test_chain_ok():
    d = _tmpdir()
    try:
        log = AuditLog(path=os.path.join(d, "a.jsonl"))
        log.append("TERMINAL_START", {"pid": 1}, source="system")
        log.append("MISSION_START", {}, source="referee")
        log.append("EMERGENCY_STOP", {"zero_velocity": True}, source="operator")
        v = log.verify()
        assert v["ok"] and v["count"] == 3, v
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_tamper_detected():
    d = _tmpdir()
    try:
        path = os.path.join(d, "a.jsonl")
        log = AuditLog(path=path)
        log.append("MISSION_START", {}, source="referee")
        log.append("SKIP_NODE", {"node": "detect_qr"}, source="operator")
        # 篡改第 2 条(改操作名),链必须断
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        rec = json.loads(lines[1])
        rec["op"] = "NO_OP"
        lines[1] = json.dumps(rec, ensure_ascii=False) + "\n"
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(lines)
        v = AuditLog(path=path).verify()
        assert not v["ok"] and v["count"] == 2, v
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_resume_chain_after_restart():
    d = _tmpdir()
    try:
        path = os.path.join(d, "a.jsonl")
        AuditLog(path=path).append("A", {}, source="system")
        second = AuditLog(path=path)          # 模拟进程重启
        entry = second.append("B", {}, source="system")
        assert entry["seq"] == 2, entry
        assert second.verify()["ok"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_prev_hash_links():
    d = _tmpdir()
    try:
        log = AuditLog(path=os.path.join(d, "a.jsonl"))
        e1 = log.append("A")
        e2 = log.append("B")
        assert e2["prev_hash"] == e1["hash"]
        assert e1["prev_hash"] == "0" * 64
    finally:
        shutil.rmtree(d, ignore_errors=True)
