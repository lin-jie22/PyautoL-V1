# noise_s72 —— 「噪声下限必须按 (臂, 口径) 在进程之间实测」的证据（第七十三轮，2026-10-24）

## 这一目录回答的三个问题

1. **同一个口径下、同一份代码，跑两次分数能差多少？**
2. 这个答案**能不能从证据里复核**，而不是靠人记「我跑了几次」？
3. 「0」到底是「测到它们一样」还是「我根本没测」？

## 读数（T=0，本机 ollama `qwen2.5:7b`，6 题 `specgen`，每次运行 `--repeat 1`）

| 臂 | 独立运行 | 各次 mean | 轮间极差 | 每次墙钟 |
|---|---|---|---|---|
| 裸模型 `llm-direct` | 5 | 0.2857 ×5 | **0.0000** | 43 / 43 / 43 / 50 / 59 s |
| 内核 `llm`（`--no-external`） | 5 | 0.5952 ×5 | **0.0000** | 175 / 177 / 180 / 206 / 241 s |

两条臂同口径 `39e815e18165b03b`；逐题（sg01–sg06）极差全部 0.0000。
墙钟差 4 倍，分数一位不动。

## 文件

| 文件 | 是什么 |
|---|---|
| `pool_direct_r1.jsonl` | 裸模型臂的 5 次独立运行（原始 `runs.jsonl` 记录，含 `run_id`） |
| `pool_kernel_r1.jsonl` | 内核臂的 5 次独立运行 |
| `bracket_report.log` | 从上面两份**重算**的读数（`noise_floor.report()`，不碰模型） |
| `probe_noise_floor.log` | 自检 **16 项**（P1–P12c，含反证组与两个真实池子的读数） |
| `withdraw_ALL_148.log` | **全量** 148 条撤回态逐条结果（150 次完整契约 = 148 次撤回 + 基线 + 还原，81 分 28 秒）：`R13 通过`，还原后基线 `568/0` |
| `freeze_direct.log` / `freeze_kernel.log` | 冻结时的输出（含拒收统计与那一池的读数） |
| `llm-direct_r1…r4.log` + `llm_r1…r5.log` | 9 份 `bench/run_noise_floor.sh` 单次运行的完整日志 |
| `noise_floor.py` / `probe_noise_floor.py` / `freeze_noise_pool.py` / `run_noise_floor.sh` | 产生上面读数时的源码快照 |
| `FILES.sha256` | 本目录所有文件的指纹 |

核验：`cd bench/evidence/noise_s72 && shasum -a 256 -c FILES.sha256`

⚠️ 两处必须说清楚，否则等于造假：

1. **裸模型第 1 次是冒烟跑**（先确认链路通），日志没有单独留档。
   它的读数在 `pool_direct_r1.jsonl` 里（`run_id=64063@1792816728`）。
   不补齐、不重跑 —— 重跑会改变池子，而池子是要被契约（AZ80）锚定的。
2. **`freeze_*.log` 是重推的**：冻结脚本是确定性的（按 `runs.jsonl` 原始行序保留），
   重推产物写 `/tmp` 后与已入库的两份池子**字节相同**
   （`direct` = `4c31d88b…55df`、`kernel` = `ddc5590d…0da8`，与 `FILES.sha256` 对得上）。
   这不是「事后补一份想要的日志」，是「同一输入同一输出」。

## 怎么重算（不需要模型）

```bash
python3 bench/probe_noise_floor.py     # 16 项自检 + 两个池子的读数
python3 tests/contract_test.py         # AZ78–AZ80
python3 tests/bench_test.py            # 锚层 97/0
```

## 怎么重新跑批（需要 ollama）

```bash
export V1_LLM_BASE_URL=http://localhost:11434/v1 V1_LLM_API_KEY=ollama V1_LLM_MODEL=qwen2.5:7b
bash bench/run_noise_floor.sh llm-direct 5
bash bench/run_noise_floor.sh llm 5
python3 bench/freeze_noise_pool.py --arm '<臂标签>' --sig <sig> --out <pool.jsonl>
```

`run_noise_floor.sh` 固定 `--repeat 1`，且**一旦某次的口径与第一次不同就停**：
「跑了 5 次但中途换了尺子」的池子不是证据。

## 边界（不许读大）

- 一台机器、一个 7B、一套 6 题纯函数题、`T=0`。远程 API 的批处理 / 缓存 / 版本漂移
  都可能带来真实的轮间抖动 —— **这里 0 不等于别处 0**。
- 5 次的极差理论上仍是下偏估计；本轮能用的是更强的事实：**5 次逐位相同**。
- 本轮**没有**动摇 §35.1 那条合法比较（裸模型 0.2857 → 内核 0.5952，Δ=+0.3095）。
  ⛔ 仍不能说「内核结构更好」：等预算对照（让裸模型也调 2–3 次）没做。
- 本轮**撤回**了 §35.3③ 的量化结论（「噪声下限被系统性低估 0.26 量级」）。
  撤回依据是口径逐字段对撞，见 DESIGN §36.1。
- ⛔ 读数里那个 `0.0000` **不代表分辨率变好了**：一条用例 = 1/42 ≈ 0.0238，
  一道题 0.1667，分布是 0/7 与 7/7 的阶跃。当前瓶颈是**刻度**，不是噪声。

## 已知的自踩（写在这里，免得下一次重犯）

- `tests/contract_test.py` 一度被写成**两份**（16248 行、`check(RS` 1122 次），
  症状全是假的，最后以沙箱 `PermissionError: EEXIST` 收场。见 DESIGN §36.5④。
- 断言失败详情串里 `{None:.4f}` 会让整份契约崩在打印理由那一行（连汇总行都没有）。
- `report()` 原来用 `sorted(...)[0]` 点名「最不稳的一题」：全 0 时会印
  「最不稳的是 sg01」，并列时只挑一个 —— **把 0 读成「某题在抖」**。
  现在分三档（全 0 / 并列 / 唯一），P12a–P12c 各带反证：
  改回旧写法时 P12a 与 P12b 当场转红（实测，不是推断）。
