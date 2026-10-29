#!/usr/bin/env bash
# V1 一键验证。每一步都会跑，任一步失败即整体失败。
#
#   ./run_tests.sh
#
# 不依赖任何第三方包。只用系统/托管的 Python 3。
set -uo pipefail

# Python 解析：优先 $PY，其次 PATH 上第一个版本 ≥3.11 的解释器。
# ⛔ 不写死任何本机路径（别人的机器上没有那条路径），
#    也不静默降级到 3.9（契约用了 tomllib，3.11 起才有 ⇒ 降级只会给一句看不懂的 ImportError）。
if [ -z "${PY:-}" ]; then
  for _c in python3 python3.13 python3.12 python3.11; do
    command -v "$_c" >/dev/null 2>&1 || continue
    if "$_c" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
      PY="$_c"; break
    fi
  done
fi
if [ -z "${PY:-}" ] || ! command -v "$PY" >/dev/null 2>&1; then
  echo "❌ 找不到 Python 3.11+。装一个，或显式指定：PY=/path/to/python3.11 ./run_tests.sh"
  exit 2
fi
cd "$(dirname "$0")"

FAIL=0

# ══════════════════════════════════════════════════════════════════
# 步骤 0  门禁自身的环境隔离（第 110 轮，2026-10-29 实测事故）
# ══════════════════════════════════════════════════════════════════
# 事故形态：`bench/probe_verify.py`（步骤 6g）与 `bench/probe_recall.py`（6f）
# 会真的 `Core(...).run()`，但本脚本当时**没给它们设 `V1_STATE_DIR`** ⇒ 回落到
# `mw/config.py:86` 的默认值 `ROOT/state` = **生产记忆库**。
# 实测后果（一次门禁的账）：`state/memory.db` 多 36 条 episode（StubLLM 的
# `solve(x)` / `flatten(d)`），`state/critique.jsonl` 多 8 条 `tasks_v4` 记录。
# ⛔ 生产库是「累积循环」的唯一载体 —— 被污染后累积读数不可归因。
# 取证：`bench/evidence/v5_s110/AUDIT_agi_loop.md`
CI_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/v1_ci_state.XXXXXX")"
export V1_STATE_DIR="$CI_ROOT/state"
export V1_LOG_DIR="$CI_ROOT/logs"
echo "门禁状态目录：$CI_ROOT   （临时；⛔ 不许落到 ./state）"

# 生产目录指纹：步骤 8 要断言它没变 —— 这不是承诺，是一条**能判红**的检查。
_prod_sha() { if [ -f "$1" ]; then shasum -a 256 "$1" | awk '{print $1}'; else echo absent; fi; }
PROD_DB_BEFORE="$(_prod_sha state/memory.db)"
PROD_CRIT_BEFORE="$(_prod_sha state/critique.jsonl)"

echo "=============================================================="
echo "步骤 1/7  依赖审计（必须零第三方依赖）"
echo "=============================================================="
if "$PY" - <<'EOF'
import ast, sys, pathlib
#: 本地一等公民：`run` 是本文件、`mw` 是内核、`bench` 是量具与分析脚本
#: （第七十九轮起 `run.py` 要 import `bench.answer_store` 才落答案本体）。
#: ⛔ 允许它们的同时**必须验证它确实是本仓库的目录** —— `pip install bench`
#:    是真实存在的第三方包，只把名字写进白名单等于给同名包开后门。
LOCAL = {"run", "mw", "bench"}
ALLOWED = {
    "argparse","ast","builtins","collections","copy","dataclasses","hashlib","json","math",
    "os","re","shlex","sqlite3","statistics","subprocess","symtable","sys","tempfile",
    "time","tokenize","typing","urllib","pathlib","__future__",
} | LOCAL
#: ⚠️ `builtins` / `symtable` 是第八十轮加的，都是 **CPython 自带**（前者是内建模块，
#:    后者在标准库里，`python -c "import symtable"` 即可）。加它们是把这个白名单
#:    维持在"标准库 + 本仓库"的口径上，不是给它开口子：仍然**零第三方依赖**。
bad = []
for _local in sorted(LOCAL):
    # 本地模块必须**在本仓库里真实存在**：同名目录（`mw/`、`bench/`）或同名文件（`run`）。
    if not (pathlib.Path(_local).is_dir() or pathlib.Path(_local + ".py").is_file()):
        bad.append(f"白名单 {_local!r}: 仓库里既没有同名目录也没有同名 .py ⇒ 可能是第三方同名包")
for p in list(pathlib.Path("mw").glob("*.py")) + [pathlib.Path("run.py")]:
    for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
        mods = []
        if isinstance(n, ast.Import):
            mods = [a.name.split(".")[0] for a in n.names]
        elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            mods = [n.module.split(".")[0]]
        for m in mods:
            if m not in ALLOWED:
                bad.append(f"{p}: {m}")
print("  允许集合外的 import:", bad if bad else "无")
sys.exit(1 if bad else 0)
EOF
then echo "  ✅ 零第三方依赖"; else echo "  ❌ 发现外部依赖"; FAIL=1; fi

echo
echo "=============================================================="
echo "步骤 2/7  契约测试（离线；K 静默降级 / L 外部能力 / O 停滞 / Q 收尾"
echo "                    U 跨领域 / V 因果闭环 / W 抽象读数 / X 血缘完整性 /"
echo "                    Y 失配信号的分辨力 / Z 内核读数落盘 / AA 模块接线 /"
echo "                    AB 闭环通量 / AC 价值层 / AD 自我模型 / AE 消融安全 /"
echo "                    AF 失败签名（第二层判据：缺了会看到什么现象）/"
echo "                    AG 外部评审（只读 / 防自评 / 防跨口径）/"
echo "                    AH 可复核性（语言与沟通的真判据）/"
echo "                    AI 自我模型（跨会话账本 / 预测校准 / 口径闸承重）/"
echo "                    AJ 族预测力闸（测不出预测力 ⇒ 不许据此行动）/
                    AK 结构簇作为「族」的定义 / AL 外部奖励只读通道（写入/检索/归因）/
                    AM 证据必须自描述 + 语义选项冲突必须显式报错 /
                    AN 抽象层：「学会了什么」与「记住了什么」分开观测 /
                    AO 安慰剂臂（等长 / 不许空层 / 不许等于原文 / 索引必须同步）/
                    AP 假记忆臂（双射 / 确定性 / 题边界水位 / 判读只一份）/
                    AQ 安慰剂成为一等公民 + 形状臂（并包逐字节回归 / 单因子操纵））"
echo "=============================================================="
if "$PY" tests/contract_test.py > /tmp/v1_contract.out 2>&1; then
  echo "  ✅ $(grep -oE 'PASS=[0-9]+  FAIL=[0-9]+' /tmp/v1_contract.out | tail -1)"
else
  echo "  ❌ 契约测试失败："
  grep "❌" /tmp/v1_contract.out | head -20
  FAIL=1
fi

echo
echo "=============================================================="
echo "步骤 3/7  外部锚层契约测试（含反证组：假尺子必须被判红）"
echo "=============================================================="
if V1_BENCH_DIR=/tmp/v1_bench_ci "$PY" tests/bench_test.py > /tmp/v1_bench.out 2>&1; then
  echo "  ✅ $(grep -oE 'PASS=[0-9]+  FAIL=[0-9]+' /tmp/v1_bench.out | tail -1)"
  grep -E "^  (校准|.*margin=)" /tmp/v1_bench.out | head -2 | sed 's/^/  /'
else
  echo "  ❌ 外部锚层契约测试失败："
  grep "❌" /tmp/v1_bench.out | head -20
  FAIL=1
fi

echo
echo "=============================================================="
echo "步骤 3b 文档计数单源核对（AZ47：文档里的数字必须等于本次运行）"
echo "=============================================================="
# 为什么放在这一步、而不是写成一条契约断言：
#   被核对的对象里就含"契约这次跑出多少条"，断言自己也在那个数里 ⇒ N 与 N+1 自指。
#   AZ44 曾把 109 写死、真值涨到 110 后**锚自己把漂移锁住了**，就是这个结构的实证。
#   ⇒ 只有"被测集合之外"能同时看见文档声明与本次实测。数字的唯一来源是这两个日志。
if "$PY" bench/check_counts.py \
     --contract-log /tmp/v1_contract.out --anchor-log /tmp/v1_bench.out; then
  echo "  ✅ 文档声明 == 本次实测（数字只写一处，核对器会说清哪几处漏改）"
else
  echo "  ❌ 文档里的计数与本次实测不一致（改文档，不许改核对器）"
  FAIL=1
fi

echo
echo "=============================================================="
echo "步骤 4/7  离线端到端自检（零网络 / 零密钥）"
echo "=============================================================="
# ⚠️ 状态目录必须隔离到临时目录：本步骤也是"测试"，不许写生产 state/
#    （V1 自己的第一条纪律就是"测试绝不碰生产目录"）。
_SELFTEST_TMP="$(mktemp -d)"
if V1_STATE_DIR="$_SELFTEST_TMP/state" V1_LOG_DIR="$_SELFTEST_TMP/logs" \
   V1_WORKSPACE="$_SELFTEST_TMP/ws" V1_LLM_OFFLINE=1 \
   "$PY" run.py --selftest > /tmp/v1_selftest.out 2>&1 \
   && grep -q "自检结论: ✅ 通过" /tmp/v1_selftest.out; then
  echo "  ✅ 主循环端到端通过"
  grep -E "^  (状态|预算|守卫拦截|世界模型|记忆|重复行)" /tmp/v1_selftest.out | sed 's/^/  /'
else
  echo "  ❌ 自检失败："
  tail -20 /tmp/v1_selftest.out
  FAIL=1
fi

echo
echo "=============================================================="
echo "步骤 5/7  跨领域冒烟（同一把打分器 × 每个领域，oracle 参照）"
echo "=============================================================="
# 为什么要有这一步：`--suite-dir` 的价值是"**每个领域**都非零"。
# 这条冒烟只用 oracle 参照（不需要模型、几秒钟），但能保证：
#   ① 每个领域的数据仍能被 calibrate 判为"有分辨力"；
#   ② 每个领域的 oracle 仍拿满分（= 任务集与打分器没漂移）。
# 它同时替「跨领域」这件事把"数据坏了"变成**回归期就能发现**的事，
# 而不是等到某天真跑模型时才发现某个领域全是 0。
# ⚠️ V1_STATE_DIR 也隔离：本步骤会**读**跨会话分数账本（自我模型起跑用），
#    不隔离的话它会读到生产 state/score_ledger.jsonl —— 生产目录被测试读也是污染。
if V1_STATE_DIR=/tmp/v1_cross_state V1_BENCH_DIR=/tmp/v1_cross_ci \
   "$PY" run.py --bench --offline --solver oracle \
     --suite-dir bench/tasks > /tmp/v1_cross.out 2>&1; then
  echo "  ✅ $(grep -oE '可信领域 [0-9]+/[0-9]+.*' /tmp/v1_cross.out | tail -1)"
else
  echo "  ❌ 跨领域冒烟失败（某领域的校准或 oracle 不满分）："
  tail -12 /tmp/v1_cross.out | sed 's/^/  /'
  FAIL=1
fi

# ── 5b 语言领域（**另一把尺子**）────────────────────────────────────────
# ⚠️ 为什么不和上一步合并：`--suite-dir` 一次只用**一把**打分器，而
#    `bench/tasks_lang/` 量的是语言（答案里的断言可不可核对），
#    `bench/tasks/` 量的是代码（能不能跑通）。**一把尺子量一种东西。**
#    把它们塞进同一个目录，就是让调用方去猜"这题该用哪把尺子" ——
#    而判据该由被测对象声明，不由调用方推断。
echo "步骤 5b  语言领域冒烟（assertion 打分器 × 可复核性）"
echo "=============================================================="
if V1_STATE_DIR=/tmp/v1_lang_state V1_BENCH_DIR=/tmp/v1_lang_ci \
   "$PY" run.py --bench --offline --solver oracle \
     --scorer bench/scorers/assertion.py \
     --suite-dir bench/tasks_lang > /tmp/v1_lang.out 2>&1; then
  echo "  ✅ $(grep -oE '可信领域 [0-9]+/[0-9]+.*' /tmp/v1_lang.out | tail -1)"
else
  echo "  ❌ 语言领域冒烟失败（校准或 oracle 不满分）："
  tail -12 /tmp/v1_lang.out | sed 's/^/  /'
  FAIL=1
fi

# ── 步骤 6：自我模型的判据（跨会话预测校准）────────────────────────────
# 为什么单独一步：它测的不是"内核跑不跑得通"，而是"自我模型说的话算不算数"。
# 判据 = 用历史会话预测未来会话，误差必须**优于**不区分能力族的对照基线；
# 且自带三个反证组（族间无差异 / 混入异口径 / 闸该关时必须关），
# 缺一个就是恒真断言。
echo "步骤 6/7  自我模型判据（跨会话预测校准 + 三个反证组 + 族预测力闸）"
echo "=============================================================="
if "$PY" bench/probe_selfmodel.py \
     --real bench/evidence/score_ledger_real_qwen25_7b_T0_8sess.jsonl \
     > /tmp/v1_self.out 2>&1; then
  grep -E "^  (自我模型预测|对照基线预测|优势|判定阈值|⇒|只收同口径|混入异口径)" \
    /tmp/v1_self.out | sed 's/^/  /'
  echo "  ── 族预测力闸（真实账本）──"
  grep -E "闸=|⛔|行=|自我模型 MAE=" /tmp/v1_self.out | sed 's/^/  /'
  echo "  ✅ $(grep -oE '结论：.*' /tmp/v1_self.out | tail -1)"
else
  echo "  ❌ 自我模型判据不成立（判据不能分辨，或反证组没转红）："
  tail -20 /tmp/v1_self.out | sed 's/^/  /'
  FAIL=1
fi

# ── 步骤 6b：族的**定义**对照（「族」是概念还是文件名？）──────────────────
# 为什么单独一步：步骤 6 证明"分族能预测"，但没问"族是什么"。
# 如果族 = 文件名，那"能预测"测的是**我怎么分装题目**，不是题本身。
# ⇒ 用同一份真实账本、同一把尺子，只把族换成题面结构自动聚出来的类，对照 snr。
# ⛔ 本步骤的期望输出里有**负面结论**（结构族更弱）。它失败（≠0）才是异常。
echo "步骤 6b 族定义对照（结构族 vs 文件名族，零新模型调用）"
echo "=============================================================="
if "$PY" bench/probe_family_def.py > /tmp/v1_famdef.out 2>&1; then
  grep -E "^  (文件名族|结构族)" /tmp/v1_famdef.out | sed 's/^/  /'
  grep -E "^判定|^  (结构族 snr|现有族预测力)" /tmp/v1_famdef.out | sed 's/^/  /'
  echo "  ✅ 对照跑通（结论为**负面**是预期结果，不是失败）"
else
  echo "  ❌ 族定义对照跑不起来："
  tail -12 /tmp/v1_famdef.out | sed 's/^/  /'
  FAIL=1
fi

# ── 步骤 6c：安慰剂对照的**结论探针**（第十七轮）──────────────────────────
# 为什么单独一步：第十六轮踏完了才明白，"读到抽象层 +0.05" 里有可能一大部分
#   只是"上下文里多了一段文本"。第十七轮加了三条等长安慰剂把这件事劈开，
#   结论是**负的**（真 Δ 落在安慰剂带内，不得认领）。
#   这一步不跑模型 —— 它把冻结的 runs.jsonl **重算**一遍：
#     · 数字写在散文里改一个字没人知道，写在探针里改数据它会自己耸；
#     · ⛔ 期望输出是**负面结论**。它失败（≠0）才是异常。
echo "步骤 6c 安慰剂对照结论探针（零模型调用，重算第十七轮冻结证据）"
echo "=============================================================="
if "$PY" bench/probe_placebo.py > /tmp/v1_placebo.out 2>&1; then
  grep -E "^  (题|k0|mean|   |    [A-D])" /tmp/v1_placebo.out | head -10 | sed 's/^/  /'
  grep -E "安慰剂 Δ 区间|极差 [0-9]" /tmp/v1_placebo.out | sed 's/^/  /'
  echo "  ✅ $(grep -oE '结论探针.*' /tmp/v1_placebo.out | tail -1)"
else
  echo "  ❌ 安慰剂结论探针失败（数据换过？判据松了？）："
  tail -20 /tmp/v1_placebo.out | sed 's/^/  /'
  FAIL=1
fi

# ── 步骤 6d：假记忆对照的**结论探针**（第十八轮）──────────────────────────
# §16.14 那个 +0.2000（隔离 0.5600 → 累积 0.7600）当时只有「隔离 vs 累积」
#   一个对照，没有安慰剂 ⇒ 分不清"学到了"与"上下文里多了一堆字"。
#   第十八轮把前面几题留下的东西换成**等长、等条数、等结构**的假货。
#   这一步同样不跑模型：它把冻结证据按事前登记的 R1–R5 判读一遍，
#   并核对"当时落盘的判定"是否等于"现在重算的判定"。
#   ⛔ 期望输出随数据而定 —— 它失败（≠0）才是信号。
echo "步骤 6d 假记忆对照结论探针（零模型调用，重算第十八轮冻结证据）"
echo "=============================================================="
if "$PY" bench/probe_fakemem.py > /tmp/v1_fakemem.out 2>&1; then
  grep -E "^  (P[0-9]|E3|task|mean|z0)" /tmp/v1_fakemem.out | head -14 | sed 's/^/  /'
  echo "  ✅ $(grep -oE '结论探针.*' /tmp/v1_fakemem.out | tail -1)"
else
  echo "  ❌ 假记忆结论探针失败（数据换过？判据松了？事前规则被改？）："
  tail -20 /tmp/v1_fakemem.out | sed 's/^/  /'
  FAIL=1
fi

# ── 步骤 6e：形状对照的**结论探针**（第十九轮）────────────────────────────
# 第十八轮把前面的答案换成等长等行的乱码（E1）之后，5 份答卷**逐字相同**
#   ⇒ 语义贡献为 0。但 E1 把字符数、逐行长度、缩进、标点分布**全部保留**了：
#   它证得了"语义没用"，证不了"形状有没有用"。
#   第十九轮加 F 臂（reshape）：**保字符序列、破行几何**。
#   这一步不跑模型，只做两件事：
#     ① 按事前登记的 S1–S6 重算判读（唯一实现，在 probe_shape.py 里）；
#     ② 把前五臂的**逐题**读数与第十八轮冻结值逐位比对
#        —— 并包重构若悄悄改了任何一臂，这里会当场转红。
#   ⛔ 期望输出随数据而定 —— 它失败（≠0）才是信号。
echo "步骤 6e 形状对照结论探针（零模型调用，重算第十九轮冻结证据）"
echo "=============================================================="
if "$PY" bench/probe_shape.py > /tmp/v1_shape.out 2>&1; then
  grep -E "^  (P[0-9]|E[0-3]|F |task|mean|z0)" /tmp/v1_shape.out | head -16 | sed 's/^/  /'
  grep -E "E3 − F|⇒ 判读" /tmp/v1_shape.out | sed 's/^/  /'
  echo "  ✅ $(grep -oE '结论探针.*' /tmp/v1_shape.out | tail -1)"
else
  echo "  ❌ 形状结论探针失败（数据换过？仪器不是同一台？判据松了？）："
  tail -20 /tmp/v1_shape.out | sed 's/^/  /'
  FAIL=1
fi

# ── 步骤 6f：按需回看的**结论探针**（第二十轮）──────────────────────────
# 缺陷（§17-9）：`stored = full[:300]` ⇒ 5000 字观测只看见 300 字，
#   其余部分当场消失**且没有任何路径能取回**。修法不是调大 300（那会让一步
#   吃掉整个上下文），而是「整段落盘 + 索引 + 只在意外时取回 + 有上限」。
# 这一步不跑模型，只做两件事：
#   ① 按事前登记的 R1–R4 重算判读（唯一实现在 bench/probe_recall.py）；
#   ② 与落盘判定对账（改过数据或改过阈值，这里会自己转红）。
echo "步骤 6f 按需回看的结论探针（零模型调用，重算第二十轮冻结证据）"
echo "=============================================================="
if "$PY" bench/probe_recall.py > /tmp/v1_recall.out 2>&1; then
  grep -E "^  (✅|❌) P[0-9]" /tmp/v1_recall.out | head -10 | sed 's/^/  /'
  grep -E "⇒ 判读" /tmp/v1_recall.out | sed 's/^/  /'
  echo "  ✅ 结论探针 全部通过"
else
  echo "  ❌ 回看结论探针失败（数据换过？仪器不是同一台？判据松了？）："
  tail -20 /tmp/v1_recall.out | sed 's/^/  /'
  FAIL=1
fi

# ── 步骤 6g：校验闸的**结论探针**（第二十一轮）──────────────────────────
# 缺陷：`tools.py` 首屏写着内核做四件事「预算、记忆、规划、**校验**」。
#   前三件都在，第四件在此之前**零实现** —— 这不是能力缺口，是"声明了没有"。
#   而它同时是边界 6（预算→能力）、边界 10（没有可学的失败）、§17-6（知识不可执行）
#   三条读数的同一个洞。
# 这一步不跑模型，做三件事：
#   ① 按事前登记的 R1–R5 重算判读（唯一实现在 bench/probe_verify.py）；
#   ② **校准**判据：21 道题的参考解必须全过（自检不许比尺子更严）；
#   ③ 与落盘判定对账（改过数据或改过阈值，这里会自己转红）。
echo "步骤 6g 校验闸的结论探针（零模型调用，重算第二十一轮冻结证据）"
echo "=============================================================="
if "$PY" bench/probe_verify.py > /tmp/v1_verify.out 2>&1; then
  grep -E "^  (✅|❌) P(1|2|3|4|5|7|8|9|11|12|13|14)/" /tmp/v1_verify.out \
    | sed 's/^/  /'
  grep -E "⇒ 判读" /tmp/v1_verify.out | sed 's/^/  /'
  echo "  ✅ 结论探针 全部通过"
else
  echo "  ❌ 校验结论探针失败（判据在造假失败？闸只拦不修？关不掉？）："
  tail -24 /tmp/v1_verify.out | sed 's/^/  /'
  FAIL=1
fi

# ── 步骤 6g′：**真模型读数**的重算（零模型调用，读冻结的 runs.jsonl）────────
# 这一步的存在理由是一条纪律：**数字写在散文里改一个字符不容易被发现**。
# 三臂（OFF / ON / 内容无关安慰剂）的结论必须能从落盘文件里重新算出来，
# 而且算出来的判定必须与落盘判定一致。⛔ 期望输出是**负面结论**（Q3），
# 它失败（≠0）才是异常。
echo "步骤 6g′ 校验闸·真模型读数重算（OFF / ON / 安慰剂，三臂）"
echo "=============================================================="
if "$PY" bench/probe_verify_real.py > /tmp/v1_verify_real.out 2>&1; then
  grep -E "^  (✅|❌) Q[0-9]" /tmp/v1_verify_real.out | sed 's/^/  /'
  grep -E "ON − OFF|ON 与 PL|mean: " /tmp/v1_verify_real.out | sed 's/^/  /'
  grep -E "⇒ 判读" /tmp/v1_verify_real.out | sed 's/^/  /'
else
  echo "  ❌ 真模型读数重算失败（证据换过？判据松了？）："
  tail -20 /tmp/v1_verify_real.out | sed 's/^/  /'
  FAIL=1
fi

# ── 步骤 6h：路线①·外部能力的**结论探针**（第二十三轮）──────────────────
# 这一轮换的不是判据，是**能力来源**：`agents.json` 从"回显示例"换成
#   `external/code_agent.py`（多角度生成 + 写 assert + 真跑筛选）。
#   事前 R1–R5 写在 DESIGN §16.22 ③，本步骤只重算它。
# 这一步不跑模型，做三件事：
#   ① 读四臂冻结证据（OFF/ON × struct_convert/heldout），按事前规则重算判读；
#   ② **双口径并列**（配对 / 缺题补零）—— 两口径判读不一致即强制 UNKNOWN；
#   ③ 核对"某臂没量到的题"被**点名**而不是被补成 0 分。
#   ⛔ 期望输出随数据而定 —— 它失败（≠0）才是信号。
echo "步骤 6h 外部能力对照结论探针（零模型调用，重算第二十三轮冻结证据）"
echo "=============================================================="
if "$PY" bench/probe_external_real.py > /tmp/v1_external.out 2>&1; then
  grep -E "^  (sc|ho):|Δ\(|⚠️ |两口径判读" /tmp/v1_external.out | sed 's/^/  /'
  grep -E "^  (外部读数|【)" /tmp/v1_external.out | sed 's/^/  /'
  echo "  ✅ 结论探针 已重算（判读见上）"
else
  echo "  ❌ 外部能力结论探针失败（证据缺失？判据被改？补零又回来了？）："
  tail -24 /tmp/v1_external.out | sed 's/^/  /'
  FAIL=1
fi

# ── 步骤 6i：2x 档的证据**必须可核验**（第二十四轮）──────────────────────
# 为什么单列一步：`external_s23` 那批证据产生于口径签名补上 `tool_catalog_sha`
# **之前** ⇒ 它只能判 `unverifiable`（不许读成 pass）。而 2x 那批是新代码跑的，
# **必须**判成 `pass`（只差声明过的 `limits` 与 `tool_catalog_sha`）。
# ⛔ 这一步一旦变成 `fail`，意味着"两臂只差外部能力"这句话又变回散文。
echo
echo "=============================================================="
echo "步骤 6i 2x 档证据的口径核验（必须是 pass，不是 unverifiable）"
echo "=============================================================="
if "$PY" bench/probe_external_real.py --dir bench/evidence/external_s24_2x \
      > /tmp/v1_external_2x.out 2>&1; then
  grep -E "口径核验|^(  sc|  ho):|Δ\(配对|【" /tmp/v1_external_2x.out | sed 's/^/  /'
  if grep -q "口径核验: pass" /tmp/v1_external_2x.out; then
    echo "  ✅ 2x 证据可核验：两臂只差声明的变量"
  else
    echo "  ❌ 2x 证据**不可**核验（判成了 unverifiable/fail）⇒ 那次对照不算同一个实验"
    FAIL=1
  fi
else
  echo "  ❌ 2x 结论探针失败（证据缺失？判据被改？）："
  tail -20 /tmp/v1_external_2x.out | sed 's/^/  /'
  FAIL=1
fi

# ── 步骤 6j：重试链守墙钟的**正式探针**（第二十五轮）──────────────────────
# 缺陷：`timeout_s=120` + `llm_retries=2` + `wall_s=120` ⇒ 一次逻辑调用串起
#   361.5s，超支 3 倍，而日志只留一个 `TimeoutError`、`steps=0`。
#   实现**违反了自己声明的硬上限** —— 这不是"预算设小了"。
# 这一步起一个**本机**慢端点（127.0.0.1，零外网），把两件事一起测：
#   ① 对照臂**逐字复刻 HEAD 的旧语义**（固定 timeout + 重试不查墙钟）
#      ⇒ 没有它就只是"新逻辑跑得快"，而"跑得快"可以被任意调小 timeout 伪造；
#   ② 修复臂的耗时 ≤ wall_s + 5、只发 1 次请求、且余量还在。
# ⛔ 两臂用同一个端点、同一组参数 ⇒ 唯一的自变量是**重试链如何看待墙钟**。
echo "步骤 6j 重试链守墙钟探针（本机慢端点，约 10 秒，零外网）"
echo "=============================================================="
# ⚠️ 必须绕过代理：本机地址若走代理，超时行为会失真（实测踩过一次）。
if NO_PROXY=127.0.0.1 no_proxy=127.0.0.1 "$PY" bench/probe_wall_retry.py \
      > /tmp/v1_wall.out 2>&1; then
  grep -E "^  (✅|❌) R1|^   (耗时|计数|剩余)" /tmp/v1_wall.out | sed 's/^/  /'
  echo "  ✅ $(grep -oE '探针结论.*' /tmp/v1_wall.out | tail -1)"
else
  echo "  ❌ 重试链仍在超支预算（或对照臂不再复刻旧语义）："
  tail -20 /tmp/v1_wall.out | sed 's/^/  /'
  FAIL=1
fi

# ── 步骤 7：证据来源指纹（第十五轮实测逼出）──────────────────────────────
# 起因：同一套 struct_convert 在 --max-tokens 1024 / 1536 下给出 0.400 / 0.7600
#   两个相差近一倍的读数。回头查那是哪个预算，却发现四份真实账本里**一行都不带
#   可读的口径字典**（只有 16 位哈希），而生成它们的实验脚本也没进仓库。
#   ⇒ 那是一份六个月后打开就哑了的证据。这一步不跑模型，只做两件事：
#     ① 把"产生证据的脚本"的 sha256 念出来 ⇒ 谁改过脚本，指纹就变；
#     ② 把"账本里有多少行不可复核"念出来 ⇒ 历史欠账不沉默。
echo "步骤 7/7  证据来源指纹（脚本 sha256 + 账本可复核性）"
echo "=============================================================="
if "$PY" - <<'EOF'
import hashlib, pathlib, sys
sys.path.insert(0, ".")
from mw.ledger import ScoreLedger, CALIBER_FIELD

targets = sorted(pathlib.Path("bench").glob("*.sh")) + \
          sorted(pathlib.Path("bench").glob("*.py"))
for p in targets:
    h = hashlib.sha256(p.read_bytes()).hexdigest()[:12]
    print(f"  {h}  {p.as_posix()}")
print("  ── 账本可复核性 ──")
worst = 0
for p in sorted(pathlib.Path("bench/evidence").glob("*.jsonl")):
    L = ScoreLedger(p)
    rows = L.load()
    if not rows:
        continue
    sig = max(set(r.get("caliber_sig", "") for r in rows),
              key=lambda s: sum(1 for r in rows if r.get("caliber_sig") == s))
    L.admissible(sig)
    n = len(rows)
    legacy = L.stats.legacy_no_caliber
    worst = max(worst, 0 if n == 0 else legacy / n)
    flag = "⚠️" if legacy else "  "
    print(f"  {flag} {p.name:52s} {n:4d} 行 不可复核={legacy}")
print(f"  ⇒ 历史账本不可复核占比最高 {worst:.0%}（这些行有分数、没有可读口径）")
sys.exit(0)
EOF
then echo "  ✅ 指纹已列出"; else echo "  ❌ 指纹步骤失败"; FAIL=1; fi

echo

echo "步骤 7b  能力曲线装置自检（oracle 恒 1.0 / 趋势判据边界 / 换尺子必须停）"
echo "=============================================================="
# 为什么单独一步、不并进契约测试：
#   它判的对象是"**曲线本身**可信不可信"，不是某个模块的行为；且零模型、零网络。
#   15 条检查里有 4 条是**真反证**（运行中改打分器必须停 / rounds<3 必须拒 /
#   恒 1.0 的曲线不得判成改进 / 真的逐轮变强时必须判成 IMPROVED）——
#   少这四条，前面那些绿勾就都是恒真的。最后一条尤其不能省：它证明的是
#   `run_loop` **每轮真的重新判分**，而不是把首轮读数复制三遍。
if "$PY" bench/loop_probe.py; then
  echo "  ✅ 曲线装置可信（零模型、零网络）"
else
  echo "  ❌ 曲线装置自检失败（装置不可信 ⇒ 曲线上任何趋势都不成立）"
  FAIL=1
fi

echo
echo "步骤 7c  停滞出口自检（停滞 ≠ 交零：同场景 A/B，零模型、零网络）"
echo "=============================================================="
# 为什么单独一步、不并进契约测试：
#   契约里的 AZ66 只钉「现行行为对」，这一支钉的是**同场景 A/B 确实不同** ——
#   A 臂把 `stall_verdict` 换成恒 `give_up`（第六十八轮之前的行为）⇒ 交付 0 字；
#   B 臂原样 ⇒ 走答案通道交付。两臂读数相同 = 这个探针什么也没测到，必须红。
#   ⛔ 它量的是**交付量**，不是能力：把 0 字变成一段话，和让那段话变对是两件事。
if "$PY" bench/probe_stall.py; then
  echo "  ✅ 停滞出口不再交零（A 臂 0 字 / B 臂走答案通道）"
else
  echo "  ❌ 停滞出口自检失败（停滞又交零，或两臂读数相同 ⇒ 探针没测到东西）"
  FAIL=1
fi

echo
echo "步骤 7d  变异有界自检（界 / 拒绝 / 判定 / 生效，零模型、零网络）"
echo "=============================================================="
# 为什么单独一步、不并进契约测试：
#   契约里的 AZ69–AZ74 钉的是**每一条判据的语义**（界全覆盖、拒绝不钳制、
#   不变式只罚引入的、判定的四个前提、唯一构造点、断段）。
#   这一步钉的是**它们连起来跑得通**，而且 "生效" 那一类是契约钉不了的：
#   它要让真 `Core` 跑三臂，看停滞轮数是否**恰好等于**阈值 ——
#   `apply_mutation` 返回 ok=True 只证明配置对象换了，不证明内核读了它
#   （本项目有一整类"配了没生效"的老账：V1_LLM_RETRIES 读不到、
#   --max-tokens 没进 Planner、external_answer_tool 从未开启）。
#   ⛔ 它**不**量"变异让内核变强了" —— 那需要一套能裁决的题集，本轮没做。
if "$PY" bench/probe_mutate.py; then
  echo "  ✅ 变异有界（界全覆盖 / 越界被拒且不钳制 / 判定四前提 / 旋钮真的生效）"
else
  echo "  ❌ 变异有界自检失败（界开了暗门，或越界被放行，或旋钮没生效）"
  FAIL=1
fi

# ── 步骤 7e：题面与 oracle **同源**的准入器自检（第七十一轮）──────────────
# 7y 关掉判据层的那条读数不是"题难"，而是题面与 oracle 是**两份手写产物**：
#   题面写「最多 n 个字符」，该题自己的 oracle 交的答案是长 4（n=3），
#   而 5/5 评分要求就是这个答案。⇒ 「来源贴着题面」≠「来源满足题面」。
# 这一步钉的是**准入器有牙**，不是"题好"：
#   ① 四条证据（结构禁令 / 后置条件 / 三方对撞 / 同源双射）各配一个坏样本；
#   ② 坏样本必须被**指名**打红（只输出 ✅ 的检测器是死代码）；
#   ③ 常识实现必须被扣分，而"长度合格"挡不住内容错（最硬的一格）；
#   ④ 落盘题目 == 现算结果（手改 jsonl 当场红）。
# ⛔ 它**不**量"这套题能分辨模型强弱" —— 那要真模型读数，本轮没有（§34）。
# ⛔ 也不在这里重新生成 jsonl：产物必须由 `gen_spec_tasks.py` 刻意重跑，
#    而不是被测试顺手刷新（否则"改了生成器"永远不会被 P9 抓到）。
echo
echo "=============================================================="
echo "步骤 7e  题面与 oracle 同源自检（准入器 + 反证组 + 铸题，零模型）"
echo "=============================================================="
if "$PY" bench/probe_taskspec.py > /tmp/v1_taskspec.out 2>&1; then
  grep -E "^  (✅|❌) P" /tmp/v1_taskspec.out | sed 's/^/  /'
  grep -E "^    (常识分|长度合格)" /tmp/v1_taskspec.out | sed 's/^/  /'
  echo "  ✅ $(grep -oE '准入器有牙.*' /tmp/v1_taskspec.out | tail -1)"
else
  echo "  ❌ 准入器自检失败（某条证据是死的，或落盘题目被手改过）："
  tail -20 /tmp/v1_taskspec.out | sed 's/^/  /'
  FAIL=1
fi

# ── 步骤 7f：噪声下限必须在**进程之间**量（第七十三轮）────────────────────
# §35 的实测：同一配置四次观测 0.2857 / 1.0 / 0.2857 / 0.2857，而 `--repeat 2`
# 在第 2 轮给极差 0.2619、在第 5 轮给 0.0000 ⇒ 「轮内极差」本身是随机变量，
# 且系统性下偏（两个样本落在同一边时它就等于 0）。而 mutation_verdict 收到 0
# 就会把任何非零差读成显著。**裁决逻辑没写错，是喂给它的输入量错了地方。**
# 这一步钉的是「下限量对了地方」：
#   ① 同一个 run_id 的多次重复不算多次运行；不到 5 次就拒判（⛔ 不默认 0）；
#   ② 混口径拒判；旧记录没有 run_id 直接抛错（不静默跳过）；
#   ③ 逐题极差取 max（取 min 会系统性低估）；
#   ④ 端到端：同一个 Δ 只换噪声下限 ⇒ KEEP 翻成 NO_EVIDENCE。
# ⛔ 它**不**量"系统更聪明了"——本轮只有噪声下限这一件事。
echo
echo "=============================================================="
echo "步骤 7f  噪声下限自检（独立进程 / 拒判 / 逐题取 max / 结论翻转）"
echo "=============================================================="
if "$PY" bench/probe_noise_floor.py > /tmp/v1_noise_floor.out 2>&1; then
  grep -E "^  (✅|❌) P" /tmp/v1_noise_floor.out | sed 's/^/  /'
  grep -E "^  (轮间极差|轮内极差|各次聚合|逐题极差)" /tmp/v1_noise_floor.out | sed 's/^/  /'
  echo "  ✅ $(grep -oE '噪声下限量对了地方.*' /tmp/v1_noise_floor.out | tail -1)"
else
  echo "  ❌ 噪声下限自检失败（有一条纪律没牙，或池子不合格）："
  tail -20 /tmp/v1_noise_floor.out | sed 's/^/  /'
  FAIL=1
fi

# ── 步骤 7g：进化闭环的驱动器（第七十六轮，§38）──────────────────────────
# 第七十五轮体检的结论是「不能进化」：变异器在内核里零引用、`mutation_verdict`
# 没吃过真数、变异台账从未被写过。本轮的 `bench/mutate_driver.py` 把线接上并**真跑了一圈**。
# 这一步钉的是驱动器的每一格，**零模型调用**：
#   ① 变异必须先过内核的界（界内接受 / 越界拒绝 / 冻结拒绝）；
#   ② env 是唯一通道 ⇒ `clean_env` 必须清掉**全部** `Limits` 字段的同名 env
#      （漏一个，调用者 shell 里恰好设着的值就会溜进"基线臂"）；
#   ③ 守门员「生效 + 单变量」五种坏样本全部拒绝（没生效 / 多变量 / 缺 run_id /
#      缺 limits / base 臂有差异），且安慰剂臂用**它自己**的旋钮判；
#   ④ 池子四重过滤（口径 / solver / limits / run_id）—— 少 solver 就会把
#      裸模型与内核混进一池（第七十三轮那个 0.3095 假噪声的实形）；
#   ⑤ 从冻结证据重算裁决 = NO_EVIDENCE，且**噪声置 0 结论不变**。
# ⛔ 它**不**量"系统更聪明了"。那一圈判的是 NO_EVIDENCE（没证据说更好 = 不改）。
echo
echo "=============================================================="
echo "步骤 7g  变异驱动器自检（注入 / 守门 / 四重分池 / 冻结重算）"
echo "=============================================================="
if "$PY" bench/probe_mutate_driver.py > /tmp/v1_mutate_driver.out 2>&1; then
  grep -E "^  (✅|❌) P" /tmp/v1_mutate_driver.out | sed 's/^/  /'
  echo "  ✅ $(grep -oE '驱动器自检 [0-9]+ 项全过.*' /tmp/v1_mutate_driver.out | tail -1)"
else
  echo "  ❌ 变异驱动器自检失败（有一条纪律没牙，或冻结证据不合格）："
  tail -20 /tmp/v1_mutate_driver.out | sed 's/^/  /'
  FAIL=1
fi

echo
echo "=============================================================="
echo "步骤 7h  答案本体落盘自检（全文 / 指纹 / 截断自陈 / 真跑接线）"
echo "=============================================================="
# 第七十九轮（§42）：同一形态的截断在四处出现过（`why[:200]`、
# `critique._MAX_ANSWER=200`、日志印 5 行、`runs.jsonl` 不存答案），
# 于是量到一个 `AttributeError` 却看不到它写的那行代码。
# 这一步钉的是**量具**，不是能力：
#   ① 默认不截断（5500 字符写读逐字相同）＋ `chars`/`sha256` 自洽；
#   ② 设了 cap 就必须在记录里写 `truncated: true`（不许静默切）；
#   ③ 空缓冲仍写 header（「没答案」≠「没写过」）；空 run_id 直接拒；
#   ④ 边界：落盘键里没有分数/判据字段，且 `mw/` 零引用；
#   ⑤ 反证：模拟历史缺陷（默认 cap=200）⇒ 「逐字相同」这条判据必须变假；
#   ⑥ **接线证明**：真起一个 `run.py --solver oracle` 子进程（离线、零模型调用），
#      要求 `answers.jsonl` 的 run_id 能在 `runs.jsonl` 里找到、最长答案 > 200 字符。
if "$PY" bench/probe_answer_store.py > /tmp/v1_answer_store.out 2>&1; then
  grep -E "^  (✅|❌) P" /tmp/v1_answer_store.out | sed 's/^/  /'
  echo "  ✅ 答案落盘自检 11 项全过"
else
  echo "  ❌ 答案落盘自检失败（量具坏了，结论会变假）："
  tail -20 /tmp/v1_answer_store.out | sed 's/^/  /'
  FAIL=1
fi

echo
echo


# ── 步骤 7i：两个**分析量具**的自证（第八十二/八十三轮）─────────────────────
# 为什么必须在这里，而不是"探针自己跑一下就行"：
#   §45 的「可动作 26 格」与 §46 的「自检检测面」都是**探针给出的读数**，
#   而探针此前**零测试守卫** —— 它坏了、或它恒真，DESIGN 里的数就会静默变假，
#   没有任何一步会红。这正是纪律⑩说的"检测器必须自带反证，否则是死代码"。
# 两件都是零模型、零网络、<5s，所以没有理由不钉。
#   · `probe_error_taxonomy.py --self-check`：失分四类 + 通过，六个分支各一个样本；
#   · `probe_selftest.py --self-check`：2×2 四格 + 抽取器四支（含 `block` 模式
#     的「重定义被检函数 ⇒ 判没牙」）。第二件尤其不能省：它的结论是
#     **否定性**的（自检写不对期望值），而否定性结论最容易由"仪器坏了"伪造。
#   · `probe_structcheck.py --self-check`（第八十四轮）：七类结构违反 + 两类类型错、
#     **`derive()` 在 10 题 oracle 上必须一条都不响**（这是"不变量"唯一能拿到的证明）、
#     题面解析器三处变异 + 「认不出的行必须抛」。第三件同样是否定性/积极性混合读数，
#     而且它给了本轮**正面**结论（结构约束可抽），正面结论更容易由"不变量推松了"伪造。
#   · `probe_delivery.py --self-check`（第八十六轮）：八组反证 —— 三条臂**只差一个车身
#     字段**（否则不是单变量）、截断/空交付检测器有牙、429 重试而 400 不重试、
#     body 级错误（HTTP 200 + error）按码分流、reasoning token 缺字段时返回 `None`
#     而不是 0、`DIRECT_SYSTEM` 从 `run.py` 用 AST 取（不抄字面量 ⇒ 不漂移）、
#     以及**打分口径对账**（用本探针的打分器重算 §49 的冻结答案，6 题必须逐格相同）。
#     最后一条最要紧：探针自己另写一套判分就等于换尺子（§45.9 混口径）。
#   · `llm_proxy.py --self-check`（第八十八轮）：七组反证 —— 响应/请求**逐字节**透传、
#     429 原样透传且 `error.code` 落盘、上游断了必须 502+记账（fail-closed）、
#     缺字段是 `None` 不是 0，以及**反证组**：`--log` 为空时盘上不许多一行
#     （否则"日志条数==请求数"可能只是恒真）。它存在的理由是一条老账：
#     §49 的 10 题跑完之后，日志里**一个状态码都没有**，"为什么重试 65 次"当时不可判定。
#   · `probe_task_attrib.py --self-check`（第八十九轮）：五组反证 —— oracle 满分（窗口闸门
#     本身就是反证：装置坏了它先红）、**`as_is` 必须复现 s88 的冻结读数 5/14**（这是"不换
#     尺子"唯一的机械保证）、把 oracle 的 `_x[:8]` 改成 `[:9]` 分数必须下降（证明打分不是
#     恒真）、修复表的替换目标不存在时必须抛（静默跳过会让「该项 0 收益」变成伪造读数）、
#     归因表算术自洽。它补的是第 79 轮的老账：`why` 截断在 200 字符 ⇒ "sg07 为什么 5/14"
#     一直是 UNKNOWN。实测结论反直觉：**只修最显眼的那条根因（B2 全局去重）= 0 格**，
#     只修另一条（B1 空白规则）= +6 格，两条一起修 = +9 格（逐项之和 ≠ 合计 ⇒ 有耦合）。
#   · `probe_caliber_split.py --self-check`（第八十九轮）：八条反证，两两成对 ——
#     S1 认得出「真·换被测对象」/ S2 认得出「只换通道」/ S3 同类同写 ⇒ SAME /
#     S4 两边都改 ⇒ MIXED / S5 同一台 ollama 的 v4-v6 两种写法 ⇒ CHANNEL_ONLY /
#     S6 本机台账复现冻结读数。**S1 与 S6 必须同时成立**：一个恒返回 MIXED 的分类器
#     会让「SUBJECT_ONLY = 0 对」看起来像重大发现，实际是仪器瞎了。
#   · `llm_proxy.py --inject`（第九十轮）：在原七组之外补 A8/A9/A9b/A9c/A10/A11/A12 ——
#     零注入必须**原字节直通**（不是"解析后原样 dump 碰巧一样"）、注入后摘掉注入键必须
#     **逐键等价**回客户端原文、多出来的字节数必须**恰好**等于注入键的文本长度
#     （这一条是实测逼出来的：第一版固定 `ensure_ascii=False`，内核发的中文
#     `\u53d1`（6 字节）被还原成 UTF-8（3 字节）⇒ 上游包比客户端包还小，
#     语义没错但"只多一个键"再也无法用字节核验）、白名单外的键**构造即抛**
#     （允许注 `messages` 就等于允许改题目）、注入键与客户端已有键重叠必须抛
#     （覆盖 = 悄悄改口径）。
#   · `probe_pair.py --self-check`（第九十轮）：七条反证 —— C1 是唯一**不该拒**的正例
#     （只换 model ⇒ 放行且判 SUBJECT_ONLY）；C2 多一个口径字段不同 ⇒ 拒并**点名**；
#     C3 只换通道 ⇒ 拒；C4 口径逐位相同 ⇒ 拒（那种对只能量噪声）；C5 有样本没量到 ⇒ 拒；
#     C6/C7 **判定阈值两侧都试**（Δ 落在噪声内 ⇒ UNKNOWN；Δ 超过噪声 ⇒ 可分辨）。
#     ⛔ 它存在的理由：第九十轮第一次真的造「只差被测对象」这一对，
#     而"这对是干净的"不能靠肉眼核对 12 个字段 —— 前三次混口径全是"标签说同口径"。
#   · `probe_repair.py --self-check`（第九十三轮）：九条反证 —— C1/C2 打分器两侧
#     （oracle 满分、空实现不满分）；C3 **两臂消息字符数逐题相等**（长度不是变量）；
#     C4 R 臂不泄漏题面里的字符串字面量；C5 **P 臂一个错误词都没有**（否则它不是安慰剂）；
#     C6 EXC/ASSERT 两个分支都真能走到（不是死代码）；C7 冻结算复算 == §59 落盘读数；
#     C8 报告器的增益/改坏/净/噪声算术。⛔ 它量「检出之后能不能改对」，
#     而这一轮量出来的是**负数** —— 正因为预注册要求成对臂，才没把 −2 读成「重试没用」。
#   · `probe_closeoverrun.py --self-check`（第九十六轮）：七条反证 C0~C6 ——
#     C0 断言桩的 `timeout_s` **真的被读到**（`StubLLM` 没有这个属性 ⇒ 走 return inf
#     ⇒ 整条裁剪路径不存在，后面全是假绿）；C1 `close_overrun_s=0` 时与旧公式逐位相同；
#     C3 **端到端**真跑一次收尾（旧臂交给 `history`、新臂交给答案，约 11s）；
#     C5 未收尾时给了宽限也不加。⛔ 它比别的量具慢十倍，因为 C3 必须真睡 ——
#     把"差 1 秒"变成可复跑，值这 11 秒。
#   · `audit_form.py --self-check`（第九十五轮）：四条反证 —— R1 分类器认得出
#     CODE/EMPTY/PROSE；R2 手造一个 `finish({...})` 壳必须被认出且**逐字还原**载荷；
#     R3 壳那一份的分**确实低于**载荷（否则说明打分器自己能剥壳，本工具的数就没意义）；
#     R4 只剥 `finish` —— 别的工具渲染剥出来当答案就是伪造。
#   · `probe_discard.py --self-check`（第九十七轮）：六条反证 D1~D6 ——
#     D1 装得下**不入选** / D2 超的入选且秒数算对 / D3 **只改 `overrun`**：收尾窗口的
#     上限必须变、探索窗口必须不变（两条分支都得是活的）/ D4 挂不上任务的进 `orphan`
#     （分母变小会让比例变好看）/ D5 `status!=200` 不算丢失 / D6 上限算成 0 的进 `unsure`
#     （⛔ 装置误差不许冒充缺陷）。它把「代理侧成功返回、内核侧没拿到」变成可复跑的数 ——
#     §62/§63 盯了两轮「差 1 秒」，但只有把台账与日志对账才知道**差的是哪一次、差几秒**。
#   · `probe_stallpath.py --self-check`（第九十八轮）：七条反证 P1~P7，两向成对 ——
#     P1/P2 同一套断言跑两种候选组合（全等臂**必须**记停滞并填 reseek / 混合臂
#     **必须**不记也不填，且走 else 支 `⛔ 全部候选被拦`）；P3 把空答案换成非空
#     ⇒ 混合消失 ⇒ P2 **必须红**；P4 每轮都带一个新动作（全等永不可能，且候选预算
#     抬到 99）⇒ P1 **必须红**；P5 `max_stall=1` ⇒ 阈值一到就收尾 ⇒ P1 **必须红**；
#     P6 没有步骤头的重复行必须进 `orphan`；P7 三档分类互斥且穷尽。
#     ⛔ 它存在的理由：`reseek`（「本轮全灭 ⇒ 换形态再问」）的判据是**全等**
#     `repeats == len(cands)`，而真实 309 份任务日志里 81 次重复有 **33 次是混合**
#     ⇒ 既不记停滞也不换形态；48 次全等的宽度**全是 1**（那一步本来就没有第二候选）。
#     判据够不到"有选择空间"的那一半，这件事只能靠两侧（内核两臂 + 日志分母）同时说。
#     ★ 第九十九轮（§66）把反证扩到 **十五条 P1~P15**，新加的钉的是**判据开关**
#     （`V1_STALL_ON_BLOCKED`）：P8/P9 一对（`B0` 关闸仍 `cascade`、`B1` 开闸翻成
#     `stalled` 且 `stalled==2`/`reseek==1`/提示词含 reseek 块）、P10/P11 一对
#     （全等步两档**逐位相同**）、P12/P13 一对（`repeats == 0` 的全拦**两档都不许**
#     算停滞 —— 这一对就是对 §65.9 草稿的**收窄**证据：照抄草稿时 P12 会红）、
#     P14/P15（三条撤回态各单红；`blocked_skipped` 零值也在 `stats`）。
#   · `audit_legs.py --self-check`（第九十九轮）：五条反证 A1~A5 ——
#     重复跳过的下一步里，那条「另一条腿」到底是**被执行**、**被拦**、还是**被预算掐掉**。
#     A1/A2 必须能把「被执行」与「被拦」**分开归**（⛔ 一个永远归不出「被拦」的分类器，
#     给出的 0 和真实的 0 同形 —— 而本轮结论正是「被拦 = 0」）；A3 全等步不进混合；
#     A4 ★回归：真实日志格式（`HH:MM:SS` + 前导空格 + `↳`）必须认成执行 ——
#     第一版写 `split(" ",1)[-1].startswith("↳")`，右半保留前导空格 ⇒ 恒 False
#     ⇒ 6 次「被执行」被误归「预算掐掉」，而那个方向**对结论有利**（§62 自我打脸）；
#     A5 步骤头后的重复必须记账（不许静默丢）。
#   · `probe_runnable.py --self-check`（第一百轮）：四条反证 S1~S4 ——
#     S1 十二例检测器样本，九个**不该报**的分支（顶层 import / 函数内 import /
#     `import as` / `from … import` / 闭包 / 推导式 / `except as` / `with as` /
#     lambda 闭包）+ 三个该报的；S2 与内核 `mw.verify.undefined_names` **逐条对拍**
#     （两条独立实现不一致 ⇒ 至少一条错，而"哪条错"不能靠读代码决定）；
#     S3 `sha` 去重（同一份答案重复 append 不许灌分母）；S4 空交付必须进 `empty`
#     而**不是** `no_def`（K1 与"交了错的代码"是两种病）。
#     ⛔ 它存在的理由：N3「`sg10` 缺 `import re` 的发生率」第一版量出 38.3%，
#     而那是**装置误差**（顶层 `import re` 与真缺 `import re` 返回同一个结果）⇒
#     修完是 7.3%。第二次仍不够：34 份坏答案按 sha 去重只有 **5 种**，
#     其中 `e63dea5c…` 出现 21 次、`38f40729…` 出现 7 次
#     ⇒ 不是"模型反复犯不同的错"，是"模型一次都没改，重交了同一份"。
#   · `probe_artifact_suite.py --self-check`（第一百轮 §70）：七条 A1~A7，
#     其中三条是**反证**而不是描述——
#     ⭐ A2 把 test **倒序**跑，分数必须不变（每条 test 自带 setup ⇒ 断言之间不共享状态）；
#     ⭐ A4 用真打分器跑 `pure`（返回值完全正确、但**什么都没写出去**）⇒ 均分必须 < 0.10。
#        这是"尺子形态确实变了"的运行时证明：同一类实现在 v1~v3 上满分，在这里 0.0469；
#     ⭐ A6 把一条 test 的期望字节改一个字符 ⇒ 分数必须掉（否则断言恒真）；
#     A5 冻结文件 == 生成器重生成（防"改了生成器忘了重冻结"）。
#     ⛔ 它存在的理由：v1~v3 的 26 题答案全是纯函数、断言全是 `f(x) == y`，
#     **没有一维**在测"做成了没有"。新尺子换了形态，就必须先证明它真的在量新东西 ——
#     否则"128 格"只是 128 个看起来像断言的字。
#     ⚠️ 它**不**证明"内核会用工具"：`ExternalScorer` 只吃 `{task, answer}`，看不到轨迹。
echo "=============================================================="
echo "步骤 7i  分析量具自证（失分分类器 / 自检探针 / 结构约束 / 选择器 / 交付面 / 代理 / 失分归因 / 口径拆分 / 两臂判定 / 交付物过闸 / 重试修复 / 交付形态 / 收尾宽限 / 丢弃对账 / 停滞路径 / 重复步对账 / 交付物可运行性 / 产物型尺子，零模型、零网络）"
echo "=============================================================="
for _p in probe_error_taxonomy probe_selftest probe_structcheck probe_select probe_delivery llm_proxy probe_task_attrib probe_caliber_split probe_pair probe_closeout_gate probe_repair audit_form probe_closeoverrun probe_discard probe_stallpath audit_legs probe_runnable probe_artifact_suite; do
  if "$PY" "bench/$_p.py" --self-check > "/tmp/v1_${_p}.out" 2>&1; then
    echo "  ✅ $_p --self-check"
    grep -E "^  ✅|^  ⚠️" "/tmp/v1_${_p}.out" | sed 's/^/     /'
  else
    echo "  ❌ $_p --self-check 失败（量具坏了 ⇒ 依赖它的结论一律作废）："
    tail -20 "/tmp/v1_${_p}.out" | sed 's/^/     /'
    FAIL=1
  fi
done

# ── 步骤 7j：题面文字 ⇒ 约束 必须等于 spec ⇒ 约束（第八十四轮）─────────────
# 这是 §47 唯一一条**正面**主张（"结构约束能从题面文字抽出来"）的守卫。
# ⛔ 不放进契约层：`probe_structcheck` 是测量装置，把装置挂进契约就是"被测与测量混在一起"。
# 反证已实测：把 squeeze 的 min_run 读成 n+1 ⇒ sg07/sg09/sg10 三题当场红。
echo
echo "=============================================================="
echo "步骤 7j  题面→约束 == spec→约束（零模型、零网络）"
echo "=============================================================="
if "$PY" bench/probe_structcheck.py --pin > /tmp/v1_pin.out 2>&1; then
  echo "  ✅ probe_structcheck --pin"
  sed 's/^/     /' /tmp/v1_pin.out
else
  echo "  ❌ --pin 失败（§47 的正面主张已失效）："
  sed 's/^/     /' /tmp/v1_pin.out
  FAIL=1
fi

# ── 步骤 7k：选择器读数（第八十五轮）─────────────────────────────────────────
# 为什么它不是"再跑一个探针"，而是一步**读数**：
#   §48 的结论是**否定性**的（闸当选择器在 v1 上 −12 格 ⇒ 有害）。否定性结论最容易被
#   两种方式弄成假绿：① 探针坏了（⇒ 已挂在 7i 的 `--self-check` 里）；
#   ② 有人把"有害"当成待修的 bug，改权重/改判据把它"修绿"。
#   ⇒ 这里只**印**读数，退出码不判红（`--report` 返回 1 是它的正常输出之一）。
# ⛔ 只跑 `--pool v1`：那个池子的两条链都冻结在 `bench/evidence/select_s85/` 里，
#   新克隆的仓库也能跑。`--pool v2` 读 `bench_out/`（gitignore 的本地台账）
#   ⇒ 不放进收口脚本，否则会在别人的机器上假红。
echo
echo "=============================================================="
echo "步骤 7k  选择器读数（v1 池，零模型、零网络，**不判红**）"
echo "=============================================================="
"$PY" bench/probe_select.py --report --pool v1 > /tmp/v1_probe_select.out 2>&1
echo "  退出码 $?（=1 是「有害」这条读数的正常取值，不是失败）"
sed -n '/二、机会/,$p' /tmp/v1_probe_select.out | sed 's/^/     /'

# ── 步骤 7l：交付面探针的**打分口径对账**（第八十六轮）──────────────────────
# 为什么这步判红、而 7k 不判红：7k 印的是**结论**（"闸当选择器有害"），结论可以被
# 更好的实验推翻，所以只印不判；7l 查的是**尺子对不对** —— 探针用自己的 `score_of`
# 重算 §49 冻结答案，只要有一格与台账记的不同，就说明探针在换尺子（§45.9）。
# 尺子错了，后面所有交付读数全废 ⇒ 必须红。
# ⚠️ `--run` 永不进收口：它要 key、要花钱、要联网，且结果是**通道读数**不是回归项。
echo
echo "=============================================================="
echo "步骤 7l  交付面探针口径对账（重算 §49 冻结答案，零模型、零网络）"
echo "=============================================================="
if "$PY" bench/probe_delivery.py --replay > /tmp/v1_deliver_replay.out 2>&1; then
  echo "  ✅ probe_delivery --replay"
  sed 's/^/     /' /tmp/v1_deliver_replay.out
else
  echo "  ❌ 重算与台账不一致（探针在换尺子，交付读数一律作废）："
  sed 's/^/     /' /tmp/v1_deliver_replay.out
  FAIL=1
fi

echo
echo "=============================================================="
echo "步骤 8  门禁隔离自检（生产目录指纹必须未变）"
echo "=============================================================="
# 为什么要这一步：步骤 6f/6g 这两个探针会真的跑 `Core(...)`.run()`。
# 「脚本里写了 export」只能说明**我打算隔离**，不能说明**真的隔离了** ——
# 2026-10-29 那次就是「打算」和「事实」差了一条赋值。
# 所以判据必须落在**结果**上：跑完之后生产库指纹必须逐字节未变。
PROD_DB_AFTER="$(_prod_sha state/memory.db)"
PROD_CRIT_AFTER="$(_prod_sha state/critique.jsonl)"
if [ "$PROD_DB_AFTER" = "$PROD_DB_BEFORE" ]; then
  echo "  ✅ 生产记忆库未被触碰（state/memory.db sha256=${PROD_DB_AFTER:0:16}）"
else
  echo "  ❌ 生产记忆库被改动："
  echo "       前 ${PROD_DB_BEFORE:0:16} → 后 ${PROD_DB_AFTER:0:16}（state/memory.db）"
  echo "     ⇒ 有步骤没隔离 `V1_STATE_DIR`（探针会真的 Core().run()）。"
  echo "     ⇒ 这就是 2026-10-29 那 36 条污染的同一机制；累积读数在此之前不可信。"
  FAIL=1
fi
if [ "$PROD_CRIT_AFTER" = "$PROD_CRIT_BEFORE" ]; then
  echo "  ✅ 评审账本未被触碰（state/critique.jsonl sha256=${PROD_CRIT_AFTER:0:16}）"
else
  echo "  ❌ 评审账本被改动：前 ${PROD_CRIT_BEFORE:0:16} → 后 ${PROD_CRIT_AFTER:0:16}"
  echo "     ⇒ 同上：门禁在写生产账本。"
  FAIL=1
fi

echo
if [ "$FAIL" -eq 0 ]; then
  echo "全部通过。"
else
  echo "有步骤失败（见上）。"
fi
exit "$FAIL"