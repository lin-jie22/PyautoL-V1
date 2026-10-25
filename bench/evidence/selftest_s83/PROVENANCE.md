# 第八十三轮证据来源（`selftest_s83`，§46）

## 这是什么

`bench/probe_selftest.py` 在**内核外**跑的一次预演：**自检能不能判对错**。
只量「检测」（能不能看见错），**不量**「修复」。

⚠️ 本轮 `mw/`、`run.py`、题集**零 diff**。这里没有任何内核读数，
也不能被读成"自检有害/有益"——它只回答"检测面有没有信号"。

## 口径（缺一项结论就不能复现）

| 项 | 值 |
|---|---|
| 模型 | 本地 ollama `qwen2.5:7b`（`http://127.0.0.1:11434/v1`） |
| 温度 | `0.0` |
| 上限 | `max_tokens=900`（A/B/C 三臂），**`2400`（仅 c2 补跑，另一口径）** |
| 题集 | `bench/specgen/tasks_v2.jsonl`，`suite_sha=d6b7ed14f56a…`，10 题 × 14 = 140 格 |
| 零点 | §45 的三次真实运行：`69666@1792912166` / `70620@1792912544` / `5425@1792919669` |
| 零点来源 | `bench_out/answers.jsonl`（内核真交的答案，**不是造的**） |
| 配对 | 3 次运行 × 10 题 = **30 对**，总丢格 **164**（40 + 60 + 64） |
| `MIN_ASSERTS` | 4（低于此判「没牙」，不进分母） |
| 提示词 | 原文在 `summary.json` 的 `prompts`（`system` / `a` / `b` / `c`） |

⚠️ `max_tokens` 是**声明的**，不是记录自带的：产生 A/B/C 原始记录时它是代码里
写死的 900，当时没有写进 `raw_*.jsonl`。`--recompute` 用 `setdefault` 补上这个值。
c2 的记录自带 `2400`。

## 复现命令

```bash
# 量具自证（零模型、零网络）
python3 bench/probe_selftest.py --self-check

# A/B 两臂（60 次调用）
V1_LLM_BASE_URL=http://127.0.0.1:11434/v1 V1_LLM_API_KEY=ollama \
  python3 bench/probe_selftest.py --arms ab

# C 臂（30 次调用）
V1_LLM_BASE_URL=http://127.0.0.1:11434/v1 V1_LLM_API_KEY=ollama \
  python3 bench/probe_selftest.py --arms c

# 零模型重判 / 断言级四格
python3 bench/probe_selftest.py --recompute
python3 bench/probe_selftest.py --cross
```

## 文件

| 文件 | 内容 |
|---|---|
| `raw_ab.jsonl` | A/B 两臂 60 条回答**全文**（不截断，含 `text` 与 `text_sha`） |
| `raw_c.jsonl` | C 臂 30 条回答全文 |
| `run_ab.log` / `run_c.log` | 现场日志（含每对的 `真判别/测试错` 打印） |
| `summary.json` | 汇总 + **提示词原文** + `max_tokens` + 截断清单 |
| `c2/raw_c.jsonl` | C′ 补跑（`max_tokens=2400`）**只跑了 1/12**，第 2 对超时中止 |
| `run_c2.log` | C′ 现场日志（含超时栈） |

量具本体：`bench/probe_selftest.py`，sha256 = `65b073da2be2692d557c36858ef4e1b898a7e864f3fba6336fc94b8c76cd72d1`。
⚠️ 这份哈希对应的是**跑完之后**的版本（含 46.6/46.6b 那两处事后修口径）。
跑 A/B/C 时的版本比它少了 `_split_flat`（能拆就拆）⇒ 原始 `raw_*.jsonl` 里的
`n_units`/`flag` 是**写入时**的判定，`summary.json` 里的数是 `--recompute` 用最终版本重判的。
**这两者不一致是刻意的**：回答原文是证据、只读；判定口径可以修，但必须留痕。


## 三处必须知道的偏离（都已写进 DESIGN §46）

1. **事后补了 oracle 交叉判据**。预注册里没有它；烟囱跑完 1 对 × 2 臂就发现
   "模型自己算错期望值"会被记成"检测成功"，于是补了「同一断言在 oracle 上也跑一遍」。
   ⇒ 这是测量装置，**读了 oracle**，绝不许接进内核。
2. **事后改过两次抽取口径**：`_strip_scaffold`（删模型的 `def f(x): pass` 脚手架）、
   `_split_flat`（能拆就拆，避免块内一条写错盖住整块）。两次方向都对假说**有利**。
3. **12 对「没牙」是生成退化**（复读同一输入列表），不是能力读数；提高上限救不了
   （c2 实测）。它们被排除在分母外 ⇒ C 臂的 57.7% 是**上界**。

## 未做的事（留痕）

- 没量**修复**（Stage R）。理由写在 §46.7：检测的选择性是 0，修复乘 0 还是 0。
- 没跑撤回态**全量**（本轮没动契约/撤回态，只抽查了 `AZ44`/`AZ91`/`AZ76`）。
- C′ 只跑了 1/12（第 2 对超时中止），所以只能说"提高上限不解决问题"，
  **不能**说"12 对全部如此"。
