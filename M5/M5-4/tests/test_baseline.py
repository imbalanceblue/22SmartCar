"""gen_baseline.py 单元测试:包内容完整 + 阈值拦截 + verify 脚本 PASS。"""
import json
import os
import sys
import tarfile
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "scripts"))
import exp as exp_mod                    # noqa: E402
import gen_baseline as gb                # noqa: E402


def _setup(tmp, params):
    exp_mod.EXP_DIR = os.path.join(tmp, "experiments")
    exp_mod.REGISTRY = os.path.join(exp_mod.EXP_DIR, "registry.json")
    exp_mod.CONFIG_DIR = os.path.join(tmp, "config")
    os.makedirs(exp_mod.CONFIG_DIR, exist_ok=True)
    os.makedirs(exp_mod.EXP_DIR, exist_ok=True)
    with open(os.path.join(exp_mod.CONFIG_DIR, "scene_race.json"),
              "w", encoding="utf-8") as f:
        json.dump({"params": {"nav_speed": 0.9, "perception_hz": 10.0,
                              "conf_threshold": 0.7}}, f)
    with open(exp_mod.REGISTRY, "w", encoding="utf-8") as f:
        json.dump({"sp09": {"base": "scene_race.yaml", "set":
                            {"nav_speed": 0.9}, "runs": []}}, f)
    old = (gb.MODELS_DIR, gb.DIST_DIR)
    gb.MODELS_DIR = os.path.join(tmp, "models")
    gb.DIST_DIR = os.path.join(tmp, "dist")
    return old


def test_baseline_package_and_verify_pass():
    tmp = tempfile.mkdtemp()
    old = _setup(tmp, {"nav_speed": 0.9})
    old_reg = (exp_mod.REGISTRY,)
    try:
        sys.argv = ["gen_baseline.py", "--exp", "sp09"]
        assert gb.main() == 0
        pkgs = [f for f in os.listdir(gb.DIST_DIR) if f.endswith(".tar.gz")]
        assert len(pkgs) == 1
        with tarfile.open(os.path.join(gb.DIST_DIR, pkgs[0])) as tar:
            names = tar.getnames()
            assert "baseline.yaml" in names and "verify_baseline.sh" in names
            assert any("models/" in n for n in names)
            tar.extractall(os.path.join(tmp, "unpacked"))
        unpacked = os.path.join(tmp, "unpacked")
        # 锁定的参数确实来自实验合并值
        text = open(os.path.join(unpacked, "baseline.yaml"),
                    encoding="utf-8").read()
        assert "nav_speed" in text and "sha256" in text
        # 异机验证:直接执行 verify 脚本(subprocess 走 bash)
        import subprocess
        r = subprocess.run(["bash", "./verify_baseline.sh"], cwd=unpacked,
                           capture_output=True, text=True, timeout=30)
        assert "PASS" in r.stdout and r.returncode == 0
    finally:
        gb.MODELS_DIR, gb.DIST_DIR = old
        (exp_mod.REGISTRY,) = old_reg


def test_out_of_threshold_rejected():
    tmp = tempfile.mkdtemp()
    old = _setup(tmp, {"nav_speed": 9.9})       # 明显超阈值的实验
    try:
        with open(exp_mod.REGISTRY, "w", encoding="utf-8") as f:
            json.dump({"bad": {"base": "scene_race.yaml",
                               "set": {"nav_speed": 9.9}, "runs": []}}, f)
        sys.argv = ["gen_baseline.py", "--exp", "bad"]
        try:
            gb.main()
            assert False, "超阈值应拒绝生成"
        except SystemExit as e:
            assert "超出阈值" in str(e)
    finally:
        gb.MODELS_DIR, gb.DIST_DIR = old
