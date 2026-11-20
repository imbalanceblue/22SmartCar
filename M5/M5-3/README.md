# M5-3 裁判/交互/远程干预一体化终端

比赛现场「人机协作」的唯一入口：收裁判指令、语音/按键触发、热切参数与模型、
分层急停、全程审计。**一个界面装下全部，不开第二个终端。**
终端跑在**主机**上，小车端只新增一个 `/emergency_stop` 订阅者。

## 架构

```
   裁判指令(HTTP /api/referee/* 或话题)      操作员(Web / 语音 / 按键)
                 │                                      │
        ┌────────▼────────┐                   ┌─────────▼─────────┐
        │ referee_bridge  │                   │  terminal_app     │
        │  3-2-1 倒计时    │                   │  do_action() 总控 │
        │  PAUSE/STOP 解析 │                   │  10Hz 快照        │
        └────────┬────────┘                   └─────────┬─────────┘
                 │ 状态回报                             │
                 ├──────────────▶ 界面(SSE)             ├──▶ race_bridge(包 M5-1)
                 └──▶ 状态机 start/pause                ├──▶ hot_switch(切参数/模型)
                                                        ├──▶ safety(分层急停)
   watchdog(拔相机 → 降级) ──▶ 审计 + 语音 ◀─────────────┤
                                                        └──▶ audit(哈希链)
```

所有动作都经过同一个 `do_action()` 分发点，所以**审计只在一处埋点**，不会漏记。

## 文件结构（与官方交付树一致）

```
M5/M5-3/
├── host_side/
│   ├── referee_bridge.py        # 裁判适配器（3-2-1 倒计时、PAUSE/STOP/判罚）
│   ├── operator_terminal.py     # ★ 一体化终端 Web 入口（端口 8081）
│   ├── terminal_app.py          # 总控：动作分发 + 10Hz 快照
│   ├── race_bridge.py           # 在 M5-1 外插暂停/跳过/急停（不改内核）
│   ├── hot_switch.py            # 参数/模型热切 + switch_log.csv
│   ├── safety.py                # ★ 四层急停
│   ├── audit.py                 # 哈希链审计日志
│   ├── watchdog.py              # 1s 去抖判视觉降级
│   └── templates/terminal.html  # 界面（手写，无 CDN）
├── robot_side/
│   └── emergency_stop_node.py   # ★ 小车端唯一新增：急停订阅者
├── tools/
│   ├── verify_audit.py          # 审计链校验（PASS / FAIL + 第几行）
│   └── referee_sim.py           # 裁判模拟器（S/P/R/E 键盘或脚本序列）
├── config/
│   ├── terminal.yaml            # 可切场景/模型清单、滑块、端口
│   └── mission_race.yaml/.json  # 比赛编排
├── docs/
│   ├── design.md                # 集成架构 + 分层急停
│   ├── audit_format.md          # 审计字段 + 哈希链算法
│   └── ui.png                   # 界面功能分区图
├── tests/                       # 18 用例
└── README.md
```

## 运行

```bash
cd ~/22SmartCar/M5/M5-3

# 终端（一条命令，自动开浏览器；端口 8081）
python3 host_side/operator_terminal.py
python3 host_side/operator_terminal.py --no-open          # VM 里手动开
python3 host_side/operator_terminal.py --race-seconds 180 --time-scale 4

# 裁判模拟器（另开一个终端；S=START P=PAUSE R=RESUME E=STOP）
python3 tools/referee_sim.py --mode keyboard
python3 tools/referee_sim.py --mode script --script "START,PAUSE,RESUME"

# 审计校验
python3 tools/verify_audit.py --log audit_log_20261120.jsonl
```

> **端口必须是 8081**：M5-2 的仪表盘默认占 8080，现场两者同时跑，
> 端口冲突会导致其中一个起不来（题面明确要求错开）。

## 测试

```bash
cd ~/22SmartCar/M5/M5-3
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests/ -q
# 预期 18 passed
```

## 验收要点

| 项 | 位置 | 怎么验 |
| :-- | :-- | :-- |
| 协议覆盖 | `host_side/referee_bridge.py` | `referee_sim.py` 发 START/PAUSE/STOP/PENALTY |
| 热切不重启 | `host_side/hot_switch.py` | 切模型后看 **PID 未变** + `switch_log.csv` 有记录 |
| 分层急停 | `host_side/safety.py` | 四层：零速度 → `/emergency_stop` → 控制器 → 审计 |
| 急停不自动恢复 | 同上 | `sim.estopped` 置位后只能软复位清 |
| 审计防篡改 | `tools/verify_audit.py` | 手改一行再跑，应输出 `FAIL 第 N 条哈希被篡改` |
| 多模态 | `terminal_app.py` 的 `VOICE_ACTIONS` | 语音/按键映射到同一 `do_action()` |
| 小车端零额外进程 | `robot_side/emergency_stop_node.py` | 只有一个订阅者 |

## 设计要点（答辩会被问）

- **为什么急停要分四层**：只发一条零速度，别的发布者会覆盖、控制器超时后会恢复。
- **为什么复用 M5-1 而不改内核**：干预属于外部行为，用 guard 包装插入，内核 29 个用例仍然全过。
- **为什么暂停是"原子级"**：闸门只在节点边界检查，不把正在跑的节点从中间切断。
- **审计能防什么/不能防什么**：能防改任意一行、删中间行；**防不了删末尾行**
  （没有后一行指认它），需要外部锚点，本实现没做。详见 `docs/audit_format.md` §4。

## 如实披露

- 未接真实 ROS2：裁判指令来源是 HTTP，真实部署换话题订阅，`feed()` 通道不变；
- 语音用关键词匹配模拟，真实部署接离线唤醒词，且**急停类动作必须二次确认**（题面禁止语音直接急停）；
- `robot_side/emergency_stop_node.py` 是 mock 订阅者，真实车上替换为 `rclpy` 节点，接口一致。
