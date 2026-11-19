"""M1-3 节点 2:同步激光与图像,把激光点投影到图像上。

链路
----
/scan ─┐
       ├─ ApproximateTimeSynchronizer ─→ 取 stamp ─→ tf2 查 camera<-laser
/camera/image_raw ─┘                              ↓
                                          激光极坐标 -> 平面点 -> camera 系
                                                  ↓
                                   针孔投影 -> 像素 -> 按距离着色画点
                                                  ↓
                                   保存 PNG + 发布 /projected_image

为什么要 ApproximateTimeSynchronizer 而不是 TimeSynchronizer:
  两路数据的采样时刻由不同硬件决定,即使都是 10Hz 也不会严格对齐到同一纳秒。
  严格同步(TimeSynchronizer)会一直等一个永远不存在的完美配对;
  近似同步允许 50ms 容差,才是实际工程里的做法。
"""

import os

import numpy as np
import rclpy
from message_filters import ApproximateTimeSynchronizer, Subscriber
from rclpy.duration import Duration
from rclpy.node import Node
from rclpy.time import Time
from sensor_msgs.msg import Image, LaserScan
from tf2_ros import Buffer, TransformException, TransformListener

from fusion_pkg.geometry import (color_by_range, draw_point, project,
                                 quat_to_matrix, rgb_to_bytes, scan_to_xy,
                                 write_png)


class ProjectorNode(Node):
    """激光点云 -> 相机图像 的投影器。"""

    def __init__(self):
        super().__init__("projector")
        self.declare_parameter("output_dir", os.path.expanduser("~/fusion_out"))
        self.declare_parameter("save_interval", 1.5)
        self.declare_parameter("max_saves", 3)
        self.declare_parameter("fx", 400.0)
        self.declare_parameter("fy", 400.0)
        self.declare_parameter("cx", 320.0)
        self.declare_parameter("cy", 240.0)
        self.declare_parameter("sync_slop", 0.05)
        self.declare_parameter("range_max_color", 6.0)

        self.buffer = Buffer(cache_time=Duration(seconds=10.0))
        self.listener = TransformListener(self.buffer, self)
        self.img_pub = self.create_publisher(Image, "/projected_image", 10)

        # 队列深度给 30:VirtualBox 里图像消息 900KB、10Hz,处理稍慢就会积压,
        # 深度 10 时旧消息会被 DDS 直接丢掉,表现为"只处理了 25 帧但时间已过 4 秒"
        self.scan_sub = Subscriber(self, LaserScan, "/scan", qos_profile=30)
        self.img_sub = Subscriber(self, Image, "/camera/image_raw",
                                  qos_profile=30)
        self.sync = ApproximateTimeSynchronizer(
            [self.img_sub, self.scan_sub],
            queue_size=30,
            slop=float(self.get_parameter("sync_slop").value),
        )
        self.sync.registerCallback(self.on_pair)

        # YAML 里的 ~/fusion_out 不会被 shell 展开,这里补一次 expanduser
        self.out_dir = os.path.expanduser(
            str(self.get_parameter("output_dir").value))
        os.makedirs(self.out_dir, exist_ok=True)
        self.count = 0
        self.saved = 0
        self.tf_fail = 0
        self.last_save_stamp = None
        self.get_logger().info(
            "projector 启动:同步 /scan + /camera/image_raw,输出 %s"
            % self.out_dir)

    # ------------------------------------------------------------ 内参
    def intrinsics(self):
        p = self.get_parameter
        return {"fx": float(p("fx").value), "fy": float(p("fy").value),
                "cx": float(p("cx").value), "cy": float(p("cy").value)}

    # ------------------------------------------------------------ 主回调
    def on_pair(self, image_msg, scan_msg):
        self.count += 1
        stamp_s = scan_msg.header.stamp.sec + scan_msg.header.stamp.nanosec * 1e-9
        drift = self.get_clock().now().nanoseconds * 1e-9 - stamp_s

        canvas = self.decode_image(image_msg)
        if canvas is None:
            return

        # ---- 1. 激光极坐标 -> laser_link 平面点
        xy, _, dists = scan_to_xy(
            scan_msg.ranges, scan_msg.angle_min, scan_msg.angle_increment,
            scan_msg.range_min, scan_msg.range_max)
        if xy.shape[0] == 0:
            return

        # ---- 2. TF:laser_link -> camera_link(优先用消息时间戳)
        T = self.lookup(scan_msg.header.stamp)
        if T is None:
            return
        R, t = T

        # ---- 3. 平面点补 z=0,转到相机系,做针孔投影
        p_laser = np.hstack([xy, np.zeros((xy.shape[0], 1))])
        p_cam = p_laser @ R.T + t
        uv, valid, _ = project(p_cam, self.intrinsics())
        colors = color_by_range(dists, range_max=float(
            self.get_parameter("range_max_color").value))

        # ---- 4. 画点(越界的不画,由 draw_point 内部裁剪)
        h, w = canvas.shape[:2]
        drawn = 0
        for i in range(uv.shape[0]):
            if not valid[i]:
                continue
            u, v = uv[i]
            if 0 <= u < w and 0 <= v < h:
                radius = 2 if dists[i] < 4.0 else 1   # 近处点画大一点
                draw_point(canvas, u, v, tuple(int(c) for c in colors[i]),
                           radius=radius)
                drawn += 1

        self.publish(canvas, scan_msg.header.stamp)
        self.maybe_save(canvas, stamp_s)

        self.get_logger().info(
            "[SYNC] #%d 点数=%d 可见=%d 漂移=%+.3fs"
            % (self.count, xy.shape[0], drawn, drift),
            throttle_duration_sec=1.0)

    # ------------------------------------------------------------ 辅助
    def lookup(self, stamp):
        """查 camera_link <- laser_link。静态TF任意时刻可查,失败则退回最新。"""
        try:
            tf = self.buffer.lookup_transform("camera_link", "laser_link",
                                              stamp, timeout=Duration(seconds=0.2))
        except TransformException:
            try:
                tf = self.buffer.lookup_transform("camera_link", "laser_link",
                                                  Time())
            except TransformException as exc:
                self.tf_fail += 1
                self.get_logger().warn(
                    "TF 查询失败(累计 %d 次):%s" % (self.tf_fail, exc),
                    throttle_duration_sec=3.0)
                return None
        q = tf.transform.rotation
        tr = tf.transform.translation
        return (quat_to_matrix(q.x, q.y, q.z, q.w),
                np.array([tr.x, tr.y, tr.z]))

    def decode_image(self, msg):
        """sensor_msgs/Image -> (H,W,3) uint8。支持 rgb8 / bgr8。"""
        if msg.encoding not in ("rgb8", "bgr8"):
            self.get_logger().warn("暂不支持的编码:%s" % msg.encoding,
                                   throttle_duration_sec=5.0)
            return None
        buf = np.frombuffer(bytes(msg.data), dtype=np.uint8)
        try:
            arr = buf.reshape(msg.height, msg.step)[:, :msg.width * 3]
            arr = arr.reshape(msg.height, msg.width, 3).copy()
        except ValueError:
            self.get_logger().warn("图像尺寸与数据长度不符", throttle_duration_sec=5.0)
            return None
        if msg.encoding == "bgr8":
            arr = arr[:, :, ::-1]
        return arr

    def publish(self, canvas, stamp):
        msg = Image()
        msg.header.stamp = stamp
        msg.header.frame_id = "camera_link"
        msg.height, msg.width = canvas.shape[0], canvas.shape[1]
        msg.encoding = "rgb8"
        msg.is_bigendian = 0
        msg.step = msg.width * 3
        msg.data = rgb_to_bytes(canvas)
        self.img_pub.publish(msg)

    def maybe_save(self, canvas, stamp_s):
        """按"消息时间戳间隔"存图,而不是按处理帧数。

        为什么不用 count % N:处理速度跟不上时 DDS 会丢帧,count 涨得比
        时间慢得多,按帧数可能永远等不到第 N 帧(bag 播完就再也没消息了)。
        按 stamp 间隔则只要数据在推进就一定会到点。
        """
        want = int(self.get_parameter("max_saves").value)
        interval = float(self.get_parameter("save_interval").value)
        if self.saved >= want:
            return
        if (self.last_save_stamp is not None
                and stamp_s - self.last_save_stamp < interval - 1e-6):
            return
        path = os.path.join(self.out_dir, "proj_%02d_t%.1f.png"
                            % (self.saved, stamp_s))
        write_png(path, canvas)
        self.saved += 1
        self.last_save_stamp = stamp_s
        self.get_logger().info("[SAVE] 第 %d/%d 张 -> %s"
                               % (self.saved, want, path))


def main():
    rclpy.init()
    node = ProjectorNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.get_logger().info(
            "退出:处理 %d 帧,保存 %d 张,TF 失败 %d 次"
            % (node.count, node.saved, node.tf_fail))
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
