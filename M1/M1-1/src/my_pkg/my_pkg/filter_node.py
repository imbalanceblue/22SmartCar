"""节点B:一阶低通滤波 + 单位转换转发。

订阅 ultrasonic(SensorData) -> 滤波 -> 发布 processed_distance(std_msgs/Float32)。
alpha 是 ROS 参数,每条消息都重新读取,因此 ros2 param set 立刻生效。
"""

import rclpy
from rclpy.node import Node
from std_msgs.msg import Float32

from my_interfaces.msg import SensorData
from my_pkg.core import clamp_alpha, is_invalid, low_pass


class FilterNode(Node):
    """一阶低通: y[n] = alpha * x[n] + (1 - alpha) * y[n-1]。"""

    def __init__(self):
        super().__init__("filter_node")
        self.declare_parameter("alpha", 0.3)        # 滤波系数:越小越平滑、越滞后
        self.declare_parameter("log_every", 10)     # 每 N 条打印一次(10Hz->1Hz 一行)

        self.sub = self.create_subscription(
            SensorData, "ultrasonic", self.on_msg, 10)
        self.pub = self.create_publisher(Float32, "processed_distance", 10)

        self.filtered = None                        # 上一拍的滤波结果
        self.valid_count = 0
        self.dropped_count = 0
        self.get_logger().info(
            f"filter_node 启动:alpha={self.param_alpha():.2f}, "
            f"ultrasonic -> processed_distance")

    # ---------- 工具 ----------
    def param_alpha(self):
        """每次调用都读参数,保证运行时修改立即生效(题面核心要求)。"""
        return clamp_alpha(self.get_parameter("alpha").value)

    # ---------- 回调 ----------
    def on_msg(self, msg: SensorData):
        if is_invalid(msg.status):
            # 无效数据不参与滤波,但要留下告警(题面明确要求)
            self.dropped_count += 1
            self.get_logger().warn(
                f"丢弃无效数据 status={msg.status} distance={msg.distance:.2f}"
                f"(累计 {self.dropped_count} 条)")
            return

        alpha = self.param_alpha()
        raw = float(msg.distance)
        # prev=None 时首个样本直接作为初值(core.low_pass 内处理)
        self.filtered = low_pass(self.filtered, raw, alpha)

        self.pub.publish(Float32(data=float(self.filtered)))
        self.valid_count += 1

        every = max(int(self.get_parameter("log_every").value), 1)
        if self.valid_count % every == 1:
            self.get_logger().info(
                f"[FILTER] alpha={alpha:.2f} raw={raw:.3f} "
                f"smooth={self.filtered:.3f} unit={msg.unit}")


def main():
    rclpy.init()
    node = FilterNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
