"""quick_review 单元测试:指标聚合/根因/对比/落盘。"""
import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "scripts"))
import quick_review as qr          # noqa: E402


def _make_run(base, name, final="SUCCESS", faults=(), metrics=True,
              conf_shift=0.0):
    d = os.path.join(base, name)
    os.makedirs(d, exist_ok=True)
    report = {
        "mission_id": name, "final": final,
        "tasks": {
            "nav": {"type": "M", "status": final, "attempts": 2,
                    "retries": 1, "duration": 12.5, "fallback": None},
            "grasp": {"type": "M", "status": final, "attempts": 1,
                      "retries": 0, "duration": 8.0,
                      "fallback": "nav" if final == "FAILED" else None},
        },
        "fallback_used": {"grasp": "nav"} if final == "FAILED" else {},
        "final_context": {},
    }
    with open(os.path.join(d, "task_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f)
    rows = []
    if metrics:
        for i, (fq, cf) in enumerate([(9.7, 0.84 + conf_shift),
                                      (9.5, 0.85 + conf_shift),
                                      (0.0, 0.5 + conf_shift)]):
            rows.append({"ts": 1000 + i, "event": "metric",
                         "name": "perception_freq", "value": fq})
            rows.append({"ts": 1000 + i, "event": "metric",
                         "name": "confidence", "value": cf})
        rows.append({"ts": 1000, "event": "metric", "name": "cpu",
                     "value": 51.0})
        rows.append({"ts": 1000, "event": "metric", "name": "battery",
                     "value": 10.9})
    for ft in faults:
        rows.append({"ts": 2000, "event": "fault", **ft})
    with open(os.path.join(d, "blackbox.jsonl"), "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    return d


def test_metrics_aggregation():
    d = _make_run(os.path.dirname(os.path.abspath(__file__)), "t_metrics")
    s = qr.review_run(d)
    assert abs(s["metrics"]["perception_freq"] - (9.7 + 9.5 + 0.0) / 3) < 1e-6
    assert s["metrics"]["cpu_peak"] == 51.0
    assert s["metrics"]["battery_min"] == 10.9
    assert s["total_s"] == 20.5 and s["final"] == "SUCCESS"


def test_root_cause_composition():
    d = _make_run(os.path.dirname(os.path.abspath(__file__)), "t_fault",
                  final="FAILED",
                  faults=[{"type": "camera_degraded", "detail": "freq<10Hz",
                           "action": "nav fallback to lidar_only",
                           "cost_s": 9.2}])
    s = qr.review_run(d)
    assert any(c.startswith("camera_degraded@") and "freq<10Hz" in c
               and "+9.2s" in c for c in s["root_causes"])
    # 报告里的降级(fallback_used)也要兜进根因
    assert any("grasp" in c and "nav" in c for c in s["root_causes"])


def test_diff_with_prev():
    base = os.path.dirname(os.path.abspath(__file__))
    d1 = _make_run(base, "t_prev1")
    s1 = qr.review_run(d1)
    d2 = _make_run(base, "t_prev2", final="FAILED",
                   conf_shift=0.1,              # 让置信度确实发生变化
                   faults=[{"type": "LOW_BATTERY", "detail": "<11.5V"}])
    s2 = qr.review_run(d2, prev_dir=d1)
    cmp = s2["compare_with_prev"]
    assert cmp["new_faults"] == ["LOW_BATTERY"]
    assert "confidence" in cmp["metrics"]      # 指标变化被记录
    assert cmp["metrics"]["confidence"]["delta"] != 0
    assert os.path.exists(os.path.join(d2, "summary.json"))


def test_markdown_report_sections():
    d = _make_run(os.path.dirname(os.path.abspath(__file__)), "t_md",
                  final="FAILED",
                  faults=[{"type": "LOW_BATTERY", "detail": "<11.5V"}])
    s = qr.review_run(d)
    md = open(s["review_md"], encoding="utf-8").read()
    for sec in ("# 复盘报告", "## 各子任务", "## 关键指标", "## 故障根因"):
        assert sec in md
    assert "FAILED" in md and "LOW_BATTERY" in md
