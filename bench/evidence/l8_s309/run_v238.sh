#!/bin/bash
# V238 部署态：**4B 进候选池** —— 产品通道按 provider 选思考字段后，
# 换路池 = `qwen2.5:7b, Qwen3.5-4B@ollama:disabled`（两台都过速度闸：17.88 / 25.05 tok/s）。
#
# 判据（预注册 §4 P5）：池里含 4B 时，换路**至少 1 次产出非空且 `vec` 可判**。
# ⛔ 不是「best 抬到 1.0」：那是 V235/V236 的判据，本轮不重复押。
#
# ⛔ 起点 obs 必须是四位 `0.8333`（与账本同精度，纪律 163）。
# ⛔ `--ledger` 必须空：`state/score_ledger.jsonl` 里 t01 有一行跨口径的 1.0（§306.7-1）。
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

D=bench/evidence/l8_s309
: > "$D/empty_ledger.jsonl"
: > "$D/order_v238.txt"
/bin/cp bench/evidence/l6_s307/obs_start_0833.jsonl "$D/obs_d.jsonl"
/bin/rm -f "$D/log_d.jsonl"
/bin/rm -rf "$D/round_d_"*.log

MODELS="qwen2.5:7b,Qwen3.5-4B@ollama:disabled"

for i in 1 2; do
  STAMP=$(/bin/date +%H%M%S)
  echo "== R${i} swap=4 models=${MODELS} =="
  $PY -u run.py --goals --goals-act 1 --goals-swap 4 \
    --goals-swap-models "$MODELS" \
    --goals-suite-dir bench/evidence/l5_s266/suite_one \
    --goals-obs "$D/obs_d.jsonl" \
    --goals-log "$D/log_d.jsonl" \
    --ledger "$D/empty_ledger.jsonl" \
    --solver llm --temperature 1.0 --budget 1 --no-external \
    > "$D/round_d_${STAMP}.log" 2>&1
  rc=$?
  echo "d	swap=4	models=${MODELS}	rc=$rc	$STAMP" >> "$D/order_v238.txt"
  /usr/bin/grep -E "^  换路|^  判据行|^  状态|^  RAISE|转运维|⚠️ 换路" \
    "$D/round_d_${STAMP}.log" | /usr/bin/tail -8
done

echo "== 汇总 =="
$PY - <<'PY'
import json, pathlib
D = pathlib.Path("bench/evidence/l8_s309")
obs = [json.loads(x) for x in (D / "obs_d.jsonl").read_text().splitlines() if x.strip()]
log = [json.loads(x) for x in (D / "log_d.jsonl").read_text().splitlines() if x.strip()]
swap = [o for o in obs if o.get("source") == "goal_swap"]
out = {
    "n_obs": len(obs),
    "n_swap": len(swap),
    "best": max(o["score"] for o in obs) if obs else None,
    "verdicts": [r["verdict"] for r in log],
    "criterion": [r.get("criterion") for r in log],
    "swap_rows": [{"model": o.get("model"), "score": o.get("score"),
                   "vec": "".join("1" if x else "0" for x in (o.get("vec") or [])),
                   "impl_sha": (o.get("impl_sha") or "")[:8]}
                  for o in swap],
}
print(json.dumps(out, ensure_ascii=False, indent=2))
(D / "readings_v238_deploy.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
PY
echo "V238 DONE $(/bin/date +%H:%M:%S)"
