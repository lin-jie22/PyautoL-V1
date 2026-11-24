#!/bin/bash
# V241：换路预算的截断曲线。三臂 = 900 / 1600 / 2400，**同一个时间窗**（纪律 106）。
#
# ⛔ 与 V239b 的差别**只有**一个：`--goals-swap-max-tokens`。
#    同一串模型、同一套件、同一温度、同一 swap 次数、同一起点种子。
#
# 归因口径（写死，防事后挑）：
#   `换路 #N 失败` 里的 N 是 `for i in range(k)` 的**下标**（0 基），
#   而模型是 `models[i % len(models)]` ⇒ **偶数位=7b、奇数位=4B**。
#   截断行不带模型名，所以只能这样反推；反过来也检验：若偶数位出现截断 ⇒ 归因错。
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
MODELS="qwen2.5:7b,Qwen3.5-4B@ollama:disabled"
SUITE=bench/evidence/l5_s266/suite_one

for BUDGET in 900 1600 2400; do
  TAG="b${BUDGET}"
  : > "$D/empty_ledger_${TAG}.jsonl"
  : > "$D/order_v241_${TAG}.txt"
  /bin/cp "$D/obs_start_0833_impl.jsonl" "$D/obs_${TAG}.jsonl"
  /bin/rm -f "$D/log_${TAG}.jsonl"
  /bin/rm -f "$D"/round_${TAG}_*.log

  for i in 1 2; do
    STAMP=$(/bin/date +%H%M%S)
    echo "== ${TAG} R${i} swap=4 start_from=ledger budget=${BUDGET} =="
    $PY -u run.py --goals --goals-act 1 --goals-swap 4 \
      --goals-swap-models "$MODELS" \
      --goals-swap-start-from ledger \
      --goals-swap-max-tokens "$BUDGET" \
      --goals-swap-cache "$PWD/$D/cand" \
      --goals-suite-dir "$SUITE" \
      --goals-obs "$D/obs_${TAG}.jsonl" \
      --goals-log "$D/log_${TAG}.jsonl" \
      --ledger "$D/empty_ledger_${TAG}.jsonl" \
      --solver llm --temperature 1.0 --budget 1 --no-external \
      > "$D/round_${TAG}_${STAMP}.log" 2>&1
    rc=$?
    echo "	${TAG}	R${i}	rc=$rc	$STAMP" >> "$D/order_v241_${TAG}.txt"
    /usr/bin/grep -E "^  ▶ 起点|^  ⚠️ 起点|^  换路|^  判据行|^  RAISE|^  ⚠️ 换路|转运维" \
      "$D/round_${TAG}_${STAMP}.log" | /usr/bin/tail -12
  done
done

echo "== 汇总 =="
$PY - <<'PY'
import json, pathlib
D = pathlib.Path("bench/evidence/l9_s310")
out = {}
for tag in ("b900", "b1600", "b2400"):
    obs = [json.loads(x) for x in (D / f"obs_{tag}.jsonl").read_text().splitlines() if x.strip()]
    swap = [o for o in obs if o.get("source") == "goal_swap"]
    logs = sorted(D.glob(f"round_{tag}_*.log"))
    trunc = []
    for lf in logs:
        for ln in lf.read_text(errors="replace").splitlines():
            if "响应被截断" in ln or "被**截断**" in ln:
                trunc.append({"file": lf.name, "line": ln.strip()[:160]})
    out[tag] = {
        "n_obs": len(obs), "n_swap": len(swap), "n_trunc_lines": len(trunc),
        "trunc": trunc,
        "swap_rows": [{"model": o.get("model"), "score": o.get("score"),
                       "vec": "".join("1" if x else "0" for x in (o.get("vec") or [])),
                       "sha": (o.get("impl_sha") or "")[:16],
                       "chars": o.get("chars")} for o in swap],
        "act_rows": [{"score": o.get("score"),
                      "vec": "".join("1" if x else "0" for x in (o.get("vec") or [])),
                      "sha": (o.get("impl_sha") or "")[:16]} for o in obs
                     if o.get("source") in ("goals-act", "goal_obs")],
    }
print(json.dumps(out, ensure_ascii=False, indent=2))
(D / "readings_v241.json").write_text(json.dumps(out, ensure_ascii=False, indent=2),
                                     encoding="utf-8")
PY
echo "V241 DONE $(/bin/date +%H:%M:%S)"
