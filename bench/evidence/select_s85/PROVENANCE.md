# 证据来源（`select_s85`，第八十五轮 §48）

本轮**零模型调用**：所有读数都是在**已经冻结**的运行记录上做选择，所以这里记的不是
"怎么跑的模型"，而是"这些记录从哪来、有没有被改过"。

## 一、`v1` 池（6 题 84 格，两条真策略）

| 文件 | 来源 | 源文件 sha256 | 冻结后的 sha256 |
|---|---|---|---|
| `v1_direct_runs.jsonl` | `/tmp/v1_direct_s80/runs.jsonl` 中 `run_id=59260@1792909152` | `281f3a4a52c4c909efe8bf7009d352a7894154d867090b32c33d68c8cb9b7f60` | `9883911c9caa00f1…` |
| `v1_direct_answers.jsonl` | `/tmp/v1_direct_s80/answers.jsonl` 同 `run_id` 的 6 条 answer | `a96285df2ca03b2bf65c62dcd74f351527118a36a78e46cbe499644d7904b7f1` | `6995b39936b1db09…` |
| `v1_kernel_runs.jsonl` | `bench_out/runs.jsonl`（`77043550e5309c71…`）中 `run_id=15808@1792896324` | 同上 | `94c5cd9eb3fa825b…` |
| `v1_kernel_answers.jsonl` | `bench_out/answers.jsonl`（`f552a9266f97ae1a…`）同 `run_id` 的 6 条 answer | 同上 | `dc100adcb80a1067…` |

⛔ **为什么要冻结**：`/tmp/v1_direct_s80` 是第八十一轮的临时目录，`bench_out/` 是 gitignore
的本地台账。两者都不在仓库里 ⇒ 不冻结，这份读数就只能在"我这台机器上"复现。

⛔ **冻结做了什么**：从源文件里**逐行筛出**该 `run_id` 的记录，**一个字段没改**
（`json.dumps(record, ensure_ascii=False)`，字段序与源一致）。筛选脚本内联在当轮命令里，
没有落成脚本 —— 这是刻意：一次性的取数不该变成可复用的"造数据工具"。

## 二、`v2` 池（10 题 140 格）

**没有冻结**，直接读 `bench_out/runs.jsonl` 与 `bench_out/answers.jsonl` 的
`69666@1792912166` / `70620@1792912544` / `5425@1792919669` 三条记录。
理由：这三条记录就是 §45/§46/§47 用过的同三条，冻结它们会把 `bench_out/` 的内容
复制进仓库（约 500KB）。代价是：**`--pool v2` 只能在有本地台账的机器上复现** ——
所以 `run_tests.sh` 步骤 7k **只跑 `--pool v1`**。

## 三、三个日志

| 文件 | 命令 | 退出码 |
|---|---|---|
| `selfcheck.log` | `probe_select.py --self-check` | `0`（六组反证全过） |
| `report_v2.log` | `probe_select.py --report --pool v2` | `0`（裁决 `UNKNOWN`） |
| `report_v1.log` | `probe_select.py --report --pool v1` | `1`（裁决**有害**；`1` 是这条读数的正常取值） |

## 四、本轮没有做的事（明写）

- ⛔ 没有新的模型调用 ⇒ 不存在"新口径"，也没有新 `caliber_sig`。
- ⛔ 没有改 `mw/`、`run.py`、`bench/specgen/*.jsonl`（`git diff` 可验）。
- ⛔ 没有跑全量撤回态 161 条（本轮只新增一个 `bench/*.py` + 一个 `run_tests.sh` 步骤 + 文档，
  没有判据/题集改动）；只抽查了 `AZ44`。
