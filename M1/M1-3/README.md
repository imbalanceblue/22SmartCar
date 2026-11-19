# M1-3 ｜ 时间与坐标:让两个传感器"说到一起去"

## 一句话

激光雷达说"我前方 7.8 米有面墙",摄像头只知道自己拍了一张图。本题把激光的每一个点算成图像上的一个像素,近处画红、远处画蓝 —— 如果 TF 坐标树和相机内参都对,这些点会**精确叠在墙面上**;错一点,点就飘到天上或画面外。

## 题面四条对照

| # | 题面要求 | 实现 | 证据 |
| :-- | :-- | :-- | :-- |
| 1 | 发布 `map→odom→base_link→laser_link` 与 `base_link→camera_link` 静态 TF | `static_tf.py`,`StaticTransformBroadcaster` 一次推整棵树 | `ros2 run tf2_tools view_frames` 出树图 |
| 2 | 用 `TimeSynchronizer` 或 `ApproximateTimeSynchronizer` 同步两路 | `projector.py` 用 `ApproximateTimeSynchronizer`,`slop=0.05s` | 日志 `[SYNC] #N 点数=360 可见=… 漂移=…` |
| 3 | 2D 激光点投影到图像,按距离着色(近红远蓝),输出 ≥3 张不同时刻的图 | `geometry.project` + `color_by_range` + `write_png`,`save_interval=1.5` 每 1.5 秒存一张 | `~/fusion_out/proj_*.png` |
| 4 | 正确处理 `use_sim_time`,并演示 true↔false 的行为差异 | launch 参数 `use_sim_time:=auto/true/false`,节点日志打印 `漂移` | 漂移从 `+0.00s` 变成 `-1.6e8 s` |

## 数据来源声明(必须如实说)

**本题的 rosbag 是自生成的,不是官方 TurtleBot4 数据。** 生成器在 `fusion_pkg/baggen.py`,一条命令产出含 `/scan`(360 线,10Hz)和 `/camera/image_raw`(640×480 rgb8,10Hz)的标准 rosbag2,可直接 `ros2 bag play`。

两个原因:

1. 官方 bag 通常几十到上百 MB,这台 VM 走 NAT 网络下载不可靠(GitHub 22 端口已被 reset 过)
2. 更关键的是**官方 bag 的激光-相机外参不公开**,无法验证"投影到底对不对",只能凭肉眼看;自造数据里房间尺寸、车的位置、相机内参全部已知,投影结果可以**数学验证**

代价是:自己造的数据天然自洽,不能证明"代码能处理真实数据"。所以额外写了 `tools/check_geometry.py`,7 项独立断言专门卡几何一致性,而不是只截一张"看起来不错"的图。

## 架构

```
/scan ─────────┐
               ├─ ApproximateTimeSynchronizer ─→ 取 stamp ─→ tf2 查 camera←laser
/camera/image_raw ─┘                                     ↓
                                          激光极坐标 → 平面点 → 补 z=0 → 相机系
                                                         ↓
                                          针孔投影 (fx,fy,cx,cy) → 像素
                                                         ↓
                                          按距离着色画点 → PNG + /projected_image
```

坐标树(全部静态):

```
map ──→ odom ──→ base_link ──┬──→ laser_link    (0.20, 0.00, 0.20)  无旋转
                             └──→ camera_link   (0.05, 0.00, 0.40)  轴重排
```

`base_link` 用 ROS 惯例(x 前 / y 左 / z 上),`camera_link` 用光学惯例(x 右 / y 下 / z 前),两者差一个轴重排,四元数 `(-0.5, 0.5, -0.5, 0.5)`。**这是本题最容易错的一处**:写成单位四元数程序不报错,但所有点会跑出画面。`check_geometry.py` 第 1、8 项专门守这个。

外参只在 `geometry.py` 定义一次,`static_tf`(发布)、`baggen`(生成)、`projector`(校验)三处共用,不存在"三份拷贝不一致"。

## 运行

### 0. 依赖

```bash
sudo apt install -y python3-numpy ros-humble-tf2-ros ros-humble-tf2-tools ros-humble-message-filters ros-humble-rosbag2-py ros-humble-rosbag2-storage-default-plugins
```

### 1. 编译

```bash
cd ~/22SmartCar/M1/M1-3
source /opt/ros/humble/setup.bash
colcon build --symlink-install
source install/setup.bash
```

期望 `Summary: 1 package finished`。若报 `No module named 'em'`,先 `conda deactivate` 再 `rm -rf build install log` 重来。

### 2. 离线自检(不启动 ROS,先验证几何)

```bash
python3 tools/check_geometry.py
```

期望末尾 `结果: 全部通过`。这一步会在没有任何 ROS 进程的情况下,把"四元数↔矩阵""TF↔几何层""投影↔反投影""点是否落在墙上"全验一遍。**答辩时可以拿这个当"我为什么确信投影是对的"的答案。**

### 3. 生成 bag

```bash
ros2 run fusion_pkg baggen --frames 50
```

默认写到 `~/bags/fusion_demo`,50 帧 = 5.0 秒。终端会打印 `已写 10/50 帧 …` 和最后的回放命令。

### 4. 一键启动(节点 + 回放)

```bash
ros2 launch fusion_pkg bringup.launch.py bag_path:=~/bags/fusion_demo
```

启动顺序是三级,不是同时拉起:

| 时刻 | 动作 |
| :-- | :-- |
| t=0.0s | `static_tf_node` 先建立坐标树 |
| t=1.0s | `projector` 开始监听(树已在,前几帧不会 TF 失败) |
| t=2.0s | `ros2 bag play --clock` 开始喂数据 |

期望日志:

```
[bringup] 参数文件: /home/.../share/fusion_pkg/config/fusion.yaml
[bringup] use_sim_time=True
[static_tf_node] 已发布静态 TF: map->odom->base_link->{laser_link, camera_link}
[bringup] >>> 静态 TF 已就绪,启动投影节点
[projector] projector 启动:同步 /scan + /camera/image_raw,输出 /home/.../fusion_out
[bringup] >>> 投影节点已就绪,开始回放 /home/.../bags/fusion_demo
[projector] [SYNC] #1 点数=360 可见=79 漂移=+0.005s
[projector] [SAVE] 第 1/3 张 -> /home/.../fusion_out/proj_00_t1672531204.0.png
```

**`漂移`的量级**是 `use_sim_time=True` 生效的证据:节点时钟跟着 bag 的 `/clock` 走,与消息时间戳在同一条时间轴上,所以漂移在 0~1 秒量级(具体数值等于消息在队列里的等待时间,不是时钟误差)。对照 `use_sim_time=false` 时的 −1.6 亿秒,差九个数量级,一眼可辨。

### 5. 看图

```bash
gio open ~/fusion_out/proj_01_*.png
```

（`gio open` 会调系统默认看图程序;如果没装图形化的看图工具,用下面的 scp 传回宿主机看。）

正确的图长这样:一张灰白渐变(墙 + 地面)的室内画面,墙面上竖着一排彩色小圆点,**红点在近处(左右墙)、蓝点在远处(前墙)**,所有点都在墙面区域内,没有一个浮在画面上方的天空里。

## 五项验收

### ① 坐标树完整

另开终端:

```bash
cd ~/22SmartCar/M1/M1-3
source /opt/ros/humble/setup.bash
source install/setup.bash
```

```bash
ros2 run tf2_tools view_frames
```

```bash
evince frames.pdf
```

期望看到 `map → odom → base_link → {laser_link, camera_link}` 五个 frame、四条边。

也可以不生成 pdf 直接问:

```bash
ros2 run tf2_ros tf2_echo base_link camera_link
```

期望打印 `Translation: [0.050, 0.000, 0.400]` 和四元数 `(-0.500, 0.500, -0.500, 0.500)`。

### ② 时间同步

看 projector 日志的 `[SYNC]` 行。`点数=360` 是激光原始点数,`可见=79` 是落在画面内的点数(相机水平视场 77°,每 1° 一条激光,理论约 77 条,加上画布边界允许的半个像素余量实测 79 —— 这个数字本身就是几何正确的旁证)。

如果同步没生效,会一直不打印 `[SYNC]`,或者打印 `点数=0`。

### ③ 三张投影图

```bash
ls -l ~/fusion_out/
```

期望 3 个 `proj_*.png`,时间戳相差约 1.5 秒(`save_interval=1.5`,按消息时间戳计,与处理了多少帧无关)。

三张图里点会**整体向画面左侧或右侧移动**,因为车在沿 +x 前进,墙相对车的角度在变。这证明是"不同时刻"而不是复制的同一张。

### ④ use_sim_time 切换(题面明确要求演示)

这是本题唯一需要"行为对比"的地方。**同一份 bag,只改一个参数,漂移值变化七个数量级。**

**关闭当前 launch,用 false 重跑:**

```bash
ros2 launch fusion_pkg bringup.launch.py bag_path:=~/bags/fusion_demo use_sim_time:=false
```

期望日志:

```
[bringup] use_sim_time=False
[projector] [SYNC] #1 点数=360 可见=79 漂移=+122578566.947s
```

**为什么是 +1.2e8 秒(正号)**:`漂移 = 节点当前时间 − 消息时间戳`。bag 里的时间戳固定为 2023-01-01 00:00:00(为了让差异一眼可见,`baggen.BASE_STAMP_NS` 故意写死),而节点此时用系统时间(2026 年)。消息在**过去**,所以减出来是**正数**:约 1418 天 ≈ 3.89 年 ≈ 1.2258 亿秒。具体数值随运行日期变动,但量级一定是 10⁸。

**关键结论(答辩要能说清)**:

- `use_sim_time=false` 时,节点时钟 = 系统墙钟,与 bag 时间戳脱节,**漂移巨大但不影响投影结果** —— 因为本题的 TF 全是静态的,静态变换与时间无关,查任意时刻都返回同一个值
- 如果 TF 是**动态**的(比如 `base_link` 会随车移动),这个漂移就是致命的:节点会去查"2026 年的 base_link 在哪",而 Buffer 里只有 2023 年的数据,`lookup_transform` 会直接抛 `LookupException`,投影全部失败
- 所以 `use_sim_time` 不是可选项,是**回放场景的强制项**:仿真时间让节点与数据活在同一个时间轴里

录屏时先跑 `use_sim_time:=true` 记一段,再 Ctrl+C 换 `false` 记一段,两段日志并排就是证据。

**两个实测现象(答辩可能被追问,先记住)**:

| 现象 | 数字 | 说明 |
| :-- | :-- | :-- |
| 两种模式的 `可见` 数**逐帧一致** | true: 79/81/83<br>false: 79/81/83 | 漂移变了 1.2 亿秒,但可见点数一个不差 —— 这是"`use_sim_time` 只改时钟、不改几何"最硬的证据 |
| 可见数本身从 79 漂到 83 | +4 | 相机位置在变,视场边缘有 2~4 个点在"恰好画内/画外"之间切换,与 `use_sim_time` 无关 |
| 启动首帧有一次 `TF 查询失败` | `累计 1 次` | `projector` 起来后 TF 经 DDS 传到 Buffer 要几十毫秒,而 bag 几乎同时开始回放,首帧抢在 TF 之前到达。代码捕获 `LookupException`、跳过该帧、累计计数,后续全部正常 —— **这正是题面要求的 lookup 失败处理**。若不捕获,此处会抛栈回溯并让整个节点退出 |

### ⑤ 报错友好

```bash
ros2 launch fusion_pkg bringup.launch.py params_file:=/tmp/nope.yaml
```

期望**只有两行**,没有 `Traceback`:

```
[ERROR] [launch]: Caught exception in launch (see debug for traceback): 参数文件不存在: /tmp/nope.yaml
  请检查 params_file:= 的路径是否正确;省略该参数则使用包内默认文件。
```

```bash
ros2 launch fusion_pkg bringup.launch.py bag_path:=/tmp/not_a_bag
```

```
[ERROR] [launch]: Caught exception in launch (see debug for traceback): bag 目录不存在: /tmp/not_a_bag
  先用 ros2 run fusion_pkg baggen 生成,或检查 bag_path:= 是否写错。
```

## 答辩问答

**Q: 为什么用 `ApproximateTimeSynchronizer` 而不是 `TimeSynchronizer`?**

严格同步要求两路消息时间戳**完全相同**(纳秒级)。相机和激光是两块独立硬件,各自打时间戳,即使都标称 10Hz,实际采样时刻也不会对齐到同一纳秒。用严格同步会一直等一个永远不存在的完美配对,表现为"一帧都不输出"。近似同步允许 50ms 容差,是工程上的正确做法。代价是可能配到"相差 40ms"的两帧 —— 车在 1.3m/s 下 40ms 走 5.2cm,对本次投影影响可忽略。

**Q: 怎么证明投影是对的,而不是"看着像对"?**

三层证据:
1. `check_geometry.py` 第 2 项:TF 推出的 `camera←laser` 变换与几何层算的坐标,误差 < 1e-9 m —— 证明广播出去的四元数和代码里用的旋转是同一个
2. 第 7 项:79 个可见点投影到渲染图上,**没有一个落在天空区域**,全部落在墙面上 —— 端到端闭环
3. 第 8 项反向验证:把四元数故意改错,可见点比例从 50% 掉到 0% —— 证明这组检查真的能抓到错,不是"怎么写都通过"

**Q: 外参错一点会怎样?**

按本题内参(fx=400)、3 米距离算:

| 误差 | 像素偏移 |
| :-- | :-- |
| 平移 5cm | 6.3 px |
| 平移 10cm | 12.7 px |
| 旋转 1° | 7.0 px |

所以外参必须精确到厘米和度。这也是为什么真实系统要做手眼标定,而不是拿卷尺量。

**Q: 为什么激光点只有 79 个可见,不是 360 个?**

相机水平视场 = 2·arctan(cx/fx) = 2·arctan(320/400) = 77.3°。激光是 360 线、每线 1°,所以落在视场内的约 77 个(画布边界允许半个像素余量,实测 79)。剩下 281 个在相机背后或侧后方,深度为负,被 `project()` 的 `min_depth` 裁掉。**这个 79 不是巧合,是视场角的直接体现** —— 如果 TF 旋转写错,这个数字会明显偏离。

**Q: 为什么把 `map→odom→base_link` 也做成静态?**

这是离线回放场景,车在 bag 里的位姿是已知且固定的(我们不跑定位和里程计),所以整棵树都可以静态发布。真实系统里 `map→odom` 由定位模块动态给出,`odom→base_link` 由轮式里程计动态给出,只有 `base_link→传感器` 是静态的。README 里把这一点写明,是因为"全静态"是为了简化,不是不知道真实做法。

**Q: 三张图里点为什么会移动?**

车以 1.3m/s 沿 +x 前进,**第 3 秒后原地停住**,bag 共 5.0 秒。前墙距离由 5.0m 缩到 1.1m 后保持不动。三张存图固定落在 **0.0 / 1.5 / 3.0 秒**(`save_interval=1.5`,首帧即存),对应前墙 **5.0m / 3.05m / 1.1m**,颜色依次为**蓝 → 紫 → 红**(着色标尺 0.3~6m)。如果三张图完全一样,说明 bag 没在推进或保存逻辑写错了。

> **为什么第 3 秒要停车**:三条约束必须同时满足——3.0 秒时前墙 ≤1.2m(第 3 张进红段)、全程不撞墙(x<7.8)、bag 明显长于 3.0 秒(节点处理有滞后,要给足余量凑到保存阈值)。只提速不缩短行驶时间会贴墙,只加长不刹车会撞墙。停车之后 bag 可以任意加长而车距不变,三个要求一次满足。
>
> **反例**:曾用 35 帧(3.4 秒)+1.3m/s,车距和颜色都对,但只出了 2 张图 —— 因为节点处理滞后,队列里最后能处理到的消息时间戳约 1202.6,而第 3 张需要 1203.0,差 0.4 秒永远等不到。**bag 长度必须覆盖"最后一个保存时刻 + 处理延迟余量"**,这不是颜色问题,是数据长度问题。

> **车速为什么定 1.3m/s**:保存时刻被 `save_interval` 锁死在 0/1.5/3.0 秒,所以 3.0 秒时的车距直接决定最后一张的颜色。1.3 × 3.0 = 3.9m,从 x=3.0 到 x=6.9,离墙 1.1m → RGB(219,40,35) 纯红。若用 1.0m/s,3.0 秒只到 2.0m,颜色停在紫红,看不到红端。**这是"标尺必须覆盖数据实际区间"的具体体现**,不是随手定的数。

> **为什么点排成一条横线而不是铺满墙面**:`/scan` 是 2D 单线激光,360 条射线全部在同一个水平面(z=0.2m)上。投影后深度只影响 v 坐标一点点(`v = cy + fy·0.2/z`,1.1m→313px、5.0m→256px),所以所有点必然落在同一条接近水平的线上。这是 2D 激光的固有特性,不是投影错误。若换成 3D 雷达(如 VLP-16),才会投影成多条线覆盖整面墙。

## 已知限制(如实写)

1. **数据是合成的**:见上文"数据来源声明"。合成数据的最大局限是图像干净、无运动模糊、无光照变化,真实相机不会这么好。
2. **激光是 2D 单线**:`/scan` 只有 x、y,z 恒为 0。所以投影只用平面点,不需要处理 3D 点云的旋转。题面明确说"`/scan` 是 2D 激光,只有 x 和 y",符合要求。
3. **没做去畸变**:相机内参只用了 fx/fy/cx/cy,没考虑畸变系数 k1/k2。合成图无畸变,所以不需要;真实相机必须加。
4. **`/clock` 依赖 bag 的 `--clock`**:如果手动 `ros2 bag play` 忘了加 `--clock`,而节点 `use_sim_time=true`,节点时钟会停在 0,`漂移` 会显示为巨大的负数。launch 里已经默认带 `--clock`,手动回放时要注意。
5. **VirtualBox 下处理能力有限**:图像消息 900KB、10Hz,在虚拟机里单帧处理(解码 + 360 点投影 + 着色 + 重编码)约几十毫秒,速度跟不上时会积压,表现为日志里 `#N` 的序号涨得比消息时间慢、`漂移` 涨到 0.7 秒左右。队列深度已提到 30 缓解,若要彻底实时,建议 `rate:=0.5` 半速回放或换宿主机直跑。**这不影响投影正确性**,只影响吞吐。
