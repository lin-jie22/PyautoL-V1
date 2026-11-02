#!/usr/bin/env bash
# V43 复验：修掉「`--allow-shell` 只在一个分支上通电」之后，同一个真实任务能不能做完
#
# 预注册：bench/evidence/v6_s133/PREREG.md（冻结于 2026-11-02 13:02）
# 判据三档：PASS / PARTIAL / FAIL，全部预注册，见 §5。
#
# ⛔ 分母是 1（单例）。这一格不许写成「成功率」。
set -uo pipefail
V1ROOT=/Users/linshaojie/V1
W="${V43_WORK:-/tmp/v43}"
WS="${W}/ws"
ST="${W}/state"

rm -rf "${W}"
mkdir -p "${WS}" "${ST}"
cp "${V1ROOT}/bench/evidence/v6_s133/sales.csv" "${WS}/sales.csv"

#: ⚠️ 必须在任何 `import mw.config` 之前导出（import 时求值）。
export V1_LLM_MODEL="qwen2.5:7b"
export V1_LLM_API_KEY="ollama-local"
export V1_LLM_BASE_URL="http://127.0.0.1:11434/v1"
export V1_LLM_TIMEOUT_S=120
export V1_LLM_RETRIES=1
export V1_LLM_MAX_TOKENS=1024
export V1_LLM_TEMPERATURE=0
export V1_MAX_WALL_S=240
export V1_MAX_LLM_CALLS=24
export V1_MAX_STEPS=12
export V1_STATE_CHARS=2400
export V1_MAX_CANDIDATES=3
export V1_CAND_FAILOVER=1          # 与臂 B 同一旋钮，只改「接线」这一件
export V1_WORKSPACE="${WS}"
export V1_STATE_DIR="${ST}"
export V1_LOG_DIR="${W}/logs"
export no_proxy=127.0.0.1 NO_PROXY=127.0.0.1
mkdir -p "${W}/logs"

GOAL="工作目录里有 sales.csv（列为 月份,区域,金额）。请计算每个区域的金额合计，按合计从高到低生成一张 markdown 表格，写入工作目录下的 report.md。表格两列为 区域|合计。"

echo "========== V43 产品验收第一格：真实任务 + shell 通路已接通 =========="
echo "工作区=${WS}"
echo "开始 $(date '+%Y-%m-%d %H:%M:%S')"

cd "${V1ROOT}" || { echo "❌ 进不去 ${V1ROOT}"; exit 2; }
if ! curl -s --noproxy 127.0.0.1 -m 5 http://127.0.0.1:11434/api/tags > /dev/null 2>&1; then
  echo "❌ ollama 不可达 ⇒ 不出读数"; exit 3
fi

START=$(python3 -c 'import time;print(time.time())')
python3 run.py --goal "${GOAL}" --allow-shell --budget 2 > "${W}/run.log" 2>&1
RC=$?
END=$(python3 -c 'import time;print(time.time())')
echo "RC=${RC} 墙钟=$(python3 -c "print(f'{$END-$START:.1f}s')")"

# ── 判据（预注册 §5）────────────────────────────────────────────────
echo
echo "--- 产物 ---"
if [ -f "${WS}/report.md" ]; then
  echo "✅ report.md 存在（$(wc -c < "${WS}/report.md") 字节）"
  cat "${WS}/report.md"
else
  echo "❌ report.md 不存在"
fi
echo
echo "--- workspace 全部文件 ---"
ls -la "${WS}"

# ── 三档判定 ───────────────────────────────────────────────────────
python3 - "${WS}" <<'PY'
import sys, pathlib, re
ws = pathlib.Path(sys.argv[1])
rp = ws / "report.md"
if not rp.exists():
    # 有没有真的执行过 run_shell？
    logs = pathlib.Path(sys.argv[1]).parent / "run.log"
    txt = logs.read_text(encoding="utf-8", errors="replace") if logs.exists() else ""
    if "shell 未开启" in txt:
        print("判定 = FAIL（run_shell 仍被 guard 拦 ⇒ 接线没修好）")
    elif "run_shell ok=True" in txt or "run_shell ⇒ ✔" in txt:
        print("判定 = PARTIAL（run_shell 执行了，但 report.md 没写出来 ⇒ 模型不会）")
    else:
        print("判定 = PARTIAL/UNKNOWN（没写出文件；日志里看不到 run_shell 的执行痕迹）")
    sys.exit(0)
t = rp.read_text(encoding="utf-8", errors="replace")
nums = {}
for k in ("华北", "华南", "华东"):
    m = re.search(k + r"[^\n]*?(\d[\d,]*)", t)
    nums[k] = int(m.group(1).replace(",", "")) if m else None
want = {"华北": 2700, "华南": 2000, "华东": 700}
ok = nums == want
print(f"读到 {nums}  期望 {want}")
print("判定 = PASS" if ok else "判定 = PARTIAL（文件写了，但数字不对）")
PY
