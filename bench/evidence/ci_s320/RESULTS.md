# V250（§320）：CI 又红了 15 次 —— 这次是 PEP 701，而 README 一直在写「全绿」

**时间**：2026-11-26 21:07~21:20
**触发**：用户要求「重写最新的 README，并查 CI 出错」
**结论**：仓库对外的 CI 从 run 234 红到 run 248（**连续 15 次**），
根因是 `tests/contract_test.py` 一行 f-string 表达式里含反斜杠 ——
**PEP 701 只在 3.12+ 允许**，3.11 直接 `SyntaxError`，整个契约文件连编译都过不去。

---

## §1 先取证：CI 到底绿不绿

`GET /repos/lin-jie22/PyautoL-V1/actions/workflows/ci.yml/runs?per_page=60&branch=main`：

| run | 提交 | 结论 |
|---|---|---|
| **248** | `9b1aaf5`（HEAD，V249） | failure |
| 247 | `8ff5c29` | failure |
| … 连续 … | | failure |
| **234** | `6d30ec1`（V236） | failure ← 红段起点 |
| **233** | `09c0ec29` | **success ← 最后一次绿** |

⇒ 连红 **15 次**（234→248）。总 run 数 248。

⭐ README 当时写着 `| .github/workflows/ci.yml | ✅ 当前全绿 |`，
这句话在 **15 次红** 期间一直存在。它后面还跟着一句「⛔ 绿勾不算读数，当前状态以 run 号为准」——
**两条互相矛盾的声明放在同一格里**，而没人去查 run 号。

## §2 定位：只有 py3.11 红

`GET /actions/runs/45822680128/jobs`：

| job | 七步验证 | 结论 |
|---|---|---|
| **py3.11** | **failure** | 红 |
| py3.12 | success | 绿 |
| py3.13 | success | 绿 |

同一 OS、同一 checkout，只有解释器版本不同 ⇒ 版本特有。
（job 日志下载返回 403 `Must have admin rights to Repository.` ⇒ 只能本地复现。）

## §3 复现（本机 python3.11 = 3.11.15）

```
$ PY=python3.11 ./run_tests.sh
步骤 2/7 契约测试 …… ❌ 契约测试失败：
❌ 契约日志里没有汇总行 /tmp/v1_contract.out（测试可能崩在中途）
  ❌ 文档里的计数与本次实测不一致
RC=1
```

直接跑契约文件拿到堆栈：

```
  File "tests/contract_test.py", line 16844
    f"pool={len(_az162_pool)} cnt={_az162_cnt} src_vec={'vec\": list(_sr' in _az162_src}")
                                                         ^
SyntaxError: f-string expression part cannot include a backslash
```

**注意这行的形态**：报错来自**一条 `f"…"` 的诊断信息**，不是判据本身。
断言的语义（`'**({"vec": ...})' in _az162_src`）完全没问题，坏掉的只是「打印这一格」这句话。
⇒ **一条只用于打印的字符串，能让一整套门禁在 3.11 上跑不起来。**

## §4 引入点

```
$ git log -S 'src_vec={' --oneline -- tests/contract_test.py
6d30ec1 V236（§307）：扩候选空间（MODEL 轴）……
```

与红段起点**逐位一致**（run 234 = `6d30ec1`）。⇒ 时间线与代码线对得上，
不是环境漂移，是这一次提交引入的。

## §5 修复

把反斜杠字面量搬到 f-string **外面**，判据与输出内容一个字不改：

```python
_az162_vec_probe = 'vec": list(_sr'          # ← 新增：PEP 701 之前，这不能写在 f-string 里
...
f"pool={len(_az162_pool)} cnt={_az162_cnt} src_vec={_az162_vec_probe in _az162_src}")
```

## §6 修复后（双版本实测）

| 版本 | 命令 | RC | 契约 | 锚层 | 计数核对 |
|---|---|---|---|---|---|
| 3.11.15 | `PY=python3.11 ./run_tests.sh` | **0** | `PASS=853 FAIL=0` | `PASS=100 FAIL=0` | `11 处声明，0 处漂移` |
| 3.13.12 | `./run_tests.sh` | **0** | `PASS=853 FAIL=0` | `PASS=100 FAIL=0` | `11 处声明，0 处漂移` |

原始输出：`READINGS_311_before.txt` / `READINGS_311_after.txt` / `READINGS_313_after.txt`。

**CI 实测（修复提交 `154bed2`）**：run **249** = **success**，三个 job 全绿
（`py3.11 success / py3.12 success / py3.13 success`，七步验证步均 success）。
⇒ 红段 `234~248` 在这里收口。⛔ 下一次改动仍要按 run 号查，徽章只反映最近一次。

⛔ **这只证明「本地两个版本绿了」，不证明「CI 绿了」**（还有 checkout / setup-python 等环节）。
CI 状态要等下一次 push 的 run 号 —— 见本节末尾的「CI 实测」。

## §7 顺手扫了一遍：还有一个文件在 3.11 上编译不过

用 3.11 对全仓 `.py` 做 `py_compile`：

| 文件 | 状态 |
|---|---|
| `tests/contract_test.py` | ✅ 已修 |
| `bench/evidence/v6_s137/probe_v47_applies.py:137` | ❌ 仍 `SyntaxError` |

后者是**历史证据文件**，不在门禁路径上（`run_tests.sh` 只跑 `bench/evidence/knowledge_s*` 的指纹），
也不被任何指纹清单收录 ⇒ **不改**（改历史证据 = 伪造记录）。
登记为已知：谁在 3.11 上直接跑它，会拿到 SyntaxError，而不是读数。

## §8 这一类不是第一次

| 轮 | 机制 | 表现 |
|---|---|---|
| V144~V168（32 次红） | `AZ130a` 拿 3.12+ 的 `sum()` 补偿求和当差异见证 | 3.11 上该子句恒假 |
| **V236~V249（15 次红）** | **f-string 表达式含反斜杠（PEP 701）** | 3.11 上连编译都不过 |

两次的共同点：**只有 py3.11 红，本地默认解释器是 3.13 ⇒ 本地永远绿。**
⇒ 「本地全绿」对「CI 全绿」零信息量，除非显式用 3.11 跑。

## §9 自踩

1. **我把 README 的「✅ 当前全绿」原样继承了 15 轮**。它不是我的读数，是我抄来的声明；
   而同一格里还写着「以 run 号为准」——**我知道该怎么查，但没查**。
2. 查 CI 时先试 `gh`（未安装）⇒ 转而用 GitHub REST；job 日志 403 ⇒ 才转本地复现。
   顺序上多花了两次调用，但**本地复现比日志更好**：能拿到精确行号。
3. 修完之后我本想加一道「禁止 f-string 里写反斜杠」的闸 —— **没加**。
   它只能抓这一种语法糖，抓不到 V169 那种运行时差异；一道只能抓一半的闸
   会让人以为「这一类已经被挡住了」。真正的闸是 CI 矩阵本身（它确实抓到了），
   缺的是**有人去看 run 号**。⇒ 修在文档上：README 不再写「全绿」，改写红段的成因与查询方法。

## §10 边界

- 本文件只覆盖**离线门禁**。bench 读数与 CI 无关（CI 不跑 bench）。
- 「本地 3.11/3.13 绿」≠「CI 绿」：CI 还有 checkout、setup-python、缓存等环节。
- 未做：把 CI 红/绿状态自动写回仓库（那需要 token 与写权限，本轮不引入密钥）。
