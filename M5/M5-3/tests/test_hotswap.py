"""热切换单测:日志落盘 / pid 不变 / 参数越界 / 模型影响 FPS。"""
import csv
import os
import shutil
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "host_side"))

from hot_switch import MODELS, HotSwapManager       # noqa: E402


def _tmpdir():
    return tempfile.mkdtemp(prefix="m53_swap_")


def _rows(path):
    with open(path, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def test_scene_switch_logged_and_pid_stable():
    d = _tmpdir()
    try:
        path = os.path.join(d, "switch_log.csv")
        hs = HotSwapManager(log_path=path)
        pid = hs.pid
        r = hs.apply("scene", None, "scene_lidar_only.yaml",
                     operator="tester", exp_name="lidar_only")
        assert r["new"] == "scene_lidar_only.yaml"
        assert r["pid"] == pid and hs.reloads == 1
        rows = _rows(path)
        assert len(rows) == 1
        assert rows[0]["type"] == "scene"
        assert rows[0]["operator"] == "tester"
        assert rows[0]["exp_name"] == "lidar_only"
        assert "ros2 param load" in rows[0]["note"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_model_switch_changes_fps():
    d = _tmpdir()
    try:
        hs = HotSwapManager(log_path=os.path.join(d, "s.csv"))
        before = hs.state()["fps"]
        hs.apply("model", None, "yolov8n_int8")
        after = hs.state()["fps"]
        assert before == MODELS["yolov8n_fp32"]["fps"]
        assert after == MODELS["yolov8n_int8"]["fps"] > before
        assert hs.state()["pid"] == hs.pid     # 热加载,不是重启
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_param_range_validation():
    d = _tmpdir()
    try:
        hs = HotSwapManager(log_path=os.path.join(d, "s.csv"))
        hs.apply("param", "nav_speed", 1.2)
        assert hs.state()["params"]["nav_speed"]["value"] == 1.2
        try:
            hs.apply("param", "nav_speed", 9.9)
            raise AssertionError("越界应当报错")
        except ValueError as e:
            assert "超出范围" in str(e)
        try:
            hs.apply("param", "no_such", 1)
            raise AssertionError("未知参数应当报错")
        except ValueError as e:
            assert "未知参数" in str(e)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_unknown_kind_and_value():
    d = _tmpdir()
    try:
        hs = HotSwapManager(log_path=os.path.join(d, "s.csv"))
        for args in (("device", None, "x"), ("model", None, "yolov9")):
            try:
                hs.apply(*args)
                raise AssertionError("应当报错")
            except ValueError:
                pass
    finally:
        shutil.rmtree(d, ignore_errors=True)
