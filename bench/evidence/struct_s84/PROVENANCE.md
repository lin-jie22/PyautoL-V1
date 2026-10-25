# `struct_s84` 证据来源（第八十四轮 §47）

内核零 diff：`mw/`、`run.py`、`bench/specgen/*.jsonl` **一行没改**。
本轮所有读数来自 `bench/probe_structcheck.py` 与已有的三次真实运行答案
（`bench_out/answers.jsonl` / `runs.jsonl`，与 §45、§46 同一批零点）。

## 零点（不是造出来的）

三次真实运行 × 10 题 = **30 对**，总丢格 **164**：

| run_id | 口径 | 丢格 | 逐题 |
|---|---|---|---|
| `69666@1792912166` | `verify_static=2`（默认） | 40 | sg01 2 / sg02 7 / sg05 14 / sg06 6 / sg07 2 / sg08 7 / sg09 2 |
| `70620@1792912544` | `=0` | 60 | sg01 13 / sg02 4 / sg05 13 / sg06 6 / sg07 2 / sg08 7 / sg09 2 / sg10 13 |
| `5425@1792919669` | `=0` | 64 | sg01 13 / sg02 7 / sg05 14 / sg06 6 / sg07 2 / sg08 7 / sg09 2 / sg10 13 |

40 + 60 + 64 = 164，与 §45 分类器逐格对上（UNDEFINED 52 / CRASH 28 / SEMANTIC 84）。

## 文件

| 文件 | 内容 |
|---|---|
| `raw_model.jsonl` | 模型臂 10 条回答**全文**（含 `text`，不截断）+ 落盘时的 `ops`/`constraints` |
| `run_model.log` | 模型臂现场日志（`--solve`，含逐题 `ops` 打印与三节报告） |
| `report.log` | 当前口径重出的完整报告（`--report`，零模型调用） |
| `selfcheck.log` | 四组反证（`--self-check`，零模型） |
| `pin.log` | `--pin`：题面→约束 必须等于 spec→约束（零模型，供步骤 7j） |
| `per_run.log` | 按运行拆的结构 / 崩溃 / 并集覆盖 |
| `summary.json` | 四个来源的算子、约束、30 对的真态与类别 |

## 口径变动（都可复算，方向如实标注）

1. **JSON 抽取收紧**（对模型**不利**）：第一版在最外层 JSON 坏掉时退而取内层第一个合法片段，
   把 `sg03`/`sg08` 记成"模型说没有 ops"。收紧后 **2/10 PARSE_FAIL**。
   `--report` 每次都用当前解析器**重解析 `raw_model.jsonl` 的 `text`**，
   所以改口径不需要重跑模型。
2. **`no_ellipsis` 改为只在出现过 `truncate` 时可推**（对模型**有利**一处、对本轮结论**中性**）：
   `keep_tail` 的题面里没有「不加省略号」这句话，无条件 True 是把人的知识塞进判据。
3. **`_cmp_ops` 不再比较 `fold`/`keep`**：题面里 dedupe 那句恒写「转成小写后相同的键」，
   `fold` 从文字上不可复原。
4. **`EXEC` 与 `struct` 拆族**（对结论**不利**）：第一版把崩溃混进结构覆盖得 68.9%，
   拆开是 20.1%。这是自我打脸，写在 §47.4。

## 复现

```sh
python3 bench/probe_structcheck.py --self-check   # 零模型，四组反证
python3 bench/probe_structcheck.py --pin          # 零模型，题面→约束 == spec→约束
python3 bench/probe_structcheck.py --report       # 零模型，从 raw_model.jsonl 重出
# 重跑模型臂（10 次调用，本地 ollama qwen2.5:7b，T=0）：
V1_LLM_BASE_URL=http://127.0.0.1:11434/v1 V1_LLM_API_KEY=ollama \
  python3 bench/probe_structcheck.py --solve
```

## 提示词原文

在 `bench/probe_structcheck.py` 的 `_SYSTEM` 与 `_MODEL_TMPL`（**未截断**）。
`raw_model.jsonl` 里每条记录都带模型**原样输出**，任何一条读数都能逐字复核。
