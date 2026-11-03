# V57 结果（§123）：候选漏斗 —— 30 提 / 18 做 / 4 拦 / 8 未轮到

> 冻结：2026-11-03 23:15。`logs.txt` + `runs.jsonl` 为原始产物，**本文件与它们冲突时以产物为准**。

## 1. 一句话

V56 那句「12 题里 6 题**只提了** `finish`」把「只**下发**了 finish」说成了「只**提**了」。
真实 12 格：**30 个候选进执行器，只下发 18 个**；`zero_step` 的 6 题由
**2（真的只想到 finish）+ 2（被 guard 拦）+ 2（finish 先成功、同轮没轮到）** 组成。

## 2. 读数（`runs.jsonl` 末行）

```json
"kernel_offer": {"tasks":12,"rounds":18,"offered":30,"produces_offered":12,
 "step_offered":18,"dispatched":18,"blocked":4,"unreached":8,
 "no_rounds":0,"only_delivery":2,"offered_not_done":4,
 "done_without_offer":0,"offered_and_done":6,
 "offered_not_done_blocked":2,"unaccounted":0}
```

- `offered 30 == dispatched 18 + blocked 4 + unreached 8`（残差 `unaccounted=0`）
- `kernel_exec` 与 V56 **逐字相同**：`tasks=12 / dispatched=18 / ok=18 / fail=0 /
  answer=12 / step=6 / zero_dispatch_tasks=0 / zero_step_tasks=6 / by_verdict={ANSWER:12}`
- `mean=0.8611`、`caliber_sig=542f9053d19a7090`、`catalog_sha=8decb60895d4a972`
- 耗时 **329s**，退出码 0

## 3. 预注册 11 条：全中

见 §123.4 的表。两个独立仪器（**上一轮冻结日志的手数** vs **本轮账本的计数**）
给出同一组整数 —— 这不是"同一个数抄两遍"，来源分别是
`v6_s146/logs_b.txt` 的 `[i] 候选=N` 行与本轮的 `plan.cands_total` / `exec` / `loop`。

唯一没押准的是**附带项**：PREREG R2 写 `n=12`，实测 `measurement.n=2`
（那是 rep 数，不是格数）。主读数 `mean=0.8611` 对得上，附带项写错照样登记。

## 4. 逐题（`logs.txt`，T07~T12 与 T01~T06 逐格相同 ⇒ T=0 重放）

| 题 | 轮 | 提 | 做 | 拦 | 未轮到 | `■` 行的 交付/步骤/未轮到 |
|---|---|---|---|---|---|---|
| T01 parse_duration | 2 | 3 | 2 | 0 | 1 | 1/1/1 |
| T02 dedupe | 1 | 1 | 1 | 0 | 0 | 1/0/0 |
| T03 commas | 1 | 3 | 1 | 0 | 2 | 1/0/2 |
| T04 merge_ranges | 2 | 3 | 2 | 0 | 1 | 1/1/1 |
| T05 top_k | 1 | 2 | 1 | 1 | 0 | 1/0/0 |
| T06 rle | 2 | 3 | 2 | 1 | 0 | 1/1/0 |
| **合计（×2）** | 18 | 30 | 18 | 4 | 8 | — |

⚠️ T05 的 `■` 行 `未轮到=0` 而"提 2 做 1 拦 1"：被拦的是 **第 1 个**候选
（`run_shell`），`finish` 在第 2 位成功 ⇒ `len(cands)-ci-1 = 0`。**这是对的** ——
"没轮到"数的是"排在成功候选**之后**的"，不是"没被执行的"。

## 5. 门禁（`run_tests.sh`，EXIT=0，2m53s）

契约 **624/0**、锚层 **97/0**、`check_counts` **22 处 0 漂移**、
生产记忆库 `359c7100c2272d14` 未变、`limits 字段 25`、`全部通过`。

定向撤回态 `AZ120`：`624/0` → `红=['AZ120'] 多红=无` → `624/0`（`R13 通过`）。
⛔ 全量 **205** 条未跑（≈2h）。

## 6. 自踩

1. ⭐ **一行日志里两个分母**：`■` 的 `guard_blocked=` 是 `Guard.blocks` 的**生命周期累计**
   （实测 T05→T12 = `1,2,2,2,2,2,3,4`），而旁边的 `执行=` 是**按题**的。
   漏斗里的 `blocked` 必须取 `loop.blocked_skipped`。
2. ⭐ **判据挂在装置到不了的格上**（第二次同型）：题面不够复杂 ⇒ `looks_complex`
   走快通道 ⇒ `n=1` ⇒ 三候选被 `[:1]` 截成一个，四格全 `(1,1,0,0)`。
   修法是把题面写复杂，不是放宽判据。
3. **两份键集当成一个**：⑩ 拿 `["unreached"]` 去比 `_koff_zero()`（14 键）⇒ 当场红。
4. **heredoc 里 `"\\n"` 变成字面反斜杠 n**：`<<'PY'` 不转义，Python 收到的是
   `"\\n"`（两个字符）⇒ 追加的 DESIGN 段前面多了一个字面 `\n`。已手工修掉。
   （与 V5x 那次"换行转义多一层"同型，第二次。）
