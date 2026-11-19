"""M1-3 工具:生成合成 rosbag(含 /scan 与 /camera/image_raw)。

为什么自己造数据
----------------
官方 TurtleBot4 示例 bag 有几十到上百 MB,在本机下载不可靠;
而且官方 bag 的激光-相机外参不公开,无法验证"投影到底对不对"。
这里用同一套几何同时生成激光和图像:房间尺寸、车位置、相机内参全部已知,
所以投影结果可以被数学验证,而不是"看着差不多"。

场景
----
矩形房间 16m x 12m(x ∈ [-8,8], y ∈ [-6,6], 墙高 2.5m)。
车沿 +x 匀速前进,朝向不变,相机与激光按 geometry 里的外参安装。

用法
----
  ros2 run fusion_pkg baggen
  ros2 run fusion_pkg baggen --out ~/bags/demo --frames 60
"""

import argparse
import math
import os
import shutil

import numpy as np
import rclpy
from rclpy.serialization import serialize_message
from rclpy.time import Time
from rosbag2_py import (ConverterOptions, SequentialWriter, StorageOptions,
                        TopicMetadata)
from sensor_msgs.msg import Image, LaserScan

from fusion_pkg.geometry import (CAMERA_IN_BASE, DEFAULT_INTRINSICS,
                                 LASER_IN_BASE, raycast_2d, render,
                                 rgb_to_bytes)

# 固定一个过去的时间原点,这样 use_sim_time=false 时能看出明显的时间漂移
BASE_STAMP_NS = 1672531200 * 10 ** 9      # 2023-01-01 00:00:00 UTC
PERIOD = 0.1                              # 10Hz
# 从 x=3.0 出发、1.3m/s,走 3 秒后停车:前墙 5.0m -> 1.1m。
# 三条约束同时要满足,所以速度与停车时刻是联立出来的:
#   1) 3.0 秒时前墙 <=1.2m  -> 第 3 张图必须进红段(着色标尺 0.3~6m)
#   2) 全程不能撞墙(x<7.8)
#   3) bag 必须长于 3.0 秒足够多 -> 节点处理滞后时仍能凑到保存阈值
# 若只提速不缩短行驶时间,3.5 秒就冲到 x=7.55,贴墙;若只加长不给停,
# 又会撞墙。所以第 3 秒后原地停住,bag 可任意加长而车距不再变化。
START_X, SPEED, DRIVE_SECONDS = 3.0, 1.3, 3.0
N_BEAMS = 360
RANGE_MIN, RANGE_MAX = 0.1, 10.0


def make_scan(stamp, cx):
    angles = np.linspace(-math.pi, math.pi, N_BEAMS, endpoint=False)
    ranges = raycast_2d(cx + LASER_IN_BASE[0], 0.0, angles)
    msg = LaserScan()
    msg.header.stamp = stamp
    msg.header.frame_id = "laser_link"
    msg.angle_min = float(-math.pi)
    msg.angle_max = float(math.pi - 2 * math.pi / N_BEAMS)
    msg.angle_increment = float(2 * math.pi / N_BEAMS)
    msg.time_increment = 0.0
    msg.scan_time = PERIOD
    msg.range_min = RANGE_MIN
    msg.range_max = RANGE_MAX
    msg.ranges = [float(v) for v in ranges]
    return msg


def make_image(stamp, cx, width, height, intr):
    cam = (cx + CAMERA_IN_BASE[0], CAMERA_IN_BASE[1], CAMERA_IN_BASE[2])
    rgb = render(cam, width, height, intr, range_max=RANGE_MAX)
    msg = Image()
    msg.header.stamp = stamp
    msg.header.frame_id = "camera_link"
    msg.height, msg.width = height, width
    msg.encoding = "rgb8"
    msg.is_bigendian = 0
    msg.step = width * 3
    msg.data = rgb_to_bytes(rgb)
    return msg


def write_bag(out, frames, width, height, log):
    intr = dict(DEFAULT_INTRINSICS)
    intr["cx"], intr["cy"] = width / 2.0, height / 2.0
    intr["fx"] = intr["fy"] = float(width) * 0.625

    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    writer = SequentialWriter()
    writer.open(StorageOptions(uri=out, storage_id="sqlite3"),
                ConverterOptions("", ""))
    for name, typ in (("/scan", "sensor_msgs/msg/LaserScan"),
                      ("/camera/image_raw", "sensor_msgs/msg/Image")):
        writer.create_topic(TopicMetadata(name=name, type=typ,
                                          serialization_format="cdr"))

    for i in range(frames):
        stamp_ns = BASE_STAMP_NS + int(i * PERIOD * 1e9)
        stamp = Time(nanoseconds=stamp_ns).to_msg()
        # 到 DRIVE_SECONDS 后停车:bag 可以继续录,但车距不再变化,
        # 这样"第 3 张够红"和"bag 够长"两个要求互不冲突
        cx = START_X + SPEED * min(i * PERIOD, DRIVE_SECONDS)

        writer.write("/scan", serialize_message(make_scan(stamp, cx)),
                     stamp_ns)
        writer.write("/camera/image_raw",
                     serialize_message(make_image(stamp, cx, width, height,
                                                 intr)), stamp_ns)
        if i % 10 == 0:
            log("  已写 %d/%d 帧" % (i + 1, frames))
    # 函数返回时 writer 析构,sqlite 才会真正落盘


def main():
    parser = argparse.ArgumentParser(description="生成 M1-3 合成 rosbag")
    parser.add_argument("--out", default=os.path.expanduser("~/bags/fusion_demo"))
    parser.add_argument("--frames", type=int, default=50)
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    args = parser.parse_args()

    # 只允许覆盖"确实是 rosbag2 输出"的目录,避免误删别的东西
    if os.path.isdir(args.out):
        if not os.path.exists(os.path.join(args.out, "metadata.yaml")):
            raise SystemExit(
                "目录已存在但不是 rosbag2 输出,拒绝覆盖: %s" % args.out)
        shutil.rmtree(args.out)

    rclpy.init()
    node = rclpy.create_node("baggen")
    log = node.get_logger().info
    log("开始生成 %d 帧 -> %s (%dx%d)"
        % (args.frames, args.out, args.width, args.height))
    write_bag(args.out, args.frames, args.width, args.height, log)
    log("完成。回放命令:")
    log("  ros2 bag play %s --clock" % args.out)
    node.destroy_node()
    rclpy.try_shutdown()


if __name__ == "__main__":
    main()
