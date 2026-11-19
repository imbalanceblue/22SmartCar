"""Mock 小车仿真源:没有真车/真 ROS2 时,提供确定性的"假车"数据流。

练习/考核场景下替代真实硬件;故障可通过 API 注入(拔相机/过热/低电量)。
"""
import random


class CarSim:
    """每 tick(0.1s)被 server 调一次,产出这一拍的所有消息。"""

    def __init__(self, seed=42):
        self.rng = random.Random(seed)   # 固定种子 -> 可复现
        self.battery = 12.4
        self.cpu = 22.0
        self.temp = 45.0
        self.faults = {"camera_off": False, "npu_overheat": False,
                       "low_battery": False}
        # 裁判系统:READY -> (START 后) RUNNING,倒计时 180s
        self.referee = {"state": "READY", "countdown": 180, "stage": "准备",
                        "fouls": 0}
        self.system_ready = False
        self._ready_timer = 1.5          # 启动 1.5s 后就绪(模拟自检)
        self.estopped = False
        self.t = 0.0

    # ---------- 被调用的接口 ----------
    def tick(self, dt=0.1):
        self.t += dt
        if self._ready_timer > 0:
            self._ready_timer -= dt
            if self._ready_timer <= 0:
                self.system_ready = True

        # 硬件:电量缓降 / CPU 温度随机游走(故障注入优先)
        if not self.faults["low_battery"]:
            self.battery = max(10.8, self.battery - 0.004)
        else:
            self.battery = max(10.2, self.battery - 0.05)
        cpu_target = 88.0 if self.faults["npu_overheat"] else 25.0
        self.cpu += (cpu_target - self.cpu) * 0.15 + self.rng.uniform(-2, 2)
        self.cpu = min(99.0, max(5.0, self.cpu))
        temp_target = 92.0 if self.faults["npu_overheat"] else 48.0
        self.temp += (temp_target - self.temp) * 0.1 + self.rng.uniform(-0.5, 0.5)

        if self.referee["state"] == "RUNNING":
            self.referee["countdown"] = max(0, self.referee["countdown"] - dt)

    def messages(self):
        """返回本拍要喂给聚合器的消息列表 (topic, stamp, conf)。"""
        msgs = []
        stamp = self.t
        if self.system_ready and not self.faults["camera_off"]:
            conf = self.rng.uniform(0.72, 0.95)
            msgs.append(("/perception/targets", stamp, conf))
        if self.system_ready:
            msgs.append(("/navigation/pose", stamp, None))
            msgs.append(("/referee/status", stamp, None))
        return msgs

    # ---------- 控制接口(给 Web 按钮调用) ----------
    def start(self):
        if self.system_ready and self.referee["state"] == "READY":
            self.referee["state"] = "RUNNING"
            self.referee["stage"] = "比赛进行中"

    def pause(self):
        if self.referee["state"] == "RUNNING":
            self.referee["state"] = "PAUSED"
            self.referee["stage"] = "暂停"

    def resume(self):
        if self.referee["state"] == "PAUSED":
            self.referee["state"] = "RUNNING"
            self.referee["stage"] = "比赛进行中"

    def foul(self):
        self.referee["fouls"] += 1

    def toggle_fault(self, name):
        if name in self.faults:
            self.faults[name] = not self.faults[name]
            return self.faults[name]
        raise ValueError(f"未知故障: {name}")

    def emergency_stop(self):
        self.estopped = True
        self.referee["state"] = "STOPPED"
        self.referee["stage"] = "紧急停车"
