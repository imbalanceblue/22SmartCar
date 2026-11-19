# M5-3 裁判 / 交互 / 远程干预一体化终端

> 比赛现场"人机协作"唯一入口:裁判指令、任务控制、热切参数、急停求助、
> 全程审计,全部收敛在**一个网页终端**里。终端跑在**主机**上,
> 小车端只新增一个 `/emergency_stop` 订阅者。

## 1. 一句话架构

```
       裁判(话题/串口/键盘)                      操作员(Web/语音/按键)
                │                                        │
                ▼                                        ▼
      referee_bridge.py                          operator_terminal.py
   协议解析 / 3-2-1 / 状态发布                  HTTP + SSE + 控制 API
                │                                        │
                ├──────────────┬─────────────────────────┤
                ▼              ▼                         ▼
        race_bridge.py    hotswap.py               audit.py
       M5-1 状态机 +      场景/模型/参数热切      只追加哈希链
       暂停/跳过/急停      switch_log.csv          audit_log_<date>.jsonl
                │
                ▼
        watchdog.py(相机断了→降级+语音)+ car_side.py(零速度+黑匣子)
                │
                ▼
        M5-2 的 Aggregator / CarSim 数据链路(复用,不复制)
```

## 2. 文件清单

| 文件 | 职责 | 关键点 |
| :-- | :-- | :-- |
| `referee_bridge.py` | 裁判协议适配器 | 6 条指令、5 种状态、3-2-1 发车、键盘模拟 S/P/R/E |
| `race_bridge.py` | 任务桥(复用 M5-1) | 节点包装插入暂停/跳过/急停，**不改 M5-1 内核** |
| `hotswap.py` | 参数/模型/场景热切 | `switch_log.csv` 落盘、pid 不变证明"不重启" |
| `watchdog.py` | 视觉看门狗 | 连续 1s 频率 < 50% 才判降级（去抖，防误报） |
| `audit.py` | 审计日志 | append-only + SHA-256 哈希链 + 篡改定位 + 毫秒时间戳 |
| `terminal_app.py` | 终端核心 | 把上述部件拧成 10Hz 快照 + 统一 `do_action()` |
| `operator_terminal.py` | Web 服务入口 | 纯标准库 HTTP + SSE、`/api/action/*`、导出/校验 |
| `static/terminal.html` | 终端页面 | 任务控制/状态机/热切/故障流/审计，**无 CDN** |
| `car_side.py` | 小车端唯一订阅者(mock) | `/emergency_stop` → 零速度 + `blackbox.jsonl` |
| `mission_race.{yaml,json}` | 比赛编排 | 识别失败→退回重新导航的 fallback 链 |
| `tests/` | 18 个用例 | 4 审计 + 7 裁判 + 4 热切 + 3 全流程 |

## 3. 运行

```bash
# 主机上(VM 里同样能跑,零第三方依赖)
python3 operator_terminal.py                 # 自动开浏览器,默认 8090
python3 operator_terminal.py --no-open       # 不自动开
python3 operator_terminal.py --race-seconds 180 --time-scale 4
```

无 pyyaml 的环境会自动用 `mission_race.json` 孪生文件。

键盘模拟裁判（仅真终端）:`S`=发车 `P`=暂停 `R`=继续 `E`=急停。

## 4. 演示脚本（3 分钟）

1. **发车**：点「启动任务」→ 全屏 3-2-1 → 裁判 `RUNNING` → 状态机从
   `wait_start` 依次点亮，倒计时开始走。
2. **热切**：模型选 `yolov8n_int8` → FPS 12→32、`switch_log.csv` 落一行、
   感知节点 PID **不变**（证明热加载而非重启）。
3. **降级**：点「拔相机」→ 1s 后看门狗判 `DEGRADED` → 故障流红条
   → `detect_qr` 重试后走 fallback 变橙 → 语音播报「视觉降级，切纯激光导航」。
4. **急停**：点「紧急停车」→ 顶栏变 `FAULT`、车端收到零速度
   （页面显示 `(0.0, 0.0)`）、`EMERGENCY_STOP` 入审计。
5. **举证**：点「导出 JSONL」下载审计链；`/api/audit/verify` 返回
   `{ok: true}`；改一行再验 → 精确定位到第几条断链。

## 5. 协议与状态

裁判指令 → 我方状态（题面要求）：

| 指令 | 合法前置状态 | 效果 |
| :-- | :-- | :-- |
| `START` | READY / STOPPED | 3-2-1 后转 RUNNING，状态机开跑 |
| `PAUSE` | RUNNING | PAUSED（节点边界原子暂停） |
| `RESUME` | PAUSED | RUNNING |
| `STOP` | READY/RUNNING/PAUSED | STOPPED |
| `PENALTY` | 任意 | 犯规 +1，加罚时长计入倒计时 |
| `SCORE_QUERY` | 任意 | 返回状态/犯规/剩余时间 |

我方发布状态：`READY` `RUNNING` `PAUSED` `STOPPED` `FAULT`。

## 6. 设计决策（答辩重点）

1. **不改 M5-1 内核**：暂停/跳过/急停都是"外部干预"，用节点包装（guard）
   在 `execute()` 外面插入，内核一行没动 —— 复用而非复制。
2. **暂停是原子级的**：闸门只在节点边界生效，不会把执行中的节点拦腰切断。
   演示编排的超时设成 60s，避免长时间暂停被误判 TIMEOUT。
3. **跳过 ≠ 判负**：操作的语义是"这个节点不要了"。有 fallback 就走 fallback
   （`detect_qr` → 重新导航）；没有 fallback 就让流程继续，记录里标 `SKIPPED`
   并注明 `skipped_by: operator`。
4. **看门狗用时间去抖**：连续 1s 低于 50% 期望频率才判降级。单帧丢失是常态，
   一断就报会让状态机疯狂走 fallback。
5. **审计用哈希链而非数据库**：JSONL 只追加、无外部依赖、可直接贴进申诉材料；
   每条含 `prev_hash`，篡改任意一条后面全断，`verify()` 能定位到序号。
6. **SSE 而非 WebSocket**：与 M5-2 同一取舍 —— 单向推送场景等价，
   零第三方依赖，现场断网也能跑。
7. **热切改的是"在线参数"而非文件**：真实环境是 `ros2 param set`；
   本实现保留 pid、只递增 `reloads`，用"pid 不变"作为"没重启"的可验证证据。

## 7. 已知限制（如实披露）

1. Mock 环境不跑真 ROS2：`/referee/cmd`、`/emergency_stop` 用 HTTP/键盘模拟，
   接口语义与话题一致，换成 `rclpy` 发布订阅即可。
2. 语音只做**关键词匹配**（"开始/暂停/停车/求助"），未接 vosk/sherpa-onnx 模型；
   接口 `POST /api/voice {"text": ...}` 已留好，接真 ASR 只需把识别结果喂进来。
3. TTS 用系统 `espeak`（没有就只在页面显示播报文本），不引入云端语音。
4. 硬复位是"全栈重建"，比赛进行中按题面要求直接拒绝（返回中文错误）。
5. 暂停期间倒计时仍在走（裁判时间不因我方暂停而停），这是赛制语义。

## 8. AI 使用声明（如实）

- 需求理解、验收标准拆解、演示脚本设计：用户与 AI 讨论确定；
- 代码（`*.py` / `*.html` / 编排文件）由 AI 生成；
- 用户在 VM 中实际运行、逐项验证（发车/热切/降级/急停/审计校验），
  并负责答辩讲解；
- 未使用任何未披露的第三方代码；实现仅依赖 Python 标准库。
