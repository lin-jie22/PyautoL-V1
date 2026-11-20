# V214（§282）结果：累积块**从未**进到外包调用里 —— 3/3 次 stdin 逐字等于题面

**判据行**：P1 ✅ / P2 ✅ / P3 ✅ / P4 ✅（四条全部成立，读数见 §2）

---

## 1. 装置（一次免费普查，零产品改动参与测量）

默认配置下 `code_agent` 吃掉 **65/77 = 84.4%** 的题次（§277）。但「内核写进 state 的
`[外部评审]` 块有没有跟着进那次调用」从来只是**代码推断**：`mw/tools.py:341` 交出去的
只是 `str(args.get("task",""))`，而 `args` 由 planner 填。

把这一步**看下来**的办法：临时把 `agents.json` 的 `code_agent.command` 指向
`bench/evidence/l3_s302/probe_agent.py` —— 它把 stdin 原样存证，再**原样**转交真的
`external/code_agent.py`，stdout 直通。

| 透明性自检 | 结果 |
|---|---|
| 同一段（不含"函数名必须是"的）stdin 直跑 vs 过壳子 | stdout **逐字节相同**（`cmp` 通过） |
| 壳子是否改变内核看到的失败形态 | 不变：腔子返回 rc=0/1 与真 agent 一致（stdout 空 ⇒ rc=1） |

⛔ `agents.json` 是**提示词的一部分**（`catalog_sha` 覆盖它）：原始 sha
`674b8941…`，patch 后 `2787a61e…`，跑完 `git checkout` 复核**逐字节恢复**（同 sha）。

## 2. 判据与读数

| # | 命题 | 实测 | 判定 |
|---|---|---|---|
| **P1** | 壳子真被调用，且与账本相等 | `probe_stdin.jsonl` **3** 行；`runs.jsonl` 的 `kernel_external.calls` = **1 + 2 = 3** | ✅ |
| **P2** | state 里确实有评审块（正例对照） | `state_ext.jsonl`：t01 3 行 / t04 2 行，`it=1` 的 state 均含 `[外部评审]` | ✅ |
| **P3** | 外包 stdin 里**没有**评审块 | **3/3** 次 `review=False`、`mem=False` | ✅ |
| **P4** | 交出去的字数 ≈ 题面字数 | `task_chars` = **172 / 112 / 172**，与题面 `len(prompt)` **完全相等**（差 0 ≤ 40） | ✅ |

样例（全文已入库 `probe_stdin.jsonl`）：

```
写一个函数 merge_ranges(pairs)，输入是由 (start, end) 组成的列表，返回合并所有重叠区间后的、
按起点升序排列的列表。例如 merge_ranges([(1,3),(2,6),(8,10)]) == [(1,6),(8,10)]。
注意：只有真正重叠的区间才合并。只输出代码，函数名必须是 merge_ranges。
```
—— 172 字，**到此为止**。而同一时刻的 state 是 416 字，里面躺着：

```
[外部评审] [coding_small/t04_merge_ranges] 0.33 — 通过 2/6；失败: assert merge_ranges([(1,3),(2,6),(8,10)]) == [(1,6),(8,10)] ⇒ TypeError: 'tuple' object do ｜ [coding_small/t05_top_k] 1.00 — 通过 4/4 ｜ …
```

⛔ 这就是上一轮（V212）那个「形状 ⇔ 通过 1:1」的机制**在默认配置下必然为假**的原因：
送出去的那一端根本没见过这段字。

## 3. 正例对照：接上之后确实到得了

`--external-handoff blocks`（本轮新增，默认 `off`）同一题同一配置再跑一次：

| 调用 | 题面 | 附言 | stdin 总长 | `review` |
|---|---|---|---|---|
| t01 | 112 | 227 | **341** = 112 + 2 + 227 | ✅ |
| t04 | 172 | 227 | **401** = 172 + 2 + 227 | ✅ |

附言原文（`probe_stdin_blocks.jsonl`）：

```
[外部评审] [coding_small/t01_parse_duration] 0.17 — 通过 1/6；失败: assert parse_duration('1h30m') == 5400 ⇒ ValueError: not enough values to unpack (expect ｜ [coding_small/t05_top_k] 1.00 — 通过 4/4 ｜ [coding_small/t06_rle] 1.00 — 通过 5/5
```

⇒ **装置接通（positive control 成立）**。这一段是不是**更好**，是 V215 的事
（`PREREG_v215.md`，20 配对块，跑中）。

## 4. 自踩 3 条

① ⛔⛔ **探针的 `has_mem` 判据把一个前缀混进了全名列表**：`_MARKERS` 里同时有
   `"[外部评审]"` 与 `"[外部评审"`，而 `has_mem` 用 `_MARKERS[1:]` 切片 ⇒
   **只要有评审块，`has_mem` 就跟着 True**。首版 blocks 臂读数因此报 `mem=True`（错）。
   修法：拆成 `_REVIEW` / `_MEM` 两个常量；重算后 `mem=False`（记忆目录是空的，
   本来就该 False）。⚠️ 这条不影响 P3 的任何结论（`off` 臂两块都 False）。
② ⛔ **临时改 `agents.json` = 改提示词**（`catalog_sha` 会变）。本轮因此**不跑契约**，
   只在恢复后跑；两个 sha 都写进 `input_sha.txt` / `agents_patched_sha.txt`。
③ ⚠️ 只有 2 次运行 / 3 次调用 ⇒ 结论只关于**结构**（"有没有"），不关于比例。
   "84.4% 都外包"这个比例仍来自 §277 的免费普查，本轮**没有**重测它。

## 5. 判决

**结构性事实成立，且不是推断**：`off`（默认）配置下，累积块进 state、不进外包调用 ——
3/3 次 stdin 逐字等于题面。

- ✅ 修法已接上并留了正例对照（`--external-handoff blocks`，默认 `off`，
  契约 AZ159a~f：只取累积块 / 空块返空串 / 默认载荷逐字节不变 / 附言只追加在题面后 /
  与 `--no-external` 冲突 / 未知档位抛错）。
- ⛔ 本轮**不判**"附言有用"：那要 V215。
- ⚠️ 附言里带的是**同一份 3 条窗口**（含 t05/t06 两条别的题）—— §275 的「跨题串味」
  在默认路径上同样存在，只是以前根本没送出去。这一条留给 V215 之后再看。

**门禁**：契约 **798/0**（792 + AZ159a~f）、锚层 **97/0**、撤回态 **339**、
`check_counts` 0 漂移、`run_tests.sh` EXIT=0。
