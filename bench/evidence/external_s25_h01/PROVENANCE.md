# external_s25_h01 —— 2x 档补测（第二十六轮，2026-10-18）

## 这是什么、不是什么
- **补测一格**：只补 §16.23 2x 档 OFF 臂缺的 `h01_int_to_roman`。
- **不是**原实验重跑；原证据 `bench/evidence/external_s24_2x/` 冻结，一个字节未改。
- 事前登记：DESIGN §16.25（R1–R5 判据 / P1–P4 预测，跑之前写死）。

## 口径（与 external_s24_2x 逐字一致）
| 项 | 值 |
|---|---|
| 题集 | `--suite-dir bench/heldout`（7 题，不裁题；suite_sha 应为 8a052e6cd79bc6545a3ae39f771c0b97） |
| 档位 | `--budget 2`（2x） |
| 臂 | OFF：`--no-external` + `V1_EXTERNAL_ANSWER_TOOL=""` |
| 模型 / 温度 / max-tokens | qwen2.5:7b / T=0 / 1024 |
| 隔离 | `--fresh`，独立 V1_STATE_DIR / V1_BENCH_DIR（不共享记忆目录） |
| 次数 | 每臂 1 次，不重试 |

## 版本臂
- A：`66b9e41`（与原 2x 实验同版本）—— worktree `/tmp/v1s25a`
- B：`849d8ad`（当前 HEAD，含第二十五轮墙钟修复）—— **仅当 A 的 h01 仍未量到时才跑**

## 原实验的一个事实（本轮核对时发现，如实登记）
`external_s24_2x/ho_off.jsonl` 是**两次部分跑**拼成的：
row0 只含 h01–h03（attempted=3，h01 skipped_no_answer），row1 只含 k01–k04。
⇒ 原实验本身就被 361s 事故打断过一次。本补测的 R3（其余 6 题逐题相同）
分别对这两行核对。

## 运行记录（实测，2026-10-18）

| 臂 | 命令 | 起止 | rc |
|---|---|---|---|
| A（66b9e41） | `V1_OUT=/tmp/v1_s25_h01 bash bench/run_h01_supplement.sh A /tmp/v1s25a` | 19:09:25 → 19:20:36（667.2s 合计） | **1**（h01 TimeoutError 后继续跑完其余题） |
| B（849d8ad） | `V1_OUT=/tmp/v1_s25_h01 bash bench/run_h01_supplement.sh B /Users/linshaojie/V1` | 19:20:56 → 19:29:40（520.9s 合计） | **0**（全程无中断） |

两臂 `caliber_sig` 与原实验逐字一致：`eead8a6f39829832`（h01–h03 行）/ `4167032f974f1e55`（k01–k04 行）。

## 读数与判读（按 §16.25 ⑥ 的事前规则，命中"A 失败、B 得 S"分支）

| 题 | 原实验 OFF | 臂 A | 臂 B | 原实验 ON |
|---|---|---|---|---|
| h01_int_to_roman | **未量到**（TimeoutError） | **仍未量到**（TimeoutError，P2 ✔） | **0.0000**（诊断读数） | 1.0 |
| h02_merge_ranges | 1.0 | 1.0 ✔ | 1.0 ✔ | — |
| h03_eval_rpn | 1.0 | 1.0 ✔ | 1.0 ✔ | — |
| k01_diff_tree | 0.8 | 0.8 ✔ | 0.8 ✔ | — |
| k02_encode_cookie | 0.8 | 0.8 ✔ | 0.8 ✔ | — |
| k03_unflatten | 0.8 | 0.8 ✔ | 0.8 ✔ | — |
| k04_to_ini | 1.0 | 1.0 ✔ | 1.0 ✔ | — |

- **R3 成立**（A、B 各自与原实验逐题相同；P1 ✔）。
- **B 的 h01 = 0.0 是"修复版本上的诊断读数"**，不是原实验的格：原实验 h01 格仍记"未量到"，
  **2x 主结论保持 UNKNOWN**（§16.25 ⑥ 事前写死，不许事后改判）。
- 诊断含义（只陈述、不认领）：OFF 诊断 0.0 vs ON 1.0 提示外部能力在 2x 上对 h01 仍可能有效，
  但两臂来自**不同版本**（且 OFF 的 0.0 有一部分是端点对这道题就是慢），⇒ **不许并入原 verdict**。
- P3 ✔：B 无 362s；`19:24:13 ⏱ 墙钟被重试链挡住，但还剩 44s ⇒ 转向收尾` → 交付 488 字符 →
  `■ closed`（第二十五轮修复按设计工作）。
- P4 ✔：S∈{0,1} 二值，实测 0.0。
- B 的 h01 失败细节（如实）：前两次尝试在 120s 上限处超时（`wall_shortened=2`，端点对该题就是慢），
  第三次被墙钟拒绝（`wall_refused_retry=1`），收尾交付的是**残片** ⇒ 5 条 assert 全部
  `SyntaxError: invalid syntax` ⇒ 0.0。装置不再炸 ≠ 这道题能答对。

## 证据文件（sha256）

```
21e16b429c8cac0c6e3e8247c355a1db222158366df5e8936e3385f92b4d718a  ho_off_A_runs.jsonl
89d85b0a43b1a2246227ebb867488d6d2f9e99fd64eb2ca2273f89e21f8068bb  ho_off_B_runs.jsonl
71223e47afb8a7f7147ca454b6f905738fb12e77aa97005cac662a2b3911ae7c  ho_off_A_out.log
27fe2b0816870962b5f3b751f5a347028bc8955912dbe367bc7f18988ccf2af6  ho_off_B_out.log
```
