# M5-4 复盘摘要字段定义

实现：`scripts/quick_review.py` 的 `review_run()`　产物：`summary.json` + `review_<ts>.md`

## 1. 顶层字段

| 字段 | 类型 | 含义 | 来源 |
| :-- | :-- | :-- | :-- |
| `run` | str | 这次运行的目录名 | 目录名 |
| `reviewed_at` | str | 复盘时间（带时区 ISO） | 运行时 |
| `mission_id` | str | 任务 ID，与 M5-1 的 `task_id` 对齐 | `task_report.json` |
| `final` | str | `SUCCESS` / `FAILED` / `UNKNOWN` | `task_report.json` |
| `total_s` | float | 各子任务耗时之和（秒） | 由 `tasks` 累加 |
| `estop_count` | int | 急停次数 | 黑匣子里 `event=estop` |

**为什么 `total_s` 是累加而不是末减首**：并行分支（M5-1 的 `Parallel`）
会让"第一个开始"到"最后一个结束"的墙钟时间明显大于各步之和，
两者都真实，但复盘要看的是"任务本身花了多少工"，所以取累加；
墙钟时间在 `task_report.json` 的 `total_elapsed_s` 里另有记录。

## 2. `tasks` —— 每步发生了什么

```json
"nav": {"status":"SUCCESS","attempts":2,"retries":1,
        "duration":12.5,"fallback":null}
```

| 字段 | 说明 |
| :-- | :-- |
| `status` | 该步最终状态（含 `SKIPPED` = 被降级顶替） |
| `attempts` | 节点自报的执行次数 |
| `retries` | 状态机层面的重试轮数 |
| `duration` | 该步耗时（秒） |
| `fallback` | 降级目标节点名，无则 `null` |

`status=SKIPPED` 且 `fallback` 非空 → 这一步是被降级替代的，
不是真的跳过，读报告时不要混。

## 3. `metrics` —— 关键指标表

| 键 | 含义 | 计算方式 |
| :-- | :-- | :-- |
| `perception_freq` | 感知平均频率 | `metric/perception_freq` 样本求均值 |
| `confidence` | 置信度均值 | `metric/confidence` 样本求均值 |
| `cpu_peak` | CPU 峰值 | `metric/cpu` 取最大 |
| `battery_min` | 电压最低值 | `metric/battery` 取最小 |
| `nav_success_rate` | 导航成功率 | 成功步数 / 总步数 |
| `path_optimality` | 路径最优性 | 实际路径 / 理论最短（无数据时为 null） |

`null` 与 `0` 严格区分：`null` = 这批数据里没有该指标，`0` = 有且为零。

## 4. `faults` 与 `root_causes`

`faults` 是**原始故障清单**（时间 + 类型 + 细节），
`root_causes` 是**归因后的一句话**，格式固定为：

```
现象(证据) → 采取的动作 → 代价
camera_degraded@12:34:56 (freq<10Hz) → nav fallback to lidar_only (+12s)
```

**只有 `faults` 没有 `root_causes` 的报告是不合格的** ——
题面明确禁止"只有现象没有因果链"。

## 5. `switch_changes` —— 参数/模型变更

来自 M5-3 的 `switch_log.csv`，每条含 `ts` / `type` / `old` / `new`。

**这次没切过怎么办**：`switch_changes` 是空列表 `[]`，
报告里显示"本次无参数变更"，不报错、不省略该行 ——
因为"没切"本身也是信息（说明这一把跑的是基线配置）。

## 6. `compare_with_prev` —— 与上一把的差异

仅当传了 `prev_dir` 且那边有 `summary.json` 时存在：

| 键 | 含义 |
| :-- | :-- |
| `metrics` | 有变化的指标及其增减量 |
| `new_faults` | 本把新增的故障类型 |
| `gone_faults` | 上把有、本把没有的故障 |
| `final_changed` | 结果是否变化 |

"自动找上一把"由 `exp.py run` 负责（按目录时间排序取前一个），
不需要人工指定文件 —— 题面明确禁止"手动指定上一把"。

## 7. 时间对齐（重要）

三个输入的时间基准**不同源**：

| 输入 | 时间基准 |
| :-- | :-- |
| `blackbox.jsonl` / `.mcap` | 系统单调时钟（或仿真 `use_sim_time`） |
| `task_report.json` | 墙钟（写文件时刻） |
| `switch_log.csv` | 墙钟 ISO 串 |

本实现以**黑匣子时间为准**做指标窗口，把另两份的时间戳按
"文件名顺序 + 相邻关系"对齐到最近的记录点，而不是直接相减 ——
两者相差可能是几十秒甚至（`use_sim_time=false` 时）上亿秒，
直接做差会得到荒谬的"耗时"。

这一点在 M1-3 的 `use_sim_time` 实验里已经实测过：漂移 +1.2 亿秒时，
按时间戳相减完全失效，按顺序对齐仍然正确。
