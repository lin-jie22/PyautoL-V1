# §56 证据：第一个「同通道、换被测对象」对 + 一条没有闸的补救通道（第九十轮）

## 这一轮要回答的两件事

1. §55 判决说「真缺口是实验设计，不是签名结构」—— 那就把**第一对 `SUBJECT_ONLY`**
   造出来，并机械地证明它是干净的。
2. 顺带把「臂内噪声」量准 —— 因为任何"变好了"的判定都要先跨过它。

## 主读数

| | 臂 A | 臂 B |
|---|---|---|
| 被测对象 | `qwen2.5:7b` | `Qwen3.5-4B:latest` |
| 通道 | `127.0.0.1:8790`（同一端口） | 同左 |
| 协议 | `reasoning_effort="none"`（同一注入） | 同左 |
| 三轮格数 | 100 / 90 / 105 | 116 / 116 / 116 |
| 均值 | **0.7024** | **0.8286** |
| 臂内极差 | **15 格** | **0 格** |

- `probe_pair.py` 判定：`SUBJECT_ONLY`，只差 `['model']`（`pair_verdict.log`）
- `Δ = +17.7 格`，噪声 `15.0 格` ⇒ **可分辨**，余量只有 **2.7 格**
- 正对照：10 题 oracle 在 `static=2` 下**全部通过**（检查器有牙但不乱咬）

## 15 格噪声是什么（这是本轮真正的发现）

不是采样抖动。`sg10` 三轮 **14 / 1 / 14**，查下来：

```
ord1  430 字符  sha db82131d…  14/14
ord2  419 字符  sha 38f40729…   1/14   ← 只少了 `import re`（14 条断言里 13 条 NameError）
ord3  430 字符  sha db82131d…  14/14   ← 与 ord1 逐字节相同
```

执行路径（`run_A_7b.log` 的 `sg10` 第二块）：

```
20:23:56  finish ok=True → 🧪 自检未过（第 1/2 次打回）⇒ 退回重写
20:24:45  [2] 候选=2 → ⏭ 重复动作已跳过（不产生新信息）: finish
20:24:45  ⏳ 预算耗尽 [wall_s] ⇒ 转向收尾
20:25:06  📦 收尾交付 419 字符（来源=收尾调用）
```

⇒ 收尾那条路 `_close_out`（`mw/core.py:1446`）拿到 `planner.answer()` 之后
**直接 `return done("closed", best, ...)`，从不调用 `vfy.run(best, self._checks)`**。
交付物没有被测过，而 `verify.runs` 不增加、`unverified_delivery` 仍是 `False`。

这与两处已写下的声明冲突（`mw/core.py:1049`「走**同一条**交付路径：**同一道自检闸**」；
`_verify` 文档第 ③ 条「**未通过自检的交付不许报 `done`**」）。

## 这条无闸路的规模

| | 臂 A | 臂 B |
|---|---|---|
| 交付物总数 | 30 | 30 |
| 通不过自检（`static=2`） | **1**（`sg10` ord=2） | **0** |
| 收尾交付次数 | 6（`wall` 1 / `wall_retry` 5） | 5（全 `wall_retry`） |
| 其中 `overrode_rejected` | 5 | 0 |
| 其中 `same_as_rejected` | 1 | 0 |

⇒ 无闸路上的不合格率 **1/11**；而这 1 次，等于臂 A 噪声极差的 **13/15**。

⛔ 本证据**不主张**「收尾有害」：`overrode_rejected=5` 说明收尾常常换掉被打回的那一版，
它可能更好也可能更差 —— 而这恰恰是问题所在：**没有测过，所以是 UNKNOWN**。

## 文件

| 文件 | 内容 |
|---|---|
| `PREREG.md` | 跑**之前**写定的设计 + 预测，以及跑完之后追加的「结果 vs 预测」（原文未改） |
| `run_pair.sh` | 两臂脚本（同一端口、同一注入、顺序跑） |
| `runs_A_7b.jsonl` / `runs_B_4b.jsonl` | 两臂运行记录（`caliber` 全字段 + `per_task` + `kernel_*` 读数） |
| `answers_A_7b.jsonl` / `answers_B_4b.jsonl` | 两臂交付物原文（各 30 条） |
| `proxy_A_7b.jsonl` / `proxy_B_4b.jsonl` | 通道台账（109 / 111 次请求，**逐条**含 `req_sha`/`up_sha`/`injected`） |
| `run_A_7b.log` / `run_B_4b.log` | 内核执行日志（含三轮逐题的收尾/自检行） |
| `readings.log` | 逐轮格数表 + `sg10` 三轮字节对比与 unified diff |
| `pair_verdict.log` | `probe_pair.py` 的判定输出（先判定、后读数） |
| `closeout_gate_A_7b.log` / `closeout_gate_B_4b.log` | 交付物过闸统计（含 oracle 正对照） |
| `attrib_sg10_rep2.log` | `sg10` ord=2 的逐条归因（13 条同一个 `NameError: re`） |
| `selfcheck_*.log` | 三件量具的自检（11 / 7 / 6 条反证） |

## 复现

```bash
PY=python3
bash bench/evidence/model_s90/run_pair.sh          # 约 50 分钟，两臂各 repeat=3（会跑真模型）
$PY bench/probe_pair.py --a runs_A_7b.jsonl --b runs_B_4b.jsonl --suite bench/specgen/tasks_v2.jsonl
$PY bench/probe_closeout_gate.py --answers answers_A_7b.jsonl
$PY bench/probe_task_attrib.py --task sg10 --answers <只含 ord=2 那一行的文件>
```

## ⛔ 边界（越界即作废）

1. **n=1 个端点、1 个题集、1 天、各 3 次采样。** 结论是「这两次运行在这个尺子上差 17.7 格」，
   ⛔ 不是「4B 比 7B 强」这种普适命题；换端点/换题集都要重测。
2. **余量只有 2.7 格。** 噪声估计来自 3 个样本的极差（它仍是下界）。
   按本轮自己的教训（见 `PREREG.md`），这个判定**只差一个不可控事件就会翻转**。
3. **`reasoning_effort="none"` 是代理注入的**，不是内核发的。
   两臂相同 ⇒ 它被固定住而没被消除；⛔ 带注入的读数与**不带注入**的历史读数不是同一把尺子
   （`endpoint` 相同但请求体不同）。
4. **4B 的"手机关掉"状态没有被测。** 它在自然状态下是思考模型（同题 554 vs 12 token、
   23.2s vs 0.57s），本轮只测了它关思考后的样子。⛔ 不许把本轮读数当成"4B 的能力上限"。
5. **`answers.jsonl` 的 `rep` 字段恒为 1**（`run.py:1199` 调 `add()` 时没传 `rep`）⇒
   第几次 repeat 只能按**行序**推断，而"行序 == 执行顺序"是一句没有声明过的约定。
   `readings.log` 里同时印 `rep`（落盘值）与 `ord`（行序推断）。
6. 本轮**没有改内核**（`mw/` 与 `run.py` 零 diff）。所有读数都是既有内核的行为，
   不是"修好之后"的行为。
