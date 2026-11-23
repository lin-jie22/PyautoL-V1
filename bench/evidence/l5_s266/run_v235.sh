#!/bin/bash
# V235：L5 闭环的两臂部署态对照。
#
# B 臂：--goals-swap 4（判 NOT_MET 的 RAISE 再换 4 条路）
# A 臂：--goals-swap 0（旧行为：只重跑同一实现 ⇒ best 不会变）
#
# 起点相同（3 次 4/6，V234 R1 账本实读）、套件相同（只有 t01）、账本清空
# （⛔ 不能混 `state/score_ledger.jsonl`：那里面 t01 有一行跨口径的 1.0，
#   见 mw/goals.py:_verifiable 的注释）。
#
# 判据见 PREREG_v235.md §3（P1~P8 已在 probe_v235.py 离线判过）；
# 这个脚本只量**部署态接线**：目标链能不能走完一圈、终止条件什么时候到。
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

D=bench/evidence/l5_s266
: > "$D/empty_ledger.jsonl"
[ -f "$D/obs_start.jsonl" ] || { echo "NO START OBS"; exit 2; }
: > "$D/order_v235.txt"

one_round () {          # $1=tag $2=swap
  local TAG=$1 SWAP=$2
  local t0=$(/bin/date +%H:%M:%S)
  $PY -u run.py --goals --goals-act 1 --goals-swap "$SWAP" \
    --goals-suite-dir "$D/suite_one" \
    --goals-obs "$D/obs_${TAG}.jsonl" \
    --goals-log "$D/log_${TAG}.jsonl" \
    --ledger "$D/empty_ledger.jsonl" \
    --solver llm --temperature 1.0 --budget 1 --no-external \
    > "$D/round_${TAG}_$(/bin/date +%H%M%S).log" 2>&1
  local rc=$?
  echo "${TAG}	swap=${SWAP}	rc=$rc	${t0}	$(/bin/date +%H:%M:%S)" >> "$D/order_v235.txt"
  /usr/bin/grep -E "^  换路|^  RAISE|^  COVER|^  PROBE|^  判据行|^  状态" \
    "$D/round_${TAG}_"*.log 2>/dev/null | /usr/bin/tail -4
}

echo "== A 臂（swap=0，旧行为）==============================="
/bin/cp "$D/obs_start.jsonl" "$D/obs_a.jsonl"
/bin/rm -f "$D/log_a.jsonl"
for i in 1 2 3 4; do one_round a 0; done

echo "== B 臂（swap=4）====================================="
/bin/cp "$D/obs_start.jsonl" "$D/obs_b.jsonl"
/bin/rm -f "$D/log_b.jsonl"
for i in 1 2 3 4; do one_round b 4; done

echo "== 汇总 =="
$PY - <<'PY'
import json, pathlib
D = pathlib.Path("bench/evidence/l5_s266")
suite = json.loads((D / "suite_one/t01.jsonl").read_text())
tests = suite["tests"]
out = {}
for tag in ("a", "b"):
    obs = [json.loads(x) for x in (D / f"obs_{tag}.jsonl").read_text().splitlines() if x.strip()]
    log = [json.loads(x) for x in (D / f"log_{tag}.jsonl").read_text().splitlines() if x.strip()] \
        if (D / f"log_{tag}.jsonl").exists() else []
    sc = [o["score"] for o in obs]
    out[tag] = {"n": len(sc), "best": max(sc) if sc else None,
                "mean": (sum(sc) / len(sc)) if sc else None,
                "scores": [round(s, 4) for s in sc],
                "verdicts": [r["verdict"] for r in log],
                "gids": len({r["gid"] for r in log})}
print(json.dumps(out, ensure_ascii=False, indent=2))
(D / "readings_v235_deploy.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
PY
echo "V235 DONE $(date +%H:%M:%S)"
