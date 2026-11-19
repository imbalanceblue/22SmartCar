#!/usr/bin/env python3
"""生成一套确定性"练习日数据"(考核验收输入):
  data 位置: M5-4/runs/run_001..005 (5 把,每把 blackbox+task_report+switch_log)
  3 套实验参数: experiments/registry.json + config/scene_nav_speed_{0.8,0.9,1.0}
  2 个模型版本: models/yolov8n_fp32.onnx + models/yolov8n_int8.onnx
  每把自动跑 quick_review 留 summary.json(供与上一把对比)

重复执行会重置这批数据(确定性,同参同结果)。真车数据请替换为实际产物。
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import exp as exp_mod                       # noqa: E402
import quick_review as qr                   # noqa: E402


def main():
    runs_dir = os.path.join(ROOT, "runs")
    # 练习日的 5 把:参数逐步调优,第 4 把相机故障,第 5 把最优
    plan = [
        ("run_001", {"nav_speed": 0.8, "perception_hz": 10.0,
                     "conf_threshold": 0.7, "model": "yolov8n_fp32"}),
        ("run_002", {"nav_speed": 0.8, "perception_hz": 10.0,
                     "conf_threshold": 0.7, "model": "yolov8n_fp32"}),
        ("run_003", {"nav_speed": 0.9, "perception_hz": 10.0,
                     "conf_threshold": 0.7, "model": "yolov8n_fp32"}),
        ("run_004", {"nav_speed": 1.0, "perception_hz": 10.0,
                     "conf_threshold": 0.7, "model": "yolov8n_fp32"}),
        ("run_005", {"nav_speed": 0.9, "perception_hz": 12.0,
                     "conf_threshold": 0.7, "model": "yolov8n_int8"}),
    ]
    prev = None
    for name, diff in plan:
        # 固定相机故障出现在 run_004(演示降级链路)
        sim = exp_mod.simulate_run(name, 1, diff, seed="practice",
                                   force_camera=(name == "run_004"))
        run_dir = os.path.join(runs_dir, name)
        exp_mod._write_run_dir(run_dir, sim, diff)
        s = qr.review_run(run_dir, prev_dir=prev)
        prev = run_dir
        print(f"{name}: {s['final']} 根因={s['root_causes'] or '无'}")

    # 3 套实验参数(注册表 + 配置差异文件)
    reg = exp_mod.load_registry()
    for nm, d, desc in [
        ("nav_speed_0.8", {"nav_speed": 0.8}, "保守速度基线"),
        ("nav_speed_0.9", {"nav_speed": 0.9}, "提高直线速度测试"),
        ("nav_speed_1.0", {"nav_speed": 1.0}, "激进速度,观察成功率"),
    ]:
        if nm not in reg:
            base = exp_mod.load_cfg(exp_mod._cfg_path("race"))
            reg[nm] = {"name": nm, "base": "scene_race.yaml", "desc": desc,
                       "set": d, "expect": "nav_time↓ success_rate 不降",
                       "created": "practice-day", "runs": [],
                       "promoted_to": None}
            cfg = {"params": {**base.get("params", {}), **d},
                   "base": "scene_race.yaml", "exp": nm}
            out = os.path.join(ROOT, "config", f"scene_{nm}.yaml")
            exp_mod.dump_cfg(cfg, out)
    exp_mod.save_registry(reg)

    # 2 个模型版本(占位文件,gen_baseline 会做哈希锁定)
    os.makedirs(os.path.join(ROOT, "models"), exist_ok=True)
    for m, note in [("yolov8n_fp32.onnx", "fp32 精度优先"),
                    ("yolov8n_int8.onnx", "int8 速度优先")]:
        p = os.path.join(ROOT, "models", m)
        with open(p, "w", encoding="utf-8") as f:
            f.write(f"PLACEHOLDER ONNX — {note},真实部署时替换\n")
    print("[OK] 练习日数据就绪: runs/run_001..005 + 3 套实验 + 2 个模型")


if __name__ == "__main__":
    main()
