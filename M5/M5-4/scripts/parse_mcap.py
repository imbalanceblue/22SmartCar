#!/usr/bin/env python3
"""关键话题提取:只解析白名单内的数据,这是"30 秒出结论"的前提。

为什么要单独一个模块:
一个 180s 的比赛 bag 可能几百 MB,全量解析必然超 30 秒。
本模块把"哪些该读"集中定义在一处,quick_review / exp 都复用同一份口径,
避免两处各写一遍白名单然后慢慢走偏。

真实车上 blackbox 是 rosbag2 的 .mcap;本环境用 JSONL 模拟同一份结构,
接口保持一致 —— 换成真 mcap 只需替换 iter_records() 的读取分支,
上层的 compute_metrics / find_root_causes 一行都不用改。
"""
import json

# 关键话题白名单:只这些参与指标计算
TOPIC_WHITELIST = (
    "/cmd_vel",
    "/perception/targets",
    "/navigation/pose",
    "/referee/status",
)

# 事件类记录(没有 topic 字段,但有 event)。
# metric=聚合指标样本、sample=话题定时样本,这两类才是算指标的主料。
IMPORTANT_EVENTS = ("estop", "fault", "degrade", "healed", "metric", "sample")


def is_important(row):
    """一条记录是否值得进入分析。"""
    return row.get("topic") in TOPIC_WHITELIST \
        or row.get("event") in IMPORTANT_EVENTS


def _iter_jsonl(path):
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except ValueError:
                continue                    # 坏行跳过:现场数据不保证干净


def _iter_mcap(path):
    """真 mcap 分支:需要 pip install mcap,只取白名单话题。"""
    try:
        from mcap.reader import make_reader
    except ImportError as e:
        raise RuntimeError(
            "解析 .mcap 需要 mcap 库:pip install mcap") from e
    with open(path, "rb") as f:
        reader = make_reader(f)
        for _schema, channel, message in reader.iter_messages(
                topics=list(TOPIC_WHITELIST)):
            yield {"topic": channel.topic, "stamp": message.log_time,
                   "event": "sample"}


def iter_records(path):
    """逐条产出记录。按后缀分派,失败给中文提示而不是 traceback。"""
    if path.endswith((".jsonl", ".json", ".ndjson")):
        yield from _iter_jsonl(path)
        return
    if path.endswith(".mcap"):
        yield from _iter_mcap(path)
        return
    raise ValueError(f"不支持的记录格式: {path}(支持 .jsonl / .mcap)")


def load_key_records(run_dir, filename="blackbox.jsonl"):
    """从一次运行目录里读出关键记录(已过滤 + 补齐 event 字段)。"""
    import os
    path = os.path.join(run_dir, filename)
    if not os.path.exists(path):
        return []
    rows = []
    for row in iter_records(path):
        row.setdefault("event", "estop" if row.get("topic") == "/cmd_vel"
                       else "unknown")
        if is_important(row):
            rows.append(row)
    return rows


if __name__ == "__main__":
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else "."
    recs = load_key_records(target)
    print(f"关键记录 {len(recs)} 条(白名单 {len(TOPIC_WHITELIST)} 个话题)")
    counts = {}
    for r in recs:
        key = r.get("topic") or r.get("event")
        counts[key] = counts.get(key, 0) + 1
    for k, v in sorted(counts.items()):
        print(f"  {k:28s} {v}")
