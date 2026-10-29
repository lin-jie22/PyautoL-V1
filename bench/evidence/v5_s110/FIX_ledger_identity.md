# 第 110 轮 · §0.5 第 2 步：语义层的身份是「动作」，不是「动作的载荷」

> 主线（MEMORY.md §零）：V1 的唯一目标是**会自己变强的系统**。
> 本轮动的不是尺子，是**累积循环的载体**：语义记忆里存的是什么。

---

## 1. 问题：`recall()` 检索出来的不是知识，是日志

`state/memory.db`（清理前，10-29 15:53）读数：

| 量 | 值 |
|---|---|
| `episodes` | **2 705**（10-17 02:51 → 10-29 15:53，12.5 天不断） |
| `facts` | **42** |
| 其中 `source=worldmodel` | **41** |
| 其中 `source=selftest` | **1**（唯一真知识） |

`facts` 按 `source × confidence × 时间`：

| source | confidence | 条数 | 时间跨度 |
|---|---|---|---|
| `worldmodel` | 0.40 ~ 0.50 | **34** | 10-17 02:51 → 10-17 05:34（**旧数据，现已不再产生**） |
| `worldmodel` | 0.70 | **7** | 10-17 11:13 → **10-29 15:53**（仍在产生） |
| `selftest` | 0.90 | 1 | 10-17 02:51 → 10-22 17:01 |

7 条 0.70 的原文形态（`hits` 是该文本被重复写入的次数）：

```
hits=206  动作 [answer finish int nint]：我预演「会成功」，实际失败。实测：[工具失败] 自检未通过：3/4 项未过：缺少 def solve(...)
hits=102  动作 [answer def dict elif]：我预演「会成功」，实际失败。实测：…1/4 项未过…
hits=101  动作 [answer def dict elif]：…2/4 项未过…
hits= 74  动作 [answer finish int nint]：…3/5 项未过…
hits= 37  动作 [answer def dict elif]：…1/5 项未过…
hits= 37  动作 [answer def dict elif]：…2/5 项未过…
hits= 14  动作 [ascii confidence encoding note]：我预演「会失败」，实际成功。实测：已存在(仅累加引用)
```

`hits` 合计 **571**。而 `episodes` 里 `kind=mismatch` 共 **628** 条，其中
`meta.mm_kind == "outcome"`（= `teachable`，唯一允许进语义层的那类）**571** 条 —— 两个数对上。

⇒ **571 次「学到了什么」的机会，产出的是 7 行带具体报错原文的流水账。**

`recall()`（`mw/memory.py:351`）**不过滤 source、不看 confidence**，只按 bm25 / hits 排序
⇒ 这 41 条只要 FTS 命中就会被塞进 prompt 当作「历史经验」。

---

## 2. 根因：两条，都在 `Mismatch` 里

### 2.1 身份取的是「动作的载荷」

```python
# 改前（mw/worldmodel.py:156）
key = " ".join(sorted(_tokens(self.action))[:4]) or "该动作"
```

而 `action` 的真实形态（`episodes.meta.action`）是：

```python
"finish({'answer': 'def flatten(d, prefix=\"\"):\n    out = {}\n    ...'})"
```

⇒ `_tokens` 把**整份交付物**切词，`sorted(...)[:4]` 取字典序前 4 个
⇒ key 由**答案内容**决定：`answer def dict elif` / `answer finish int nint` / `answer encoding finish open`。
同一类动作换一份代码就换一个身份。

### 2.2 文本抄的是「本次报错」

```python
# 改前
detail = " ".join(self.actual.split())[:90]
return f"动作 [{key}]：我预演「会{want}」，实际{got}。实测：{detail}"
```

那 90 字是**这一次**的自检清单（`缺少 def solve(...)` / `代码块语言标签是 'c'`）。
换一道题，这条「知识」一个字都用不上。

---

## 3. 改动（`mw/worldmodel.py`，+42 / −10 行）

```python
@property
def category(self) -> str:
    """动作的身份 = 最外层调用名。⛔ 不是它携带的载荷。"""
    head = (self.action or "").strip().split("(", 1)[0].strip()
    parts = head.split()
    return (parts[0].strip("'\"") if parts else "") or "该动作"

def as_heuristic(self) -> str:
    cat = self.category
    if self.kind == "outcome":
        if self.predicted_ok and not self.actual_ok:
            return f"动作 [{cat}] 存在乐观偏差：我预演「会成功」，实际失败 ⇒ 该动作的预演应先打折再采信"
        if (not self.predicted_ok) and self.actual_ok:
            return f"动作 [{cat}] 存在悲观偏差：我预演「会失败」，实际成功 ⇒ 该动作的预演结论偏保守"
        return f"动作 [{cat}] 的预演方向不可判定，仅登记该类别出现过失配"
    return (...)   # text 类分支原样不动（它本来就不进语义层）
```

**文本里刻意不写次数**：次数由 `commit_fact` 的 `hits` 承载。
若写进文本，`fingerprint` 每次都不同 ⇒ 每写一次新增一行（旧实现 `hits=206` 说明的正是「同一文本反复写」这条路径是通的）。

⇒ 语义层从「流水账」变成「校准表」：**每个（类别, 偏差方向）恒为一行，`hits` = 样本量**。

---

## 4. 读数：同一批真数据，新旧实现的对比

用 `episodes` 里 571 条 `mm_kind='outcome'` 的失配反算（`action` 与方向都取自落盘数据）：

| | 唯一身份 | 唯一语义文本 |
|---|---|---|
| 旧实现 | **3** | **3** |
| 新实现 | **3** | **2** |

新形态全文只有两行：

```
x 557  动作 [finish] 存在乐观偏差：我预演「会成功」，实际失败 ⇒ 该动作的预演应先打折再采信
x  14  动作 [note]   存在悲观偏差：我预演「会失败」，实际成功 ⇒ 该动作的预演结论偏保守
```

### ⚠️ 这里必须纠正我自己先写错的一个判断

我第一版口径把 **57 条 `mm_kind='?'`** 的旧数据也混进 outcome 组，
读出「22 个唯一身份 / 27 行文本」，据此写了「行数爆炸是主因」。

按 `learn()` 的真实准入（`teachable` = `kind=="outcome"`）重算 ⇒ **旧实现也只 3 行**。
**「行数爆炸」不成立，该判断已撤回。** 真主因是 §2.2 的**不可迁移**。

库里现有 7 行（而非 3 行）是**历次代码版本漂移**留下的历史堆积，不是单次运行的产物。

---

## 5. 判据与反证

### 5.1 AZ96（`tests/contract_test.py`，第 589 条契约）

```
① 同类别、同方向、载荷完全不同 ⇒ 提炼文本必须逐字节相同
   （⇒ 严格蕴含「不许抄本次报错」：只要含 actual，两份不同载荷必然不同 ⇒ ① 先红）
② 不同类别 ⇒ 文本不同（防 category 退化成常量）
③ 走真 DB：同类别失配两次 ⇒ facts 只 1 行、hits == 2
```

### 5.2 两条撤回态，各咬一处（**实测**，不是推断）

`bench/withdraw_check.py` 新增 AX5 / AX6，并用等价 monkeypatch 定向演练：

| 变异 | ① 载荷无关 | ② 类别区分 | ③ 单行 hits2 |
|---|---|---|---|
| 现状 | ✅ | ✅ | ✅ |
| **AX5** `category` → 乱序词袋 | ❌ | ✅ | ❌ |
| **AX6** `category` → 常量 `"该动作"` | ✅ | ❌ | ✅ |

- AX5 让 ①③ 红（换身份 ⇒ 载荷一变就换行 ⇒ 不止 1 行）；
- AX6 **只**让 ② 红 —— 它是 AX5 的**对立方向**（太碎 vs 太糊），两个病必须各有一条。
- AX1（`learn()` 不再 `commit_fact`）让 ③ 红（0 行）。

⚠️ **AX5/AX6 只做了定向演练，未进 175 条全量批次**。README/site 的文案已按此改写，
⛔ 不写「177 条逐条跑过」这种假话。

---

## 6. 生产库清理（可回滚）

| 动作 | 读数 |
|---|---|
| 备份 | `/tmp/v1_s110_memory_before_purge.db`（4 915 200 字节，sha16 `81b3359ebfe7e9ae`） |
| 导出待删内容 | `bench/evidence/v5_s110/legacy_facts_dump.jsonl`（41 条 / 17 052 字节，含 id/key/text/confidence/hits/ts） |
| 删除 | `facts_fts` 41 行 + `facts` 41 行（无 trigger，故 fts 手工同步） |
| 清理后 | `facts` **1**（`selftest`：`python 写文件时应显式指定 encoding=utf-8…`，conf 0.9 / hits 24）；`facts_fts` 1；**`episodes` 2 705 一条未动** |

⇒ 删的是**我自己造的日志**，不是用户数据；`episodes`（真情景记忆）完整保留。

---

## 7. 自踩（三条）

1. ⚠️ **口径错**：第一版把 57 条 `mm_kind='?'` 混进 outcome 组，读出「22 身份 / 27 行」，
   据此下了「行数爆炸」的判断。按真实准入重算是 **3 行**，判断撤回。
   ⇒ 与 §71.9 / §79.8.1 / §79.8.4 同族（第 5 次）：**拿自己临时算的数当读数**。
   这次修法仍是同一句：**先把它写成能自证的读数，再下判断**。
2. ⚠️ **判定式写错，把「有牙」读成「无牙」**：演练里写
   `red = not any(old[k] for k in ("载荷无关", "不抄实例"))`，
   而 `不抄实例` 在 AX5 下仍为 True ⇒ `any` 为真 ⇒ 打印「检测器无牙」。
   实际 ① 已红。⇒ **判据的聚合方式本身也会骗人**；逐项印出来才对。
3. ⚠️ `conn.rowcount` 不存在（cursor 才有）：第一次删除脚本在 `commit` 前抛异常
   ⇒ 数据未提交、库未变（已核实「动手前 42 条」）。⇒ 破坏性脚本必须先打印「动手前」状态。

---

## 8. 下一步（V19 的前置已完成一半）

- ✅ ① 语义层不再存日志（本轮）
- ✅ ② 生产库旧噪声已清（41 → 1）
- ⏳ ③ **V19 累积对照**：A `--fresh` vs B `--memory-dir D`，看第 N 轮 `M` 是否超 `mw/measure.py` 的 `noise_floor`。

⛔ V19 判负条件（先写死）：**共享记忆臂的 `M` 不升 ⇒ V1 没有累积能力，「AGI 路径」这个说法不成立。**
⚠️ 但 V19 的判决只有在 ①② 之后才可归因 —— 否则「记忆里全是垃圾」会伪装成「记忆没用」。
