#!/usr/bin/env bash
# ===========================================================================
# 第十七轮：安慰剂对照 —— 「多了一段文字」还是「那段文字说了什么」
# ===========================================================================
#
# ★ 要拆的假说（§16.15④b 事前登记，不许事后改）
#   第十六轮：同一个记忆状态、同一批题，读抽象层的 C 臂 0.8500 > 空记忆 A 臂 0.8000。
#   但幸存经验只有一句 13 字的话，且**与受益题 k03 的内容几乎无关**。
#   ⇒ 存在第二个同样能解释 +0.05 的假说：**存在性效应** ——
#     上下文里多了一段 `[经验] …`，改变了模型的生成，与内容无关。
#
#   ⛔ T=0 保证**确定性**，不保证**因果性**。
#      同一份输入给同一个输出只能证明可复现，不能证明是被谁改的。
#
# ★ 本实验加的三个安慰剂臂（唯一变量 = 抽象层那段文字的**内容**）
#     C   真实经验   解析查询字符串前先处理空项
#     D1  打乱       空处先字符理项查解析询前串   ← 同字多重集，零语义，必然等长
#     D2  等长异域   编译报错时先读最底层栈信息   ← 同为 13 字经验，不同领域
#     D3  最小对立   解析查询字符串时可忽略空项   ← 只差 3 个字，**语义相反**
#
#   三者都是 13 字 ⇒ 「多写了几个字」被钉死为常量。
#   ⇒ C 与 {D1,D2,D3} 之间的任何差异，只可能来自**内容**。
#
# ★ 修掉第十六轮的一个真实缺陷（这也是本脚本存在的第二个理由）
#   第十六轮里 B、C **共用同一个** --memory-dir。跑完后那个库里有
#   13 组 goal/finish（= 5 训练 + 4 臂B + 4 臂C）和一条**臂 B 跑出来的**
#   worldmodel 事实 ⇒ C 的起跑状态 ≠ B 的起跑状态，四臂不是同一次投掷。
#   ⇒ 本脚本：`mem_base` **只读**，每个臂各拷一份，跑之前打印 sha256 自证同源。
#
# ★ 事前登记的判读规则（跑之前写死）
#   R1  (C−A) > max(Dᵢ−A)          ⇒ 内容起了作用，可归因到"说的是什么"
#   R2  存在 Dᵢ−A ≥ (C−A)          ⇒ ⛔ 不得认领 +0.05，改写为存在性效应
#   R3  无论 R1/R2，**placebo 之间的极差**必须归档：它就是"一次上下文扰动"
#       在这个模型上的抖动量级 —— 这是 T=0 实验此前拿不到的数
#       （T=0 的逐次极差恒为 0，很容易被误读成"这个系统没有噪声"）。
#   R4  逐题看：k01 在 C 臂掉了 −0.20。若安慰剂也掉 ⇒ 那 −0.20 同样只是扰动。
#
# 跑法：bash bench/run_placebo_control.sh
# ===========================================================================
set -u

cd /Users/linshaojie/V1
PY=/Users/linshaojie/.workbuddy/binaries/python/versions/3.13.12/bin/python3

export V1_LLM_BASE_URL=http://localhost:11434/v1
export V1_LLM_API_KEY=ollama
export V1_LLM_MODEL=qwen2.5:7b

TRAIN=bench/tasks/struct_convert.jsonl
EXAM=bench/heldout/struct_convert_heldout.jsonl
ROOT=/tmp/v1_s17
BASE="${ROOT}/mem_base"

rm -rf "${ROOT}"
mkdir -p "${ROOT}"

echo "=============================================================="
echo "第 0 步  训练期：跑 struct_convert（累积记忆到 ${BASE}）"
echo "=============================================================="
V1_BENCH_DIR="${ROOT}/train_bench" "$PY" run.py --bench --solver llm \
  --suite "$TRAIN" --memory-dir "${BASE}" \
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
"$PY" bench/distill.py --memory-dir "${BASE}" --show > "${ROOT}/distill.out" 2>&1
rc=$?
cat "${ROOT}/distill.out"
if [ "$rc" -ne 0 ]; then
  echo "❌ 一条都没提炼出来 ⇒ 抽象层为空 ⇒ 安慰剂比对退化成'空 vs 空'，不跑。"
  exit 1
fi

echo
echo "=============================================================="
echo "第 2 步  ⛔ 基态冻结 —— 从此刻起 ${BASE} 只读"
echo "=============================================================="
BASE_SHA=$(shasum -a 256 "${BASE}/memory.db" | awk '{print $1}')
echo "  base sha256 = ${BASE_SHA}"
"$PY" - "${BASE}" <<'EOF'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1] + "/memory.db")
c.row_factory = sqlite3.Row
print("  facts =", [(r["source"], r["text"]) for r in c.execute(
    "SELECT source, text FROM facts ORDER BY id")])
print("  episodes =", c.execute("SELECT COUNT(*) c FROM episodes").fetchone()["c"])
EOF

echo
echo "=============================================================="
echo "第 3 步  造安慰剂（各自一份全新目录；--src 只读，不碰 base）"
echo "=============================================================="
"$PY" bench/placebo.py --src "${BASE}" --out "${ROOT}/mem_D1" \
      --variant scramble --seed 17 || exit 1
"$PY" bench/placebo.py --src "${BASE}" --out "${ROOT}/mem_D2" \
      --variant text --text '编译报错时先读最底层栈信息' || exit 1
"$PY" bench/placebo.py --src "${BASE}" --out "${ROOT}/mem_D3" \
      --variant text --text '解析查询字符串时可忽略空项' || exit 1

# B / C 也必须有自己的一份 —— 与 D1/D2/D3 **同源**（同一字节的 base）
for arm in B C; do
  cp -R "${BASE}" "${ROOT}/mem_${arm}"
done

echo "  ⛳ 起跑校验：B/C 必须与 base **逐字节相同**；D* 必须不同（文字被换掉了）"
for arm in B C D1 D2 D3; do
  s=$(shasum -a 256 "${ROOT}/mem_${arm}/memory.db" | awk '{print $1}')
  same="不同"
  [ "$s" = "${BASE_SHA}" ] && same="== base"
  echo "    ${arm}: ${s:0:16}… ${same}"
done

run_arm () {
  local label="$1"; shift
  local memflag=""
  if [ -d "${ROOT}/mem_${label}" ]; then
    memflag="--memory-dir ${ROOT}/mem_${label}"
  fi
  echo
  echo "=============================================================="
  echo "第 4 步  考试期 —— 臂 ${label}：$* ${memflag}"
  echo "=============================================================="
  V1_BENCH_DIR="${ROOT}/exam_${label}_bench" "$PY" run.py --bench --solver llm \
    --suite "$EXAM" "$@" ${memflag} --temperature 0 --max-tokens 1024 \
    > "${ROOT}/exam_${label}.out" 2>&1
  local rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "❌ 臂 ${label} 失败 rc=$rc"; tail -20 "${ROOT}/exam_${label}.out"; return 1
  fi
  grep -E "mean=|记忆层" "${ROOT}/exam_${label}.out" | tail -3
  return 0
}

run_arm A --fresh
run_arm B --memory-mode raw
run_arm C --memory-mode abstract
run_arm D1 --memory-mode abstract
run_arm D2 --memory-mode abstract
run_arm D3 --memory-mode abstract

echo
echo "=============================================================="
echo "第 5 步  收尾校验：每个臂各自演化，谁也没污染谁"
echo "=============================================================="
"$PY" - "${BASE}" "${ROOT}" <<'EOF'
import sqlite3, sys, traceback
base, root = sys.argv[1], sys.argv[2]


def counts(d):
    """⛔ 不许出现 "?"：第十七轮第一次跑这里打印了一整屏 `episodes=? facts=?`，
       原因是漏了 `row_factory`，`fetchone()["c"]` 必然抛 TypeError，
       而它被 `except` 吞掉后只剩一个问号 —— 那是 R41 明令禁止的静默兜底：
       失败了必须**带着理由**，否则"看不出来它是失败"本身就是最坏的结果。"""
    try:
        c = sqlite3.connect(f"{d}/memory.db")
        c.row_factory = sqlite3.Row
        return (c.execute("SELECT COUNT(*) c FROM episodes").fetchone()["c"],
                c.execute("SELECT COUNT(*) c FROM facts").fetchone()["c"])
    except Exception:
        traceback.print_exc()
        raise            # 念出来之后仍然不许假装成功


def report(d):
    try:
        return counts(d)
    except Exception:
        return None


br = report(base)
print(f"  base        episodes={br[0]} facts={br[1]}   ← 冻结至今必须保持 {br[0]}/{br[1]}")
for arm in ("B", "C", "D1", "D2", "D3"):
    r = report(f"{root}/mem_{arm}")
    if r is None:
        print(f"  ❌ {arm}: 读不出来（上面已打印原因）")
        continue
    print(f"  {arm:11s} episodes={r[0]} facts={r[1]}"
          f"   {'✅ 起跑与 base 同源且各自演化' if r[0] >= br[0] else '❌ 异常'}")
EOF

echo
echo "=============================================================="
echo "判读（逐题配对差 —— 不是两个平均分在比）"
echo "=============================================================="
"$PY" - "${ROOT}" <<'EOF'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
ARMS = ["A", "B", "C", "D1", "D2", "D3"]


def per_task(arm):
    p = root / f"exam_{arm}_bench" / "runs.jsonl"
    if not p.exists():
        return {}
    rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    return {t["id"]: float(t["score"]) for t in rows[-1]["report"]["per_task"]}


V = {a: per_task(a) for a in ARMS}
missing = [a for a in ARMS if not V[a]]
if missing:
    print(f"缺少 {missing} 的逐题结果，跳过对照")
    raise SystemExit(0)

hdr = f"{'题':22s}" + "".join(f"{a:>7s}" for a in ARMS) + f"{'C-A':>7s}"
print(hdr)
for t in sorted(V["A"]):
    print(f"{t:22s}" + "".join(f"{V[a][t]:7.2f}" for a in ARMS)
          + f"{V['C'][t] - V['A'][t]:+7.2f}")


def m(d):
    return sum(d.values()) / len(d) if d else 0.0


print(f"\n{'mean':22s}" + "".join(f"{m(V[a]):7.4f}" for a in ARMS)
      + f"{m(V['C']) - m(V['A']):+7.4f}")

base = m(V["A"])
print(f"\n相对空记忆 A 的增益（这才是被质疑的那个 +0.05）：")
for a in ARMS[1:]:
    d = m(V[a]) - base
    tag = "  ← 真抽象层" if a == "C" else ""
    print(f"  {a:3s} Δ={d:+7.4f}{tag}")

placebo = [m(V[a]) - base for a in ("D1", "D2", "D3")]
real = m(V["C"]) - base
spread = max(placebo) - min(placebo)
print(f"\n安慰剂极差 = {spread:.4f}（D1/D2/D3 三者之差）"
      f" ｜ 安慰剂最大 Δ = {max(placebo):+.4f} ｜ 真 Δ = {real:+.4f}")

print("\n判读（事前登记，不得事后改）：")
if real > max(placebo):
    print("  R1 成立：真 Δ 严格大于**所有**安慰剂 ⇒ 差异至少部分来自【内容】。")
    print("     ⛔ 但要同念：本轮只有 1 道题在动 ⇒ 结论限于"
          "『这条经验改变了 k03 的行为』，不是『抽象层有用』。")
elif max(placebo) >= real:
    print("  ⛔ R2 命中：有安慰剂 Δ ≥ 真 Δ ⇒ "+f"+{real:.4f} 不得认领。")
    print("     应当改写为：上下文里多一段等长文本本身就能产生同量级变化。")
print(f"  R3 归档：安慰剂极差 {spread:.4f} = 一次『上下文扰动』的抖动量级。")
print("        T=0 的逐次极差恒为 0，很容易被误读成『这个系统没有噪声』")
print("        —— 本数说明噪声不在采样里，在【条件】里。")
print("\n  R4 逐题：k01 在 C 掉了 %.2f；安慰剂是否也掉 ⇒ 决定"
      "『−0.20』是否也只是一次扰动。" % (V["C"]["k01_diff_tree"] - V["A"]["k01_diff_tree"]))
EOF
