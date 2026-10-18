#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# 路线①·外部能力对照（2026-10-18，第二十三轮）
#
# 事前登记写在 DESIGN §16.22 ③，**跑之前**就写死了（R1–R5）。
# 本脚本只负责执行，不含任何判读 —— 判读在 bench/probe_external_real.py 里，
# 且它的规则必须与 §16.22 逐字一致（否则就是两份判据，第十八轮踩过）。
#
# 两臂的**唯一**差别：外部能力在不在。
#   OFF : `--no-external`（`agents.json` 视同不存在）+ 开关为空
#   ON  : 注册 `agent:code_agent` + `V1_EXTERNAL_ANSWER_TOOL=agent:code_agent`
# 其它一律同口径：同题集 / 同尺子 / T=0 / --fresh（每题新建内核 + 记忆隔离）。
# ⛔ 不共享记忆目录：第十六轮就是在这里翻的车（B/C 共用一个目录 ⇒ 假读数）。
# ⛔ 本脚本跑起来之后**不许改**（边跑边改 = bash 按字节偏移重读 = 错位重跑）。
# ---------------------------------------------------------------------------
set -u
cd "$(dirname "$0")/.."

PY=/Users/linshaojie/.workbuddy/binaries/python/versions/3.13.12/bin/python3
ROOT=/tmp/v1_s23_ext

export V1_LLM_BASE_URL=http://localhost:11434/v1
export V1_LLM_API_KEY=ollama
export V1_LLM_MODEL=qwen2.5:7b

# $1 = 标签(off/on)  $2 = 题集参数（--suite X 或 --suite-dir Y）  $3 = 目录名
one_arm() {
  local tag="$1"; shift
  local args="$1"; shift
  local dir="$1"; shift
  rm -rf "$dir"; mkdir -p "$dir"
  if [ "$tag" = "off" ]; then
    V1_EXTERNAL_ANSWER_TOOL="" \
    V1_BENCH_DIR="$dir/bench" V1_STATE_DIR="$dir/state" \
      "$PY" run.py --bench --solver llm --temperature 0 --max-tokens 1024 \
        --fresh --no-external $args > "$dir/out.log" 2>&1
  else
    V1_EXTERNAL_ANSWER_TOOL="agent:code_agent" \
    V1_BENCH_DIR="$dir/bench" V1_STATE_DIR="$dir/state" \
      "$PY" run.py --bench --solver llm --temperature 0 --max-tokens 1024 \
        --fresh $args > "$dir/out.log" 2>&1
  fi
  echo "  [$tag] rc=$?"
  grep -E "mean=" "$dir/out.log" | tail -1 | sed 's/^/    /'
  grep -oE "外部\[[^]]*\][^|]*" "$dir/out.log" | tail -1 | sed 's/^/    /'
}

# 允许只重跑一部分：`V1_CASES="ho" V1_ARMS="on" ./bench/run_external_control.sh`
# ⛔ 这不是"挑好看的那一格"：只重跑某一臂时，另一臂**必须已有同版本代码的读数**，
#    否则两臂来自不同版本的 agent，Δ 就不是同一个实验。
WANT_CASES="${V1_CASES:-sc ho}"
WANT_ARMS="${V1_ARMS:-off on}"

for CASE in "sc|--suite bench/tasks/struct_convert.jsonl" \
            "ho|--suite-dir bench/heldout"; do
  NAME="${CASE%%|*}"
  ARGS="${CASE#*|}"
  case " $WANT_CASES " in *" $NAME "*) ;; *) continue ;; esac
  echo
  echo "=================== 题集 ${NAME} ==================="
  case " $WANT_ARMS " in *" off "*)
    # shellcheck disable=SC2086
    one_arm off "$ARGS" "$ROOT/${NAME}_off" ;; esac
  case " $WANT_ARMS " in *" on "*)
    # shellcheck disable=SC2086
    one_arm on  "$ARGS" "$ROOT/${NAME}_on" ;; esac

  echo
  echo "  ── 逐题配对（${NAME}）──"
  "$PY" - "$ROOT" "$NAME" <<'PY'
import json, os, sys
root, name = sys.argv[1], sys.argv[2]
res = {}
for label, sub in (("OFF", f"{name}_off"), ("ON", f"{name}_on")):
    p = os.path.join(root, sub, "bench", "runs.jsonl")
    if not os.path.exists(p):
        print(f"    {label}: 无结果"); continue
    # ⚠️ 必须摊平**全部**记录：`--suite-dir` 每个领域写一条，
    #    只读最后一条会静默少读 3 道题（第二十一轮踩过一次，本轮又踩了一次）。
    rows = [json.loads(l) for l in open(p) if l.strip()]
    res[label] = {}
    for rec in rows:
        for t in rec["report"]["per_task"]:
            res[label][t["id"]] = t["score"]
    m = sum(res[label].values()) / len(res[label])
    print(f"    {label:3s} mean={m:.4f}  n={len(res[label])}")
if len(res) == 2:
    ids = sorted(set(res["OFF"]) | set(res["ON"]))
    moved = [i for i in ids if res["OFF"].get(i) != res["ON"].get(i)]
    d = sum(res["ON"].get(i, 0) - res["OFF"].get(i, 0) for i in ids) / len(ids)
    print(f"    Δ={d:+.4f}  位移题={moved or '无'}")
    for i in moved:
        print(f"      {i}: {res['OFF'].get(i)} → {res['ON'].get(i)}")
PY
done

echo
echo "=================== 外部能力读数（落盘，非日志）==================="
"$PY" - "$ROOT" <<'PY'
import json, os, sys
root = sys.argv[1]
for sub in ("sc_off", "sc_on", "ho_off", "ho_on"):
    p = os.path.join(root, sub, "bench", "runs.jsonl")
    if not os.path.exists(p):
        continue
    rows = [json.loads(l) for l in open(p) if l.strip()]
    ex = (rows[-1].get("kernel_external") or {})
    print(f"  {sub:7s} {json.dumps(ex, ensure_ascii=False)}")
PY
