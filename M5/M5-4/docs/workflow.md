# M5-4 完整工作流:从跑完一把到 promote 成基线

## 0. 全链路

```
一次比赛/练习
   ├─ blackbox.jsonl / .mcap      ← 关键话题
   ├─ task_report.json            ← M5-1 产物
   └─ switch_log.csv              ← M5-3 产物
              │
              ▼
    quick_review.py  ── 30 秒 ──▶ summary.json + review_<ts>.md
              │
              ▼ (攒够几把)
    exp.py compare  ──▶ compare_latest.md / .html
              │
              ▼ 选最优
    exp.py promote  ──▶ 覆盖 scene_race.yaml + git commit
              │
              ▼
    gen_baseline.py ──▶ baseline_package_<date>.tar.gz
              │
              ▼
    异机解压 → ./verify_baseline.sh → PASS
```

## 1. 单次复盘（现场掐表这条）

```bash
time python scripts/quick_review.py --mcap data/run_01.mcap \
     --logs data/logs/ --switch-log data/switch_log.csv
```

**30 秒怎么保证**：

1. 只读白名单话题（`scripts/parse_mcap.py` 的 `TOPIC_WHITELIST`），
   不把整个 bag 解成帧；
2. 指标用一次遍历的滑动窗口，不落中间文件；
3. 不画图（图在 `compare` 阶段生成），终端只输出文本摘要。

**"自动找上一把"**：不传 `--prev` 时，脚本在同级目录里按名字排序找
最近一个带 `summary.json` 的目录，不要求人工指定。

## 2. 实验四步

```bash
# ① 只记差异
python scripts/exp.py new --name nav_speed_0.9 --base scene_race.yaml \
       --set nav_speed=0.9 --desc "提高直线速度测试"

# ② 跑 N 把(每把自动复盘)
python scripts/exp.py run --name nav_speed_0.9 --scene nav_speed_0.9 --runs 3

# ③ 多实验对比
python scripts/exp.py compare --names "nav_speed_0.8,nav_speed_0.9,nav_speed_1.0" \
       --metric nav_time,success_rate,cpu_peak

# ④ 推为新基线
python scripts/exp.py promote --name nav_speed_0.9 --to scene_race.yaml
```

### 2.1 「只记差异」怎么合回完整配置

`new` 生成的 `config/experiments/<name>.json` 只存
`{"base": "scene_race.yaml", "diff": {"nav_speed": 0.9}, ...}`。
运行时由 `merge()` **深合并**：先读基线，再用 `diff` 覆盖同名字段，
嵌套字典逐层覆盖，列表整体替换。

好处：改了哪个参数一眼可见；基线一变，所有实验自动跟着变。

### 2.2 `run` 怎么保证环境一致

每把开始前记录 `git rev-parse HEAD` 与模型文件的 sha256，
写进 `registry.json` 的该次运行记录。如果两把之间 commit 或模型变了，
`compare` 会在表格下方标一行警告 —— **不阻止对比，但让人知道口径变了**。

### 2.3 `compare` 的成功率分母

`success_rate = 成功把数 / 该实验的总把数`（`runs` 参数）。
不是"成功步数 / 总步数"（那是导航成功率，另一回事）。

### 2.4 `promote` 的 git 安全（重点）

题面明确禁止"把无关改动一起 commit"。实现三条：

1. `promote` 前先 `git status --porcelain`；
2. **工作区有未提交改动 → 直接拒绝**，提示先 stash/commit，不替你决定；
3. 只 `git add` 目标场景文件 + `registry.json`，然后
   `git commit -m "promote: <name> (<摘要>)"`，绝不 `git add -A`。

这样即使有人手工绕过，`git log --stat` 也能看出只动了两个文件。

## 3. 基线包与异机验证

```bash
python scripts/gen_baseline.py --exp nav_speed_0.9 \
       --out dist/baseline_package_$(date +%Y%m%d).tar.gz
```

包内四件：

| 文件 | 作用 |
| :-- | :-- |
| `baseline.yaml` | 锁定全部关键参数（不是差异） |
| `baseline.json` | 同内容的 JSON 副本，给没装 pyyaml 的机器 |
| `models/` | 最优模型文件 |
| `verify_baseline.sh` | 带明确阈值的自验脚本 |

**验证什么**（必须可判定）：

1. 结构完整：每个关键参数都在，没有缺项；
2. 参数在阈值内：`min <= v <= max`，不是"看起来正常"；
3. 模型哈希一致：sha256 与锁定值逐字节比对。

**为什么同时放 json**：异机可能没装 pyyaml。
`verify_baseline.sh` 优先读 `baseline.json`（标准库），
不存在才退回 `baseline.yaml`。这条是踩过坑之后加的 ——
在有 pyyaml 的机器上生成、在没 pyyaml 的机器上验证，原本会直接崩。

**缺依赖时给什么提示**：脚本 `set -u` 但不 `set -e`，
先在输出里打印"缺少 xxx，请 pip install -r requirements.txt"，
再 `exit 1`，不抛 Python traceback。

**包多大**：模型 3 个共约 12MB（`yolov8n` 级别），
压缩后约 11MB，可接受；不含 `runs/` 原始数据。

## 4. 目录约定

| 目录 | 内容 | 是否入库 |
| :-- | :-- | :-- |
| `config/scene_*.yaml` | 场景基线 | ✅ |
| `config/experiments/` | 实验差异配置 + `registry.json` | ✅ |
| `data/` | 示例小样本（可复现） | ✅ |
| `runs/<exp>_<i>/` | 每次运行产物 | ❌ gitignore |
| `outputs/` | 复盘报告、对比图 | ❌ gitignore |
| `dist/` | 基线包 | ❌ gitignore |
