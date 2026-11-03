# V54（第一百四十三轮）预注册 —— 准入空轮出口第一次说得出「此前做过几次」

> 冻结时刻：2026-11-03 21:2x（改动 `mw/` **之前**）。
> ⚠️ **J1/J2 是在写这份预注册之前量的**（它们决定"要不要改"，属于开工第一件事）。
> 下面逐条标明「预注册前已量」与「预注册时押注」，两者不混。

## 0. 上一轮交给本轮的那条判据

MEMORY §五 第 2 项（V54 第 1 项）：

> ⭐⭐⭐ **准入空轮不许转向交付**：`status=not_applicable` 时 `verdict` 不许是 `ANSWER`。

本轮的处置是：**先量分母，再决定改不改代码**。结论是 **不按它改**（理由 J1/J2），
换成一条**可判定**的边（J3/J4）。

## 1. J1（预注册前已量）：这条判据在已有语料上分母为 0

扫描命令（可复算）：

```python
# 扫 bench/evidence/**/*.txt 与 *.log 里含 "not_applicable" 的每一行
# 其中以 "■ not_applicable" 开头的是「状态行」，再用正则取 验收=([A-Z]+)
```

| 项 | 值 |
|---|---|
| 含 `not_applicable` 的行 | 113 行（其中 106 行是 stats JSON 字段 `dropped_not_applicable` / `skipped_not_applicable`，**不是状态行**） |
| `■ not_applicable` 状态行 | **7 条** |
| 其中 `验收=ANSWER` | **0 条** |
| 其中 `验收=PASS` | 4 条（`v6_s137`×2 / `v6_s138` / `v6_s139`） |
| 其中 `验收=FAIL` | 3 条（`v6_s141/logs_c` / `v6_s142/logs_a` / `v6_s142/logs_b`） |

⇒ 判据「`not_applicable` ⇒ `verdict ≠ ANSWER`」在已有语料上**一次都不会开火**。
⛔ 本轮的结论**不是**「这条边不存在」，而是「**已有语料 0 次** ⇒ 改代码也无法证伪」。
按纪律 15（「没被尝试」≠「尝试过并失败」）与「没证据更好 = 不改」，**不按它改 `verdict`**。

## 2. J2（预注册前已量）：「准入空轮 = 什么都没做」被自己的证据否证

`bench/evidence/v6_s139/logs_b.txt`，同一题（sales.csv → `report.md`）：

```
00:19:20  ↳ run_shell ok=True 使用 shell 命令处理 CSV 文件
00:19:49  ↳ run_shell ok=True 使用 awk 和 sed 组合命令生成报告
00:20:28  ↳ run_shell ok=True 检查报告文件是否存在，如果不存在则生成
00:20:58  ⊘ 准入空轮到阈值 ⇒ 探索到此为止，但**还有额度** ⇒ 转向交付
00:21:03  ■ not_applicable 验收=PASS(1/1)
```

⇒ 这条 `not_applicable` **是做过三件事之后走神**，产物真在。
若照原判据把它的 `verdict` 从 ANSWER 降级，会连**正确交付**一起打掉
（这条是 PASS 不是 ANSWER，但同一条出口在无产物题上就是 ANSWER）。
⇒ 判据必须**条件化在「此前是否真的执行过」**，而这个条件现在**无法计算**（见 J3）。

## 3. J3（预注册时押注）：内核没有「下发过几次」的计数器

- `■` 行的 `steps=N` = `len(history)`，数的是**规划轮**（"让位"、"预演否决"、
  "跳过"、"准入空轮"都会往 `history` 里写行）⇒ **不等于做过几件事**；
- `flow.consumed["reason>act"][0]` 是执行条数，但**只有条数、没有成败**，
  且在 `■` 行上只以**比值**现身（`reason>act=0.6`），单看它读不出绝对值；
- `external_stats["calls"/"ok"/"err"]` 只覆盖 `produces_answer` 的外部能力，
  `run_shell` / `read_file` / `write_file` 一概不记。

⇒ 「做过三件事之后走神」与「一件事都没做成」在读数上**同形**。这是 J1 那条判据
无法被判定、J2 那条反例当时也看不见的**物理原因**。

**押注 P0**：`mw/core.py` 里除 `self.tools.dispatch(`（唯一调用点，1409 行）之外，
不存在第二条下发路径；不存在 `exec` 类计数（grep `exec_ok|executed|dispatched` 为空）。

## 4. J4（本轮落地：只记账，不改判据）

| # | 改动 | 位置 |
|---|---|---|
| 1 | `Core.exec_stats = {"dispatched":0,"ok":0,"fail":0}`，`_reset_exec()` 在 `__init__` 与 `run()` 各调一次 | `mw/core.py` |
| 2 | 在**唯一**下发点更新三项（`result.ok` 分成败） | `mw/core.py:1409` 附近 |
| 3 | `_finish` 的 `stats` 增 `"exec"` 键；`■` 行在 `steps=` 后紧跟 `执行=N下发/M成/K败` | `mw/core.py:_finish` |
| 4 | `_close_out` 三个入口的 `reason` 追加「此前已下发 N 次（成功 M）」；`N=0` 时写「**一次都没下发**」；收尾日志同样一句 | `mw/core.py:_close_out` |
| 5 | `closeout` 增 `zero_exec`（收尾时零下发）与 `zero_exec_by_reason`（按入口分列） | `_reset_closeout` + `_finish` 的 stats 深拷 |
| 6 | 两个新键进 `runs.jsonl` 的 `kernel_closeout` | `run.py:_kco_zero/_kco_absorb` |

⛔ 不动 `best` / `status` / `verdict` / `by_reason` 的语义（只**追加**字句与键）；
⛔ `mw/verify.py`、`mw/planner.py` 零 diff；⛔ 不新造判据、不新增阈值。

## 5. J5（可证伪预测）

零模型探针 `probe_v54_exec.py`，四格：

| 格 | 场景 | 预测（改后） | 押错的表现 |
|---|---|---|---|
| S1 | 零下发 + 无约定产物 ⇒ 准入空轮出口 | `verdict=ANSWER`（**不变**）、`zero_exec=1`、`zero_exec_by_reason["not_applicable"]=1`、reason 含「一次都没下发」 | 改前就该无这两个键 |
| S2 | 零下发 + 约定产物（V52 那道） | `verdict=FAIL`（不变）、`zero_exec=1`、`exec=0下发` | `verdict` 变了 |
| S3 | **三次成功下发** + 无约定产物 ⇒ 同一出口 | `zero_exec=0`、`exec=3下发/3成/0败`、reason 含「已下发 3 次（成功 3）」 | 若 `zero_exec=1` ⇒ 计数接错了点 |
| S4 | 直接调 `_close_out`（`ledger=None`，AS20 那种单元级入口） | 不炸、reason 含「已下发 0 次」 | 抛异常 ⇒ 收尾路径自己炸 |

**P5（交付物反向闸）**：四格 `best` 的 sha256 前 16 位改前/改后**逐字节相同**。
**P6（真实臂，A=HEAD / B=补丁，`repeat=1`，同一道 sales.csv → report.md 题）**：
- `report.md` 两臂**都不存在**（V52 实测这就是模型的行为）；
- `status` 两臂都 `not_applicable`；`verdict` 两臂都 `FAIL`；
- B 臂日志多出 `执行=` 段与「此前已下发 N 次」句。
⇒ 押「**行为不变、读数变多**」。

## 6. ABORT / 撤回条件（先写死）

1. P6 里 `report.md` 出现，或 `status` 改变，或 `verdict` 出现非良性变化
   ⇒ **记账动了行为** ⇒ 整轮回退，只登记负结果。
2. P5 任一格交付物 sha 变化 ⇒ 同上。
3. 契约打红**与本改动无关**的断言（V51 那种 +35s/+6calls 式净负）⇒ 按「没证据更好」撤回。

## 7. 分母纪律

- 本轮所有「几次」都必须带**分母**：`■ not_applicable` 7 条 / 全语料 821 个日志文件；
  探针 4 格；真实臂 2 格（1 题 × 1 rep × 2 臂）。
- ⛔ 不写「7 条里 0 条」之外的任何推断；⛔ 不把 4 格探针写成「能力分数」。
