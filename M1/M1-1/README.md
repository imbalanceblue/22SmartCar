# M1-1 三节点链路 + 动态配参

虚拟超声波测距链路:传感器(10Hz) → 低通滤波(alpha 可动态改) → 阈值判断 + 报警服务。

```
sensor_node ──ultrasonic(my_interfaces/SensorData)──> filter_node
                                                          │
                                            processed_distance(std_msgs/Float32)
                                                          ▼
                                                    executor_node
                                                          │ 距离 < 阈值
                                                          ▼
                                              trigger_alarm(my_interfaces/TriggerAlarm)
                                                   (服务端也在 executor_node 内)
```

## 一、目录结构

```
M1/M1-1/
├── src/
│   ├── my_interfaces/            # 接口包(ament_cmake):只管 msg/srv 定义
│   │   ├── msg/SensorData.msg
│   │   ├── srv/TriggerAlarm.srv
│   │   ├── CMakeLists.txt
│   │   └── package.xml
│   └── my_pkg/                   # 逻辑包(ament_python):三个节点
│       ├── my_pkg/core.py        # 纯计算(滤波/仿真),不依赖 ROS,可离线测
│       ├── my_pkg/sensor_node.py     # 节点A 发布者
│       ├── my_pkg/filter_node.py     # 节点B 处理者
│       ├── my_pkg/executor_node.py   # 节点C 执行者(客户端 + 服务端)
│       ├── setup.py / setup.cfg / package.xml / resource/my_pkg
└── tools/sim_check.py            # 无 ROS 环境下的 4 项逻辑自检
```

## 二、编译

```bash
source /opt/ros/humble/setup.bash
cd ~/22SmartCar/M1/M1-1
colcon build --symlink-install
source install/setup.bash          # 每个新终端都要 source 这一句
```

首次编译若报缺组件:

```bash
sudo apt install -y python3-colcon-common-extensions
sudo apt install -y ros-humble-rosidl-default-generators
```

## 三、运行(三个终端,都要先 `source install/setup.bash`)

```bash
ros2 run my_pkg sensor_node      # 终端1:节点A
ros2 run my_pkg filter_node      # 终端2:节点B
ros2 run my_pkg executor_node    # 终端3:节点C
```

预期日志(约 4 秒后距离掉到 0.3m 以下):

```
[sensor_node]   sensor_node 启动:10.0Hz 发布 ultrasonic(SensorData)
[filter_node]   [FILTER] alpha=0.30 raw=0.812 smooth=0.812 unit=m
[executor_node] [TRIGGER] 调用报警服务:distance=0.296m < threshold=0.30m
[executor_node] [ALARM] 距离过近！(distance=0.296m < threshold=0.30m)
[executor_node] [OK] 报警成功:报警已触发@...
```

## 四、验收清单(对应题面每条要求)

| 题面要求 | 验证命令 | 预期 |
| :-- | :-- | :-- |
| 自定义消息 4 字段 | `ros2 interface show my_interfaces/msg/SensorData` | distance/unit/stamp/status 齐全 |
| 10Hz 发布 | `ros2 topic hz /ultrasonic` | average rate ≈ 10 |
| 话题能看到 | `ros2 topic list` | 出现 `/ultrasonic`、`/processed_distance` |
| 滤波结果 | `ros2 topic echo /processed_distance` | data 平滑跟随,无跳变 |
| alpha 可动态改 | `ros2 param set /filter_node alpha 0.9` | 终端日志里 alpha 立刻变 0.90,echo 抖动明显变大 |
| 滤除无效数据 | `ros2 run my_pkg sensor_node --ros-args -p fault_every:=20` | filter_node 打印"丢弃无效数据 status=1/2" |
| 报警服务 | `ros2 service list \| grep trigger_alarm` | 服务存在;低于阈值时打印 `[ALARM] 距离过近！` |
| 服务超时 | `ros2 param set /executor_node server_delay 1.5` | 下次报警打印 `[WARN] 报警服务超时` |

**动态调参对比(答辩必演)**:先 `alpha=0.1` 再 `alpha=0.9`,同时看 `ros2 topic echo /processed_distance` —— 0.1 时曲线圆滑滞后,0.9 时几乎跟着原始噪声跳。

离线先验一遍数学逻辑(不需要 ROS):

```bash
python3 tools/sim_check.py     # 预期 PASS: 4 项逻辑自检全部通过
```

## 五、参数表

| 节点 | 参数 | 默认 | 说明 |
| :-- | :-- | :-- | :-- |
| sensor_node | `publish_rate` | 10.0 | 发布频率 Hz(仅启动时生效) |
| sensor_node | `wave_period` | 6.0 | 距离起伏周期 s,决定多久报一次警 |
| sensor_node | `center` / `amplitude` | 0.55 / 0.35 | 距离在 center±amplitude 之间往复 |
| sensor_node | `noise_std` | 0.05 | 高斯噪声标准差,让滤波效果肉眼可见 |
| sensor_node | `fault_every` | 0 | 每 N 帧注入一次异常(1=超量程 2=错误) |
| sensor_node | `seed` | 0 | 噪声种子,固定则每次运行一致 |
| filter_node | `alpha` | 0.3 | 一阶低通系数,**运行时改立即生效** |
| filter_node | `log_every` | 10 | 每 N 条打印一行日志 |
| executor_node | `alarm_threshold` | 0.3 | 报警阈值 m |
| executor_node | `cooldown` | 2.0 | 两次报警最小间隔 s,防刷屏 |
| executor_node | `service_timeout` | 1.0 | 服务等待上限 s,超时打印 WARN |
| executor_node | `server_delay` | 0.0 | 服务端模拟延迟 s,用来演示超时 |

## 六、设计决策(答辩会被问)

1. **接口包与逻辑包分离**:`my_interfaces` 用 `ament_cmake`(rosidl 生成器只在 CMake 下工作),`my_pkg` 用 `ament_python`。这是 ROS2 官方推荐的落地方式——消息属于"协议",节点属于"业务"。
2. **纯计算抽到 `core.py`**:滤波公式、距离公式不 import rclpy,因此能在没装 ROS 的机器上跑 `tools/sim_check.py`。ROS 层只做收发,便于测试与复用。
3. **alpha 每条消息都重读参数**:如果只在 `__init__` 缓存一次,`ros2 param set` 改了不生效——这正是题面"运行时动态修改并看到效果"的考点。
4. **首个样本直接作初值**:若从 0 开始滤波,前几拍会输出接近 0 的假距离,触发假报警。
5. **服务端放在节点C内部**:题面只要求三个节点,报警服务模拟"车载蜂鸣器驱动",内置后可自闭环;真实部署时可拆成独立节点,客户端代码只需改服务名。
6. **多线程执行器 + ReentrantCallbackGroup**:节点C 在自己的订阅回调里调用自己提供的服务,单线程执行器下服务请求永远排不上队(假超时)。多线程才能并发处理。
7. **异步调用 + 看门狗判超时**(而非在回调里死等):`call_async` 后立即返回,100ms 定时器检查是否超期。超时后把 `token` 加一,迟到的响应会被丢弃,不会污染下一次报警的结果。
8. **冷却时间**:距离在阈值附近抖动时会连续触发,加 `cooldown` 让演示输出干净(题面未要求,属于工程习惯)。

## 七、常见问题

| 现象 | 原因 / 解决 |
| :-- | :-- |
| `Package 'my_interfaces' not found` | 没 source:`source install/setup.bash` |
| `colcon: command not found` | `sudo apt install -y python3-colcon-common-extensions` |
| 编译报 `rosidl_default_generators` 找不到 | `sudo apt install -y ros-humble-rosidl-default-generators` |
| 改了代码没反应 | 用了 `--symlink-install` 时 Python 改动无需重编;否则重跑 `colcon build` |
| `[WARN] 报警服务不可用` | 节点C 还没起来(服务端在它内部);先起 C 再起别的,或等 1 秒重试 |
| 一直不报警 | 检查 `alarm_threshold` 与距离范围:`ros2 topic echo /ultrasonic` 看实际值 |

## 八、AI 使用说明(如实披露)

- 需求拆解与接口设计:作者与 AI 共同讨论确定(三个节点职责、参数名、演示方式)。
- 代码(`src/` 与 `tools/sim_check.py`)由 AI 生成,作者逐行阅读并核对题面条款。
- 作者在 Ubuntu 22.04 + ROS2 Humble 环境完成 `colcon build`、三节点联调、动态调参与超时演示,并将结果作为验收证据。
- 答辩与现场变体(改阈值、改 alpha、注入异常帧)由作者本人完成。
