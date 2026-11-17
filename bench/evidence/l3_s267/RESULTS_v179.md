# RESULTS V179（STAGE0）—— 「修复轴消费」在现语义下是**空操作**，机制已定位

**预注册**：`PREREG_v179.md`（18:47 落盘，早于首跑 18:57）+ `PREREG_v179_v2.md`（19:04，追加 P7/P8）
**读数**：`readings_stage0_v179_run1.json`（P1~P6）、`readings_stage0_v179_run2.json`（P1~P8）
**装置**：`probe_v179_stage0.py`（零模型、零网络、真尺子子进程）
**汇总**：run2 **5 PASS / 3 FAIL**（P3 判负 = 押注被否证；P5/P6 判负 = 我把判据写错了）

---

## §1 押注的回答

| 押 | 内容 | 读数 | 判决 |
|---|---|---|---|
| **A** | 分数级 label 与行为级 label 在 ≥1 格不同 | **5/6 格不同**（5 格 COARSE vs INJECTIVE；m02 两层都 COARSE） | ✅ 成立 |
| **B** | ≥1 格「行为级 claim 触发而分数级不触发」 | **0 格**（6 格两级 claim 全为 `None`） | ❌ **不成立** |
| **C** | 若 B=0 ⇒ 消费是空操作 | 轴不同的格 **0/6** | ✅ 成立 |

⇒ **这一刀砍不下去**：把行为级读数接进修复轴，轴一个都没变。

## §2 为什么不变（机制，P7 钉住）

P7 的构造池（3 实现、同分 0.25、行为两两不同）：

```
行为级  CONSTANT  claim=BEHAVIOR_UNRESOLVED  轴=('MODEL','RULER')
分数级  CONSTANT  claim=SCORE_UNMOVABLE      轴=('MODEL','RULER')
```

**两个 claim 名字不同，轴却相同** —— 因为 `repair_axes()` 把两条 claim 都映射到
同一个 `RULER`。轴的值域只有三个名字（`CRITERION`/`MODEL`/`RULER`），
它回答「该动哪一层**大件**」，不回答「**哪一层的尺子坏了**」。

⇒ 行为层多出来的那点信息（「分数分辨不出行为」vs「分数分辨不出实现」），
在轴这一步**被压掉了**。这不是接线没接好，是**轴的语义粒度不够**。

## §3 真实池为什么连 claim 都不触发

6 格逐格：

| 格 | 分数级 | 行为级 | 池 n | 说明 |
|---|---|---|---|---|
| m01_records | COARSE | INJECTIVE | 4 | 行为能分开，分数不能 |
| m02_vfs | COARSE | COARSE | 5 | 两层都粗（V177 已点名） |
| m03_tiered | COARSE | INJECTIVE | 4 | |
| s01_kv_expiry | COARSE | INJECTIVE | 4 | |
| s02_limiter | COARSE | INJECTIVE | 4 | |
| s03_tx_journal | COARSE | INJECTIVE | 4 | |

`claim` 只在 `label == CONSTANT` 时触发（V174/V177 同款），而**真实池极少落到
CONSTANT —— 只要池里有一个 oracle@1.0 加一个 partial，就已经是 COARSE**。
⇒ 「分辨力不足」这个更常见的病，在现判据里**不是「做不到」**，因此不进 `esc`、
不给轴、不产生任何动作。这正是 B=0 的结构原因。

## §4 我局把判据写错了两处（登记，⛔ 不删）

1. **P5** 断言「同分异行为池 ⇒ 分数级 claim 不触发、轴为空」。
   实测：同分池（0.25×3）分数级当然也是 `CONSTANT` ⇒ `SCORE_UNMOVABLE` **会**触发；
   而 `axes_of()` 传的 reason 是 `STUCK_MEANS`，它本身就给 `MODEL`
   ⇒ 轴永远不空。**我把「只由 claim 决定的量」当成了「轴」，忘了 reason 也是输入**
   （纪律 63 同型：判「数值相同」时原语也是判据）。
2. **P6** 断言「无 vec ⇒ 轴空」。同上，`STUCK_MEANS` 恒给 `MODEL`。
   正确的可判形态是 P8（缺席被计数 `n_no_vec==3`）—— P8 过。

两处都不是世界不对，是我把装置的问法写错了。按纪律 89：原样留档，另立 P7/P8。

## §5 顺带发现的真缺口（⛔ 不许当成已解决）

`behavioral()` 对「没有 vec」和「vec 只有 2 条」给的是**同一个** reason（`TOO_FEW`）。
区分只能靠 `beh_pool_from_rows` 的 `counts`（`n_no_vec` / `n_kept`）。
⇒ 谁只读 `behavioral()` 的返回、不读 counts，就会把「没量到」读成「样本少」。
本轮不修（改 `behavioral()` 的 reason 词表要动 AZ139 锚，另立一轮）。

## §6 边界

- ⛔ 一行产品代码都没改（`mw/` 零改动）：本轮只量，不改判据、不加目标类型。
- ⛔ 不说「消费做完了」。相反：本轮证明**按现在的轴，消费无处落地**。
- 契约/锚层/撤回态计数不变（727/0、97/0、295）—— 没有新增断言，也就没有新增撤回态。
