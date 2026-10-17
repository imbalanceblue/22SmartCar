# M0-4 ｜ TaskScheduler 任务调度模拟器

命令行任务调度模拟器：读取 YAML/JSON 任务配置，按依赖关系（拓扑序）执行任务，失败自动重试、下游连坐跳过、全局超时中断，并生成 JSON 运行报告。不依赖 ROS 或 GUI。

## 运行环境

- Ubuntu 22.04 / Python 3.10
- conda 虚拟环境 `car_env`（复用 M0-1 建好的环境）
- 依赖仅一个：`pip install -r requirements.txt`（PyYAML>=6.0）

## 参数说明

```bash
python scheduler.py --config <path> [--timeout <float>] [--report <path>] [--seed <int>]
```

| 参数 | 必填 | 说明 |
| :-- | :---: | :-- |
| `--config` | 是 | 配置文件路径，支持 `.yaml` / `.yml` / `.json` |
| `--timeout` | 否 | 覆盖配置文件中的 `timeout`（秒）。显式给出时优先生效 |
| `--report` | 否 | 报告输出路径，默认 `report.json` |
| `--seed` | 否 | 随机种子。给定后同配置多次运行的任务判定结果完全一致，便于复现与验收 |

## 运行示例

### 示例 1：正常调度

```bash
python scheduler.py --config tasks_demo.yaml --timeout 15 --report report.json --seed 42
```

7 个任务按拓扑序执行：`Init → Navigate → DetectQR / AvoidObstacle（并行）→ Grasp → Place（汇合）→ ReturnHome`。
控制台输出彩色状态（SUCCESS 绿 / FAILED 红 / RETRY 黄 / SKIPPED 蓝 / TIMEOUT 紫），结束后生成 `report.json`。

### 示例 2：紧超时触发中断与连坐

```bash
python scheduler.py --config tasks_demo.yaml --timeout 2 --report r2.json --seed 42
```

总耗时上限 2 秒：`Init` 正常完成；`Navigate`（2.5s）睡眠期间总耗时过线被标 `TIMEOUT`；其下游 `DetectQR`、`Grasp`、`Place`、`ReturnHome` 全部 `SKIPPED`。

### 示例 3：依赖成环直接报错

```bash
python scheduler.py --config bad/cycle.yaml
# 错误: 依赖成环，无法调度，涉及任务: A, B   （退出码 1）
```

## 异常输入处理

以下 6 类坏配置均以一句可读错误 + 退出码 1 结束，**不抛栈回溯**（测试文件在 `bad/` 目录）：

| 坏输入 | 行为 |
| :-- | :-- |
| 配置文件不存在 | `错误: 配置文件不存在: xxx` |
| YAML/JSON 语法错误 | 解析错误提示 |
| 依赖成环 | `依赖成环，无法调度，涉及任务: ...` |
| 依赖不存在的任务 | `任务 X 依赖不存在的任务: Y` |
| 缺少 duration / success_rate | `任务 X 缺少 duration` |
| `success_rate` 越界（如 1.5） | `success_rate 越界: 1.5（应为 0~1）` |

## report.json 说明

```json
{
  "timeout_occurred": false,
  "total_duration": 11.08,
  "tasks": [
    { "name": "Init", "status": "SUCCESS", "attempts": 1 }
  ]
}
```

- `status` 取值仅四种：`SUCCESS` / `FAILED` / `SKIPPED` / `TIMEOUT`
- `tasks` 包含配置中的**每一个**任务（含被连坐跳过、超时未执行的）
- `attempts` 为实际尝试次数，不超过 3（失败最多重试 2 次）
- `total_duration` 为真实墙钟时间；`--seed` 只固定随机判定序列，不固定耗时

## 设计权衡

1. **执行前 + 每次 sleep 后各查一次超时**：只查一次的话，最后一个长任务会"睡过上限"才被发现。`--timeout 2` 示例中 Navigate 正是在睡眠期间被中断的。
2. **SKIPPED 判定用 `any(前置 != SUCCESS)` 而非枚举失败态**：前置可能是 SKIPPED/FAILED/TIMEOUT 三种"没成功"，反向条件一条覆盖所有情况。无前置任务（空 dependencies）经 `any(空序列)=False` 天然放行，无需特判。
3. **环检测用 Kahn 剥洋葱 + 计数（pending）而非布尔标记**：`Place` 有两个前置，必须计数"还欠几个"；排序不完即有环，执行前就报错退出——错得早比错得晚便宜。
4. **`--seed` 值得支持**：固定随机判定序列，验收时"同命令同结果"，答辩演示可复现。
5. **requirements.txt 手写 `PyYAML>=6.0` 而非 `pip freeze`**：freeze 会把环境（含 ROS 相关包）全部倒出；依赖清单应描述"项目需要什么"，不是"环境里有什么"。
6. **彩色日志区分过程与结果**：只有四种结果状态上色，RUN 保持素色——颜色用来突出"发生了什么结果"。
7. **非 TTY 降级**：`sys.stdout.isatty()` 判断，重定向到文件时不输出 ANSI 转义符，避免日志文件出现乱码。

## AI 使用说明

本次任务使用了 AI（WorkBuddy）辅助开发，使用方式与范围如下，全部如实披露：

1. **代码结构与思路**：`scheduler.py` 的函数划分（load_config / topo_order / run）、拓扑排序的 Kahn 算法框架、主循环"连坐→查超时→重试"的顺序，由 AI 给出骨架与伪代码；字段校验的具体实现（5 处 TODO）由本人在理解后自行补全，并逐条用坏配置测试验证。
2. **本人的独立工作**：纸上推演依赖图与拓扑顺序、发现"字典缺字段该不该报错"并主动追问、确认 Init 无前置时 any(空序列) 的行为、全部测试文件与异常用例的创建、全部 git 提交与 scp 操作、以及定位"RUN 日志误写为 TIMEOUT"的调试过程。
3. **逐行验证**：所有代码均在本地逐行理解后运行验证，验收命令（正常调度 / 紧超时 / 环报错）全部由本人亲手执行并核对输出，未直接采用未经测试的代码。
4. **README**：初稿由 AI 根据实际运行结果生成，本人逐节核对示例命令与输出后采用。
5. **未使用的部分**：AI 未代填本声明之外的任何"如实披露"内容；代码未抄袭任何他人仓库。

答辩时可以针对以上任何一条现场演示或逐行讲解。

## 已知局限

- 任务执行用 `sleep(duration)` 模拟，不做真实并行（依赖分叉只是逻辑分叉，顺序执行总耗时为各任务之和）
- `ready.pop(0)` 为 O(n)，任务数大时可换 `collections.deque.popleft()` 优化为 O(1)（当前 7 任务规模无影响）
