# M5-4 练习/比赛场次复盘与实验管理工具箱

> 场场跑、场场看、场场改:30 秒看懂一把、一条命令做实验、半小时出基线包。
> 全部工具跑在**主机**上,不占小车算力。纯 Python 标准库实现(YAML 读写可选 pyyaml,无则自动 JSON 孪生)。

## 一、三个工具

### 1. `scripts/quick_review.py` — 单次运行复盘(30 秒出结果)

```bash
python3 scripts/quick_review.py --run-dir runs/run_004 --prev runs/run_003
```

- 输入一个 run 目录:`task_report.json`(M5-1/M5-3 状态机报告)+ `blackbox.jsonl`(黑匣子)+ `switch_log.csv`(M5-3 热切日志)
- 输出:终端彩色摘要 + `review_<时间戳>.md`(可直接贴群)+ `summary.json`(结构化,供对比)
- 内容:任务结果/总耗时/各子任务耗时与重试降级、关键指标表(感知频率/置信度/CPU 峰值/电量等)、**故障根因一句话**(如 `camera_degraded@20:48:17 (freq<10Hz) -> nav fallback to lidar_only (+9.2s)`)、**与上一把对比**(指标增减、新增/消失故障、参数变更)

### 2. `scripts/exp.py` — 参数/模型实验管理器

```bash
python3 scripts/exp.py new     --name nav_speed_0.9 --base scene_race.yaml \
               --set nav_speed=0.9 --desc "提高直线速度测试"
python3 scripts/exp.py run     --name nav_speed_0.9 --runs 3
python3 scripts/exp.py compare --names nav_speed_0.8,nav_speed_0.9,nav_speed_1.0 \
               --metric nav_time,success_rate,cpu_peak
python3 scripts/exp.py promote --name nav_speed_0.9 --to race
```

- `new`:只存差异值(`config/scene_<name>.yaml`),元数据进 `config/experiments/registry.json`(基线/修改项/目的)
- `run`:热加载参数(写 switch_log,含真实 ros2 命令)→ 跑 N 把 → 每把自动过 quick_review → 汇总均值/方差/成功率
- `compare`:Markdown 表格 + 无 CDN 的 Canvas 交互图(`config/experiments/compare_latest.html`,现场断网可看)
- `promote`:一键把最优实验写回基线场景,并生成含实验摘要的 git 提交(无 git 环境降级为打印命令)

### 3. `scripts/gen_baseline.py` — 最优基线包生成器

```bash
python3 scripts/gen_baseline.py --exp nav_speed_0.9
```

产出 `dist/baseline_package_<日期>.tar.gz`:锁参数的 `baseline.yaml` + 模型文件(sha256 锁定)+ `verify_baseline.sh` + README。异机验证:解压 → `./verify_baseline.sh` → `PASS`。

## 二、目录结构（与官方交付树一致）

```
M5-4/
├── scripts/
│   ├── quick_review.py   # ★ 单次复盘（< 30s，实测 0.19s）
│   ├── exp.py            # ★ 实验管理 new/run/compare/promote
│   ├── gen_baseline.py   # ★ 基线包生成
│   ├── parse_mcap.py     # ★ 关键话题提取（被 quick_review 复用）
│   └── make_demo_data.py # 生成确定性练习日数据
├── config/
│   ├── scene_race.yaml   # 当前基线（+ JSON 孪生）
│   ├── scene_*.json      # 各实验完整场景
│   └── experiments/      # registry.json + compare_latest.{md,html} + 差异配置
├── data/                 # 示例小样本（sample_run/，可复现）
├── outputs/              # 复盘报告 / 对比图导出（运行产物）
├── runs/                 # 练习日 run_001..005 + 实验运行产物（不入库）
├── models/               # 3 个模型版本（占位文件，哈希锁定）
├── docs/
│   ├── review_fields.md  # 摘要各字段定义 + 时间对齐
│   └── workflow.md       # 从跑完到 promote 的完整流程
├── dist/                 # 生成的基线包（运行产物，不入库）
└── tests/                # 9 个用例
```

`scripts/make_demo_data.py` 一键生成确定性"练习日数据"：5 把 blackbox（第 4 把相机故障走降级）、3 套实验参数、2 个模型版本。

## 三、设计决策(答辩要点)

1. **只解析关键数据,不读全量日志** — 30 秒约束来自数据面裁剪:任务结果读 task_report.json、指标只取黑匣子里的 metric/fault/estop 事件,复杂图留给 exp compare。
2. **summary.json 作为复盘的结构化锚点** — "与上一把对比"和 exp 的跨实验对比都读同一份 summary,避免解析 Markdown。
3. **实验只存差异值** — config/scene_<exp>.yaml 完整继承基线、只覆盖改动项,promote 时合并回基线,保证"一条命令切换"且不产生配置分叉。
4. **确定性仿真替代真车** — 无真车环境用 seeded 随机模拟(同参同结果),但热加载留痕、复盘链路、对比统计与真车完全一致;换真车只替换 simulate_run。
5. **基线包三重验证** — 参数结构完整、关键参数在阈值内、模型 sha256 一致,任一不过即 FAIL,防"拷错/拷坏"。
6. **零第三方依赖** — Plotly 交互图换成手写 Canvas(功能等价:多序列折线+图例),现场无网可演示。

## 四、已知限制(如实披露)

1. 真车黑匣子是 rosbag2 `.mcap`;本环境用同构 JSONL(M5-3 car_side 链路)。
   读取分支已抽到 `scripts/parse_mcap.py`,装 `mcap` 库即可直接读真 bag,上层分析不用改。
2. `run` 阶段的"真车跑一把"以确定性仿真代替(见设计决策 4)。
3. 模型文件为占位文件(哈希锁定机制可用),真实部署替换为导出的 best.onnx。
4. M5-1 调度内核已从 241 行压缩至 196 行(仅压注释/空行/合并赋值,逻辑零改动),满足题面 200 行要求。

## 五、快速验收

```bash
python3 scripts/quick_review.py --run-dir runs/run_004 --prev runs/run_003   # 故障根因+对比
python3 scripts/exp.py compare --names nav_speed_0.8,nav_speed_0.9,nav_speed_1.0 \
               --metric nav_time,success_rate,cpu_peak                        # 三实验对比
python3 scripts/gen_baseline.py --exp nav_speed_0.9                          # 基线包
python3 -m pytest tests/ -v                                                  # 9 用例(VM 中加
                                                                             # PYTEST_DISABLE_PLUGIN_AUTOLOAD=1)
```

## 六、AI 使用声明(如实)

- 需求理解与演示流程设计:本人与 AI 讨论确定;
- 代码实现:AI 生成,本人逐文件核对并在 VM 中实际运行验证(单测/复盘/实验/基线包);
- 过程中 AI 修复了本人确认的缺陷(如仿真成功逻辑反了、verify 对缺失关键参数的拦截);
- 后续答辩由本人现场讲解与演示。
