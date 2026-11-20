# M5-1 任务状态机

纯 Python 状态机,把比赛任务按顺序 / 并发 / 重试 / 超时 / 降级串起来。
不依赖 BehaviorTree.CPP、Groot2、py_trees,也不依赖 ROS2 运行时 ——
终端可跑、日志带色、单测 29 个用例。

## 目录结构

```
M5-1/
├── task_fsm/               框架包
│   ├── core.py             调度内核(199 行,< 200)
│   ├── context.py          TaskContext
│   ├── nodes.py            5 个标准节点(Mock)+ MockNode 基类
│   ├── registry.py         type -> 节点类 的注册表 + 节点工厂
│   └── logger.py           彩色日志
├── run_task.py             入口:python run_task.py tasks/task_demo.yaml
├── tasks/
│   ├── task_demo.yaml      自带示例(含并发 / 重试 / 降级)
│   ├── task_demo.json      等价 JSON 版(环境没装 pyyaml 时用)
│   ├── task_simple.yaml    最小示例
│   └── poses/              位姿片段(当前未启用外部文件,见 docs/dsl.md)
├── tests/
│   ├── test_nodes.py       16 个节点级用例
│   └── test_fsm.py         13 个框架级用例
├── docs/
│   ├── design.md           设计说明(状态图 + 取舍)
│   └── dsl.md              YAML 字段说明(考官按此写测试 YAML)
└── README.md
```

## 运行

```bash
cd ~/22SmartCar/M5/M5-1

# 一键运行(官方约定的入口形式)
python3 run_task.py tasks/task_demo.yaml

# 其余示例
python3 run_task.py tasks/task_simple.yaml
python3 run_task.py tasks/task_demo.json --deadline 60 --report out.json

# 单测(不装 pytest 也能跑,两套都有自跑入口)
pytest tests/ -q
python3 tests/test_fsm.py
python3 tests/test_nodes.py
```

输出:终端彩色日志 + `task_report.json`。
退出码:`0` = 全部成功;非 0 = 失败 / 超时 / 中止。

## 核心设计

**分权**:三类角色,边界不能破。

| 角色 | 职责 | 不管什么 |
| :-- | :-- | :-- |
| `TaskNode` 子类 | 执行一次,返回 `SUCCESS`/`FAILED`/`SKIPPED` | 重试、超时、降级 |
| `StateMachine` | 重试 / 超时 / 降级 / 连坐 / 全局预算 | 具体任务怎么干 |
| `TaskContext` | 共享黑板:传数据、记预算、记降级 | 任何决策 |

节点若自己决定重试,状态机就失去调度权 —— 这是放弃 BehaviorTree 之后必须守住的边界。

### 状态与转移

```
IDLE -> RUNNING -> SUCCESS / RETRYING -> ... -> FAILED / SKIPPED
```

- 成功 → 下一节点
- 失败 → 重试(`retry` 次,间隔 `retry_interval`)→ 仍失败 → 走 `fallback`
- **超时 → 不重试**,直接走降级。超时说明节点卡死,重试大概率再卡一次
- 全局重试预算(默认 10 次)耗尽 → 立即判失败
- 无 fallback 且 `on_fail: abort` → 后续节点全部 SKIPPED(连坐)

### 几个关键取舍

**两层重试预算**:每步有 `retry`,全局有 `retry_budget`。只用每步,20 个节点 × 3 次重试
最坏跑 80 遍;只用全局,预算耗尽后偶发抖动也会让任务失败。

**fallback 只跳一层**:a→b 跑一次,b 再失败就停。不递归,所以 a→b、b→a 这种配置
不会无限跳 —— 这是官方明确要求的终止条件。

**Parallel 的失败策略**:`any_fail`(默认)任一分支失败即 `cancel()` 其余分支;
`all_must_succeed` 等全部自然结束。两种都做,由 YAML 的 `fail_policy` 选。

**取消是协作式的**:`cancel()` 只置标志位,Mock 节点把 sleep 切成 10ms 片检查一次。
Python 没有安全的线程强杀,这是唯一可靠做法。

**超时分两层**:分支的超时归分支自己的 `run_in_thread`,整体的归状态机外层。
早期版本两层共用同一个 timeout,两边同时到期、日志里出现两次超时记录,才改成现在这样。

## YAML DSL

完整字段、默认值、缺字段的报错信息、并发写法、完整示例:`docs/dsl.md`。

节点类型:`WaitForTrigger` `NavigateTo` `DetectTarget` `ExecuteGrasp` `SpeakTTS` `Parallel`。
新增节点只需在 `task_fsm/registry.py` 的 `REGISTRY` 加一行,内核不改。

## task_report.json

字段名按官方 README §2.4:

```json
{
  "task_id": "grab_and_return",
  "result": "SUCCESS",
  "total_elapsed_s": 1.61,
  "steps": [
    {"name": "wait_start", "type": "WaitForTrigger", "status": "SUCCESS",
     "elapsed_s": 0.104, "retries": 0, "degraded": false}
  ],
  "final_context": {"target_id": "qrcode", "target_pose": [1.0, 2.0, 0.0]}
}
```

`result` 四种取值:`SUCCESS` / `FAILED` / `TIMEOUT`(任务级 deadline)/ `ABORTED`(急停等外部中断)。

## 核心行数

```bash
wc -l task_fsm/core.py    # 199
```

统计范围:状态常量 + `TaskNode` + `Parallel` + `StateMachine`。
不含 `context.py`(数据结构)、`nodes.py`(节点实现)、`logger.py`(输出)、
`registry.py`(工厂)、`run_task.py`(入口与报告)。

## Mock 与真实 ROS2 的对应

Mock 用 `fail_times` + `duration` 替代硬件交互,保证离线可测、结果可复现。
真实化时只替换各节点 `execute()` 内部为 rclpy 调用,框架代码零改动。
`MockNode` 基类里的计时与人为失败能力不会进入真实节点。

## 已知限制(如实披露)

1. **节点不对外暴露执行进度**:长任务只能看到"运行中",看不到百分比。真实节点接入时
   需要在 `TaskContext.shared` 写进度字段。
2. **取消要节点配合**:卡在第三方阻塞调用里的节点收不到取消标志,只能等它返回。
3. **`!include` 不支持**:位姿只能内联或从上下文取,详见 `docs/dsl.md` §9。
4. **`any_fail` 的分支不是瞬间消失**:`cancel()` 后线程要等自己检查标志位退出。

## AI 使用声明(如实披露)

本模块代码由 AI(WorkBuddy)在作者提出需求与设计讨论后生成:

- 作者完成了模块选型分析、与 M0-4 调度器的架构对比、状态机设计要点的确认;
- 代码(框架 / 节点 / DSL 加载器 / 单测 / 文档)由 AI 生成,作者逐行阅读并验证运行结果;
- 目录结构按官方 README 的「交付物与格式」重构,逻辑未重写,仅拆分与补齐缺失能力
  (`on_fail`、`fail_policy`、`cancel()`、报告字段);
- 按考核规则,本模块接受现场答辩检验,作者已按答辩准备文档复习全部实现细节。
