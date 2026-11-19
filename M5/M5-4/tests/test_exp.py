"""exp.py 单元测试:new/run/compare/promote 全流程(目录隔离到 tmp)。"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "scripts"))
import exp as exp_mod             # noqa: E402


class _Tmp:
    """把 exp 的目录常量重定向到临时目录,测试不污染真实 config/registry。"""

    def __enter__(self):
        self.tmp = tempfile.mkdtemp()
        self.old = (exp_mod.CONFIG_DIR, exp_mod.EXP_DIR,
                    exp_mod.RUNS_DIR, exp_mod.REGISTRY)
        exp_mod.CONFIG_DIR = os.path.join(self.tmp, "config")
        exp_mod.EXP_DIR = os.path.join(self.tmp, "experiments")
        exp_mod.RUNS_DIR = os.path.join(self.tmp, "runs")
        exp_mod.REGISTRY = os.path.join(exp_mod.EXP_DIR, "registry.json")
        os.makedirs(exp_mod.CONFIG_DIR, exist_ok=True)
        os.makedirs(exp_mod.EXP_DIR, exist_ok=True)
        # 迷你基线场景(用 JSON 夹具,不依赖 pyyaml)
        with open(os.path.join(exp_mod.CONFIG_DIR, "scene_race.json"),
                  "w", encoding="utf-8") as f:
            json.dump({"params": {"nav_speed": 0.8, "perception_hz": 10.0,
                                  "conf_threshold": 0.7}}, f)
        return self

    def __exit__(self, *a):
        (exp_mod.CONFIG_DIR, exp_mod.EXP_DIR,
         exp_mod.RUNS_DIR, exp_mod.REGISTRY) = self.old
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)


class Args:
    def __init__(self, **kw):
        self.name = kw.get("name")
        self.base = kw.get("base", "scene_race.yaml")
        self.set = kw.get("set", [])
        self.desc = kw.get("desc", "")
        self.expect = kw.get("expect", "")
        self.runs = kw.get("runs", 2)
        self.names = kw.get("names")
        self.metric = kw.get("metric", "nav_time,success_rate")
        self.to = kw.get("to")


def test_new_creates_diff_config():
    with _Tmp():
        rc = exp_mod.cmd_new(Args(name="sp09", set=["nav_speed=0.9"],
                                  desc="提速"))
        assert rc == 0
        cfg = exp_mod.load_cfg(exp_mod._cfg_path("sp09"))
        assert cfg["params"]["nav_speed"] == 0.9          # 只覆盖差异
        assert cfg["params"]["perception_hz"] == 10.0     # 其余继承基线
        reg = exp_mod.load_registry()
        assert reg["sp09"]["base"].startswith("scene_race")  # yaml 或 json 孪生
        rc = exp_mod.cmd_new(Args(name="sp09", set=["nav_speed=0.9"]))
        assert rc == 1                                    # 重名拒绝


def test_deterministic_simulation():
    a = exp_mod.simulate_run("x", 1, {"nav_speed": 0.9}, seed=42)
    b = exp_mod.simulate_run("x", 1, {"nav_speed": 0.9}, seed=42)
    assert a["nav_time"] == b["nav_time"]                 # 同参同结果
    assert a["report"]["final"] == b["report"]["final"]


def test_run_and_compare_and_promote():
    with _Tmp() as t:
        exp_mod.cmd_new(Args(name="sp08", set=["nav_speed=0.8"]))
        exp_mod.cmd_new(Args(name="sp10", set=["nav_speed=1.0"]))
        assert exp_mod.cmd_run(Args(name="sp08", runs=3)) == 0
        assert exp_mod.cmd_run(Args(name="sp10", runs=3)) == 0
        reg = exp_mod.load_registry()
        agg = reg["sp08"]["runs"][-1]["summary"]
        assert agg["runs"] == 3 and agg["success"] == 3
        assert isinstance(agg["nav_time"]["mean"], float)
        # 每把都留了完整 run 目录(含 summary.json)
        run_dir = os.path.join(exp_mod.RUNS_DIR, "sp08_01")
        assert os.path.exists(os.path.join(run_dir, "summary.json"))
        assert os.path.exists(os.path.join(run_dir, "blackbox.jsonl"))
        # compare 产出 markdown + html
        assert exp_mod.cmd_compare(Args(names="sp08,sp10",
                                        metric="nav_time,success_rate")) == 0
        md = open(os.path.join(exp_mod.EXP_DIR, "compare_latest.md"),
                  encoding="utf-8").read()
        assert "sp08" in md and "sp10" in md
        html = open(os.path.join(exp_mod.EXP_DIR, "compare_latest.html"),
                    encoding="utf-8").read()
        assert "canvas" in html.lower()
        # promote 把实验参数写回基线
        assert exp_mod.cmd_promote(Args(name="sp10", to="race")) == 0
        base = exp_mod.load_cfg(exp_mod._cfg_path("race"))
        assert base["params"]["nav_speed"] == 1.0
        assert exp_mod.load_registry()["sp10"]["promoted_to"] == "race"
