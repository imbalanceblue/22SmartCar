#!/usr/bin/env python3
"""M5-4 单次运行复盘:30 秒看懂一把成败、关键指标、故障根因、与上一把差异。

输入(一个 run 目录):
  task_report.json   M5-1/M5-3 状态机报告(各节点状态/耗时/重试/降级)
  blackbox.jsonl     黑匣子(急停 /cmd_vel、指标、故障事件,JSONL)
  switch_log.csv     M5-3 热切换日志(参数/模型/场景变更留痕)

输出:
  终端彩色摘要(跑完直接看)
  review_<timestamp>.md  Markdown 报告(可直接贴群/写技术报告)
  summary.json           结构化摘要(供 exp.py compare 与"与上一把对比"使用)

用法:
  python3 scripts/quick_review.py --run-dir runs/run_005 [--prev runs/run_004]

注:真实车上 blackbox 是 rosbag2 的 .mcap;本环境为纯标准库 mock,
黑匣子以 JSONL 落盘(与 M5-3 car_side.py 同一条链路),解析逻辑等价。
"""
import argparse
import csv
import json
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))


def _color(enabled, code, text):
    return f"\033[{code}m{text}\033[0m" if enabled else text


def load_task_report(run_dir):
    path = os.path.join(run_dir, "task_report.json")
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_blackbox(run_dir):
    """黑匣子 JSONL。兼容 M5-3 的纯急停行(无 event 字段按 estop 处理)。"""
    path = os.path.join(run_dir, "blackbox.jsonl")
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            row.setdefault("event", "estop" if row.get("topic") == "/cmd_vel"
                           else "unknown")
            rows.append(row)
    return rows


def load_switch_log(run_dir):
    path = os.path.join(run_dir, "switch_log.csv")
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _fmt_ts(ms):
    try:
        return datetime.fromtimestamp(ms / 1000).strftime("%H:%M:%S")
    except (TypeError, ValueError, OSError):
        return "?"


def compute_metrics(rows):
    """从黑匣子事件聚合关键指标。"""
    agg = {"perception_freq": [], "confidence": [], "cpu": [], "temp": [],
           "battery": [], "nav_success": [], "path_opt": []}
    for r in rows:
        if r.get("event") == "metric" and r.get("name") in agg:
            try:
                agg[r["name"]].append(float(r["value"]))
            except (TypeError, ValueError):
                continue
    m = {}
    if agg["perception_freq"]:
        m["perception_freq"] = sum(agg["perception_freq"]) / len(agg["perception_freq"])
    if agg["confidence"]:
        m["confidence"] = sum(agg["confidence"]) / len(agg["confidence"])
    if agg["cpu"]:
        m["cpu_peak"] = max(agg["cpu"])
    if agg["temp"]:
        m["temp_peak"] = max(agg["temp"])
    if agg["battery"]:
        m["battery_min"] = min(agg["battery"])
    if agg["nav_success"]:
        m["nav_success_rate"] = sum(1 for v in agg["nav_success"] if v >= 0.5) / len(agg["nav_success"])
    if agg["path_opt"]:
        m["path_optimality"] = agg["path_opt"][-1]
    return {k: round(v, 4) for k, v in m.items()}


def collect_faults(rows):
    return [r for r in rows if r.get("event") == "fault"]


def find_root_causes(report, rows):
    """故障根因一句话:故障事件 -> (降级/换模型/超时) 的因果链。"""
    causes = []
    faults = collect_faults(rows)
    fallback = (report or {}).get("fallback_used") or {}
    for f in faults:
        ftype = f.get("type", "unknown")
        detail = f.get("detail", "")
        act = f.get("action", "")
        cost = f.get("cost_s")
        line = f"{ftype}@{_fmt_ts(f.get('ts'))}"
        if detail:
            line += f" ({detail})"
        if act:
            line += f" -> {act}"
        if cost is not None:
            line += f" (+{cost}s)"
        causes.append(line)
    for node, target in fallback.items():          # 报告里有降级但黑匣子漏记也兜住
        if not any(node in c for c in causes):
            dur = ((report or {}).get("tasks") or {}).get(node, {}).get("duration")
            extra = f" (+{dur}s)" if dur else ""
            causes.append(f"{node} failed -> fallback to {target}{extra}")
    for name, rec in ((report or {}).get("tasks") or {}).items():
        if rec.get("status") == "TIMEOUT":
            causes.append(f"{name} TIMEOUT@{rec.get('duration', '?')}s (不重试,直接降级)")
    return causes


def review_run(run_dir, prev_dir=None):
    """复盘一个 run,返回 summary dict 并落盘 summary.json + review_<ts>.md。"""
    report = load_task_report(run_dir)
    rows = load_blackbox(run_dir)
    switches = load_switch_log(run_dir)

    tasks = (report or {}).get("tasks") or {}
    total = round(sum(float(t.get("duration") or 0) for t in tasks.values()), 2)
    estops = [r for r in rows if r.get("event") == "estop"]
    summary = {
        "run": os.path.basename(run_dir.rstrip("/\\")),
        "reviewed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "mission_id": (report or {}).get("mission_id", "?"),
        "final": (report or {}).get("final", "UNKNOWN"),
        "total_s": total,
        "tasks": {n: {k: t.get(k) for k in ("status", "attempts", "retries",
                                            "duration", "fallback")}
                  for n, t in tasks.items()},
        "metrics": compute_metrics(rows),
        "faults": [{"type": f.get("type"), "ts": f.get("ts"),
                    "detail": f.get("detail")} for f in collect_faults(rows)],
        "root_causes": find_root_causes(report, rows),
        "estop_count": len(estops),
        "switch_changes": [{"ts": s.get("ts"), "type": s.get("type"),
                            "old": s.get("old_value"), "new": s.get("new_value")}
                           for s in switches],
    }

    if prev_dir:
        psum_path = os.path.join(prev_dir, "summary.json")
        if os.path.exists(psum_path):
            with open(psum_path, "r", encoding="utf-8") as f:
                prev = json.load(f)
            summary["compare_with_prev"] = diff_summaries(prev, summary)
        else:
            summary["compare_with_prev"] = {"note": "上一把无 summary.json,跳过对比"}

    with open(os.path.join(run_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    md = render_markdown(summary)
    md_path = os.path.join(run_dir, "review_"
                           + datetime.now().strftime("%Y%m%d_%H%M%S") + ".md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)
    summary["review_md"] = md_path
    return summary


def diff_summaries(prev, cur):
    """与上一把对比:指标增减、新增/消失故障、参数/模型变更。"""
    pm, cm = prev.get("metrics", {}), cur.get("metrics", {})
    metric_diff = {}
    for k in sorted(set(pm) | set(cm)):
        if k in pm and k in cm and pm[k] != cm[k]:
            metric_diff[k] = {"prev": pm[k], "cur": cm[k],
                              "delta": round(cm[k] - pm[k], 4)}
    pf = {f.get("type") for f in prev.get("faults", [])}
    cf = {f.get("type") for f in cur.get("faults", [])}
    prev_sw = {(s.get("type"), s.get("old"), s.get("new"))
               for s in prev.get("switch_changes", [])}
    cur_sw = {(s.get("type"), s.get("old"), s.get("new"))
              for s in cur.get("switch_changes", [])}
    return {
        "metrics": metric_diff,
        "new_faults": sorted(cf - pf),
        "gone_faults": sorted(pf - cf),
        "switch_changes": [{"type": t, "old": o, "new": n}
                           for t, o, n in sorted(cur_sw - prev_sw)],
        "time_delta_s": (round(cur.get("total_s", 0) - prev.get("total_s", 0), 2)),
    }


def render_markdown(s):
    lines = [f"# 复盘报告 — {s['run']}", "",
             f"- 任务结果:**{s['final']}**(总耗时 {s['total_s']}s,"
             f" 急停 {s['estop_count']} 次)",
             f"- mission: `{s['mission_id']}`,复盘时间:{s['reviewed_at']}", "",
             "## 各子任务", "",
             "| 节点 | 状态 | 尝试 | 重试 | 耗时s | 降级 |", "|--|--|--|--|--|--|"]
    for n, t in s["tasks"].items():
        lines.append(f"| {n} | {t['status']} | {t['attempts']} | "
                     f"{t['retries']} | {t['duration']} | {t['fallback'] or '-'} |")
    lines += ["", "## 关键指标", "", "| 指标 | 值 |", "|--|--|"]
    for k, v in s["metrics"].items():
        lines.append(f"| {k} | {v} |")
    lines += ["", "## 故障根因", ""]
    lines += [f"- {c}" for c in s["root_causes"]] or ["- 无故障 ✅"]
    if s["switch_changes"]:
        lines += ["", "## 本把热切换", "",
                  "| 时间 | 类型 | 旧 | 新 |", "|--|--|--|--|"]
        for c in s["switch_changes"]:
            lines.append(f"| {c['ts']} | {c['type']} | {c['old']} | {c['new']} |")
    cmp = s.get("compare_with_prev")
    if cmp and "metrics" in cmp:
        lines += ["", "## 与上一把对比", ""]
        if cmp.get("time_delta_s") is not None:
            lines.append(f"- 总耗时变化:{cmp['time_delta_s']:+}s")
        for k, d in cmp["metrics"].items():
            lines.append(f"- {k}:{d['prev']} -> {d['cur']}({d['delta']:+})")
        if cmp.get("new_faults"):
            lines.append(f"- 新增故障:{', '.join(cmp['new_faults'])}")
        if cmp.get("gone_faults"):
            lines.append(f"- 消失故障:{', '.join(cmp['gone_faults'])}")
        for c in cmp.get("switch_changes", []):
            lines.append(f"- 参数变更:{c['type']} {c['old']} -> {c['new']}")
        if not cmp["metrics"] and not cmp.get("new_faults") \
                and not cmp.get("switch_changes"):
            lines.append("- 与上一把无显著差异")
    return "\n".join(lines) + "\n"


def print_summary(s, color=True):
    def mk(code):
        return lambda t: _color(color, code, t)
    ok, bad, warn = mk("32"), mk("31"), mk("33")
    final = s["final"]
    tag = ok(final) if final == "SUCCESS" else bad(final)
    print(f"\n===== 复盘 {s['run']} =====")
    print(f"结果: {tag} | 总耗时 {s['total_s']}s | 急停 {s['estop_count']} 次")
    for n, t in s["tasks"].items():
        mark = {"SUCCESS": ok, "FAILED": bad, "SKIPPED": warn}.get(
            t["status"], lambda x: x)
        line = f"  {n:<18} {t['status']:<8} attempts={t['attempts']} " \
               f"retries={t['retries']} {t['duration']}s"
        if t.get("fallback"):
            line += f" 降级->{t['fallback']}"
        print(mark(line))
    print("指标: " + "  ".join(f"{k}={v}" for k, v in s["metrics"].items()))
    if s["root_causes"]:
        print("根因:")
        for cause in s["root_causes"]:
            print(f"  ! {cause}")
    else:
        print("根因: 无故障 ✅")
    cmp = s.get("compare_with_prev")
    if cmp and "metrics" in cmp:
        bits = []
        if cmp.get("time_delta_s") is not None:
            bits.append(f"耗时{cmp['time_delta_s']:+}s")
        bits += [f"{k}{d['delta']:+}" for k, d in cmp["metrics"].items()]
        if cmp.get("new_faults"):
            bits.append(f"新增故障 {','.join(cmp['new_faults'])}")
        print("对比上一把: " + ("; ".join(bits) if bits else "无显著差异"))
    print(f"报告: {s.get('review_md', '(md 已写入 run 目录)')}\n")


def main():
    ap = argparse.ArgumentParser(description="M5-4 单次运行复盘")
    ap.add_argument("--run-dir", required=True, help="run 目录(含 task_report.json 等)")
    ap.add_argument("--prev", default=None, help="上一把 run 目录(可选,用于对比)")
    ap.add_argument("--no-color", action="store_true", help="关闭彩色输出")
    args = ap.parse_args()
    if not os.path.isdir(args.run_dir):
        print(f"[ERROR] run 目录不存在: {args.run_dir}")
        sys.exit(1)
    s = review_run(args.run_dir, prev_dir=args.prev)
    print_summary(s, color=not args.no_color and sys.stdout.isatty())


if __name__ == "__main__":
    main()
