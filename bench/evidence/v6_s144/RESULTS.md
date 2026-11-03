# V54（第一百四十三轮）结果 —— 准入空轮出口第一次说得出「此前做过几次」

> 预注册：本目录 `PREREG.md`（J1/J2 是**预注册前**量的，已标注；J4/J5/P5/P6 是预注册时押的）。
> 证据：`p0_path.txt` / `p0_before.txt` / `p1_after.txt` / `probe_v54_exec.py` /
> `run_v54.sh` / `logs_{a,b}.txt` / `logs_arms.txt` / `arms_summary.txt` / `goal.txt`。

## 1. 结论一句话

上一轮交办的第 1 项（「`not_applicable` 时 `verdict` 不许是 `ANSWER`」）**分母为 0，且被自己的
证据否证**（准入空轮出口可以是"做过三件事之后走神"，产物真在）⇒ **不按它改判据**。
本轮的落地换成一条**可判定**的边：内核第一次有了「下发过几次」的计数器，
收尾出口因此说得出「此前已下发 N 次（成功 M）」或「**一次都没下发**」。

## 2. J1 分母（预注册前已量）

扫 `bench/evidence/**` 下 **829** 个 `.txt/.log`：

| 项 | 值 |
|---|---|
| 含 `not_applicable` 的行 | 113（其中 106 是 stats JSON 字段，不是状态行） |
| `■ not_applicable` 状态行 | **7** |
| 其中 `验收=ANSWER` | **0** |
| `验收=PASS` | 4（`v6_s137`×2 / `v6_s138` / `v6_s139`） |
| `验收=FAIL` | 3（`v6_s141/logs_c` / `v6_s142/logs_a` / `v6_s142/logs_b`） |

⇒ 原判据在已有语料上**一次都不会开火**。⛔ 结论不是「这条边不存在」，是「已有语料 0 次 ⇒ 改了也无法证伪」。

## 3. J2 反证：「准入空轮 = 什么都没做」不成立

`bench/evidence/v6_s139/logs_b.txt`，同一题：

```
00:19:20  ↳ run_shell ok=True 使用 shell 命令处理 CSV 文件
00:19:49  ↳ run_shell ok=True 使用 awk 和 sed 组合命令生成报告
00:20:28  ↳ run_shell ok=True 检查报告文件是否存在，如果不存在则生成
00:20:58  ⊘ 准入空轮到阈值 ⇒ 探索到此为止 … ⇒ 转向交付
00:21:03  ■ not_applicable 验收=PASS(1/1)
```

⇒ 这条 `not_applicable` 是**做过三件事之后走神**。按原判据把它降级，会连正确交付一起打掉。

## 4. J3/P0 真缺口：`steps=N` 数的是规划轮，不是动作

`p0_path.txt`（改前实测）：`tools.dispatch(` 全仓**只有一处**（`mw/core.py:1409`）；
`exec_ok|executed_count|dispatched|exec_stats` **一个都不存在**；
`flow` 的 `reason>act` 只有条数、没有成败，且只在 `■` 行以**比值**出现（`reason>act=0.333`）。

## 5. J4 落地（只记账）

| # | 改动 | 位置 |
|---|---|---|
| 1 | `exec_stats = {dispatched, ok, fail}` + `_reset_exec()`（`__init__`/`run()` 各一次） | `mw/core.py` |
| 2 | 唯一下发点更新三项（`result.ok` 分成败） | `mw/core.py` |
| 3 | `stats["exec"]`；`■` 行 `steps=N 执行=D下发/M成/K败` | `_finish` |
| 4 | 入口日志 `⊞ 此前已下发 N 次（成功 M、失败 K）` / `⊞ 此前**一次都没下发**` | `_close_out` |
| 5 | 三个出口的 `reason` 追加同一句（） | `_close_out` |
| 6 | `closeout.zero_exec` + `closeout.zero_exec_by_reason` | `_reset_closeout` / `_finish` |
| 7 | 两个键进 `runs.jsonl` 的 `kernel_closeout` | `run.py:_kco_zero/_kco_absorb` |

⛔ 未动 `best` / `status` / `verdict` / `by_reason`；`mw/verify.py`、`mw/planner.py` 零 diff；
`catalog_sha` 两口径不变（`8decb608…` / `29091404…`）。

## 6. J5 读数（零模型四格 × 14 条判据）

| 格 | 场景 | 改前 | 改后 |
|---|---|---|---|
| S1 | 零下发 + 无约定产物 | `verdict=ANSWER`，**无**任何下发读数 | `ANSWER`（不变）+ `zero_exec=1` + reason/日志「一次都没下发」 |
| S2 | 零下发 + 约定产物 | `FAIL`，无读数 | `FAIL`（不变）+ `zero_exec=1` |
| S3 | **3 次成功下发** + 无约定产物 | 无读数（`steps=5` 与 S1 的 `steps=2` 是唯一差别） | `exec=3下发/3成/0败`、`zero_exec=0`、reason「已下发 3 次」 |
| S4 | `_close_out` 单元级（`ledger=None`） | 不炸、但零痕迹 | 不炸 + 「一次都没下发」 |

- `p0_before.txt` `EXIT=1`（**10 红 4 绿**）→ `p1_after.txt` `EXIT=0`（14 绿）。
- **P5 成立**：四格交付物 sha 两态同为 `8d77710519debdd2`（47 字）⇒ 只记账、没改分。

## 7. P6 真实两臂 —— **押错一条，另一条成立**

`run_v54.sh`（同一道 sales.csv → `report.md`，`repeat=1`，T=0，串行，33s / 29s）：

| 读数 | A（HEAD） | B（本轮补丁） |
|---|---|---|
| `status` | `done` | `done` |
| `verdict` | `FAIL` | `FAIL` |
| `report.md` | 不存在 | 不存在 |
| `plan.rounds` / `dropped_na` | 1 / 3 | 1 / 3 |
| `stats["exec"]` | **null** | `{"dispatched":1,"ok":1,"fail":0}` |
| `■` 行 | `steps=1`（无执行段） | `steps=1 执行=1下发/1成/0败` |

⇒ **押对**：行为逐项相同（A==B），读数变多。
⇒ **押错**：PREREG 预测两臂都 `status=not_applicable` —— 实际两臂都是 `done`。
原因（`logs_b.txt`）：模型第 1 轮就提了 `finish`（编了一张「区域A..E」的表），
只有 3 个 `code_agent` 被准入丢下，**其余候选仍然存在** ⇒ 不进准入空轮。
⛔ 这条必须记成押错，不许写成"等价于 not_applicable"。

**顺带一条更硬的读数**：V52 之所以能走到准入空轮，是因为当时装着**交付闸**（把编的表打回，
逼出更多轮）。闸在 V52 收尾时撤回 ⇒ 现网（HEAD）这条出口的分母比历史 7 条**更小**。
⇒ 本轮这条边是"读数补上"，不是"病灶修好"。

## 8. 门禁

| 项 | 值 |
|---|---|
| 契约 | **621/0**（+`AZ117` 六条判据） |
| 锚层 | **97/0** |
| `check_counts` | 22 处、0 漂移 |
| 生产库指纹 | `359c7100c2272d14` 未变 |
| 定向撤回态 | `AZ117`：`621/0` → `红=['AZ117'] 多红=无` → `621/0` |
| 撤回态全量 | ⛔ **未跑**（202 条 ≈ 2h）；沿用旧读数必须写明是旧读数 |

## 9. 自踩

1. **`Core.log` 默认写文件、不是 stdout**：第一版探针只 `redirect_stdout` ⇒ 日志捕获恒空，
   「日志锚点」一节空白。读起来像"一行日志都没有"，实际是**量具接错了口**。
   ⇒ 传 `log=` 收集器。
2. **一轮塞三个候选只会执行一个**：主循环一旦有候选成功就停在本轮（其余是"失败后的替代品"）。
   第一版 S3 断言 `exec=3/3/0` 实测 `1/3/0` —— 是**装置**没给到三次机会，不是计数器错。
3. **BSD grep 不认 `\|`**：`grep -n "■ done\|■ not_applicable"` 返回空且 `EXIT=1`，
   读起来像"两臂都没有状态行"。改用 Python 逐行判 —— 与 V53 第 1 条同型，**第二次**。
4. **契约断言里写了半角双引号**：`"...读成"做了几步"）"` 直接把字符串截断 ⇒ `SyntaxError`。
   ⇒ 中文叙述里的引号一律用「」。
5. **`■` 主行带时间戳**（`21:26:49 ■ done …`），`startswith("■ ")` 抓不到；
   同文件里 `■ 验收 …` 是 run.py 打的、不带时间戳 ⇒ 同一文件两种行格式，过滤器必须按内容判。
