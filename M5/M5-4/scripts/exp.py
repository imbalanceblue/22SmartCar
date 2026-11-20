#!/usr/bin/env python3
"""M5-4 参数/模型实验管理器:一条命令建实验、跑 N 把、对比、推基线。

  python3 scripts/exp.py new     --name nav_speed_0.9 --base scene_race.yaml \
                 --set nav_speed=0.9 --desc "提高直线速度测试"
  python3 scripts/exp.py run     --name nav_speed_0.9 --runs 3
  python3 scripts/exp.py compare --names nav_speed_0.8,nav_speed_0.9 \
                 --metric nav_time,success_rate
  python3 scripts/exp.py promote --name nav_speed_0.9 --to scene_race.yaml

目录约定(相对 M5-4 根):
  config/scene_*.yaml|.json   场景配置(实验只存差异值)
  config/experiments/registry.json  实验元数据(基线/修改项/目的/运行结果)
  runs/<exp>_<i>/             每把产物(task_report/blackbox/switch_log/summary)

注:真实环境 run 阶段会"热加载参数 -> 真车跑一把";本环境无真车,
用确定性 seeded 仿真代替(同参同结果),但热加载留痕(switch_log.csv)、
复盘链路(quick_review)与对比逻辑与真实环境完全一致。
git 操作在无 git 环境自动降级为仅打印命令。
"""
import argparse
import csv
import json
import os
import random
import subprocess
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                      # M5-4 根目录
sys.path.insert(0, HERE)
import quick_review as qr                          # noqa: E402

CONFIG_DIR = os.path.join(ROOT, "config")
EXP_DIR = os.path.join(ROOT, "config", "experiments")
RUNS_DIR = os.path.join(ROOT, "runs")
REGISTRY = os.path.join(EXP_DIR, "registry.json")

# ---------------- 配置读写(YAML 可选,自动 JSON 孪生) ----------------

def _cfg_path(name):
    """config/ 下找 scene_<name>.yaml / .json。容忍传入 scene_xxx.yaml 全名。"""
    name = os.path.basename(str(name))
    for ext in (".yaml", ".yml", ".json"):
        if name.endswith(ext):
            name = name[:-len(ext)]
    if name.startswith("scene_"):
        name = name[len("scene_"):]
    for ext in (".yaml", ".yml", ".json"):
        p = os.path.join(CONFIG_DIR, f"scene_{name}{ext}")
        if os.path.exists(p):
            return p
    return None


def load_cfg(path):
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    if path.endswith(".json"):
        return json.loads(text)
    try:
        import yaml
        return yaml.safe_load(text)
    except ImportError:
        twin = path[:-5] + ".json" if path.endswith(".yaml") else None
        if twin and os.path.exists(twin):
            with open(twin, "r", encoding="utf-8") as f:
                return json.load(f)
        raise


def dump_cfg(data, path):
    """写配置。yaml 可用则写 yaml(+JSON 孪生同步);不可用只写 JSON 孪生。

    注意:必须先探测 pyyaml 再打开文件——先 open('w') 截断再失败,
    会留下空 yaml(真踩过的坑:VM 有 pyyaml,把空文件解析成 None 崩溃)。
    """
    def _write_json(p):
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    if path.endswith(".json"):
        _write_json(path)
        return
    try:
        import yaml                                  # noqa: F401  先探测
        have_yaml = True
    except ImportError:
        have_yaml = False
    if have_yaml:
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
        _write_json(path[:-5] + ".json")             # 孪生同步,便于无 yaml 环境
    else:
        _write_json(path[:-5] + ".json")             # 不动 yaml 文件本身


# ---------------- 实验注册表 ----------------

def load_registry():
    if os.path.exists(REGISTRY):
        with open(REGISTRY, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_registry(reg):
    os.makedirs(EXP_DIR, exist_ok=True)
    with open(REGISTRY, "w", encoding="utf-8") as f:
        json.dump(reg, f, ensure_ascii=False, indent=2)


# ---------------- 子命令:new / run / compare / promote ----------------

def cmd_new(args):
    reg = load_registry()
    if args.name in reg:
        print(f"[ERROR] 实验 {args.name} 已存在")
        return 1
    base_path = _cfg_path(args.base)
    if not base_path:
        print(f"[ERROR] 基线场景不存在: config/scene_{args.base}.*")
        return 1
    base = load_cfg(base_path)
    diff = {}
    for kv in args.set:
        if "=" not in kv:
            print(f"[ERROR] --set 需要 key=value,收到: {kv}")
            return 1
        k, v = kv.split("=", 1)
        if k not in base.get("params", {}):
            print(f"[ERROR] 基线没有参数 {k}(可选: {sorted(base.get('params', {}))})")
            return 1
        try:
            diff[k] = type(base["params"][k])(v) if not isinstance(
                base["params"][k], str) else v
        except ValueError:
            diff[k] = v
    exp = {
        "name": args.name, "base": os.path.basename(base_path),
        "desc": args.desc or "", "set": diff, "expect": args.expect or "",
        "created": datetime.now().astimezone().isoformat(timespec="seconds"),
        "runs": [], "promoted_to": None,
    }
    reg[args.name] = exp
    save_registry(reg)
    exp_cfg = {"params": {**base.get("params", {}), **diff},
               "base": exp["base"], "exp": args.name}
    out = os.path.join(CONFIG_DIR, f"scene_{args.name}.yaml")
    dump_cfg(exp_cfg, out)
    print(f"[OK] 实验 {args.name} 已创建")
    print(f"     配置: {os.path.relpath(out, ROOT)}"
          + (" (+ .json 孪生)" if os.path.exists(out[:-5] + ".json") else ""))
    print(f"     差异: {diff} | 目的: {exp['desc']}")
    return 0


# ---------- 确定性仿真:参数 -> 一把跑出来的数据 ----------

def simulate_run(exp_name, run_idx, diff, seed=42, force_camera=False):
    """同参同结果。参数影响指标与成败,产出与真车同构的 run 目录。"""
    rng = random.Random(f"{exp_name}#{run_idx}#{seed}")
    nav_speed = diff.get("nav_speed", 0.8)
    perception_hz = diff.get("perception_hz", 10.0)
    conf_th = diff.get("conf_threshold", 0.7)
    model = diff.get("model", "yolov8n_fp32")
    model_map = {"yolov8n_fp32": 0.85, "yolov8s_fp16": 0.872,
                 "yolov8n_int8": 0.832}.get(model, 0.85)

    nav_time = round(42.0 / max(nav_speed, 0.1) * rng.uniform(0.9, 1.1), 1)
    conf = round(max(0.0, model_map + rng.uniform(-0.05, 0.05)
                     - max(0.0, conf_th - 0.7) * 0.3), 3)
    cpu = round(38 + 26 * (max(nav_speed, 0.5) - 0.5) + rng.uniform(-3, 3), 1)
    freq = round(perception_hz * rng.uniform(0.92, 1.0), 1)
    camera_break = force_camera or rng.random() < 0.18
    fail_prob = max(0.0, (nav_speed - 1.0) * 0.6)
    success = (not camera_break) and rng.random() > fail_prob
    t0 = int(datetime.now().timestamp() * 1000) - int(nav_time * 1000)

    tasks = {}
    segs = [("navigate_to_scan", 0.18), ("detect_qr", 0.15),
            ("navigate_to_grasp", 0.25), ("grasp", 0.15),
            ("navigate_to_place", 0.2), ("speak_done", 0.07)]
    elapsed = 0.0
    for name, ratio in segs:
        dur = round(nav_time * ratio, 2)
        elapsed += dur
        status = "SUCCESS" if (success or name in
                               ("navigate_to_scan", "detect_qr")) else "FAILED"
        tasks[name] = {"type": "Mock", "status": status, "attempts": 1,
                       "retries": 0, "duration": dur, "fallback": None}
    if not success:
        tasks["navigate_to_place"]["fallback"] = "navigate_to_grasp"

    report = {"mission_id": f"exp_{exp_name}_{run_idx}",
              "final": "SUCCESS" if success else "FAILED",
              "tasks": tasks, "fallback_used": {} if success
              else {"navigate_to_place": "navigate_to_grasp"},
              "retry_budget_left": 3, "final_context": {}}
    return {"report": report, "nav_time": nav_time, "conf": conf,
            "cpu": cpu, "freq": freq, "camera_break": camera_break,
            "success": success, "t0": t0, "elapsed": elapsed}


def _write_run_dir(run_dir, sim, diff):
    os.makedirs(run_dir, exist_ok=True)
    with open(os.path.join(run_dir, "task_report.json"), "w",
              encoding="utf-8") as f:
        json.dump(sim["report"], f, ensure_ascii=False, indent=2)
    rows = []
    t = sim["t0"]
    for i in range(40):
        t += 500
        rows.append({"ts": t, "event": "metric", "name": "perception_freq",
                     "value": sim["freq"] if not sim["camera_break"] or i < 8 else 0.0})
        rows.append({"ts": t, "event": "metric", "name": "confidence",
                     "value": max(0.0, sim["conf"] - (0.2 if i > 8 and
                                sim["camera_break"] else 0))})
    rows.append({"ts": t, "event": "metric", "name": "cpu", "value": sim["cpu"]})
    rows.append({"ts": t, "event": "metric", "name": "temp", "value": 46.0})
    rows.append({"ts": t, "event": "metric", "name": "battery", "value": 10.9})
    rows.append({"ts": t, "event": "metric", "name": "nav_success",
                 "value": 1.0 if sim["success"] else 0.0})
    rows.append({"ts": t, "event": "metric", "name": "path_optimality",
                 "value": round(0.9 if sim["success"] else 0.72, 2)})
    if sim["camera_break"]:
        rows.append({"ts": sim["t0"] + 5200, "event": "fault",
                     "type": "camera_degraded", "detail": "freq<10Hz",
                     "action": "nav fallback to lidar_only",
                     "cost_s": round(sim["elapsed"] * 0.2, 1)})
    if not sim["success"]:
        rows.append({"ts": sim["t0"] + int(sim["elapsed"] * 1000) - 900,
                     "event": "estop", "topic": "/cmd_vel", "linear": 0.0,
                     "angular": 0.0, "reason": "task failed", "source": "monitor",
                     "seq": 1})
    with open(os.path.join(run_dir, "blackbox.jsonl"), "w",
              encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    ts = datetime.now().astimezone().isoformat(timespec="milliseconds")
    with open(os.path.join(run_dir, "switch_log.csv"), "w",
              encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ts", "type", "old_value", "new_value", "operator",
                    "exp_name", "note"])
        for k, v in diff.items():
            w.writerow([ts, "param", "?", v, "exp_runner", "", f"ros2 param set ... {k}={v} (热加载,不重启)"])


def cmd_run(args):
    reg = load_registry()
    exp = reg.get(args.name)
    if not exp:
        print(f"[ERROR] 实验 {args.name} 不存在,先 exp.py new")
        return 1
    diff = exp["set"]
    print(f"[RUN] {args.name}: {args.runs} 把,差异 {diff}")
    results = []
    for i in range(1, args.runs + 1):
        sim = simulate_run(args.name, i, diff)
        run_dir = os.path.join(RUNS_DIR, f"{args.name}_{i:02d}")
        _write_run_dir(run_dir, sim, diff)
        s = qr.review_run(run_dir)               # 每把跑 quick_review
        results.append({"run": s["run"], "final": s["final"],
                        "nav_time": sim["nav_time"],
                        "success_rate": 1.0 if s["final"] == "SUCCESS" else 0.0,
                        "confidence": s["metrics"].get("confidence"),
                        "cpu_peak": s["metrics"].get("cpu_peak"),
                        "perception_freq": s["metrics"].get("perception_freq"),
                        "path_optimality": s["metrics"].get("path_optimality"),
                        "root_cause": (s["root_causes"][0]
                                       if s["root_causes"] else None)})
        mark = "✅" if s["final"] == "SUCCESS" else "❌"
        print(f"  第{i}把 {mark} {s['final']} nav_time={sim['nav_time']}s "
              + (f"根因: {s['root_causes'][0]}" if s["root_causes"] else ""))
    ok = sum(1 for r in results if r["success_rate"] == 1.0)
    agg = {"runs": len(results), "success": ok,
           "success_rate": round(ok / len(results), 3)}
    for k in ("nav_time", "confidence", "cpu_peak", "perception_freq",
              "path_optimality"):
        vals = [r[k] for r in results if r[k] is not None]
        if vals:
            mean = sum(vals) / len(vals)
            var = sum((v - mean) ** 2 for v in vals) / len(vals)
            agg[k] = {"mean": round(mean, 3), "std": round(var ** 0.5, 3)}
    exp["runs"].append({"when": datetime.now().astimezone().isoformat(
        timespec="seconds"), "summary": agg, "results": results})
    save_registry(reg)
    print(f"[OK] 汇总: {agg['runs']} 把成功 {agg['success']} "
          f"(成功率 {agg['success_rate']})")
    for k, v in agg.items():
        if isinstance(v, dict):
            print(f"     {k}: {v['mean']} ± {v['std']}")
    return 0


def cmd_compare(args):
    reg = load_registry()
    names = [n.strip() for n in args.names.split(",") if n.strip()]
    metrics = [m.strip() for m in args.metric.split(",") if m.strip()]
    rows = []
    for n in names:
        exp = reg.get(n)
        if not exp or not exp["runs"]:
            print(f"[WARN] {n} 无运行数据,跳过")
            continue
        agg = exp["runs"][-1]["summary"]
        rows.append((n, agg))
    if not rows:
        print("[ERROR] 没有可对比的数据")
        return 1
    md = ["| 实验 | 成功率 | " + " | ".join(metrics) + " |",
          "|--|--|" + "--|".join(["--"] * len(metrics)) + "|"]
    for n, agg in rows:
        cells = []
        for m in metrics:
            v = agg.get(m)
            cells.append(f"{v['mean']}±{v['std']}" if isinstance(v, dict)
                         else str(v))
        md.append(f"| {n} | {agg['success_rate']} | " + " | ".join(cells) + " |")
    table = "\n".join(md)
    print(table)
    os.makedirs(EXP_DIR, exist_ok=True)
    html_path = os.path.join(EXP_DIR, "compare_latest.html")
    _write_compare_html(rows, metrics, html_path)
    with open(os.path.join(EXP_DIR, "compare_latest.md"), "w",
              encoding="utf-8") as f:
        f.write(table + "\n")
    print(f"[OK] Markdown 已存 {os.path.relpath(os.path.join(EXP_DIR, 'compare_latest.md'), ROOT)}")
    print(f"[OK] 交互图已存 {os.path.relpath(html_path, ROOT)}(无 CDN,离线可开)")
    return 0


def _write_compare_html(rows, metrics, path):
    """手写 Canvas 对比图(无 CDN,现场断网可看)。"""
    import html as html_mod
    series = []
    for m in metrics:
        vals = [a.get(m) for _, a in rows]
        vals = [v["mean"] if isinstance(v, dict) else (v or 0) for v in vals]
        series.append((m, vals))
    data_json = json.dumps({"labels": [html_mod.escape(n) for n, _ in rows],
                            "series": series})
    page = """<!DOCTYPE html><html><head><meta charset="utf-8">
<title>实验对比</title><style>body{background:#0f1115;color:#e8eaed;
font-family:sans-serif;margin:24px}canvas{background:#171a21;
border-radius:8px}</style></head><body><h2>实验对比(M5-4)</h2>
<canvas id="cv" width="760" height="420"></canvas><div id="legend"></div>
<script>const D=__DATA__;const cv=document.getElementById('cv'),x=cv.getContext('2d');
const colors=['#4fd1c5','#f6ad55','#fc8181','#9f7aea'];const P=50;
let mx=0;D.series.forEach(s=>s[1].forEach(v=>{if(Math.abs(v)>mx)mx=Math.abs(v)}));
mx=mx||1;D.series.forEach((s,si)=>{x.strokeStyle=colors[si%4];x.lineWidth=2;x.beginPath();
s[1].forEach((v,i)=>{const px=P+i*((cv.width-2*P)/Math.max(1,D.labels.length-1));
const py=cv.height-P-(v/mx)*(cv.height-2*P);i?x.lineTo(px,py):x.moveTo(px,py)});
x.stroke();s[1].forEach((v,i)=>{const px=P+i*((cv.width-2*P)/Math.max(1,D.labels.length-1));
const py=cv.height-P-(v/mx)*(cv.height-2*P);x.fillStyle=colors[si%4];
x.beginPath();x.arc(px,py,4,0,7);x.fill()})});
x.fillStyle='#e8eaed';D.labels.forEach((l,i)=>{x.fillText(l,P+i*((cv.width-2*P)/Math.max(1,D.labels.length-1))-20,cv.height-15)});
const lg=document.getElementById('legend');lg.innerHTML=D.series.map((s,i)=>
`<span style="color:${colors[i%4]}">■</span> ${s[0]}(max=${mx.toFixed(2)})`).join(' ');
</script></body></html>"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(page.replace("__DATA__", data_json))


def cmd_promote(args):
    reg = load_registry()
    exp = reg.get(args.name)
    if not exp:
        print(f"[ERROR] 实验 {args.name} 不存在")
        return 1
    to_path = _cfg_path(args.to)
    if not to_path:
        print(f"[ERROR] 目标场景不存在: scene_{args.to}.*")
        return 1
    base = load_cfg(to_path)
    base.setdefault("params", {}).update(exp["set"])
    base["last_promoted_from"] = args.name
    base["promoted_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    dump_cfg(base, to_path)
    exp["promoted_to"] = args.to
    save_registry(reg)
    best = (exp["runs"][-1]["summary"] if exp["runs"] else {})
    msg = (f"chore(config): promote {args.name} -> scene_{args.to}.yaml "
           f"(success_rate={best.get('success_rate')}, "
           f"nav_time={ (best.get('nav_time') or {}).get('mean') })")
    cmd = ["git", "commit", "-am", msg]
    try:
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                           timeout=15)
        if r.returncode == 0:
            print(f"[OK] 已提交 git: {msg}")
        else:
            print(f"[OK] 配置已更新(git 未提交: {r.stderr.strip()[:80]})")
            print(f"     手动执行: cd {ROOT} && git add -A && {msg}")
    except (OSError, subprocess.TimeoutExpired):
        print(f"[OK] 配置已更新。手动 git 提交: {msg}")
    print(f"[OK] {args.name} -> scene_{args.to}.yaml (参数: {exp['set']})")
    return 0


def main():
    ap = argparse.ArgumentParser(description="M5-4 实验管理器")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("new")
    p.add_argument("--name", required=True)
    p.add_argument("--base", required=True, help="基线场景名(如 scene_race.yaml 或 race)")
    p.add_argument("--set", action="append", default=[], help="key=value,可多次")
    p.add_argument("--desc", default="")
    p.add_argument("--expect", default="", help="预期指标(可选)")
    p.set_defaults(fn=cmd_new)
    p = sub.add_parser("run")
    p.add_argument("--name", required=True)
    p.add_argument("--runs", type=int, default=3)
    p.set_defaults(fn=cmd_run)
    p = sub.add_parser("compare")
    p.add_argument("--names", required=True, help="逗号分隔实验名")
    p.add_argument("--metric", default="nav_time,success_rate,cpu_peak")
    p.set_defaults(fn=cmd_compare)
    p = sub.add_parser("promote")
    p.add_argument("--name", required=True)
    p.add_argument("--to", required=True, help="目标场景名(如 race)")
    p.set_defaults(fn=cmd_promote)
    args = ap.parse_args()
    sys.exit(args.fn(args) or 0)


if __name__ == "__main__":
    main()
