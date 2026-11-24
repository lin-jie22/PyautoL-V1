#!/bin/bash
# V239b 部署态：`--goals-swap-start-from ledger` ⇒ 起点**强制**用账本 best 的实现。
#
# 为什么重跑一轮（PREREG_v239 §8 已按此修订登记）：
#   R1 (auto) 的 act 产出自己就拿了 0.8333（`09d0e23d`，vec=`011111`），
#   而账本 best 也是 0.8333（`90579ece`，vec=`111011`）⇒ **同分不同体**。
#   `auto` 只在「严格更高」时升级 ⇒ 那一轮根本没进升级分支，P1/P5 未被执行。
#   ⇒ 这一轮只改**起点策略**：why 的数字与 R1 **逐字节相同**（都是 0.8333），
#     唯一变的是给模型的那份**代码**。这是本轮最干净的单变量。
#
# ⛔ 其余全部与 R1 相同（同一串模型、同一套件、同一预算、同一温度）。
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
: > "$D/empty_ledger2.jsonl"
: > "$D/order_v239b.txt"
/bin/cp "$D/obs_start_0833_impl.jsonl" "$D/obs_d2.jsonl"
/bin/rm -f "$D/log_d2.jsonl"
/bin/rm -f "$D"/round_d2_*.log

MODELS="qwen2.5:7b,Qwen3.5-4B@ollama:disabled"

for i in 1 2; do
  STAMP=$(/bin/date +%H%M%S)
  echo "== R${i} swap=4 start_from=ledger =="
  $PY -u run.py --goals --goals-act 1 --goals-swap 4 \
    --goals-swap-models "$MODELS" \
    --goals-swap-start-from ledger \
    --goals-swap-cache "$PWD/$D/cand" \
    --goals-suite-dir bench/evidence/l5_s266/suite_one \
    --goals-obs "$D/obs_d2.jsonl" \
    --goals-log "$D/log_d2.jsonl" \
    --ledger "$D/empty_ledger2.jsonl" \
    --solver llm --temperature 1.0 --budget 1 --no-external \
    > "$D/round_d2_${STAMP}.log" 2>&1
  rc=$?
  echo "d	swap=4	start=ledger	rc=$rc	$STAMP" >> "$D/order_v239b.txt"
  /usr/bin/grep -E "^  ▶ 起点|^  ⚠️ 起点|^  换路|^  判据行|^  RAISE|转运维|⚠️ 换路" \
    "$D/round_d2_${STAMP}.log" | /usr/bin/tail -10
done

echo "== 汇总 =="
$PY - <<'PY'
import json, pathlib
D = pathlib.Path("bench/evidence/l9_s310")
obs = [json.loads(x) for x in (D / "obs_d2.jsonl").read_text().splitlines() if x.strip()]
log = [json.loads(x) for x in (D / "log_d2.jsonl").read_text().splitlines() if x.strip()]
swap = [o for o in obs if o.get("source") == "goal_swap"]
out = {
    "n_obs": len(obs), "n_swap": len(swap),
    "best": max(o["score"] for o in obs) if obs else None,
    "verdicts": [r["verdict"] for r in log],
    "swap_rows": [{"model": o.get("model"), "score": o.get("score"),
                   "vec": "".join("1" if x else "0" for x in (o.get("vec") or [])),
                   "impl_sha": (o.get("impl_sha") or "")[:16]} for o in swap],
    "act_rows": [{"score": o.get("score"), "impl_sha": (o.get("impl_sha") or "")[:16],
                  "vec": "".join("1" if x else "0" for x in (o.get("vec") or []))}
                 for o in obs if o.get("source") in ("goals-act", "goal_obs")],
    "cand_dir": sorted(p.name for p in (D / "cand").glob("*.py")),
}
print(json.dumps(out, ensure_ascii=False, indent=2))
(D / "readings_v239b_deploy.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
PY
echo "V239b DONE $(/bin/date +%H:%M:%S)"
