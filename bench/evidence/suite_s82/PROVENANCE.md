# 第八十二轮（§45）证据台账 —— 加题：尺子更容易看见退化，天花板没动

日期：2026-10-25　本地模型 `qwen2.5:7b`（ollama，`127.0.0.1:11434`），T=0，
`--fresh --no-external --repeat 1 --no-ledger`。

## 文件

| 文件 | 是什么 |
|---|---|
| `calib_v2.log` | v2 的三条**内核无关**标定：`oracle` 1.0000 / `partial` 0.2429 / `null` 0.0000 |
| `base_r1.log` | 真跑：`verify_static=2`（默认，`on`）一次，**100/140 = 0.7143** |
| `off_r1.log` | 真跑：`V1_VERIFY_STATIC=0`（`off`）第 1 次，**80/140 = 0.5714** |
| `off_r2.log` | 真跑：`V1_VERIFY_STATIC=0`（`off`）第 2 次，**76/140 = 0.5428**（差全在 `sg02`/`sg05`）|
| `taxonomy_base_r1.log` | 对 `on` 臂的失分做机械分类：40 格 = 26 语义 + 14 崩溃，**可动作 0** |
| `taxonomy_off_r1.log` | 对 `off` r1（原文）分类：60 格 = 26 未定义名 + 34 语义，**可动作 43.3%** |
| `taxonomy_off_r2.log` | 对 `off` r2（原文）分类：64 格 = **26** 未定义名 + 24 语义 + 14 崩溃，**可动作 40.6%** |
| `withdraw_ALL_161.log` | 全量撤回 161 条：160 绿 + 1 条多红（㉘ 连带 AZ91，已登记 `COUPLED["AZ76"]`）|
| `withdraw_AZ76_after_coupled.log` | 登记白名单后单条复跑：✅ 单红命中 |
| `key_answers.jsonl` | `sg01`/`sg10` 两臂的答案原文（4 条，带 sha256 与字符数） |

## 两条 run_id（口径唯一标识）

| 臂 | run_id | caliber_sig | suite_sha |
|---|---|---|---|
| `on`（`verify_static=2`） | `69666@1792912166` | `8efab8f72ee4ac5e` | `d6b7ed14f56a` |
| `off`（`verify_static=0`）r1 | `70620@1792912544` | `d3a7da8749b89f8f` | `d6b7ed14f56a` |
| `off`（`verify_static=0`）r2 | `5425@1792919669` | `d3a7da8749b89f8f` | `d6b7ed14f56a` |

两次运行**只差 `limits.verify_static` 一个字段**（`limits` 指纹 `d6727c1c…` vs `f0164298…`），
其余 caliber 字段逐个相同 —— 这是"单变量"的机器判据，不是散文承诺。

## 四条禁令（读这份台账时必须带着）

1. **不许把 140 格的分数与 84 格的分数并列**：`suite_sha` 不同（`d6b7ed14` vs `cee95582`）。
   可以并列的只有**逐题**读数 —— 共享的 6 题在两份 jsonl 里**逐字节相同**（AZ91 钉住）。
2. **不许把 `0.6905 → 0.7143` 写成"涨了"**：分母 84 → 140，分子 58 → 100，
   涨的那部分全来自新题（`sg10` 一题给了 14 格全满）。见 §45.9。
3. **不许把这些运行当裁决**：`off` 2 次（极差 0.0286，全在 `sg02`/`sg05`）、`on` 1 次，
   凑不够 `noise_floor.MIN_RUNS=5`。
   `sg02` −3 / `sg05` −1 与冷启动抖动、一次性点同源，不可反读成"旋钮有害"。
4. **不许删题**：`sg10` 是按"诱发未定义名"设计的（预注册在 `taskspec.WHY_NEW_OPS`），
   它真的诱发了 13 格 —— 删掉它就是拿内核分数筛题（§16.9 明令禁止）。

## 复现

```bash
# 题集（从 spec 重算，旧 v1 文件应逐字节不变）
python3 bench/gen_spec_tasks.py

# 三条内核无关标定（不需要模型/网络）
python3 run.py --bench --suite bench/specgen/tasks_v2.jsonl --solver oracle  --fresh --no-external --no-ledger
python3 run.py --bench --suite bench/specgen/tasks_v2.jsonl --solver partial --fresh --no-external --no-ledger
python3 run.py --bench --suite bench/specgen/tasks_v2.jsonl --solver null    --fresh --no-external --no-ledger

# 两臂（需要本地 ollama）
python3 run.py --bench --suite bench/specgen/tasks_v2.jsonl --solver llm --fresh --no-external -T 0 --repeat 1 --no-ledger
V1_VERIFY_STATIC=0 python3 run.py --bench --suite bench/specgen/tasks_v2.jsonl --solver llm --fresh --no-external -T 0 --repeat 1 --no-ledger

# 失分分类（先自证）
python3 bench/probe_error_taxonomy.py --self-check
python3 bench/probe_error_taxonomy.py --run-id <run_id>
```
