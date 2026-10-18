#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# 2x 档补测（2026-10-18，第二十六轮）—— 只补 `h01@2x` 在 OFF 臂的那一格。
#
# 事前登记在 DESIGN §16.25，**跑之前**就写死了（R1–R5 / P1–P4）。
# 本脚本只负责执行，不含任何判读；判读的唯一实现仍是
# `bench/probe_external_real.py`（R5：不许出现第二份判据）。
#
# ⛔ 纪律
#   · 每臂**只跑一次**，失败就是失败，不许重跑直到通过。
#   · 题集**不许裁**（裁成单题会改 `suite_sha`，那就是换了尺子）。
#   · 冻结证据 `bench/evidence/external_s24_2x/` 一个字节都不动。
#   · 本脚本跑起来之后**不许改**（边跑边改 = bash 按字节偏移重读 = 错位重跑）。
#
# 用法
#   bench/run_h01_supplement.sh A /tmp/v1s25a     # 版本臂 A = 66b9e41
#   bench/run_h01_supplement.sh B /Users/linshaojie/V1   # 版本臂 B = 849d8ad
# ---------------------------------------------------------------------------
set -u

LABEL="${1:?用法: run_h01_supplement.sh <A|B> <代码根>}"
CODE="${2:?用法: run_h01_supplement.sh <A|B> <代码根>}"

PY=/Users/linshaojie/.workbuddy/binaries/python/versions/3.13.12/bin/python3
OUT="${V1_OUT:-/tmp/v1_s25_h01}"

export V1_LLM_BASE_URL=http://localhost:11434/v1
export V1_LLM_API_KEY=ollama
export V1_LLM_MODEL=qwen2.5:7b
#: 本地端点必须绕开代理，否则请求走代理、行为失真（第二十五轮实测踩过）。
export NO_PROXY=127.0.0.1
export no_proxy=127.0.0.1

DIR="$OUT/ho_off_$LABEL"
rm -rf "$DIR"; mkdir -p "$DIR"

#: 把**实际执行的**命令与环境落盘 —— 事后复算靠它，不靠记忆。
{
  echo "# 版本臂 $LABEL"
  echo "code_root=$CODE"
  echo "git=$(cd "$CODE" && git rev-parse HEAD 2>/dev/null)"
  echo "started=$(date '+%Y-%m-%dT%H:%M:%S')"
  echo "budget=2 suite=bench/heldout arm=OFF fresh=yes"
  echo "model=$V1_LLM_MODEL temperature=0 max_tokens=1024"
} > "$DIR/provenance.txt"

cd "$CODE"

V1_EXTERNAL_ANSWER_TOOL="" \
V1_BENCH_DIR="$DIR/bench" V1_STATE_DIR="$DIR/state" \
  "$PY" run.py --bench --solver llm --temperature 0 --max-tokens 1024 \
    --budget 2 --fresh --no-external --suite-dir bench/heldout \
    > "$DIR/out.log" 2>&1
echo "rc=$?" >> "$DIR/provenance.txt"
echo "ended=$(date '+%Y-%m-%dT%H:%M:%S')" >> "$DIR/provenance.txt"

# ── 只打印事实，不判读 ────────────────────────────────────────────────────
echo "=================== 版本臂 $LABEL ==================="
cat "$DIR/provenance.txt" | sed 's/^/  /'
echo
"$PY" - "$DIR" <<'PY'
import json, pathlib, sys
d = pathlib.Path(sys.argv[1])
p = d / "bench" / "runs.jsonl"
if not p.exists():
    print("  ❌ 没有 runs.jsonl ⇒ 这一臂没跑完")
    raise SystemExit(0)
rows = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
scores, el = {}, 0.0
for r in rows:
    rep = r["report"]
    el += float(rep.get("elapsed_s") or 0)
    for t in rep["per_task"]:
        scores[t["id"]] = float(t["score"])
    print(f"  记录 领域={rep.get('suite')} n={len(rep['per_task'])} "
          f"elapsed={rep.get('elapsed_s')}s 刻度={r.get('caliber',{}).get('budget_tag')} "
          f"sig={r.get('caliber_sig')}")
print(f"  合计 elapsed={el:.1f}s  已量 {len(scores)} 题")
for k in sorted(scores):
    print(f"    {k:20s} {scores[k]:.4f}")
print(f"  h01 已量到 = {'h01_int_to_roman' in scores}")
PY
