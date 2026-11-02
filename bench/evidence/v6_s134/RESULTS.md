# V44 RESULTS —— 「状态说实话」：交付物加一道产物验收（§110）

日期 2026-11-02。预注册 `PREREG.md`（**写在任何一行实现之前**）。
代码：`mw/verify.py`（新增 `expected_artifacts` / `check_delivery`）、
`mw/core.py`（`Outcome.verdict` + `_finish` 计算 + `_prime_counters`）、
`run.py`（`cmd_run` 的退出码改由 verdict 决定）。
契约 **AZ107**（606→**607**）+ 撤回态 **AZ107**（192→**193**）。

---

## 一、判决：**两个方向的谎都堵住了，而且是用同一个词堵的**

| 现场 | `status`（过程量） | 改之前用户看到的 | 改之后第一行 | 产物 |
|---|---|---|---|---|
| A：不给 shell | `done` | 「done」⇒ 退出码 0 | **`■ 验收 FAIL（产物 0/1，缺 report.md）｜过程 status=done`** ⇒ 退出码 **1** | 不存在 |
| B：给 shell | `stalled` | 「stalled」⇒ 退出码 1 | **`■ 验收 PASS（产物 1/1）｜过程 status=stalled`** ⇒ 退出码 **0** | 44 字节，内容正确 |

⭐ 这两行是**同一份代码、同一个模型、同一个题面**，唯一变量是 `--allow-shell`。
改之前：A 一个字节都没产出却报"成功"，B 产物全对却报"失败"。
改之后：两个都说真话，**而且真话的顺序是"结果在前、过程在后"**。

## 二、离线八景（真 `Core`、零模型、隔离状态目录）

`p1_probe.txt`（**同一份代码两次跑 sha 相同** `a8dbe298…`，可复算）。

| 景 | 造的场景 | 预测 | 实测 |
|---|---|---|---|
| S1 | 题面要 `report.md`，文件不存在，有答案 | FAIL | ✅ FAIL |
| S2 | 同上，文件存在 | PASS | ✅ PASS |
| S3 | 题面无文件 token，答案非空 | ANSWER | ✅ ANSWER |
| S4 | 题面无文件 token，答案空 | EMPTY | ✅ EMPTY |
| S5 | 题面要 `a.md` **与** `b.json`，只有 a.md | PARTIAL | ✅ PARTIAL |
| S6 | 题面只有输入 `sales.csv`（存在），无写动作 | ANSWER | ✅ ANSWER |
| S7 | `status=stalled` + 产物正确（**复现 V43 的 C 臂**） | PASS | ✅ PASS |
| S8 | 空题面 + 空答案 | EMPTY | ✅ EMPTY |

⚠️ **S5 第一次是红的**（实测 PASS），详见 §5 自踩①。

## 三、真实两臂（`logs_a_noshell.log` / `logs_b_shell.log`）

| 项 | A（`--allow-shell` 缺省） | B（`--allow-shell --budget 2`） |
|---|---|---|
| 步数 | 4 | 5 |
| 状态 | `done` | `stalled` |
| 产物 | **无** | `report.md` 44 字节（华北 2700 / 华南 2000 / 华东 700） |
| 验收 | **FAIL(0/1，缺 report.md)** | **PASS(1/1)** |
| 退出码 | 1 | 0 |

⛔ A 臂最后交出去的是 pandas 代码片段（`pd.read_csv('sales.csv')` …）——
**代码不是产物**。改之前这一格被记成 `done`，也就是"成功"。

## 四、退出码六落点（`p2_exitcode.txt`，真调 `cmd_run`）

| status | verdict | 退出码 |
|---|---|---|
| done | FAIL | 1 |
| stalled | PASS | 0 |
| done | PASS | 0 |
| done | ANSWER | 0 |
| done | EMPTY | 1 |
| stalled | PARTIAL | 1 |

⛔ 只测 FAIL 那一格是不够的：一个"永远返回 1"的实现也能过。

## 五、自踩（照实登记）

1. ⭐⭐⭐ **12 字符窗口在并列列举上不成立**。`请生成 out/a.md 与 out/b.json 两个文件。`
   ⇒ `out/b.json` 前面只有「 与 」⇒ 被判成**输入** ⇒ 分母只剩 1 个产物
   ⇒ 少写一个文件也会读成 `PASS`。
   **预注册押 PARTIAL、实测 PASS ⇒ 按纪律改分类器、不改预测**（加了"并列传递"：
   两个 token 之间只有连接词时，产物身份向后传递）。
   ⛔ 没有把窗口从 12 放大到 40 —— 那一定会盖到「读取 a.csv，生成 b.md」里的 `a.csv`，
   把输入算进分母，"零产物"就会被读成 `PARTIAL`（V43 B 臂那个谎的镜像）。
2. ⭐⭐ **`_finish` 不能单独驱动**：探针一连撞两个 `AttributeError`
   （`failover_stats` → `expand_stats`）。病根不是探针 —— 是**单轮计数器只建在
   `run()` 里**，而 `_finish` 是收尾的唯一出口。修法是把单轮计数器的初始化收进
   `_prime_counters()` 一处，`__init__`/`run()`/探针共用。
   ⛔ 没在探针里手工补赋值：AS20 那次的教训就是"探针自己把洞挡住了"。
3. ⭐ **AZ107 第一版自己是错的**：五值表里 PASS 那一格拿"要 `report.md`"的题面
   去核一个只放了 `a.md` 的目录 ⇒ 必然 FAIL。**是测试的错，不是内核的错**，
   但它长得跟"机制坏了"一模一样 ⇒ 改了题面而不是改判据。
4. ⭐ 计数同步漏了两处（`quickstart.sh` / `site/index.html`），AZ44 当场红；
   改文档不改核对器。**另外**：第一次用 `sed` 式全量替换把 `DESIGN.md` 里的
   **历史读数**（§109 的 `605→606`）也改成了 607 —— `check_counts.py` 明确
   **不扫** DESIGN.md（它是历史台账），已还原。⛔ 许撤不许藏，也不许改历史。

## 六、负结果（预注册 §6，`p4_v8.txt`）

v8 六道题（`bench/specgen/tasks_v8.jsonl`）**抽出 0 个产物** ⇒ verdict 恒为
`ANSWER`/`EMPTY` ⇒ **96 格一格不动**。这是本轮"不改尺子"的自证。

## 七、没证明的事（不许含糊）

1. ⛔ **没跑 96 格**。验收层是读数层，不进打分器，但"没跑"就是"没跑"。
2. ⚠️ `Caliber.signature()` **未变的证据只有两条间接读数**：
   ① `git diff` 不含 `mw/config.py`（`limits` 的唯一声明处）；
   ② 门禁 P5 硬锚绿，打印 `limits 字段 25 个`、`V1_CAND_FAILOVER=已清`。
   ⇒ 这是**结构证据，不是"同一实验两次跑出同一个 sig"**。真正的读数是下一次跑批时对 sig。
3. ⛔ 本轮**没有**让累积循环更近一步：它是读数修复。
   它服务主线的唯一方式是：`status` 会写进语义记忆
   （`memory.record("finish", f"{status}: …")`），而一个"自称 done 却没产物"的成功标记
   会作为先例回灌。**要不要把 verdict 也写进记忆（那会改 prompt）单开一轮**。

## 八、门禁

`./run_tests.sh` **EXIT=0**：契约 **607/0**、锚层 **97/0**、撤回态 **193**（AZ107 撤回
实测 `红=['AZ107']`、多红=无、Traceback=无、还原后 607/0）、`check_counts` 22 处 0 漂移、
生产库指纹 `359c7100…` 未变。

## 九、下一轮（按证据排）

1. ⭐⭐⭐⭐ **候选构成**：真实任务第 1 轮给了 5 个候选，其中 3 个是同一个
   `agent:code_agent`，而这一个必败（「题面里没有『函数名必须是 X』」）。
   ⇒ 先把"每轮候选都是什么 tool"变成**恒存在的读数**，再谈去重/分派。
2. ⭐⭐⭐ **外部能力只在函数题上通电**：`code_agent` 占宽度、零收益，
   且它的失败会连续三轮把循环拖进 `stalled`（A 臂实测 3 轮全在它身上）。
3. ⭐⭐ 把 verdict 写进 `answers.jsonl` 与记忆（会改 prompt，单开一轮 + 重跑 96 格）。
