# PREREG V217（先写后跑）—— 把同题条目挡掉之后：跨题失败形状还改不改答案

证据目录：`bench/evidence/l3_s302/`。写于 2026-11-21 21:4x，**跑之前**。

## 0. 一句话问题

V216 证明：shape 档（附言无测试源码）下，块仍能稳定改答案（Δ=[0,+2,+2]）。
但块的第一行**就是本题自己的历史** ⇒ 效应与「知识转移」不可识别（纪律 120）。
本轮把 `--critique-scope` 从 `prev-self` 换成 **`prev`**（`allow_self=False`，同题条目被挡），
问：**只看别的题的失败形状，答案还会不会变。**

## 1. 前置免费量（已做，`FREE_scope_v217.txt`）

用生产函数（`_critique_snapshot` + `admissible` + `mw/core.py:2319` 的取块逻辑）实算：
种子 = `prior_v214.jsonl`（6 条：t01 0.17 / t04 0.33 / t02,t03,t05,t06 各 1.00）。

| scope | 题 | 块内容 | 含本题 | 含失败信息 |
|---|---|---|---|---|
| `prev-self` | t01 | t01 0.17 + t05 + t06 | **True** | 1 |
| `prev-self` | t04 | t04 0.33 + t05 + t06 | **True** | 1 |
| **`prev`** | **t01** | **t04 0.33（TypeError,AssertionError）+ t05 + t06** | **False** | **1** |
| **`prev`** | **t04** | **t03 1.00 + t05 1.00 + t06 1.00** | **False** | **0** |

⇒ 换 scope 后**本题条目确实消失**（纪律 120 的先决条件满足）；
但**只有 t01 那一格拿得到有信息的行**，`t04` 拿到的是一段「三行全过」的空话（119 字）。
这不是设计缺陷，而是本轮**内建的零信息对照**：同样的机制、同样的转发路径、不同的内容信息量。

## 2. 两臂（只差 `--external-handoff`）

| 臂 | 参数 |
|---|---|
| `off` | `--external-handoff off` |
| `blocks` | `--external-handoff blocks` |

两臂**都**用 `--critique-scope prev --critique-why shape`；其余（`--solver llm --temperature 1.0
--budget 1 --repeat 1 --critique-file prior_v214.jsonl --suite suite_sub2.jsonl`）逐字沿用 V216。

## 3. 装置

- **3 配对块 × 2 臂 = 6 次**；`interleave.py --arms off,blocks --blocks 3`，块内相邻（≤2 min）。
- **6 次全部走探针**（`probe_agent.py` 接管 `code_agent`，`V1_PROBE_LOG` 分臂）⇒ P1d 的入参原文
  与判据读数**同一批运行**，不额外多跑。`agents.json` 原 sha `674b8941…`，跑完 `git checkout` 复核。
  ⚠️ patch 期间 `catalog_sha` 变 ⇒ **不跑契约**，恢复后再跑。
- 跑前闸：真实负载中位解码 ≥15 tok/s 才启动；任一单次 >300 s 整批停（纪律 116）。预计 12~16 min。

## 4. 判据（三值，先写后跑）

| # | 门槛 | 不达即 |
|---|---|---|
| **P1a** | 6/6 `rc=0` 且单次 ≤300 s | UNKNOWN |
| **P1b** | 两臂 state 各 ≥3 行带 `[外部评审]` | UNKNOWN |
| **P1c** | 每臂 ≥5/6 次 `kernel_external.calls ≥ 1` | UNKNOWN |
| **P1d** | 入参原文：`off` 6/6 追加段为空；`blocks` 6/6 追加段 **不含本题 task_id**，且 t01 的追加段**含** `t04_merge_ranges` | UNKNOWN（scope 没接上） |
| **P2** | 主判据 = **t01 通过数**（分母 6），Δ_k = blocks − off，k=1..3：<br>**成立** = ≥2/3 块 Δ ≥ +1；**不成立** = ≥2/3 块 Δ ≤ −1；其余 = 不可判 | —— |
| **P3** | 内建对照 = **t04 通过数**（分母 6，块里零信息）。押 Δ=0；若 t04 也 Δ ≥ +2 ⇒ 「**加文本改抽样**」这个解释胜出，P2 的归因作废 | —— |

⛔ 不给 p 值（纪律 122）；n_eff 用「不同 vec 状态数」报。
⛔ 主判据选 t01 是因为它**有变化空间**（`off` 基线 3/6，天花板 6/6）；`t04` 在 V216 两臂全 6/6，
**已到天花板**，所以它只能当对照，不能当主判据（纪律 119）。

## 5. 押什么

押「**P2 不成立 + P3 Δ=0**」：t04 那种「tuple 不可变」的形状提示对 parse_duration（字符串解析）
没有可迁移内容，块只是一句「别的题也没全过」。若真如此，结论是：
**V216 的效应里，能被跨题搬运的那部分 ≈ 0**。

## 6. 不许说

⛔ 不说「跨题知识有效/无效」（2 题、n_eff≈1）；⛔ 不说「累积有牙」；⛔ P1 破了不改判据、不换题集。
