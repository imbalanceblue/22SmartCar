# M5-2 上位机可视化监控面板

一屏看清全栈健康度与任务进度：任务状态机、关键指标、故障标签流、裁判系统、系统资源。
**零第三方运行时依赖**（纯 Python 标准库 + 原生 HTML/JS/Canvas），断网现场可跑。

## 架构

```
┌──────────────┐  10Hz 聚合快照    ┌──────────────────┐  SSE 推送     ┌────────────┐
│ Mock 小车     │ ───────────────→ │ host_side/app.py │ ────────────→ │ 浏览器五区  │
│ CarSim       │   /dashboard/*   │ (HTTP + SSE)     │  /api/stream  │ index.html │
└──────────────┘                  │ + StateBridge    │               └────────────┘
                                  └──────────────────┘
```

- **聚合而非转发**：原始话题不外传，聚合器算好"频率/延迟/丢包/置信度均值"再推，小车端只出摘要。
- **故障联动**：拔相机 → 感知频率骤降 → 聚合器判 `CAMERA_DEGRADED` → 状态桥让 DetectTarget 失败 →
  重试 → 走降级（页面节点变橙）。
- **复用 M5-1**：`host_side/state_bridge.py` 直接 import M5-1 的 `task_fsm`，
  题目要求 M5-2/3/4 复用 M5-1 的节点接口与上下文结构。
- **零硬编码**：话题与阈值全部来自 `config/dashboard.yaml`，改配置即改行为。

## 文件结构（与官方交付树一致）

```
M5/M5-2/
├── robot_side/
│   └── dashboard_aggregator.py  # ★ 小车端聚合节点（144 行，满足 < 150）
├── host_side/
│   ├── app.py                   # Web 应用入口（HTTP + SSE + 自动开浏览器）
│   ├── state_bridge.py          # 后台线程跑 M5-1 状态机，状态镜像给面板
│   ├── mock_source.py           # Mock 小车：确定性仿真 + 故障注入
│   ├── requirements.txt
│   └── static/index.html        # 前端五区（手写 Canvas，无 CDN）
├── config/
│   ├── dashboard.yaml           # ★ 话题模式 + 阈值（零硬编码的关键）
│   ├── dashboard.json           # 同内容 JSON，无 pyyaml 环境降级
│   └── mission_demo.yaml/.json  # 演示编排（与 M5-1 接口一致）
├── tools/
│   └── measure_resources.py     # 量 CPU/内存，60s 窗口平均
├── docs/
│   ├── architecture.md          # 双轨通信 + 为何不传图
│   ├── fault_rules.md           # 五类故障的阈值与持续时间
│   └── layout.png               # 真实运行截图(五区标注)
├── tests/                       # 9 单测 + 1 全流程集成
└── README.md
```

## 运行

```bash
cd ~/22SmartCar/M5/M5-2
python3 host_side/app.py                # 自动打开浏览器 http://127.0.0.1:8080
python3 host_side/app.py --no-open      # 不自动开（VM 里手动开浏览器）
python3 host_side/app.py --port 9000    # 换端口
python3 host_side/app.py --config config/dashboard.yaml   # 指定配置（默认就是它）
```

> 端口固定 **8080**；M5-3 的终端占 8081，现场两者同时跑不会冲突。

## 页面操作

发车 → 状态机节点依次变绿/蓝 → 点「📷 拔相机」→ 故障标签 1s 内出现、
感知折线归零、detect_qr 红色重试后走降级变橙 → 「⬤ 急停」→ 「💾 导出快照」。

## 测试

```bash
cd ~/22SmartCar/M5/M5-2
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests/ -q
# 预期 10 passed
```

## 验收要点

| 项 | 位置 | 怎么验 |
| :-- | :-- | :-- |
| 聚合节点 < 150 行 | `robot_side/dashboard_aggregator.py` | `wc -l` |
| 零硬编码 | `config/dashboard.yaml` | 改 `topics` 里的频率再启动，指标随之变化 |
| 故障 < 1s 上屏 | `docs/fault_rules.md` §4 | 拔相机看标签流 |
| 比赛不传图 | `config/dashboard.yaml` 的 `video.enable: false` | 看配置 |
| 资源占用 | `tools/measure_resources.py --pid <pid> --seconds 60` | 现场量 |

## 关于技术选型（如实披露）

题目允许 FastAPI + WebSocket 等方案。本实现用**标准库 HTTP + SSE**：

- 仪表盘是**纯下行**场景，SSE 与 WebSocket 能力够用；
- 浏览器原生 `EventSource` 零依赖、自动重连；
- 现场断网、无 pip 环境也能起。

若要求真接 ROS2，把主循环换成 `rclpy` 订阅、SSE 换成 rosbridge 的 WS 即可，
聚合逻辑与界面不用改。详见 `docs/architecture.md`。
