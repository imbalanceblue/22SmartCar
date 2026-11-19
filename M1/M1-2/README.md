# M1-2 ｜ Launch 编排：一键启动

> ⚠️ **本题代码不在本目录**，而在 `M1-1/src/my_pkg/{launch,config}/`。
> 原因：题面指定的命令是 `ros2 launch my_pkg bringup.launch.py`，而 `ros2 launch <包名> <文件名>` 要求 launch 文件必须安装在 `share/<包名>/` 下，只能放在 `my_pkg` 包里，不能是独立目录。
> 本目录只放题解与验收步骤。

## 一句话

M1-1 是三个孤立节点（要开三个终端、source 三次、按顺序敲三次命令），M1-2 把它们装配成**一条命令启动**，并支持命名空间隔离、YAML 参数、参数覆盖、启动顺序、友好报错。

## 交付物位置

题目要求 `ros2 launch my_pkg bringup.launch.py`，所以 launch 与配置文件必须装进 `my_pkg` 的 share 目录，物理位置在 M1-1 的包内：

| 文件 | 作用 |
| :-- | :-- |
| `M1-1/src/my_pkg/launch/bringup.launch.py` | Python launch 入口 |
| `M1-1/src/my_pkg/config/params.yaml` | 三个节点的全部参数 |
| `M1-1/src/my_pkg/setup.py` | 已补 `data_files`，把上面两目录装进 share |
| `M1-1/src/my_pkg/package.xml` | 已补 `launch` / `launch_ros` / `ament_index_python` 依赖 |

节点代码一个字都没改——M1-2 是纯工程化改造。

## 题面六条要求对照

| # | 要求 | 实现 |
| :-- | :-- | :-- |
| 1 | Python launch，一键起三节点 | `bringup.launch.py` + `OpaqueFunction` |
| 2 | `namespace:=my_robot` 传参，全部节点同一命名空间，节点名显式指定 | `DeclareLaunchArgument("namespace")`；`_resolve_ns()` 单数优先；每个 `Node(namespace=ns, name="xxx_node")` |
| 3 | B 的滤波参数、C 的阈值从 YAML 加载，路径可配 | `params_file` 参数；`_resolve_params_file()` 留空回落包内默认 |
| 4 | 同一参数在不同命名空间取不同值 | `THRESHOLD_BY_NS = {"robot1": 0.3, "robot2": 0.5}`，用 `parameters=[params_file, {"alarm_threshold": ...}]` 覆盖（列表靠后的优先） |
| 5 | 两组实例同时运行，互不干扰 | `namespaces` 默认 `robot1,robot2`，循环生成两套；话题自动变 `/robot1/ultrasonic`、`/robot2/ultrasonic` |
| 6 | C 必须在 B 完全启动后才启动 | `RegisterEventHandler(OnProcessStart(target_action=filter_node, on_start=[TimerAction(1.5s, [executor])]))` |
| 7 | YAML 不存在给清晰提示，不要栈回溯 | `os.path.isfile()` 检查后 `raise RuntimeError("参数文件不存在: ...")`；launch 默认只打印一行 `[ERROR] Caught exception in launch (see debug for traceback): 原因` |

## 运行方式

```bash
# 0. 先编译(改了 setup.py 的 data_files,必须重新 build)
cd ~/22SmartCar/M1/M1-1
source /opt/ros/humble/setup.bash
colcon build --symlink-install
source install/setup.bash

# 1. 默认两组:robot1(阈值0.3) + robot2(阈值0.5)
ros2 launch my_pkg bringup.launch.py

# 2. 单组,自定义命名空间
ros2 launch my_pkg bringup.launch.py namespace:=my_robot

# 3. 三组
ros2 launch my_pkg bringup.launch.py namespaces:=robot1,robot2,robot3

# 4. 指定参数文件
ros2 launch my_pkg bringup.launch.py params_file:=/home/imbalanceblue/22SmartCar/M1/M1-1/src/my_pkg/config/params.yaml

# 5. 错误演示:文件不存在
ros2 launch my_pkg bringup.launch.py params_file:=/tmp/nope.yaml
```

## 验收步骤

### ① 一键启动,看命名空间区分

```bash
ros2 launch my_pkg bringup.launch.py
```

终端应出现类似:

```
[bringup] 参数文件: /home/.../install/my_pkg/share/my_pkg/config/params.yaml
[bringup] >>> 命名空间 robot1 启动:阈值=0.30m,节点=sensor_node/filter_node/executor_node
[bringup] >>> 命名空间 robot2 启动:阈值=0.50m,节点=sensor_node/filter_node/executor_node
[filter_node-2] filter_node 启动:alpha=0.45, ultrasonic -> processed_distance
[filter_node-4] filter_node 启动:alpha=0.45, ultrasonic -> processed_distance
```

### ② 六个进程都在

```bash
ros2 node list
```

期望 6 个:

```
/robot1/executor_node
/robot1/filter_node
/robot1/sensor_node
/robot2/executor_node
/robot2/filter_node
/robot2/sensor_node
```

### ③ 话题自动隔离

```bash
ros2 topic list
```

期望看到 `/robot1/ultrasonic`、`/robot1/processed_distance`、`/robot2/ultrasonic`、`/robot2/processed_distance` 等成对的隔离话题。

### ④ 参数覆盖生效(核心验证)

```bash
ros2 param get /robot1/executor_node alarm_threshold
```

应为 `Double value is: 0.3`

```bash
ros2 param get /robot2/executor_node alarm_threshold
```

应为 `Double value is: 0.5`

```bash
ros2 param get /robot1/filter_node alpha
```

应为 `Double value is: 0.45`(代码里默认是 0.3)。**这是 YAML 真正被加载的铁证**——如果这里显示 0.3,说明参数文件没读到,只是用了代码默认值。

**行为层面的证据(最直观)**:盯 launch 终端看 `[ALARM]` 出现的密度。`robot2` 阈值 0.5,距离 0.2~0.9 的波动中有一大半时间低于 0.5,所以报警明显比 `robot1`(阈值 0.3,只有谷底才触发)更密集。**同一个可执行文件、同一份参数文件,只因为命名空间不同就表现不同**,这就是参数覆盖生效。

### ⑤ 启动顺序

观察 launch 输出时间:每个 `filter_node` 启动日志出现约 1.5 秒后,才出现对应 `[executor_node-N] executor_node 启动`。两组各自独立计时。

### ⑥ 报错友好

```bash
ros2 launch my_pkg bringup.launch.py params_file:=/tmp/nope.yaml
```

期望只打印一两行:

```
[ERROR] [launch]: Caught exception in launch (see debug for traceback): 参数文件不存在: /tmp/nope.yaml
```

**不应该**出现大段 `Traceback (most recent call last):`。

## 设计决策(答辩问答)

**Q:为什么用 `OpaqueFunction` 而不是直接拼 Node?**
参数个数不固定(命名空间数量由命令行决定),必须在 launch 运行时才能知道要建几个 Node。`OpaqueFunction` 提供 `context`,可以 `perform()` 出参数值再动态生成 action 列表。

**Q:参数覆盖为什么写 `[params_file, {"alarm_threshold": x}]`?**
`parameters` 是一个列表,后面的元素覆盖前面的。第一个元素加载 YAML,第二个字典只改一个键,所以 robot2 得到 0.5 而其他参数仍来自 YAML。这比写两份 YAML 干净。

**Q:为什么先起 B 再起 C,而不是同时?**
进程创建和节点真正"上线"是两件事。`ros2 run` 拉起进程后,rclpy 还要初始化中间件、创建 publisher/subscription,才真正对外可见。如果 C 与 B 同时创建,C 可能在 B 还没出版话题时就开始订阅,前期收到的数据是空的。用 `OnProcessStart` 监听 B 的进程启动事件,再延迟 1.5 秒拉起 C,是成本最低且可靠的顺序保证。

**Q:为什么 `raise RuntimeError` 就满足"不要栈回溯"?**
launch service 会捕获 action 里的异常,默认只打印一行 `Caught exception in launch (see debug for traceback): <异常信息>`,完整栈回溯只有在 `--debug` 时才输出。所以异常信息本身写得清楚,用户看到的就是人话。

**Q:命名空间隔离是靠什么实现的?**
ROS2 的话题名是相对名解析的。节点里写 `create_publisher(SensorData, "ultrasonic")` 用的是相对名,最终全名 = `/{namespace}/ultrasonic`。同一份代码在不同命名空间下自动产生不同话题,不需要改代码——这就是命名空间存在的意义。

**Q:`/**` 通配会不会把参数塞给不该收的节点?**
不会。参数文件里的键只是 override 记录,节点只有在 `declare_parameter` 声明某个键时才会去取,取不到就用代码里的默认值。所以 sensor 节点看到 `alarm_threshold` 也只是一条无关记录,不会报错也不会生效。

## 提交计划(5 条)

1. `feat(m1-2): 新增 YAML 参数文件集中管理三节点配置`
2. `feat(m1-2): 编写 bringup launch 支持命名空间与一键启动`
3. `feat(m1-2): 通过参数覆盖实现多命名空间差异化阈值`
4. `feat(m1-2): 用事件依赖保证节点C在节点B之后启动`
5. `chore(m1-2): 安装 launch 与 config 目录并补充依赖声明`
