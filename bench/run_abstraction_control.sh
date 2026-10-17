#!/usr/bin/env bash
# ===========================================================================
# 第十六轮：抽象 vs 原文 —— 「经验迁移」与「抄答案」的对照
# ===========================================================================
#
# ★ 动机（第十五轮的负面结论）
#   隔离 0.5600 → 累积 0.7600，Δ=+0.2000，本项目第一个正的跨任务增益。
#   但排序对照把它解剖了：把受益的 z03_parse_query 挪到**第一题**，
#   它掉回 0.00，mean 回到隔离基线。⇒ 增益属于**位置**，不属于能力。
#   机制证据：累积记忆里每组 (goal, finish) 的 finish 带着**上一题的完整答案**。
#
# ★ 本实验要分辨什么
#   同一个记忆状态、同一批考试题，**唯一变量** = 允许读到记忆的哪一层：
#
#     A  空记忆        --fresh                       ⇒ 能力基线（什么都没读到）
#     B  原文层        --memory-dir M --memory-mode raw
#     C  抽象层        --memory-dir M --memory-mode abstract
#
#   ⛔ B 与 C 用**同一个** M（同一批episodic经历 + 同一批abstract facts），
#      因为换目录会连记忆状态一起换掉 ⇒ 同时动两个变量 ⇒ 归因不了任何东西。
#
# ★ 事前登记的判读规则（写在**跑之前**，不许事后改）
#   ① C > A 且接近 B  ⇒ 提炼出的经验**真的**迁移到了不同表面的新题（真泛化）
#   ② C ≈ A 而 B > A ⇒ B 的收益全部来自答案复用（大概率是照抄）
#   ③ C > B         ⇒ 读原文反而有害（噪音盖过信号）
#   ④ 三臂全等       ⇒ 这份数据没有任何可归因的迁移
#
# ★ 为什么考试题必须是跨期的
#   struct_convert（训练）与 struct_convert_heldout（考试）：
#   **同结构**（递归遍历/序列化/反解析），但**函数名、数据格式、领域词汇全不同**
#   —— 否则 few-shot 可以照抄，答的就是"能不能抄"而不是"会不会迁移"。
#
# 跑法：bash bench/run_abstraction_control.sh
# ===========================================================================
set -u

cd /Users/linshaojie/V1
PY=/Users/linshaojie/.workbuddy/binaries/python/versions/3.13.12/bin/python3

export V1_LLM_BASE_URL=http://localhost:11434/v1
export V1_LLM_API_KEY=ollama
export V1_LLM_MODEL=qwen2.5:7b

TRAIN=bench/tasks/struct_convert.jsonl
EXAM=bench/heldout/struct_convert_heldout.jsonl
ROOT=/tmp/v1_s16
MEM="${ROOT}/mem"

rm -rf "${ROOT}"
mkdir -p "${MEM}"

echo "=============================================================="
echo "第 0 步  训练期：跑 struct_convert（累积记忆到 ${MEM}）"
echo "=============================================================="
V1_BENCH_DIR="${ROOT}/train_bench" "$PY" run.py --bench --solver llm \
  --suite "$TRAIN" --memory-dir "${MEM}" \
  --temperature 0 --max-tokens 1024 > "${ROOT}/train.out" 2>&1
rc=$?
if [ "$rc" -ne 0 ]; then
  echo "❌ 训练失败 rc=$rc"; tail -20 "${ROOT}/train.out"; exit 1
fi
grep -E "mean=" "${ROOT}/train.out" | tail -2

echo
echo "=============================================================="
echo "第 1 步  蒸馏：把 episodic 原文变成自然语言经验"
echo "=============================================================="
"$PY" bench/distill.py --memory-dir "${MEM}" --show > "${ROOT}/distill.out" 2>&1
rc=$?
cat "${ROOT}/distill.out"
if [ "$rc" -ne 0 ]; then
  echo "❌ 蒸馏一条都没提炼出来 ⇒ C 臂会退化成'和空记忆比'，实验不成立"
  exit 1
fi

run_arm () {
  local label="$1"; shift
  echo
  echo "=============================================================="
  echo "第 2 步  考试期 —— 臂 ${label}：$*"
  echo "=============================================================="
  V1_BENCH_DIR="${ROOT}/exam_${label}_bench" "$PY" run.py --bench --solver llm \
    --suite "$EXAM" "$@" --temperature 0 --max-tokens 1024 \
    > "${ROOT}/exam_${label}.out" 2>&1
  local rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "❌ 臂 ${label} 失败 rc=$rc"; tail -20 "${ROOT}/exam_${label}.out"; return 1
  fi
  grep -E "mean=|可信|记忆层" "${ROOT}/exam_${label}.out" | tail -4
  return 0
}

run_arm A --fresh
run_arm B --memory-dir "${MEM}" --memory-mode raw
run_arm C --memory-dir "${MEM}" --memory-mode abstract

echo
echo "=============================================================="
echo "逐题对照（per-task 配对差 —— 不是两个平均分在比）"
echo "=============================================================="
"$PY" - "${ROOT}" <<'EOF'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])


def per_task(arm: str) -> dict[str, float]:
    p = root / f"exam_{arm}_bench" / "runs.jsonl"
    if not p.exists():
        return {}
    rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    return {t["id"]: float(t["score"]) for t in rows[-1]["report"]["per_task"]}


A, B, C = per_task("A"), per_task("B"), per_task("C")
if not (A and B and C):
    print("缺少某一臂的逐题结果，跳过对照")
    raise SystemExit(0)

print(f"{'题':22s} {'A 空':>7s} {'B 原文':>7s} {'C 抽象':>7s} {'B-A':>7s} {'C-A':>7s}")
for t in sorted(A):
    print(f"{t:22s} {A[t]:7.2f} {B[t]:7.2f} {C[t]:7.2f} "
          f"{B[t] - A[t]:+7.2f} {C[t] - A[t]:+7.2f}")


def mean(d):
    return sum(d.values()) / len(d) if d else 0.0


print(f"\n{'mean':22s} {mean(A):7.4f} {mean(B):7.4f} {mean(C):7.4f} "
      f"{mean(B) - mean(A):+7.4f} {mean(C) - mean(A):+7.4f}")

up_b = sum(1 for t in A if B[t] > A[t])
dn_b = sum(1 for t in A if B[t] < A[t])
up_c = sum(1 for t in A if C[t] > A[t])
dn_c = sum(1 for t in A if C[t] < A[t])
print(f"逐题配对：B 变好={up_b} 变差={dn_b} ｜ C 变好={up_c} 变差={dn_c}")
print("\n判读（事前登记，不得事后改）：")
print("  ① C>A 且接近 B ⇒ 经验真的迁移了（真泛化）")
print("  ② C≈A 而 B>A  ⇒ B 的收益全部来自答案复用（照抄）")
print("  ③ C>B         ⇒ 读原文反而有害（噪音盖过信号）")
print("  ④ 三臂全等     ⇒ 这份数据没有可归因的迁移")
EOF
