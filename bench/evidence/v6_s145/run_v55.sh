#!/usr/bin/env bash
# V55 真实两臂 —— 「按题的下发次数 × 验收结论」进不进得了账本
#
# 常量：模型 / 温度 / 预算 / 提示词（`catalog_sha` 两态必须相同）
# 变量：**只**多了 `kernel_exec` 这一个落盘键（`run.py` 的累加器 + 账本行）
#
# ⛔ 两臂各自新建 state 目录：共享状态库 = 臂 2 继承臂 1 的经验。
# ⛔ 串行跑：并发会抢同一个 ollama，耗时读数不可比。
# ⛔ 不许接管道（SIGPIPE ⇒ EXIT 137），只许重定向到文件。
#
# 格数（PREREG §3 P3）：
#   B 臂 6 题 × 2 rep = **12 格**（判据 P3 的分母）
#   A 臂 6 题 × 1 rep = 6 格（只为证明"改前账本里没有这个键"，不是分数对照）
set -u
cd /Users/linshaojie/V1 || exit 1
ROOT=/tmp/v55
A="$ROOT/wtA"
[ -d "$A/mw" ] || { echo "⛔ 先解包 $A"; exit 1; }

export V1_LLM_MODEL="qwen2.5:7b"
export V1_LLM_API_KEY="ollama-local"
export V1_LLM_BASE_URL="http://127.0.0.1:11434/v1"
export V1_LLM_TEMPERATURE=0
export V1_LLM_MAX_TOKENS=1024
export V1_LLM_TIMEOUT_S=120
export V1_LLM_RETRIES=1
export V1_MAX_WALL_S=300
export V1_MAX_LLM_CALLS=24
export V1_MAX_STEPS=12
export V1_STATE_CHARS=2400
export V1_MAX_CANDIDATES=3
export V1_CAND_FAILOVER=1
export no_proxy=127.0.0.1 NO_PROXY=127.0.0.1

run_bench () {        # $1=tag  $2=run.py 所在目录  $3=重复次数
  local tag="$1" kroot="$2" rep="$3"
  rm -rf "$ROOT/st_$tag" "$ROOT/ws_$tag" "$ROOT/bench_$tag"
  mkdir -p "$ROOT/st_$tag" "$ROOT/ws_$tag" "$ROOT/bench_$tag"
  echo "═══ 臂 ${tag}（内核=${kroot}，repeat=${rep}）═══"
  local t0 t1 rc
  t0=$(date +%s)
  # ⛔ `V1_BENCH_DIR` 必须指到 /tmp：否则两臂都会往**仓库里**的
  #    `bench_out/runs.jsonl` 追加一行 ⇒ 把已跟踪的文件弄脏。
  V1_WORKSPACE="$ROOT/ws_$tag" V1_STATE_DIR="$ROOT/st_$tag" \
    V1_BENCH_DIR="$ROOT/bench_$tag" \
    python3 -u "$kroot/run.py" --bench --suite bench/tasks/coding_small.jsonl \
      --repeat "$rep" --no-external --no-ledger --no-answers \
      > "$ROOT/out_$tag.txt" 2>&1
  rc=$?
  t1=$(date +%s)
  echo "  退出码=$rc  耗时=$((t1-t0))s"
  python3 - "$tag" "$ROOT/bench_$tag" <<'PY'
import json, sys, pathlib
tag, benchdir = sys.argv[1], sys.argv[2]
raw = pathlib.Path(f"/tmp/v55/out_{tag}.txt").read_text(encoding="utf-8", errors="replace")
for line in raw.splitlines():
    t = line.strip()
    if t.startswith("■ ") or "mean=" in t or "本次口径" in t:
        print("  ", t[:230])
p = pathlib.Path(benchdir) / "runs.jsonl"
if not p.exists():
    print("  ⛔ 没有 runs.jsonl"); raise SystemExit(0)
rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
r = rows[-1]
print(f"  账本行数={len(rows)}  keys含kernel_exec={'kernel_exec' in r}")
print(f"  measurement mean={r['measurement']['mean']:.4f} n={r['measurement']['n']}")
print(f"  caliber_sig={r.get('caliber_sig')}  catalog={(r.get('caliber') or {}).get('tool_catalog_sha','')[:16]}")
print(f"  kernel_exec={json.dumps(r.get('kernel_exec'), ensure_ascii=False)}")
kc = r.get("kernel_closeout") or {}
print(f"  kernel_closeout.triggered={kc.get('triggered')} zero_exec={kc.get('zero_exec')}")
PY
  echo
}

run_bench a "$A" 1
run_bench b /Users/linshaojie/V1 2

echo "── 两臂对比 ──"
python3 - <<'PY'
import json, pathlib
def load(benchdir):
    p = pathlib.Path(benchdir) / "runs.jsonl"
    if not p.exists():
        return None
    rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    return rows[-1]
a = load("/tmp/v55/bench_a"); b = load("/tmp/v55/bench_b")
if not a or not b:
    print("  ⛔ 有一臂没有账本行")
else:
    print(f"  A 有 kernel_exec? {'kernel_exec' in a}   B 有? {'kernel_exec' in b}")
    print(f"  A mean={a['measurement']['mean']:.4f}  B mean={b['measurement']['mean']:.4f}")
    print(f"  A sig={a.get('caliber_sig')}")
    print(f"  B sig={b.get('caliber_sig')}")
    print(f"  同口径: {a.get('caliber_sig') == b.get('caliber_sig')}")
    ke = b.get("kernel_exec") or {}
    print(f"  B kernel_exec: tasks={ke.get('tasks')} dispatched={ke.get('dispatched')}"
          f" ok={ke.get('ok')} fail={ke.get('fail')}"
          f" zero_dispatch_tasks={ke.get('zero_dispatch_tasks')}")
    print(f"  B by_verdict={json.dumps(ke.get('by_verdict') or {}, ensure_ascii=False)}")
    print(f"  B zero_dispatch_by_verdict={json.dumps(ke.get('zero_dispatch_by_verdict') or {}, ensure_ascii=False)}")
PY
