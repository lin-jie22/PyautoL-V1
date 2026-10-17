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
echo "步骤 1/5  依赖审计（必须零第三方依赖）"
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
echo "步骤 2/5  契约测试（离线；K 静默降级 / L 外部能力 / O 停滞 / Q 收尾"
echo "                    U 跨领域 / V 因果闭环 / W 抽象读数 / X 血缘完整性 /"
echo "                    Y 失配信号的分辨力 / Z 内核读数落盘 / AA 模块接线 /"
echo "                    AB 闭环通量 / AC 价值层 / AD 自我模型 / AE 消融安全）"
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
echo "步骤 3/5  外部锚层契约测试（含反证组：假尺子必须被判红）"
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
echo "步骤 4/5  离线端到端自检（零网络 / 零密钥）"
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
echo "步骤 5/5  跨领域冒烟（同一把打分器 × 每个领域，oracle 参照）"
echo "=============================================================="
# 为什么要有这一步：`--suite-dir` 的价值是"**每个领域**都非零"。
# 这条冒烟只用 oracle 参照（不需要模型、几秒钟），但能保证：
#   ① 每个领域的数据仍能被 calibrate 判为"有分辨力"；
#   ② 每个领域的 oracle 仍拿满分（= 任务集与打分器没漂移）。
# 它同时替「跨领域」这件事把"数据坏了"变成**回归期就能发现**的事，
# 而不是等到某天真跑模型时才发现某个领域全是 0。
if V1_BENCH_DIR=/tmp/v1_cross_ci "$PY" run.py --bench --offline --solver oracle \
     --suite-dir bench/tasks > /tmp/v1_cross.out 2>&1; then
  echo "  ✅ $(grep -oE '可信领域 [0-9]+/[0-9]+.*' /tmp/v1_cross.out | tail -1)"
else
  echo "  ❌ 跨领域冒烟失败（某领域的校准或 oracle 不满分）："
  tail -12 /tmp/v1_cross.out | sed 's/^/  /'
  FAIL=1
fi

echo
if [ "$FAIL" -eq 0 ]; then
  echo "全部通过。"
else
  echo "有步骤失败（见上）。"
fi
exit "$FAIL"
