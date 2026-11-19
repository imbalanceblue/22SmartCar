"""M5-3 审计日志:只追加 + 哈希链,赛后可直接作为申诉/复盘证据链。

设计要点(答辩会问):
- 只追加:open(..., "a"),从不改写历史行;
- 哈希链:每条记录含 prev_hash,自身 hash = sha256(规范化 JSON),
  改动任意一条 -> 后面全部断链,verify() 能定位到第几条;
- 毫秒时间戳:可与 M5-2 的 10Hz 快照对齐(证据可追溯到毫秒级话题数据)。
"""
import hashlib
import json
import os
import threading
from collections import deque
from datetime import datetime, timezone

GENESIS = "0" * 64                       # 创世哈希(链的起点)
_HERE = os.path.dirname(os.path.abspath(__file__))


def _canonical(obj):
    """规范化 JSON:键排序 + 紧凑分隔符,保证同一内容哈希唯一。"""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


class AuditLog:
    """append-only 审计日志。进程重启后从文件尾部恢复链。"""

    def __init__(self, path=None, operator="operator"):
        today = datetime.now().strftime("%Y-%m-%d")
        self.path = path or os.path.join(_HERE, f"audit_log_{today}.jsonl")
        self.operator = operator
        self._lock = threading.Lock()
        self.seq = 0
        self.prev_hash = GENESIS
        self.recent = deque(maxlen=100)     # 给界面显示的尾巴
        self._restore()

    def _restore(self):
        """重启后接着上一条写,不能从创世重开(否则链断)。"""
        if not os.path.exists(self.path):
            return
        last = None
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    last = line
        if not last:
            return
        try:
            rec = json.loads(last)
            self.seq = int(rec["seq"])
            self.prev_hash = rec["hash"]
        except (ValueError, KeyError, TypeError):
            pass                        # 文件损坏:从创世续写,verify 会报断链

    def append(self, op, params=None, source="operator", result="ok",
               operator=None, note=None):
        """写一条操作记录,返回该条(含 hash)。"""
        with self._lock:
            now = datetime.now(timezone.utc)
            entry = {
                "seq": self.seq + 1,
                "ts": int(now.timestamp() * 1000),          # 毫秒
                "ts_iso": now.astimezone().isoformat(timespec="milliseconds"),
                "op": op,
                "params": params or {},
                "source": source,                            # operator/referee/voice/watchdog/keyboard/system
                "operator": operator or self.operator,
                "result": result,
                "prev_hash": self.prev_hash,
            }
            if note:
                entry["note"] = note
            entry["hash"] = hashlib.sha256(
                _canonical(entry).encode("utf-8")).hexdigest()
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            self.seq = entry["seq"]
            self.prev_hash = entry["hash"]
            self.recent.append(entry)
            return entry

    def verify(self):
        """从磁盘重算整条链(不信任内存),返回断链位置。"""
        if not os.path.exists(self.path):
            return {"ok": True, "count": 0, "message": "空日志"}
        prev = GENESIS
        n = 0
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                n += 1
                try:
                    rec = json.loads(line)
                except ValueError:
                    return {"ok": False, "count": n,
                            "message": f"第 {n} 行不是合法 JSON"}
                expect = rec.pop("hash", None)
                actual = hashlib.sha256(
                    _canonical(rec).encode("utf-8")).hexdigest()
                if rec.get("prev_hash") != prev:
                    return {"ok": False, "count": n,
                            "message": f"第 {n} 条 prev_hash 断链"}
                if expect != actual:
                    return {"ok": False, "count": n,
                            "message": f"第 {n} 条哈希被篡改"}
                prev = expect
        return {"ok": True, "count": n, "message": "哈希链完整"}

    def tail(self, n=20):
        return list(self.recent)[-n:]

    def export_text(self):
        """赛后导出:原样返回 JSONL(带 Content-Disposition 下载)。"""
        if not os.path.exists(self.path):
            return ""
        with open(self.path, "r", encoding="utf-8") as f:
            return f.read()
