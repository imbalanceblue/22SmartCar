# YAML 编排 DSL 说明

> 考官只需看这一份就能写出测试 YAML。所有字段、默认值、缺字段的行为都在这里。

## 1. 文件结构

顶层只有三个键:

| 键 | 类型 | 必填 | 默认 | 说明 |
| :-- | :-- | :-- | :-- | :-- |
| `tasks` | list | **是** | — | 任务序列,按顺序执行 |
| `mission_id` | str | 否 | `mission` | 任务 ID,写进报告的 `task_id` |
| `deadline` | float | 否 | 无 | 任务级截止时间(秒)。超时 → `result=TIMEOUT` |

```yaml
mission_id: my_mission
deadline: 60
tasks:
  - name: ...
```

`deadline` 与节点的 `timeout` 不是一回事:
- `deadline` 是**整个任务**的截止时间,到点后剩下的节点全部不执行
- `timeout` 是**单个节点**的超时,超时按失败处理(可重试/降级)

## 2. 单个任务的字段

| 字段 | 类型 | 必填 | 默认 | 作用 |
| :-- | :-- | :-- | :-- | :-- |
| `name` | str | **是** | — | 节点名,**全场唯一** |
| `type` | str | 条件 | — | 节点类型,见 §3。用 `parallel` 写法时可省略 |
| `timeout` | float | 否 | 不限 | 单次执行超时(秒) |
| `retry` | int | 否 | `0` | 失败后重试次数(总共执行 `retry + 1` 次) |
| `retry_interval` | float | 否 | `0.5` | 每次重试前的等待(秒) |
| `fallback` | str | 否 | 无 | 失败后降级到哪个节点(填节点 `name`) |
| `on_fail` | str | 否 | `abort` | `abort` / `skip`,见 §4 |
| `parallel` | dict | 条件 | — | 并发分支配置,见 §5 |
| `fail_policy` | str | 否 | `any_fail` | 与 `parallel` / `type: Parallel` 搭配 |
| `branches` | list | 条件 | — | `Parallel` **必填**,子任务列表 |
| `duration` | float | 否 | `0.0` | **mock 专用**:模拟执行耗时(秒) |
| `fail_times` | int | 否 | `0` | **mock 专用**:前 N 次执行故意失败 |

节点专属字段:

| 字段 | 适用于 | 默认 | 说明 |
| :-- | :-- | :-- | :-- |
| `pose` | `NavigateTo` | 无 | 固定目标位姿,`[x, y, yaw]` |
| `pose_from_context` | `NavigateTo` | 无 | 从上下文读位姿的键名,如 `target_pose` |
| `target_class` | `DetectTarget` | `qrcode` | 目标类别,写入 `context.target_id` |
| `min_confidence` | `DetectTarget` | `0.7` | 置信度阈值 |
| `grasp_type` | `ExecuteGrasp` | `top_down` | 抓取方式 |
| `text` | `SpeakTTS` | `任务完成` | 播报文本 |

`pose` 与 `pose_from_context` 二选一;两个都不给时 `NavigateTo` 视为无目标,
执行会失败(`FAILED`)。

## 3. 节点类型(`type` 取值)

| type | 行为 | 产出到上下文 |
| :-- | :-- | :-- |
| `WaitForTrigger` | 等发车口令(真实版接 `/referee/start_cmd`) | `start_time` |
| `NavigateTo` | 导航到目标位姿 | `current_pose`、`last_nav_result` |
| `DetectTarget` | 识别目标并等待置信度达标 | `target_id`、`target_pose`、`confidence` |
| `ExecuteGrasp` | 抓取(输入 `grasp_pose`) | `grasp_result`、`object_pose` |
| `SpeakTTS` | 语音播报 | — |
| `Parallel` | 并发分支 | — |

写错 `type` 会直接报错并列出全部可用类型:

```
[ERROR] 配置错误: 未知节点类型: NoSuchNode(可选: ['DetectTarget', 'ExecuteGrasp', 'NavigateTo', 'Parallel', 'SpeakTTS', 'WaitForTrigger'])
```

## 4. `fallback` 与 `on_fail` 的语义

这是最容易搞混的地方,分两种情况。

**情况 A:配了 `fallback`**

```
主节点失败 → 执行 fallback 节点
             ├─ fallback 成功 → 主节点标 SKIPPED,整体继续往下走
             └─ fallback 失败 → 主节点标 FAILED,后续节点全部 SKIPPED
```

- 主节点最终状态记 **`SKIPPED`**(不是 FAILED),因为"降级后的结果"就是 SKIPPED
- 报告里该步 `degraded=true`,并记录 `fallback_used`
- **fallback 只跳一层,不递归**。a→b、b→a 这种配置不会无限跳:跑到 b 失败就停,整体判 FAILED

**情况 B:没配 `fallback`,只看 `on_fail`**

| `on_fail` | 失败/超时后 | 该步状态 | 后续节点 | 整体 `result` |
| :-- | :-- | :-- | :-- | :-- |
| `abort`(默认) | 停止整个任务 | `FAILED` | 全部 `SKIPPED` | `FAILED` |
| `skip` | 跳过这一步继续 | `SKIPPED` | 照常执行 | 可能仍为 `SUCCESS` |

**超时的特殊处理**:步骤级 `timeout` 触发时**不消耗重试次数**,直接进入
fallback / `on_fail` 分支。报告里该步 `status` 记 `TIMEOUT`(与普通失败区分开)。

## 5. 并发分支 `parallel`

两种写法等价,任选:

```yaml
# 写法一:parallel 子键(无 type)
- name: precheck
  parallel:
    fail_policy: any_fail
    branches:
      - name: check_battery
        type: SpeakTTS
        text: "检查电量"
      - name: check_lidar
        type: SpeakTTS
        text: "检查雷达"

# 写法二:type: Parallel
- name: precheck
  type: Parallel
  fail_policy: any_fail
  branches:
    - name: check_battery
      type: SpeakTTS
      text: "检查电量"
```

`fail_policy` 两个取值:

| 取值 | 行为 |
| :-- | :-- |
| `any_fail`(默认) | 任一分支失败 → 立即取消其余分支,整体判 `FAILED` |
| `all_must_succeed` | 等所有分支自然结束,全部成功才 `SUCCESS` |

分支内可以嵌套任意节点,包括 `fallback`、`retry`、`timeout`,与顶层规则相同。
**分支里不能再嵌套 `Parallel` 之外的分支配置问题**——嵌套 `Parallel` 是允许的。

## 6. 必填字段缺失会怎样

所有配置错误都在**启动时**报出,不会跑到一半才崩。输出是一行人话,退出码 1,没有 traceback。

| 缺什么 | 报错信息 |
| :-- | :-- |
| 顶层没有 `tasks` | `配置错误: 配置缺少 tasks 列表` |
| `tasks` 不是列表 | `配置错误: tasks 必须是列表` |
| 任务缺 `name` | `配置错误: 任务缺少 name 字段` |
| 缺 `type` 且没有 `parallel` | `配置错误: 未知节点类型: None(可选: [...])` |
| `Parallel` 缺 `branches` | `配置错误: Parallel 节点 xxx 缺少 branches 列表` |
| `name` 重复 | `配置错误: 节点名重复: [...]` |
| `fallback` 指向不存在的名字 | `配置错误: xxx 的 fallback 指向不存在的节点: yyy` |
| YAML 语法错误 | `配置错误: YAML 解析失败: ... (line N, column M)` |
| 文件不存在 | `文件不存在: xxx.yaml` |

## 7. 执行结果与退出码

`task_report.json` 的 `result` 取值:

| 值 | 含义 |
| :-- | :-- |
| `SUCCESS` | 全部步骤成功(或降级后继续跑完) |
| `FAILED` | 有步骤最终失败且未能降级 |
| `TIMEOUT` | 任务级 `deadline` 到了,被强制结束 |
| `ABORTED` | 被外部中断(急停 / 裁判 STOP) |

退出码:`0` = `SUCCESS`;非 0 = 其他三种。

## 8. 完整示例(可直接跑)

`tasks/task_demo.yaml` 原样贴出,`python run_task.py tasks/task_demo.yaml` 即可运行:

```yaml
mission_id: grab_and_return
deadline: null
tasks:
  - name: wait_start
    type: WaitForTrigger
    timeout: 30
    duration: 0.1

  - name: navigate_to_scan
    type: NavigateTo
    pose: [0.0, 0.0, 0.0]
    duration: 0.2
    retry: 2
    retry_interval: 0.1
    fallback: wait_start

  - name: detect_qr
    type: DetectTarget
    target_class: "qrcode"
    min_confidence: 0.7
    timeout: 15
    retry: 2
    fallback: navigate_to_scan

  - name: cruise_and_avoid
    type: Parallel
    timeout: 10
    branches:
      - name: follow_line
        type: NavigateTo
        pose: [2.0, 1.0, 0.0]
        duration: 0.3
      - name: avoid_obstacle
        type: DetectTarget
        target_class: "obstacle"
        min_confidence: 0.5
        duration: 0.5

  - name: navigate_to_grasp
    type: NavigateTo
    pose_from_context: target_pose
    retry: 1
    duration: 0.2

  - name: grasp
    type: ExecuteGrasp
    grasp_type: "top_down"
    retry: 2
    fallback: navigate_to_grasp
    duration: 0.2

  - name: speak_done
    type: SpeakTTS
    text: "任务完成"
    duration: 0.1
```

## 9. 位姿来源

**当前版本不支持 `!include`**(那是 YAML 自定义构造器,会让考官写的 YAML 解析失败)。
位姿的两种给法:

```yaml
pose: [2.0, 0.5, 0.0]              # 直接内联
pose_from_context: target_pose     # 用上游节点写进上下文的值
```

`tasks/poses/` 目录当前只放说明,未启用外部位姿文件。

## 10. 已知限制

- `!include` 不支持,原因见 §9
- `parallel` 分支内暂不支持"失败后继续其他分支"以外的策略(只有 `fail_policy` 两种)
- `duration` / `fail_times` 只对 Mock 节点有效,接真实 ROS2 节点时无效
- 配置文件支持 `.yaml` 和 `.json` 两种后缀。环境没装 pyyaml 时,用 `.json` 也能跑
  (`tasks/task_demo.json` 是 `task_demo.yaml` 的等价副本)
