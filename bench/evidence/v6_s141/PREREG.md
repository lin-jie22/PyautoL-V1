# V51 预注册 —— `finish` 成功之后能不能反悔（修法 A：交付闸）

冻结时刻：2026-11-03 12:05（+08）。**本文件写于任何 V51 读数之前。**
上游：MEMORY §五 第 1 项；DESIGN §116.9（V50 的三条判决）。
证据目录：`bench/evidence/v6_s141/`。

---

## 1 唯一变量与两臂

| 臂 | `mw/core.py` 的 `_gate_finish` |
|---|---|
| **A（对照）** | HEAD `972424e` 原样 |
| **B（处理）** | 闸里增加**第二问**：题面要求的产物不存在 ⇒ 打回 |

两臂用**两个独立目录**（`git archive HEAD` 解包），B 只覆盖 `mw/core.py`。
`diff -r` 必须**只有一个文件**（`mount_diff.txt` 落盘）。其余全同：
同一题面、同一 `sales.csv`、`qwen2.5:7b` / T=0 / `MAX_TOKENS=1024` / 宽 3 /
`cand_failover=1` / `allow-shell` / 各自冷启动 `V1_STATE_DIR`。

## 2 为什么押 A 而不是 B（V50 已给出的两条理由）

1. **A 复用已有判据与已有插线**：`vfy.check_delivery(goal, workspace, answer)` 是
   `verdict` 现在就在用的那一份实现（`core.py:2295`），`_gate_finish` 打回后
   自动走 `if not result.ok:` → `cand_failover` 让位（`core.py:1545`）。
   ⇒ **一处改动，两个后果**（打回 + 让位 + 世界模型 `ok=False`），不需要第二条插线。
2. **B（成功也让位）不修学习信号**：V50 §116.5 已实测「世界模型学到的正是编造是成功的」
   （C 臂预演命中率 1.0）。B 保持 `finish` 为成功 ⇒ 这条反向学习原样留着。
   ⇒ A 与 B **不是同一个实验的两个写法**，A 多修一条边；本轮只押 A，B 留作 V52 的对照。

## 3 判据的分母（三选一 + 理由）

| 项 | 选择 | 理由 |
|---|---|---|
| 真实任务 | **全跑**：两臂各 1 次 | V50 同一题面同一配置，单次 ~35s；这是唯一能回答 `verdict` 的装置 |
| 护能力闸 | **6 题 × 1 rep × 2 臂 = 12 格** | `expected_artifacts` 在单函数题上返回空（题面无 `*.md` 等 token）⇒ 按代码路径**不该触发**。此闸验的是"没乱开火"，不是能力分数 ⇒ 1 rep 足够；⛔ 不许把它写成 24 格或 96 格 |

## 4 押注（读数形态一起写死）

| # | 押注 | A 臂期望 | B 臂期望 | 判据 |
|---|---|---|---|---|
| J1 | 交付结论 | `FAIL` | **`PASS`** | `stats.delivery.verdict` |
| J2 | 闸真的打回了 | `verify.failed = 0` | `verify.failed ≥ 1` | 闸的失败计数（不是日志） |
| J3 | **产物判据**单独可见 | `verify.artifact_failed = 0` | `verify.artifact_failed ≥ 1` | 新增计数（与自检失败分列） |
| J4 | 让位真的发生 | `failover.used = 0` | `failover.used ≥ 1` | 已有计数 |
| J5 | 产物真的在 | `report.md` 不存在 | `report.md` 存在且合计正确 | 文件字节 + 与 `sales.csv` 真值比对 |
| J6 | 反向闸（不许乱开火） | 6 题均分 `m₀` | **逐题与 A 相同** | 12 格逐题比对；`outs=[]` ⇒ 闸不触发 |

**J6 的形态**：不是"B ≥ A − 0.05"，是**逐题完全相同**。
理由：这不是能力实验，是"机制该不该在此开火"的装置闸；出现任何差异都说明
`expected_artifacts` 在单函数题上误判（那本身是要修的 bug，不是允许的噪声）。

## 5 押错时怎么读（先写好，事后再写就是编）

| 现场 | 归因 |
|---|---|
| B 的 `verify.runs = 0` | **装置没通电**（闸根本没被调用）⇒ 整轮作废，不许解释成"修法无效" |
| `artifact_failed ≥ 1` 而 `failover.used = 0` | 闸通电了，**让位没接上**（`cand_failover` / `_produces` / 候选数） |
| `failover.used ≥ 1` 而 `report.md` 不存在 | 让位到了 `run_shell` 但那条命令没写成 —— 去看它的 `ok`/`error` |
| `report.md` 存在而 `verdict ≠ PASS` | `expected_artifacts` 与实际路径不一致（判据窗口问题） |
| B 的 `rounds` 变多而 `verdict` 仍 FAIL | ⛔ 不许记成"有进展"（V47/V50 各押错一次） |

## 6 边界（本轮不做）

- ⛔ 不改 `planner._SYS`、不改 `finish` 描述（V50 已量死）。
- ⛔ 不改 `limits`（P5 硬锚 `25`）、不改 `catalog()`（`catalog_sha` 必须不变）。
- ⛔ 不动 `check_delivery` / `expected_artifacts` 的**分类器**（它已被 §110/B 臂验证）。
- ⛔ 不跑 96 格（V50 实测 64 分钟/臂）；跑 12 格并如实登记为装置降级。
- ⛔ 一旦 A 臂读数与 V50 的 A 臂（`verdict=FAIL / rounds=1 / ext.calls=0`）不一致，
  先查装置（题面/配置/模型），不许把差异归给本轮改动。

---

## 10 POST-HOC（看到 B 臂日志之后才加，**不进 §4 主押注**）

B 臂日志（`/tmp/v51/out_b.txt`，已落盘为 `logs_b.txt`）在跑完 3 秒内就暴露了下一跳的死因：

```
🧪 自检未过（第 1/2 次打回） ⇒ 退回重写：题面要求的产物不存在：report.md
↪ 交付被打回 ⇒ 让位给候选 2/3（不重规划，直接用已有的）
↳ run_shell ok=False 原因=run_shell 缺参数 ['timeout_s'] 检查sales.csv文件是否存在
```

⇒ **闸通了、让位也通了，但被执行的那条命令在 `dispatch` 里就被拒了。**
根因不在本轮变量里：`run_shell` 的目录行写着 `timeout_s`、描述写「(可选)」、
handler 也写 `args.get("timeout_s", 30)`，而 `dispatch()` 把 `params` 的每个键都当必填。

因此追加 **臂 C = B + `ToolSpec.optional`**（`run_shell` 的 `timeout_s` 声明为可选）。
用途只有一条：**把"闸链通不通"与"工具契约撒没撒谎"分开**。

⛔ C 的读数**不许**支持"主押注 J1 成立"：J1~J6 只由 A/B 结算。
⛔ C 也不许被写成"修法 A 有效"的证据 —— 它是"两处缺陷串联"的归因。
✅ C 允许被写成：**如果 C 达成 PASS + 产物存在，则 A 的链条本身是通的**，
且 B 的失败可以精确归到 `dispatch` 的必填判定，而不是闸或让位。
