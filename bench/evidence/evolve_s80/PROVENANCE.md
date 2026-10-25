# 证据：三臂真跑第一次（§43.9）—— 细节载荷帮倒忙

## 一、这一发在问什么

`verify_static` 三值旋钮（§43.4）：`0` 关 / `1` 报名字 / `2` **同一触发集盲报**。
第一发把 `2` 放进**安慰剂槽位**，本意是「等长安慰剂臂」——
触发时机与多花的 LLM 调用与 `1` 完全相同，只有载荷不同。

## 二、命令（可重放）

```bash
export V1_LLM_BASE_URL=http://127.0.0.1:11434/v1 V1_LLM_API_KEY=ollama V1_LLM_MODEL=qwen2.5:7b
python3 -u bench/mutate_driver.py \
  --knob verify_static --to 1 \
  --placebo-knob verify_static --placebo-to 2 \
  --n 5 --min-effect-cells 3 --out bench/evidence/evolve_s80
```

## 三、读数

| 臂 | 台账户均 | 各次 | 轮间极差 |
|---|---|---|---|
| base（`0`） | 0.5536 | 0.5238, 0.5595×5 | **0.0357** |
| cand（`1` 报名字） | 0.6190 | 0.6190×6 | 0.0000 |
| placebo（`2` 盲报） | **0.6905** | 0.6905×5 | 0.0000 |

逐题（格 /14）：`sg01` **1 → 6 → 12**；`sg02` 7或10 → 10 → 10；
`sg03/04` 恒 14；`sg05` 恒 0；`sg06` 恒 8。

**裁决**：`NO_EVIDENCE`（交叠闸：cand 0.6190 没有严格好过盲报臂 0.6905）。
驱动器同时报「安慰剂不惰性 ⇒ 本次裁决不成立」—— 这句是**装置用错了对象**：
`2` 是故意的**剂量臂**，不是惰性对照（见 DESIGN §43.9 末段）。

## 四、文件

| 文件 | 内容 |
|---|---|
| `base_r{1..5}.log` | base 臂每次的完整 stdout |
| `cand_r{1..5}.log` | 报名字臂 |
| `placebo_r{1..5}.log` | 盲报臂 |
| `pool_{base,cand,placebo}.jsonl` | 三臂池子（每行一条 `runs.jsonl` 原始记录） |
| `verdict.json` | 驱动器落盘的裁决明细（含 `placebo_inert: false`） |
| `PROVENANCE.md` / `FILES.sha256` | 本文件与指纹 |

池子**不对称**：base 6 / cand 6 / placebo 5（烟测已占前两臂各 1 次）。
每臂都 ≥ `MIN_RUNS=5`，噪声下限可测；不对称本身写在明处，不隐去。

## 五、边界

1. 单模型（qwen2.5:7b 本地）、T=0、6 题 × 14 用例 = 84 格。
2. 效应**全部落在 `sg01`**（唯一带未定义名的题）；`sg02` 的 7→10 只在 base 第一次运行
   出现，是预热抖动，不算效应。
3. `sg05` 一格不动 —— 类型错，静态判据看不见（§43.3 已预判）。
4. ⛔ **不主张「内核会自己变强」**：这一发的裁决是 `NO_EVIDENCE`。真正的候选是
   `verify_static=2`，那是下一发（`bench/evidence/evolve_s80b/`）。
