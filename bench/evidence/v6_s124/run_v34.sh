#!/usr/bin/env bash
# V34 —— 决定性探针：同一个 prompt 重放 N 次，答案唯一吗？（§99）
#
# 预注册：bench/evidence/v6_s124/PREREG.md（起跑前落盘；`ls -lT` 是唯一时间戳证据）
# 探针：bench/evidence/v6_s124/probe_v34_replay.py
#
# 为什么先量这一条：V33 的纪律 41（「3/3 实为 1 次观察」）依赖一个从未直测的前提
#   ——「T=0 + 同一个 prompt ⇒ 同一个答案」。离线同 state 不同答案的嫌疑有 3 组，
#   但 `--state-dump` 不是完整 prompt ⇒ 不闭合。本探针把同一份 messages 直接连发。
#
# 组 A：n=1（与 V33 的 e1wi 首问同形）  组 B：n=3
# 每组 N=16 次 ⇒ 32 次调用，全部经过与 V33 同一个 `bench/llm_proxy.py`
#   （同样注入 `{"reasoning_effort":"none"}` ⇒ 协议固定，模型是唯一变量）。
set -uo pipefail
V1ROOT=/Users/linshaojie/V1
W="${V34_WORK:-/tmp/v34}"
PY=/Users/linshaojie/.workbuddy/binaries/python/versions/3.13.12/bin/python3
PORT=8835
N="${V34_N:-16}"

mkdir -p "${W}"
cd "${V1ROOT}" || { echo "❌ 进不去 ${V1ROOT}"; exit 2; }

echo "========== V34 重放探针  N=${N}（每组） model=qwen2.5:7b =========="
echo "开始 $(date '+%Y-%m-%d %H:%M:%S')  工作目录=${W}"

#: ⚠️ 必须在任何 `import mw.config` 之前导出：LLM 字段的 default_factory
#:    在 **import 时**就求值（`config.py` 的注释钉过这条），之后再设无效。
export V1_LLM_BASE_URL="http://127.0.0.1:${PORT}/v1"
export V1_LLM_API_KEY="ollama-local"
export V1_LLM_MODEL="qwen2.5:7b"
export V1_LLM_TEMPERATURE=0
export V1_LLM_TIMEOUT_S=120
export V1_LLM_RETRIES=1
export V1_LLM_MAX_TOKENS=1024
export V1_STATE_CHARS=2400
export V1_STATE_DIR="${W}/state"
export V1_LOG_DIR="${W}/logs"
export V1_BENCH_DIR="${W}/bench"
export no_proxy=127.0.0.1 NO_PROXY=127.0.0.1

# ── H0 装置自检（全部通过才允许出读数）─────────────────────────────────
echo
echo "--- H0 装置自检 ---"
if ! curl -s --noproxy 127.0.0.1 -m 5 http://127.0.0.1:11434/api/tags > /dev/null 2>&1; then
  echo "❌ H0-a 失败：ollama 不可达 ⇒ 不出读数"; exit 3
fi
echo "  ✅ H0-a ollama 可达"

#: H0-b 温度必须是 0。历史事故：内核三处调用点都没传 temperature，
#: 悄悄落到写死的 0.2，而口径签名写着 0.0 ⇒「T=0 不可复现」。
"${PY}" - <<'PY' > "${W}/p0_env.txt" 2>&1
import os, sys
sys.path.insert(0, "/Users/linshaojie/V1")
from mw import config as cfg
s = cfg.settings()
print(f"base_url={s.llm.base_url}")
print(f"model={s.llm.model}")
print(f"temperature={s.llm.temperature}")
print(f"thinking={s.llm.thinking!r}")
print(f"llm_max_tokens={s.limits.llm_max_tokens}")
print(f"state_chars={s.limits.state_chars}")
ok = (float(s.llm.temperature) == 0.0)
print(f"TEMP_ZERO={ok}")
raise SystemExit(0 if ok else 1)
PY
if ! grep -q "TEMP_ZERO=True" "${W}/p0_env.txt"; then
  echo "❌ H0-b 失败：temperature 不是 0"; cat "${W}/p0_env.txt"; exit 3
fi
echo "  ✅ H0-b temperature=0（$(grep '^temperature=' "${W}/p0_env.txt")）"

#: H0-c 待复现的 state 必须与 V33 `c0_r1` 的 e1wi it=1 逐字节同一份
"${PY}" - <<'PY' > "${W}/p0_state.txt" 2>&1
import hashlib, json
from pathlib import Path
p = Path("/tmp/v33/states_c0_r1.jsonl")
res = {}
for line in p.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line:
        continue
    d = json.loads(line)
    if d.get("task") == "e1wi" and int(d.get("it") or 0) == 1:
        k = hashlib.sha256(d["state"].encode()).hexdigest()[:16]
        res[k] = len(d["state"])
        break
print("state_sha16/len =", res)
want = "7fdac54a236b58a8"
print("MATCH=" + str(want in res))
PY
cat "${W}/p0_state.txt"
if ! grep -q "MATCH=True" "${W}/p0_state.txt"; then
  echo "❌ H0-c 失败：待复现的 state 与 V33 c0_r1 不同（或 /tmp/v33 已清）"; exit 3
fi
echo "  ✅ H0-c state 与 V33 c0_r1 逐字节同一份"

"${PY}" bench/llm_proxy.py --upstream http://127.0.0.1:11434 \
    --log "${W}/proxy.jsonl" --listen "127.0.0.1:${PORT}" \
    --inject '{"reasoning_effort":"none"}' > "${W}/proxy.out" 2>&1 &
PP=$!
sleep 1.5
if ! kill -0 "${PP}" 2>/dev/null; then
  echo "❌ H0-d 失败：代理没起来"; cat "${W}/proxy.out"; exit 3
fi
echo "  ✅ H0-d 代理已起（pid=${PP}，端口 ${PORT}）"
trap 'kill "${PP}" 2>/dev/null' EXIT

echo
echo "--- 重放 ---"
V34_N="${N}" "${PY}" -u bench/evidence/v6_s124/probe_v34_replay.py 2>&1 | tee "${W}/replay.txt"
RC=${PIPESTATUS[0]}

echo
echo "--- 代理台账 ---"
LINES=$(wc -l < "${W}/proxy.jsonl" 2>/dev/null | tr -d ' ')
echo "proxy.jsonl = ${LINES} 行（期望 $((2 * N))）"
echo "结束 $(date '+%Y-%m-%d %H:%M:%S')  EXIT=${RC}"
exit "${RC}"
