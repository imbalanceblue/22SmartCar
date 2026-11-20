# M5-3 审计日志格式与哈希链算法

实现文件：`host_side/audit.py`　校验工具：`tools/verify_audit.py`

## 1. 文件形态

`audit_log_<date>.jsonl`，**一行一条 JSON**，只追加不修改。

```
M5-3/host_side/audit_log_2026-11-20.jsonl
```

用 `.jsonl` 而不是 `.json` 的原因：追加时不需要读改写整个文件，
崩溃时最多丢最后一行，不会把历史全弄坏。

## 2. 一行长什么样

```json
{"seq":2,"ts":1763630401000,"ts_iso":"2026-11-20T15:40:01.000+08:00",
 "op":"EMERGENCY_STOP","params":{"zero_velocity":true,"cmd_vel":[0.0,0.0],
 "reason":"test"},"source":"operator","operator":"operator","result":"ok",
 "prev_hash":"9f2c...","hash":"a41d..."}
```

| 字段 | 含义 | 说明 |
| :-- | :-- | :-- |
| `seq` | 序号 | 从 1 递增，也是"第几条" |
| `ts` | 毫秒时间戳 | 排序用 |
| `ts_iso` | 本地时区 ISO 串 | 人读用 |
| `op` | 操作类型 | `EMERGENCY_STOP` / `MODEL_SWITCH` / `PARAM_SET` / `WATCHDOG_DEGRADE` / `SKIP_NODE` / `PENALTY` … |
| `params` | 操作参数 | 切换类操作含 `from` / `to` |
| `source` | 来源 | `operator` / `referee` / `voice` / `keyboard` / `watchdog` / `system` |
| `operator` | 操作人 | 单机场景固定为 `operator`（见 §5） |
| `result` | 结果 | `ok` / `fail` |
| `prev_hash` | 前一条的 hash | 链的指针 |
| `hash` | 本条 hash | sha256(规范化 JSON) |
| `note` | 备注 | 可选 |

**覆盖自动触发的事件**：看门狗降级（`WATCHDOG_DEGRADE`）、
裁判判罚（`PENALTY`）也走同一个 `append()`，
不是只记录"人点了按钮"。

## 3. 哈希链怎么算

```
GENESIS = "0" × 64                       # 第一条的 prev_hash
canonical = json.dumps(entry, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False)
hash = sha256(canonical.encode("utf-8")).hexdigest()
```

三个细节都是有意的：

1. **`sort_keys=True`** —— 字典顺序不稳定，不排序会导致同一份数据算出不同 hash；
2. **`separators=(",", ":")`** —— 去掉空格，避免"空格变了 hash 就变"的假报警；
3. **先去掉 `hash` 字段再算** —— 否则自己证明自己，永远算不出来。

`params` 里嵌套的字典同样被 `sort_keys` 规范化，所以嵌套层级不影响可复现性。

## 4. 校验能发现什么

`tools/verify_audit.py` 从磁盘重读（**不信任内存**），逐行：

| 篡改方式 | 能否发现 | 原因 |
| :-- | :-- | :-- |
| 改某行的 `op` 或 `params` | ✅ | 该行重算 hash 与记录值不符 |
| 改某行的 `prev_hash` | ✅ | 与上一行实际 hash 不符 |
| 删掉中间一行 | ✅ | 下一行的 `prev_hash` 指向不存在的那条 |
| 删掉最后一行 | ❌ | 链尾没有"后一行"来指认它 |
| 整份替换成另一份合法日志 | ❌ | 只验内部一致性，不验来源 |

最后两条是**如实说明的局限**。要防它需要外部锚点：
把最新的 `(seq, hash)` 定期写到别处（如 git commit、纸质记录），
本实现没做，因为题面只要求"篡改任一行会导致后续校验失败"。

## 5. 关于 `operator`

现场只有一台主机，事实上无法区分人。这里固定写 `operator`，
**不假装有多用户**。如果将来接键盘/卡片登录，
把 `AuditLog(operator=...)` 传进去即可，格式不用改。

## 6. 重启后能否续链

`AuditLog.__init__` 会读最后一行，恢复 `seq` 与 `prev_hash`，
所以重启后继续追加仍然是同一条链，不会出现两条 GENESIS 起头的链。
