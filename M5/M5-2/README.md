# M5-2 上位机可视化监控面板

一屏看清全栈健康度与任务进度:任务状态机、关键指标、故障标签流、裁判系统、系统资源。
**零第三方运行时依赖**(纯 Python 标准库 + 原生 HTML/JS/Canvas),断网现场可跑。

## 架构

```
┌──────────────┐  10Hz 聚合快照   ┌────────────────┐  SSE 推送     ┌────────────┐
│ Mock 小车     │ ──────────────→ │ server.py       │ ────────────→ │ 浏览器五区   │
│ CarSim       │   /dashboard/*  │ (HTTP+SSE)      │  /api/stream  │ index.html │
└──────────────┘                 │  + StateBridge  │               └────────────┘
                                 └────────────────┘
```

- **聚合思想**:原始话题不外传,聚合器算好"频率/延迟/丢包/置信度均值"再推 —— 小车端只出摘要。
- **故障联动**:拔相机 → 感知频率骤降 → 聚合器 1s 内产出 `CAMERA_DEGRADED` → 状态桥让 DetectTarget 失败 → 重试 → 走降级(页面节点变橙)。
- **复用 M5-1**:`state_bridge.py` 直接 import M5-1 的 `StateMachine`/`runner`,题目要求 M5-2/3/4 复用 M5-1 节点接口与上下文结构。

## 文件结构

```
M5/M5-2/
├── aggregator.py            # 指标聚合引擎(滑动窗口:频率/延迟/丢包/置信度 + 故障规则)
├── mock_source.py           # Mock 小车:确定性仿真 + 故障注入 + 裁判模拟
├── state_bridge.py          # 后台线程跑 M5-1 状态机,状态实时镜像给面板
├── server.py                # HTTP + SSE 服务、控制 API、快照导出、自动开浏览器
├── mission_demo.yaml/.json  # 演示编排(与 M5-1 接口一致;无 pyyaml 环境自动用 .json)
├── static/index.html        # 前端五区:状态机/折线图/故障流/裁判/资源(手写 Canvas,无 CDN)
├── tests/                   # 9 单测 + 1 个全流程集成测试
└── README.md
```

## 运行

```bash
cd ~/22SmartCar/M5/M5-2
python3 server.py                # 自动打开浏览器 http://127.0.0.1:8080
python3 server.py --no-open      # 不自动开(SSH 远程时用)
python3 server.py --port 9000
```

页面操作:发车 → 状态机节点依次变绿/蓝 → 点「📷 拔相机」→ 故障标签 1s 内出现、
感知折线归零、detect_qr 红色重试后走降级变橙 → 「⬤ 急停」→ 「💾 导出快照」。

## 测试

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests/ -v
```

(VM 上必须加前缀禁用 ROS2 的 pytest 插件,M0 时期踩过的坑)
集成测试会真实起服务读 SSE 流,验证:零配置出页面 / 状态机同步 / 故障<1s / 降级联动 / 急停 / 导出。

## 控制 API(也是手机/平板同屏的入口)

| 方法 | 路径 | 作用 |
| :-- | :-- | :-- |
| GET | `/api/stream` | SSE 10Hz 快照流 |
| GET | `/api/latest` | 最新一张快照 |
| GET | `/api/export` | 导出 dashboard_snapshot(自包含 HTML,可离线回放) |
| POST | `/api/referee/start` / `pause` / `resume` / `foul` | 裁判模拟 |
| POST | `/api/fault/camera_off` / `npu_overheat` / `low_battery` | 故障注入(切换开关) |
| POST | `/api/estop` | 紧急停车 |
| POST | `/api/reset` | 软复位(重建状态机上下文,不重启进程) |

## 设计决策与取舍(答辩重点)

1. **SSE 而非 WebSocket**:题目允许三选一(FastAPI+WS / Dash / 自选)。仪表盘是"服务端→浏览器"单向推送场景,SSE 用浏览器原生 EventSource 实现,零依赖、断网可跑;真要双向高频上行再升级 rosbridge/WS(架构不变,换传输层)。
2. **数据降频聚合**:小车端 <2% CPU 的关键是"算完再传"——聚合器 3s 滑动窗口算指标,10Hz 只推摘要,原始数据不出去。视频默认不传(题目要求)。
3. **故障判定防误报**:刚启动没收到过数据 ≠ 相机坏了。区分"从未收到(未知)"与"收到过但频率骤降(故障)"(`ever_received` 字段)。
4. **状态同步实现**:状态机跑在后台线程,每张快照轮询其 records 字典镜像节点状态;节点 RUNNING/RETRYING 时记录里就是中间态,天然实时。
5. **零配置**:server 启动即自动建线程、自动开浏览器;未知话题自动注册进聚合器(自动发现)。

## 已知限制(如实披露)

1. 急停 = 让当前节点立即返回 FAILED 并标记 STOPPED,不是"零速度指令 + 黑匣子"(那是 M5-3 范畴)。
2. SSE 每个客户端一条 HTTP 连接,不支持浏览器→服务端高频上行(控制用普通 POST)。
3. Mock 源替代真实 ROS2;真实化时把 `mock_source` 换成 ROS2 订阅,聚合器/前端零改动。

## AI 使用声明(如实披露)

本模块代码由 AI(WorkBuddy/Claude)在作者明确需求下生成:作者完成了与 M5-1 的复用关系确认、
运行环境决策(VM + 无真车 Mock)、功能验收标准对照;代码由 AI 生成,作者逐行阅读并在 VM 验证;
前端无第三方库依赖的方案是作者为"断网现场可跑"提出的要求。按考核规则,本模块接受现场答辩检验,
作者已按《M5-1/2 答辩准备》文档复习全部实现细节。
