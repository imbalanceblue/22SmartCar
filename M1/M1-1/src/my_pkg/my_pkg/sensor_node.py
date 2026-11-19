"""节点A:虚拟超声波传感器。

以 10Hz 发布 SensorData(距离/单位/时间戳/状态)。
距离按正弦波扫过 0.3m 阈值,便于演示"距离过近 -> 报警"链路。
"""

import random

import rclpy
from rclpy.node import Node

from my_interfaces.msg import SensorData
from my_pkg.core import sim_distance


class SensorNode(Node):
    """模拟超声波探头:周期变化 + 噪声 + 可选的异常帧注入。"""

    def __init__(self):
        super().__init__("sensor_node")
        # ---- 参数:全部可通过 --ros-args -p 或 ros2 param set 修改 ----
        self.declare_parameter("publish_rate", 10.0)    # 发布频率 Hz(题面要求 10Hz)
        self.declare_parameter("wave_period", 6.0)      # 距离变化一个周期(s)
        self.declare_parameter("center", 0.55)          # 距离中心值(m)
        self.declare_parameter("amplitude", 0.35)       # 波动幅度(m) -> 0.20~0.90m
        self.declare_parameter("noise_std", 0.05)       # 高斯噪声标准差(m),让滤波效果可见
        self.declare_parameter("fault_every", 0)        # 每 N 帧注入一次异常,0=关闭
        self.declare_parameter("seed", 0)               # 噪声种子,固定则每次运行一致

        self.pub = self.create_publisher(SensorData, "ultrasonic", 10)
        rate = float(self.get_parameter("publish_rate").value)
        self.rng = random.Random(int(self.get_parameter("seed").value))
        self.seq = 0
        self.timer = self.create_timer(1.0 / max(rate, 0.1), self.on_timer)
        self.get_logger().info(
            f"sensor_node 启动:{rate:.1f}Hz 发布 ultrasonic(SensorData)")

    def on_timer(self):
        self.seq += 1
        get = lambda n: self.get_parameter(n).value  # noqa: E731
        noise = self.rng.gauss(0.0, float(get("noise_std")))
        distance = sim_distance(
            self.seq,
            rate=float(get("publish_rate")),
            period=float(get("wave_period")),
            center=float(get("center")),
            amplitude=float(get("amplitude")),
            noise=noise,
        )

        msg = SensorData()
        msg.stamp = self.get_clock().now().to_msg()
        msg.unit = "m"
        msg.status = 0
        msg.distance = float(max(distance, 0.0))

        fault_every = int(get("fault_every"))
        if fault_every > 0 and self.seq % fault_every == 0:
            # 交替注入:1=超出量程, 2=传感器错误(下游必须丢弃)
            msg.status = 1 if (self.seq // fault_every) % 2 else 2
            msg.distance = 9.99 if msg.status == 1 else -1.0
            self.get_logger().warn(
                f"注入异常帧 seq={self.seq} status={msg.status} distance={msg.distance}")

        self.pub.publish(msg)


def main():
    rclpy.init()
    node = SensorNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
