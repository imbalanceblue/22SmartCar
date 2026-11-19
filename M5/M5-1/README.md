# M5-1 任务状态机

纯 Python 状态机,把比赛任务按顺序/并发/重试/超时/降级串起来。
不依赖 BehaviorTree.CPP / Groot2 / ROS2,终端可跑、日志清晰、单测覆盖。

## 文件结构

```
M5/M5-1/
├── state_machine.py    # 核心框架:TaskContext + TaskNode + Parallel + StateMachine
├── mock_nodes.py       # 5 个 Mock 任务节点(继承 TaskNode)
├── runner.py           # 入口:YAML/JSON DSL -> 节点实例 -> 运行 -> 报告
├── mission.yaml        # 主编排文件(顺序 + 重试 + 降级)
├── mission_parallel.yaml # 并发示例(Parallel)
├── mock/ros2.py        # Mock ROS2 接口桩(Action/Service 形状)
├── tests/test_nodes.py # pytest 单测(16 个用例)
└── README.md
```

## 运行

```bash
cd ~/22SmartCar/M5/M5-1
python3 runner.py --config mission.yaml
python3 runner.py --config mission_parallel.yaml
python3 runner.py --config mission.yaml --deadline 60 --report out.json
python3 -m pytest tests/ -v        # 单测
```

## 核心设计(答辩重点)

**分权架构**:M0-4 的调度器是"中央集权"(run 函数管一切);M5-1 分权为三块:

| 角色 | 职责 | 不管什么 |
| :-- | :-- | :-- |
| `TaskNode` 子类 | 执行一次,返回 `SUCCESS`/`FAILED` | 重试、超时、降级 |
| `StateMachine` | 调度:重试/超时/降级/连坐/预算 | 具体任务怎么干 |
| `TaskContext` | 全程共享"黑板":传数据、记预算 | 任何决策 |

### 状态与转移

`IDLE -> RUNNING -> SUCCESS / RETRYING / FAILED / SKIPPED / TIMEOUT`

- 成功 -> 下一节点
- 失败 -> 重试(`retry` 次,间隔 `retry_interval`) -> 仍失败 -> 走 `fallback` 降级
- **超时 -> 不重试,直接走降级**(设计决策:超时说明环境卡死,重试大概率再超时,快速降级更有价值)
- 全局重试预算 `retry_budget` 耗尽 -> 立即判失败
- 最终失败 -> 后续所有节点 SKIPPED(连坐)

### Parallel 失败策略(设计决策)

**等所有分支跑完再汇总,任一分支 FAILED/TIMEOUT 则整体 FAILED。**
不选"一失败立即取消其余分支":避免线程强杀和清理的复杂性,Mock 场景下分支耗时短,等完的代价可忽略。

### fallback 语义(设计决策)

主节点最终失败 -> 执行 fallback 指向的节点(它自带重试) -> 成功则**继续主流程**(fallback_used 记录降级发生) -> 失败则后续 SKIPPED。

## YAML DSL 字段

| 字段 | 适用 | 说明 |
| :-- | :-- | :-- |
| `name` | 全部 | 节点名(唯一) |
| `type` | 全部 | 节点类名(见 REGISTRY) |
| `timeout` | 全部 | 单次执行超时(秒),默认不限 |
| `retry` | 全部 | 失败重试次数,默认 0 |
| `retry_interval` | 全部 | 重试间隔(秒),默认 0.5 |
| `fallback` | 全部 | 降级目标节点名 |
| `fail_times` | 全部 | Mock:前 N 次故意失败 |
| `duration` | 全部 | Mock:模拟执行耗时(秒) |
| `pose` | NavigateTo | 固定目标坐标 |
| `pose_from_context` | NavigateTo | 从上下文读坐标(如 `target_pose`) |
| `target_class` / `min_confidence` | DetectTarget | 目标类别/置信度阈值 |
| `grasp_type` | ExecuteGrasp | 抓取方式 |
| `text` | SpeakTTS | 播报内容 |
| `branches` | Parallel | 子任务列表 |

## task_report.json

```json
{
  "mission_id": "grab_and_return",
  "final": "SUCCESS",
  "tasks": {
    "detect_qr": {"type": "DetectTarget", "status": "SUCCESS",
                   "attempts": 1, "retries": 0, "duration": 0.2,
                   "fallback": null}
  },
  "fallback_used": {},
  "retry_budget_left": 10,
  "final_context": {"target_pose": [1.0, 2.0, 0.0], "...": "..."}
}
```

每个节点记录:最终状态 / 执行次数 / 重试次数 / 耗时 / 降级目标;外加最终上下文。

## Mock 说明与真实 ROS2 的对应

Mock 用 `fail_times` + `duration` 替代真实硬件交互,保证离线可测、结果可复现。
真实化时只需替换各节点 `execute()` 内部为 rclpy 调用(接口形状见 `mock/ros2.py`),
框架代码零改动 —— 这是"分权"架构的直接收益。

## 已知限制(如实披露)

1. 超时后子线程无法强杀,只能不再等它;真实场景需用可取消的 Action。
2. Parallel 分支支持各自独立 timeout(分支自己的 timeout 字段);
   节点整体 timeout 由状态机外层判定,内部不做二次超时(避免竞争)。
3. fallback 只支持单层跳转,不做 fallback 链。

## AI 使用声明(如实披露)

本模块代码由 AI(WorkBuddy/Claude)在作者口述需求与设计讨论后生成:
- 作者完成了模块选型分析、与 M0-4 的架构对比、状态机设计要点确认;
- 代码(框架/Mock 节点/DSL 加载器/单测)由 AI 生成,作者逐行阅读并验证运行结果;
- README 与设计决策说明由 AI 起草,作者审核;
- 按考核规则,本模块接受现场答辩检验,作者已按答辩准备文档复习全部实现细节。
