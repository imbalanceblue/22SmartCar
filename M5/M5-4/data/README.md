# 示例数据（可复现）

`sample_run/` 是从 `runs/run_005/`（练习日最优那把）复制的最小样本，
用于在没有真车数据时验证 `quick_review.py` 能跑通。

| 文件 | 来源 | 说明 |
| :-- | :-- | :-- |
| `task_report.json` | M5-1 状态机 | 每步状态/重试/降级 |
| `blackbox.jsonl` | 黑匣子（JSONL 模拟 mcap） | 关键话题与事件 |
| `switch_log.csv` | M5-3 终端 | 参数/模型切换记录 |
| `summary.json` | 本工具产出 | 已复盘过的摘要 |

## 怎么用

```bash
python scripts/quick_review.py --run data/sample_run
```

## 重新生成整套练习数据

```bash
python scripts/make_demo_data.py      # 重置 runs/run_001..005 + 实验 + 模型
```

**说明**：真实场景下 `blackbox` 是 rosbag2 的 `.mcap`。
本环境用 JSONL 模拟同一份结构（字段一致），切换只需替换
`scripts/parse_mcap.py` 的读取分支，上层分析逻辑不用改。
