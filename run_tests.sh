#!/usr/bin/env bash
# V1 一键验证。五步都会跑，任一步失败即整体失败。
#
#   ./run_tests.sh
#
# 不依赖任何第三方包。只用系统/托管的 Python 3。
set -uo pipefail

PY="${PY:-/Users/linshaojie/.workbuddy/binaries/python/versions/3.13.12/bin/python3}"
if [ ! -x "$PY" ]; then PY="$(command -v python3)"; fi
cd "$(dirname "$0")"

FAIL=0

echo "=============================================================="
echo "步骤 1/6  依赖审计（必须零第三方依赖）"
echo "=============================================================="
if "$PY" - <<'EOF'
import ast, sys, pathlib
ALLOWED = {
    "argparse","ast","collections","copy","dataclasses","hashlib","json","math","os","re",
    "shlex","sqlite3","statistics","subprocess","sys","tempfile","time","tokenize",
    "typing","urllib","pathlib","__future__","run","mw",
}
bad = []
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
echo "步骤 2/6  契约测试（离线；K 静默降级 / L 外部能力 / O 停滞 / Q 收尾"
echo "                    U 跨领域 / V 因果闭环 / W 抽象读数 / X 血缘完整性 /"
echo "                    Y 失配信号的分辨力 / Z 内核读数落盘 / AA 模块接线 /"
echo "                    AB 闭环通量 / AC 价值层 / AD 自我模型 / AE 消融安全 /"
echo "                    AF 失败签名（第二层判据：缺了会看到什么现象）/"
echo "                    AG 外部评审（只读 / 防自评 / 防跨口径）/"
echo "                    AH 可复核性（语言与沟通的真判据）/"
echo "                    AI 自我模型（跨会话账本 / 预测校准 / 口径闸承重）/"
echo "                    AJ 族预测力闸（测不出预测力 ⇒ 不许据此行动））"
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
echo "步骤 3/6  外部锚层契约测试（含反证组：假尺子必须被判红）"
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
echo "步骤 4/6  离线端到端自检（零网络 / 零密钥）"
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
echo "步骤 5/6  跨领域冒烟（同一把打分器 × 每个领域，oracle 参照）"
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
echo "步骤 6/6  自我模型判据（跨会话预测校准 + 三个反证组 + 族预测力闸）"
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

echo
if [ "$FAIL" -eq 0 ]; then
  echo "全部通过。"
else
  echo "有步骤失败（见上）。"
fi
exit "$FAIL"
