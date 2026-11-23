#!/bin/bash
# V236 部署态：**MODEL 轴**接进 L5 闭环 —— 起点 5/6（V235 最终 best），
# 换路用 qwen2.5:14b（离线小样命中 2/4），看 best 能否真抬到 6/6 ⇒ MET。
#
# ⛔ 起点 obs 用四位 `0.8333`（与账本同精度，纪律 163）。
# ⛔ `--ledger` 必须是空账本：`state/score_ledger.jsonl` 里 t01 有一行跨口径的 1.0（§306.7-1）。
#
# 期望轨迹（预注册 §9）：R1 `act 5/6 ⇒ NOT_MET ⇒ 换路命中 6/6 ⇒ MET ⇒ 提新一代`
#                        R2 `base=1.0 ⇒ NOT_MET ×N ⇒ STUCK_BEST ⇒ IDLE`
set -u
cd /Users/linshaojie/V1
unset PYTHONPATH
export CODEBUDDY_BROKERED_FS_HOOK_ENABLED=0
export PATH=/usr/bin:/bin:/usr/sbin:/sbin
PY=/Users/linshaojie/.workbuddy/binaries/python/versions/3.13.12/bin/python3
export V1_LLM_BASE_URL=http://localhost:11434/v1
export V1_LLM_API_KEY=ollama
export V1_LLM_MODEL=qwen2.5:7b
export V1_LLM_TEMPERATURE=1.0

D=bench/evidence/l6_s307
: > "$D/empty_ledger.jsonl"
: > "$D/order_v236.txt"
/bin/cp "$D/obs_start_0833.jsonl" "$D/obs_d.jsonl"
/bin/rm -f "$D/log_d.jsonl"

one_round () {          # $1=swap $2=models
  local SWAP=$1 MODELS=$2
  local STAMP=$(/bin/date +%H%M%S)
  $PY -u run.py --goals --goals-act 1 --goals-swap "$SWAP" \
    --goals-swap-models "$MODELS" \
    --goals-suite-dir bench/evidence/l5_s266/suite_one \
    --goals-obs "$D/obs_d.jsonl" \
    --goals-log "$D/log_d.jsonl" \
    --ledger "$D/empty_ledger.jsonl" \
    --solver llm --temperature 1.0 --budget 1 --no-external \
    > "$D/round_d_${STAMP}.log" 2>&1
  local rc=$?
  echo "d	swap=$SWAP	models=$MODELS	rc=$rc	$STAMP" >> "$D/order_v236.txt"
  /usr/bin/grep -E "^  换路|^  判据行|^  状态|^  RAISE|转运维" \
    "$D/round_d_${STAMP}.log" | /usr/bin/tail -5
}

for i in 1 2 3; do
  echo "== R${i} swap=4 models=qwen2.5:14b =="
  one_round 4 "qwen2.5:14b"
done

echo "== 汇总 =="
$PY - <<'PY'
import json, pathlib
D = pathlib.Path("bench/evidence/l6_s307")
obs = [json.loads(x) for x in (D / "obs_d.jsonl").read_text().splitlines() if x.strip()]
log = [json.loads(x) for x in (D / "log_d.jsonl").read_text().splitlines() if x.strip()]
sc = [o["score"] for o in obs]
out = {"n": len(sc), "best": max(sc) if sc else None,
       "mean": round(sum(sc) / len(sc), 4) if sc else None,
       "scores": [round(s, 4) for s in sc],
       "verdicts": [r["verdict"] for r in log],
       "criterion": [r.get("criterion") for r in log],
       "bests": [(r.get("reading") or {}).get("best_score") for r in log],
       "gids": [r["gid"] for r in log],
       "with_vec": sum(1 for o in obs if o.get("vec"))}
print(json.dumps(out, ensure_ascii=False, indent=2))
(D / "readings_v236_deploy.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
PY
echo "V236 DONE $(/bin/date +%H:%M:%S)"
