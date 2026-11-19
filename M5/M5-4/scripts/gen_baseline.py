#!/usr/bin/env python3
"""M5-4 最优基线包生成器:给学弟留/下赛季用。

  python3 scripts/gen_baseline.py --exp nav_speed_0.9
  python3 scripts/gen_baseline.py --scene scene_race.yaml

产出 dist/baseline_package_<date>.tar.gz,内含:
  baseline.yaml         锁定所有关键参数(来自最优实验/场景)
  models/best_model.onnx 模型文件(本环境为占位文件 + 哈希锁定)
  verify_baseline.sh    异机验证脚本(解压后 ./verify_baseline.sh -> PASS)
  README.md             使用说明

verify 逻辑:校验 baseline.yaml 结构完整、关键参数在阈值内、
模型文件 sha256 与 baseline.yaml 记录一致 —— 三关全过才 PASS。
"""
import argparse
import hashlib
import json
import os
import sys
import tarfile
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import exp as exp_mod                       # noqa: E402  (复用配置读写)

MODELS_DIR = os.path.join(ROOT, "models")
DIST_DIR = os.path.join(ROOT, "dist")

THRESHOLDS = {
    "nav_speed": {"min": 0.2, "max": 1.5},
    "perception_hz": {"min": 5.0, "max": 30.0},
    "conf_threshold": {"min": 0.3, "max": 0.95},
}


def _resolve_source(args, reg):
    """--exp 优先:用该实验的合并参数;否则 --scene 直接锁定。"""
    if args.exp:
        e = reg.get(args.exp)
        if not e:
            raise SystemExit(f"[ERROR] 实验 {args.exp} 不存在")
        base_name = e["base"].replace("scene_", "").rsplit(".", 1)[0]
        base = exp_mod.load_cfg(exp_mod._cfg_path(base_name))
        params = {**base.get("params", {}), **e["set"]}
        src_label = f"exp:{args.exp}"
    elif args.scene:
        p = exp_mod._cfg_path(args.scene.replace("scene_", "")
                              .rsplit(".", 1)[0])
        if not p:
            raise SystemExit(f"[ERROR] 场景不存在: {args.scene}")
        params = exp_mod.load_cfg(p).get("params", {})
        src_label = os.path.basename(p)
    else:
        raise SystemExit("[ERROR] 需要 --exp 或 --scene 之一")
    return params, src_label


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser(description="M5-4 基线包生成器")
    ap.add_argument("--exp", help="最优实验名(合并其差异值)")
    ap.add_argument("--scene", help="或直接指定场景(如 race)")
    ap.add_argument("--model", default="best_model.onnx")
    args = ap.parse_args()
    reg = exp_mod.load_registry()
    params, src_label = _resolve_source(args, reg)

    for k, v in params.items():
        th = THRESHOLDS.get(k)
        if th and not (th["min"] <= float(v) <= th["max"]):
            raise SystemExit(f"[ERROR] {k}={v} 超出阈值 {th},拒绝生成基线")

    os.makedirs(MODELS_DIR, exist_ok=True)
    model_path = os.path.join(MODELS_DIR, args.model)
    if not os.path.exists(model_path):
        with open(model_path, "w", encoding="utf-8") as f:
            f.write("PLACEHOLDER ONNX — 本环境无真实模型文件,"
                    "以哈希锁定文件代替,真实部署时替换为导出的 best.onnx\n")
    model_hash = _sha256(model_path)

    baseline = {
        "locked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": src_label,
        "params": params,
        "model": {"file": f"models/{args.model}", "sha256": model_hash},
        "thresholds": THRESHOLDS,
    }

    work = os.path.join(DIST_DIR, "_pkg")
    os.makedirs(os.path.join(work, "models"), exist_ok=True)
    with open(os.path.join(work, "baseline.yaml"), "w", encoding="utf-8") as f:
        try:
            import yaml
            yaml.safe_dump(baseline, f, allow_unicode=True, sort_keys=False)
        except ImportError:
            with open(os.path.join(work, "baseline.yaml"), "w",
                      encoding="utf-8") as f2:
                json.dump(baseline, f2, ensure_ascii=False, indent=2)
    import shutil
    shutil.copy(model_path, os.path.join(work, "models", args.model))

    verify_sh = f"""#!/usr/bin/env bash
# M5-4 基线验证:解压后在任意同款环境执行 ./verify_baseline.sh,全过则 PASS
set -u
cd "$(dirname "$0")"
python3 - << 'PYEOF'
import hashlib, json, sys
try:
    import yaml
    with open("baseline.yaml", encoding="utf-8") as f:
        b = yaml.safe_load(f)
except ImportError:
    with open("baseline.yaml", encoding="utf-8") as f:
        b = json.load(f)
fails = []
for k, th in b["thresholds"].items():
    if k not in b["params"]:
        fails.append(f"缺少关键参数 {{k}}"); continue
    v = float(b["params"][k])
    if not (th["min"] <= v <= th["max"]):
        fails.append(f"{{k}}={{v}} 超出 [{{th['min']}}, {{th['max']}}]")
h = hashlib.sha256(open(b["model"]["file"], "rb").read()).hexdigest()
if h != b["model"]["sha256"]:
    fails.append("模型文件哈希不一致,可能被篡改/替换")
if fails:
    print("FAIL")
    [print(" -", f) for f in fails]
    sys.exit(1)
print("PASS: 结构完整,参数在阈值内,模型哈希一致")
PYEOF
"""
    with open(os.path.join(work, "verify_baseline.sh"), "w",
              encoding="utf-8", newline="\n") as f:
        f.write(verify_sh)
    readme = (f"# 基线包({src_label})\n\n"
              "1. 解压后进入目录\n2. `./verify_baseline.sh` 预期输出 PASS\n"
              "3. baseline.yaml 为锁定的最优参数,models/ 内为锁定模型\n")
    with open(os.path.join(work, "README.md"), "w", encoding="utf-8") as f:
        f.write(readme)
    os.chmod(os.path.join(work, "verify_baseline.sh"), 0o755)

    date = datetime.now().strftime("%Y%m%d")
    out = os.path.join(DIST_DIR, f"baseline_package_{date}.tar.gz")
    with tarfile.open(out, "w:gz") as tar:
        for name in ("baseline.yaml", "models", "verify_baseline.sh",
                     "README.md"):
            tar.add(os.path.join(work, name), arcname=name)
    shutil.rmtree(work)
    print(f"[OK] 基线包已生成: {os.path.relpath(out, ROOT)}")
    print(f"     来源: {src_label} | 参数: {params}")
    print(f"     验证: 解压后 ./verify_baseline.sh -> PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
