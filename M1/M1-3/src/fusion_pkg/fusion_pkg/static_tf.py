"""M1-3 节点 1:发布整棵静态 TF 坐标树。

    map -> odom -> base_link -> laser_link
                          \\--> camera_link

为什么全是静态:
  这是离线回放场景,车在 bag 里的位姿是"已知且固定"的(我们不跑定位)。
  把 map->odom->base_link 也当静态发布,是为了让整棵树完整可查,
  投影节点只需要一次 lookup 就能拿到 laser->camera 的完整变换。
  真实系统里 map->odom 是定位模块算出来的,会动态变。

外参改动只需改 geometry.py 里的 LASER_IN_BASE / CAMERA_IN_BASE / CAMERA_QUAT,
本文件不含任何数值。
"""

import rclpy
from geometry_msgs.msg import TransformStamped
from rclpy.node import Node
from tf2_ros.static_transform_broadcaster import StaticTransformBroadcaster

from fusion_pkg.geometry import tf_chain


class StaticTfNode(Node):
    """一次性把整棵树推上 /tf_static。"""

    def __init__(self):
        super().__init__("static_tf_node")
        self.broadcaster = StaticTransformBroadcaster(self)

        # 变换表定义在 geometry.tf_chain(),此处只做发布。
        # 外参改动只需改 geometry,投影与自检会自动跟着变。
        transforms = [self._make(*item) for item in tf_chain()]
        self.broadcaster.sendTransform(transforms)
        self.get_logger().info(
            "已发布静态 TF: map->odom->base_link->{laser_link, camera_link}")
        for t in transforms:
            self.get_logger().info(
                "  %s -> %s  xyz=(%.2f, %.2f, %.2f)"
                % (t.header.frame_id, t.child_frame_id,
                   t.transform.translation.x, t.transform.translation.y,
                   t.transform.translation.z))

    def _make(self, parent, child, xyz, quat):
        t = TransformStamped()
        t.header.stamp = self.get_clock().now().to_msg()
        t.header.frame_id = parent
        t.child_frame_id = child
        t.transform.translation.x, t.transform.translation.y, \
            t.transform.translation.z = xyz
        t.transform.rotation.x, t.transform.rotation.y, \
            t.transform.rotation.z, t.transform.rotation.w = quat
        return t


def main():
    rclpy.init()
    node = StaticTfNode()
    try:
        rclpy.spin(node)          # 保持存活,/tf_static 才能持续对新订阅者可见
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
