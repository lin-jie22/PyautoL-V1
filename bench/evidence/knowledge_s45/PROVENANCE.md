# ★7p 量具补强轮（第四十三轮）PROVENANCE

日期：2026-10-20 13:05–13:40　HEAD 前置：`4ef77f1`（7n）
主张：规模层不比（4 题对 100+ 基准）；**测量有效性层**做出 Inspect 没有的
东西（三臂/安慰剂/撤回态/逐格取证），并补进它的四件机械长处。
完整登记与判读见 `DESIGN.md` §★7p。

## 产出物

| 文件 | 内容 |
|---|---|
| `../scorers/code_exec.py`（改动） | 沙箱补强：RLIMIT_CPU=6s / RLIMIT_FSIZE=1MB（全平台）+ RLIMIT_AS=512MB（**仅 Linux**，macOS setrlimit 实测抛错，如实登记）；cwd=独立临时目录（finally 清理）。打分协议与语义**零变更** |
| `../suite_manifest.py`（新增） | 考题完整性闸：build/verify，heldout/*.jsonl 逐文件 sha256 对照 `suites.manifest.json`（9 份，已落库）。改考题必须刻意重新 build —— 刻意的摩擦 = 功能 |
| `../evallog.py`（新增） | 合并 eval log：一个 evidence 目录的 `*_runs.jsonl` → 单文件 `evallog.jsonl`（每格 arm/rep/model/llm_of_record/per_task{score,why,elapsed_s}/mean/transport），对应 Inspect `.eval` 的最小对应物。transport 的 str(dict) 形态在此解析（7m-β 的坑） |
| `suites.manifest.json`（新增，库根） | 9 份 heldout 的 sha256 |
| `evallog_s42.jsonl` / `evallog_s43.jsonl` | s42/s43 各 9 格的合并日志（模型 qwen2.5:7b、全 0.2，与 probe_g7d 读数逐格一致） |
| `regression_scores.txt` | 沙箱补强回归：s42/s43 的 6 份冻结 H 臂交付原文在新打分器重放，**6/6 score=0.2 与历史逐格相等** ⇒ 打分语义未变 |

## 契约与撤回态

- 契约 AZ31–AZ35（520→**525**）：AZ31 打分器进程级生存性（墙钟+CPU 双炸弹
  都只判 fail、rc=0）；AZ32 FSIZE=1MB 截 2MB 落盘；AZ33 cwd 隔离；
  AZ34 manifest 逐一致且全覆盖；AZ35 evallog schema + str(dict) transport 解析。
- 撤回态 95→**100**（AZ31–AZ35 全部精确转红、无多红、还原后基线绿）。
- **AZ31 第一版撤回态红=无（当场被抓）**：`while True` 在 6s 被
  RLIMIT_CPU 的 SIGXCPU 先杀，走不到 TimeoutExpired 分支 ⇒ 原判据（run_one
  直调层）恒绿。改判据到打分器进程级、墙钟炸弹（sleep 30）走 TimeoutExpired
  分支后精确转红。教训：**两道防护共存时，撤哪道必须先实测"哪道先杀"**。

## provider 层（补强④，零代码）

`LLMConfig`（mw/config.py:117）本就是 OpenAI 兼容 env 驱动
（`V1_LLM_BASE_URL/MODEL/KEY`，default_factory 每次读 env）——
偷清单第四项**已经就位**，本轮不改代码，只在 evallog 显式落 `model`
字段（从 `report.candidate` 取证，如 `qwen2.5:7b`）。

## 如实登记的未做项

- **Docker 沙箱**：本机无守护进程；rlimit 子集可验证，Docker 化未做。
- **macOS 内存炸弹无 rlimit 防护**（RLIMIT_AS setrlimit 在子进程实测抛错）。
- heldout 出库（双盲精神的完整版）：未做，manifest 是中间态。
