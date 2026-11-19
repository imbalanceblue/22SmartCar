"""M1-2 一键启动:三节点 x N 个命名空间,YAML 参数 + 启动顺序控制。

用法示例:
  ros2 launch my_pkg bringup.launch.py
  ros2 launch my_pkg bringup.launch.py namespaces:=robot1,robot2
  ros2 launch my_pkg bringup.launch.py namespace:=my_robot
  ros2 launch my_pkg bringup.launch.py params_file:=/path/to/params.yaml
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument,
    LogInfo,
    OpaqueFunction,
    RegisterEventHandler,
    TimerAction,
)
from launch.event_handlers import OnProcessStart
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

# 参数覆盖表:YAML 里统一写 0.3,这里把 robot2 抬到 0.5。
# 这是"同一参数在不同命名空间取不同值"的落地方式。
THRESHOLD_BY_NS = {"robot1": 0.3, "robot2": 0.5}

# 节点B 起来后等多久再启动节点C。进程存在不代表 DDS 已完成注册,
# 留这段时间让 filter_node 把 publisher/subscription 建好。
STARTUP_DELAY = 1.5

NODE_NAMES = ("sensor_node", "filter_node", "executor_node")


def _resolve_ns(context):
    """单数参数优先;没给单数才用逗号分隔的列表。"""
    single = LaunchConfiguration("namespace").perform(context).strip()
    if single:
        return [single]
    raw = LaunchConfiguration("namespaces").perform(context)
    return [item.strip() for item in raw.split(",") if item.strip()]


def _resolve_params_file(context):
    """没显式给路径时,回落到包内的 share/my_pkg/config/params.yaml。"""
    given = LaunchConfiguration("params_file").perform(context).strip()
    if given:
        return given
    return os.path.join(
        get_package_share_directory("my_pkg"), "config", "params.yaml")


def _launch_setup(context):
    ns_list = _resolve_ns(context)
    if not ns_list:
        raise RuntimeError(
            "命名空间列表为空。示例:namespaces:=robot1,robot2")

    params_file = _resolve_params_file(context)
    # 文件不存在时直接抛 RuntimeError:launch 会打印
    # [ERROR] Caught exception in launch (see debug for traceback): <原因>
    # 不指定 --debug 时不会有 Python 栈回溯,正好满足题面的"清晰错误提示"。
    if not os.path.isfile(params_file):
        raise RuntimeError(
            "参数文件不存在: %s\n"
            "  请检查 params_file:= 的路径是否正确;"
            "省略该参数则使用包内默认文件。" % params_file)

    fallback = float(LaunchConfiguration("default_threshold").perform(context))

    actions = [LogInfo(msg="[bringup] 参数文件: %s" % params_file)]

    for ns in ns_list:
        threshold = THRESHOLD_BY_NS.get(ns, fallback)
        actions.append(LogInfo(
            msg="[bringup] >>> 命名空间 %s 启动:阈值=%.2fm,节点=%s"
                % (ns, threshold, "/".join(NODE_NAMES))))

        sensor = Node(
            package="my_pkg", executable="sensor_node",
            namespace=ns, name="sensor_node", output="screen",
            parameters=[params_file],
        )
        filter_node = Node(
            package="my_pkg", executable="filter_node",
            namespace=ns, name="filter_node", output="screen",
            parameters=[params_file],
        )
        # 列表里靠后的字典优先级更高,所以这一项会盖掉 YAML 里的 0.3
        executor = Node(
            package="my_pkg", executable="executor_node",
            namespace=ns, name="executor_node", output="screen",
            parameters=[params_file, {"alarm_threshold": threshold}],
        )

        actions.extend([sensor, filter_node])
        # 事件依赖:B 的进程真正起来之后,再等 STARTUP_DELAY 秒拉起 C
        actions.append(RegisterEventHandler(
            OnProcessStart(
                target_action=filter_node,
                on_start=[TimerAction(period=STARTUP_DELAY,
                                      actions=[executor])],
            )
        ))

    return actions


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument(
            "namespace", default_value="",
            description="单组模式:只启动这一个命名空间,例如 my_robot"),
        DeclareLaunchArgument(
            "namespaces", default_value="robot1,robot2",
            description="多组模式:逗号分隔的命名空间列表"),
        DeclareLaunchArgument(
            "params_file", default_value="",
            description="YAML 参数文件绝对路径,留空用包内默认文件"),
        DeclareLaunchArgument(
            "default_threshold", default_value="0.3",
            description="未在覆盖表中登记的命名空间使用的报警阈值"),
        OpaqueFunction(function=_launch_setup),
    ])
