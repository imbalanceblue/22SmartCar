"""M5-2 单元测试:聚合器指标计算 + 故障检测/自愈 + Mock 源。

运行:PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests/ -v
(VM 上必须禁用 ROS 的 pytest 插件自动加载)
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "robot_side"))
sys.path.insert(0, os.path.join(_ROOT, "host_side"))

from dashboard_aggregator import Aggregator
from mock_source import CarSim

# ---------- 指标计算 ----------

def test_freq_normal():
    """10Hz 正常喂 3 秒 -> 频率约 10Hz,丢包约 0。"""
    agg = Aggregator()
    t = 100.0
    for i in range(31):                    # 0~3.0s,10Hz
        agg.on_topic("/perception/targets", stamp=t + i * 0.1, conf=0.8)
        agg.metrics()                      # 顺带驱动窗口滑动
        t += 0.0                           # recv 用 monotonic,stamp 用 sim 时钟
    m = agg.metrics()
    per = m["topics"]["/perception/targets"]
    assert per["freq"] > 5.0               # 窗口内样本充足
    assert per["confidence"] == 0.8


def test_camera_off_shows_loss():
    agg = Aggregator()
    m = agg.metrics()
    per = m["topics"]["/perception/targets"]
    assert per["freq"] == 0.0              # 没喂过消息 -> 频率 0
    assert per["loss"] > 0.9               # 理应收到却没收到


def test_unknown_topic_autodiscovered():
    """未注册话题自动出现(自动发现考点)。"""
    agg = Aggregator()
    agg.on_topic("/some/new/topic", stamp=1.0)
    assert "/some/new/topic" in agg.metrics()["topics"]


# ---------- 故障检测 ----------

def test_camera_fault_triggers_and_heals():
    agg = Aggregator()
    agg.battery, agg.cpu, agg.temp = 12.0, 20.0, 45.0
    snap = agg.snapshot()
    assert snap["active_faults"] == []     # 初始无故障
    # 电池跌破阈值 -> 故障激活 + 事件流记录
    agg.battery = 11.2
    snap = agg.snapshot()
    types = [f["type"] for f in snap["active_faults"]]
    assert "LOW_BATTERY" in types
    assert any(e["event"] == "FAULT" and e["type"] == "LOW_BATTERY"
               for e in snap["event_log"])
    # 回升 -> 自愈
    agg.battery = 12.0
    snap = agg.snapshot()
    assert all(f["type"] != "LOW_BATTERY" for f in snap["active_faults"])
    rec = agg.faults["LOW_BATTERY"]
    assert rec["self_healed"] and rec["duration"] >= 0.0
    assert any(e["event"] == "HEALED" and e["type"] == "LOW_BATTERY"
               for e in snap["event_log"])


def test_thermal_and_cpu_rules():
    agg = Aggregator()
    agg.temp = 90.0
    agg.cpu = 95.0
    types = {f["type"] for f in agg.snapshot()["active_faults"]}
    assert {"THERMAL_WARNING", "CPU_OVERLOAD"} <= types


def test_snapshot_structure():
    agg = Aggregator()
    snap = agg.snapshot(task_state={"mission_id": "m", "nodes": {}},
                        referee={"state": "READY"}, system_ready=True)
    assert set(snap) >= {"ts", "metrics", "active_faults", "event_log",
                         "task_state", "referee", "system_ready"}


# ---------- Mock 源 ----------

def test_carsim_deterministic():
    """固定种子 -> 两次初始化的 CPU 初值一致(可复现)。"""
    a, b = CarSim(seed=7), CarSim(seed=7)
    a.tick(0.1)
    b.tick(0.1)
    assert abs(a.cpu - b.cpu) < 1e-9


def test_carsim_faults_and_referee():
    sim = CarSim(seed=1)
    for _ in range(20):
        sim.tick(0.1)                      # 2s 后 system_ready
    assert sim.system_ready
    assert len(sim.messages()) == 3        # 感知+导航+裁判
    sim.toggle_fault("camera_off")
    assert len(sim.messages()) == 2        # 相机断了,没有感知消息
    sim.start()
    assert sim.referee["state"] == "RUNNING"
    sim.emergency_stop()
    assert sim.referee["state"] == "STOPPED"
    assert sim.estopped


def test_carsim_unknown_fault_raises():
    sim = CarSim(seed=1)
    try:
        sim.toggle_fault("no_such_fault")
        assert False, "应抛 ValueError"
    except ValueError:
        pass
