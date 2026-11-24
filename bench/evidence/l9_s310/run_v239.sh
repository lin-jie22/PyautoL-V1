#!/bin/bash
# V239 部署态：**起点换成账本里的真 best（及其实现）**，其余与 V238 保持一致。
#
# 单变量声明（PREREG_v239 §2）：
#   - 池**不变**：`qwen2.5:7b, Qwen3.5-4B@ollama:disabled`（与 V238 同一串）
#   - 套件/预算/温度/solver 全部不变
#   - 唯一变的：起点从「本轮 act 产出」→「账本 best 的实现」
#
# ⛔ 起点 obs 必须带 `impl_sha`（否则会走降级分支，测的就不是 B3）——
#    这正是 V238 起点 obs 的形态缺陷，本轮已补。
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

D=bench/evidence/l9_s310
: > "$D/empty_ledger.jsonl"
: > "$D/order_v239.txt"
/bin/cp "$D/obs_start_0833_impl.jsonl" "$D/obs_d.jsonl"
/bin/rm -f "$D/log_d.jsonl"
/bin/rm -f "$D"/round_d_*.log

MODELS="qwen2.5:7b,Qwen3.5-4B@ollama:disabled"

for i in 1 2; do
  STAMP=$(/bin/date +%H%M%S)
  echo "== R${i} swap=4 models=${MODELS} start=ledger_best =="
  $PY -u run.py --goals --goals-act 1 --goals-swap 4 \
    --goals-swap-models "$MODELS" \
    --goals-swap-cache "$PWD/$D/cand" \
    --goals-suite-dir bench/evidence/l5_s266/suite_one \
    --goals-obs "$D/obs_d.jsonl" \
    --goals-log "$D/log_d.jsonl" \
    --ledger "$D/empty_ledger.jsonl" \
    --solver llm --temperature 1.0 --budget 1 --no-external \
    > "$D/round_d_${STAMP}.log" 2>&1
  rc=$?
  echo "d	swap=4	models=${MODELS}	start=ledger_best	rc=$rc	$STAMP" >> "$D/order_v239.txt"
  /usr/bin/grep -E "^  换路|^  起点|^  ⚠️ 起点|^  判据行|^  状态|^  RAISE|转运维|⚠️ 换路" \
    "$D/round_d_${STAMP}.log" | /usr/bin/tail -10
done

echo "== 汇总 =="
$PY - <<'PY'
import json, pathlib
D = pathlib.Path("bench/evidence/l9_s310")
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
                   "impl_sha": (o.get("impl_sha") or "")[:16]}
                  for o in swap],
    "act_rows": [{"score": o.get("score"),
                  "impl_sha": (o.get("impl_sha") or "")[:16],
                  "vec": "".join("1" if x else "0" for x in (o.get("vec") or []))}
                 for o in obs if o.get("source") == "goal_obs"],
    "cand_dir": sorted(p.name for p in (D / "cand").glob("*.py")),
}
print(json.dumps(out, ensure_ascii=False, indent=2))
(D / "readings_v239_deploy.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
PY
echo "V239 DONE $(/bin/date +%H:%M:%S)"
