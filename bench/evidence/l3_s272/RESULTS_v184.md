# V184 结果：判据生成器**带槽类型** + 接记忆

- 轮次 l3_s272｜2026-11-17｜`qwen2.5:7b`（ollama `:11434/v1`）、`V1_LLM_TEMPERATURE=0`
- 预注册：`PREREG_v184.md` + `PREREG_v184_v2.md`（v2 在跑臂前落盘，记装置修正与分母）
- 读数：`readings_v184.json`（24 行）、`stage0_v184.json`、`arms_v184.log`
- 命令：
  ```
  python3 bench/build_g7d_seed.py --out-dir /tmp/v184_seed \
      --heldout bench/heldout/g7j_n4_heldout.jsonl --variant 7j
  python3 -u bench/evidence/l3_s272/stage0_v184.py
  V1_LLM_API_KEY=ollama V1_LLM_BASE_URL=http://localhost:11434/v1 \
  V1_LLM_MODEL=qwen2.5:7b V1_LLM_TEMPERATURE=0 \
  python3 -u bench/evidence/l3_s272/arms_v184.py
  ```

---

## 一、一句话

**槽形状不再是瓶颈（4 条 → 1 条），W3 第一次有非零判别力（0/8 → 1/8），
但过正确仍只有 2/8，且 5 条失败全是「选错算子」；记忆接上后**确实改对了槽值**
（`keep`：`tail_n` → `ellipsis_tail_n`），却没改变任何一格的 pass/bite。**

⛔ 不说「墙倒了」。⛔ 不说「模型变强了」——装置换了。

---

## 二、三臂读数（同题、同尺、同一进程）

| 臂 | 装置 | 记忆 | 可跑 | 过正确 | 咬错 | **判别力** | 准入 |
|---|---|---|---|---|---|---|---|
| `base` | V183 `templates.py` | — | 4/8 | 0/8 | 4/8 | **0/8** | 4/8 |
| `typed` | **`mw/critgen.py`** | — | **7/8** | **2/8** | 6/8 | **1/8** | 7/8 |
| `typed_mem` | 同 `typed` | `recall(prompt)` | 6/8 | 2/8 | 5/8 | **1/8** | 6/8 |

失败分类：

| 臂 | SHAPE（模型给的结构错） | UNREACH | OUT_OF_DOMAIN | SEMANTIC（结构对、关系不成立） |
|---|---|---|---|---|
| base | 4 | 0 | 0 | 0 |
| typed | **1** | 0 | 0 | **5** |
| typed_mem | **2** | 0 | 0 | **4** |

⇒ **base 的 4 条失败全是「inputs 写不成形」；typed 的 5 条失败全是「算子选错」。**
这就是本轮最重要的位移：**从「装置不会用」走到「关系选不对」**。

### 逐题（`P`=过正确、`B`=咬错）

| 题 | base | typed | typed_mem |
|---|---|---|---|
| k01_diff_tree | EMPTY_IFF_EQUAL `Px` | EMPTY_IFF_EQUAL `Px` | EMPTY_IFF_EQUAL `Px` |
| k02_encode_cookie | ROUNDTRIP `xx` | ROUNDTRIP `xB` | ROUNDTRIP `xB` |
| k03_unflatten | ROUNDTRIP `xx` | ROUNDTRIP `xB` | ROUNDTRIP `xB` |
| k04_to_ini | ROUNDTRIP `xx` | ROUNDTRIP `xB` | ROUNDTRIP `xB` |
| g7d2_dedupe_keep | TAIL `xB` | TAIL **SHAPE** | TAIL **SHAPE** |
| g7d3_fmt_amount | ROUNDTRIP `xx` | ROUNDTRIP `xB` | BOUND **SHAPE** |
| g7e1_parse_flag | BOUND `xB` | ROUNDTRIP `xB` | ROUNDTRIP `xB` |
| g7e2_truncate_tail | TAIL `xB` | TAIL **`PB`** | TAIL **`PB`** |

⇒ 过正确的 2 格是 `k01` 与 `g7e2`；判别力**只有 `g7e2` 一格**。

---

## 三、押注判定（六条）

| 记号 | 命题 | 结果 | 读数 |
|---|---|---|---|
| **A1** | `typed` 的形状类失败 = 0 | **✗** | SHAPE=**1**（`g7d2` 选 TAIL 却只给 1 个 type） |
| **A2** | `typed` 可跑 8/8 | **✗** | **7/8**（同上） |
| **A3** | `typed` 判别力 ≥1/8 | **✅** | **1/8**（`g7e2`） |
| **A4** | 出现 ≥1 条 `UNREACH` | **✗** | **0** 条（`g7d2` 本质是选错算子，但被 types 长度检查先拦下 ⇒ 记成 SHAPE，见 §五.4） |
| **B1** | `typed_mem` 在 4 道记忆题上 ≥1 题过正确 | **✅** | 1 题（`g7e2`） |
| **B2** | `typed_mem` 与 `typed` 在自足 4 题上逐题相同 | **✅** | 4/4 相同（⚠️ `k04` 的 `mem_chars=0` ⇒ 该格不构成证据，有效 3 题） |

**3/6**。核心命题 A3 成立，但它**不是**本轮最强的那条证据 —— 见 §四。

---

## 四、`g7e2` 那一格到底是什么（⭐ 本轮最关键的一段）

两臂都 `P∧B`，但**判据源码不同**：

| 臂 | `keep` 槽 | 判据 | 严格性 |
|---|---|---|---|
| `typed` | `tail_n` | `got.endswith(s[-n:])` | **松** —— `'…6789'` 也满足 |
| `typed_mem` | `ellipsis_tail_n` | `got.endswith('…' + s[-n:])` | **准** —— 正是惯例写的形状 |

⇒ **记忆在「判据源码」这一层产生了唯一的、方向正确的效应**：模型自己选的是 `tail_n`
（漏掉省略号），接上记忆后选中了 `ellipsis_tail_n`。

但它的效应**没有到达四格**：`partial` 的截断连尾 `n` 字符都不对，两种判据都能咬。
⇒ 追加口径（更严，跑前未预注册，只作为**补充读数**，不计入押注）：

| 口径 | `typed` | `typed_mem` |
|---|---|---|
| `disc`（过正确 ∧ 咬错） | 1/8 | 1/8 |
| **`disc` ∧ 槽值与惯例一致** | **0/8** | **1/8** |

⛔ 这一格是**单格**、且压线（纪律 91）。不能据此说「记忆有用」，
只能说「记忆能把一个槽值改对，且这是本轮唯一可归因的记忆效应」。

---

## 五、自踩（5 条）

1. **`run_typed` 用 `mem` 是否为空推断臂名** ⇒ `k04` 的 `recall` 返回 0 条 ⇒ 那一行被记成
   `typed`，第一次汇总出现 `typed` 11 行、`typed_mem` 5 行，而分母写死 8 ⇒ 打印出
   **`typed 8/8` 的假读数**。修正后是 **7/8**。
   ⚠️ 这不是「温度 0 的后端抖动」，是我的分类 bug —— 两次跑的唯一差别就是它。
2. **单参数输入初版包成 1-tuple 却 `fn(x)` 直传** ⇒ 8 题一起报
   `'tuple' object has no attribute ...`。若没查就先读，会把整轮读成「模型不会填」。
   （纪律 88 同型：先怀疑装置。）
3. **通用池会越界**：`to_ini` 要求顶层值是 dict、`fmt_amount` 要数值 ⇒ 越界输入让
   **正确实现**也失败。加 `legal_indexes()`（在目标实现上圈域，只问「能不能吃」）后
   `k04` 从 `2/3` 组收敛，`g7d3` 的 `str` 池整池判 `OUT_OF_DOMAIN`。
   ⛔ 圈域**不看**返回值对不对 —— 那是判别力，属收费那一半（纪律 66）。
4. **`validate()` 的检查顺序**：先判 `len(types) == n_args`、后判 `reachable()` ⇒
   `g7d2`（选 TAIL 但只给 1 个 type）被记成 `SHAPE` 而不是 `UNREACH`。
   押注 A4 因此**假阴**。修口径要单独立轮预注册，本轮只登记。
5. **前台跑撤回演练被杀 ⇒ 留下变异文件**：`python3 bench/withdraw_check.py AZ145a…`
   在前台被 SIGKILL（`finally` 没跑），仓库里留下一个**变异过的 `mw/critgen.py`**。
   下一次启动时驱动器自己报警并还原（`↩️ 启动先还原 1 个残留变异文件`）。
   ⇒ 门槛：撤回演练**必须在后台跑完**，且跑完要核 `git status` / 关键行。
   （这条正是 `withdraw_check.py` 文档里写的那个坑，本轮又踩了一次。）

---

## 六、STAGE0 量到的分母（⛔ 不是结论）

| 项 | 读数 |
|---|---|
| `arity` | k01=2（`prefix` 有默认值）｜其余一元，g7e2=2 |
| 记忆送达 | 4 道记忆题**均含自己那条**；自足题也各取回 1~4 条（装置不筛，部署同形） |
| 可行性矩阵 | 48 格中 **24 格结构不可达**；可达 24 格里 oracle 通过 **4**：`k01 EMPTY_IFF_EQUAL`、`k03 CONSERVE`、`g7d2 SUBSEQ`、`g7e2 TAIL` |
| 覆盖缺口 | `g7e1` 6 个算子**全不可达**（返回 `True/False/None`，库里没有「输出落在某域」的算子）；`g7d3` 需数值类型 + 金额 decode，6 算子均不可达 |

⇒ 这两题在**当前算子库**下**结构上必 0**，与模型无关。报告同时给两个分母：
**8 题：1/8**；**剔除 g7e1/g7d3：1/6**。

---

## 七、边界（⛔ 不许放大）

1. **装置变了**：`base`↔`typed` 是装置单变量，`typed`↔`typed_mem` 才是记忆单变量。
   ⛔ 不许把 4/8→7/8 记成「模型变强」。
2. 8 题、单模型、单次采样、温度 0。⛔ 不外推；换 14b 要单独立轮，且与 7b 不可相减。
3. `partial` 是题集自带的半对实现（V183 已登记），比模型错答更接近正确。
4. **判别力 1/8 是单格**，且其中一格（`typed`）的判据是松的 ⇒ 严格口径下
   `typed=0/8`、`typed_mem=1/8`。
5. 记忆是**人写、装置喂**的。本轮只证明「接上后能改对一个槽值」，
   ⛔ 不证明「系统自己会去找记忆」。
6. 判据准入闸（`metacrit.admit`）在 A 臂上 7/8、6/8 放行，**只作读数**，
   仍**没有任何循环路径调用它**（V183 §251.5 的状态未变）。
7. ⛔ 不说「V79 墙倒了」「自进化完成」。

---

## 八、下一刀（按可判性排序）

1. **arity 硬约束 + 一次重试**：`g7d2` 证明模型不会自己读「必填参数 1 个」——
   装置应把「TAIL 需要 2 个参数，本题只有 1 个」作为**回错**（而不是等 `types` 长度不符），
   并给它一次改选算子的机会。这是 V183 R 臂「校验+修一次」的正确形态（本轮证明回错要
   **带算子名**，不是只带槽形状）。
2. **检查顺序口径**：先 `reachable` 再 types 长度，单独立轮预注册。
3. **算子库补两类缺口**：`g7e1` 需要「输出域」（如 `OUTPUT_IN_SET`），`g7d3` 需要
   数值类型池 + 金额 decode。⛔ 补之前先量「补了之后 oracle 能不能过」——
   否则又是把不可达格混进分母。
4. **记忆的效应要穿到四格**：本轮只到判据源码。下一步应做成对设计
   （同题：有/无记忆 × 同一算子），把「槽值改对」直接映射成「pass 变化」的题找出来。
