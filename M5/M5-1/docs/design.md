# 设计说明:状态图与取舍

## 1. 模块划分

```
task_fsm/
├── core.py       调度内核:状态常量 + TaskNode + Parallel + StateMachine(199 行)
├── context.py    TaskContext:任务 ID / 截止时间 / 共享字典 / 重试预算 / 降级记录 / 元数据
├── nodes.py      5 个标准节点的 Mock 实现 + MockNode 基类(计时与人为失败)
├── registry.py   type → 节点类 的注册表 + 节点工厂 build_node()
└── logger.py     彩色日志(非终端自动退化纯文本)
run_task.py       入口:读 DSL → 建节点 → 跑状态机 → 写 task_report.json
```

**为什么把 mock 的计时逻辑放 `nodes.py` 而不是 `core.py`**
`run_once`(按 `fail_times` 前 N 次失败)和 `sleep_interruptible` 只服务于测试替身,
真实节点不会用。放进 `MockNode` 让 `core.py` 里只剩调度,行数才压得进 200。

**为什么要有 `registry.py`**
`build_node()` 把 YAML 字典变成节点对象。这一层独立后,新增节点类型只需在
`REGISTRY` 加一行,调度内核一行都不用改 —— 这是"改 YAML 不改代码"能成立的机制前提。

## 2. 状态转移

```
                    ┌──────────────────────────────────────────┐
                    │                                          │
   IDLE ──start──> RUNNING ──execute 返回 SUCCESS──> SUCCESS ──┼──> 下一个节点
                    │                                          │
                    ├──执行返回 FAILED──> RETRYING ──预算未用完─┘
                    │                        │
                    │                        └──预算用完──> fallback?
                    │                                        ├─有:跑 fallback
                    │                                        │   ├─成功 → 本节点标 SKIPPED,继续
                    │                                        │   └─失败 → 本节点标 FAILED,后续全 SKIPPED
                    │                                        └─无:看 on_fail
                    │                                            ├─abort → FAILED,后续全 SKIPPED
                    │                                            └─skip  → SKIPPED,继续
                    │
                    └──超时(timeout 触发)──> 不重试,直接进 fallback / on_fail 分支
```

任务级还有两条出口:

```
某个节点执行中 → ctx.deadline 已过 → result = TIMEOUT(剩余节点不执行)
StateMachine.abort() 被调用     → result = ABORTED(取消所有节点)
```

## 3. 五个关键取舍

### 3.1 重试预算:全局 10 次 + 每步 `retry`

**做法**:每个节点有自己的 `retry` 次数,同时共享 `TaskContext.retry_budget`(默认 10)。
任一节点要重试时,先从全局预算里扣 1;预算见底则直接判失败。

**为什么不只用每步次数**:如果一个任务有 20 个节点、每步 `retry: 3`,
最坏情况会跑 80 次,现场 180 秒根本跑不完。全局预算是一道总闸。

**为什么不只用全局**:全局预算耗尽后所有节点都失去重试能力,后面一个偶发抖动
也会让任务失败。两层配合,既有总量控制,又给每步留余地。

### 3.2 `fallback` 只跳一层,不递归

**做法**:主节点失败 → 跑一次 fallback;fallback 再失败 → 直接判 FAILED。

**为什么不递归**:a 的 fallback 是 b、b 的 fallback 是 a 时,递归会无限跳。
现在 `a → b` 跑一次,b 失败就停,不会有环。**这是官方明确要求的终止条件。**

**要不要计数**:不计数也没有死循环风险,因为只跳一层。如果将来要支持多级降级链,
就必须引入"已访问集合"或跳转计数上限。

### 3.3 超时不重试

**做法**:`timeout` 触发返回 `TIMEOUT`,不消耗 `retry`,直接进降级/`on_fail` 分支。

**理由**:超时通常意味着节点卡死(等不到服务、函数阻塞)。再跑一次大概率还是卡死,
只是把时间浪费两遍。失败(逻辑错误、临时异常)才值得重试。

### 3.4 协作式取消,不是强杀

**做法**:`cancel()` 只置 `_cancelled = True`;Mock 节点的 `sleep_interruptible`
把 sleep 切成 10ms 片,每片检查一次标志位,发现取消就提前返回。
框架在 `run_in_thread` 超时时会主动调 `cancel()`。

**为什么不是强杀**:Python 没有安全的线程强杀机制(`Thread._stop` 会留下锁未释放的
隐患)。协作式取消是唯一可靠做法,代价是节点作者必须在耗时循环里检查标志位。

**已知不足**:真实 ROS2 节点如果卡在 `action_client.wait_for_result()`,
标志位检查不到,只能等它自己返回。正确做法是节点内部把长任务拆成
"分段等待 + 检查 cancel",这一层留给了接真实节点时的实现者。

### 3.5 `Parallel` 超时分两层

**做法**:分支的超时由各分支自己的 `run_in_thread` 负责;整体的超时由状态机外层
`_run_with_retry` 负责,`Parallel.execute()` 内部不再套一层 timeout。

**踩过的坑**:早期版本在 `Parallel.execute` 里也用了 `run_in_thread`,结果节点级的
timeout 和分支级的 timeout 同时生效,两边同时到期、同时判失败,日志里出现两次
超时记录。分层后责任清晰:分支管自己,整体管总闸。

## 4. 核心为什么是 199 行

官方要求调度内核 **< 200 行**(不含节点实现)。统计口径:

```bash
wc -l task_fsm/core.py    # 199
```

范围内:`状态常量 + TaskNode 基类 + Parallel + StateMachine`。
不在范围内:`context.py`(数据结构)、`nodes.py`(节点实现)、`logger.py`(输出)、
`registry.py`(工厂)、`run_task.py`(入口与报告)。

压缩手法(逻辑零改动,29 个用例回归兜底):模块与类 docstring 压到两行内、
合并连续赋值、删除只做转发的属性、把报告生成移到入口。

## 5. 验证方式

```bash
# 冒烟:完整任务(含并发、重试、降级)
python run_task.py tasks/task_demo.yaml

# 单测(两套都带自跑入口,不装 pytest 也能跑)
python tests/test_fsm.py      # 13 个框架级用例
python tests/test_nodes.py    # 16 个节点级用例
```

`test_fsm.py` 覆盖的重灾区:

| 用例 | 验的是什么 |
| :-- | :-- |
| `test_fallback_also_fails_no_loop` | fallback 也失败时能终止,不无限跳 |
| `test_timeout_goes_fallback` | 超时不消耗重试次数,直接降级 |
| `test_parallel_any_fail` | 任一分支失败 → 整体失败,后续连坐跳过 |
| `test_on_fail_skip` | `on_fail: skip` 时该步 SKIPPED 但主流程继续 |
| `test_task_deadline` | 任务级 deadline 触发后 `result=TIMEOUT` |
| `test_abort` | 外部中止 → `result=ABORTED` |

## 6. 已知限制

1. **节点进度不对外暴露**:长任务(Mock 里不存在)如果需要"执行到 40%"这种中间进度,
   当前只靠日志。真实节点接入时需要在 `TaskContext.shared` 里写进度字段,
   由上层订阅。
2. **`!include` 不支持**:位姿只能内联或从上下文取,详见 `docs/dsl.md` §9。
3. **取消依赖节点配合**:卡在第三方阻塞调用里的节点收不到取消,只能等它返回。
4. **并发失败不回收**:`any_fail` 策略下会 `cancel()` 其余分支,但已经在执行的
   线程要等它自己检查标志位退出,不是立刻消失。
