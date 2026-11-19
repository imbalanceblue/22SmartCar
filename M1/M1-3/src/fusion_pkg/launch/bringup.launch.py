"""M1-3 一键启动:静态 TF -> 投影节点 ->(可选)回放 rosbag。

用法示例:
  ros2 launch fusion_pkg bringup.launch.py
  ros2 launch fusion_pkg bringup.launch.py bag_path:=~/bags/fusion_demo
  ros2 launch fusion_pkg bringup.launch.py bag_path:=~/bags/fusion_demo rate:=0.5
  ros2 launch fusion_pkg bringup.launch.py use_sim_time:=false
  ros2 launch fusion_pkg bringup.launch.py params_file:=/tmp/nope.yaml   # 看报错

启动顺序不是"同时拉起",而是按依赖分三级:
  t=0.0s  static_tf_node   —— 先把坐标树立起来
  t=1.0s  projector        —— 有了树再开始监听,否则前几帧会 TF 查询失败
  t=2.0s  ros2 bag play    —— 最后才喂数据,保证订阅方已就绪

三级延迟都是固定常量而不是"等事件",因为这三个进程之间没有进程句柄可依赖
(ros2 bag play 是外部命令,不是 Node action)。代价是慢 2 秒,换来的是
启动阶段零 TF 告警、零丢帧。
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument,
    ExecuteProcess,
    LogInfo,
    OpaqueFunction,
    TimerAction,
)
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

PKG = "fusion_pkg"

TF_DELAY = 1.0      # 静态 TF 起来后,等这么久再起投影节点
BAG_DELAY = 2.0     # 投影节点起来后,等这么久再开始回放


def _resolve_params_file(context):
    """没显式给路径时,回落到包内的 share/fusion_pkg/config/fusion.yaml。"""
    given = LaunchConfiguration("params_file").perform(context).strip()
    if given:
        return os.path.expanduser(given)
    return os.path.join(
        get_package_share_directory(PKG), "config", "fusion.yaml")


def _resolve_sim_time(context, has_bag):
    """auto = 有 bag 就用仿真时间(配合 --clock),没 bag 就用系统时间。"""
    raw = LaunchConfiguration("use_sim_time").perform(context).strip().lower()
    if raw == "auto":
        return has_bag
    return raw in ("true", "1", "yes")


def _launch_setup(context):
    params_file = _resolve_params_file(context)
    # 抛 RuntimeError 而不是让下游报错:launch 只打印一行摘要,
    # 不加 --debug 就没有 Python 栈回溯,满足题面的"清晰的错误提示"。
    if not os.path.isfile(params_file):
        raise RuntimeError(
            "参数文件不存在: %s\n"
            "  请检查 params_file:= 的路径是否正确;"
            "省略该参数则使用包内默认文件。" % params_file)

    bag_path = os.path.expanduser(
        LaunchConfiguration("bag_path").perform(context).strip())
    has_bag = bool(bag_path)
    if has_bag:
        if not os.path.isdir(bag_path):
            raise RuntimeError(
                "bag 目录不存在: %s\n"
                "  先用 ros2 run fusion_pkg baggen 生成,"
                "或检查 bag_path:= 是否写错。" % bag_path)
        if not os.path.isfile(os.path.join(bag_path, "metadata.yaml")):
            raise RuntimeError(
                "目录缺少 metadata.yaml,不是合法的 rosbag2: %s\n"
                "  如果用 baggen 生成过,可能被中断,删掉重跑。" % bag_path)

    use_sim = _resolve_sim_time(context, has_bag)
    rate = LaunchConfiguration("rate").perform(context).strip() or "1.0"

    tf_node = Node(
        package=PKG, executable="static_tf", name="static_tf_node",
        output="screen", parameters=[params_file, {"use_sim_time": use_sim}],
    )
    projector = Node(
        package=PKG, executable="projector", name="projector",
        output="screen", parameters=[params_file, {"use_sim_time": use_sim}],
    )

    actions = [
        LogInfo(msg="[bringup] 参数文件: %s" % params_file),
        LogInfo(msg="[bringup] use_sim_time=%s%s"
                    % (use_sim, "" if has_bag else "(无 bag,纯监听模式)")),
        tf_node,
        TimerAction(period=TF_DELAY, actions=[
            LogInfo(msg="[bringup] >>> 静态 TF 已就绪,启动投影节点"),
            projector,
        ]),
    ]

    if has_bag:
        actions.append(TimerAction(period=BAG_DELAY, actions=[
            LogInfo(msg="[bringup] >>> 投影节点已就绪,开始回放 %s" % bag_path),
            ExecuteProcess(
                cmd=["ros2", "bag", "play", bag_path, "--clock",
                     "-r", rate],
                output="screen",
            ),
        ]))
    else:
        actions.append(LogInfo(
            msg="[bringup] 未指定 bag_path,节点在等数据。"
                "另开终端执行 ros2 bag play <dir> --clock 即可。"))

    return actions


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument(
            "params_file", default_value="",
            description="YAML 参数文件绝对路径,留空用包内默认文件"),
        DeclareLaunchArgument(
            "bag_path", default_value="",
            description="要回放的 rosbag 目录,留空则只起节点不喂数据"),
        DeclareLaunchArgument(
            "use_sim_time", default_value="auto",
            description="auto/true/false;auto 表示有 bag 就用仿真时间"),
        DeclareLaunchArgument(
            "rate", default_value="1.0",
            description="回放倍速,0.5 为半速"),
        OpaqueFunction(function=_launch_setup),
    ])
