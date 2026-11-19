"""节点C:阈值判断 + 报警服务调用(含超时处理)。

订阅 processed_distance(Float32);距离低于阈值时调用 TriggerAlarm 服务。
本节点同时充当报警服务端(打印 [ALARM] 并返回成功),这样三个节点即可闭环。

为什么用 MultiThreadedExecutor:
  订阅回调里发起服务调用,如果执行器只有一个线程、且服务端与客户端在同一个节点,
  服务请求永远轮不到处理 -> 假超时。多线程 + ReentrantCallbackGroup 才能并发处理。
"""

import time

import rclpy
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from std_msgs.msg import Float32

from my_interfaces.srv import TriggerAlarm


class ExecutorNode(Node):
    """执行者:距离过近 -> 报警。"""

    def __init__(self):
        super().__init__("executor_node")
        self.declare_parameter("alarm_threshold", 0.3)   # 报警阈值(m)
        self.declare_parameter("cooldown", 2.0)          # 两次报警最小间隔(s)
        self.declare_parameter("service_timeout", 1.0)   # 服务超时(s)
        self.declare_parameter("server_delay", 0.0)      # 服务端模拟延迟(s),演示超时用

        group = ReentrantCallbackGroup()                 # 允许回调并发,避免自调自死锁
        self.pub_client = self.create_client(
            TriggerAlarm, "trigger_alarm", callback_group=group)
        self.srv = self.create_service(
            TriggerAlarm, "trigger_alarm", self.on_alarm_request, callback_group=group)
        self.sub = self.create_subscription(
            Float32, "processed_distance", self.on_distance, 10, callback_group=group)
        self.timer = self.create_timer(0.1, self.check_timeout, callback_group=group)

        self.pending = None          # 在等待响应的 future
        self.token = 0               # 调用代次,用于忽略"已判超时"的迟到响应
        self.deadline = 0.0
        self.last_alarm = -1e9
        self.get_logger().info(
            f"executor_node 启动:threshold={self.threshold():.2f}m, "
            f"服务 trigger_alarm 已就绪")

    # ---------- 工具 ----------
    def now_s(self) -> float:
        return self.get_clock().now().nanoseconds / 1e9

    def threshold(self) -> float:
        return float(self.get_parameter("alarm_threshold").value)

    # ---------- 订阅回调:距离判断 ----------
    def on_distance(self, msg: Float32):
        distance = float(msg.data)
        threshold = self.threshold()
        if distance >= threshold:
            return
        now = self.now_s()
        if now - self.last_alarm < float(self.get_parameter("cooldown").value):
            return                       # 冷却期内不重复报警,避免刷屏
        if self.pending is not None:
            return                       # 上一次调用还没结束
        self.last_alarm = now
        self.trigger_alarm(distance, threshold)

    # ---------- 客户端:发起调用 + 非阻塞等待 ----------
    def trigger_alarm(self, distance: float, threshold: float):
        if not self.pub_client.service_is_ready():
            self.pub_client.wait_for_service(timeout_sec=0.5)
            if not self.pub_client.service_is_ready():
                self.get_logger().warn("[WARN] 报警服务不可用,本次跳过")
                return

        request = TriggerAlarm.Request()
        request.reason = f"distance={distance:.3f}m < threshold={threshold:.2f}m"
        self.token += 1
        token = self.token               # 固定本次调用的代次(不能等到回调时再读)
        future = self.pub_client.call_async(request)
        future.add_done_callback(lambda f, t=token: self.on_alarm_done(f, t))
        self.pending = future
        self.deadline = self.now_s() + float(
            self.get_parameter("service_timeout").value)
        self.get_logger().info(f"[TRIGGER] 调用报警服务:{request.reason}")

    def on_alarm_done(self, future, token: int):
        if token != self.token or self.pending is None:
            return                       # 已被判定超时,丢弃迟到响应
        self.pending = None
        try:
            response = future.result()
        except Exception as exc:         # 服务端异常/连接断开
            self.get_logger().error(f"报警服务调用失败:{exc}")
            return
        if response.success:
            self.get_logger().info(f"[OK] 报警成功:{response.message}")
        else:
            self.get_logger().warn(f"[FAIL] 报警失败:{response.message}")

    # ---------- 看门狗:超时判定(优于在回调里阻塞等待) ----------
    def check_timeout(self):
        if self.pending is None:
            return
        if self.now_s() >= self.deadline:
            self.pending = None
            self.token += 1              # 作废当前 future 的回调
            self.get_logger().warn("[WARN] 报警服务超时")

    # ---------- 服务端:被调用时执行 ----------
    def on_alarm_request(self, request, response):
        delay = float(self.get_parameter("server_delay").value)
        if delay > 0:
            time.sleep(delay)            # 模拟慢设备;多线程执行器下不会卡死其他回调
        self.get_logger().info(f"[ALARM] 距离过近！({request.reason})")
        response.success = True
        response.message = f"报警已触发@{self.now_s():.3f}"
        return response


def main():
    rclpy.init()
    node = ExecutorNode()
    executor = MultiThreadedExecutor(num_threads=4)
    executor.add_node(node)
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        executor.shutdown()
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
