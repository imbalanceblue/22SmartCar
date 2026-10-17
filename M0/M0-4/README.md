# M0-4 ｜ Mini 项目实战:TaskScheduler

> ⚠️ **占位文件**。本题完成后,请把本文件替换为正式 README(参数说明 + ≥ 3 个运行示例)。
> 难度 ★★★★ ｜ 权重 40 ｜ 交付物:`scheduler.py` + `requirements.txt` + 本 README + `report.json`

---

## 待办清单

### 1. 配置加载

- [ ] 读取 `.yaml` / `.yml` / `.json`,兼容两种格式
- [ ] 字段校验:`name` 唯一、`duration` ≥ 0、`success_rate` ∈ [0,1]、`dependencies` 为列表

### 2. 核心调度逻辑

- [ ] 拓扑排序执行,正确处理**分叉 / 汇合**
- [ ] 检测**依赖环**并报错
- [ ] 按 `success_rate` 随机判定成功 / 失败
- [ ] 失败最多重试 2 次(共 3 次尝试);仍失败 → `SKIPPED`
- [ ] 前置未成功时,下游任务也标记为 `SKIPPED`(**规则要先想透**)
- [ ] 时间会计:每任务 `sleep(duration)` 并累加
- [ ] 超时判定:**执行前查一次 + 每次 sleep 后再查一次**,超时立即终止并输出 `TIMEOUT`

### 3. 日志与输出

- [ ] 控制台彩色:`SUCCESS` 绿 / `FAILED` 红 / `RETRY` 黄 / `SKIPPED` 蓝
- [ ] **非 TTY 环境自动降级**(重定向到文件时不出现乱码转义符)
- [ ] 生成 `report.json`:`timeout` / `total_duration` / `tasks[]`
- [ ] `tasks` 条目字段:`name` / `status` / `attempts` / `duration` / `started_at` / `ended_at`
- [ ] `tasks` **必须包含配置里的每一个任务**(含被跳过的、超时未执行的)
- [ ] `status` 只能取 `SUCCESS` / `FAILED` / `SKIPPED` / `TIMEOUT`;`attempts` ≤ 3

### 4. 工程规范

- [ ] 使用虚拟环境(uv 或 conda)
- [ ] 提供 `requirements.txt`
- [ ] README 含**参数说明** + **≥ 3 个运行示例**

### 5. 接口契约(现场按此验收)

```bash
python3 scheduler.py --config tasks.yaml --timeout 15 --report report.json --seed 42
```

- [ ] `--config` 必填,支持 `.yaml` / `.yml` / `.json`
- [ ] `--timeout` 覆盖配置文件中的 `timeout`
- [ ] `--report` 默认 `report.json`
- [ ] `--seed` 支持复现

### 6. 异常输入清单(考官会喂)

| 输入 | 期望行为 |
| :-- | :-- |
| 依赖成环 | 可读错误 + 非 0 退出 |
| 依赖不存在的任务 | 可读错误 + 非 0 退出 |
| 空任务列表 | 可读错误或空报告,不崩 |
| `success_rate` 越界(如 1.5) | 可读错误 + 非 0 退出 |
| YAML / JSON 语法错误 | 可读错误,指出大致位置 |
| 文件不存在 | 可读错误 + 非 0 退出 |

---

## 示例配置的依赖图(备查)

```
Init
 └─ Navigate ─┬─ DetectQR ── Grasp ──┐
              └─ AvoidObstacle ──────┴─ Place ── ReturnHome
```

① `Navigate` 分叉成两条链;② `Place` 依赖 `Grasp` + `AvoidObstacle` 两个前置(汇合点)。
