#!/usr/bin/env python3
"""第十八件量具：`tasks_v4`（产物型尺子）的**性质**，不是它的分数。

⛔ 为什么"造一把新尺子"必须配一个量具：
   v1~v3 的 26 题答案都是纯函数、断言都是 `f(x) == y`。v4 换了形态
   （断言看**文件系统副作用**）。换形态的风险不是"题目难一点"，
   而是**新尺子可能根本没在测新东西** —— 它可能：
     · 某条 test 恒真（期望值写错成恒成立）
     · 依赖执行顺序（前一条 test 造好的文件被后一条用上）
     · 冻结文件与生成器不一致（改了生成器忘了重新冻结）
   这三个都是「看起来是 128 格、其实没量到 128 格」的形态。
   本文件逐条把它们变成可失败的断言。

七条（A1~A7），其中 A2/A4/A6 是**反证**性质，不是描述性质：

  A1 冻结题集结构（8 题 / 128 格 / id 唯一 / 每题 16 条 / 字段齐）
  A2 ⭐ **打乱 test 顺序，分数必须不变** —— 证明每条 test 自带 setup
  A3 真打分器跑 8 题 oracle ⇒ 全部 1.0（题目可解）
  A4 ⭐ 真打分器跑 `pure`（返回值正确但**不落盘**）⇒ 均分必须 < 0.10
        这是"形态确实变了"的运行时证明：同一个人在 v1~v3 上拿满分
  A5 冻结文件 == 生成器重生成（逐字节）—— 防"改了生成器忘了重冻结"
  A6 ⭐ 把一条 test 的期望值改一个字 ⇒ 分数必须掉（断言不是恒真）
  A7 空答案 ⇒ 0 分（防"没答案也给分"）

零模型、零网络。A2 只抽 3 题（at03 幂等 / at05 多产物 / at07 目录遍历）——
它们是最容易依赖顺序的三题；全量 8 题会把门禁拉到 40s 以上而没有额外信息。

跑法
    python3 bench/probe_artifact_suite.py --self-check
    python3 bench/probe_artifact_suite.py          # 出标定表
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SUITE = ROOT / "bench" / "specgen" / "tasks_v4.jsonl"
GEN = ROOT / "bench" / "specgen" / "ext_v4.py"
SCORER = ROOT / "bench" / "scorers" / "code_exec.py"
PY = sys.executable

#: A2 抽样的三题（见文件头）
ORDER_SENSITIVE = ("at03", "at05", "at07")


def load() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for line in SUITE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        d = json.loads(line)
        out[d["id"]] = d
    return out


def score(task: dict, answer: str, *, tests: list[str] | None = None) -> float:
    """走**真打分器**（子进程、`python -I`、每条 test 独立临时目录）。

    ⛔ 不在这里复写判分语义：`code_exec.py` 才是尺子。
       量具如果自己跑一遍 exec，量到的就是量具，不是尺子。
    """
    payload = json.dumps({"task": {"id": task["id"],
                                   "tests": tests if tests is not None
                                   else task["tests"]},
                          "answer": answer}, ensure_ascii=False)
    p = subprocess.run([PY, str(SCORER)], input=payload, capture_output=True,
                       text=True, cwd=str(tempfile.gettempdir()))
    if p.returncode != 0:
        raise RuntimeError(f"打分器失败 rc={p.returncode}: "
                           f"{(p.stderr or '')[:160]}")
    return float(json.loads(p.stdout.strip().splitlines()[-1])["score"])


def gen_rows() -> list[dict]:
    """调**生成器本身**重算一遍题集（A5 与各条反证都要拿 pure/oracle）。"""
    import runpy
    m = runpy.run_path(str(GEN), run_name="v4gen")
    return m["rows_of"]()


def gen_specs() -> dict[str, object]:
    """`pure` 变体**不在冻结题集里**（它不进尺子，只用来做 A4 反证）。

    ⛔ 所以必须从生成器模块拿 `SPECS`，而不是从 `rows_of()` 的 dict 里找 ——
       本轮第一版就是从 dict 里找，结果 `pure` 恒为 None、A4 直接失效
       （而"失效"长得和"通过"一样：它只是提前 break 了，没打印分数）。
    """
    import runpy
    m = runpy.run_path(str(GEN), run_name="v4spec")
    return {sp.id: sp for sp in m["SPECS"]}


# ===========================================================================
# 自检
# ===========================================================================
def self_check() -> int:
    print("=" * 78)
    print("  第十八件量具：tasks_v4 产物型尺子的性质（A1~A7）")
    print("=" * 78)
    bad = 0
    suite = load()
    rows = {r["id"]: r for r in gen_rows()}

    # ── A1 冻结题集结构 ────────────────────────────────────────────────
    ids = sorted(suite)
    cases = {k: len(v["tests"]) for k, v in suite.items()}
    fields_ok = all(
        v.get("prompt") and v.get("oracle") and v.get("meta")
        for v in suite.values())
    a1 = (len(suite) == 8 and set(cases.values()) == {16}
          and fields_ok and len(set(ids)) == 8)
    print(f"\n[A1] 结构：{len(suite)} 题 / {sum(cases.values())} 格 / "
          f"每题格数 {sorted(set(cases.values()))} / 字段齐={fields_ok}"
          f"  {'✅' if a1 else '❌'}")
    bad += 0 if a1 else 1

    # ── A2 打乱 test 顺序，分数必须不变 ───────────────────────────────
    print("\n[A2] 打乱 test 顺序 ⇒ 分数不变（每条 test 自带 setup）")
    a2 = True
    for tid in ORDER_SENSITIVE:
        t = suite[tid]
        base = score(t, t["oracle"])
        rev = score(t, t["oracle"], tests=list(reversed(t["tests"])))
        ok = abs(base - rev) < 1e-9
        a2 &= ok
        print(f"  {tid:5s} 正序={base:.4f}  倒序={rev:.4f}  "
              f"{'✅' if ok else '❌ 依赖执行顺序'}")
    bad += 0 if a2 else 1

    # ── A3 真打分器：oracle 全 1.0 ────────────────────────────────────
    print("\n[A3] 真打分器跑 oracle（题目必须可解）")
    a3 = True
    for tid in ids:
        s = score(suite[tid], suite[tid]["oracle"])
        ok = s == 1.0
        a3 &= ok
        print(f"  {tid:5s} {s:.4f}  {'✅' if ok else '❌'}")
    bad += 0 if a3 else 1

    # ── A4 ⭐ pure（返回值对、不落盘）必须低分 ────────────────────────
    print("\n[A4] 真打分器跑 pure（返回值完全正确、但什么都没写出去）")
    print("     这是「形态确实变了」的运行时证明")
    tot = 0.0
    per: dict[str, float] = {}
    specs = gen_specs()
    a4 = True
    for tid in ids:
        src = getattr(specs.get(tid), "pure", "")
        if not src:
            print(f"  {tid:5s} ⚠️ 生成器未提供 pure ⇒ A4 无法成立")
            a4 = False
            break
        s = score(suite[tid], src)
        per[tid] = s
        tot += s
        print(f"  {tid:5s} pure={s:.4f}   oracle=1.0000")
    else:
        mean = tot / len(ids)
        a4 = mean < 0.10
        print(f"  均分 pure={mean:.4f}（判据 < 0.10）  "
              f"{'✅' if a4 else '❌ 尺子没在测副作用'}")
        # 说清 at04 那 0.38 不是漏洞：那几格只要求「抛异常且不留文件」，
        # pure 恰好满足 —— 它"什么都不做"在原子性题上是**正确行为**。
        print(f"  ⚠️ at04 pure={per.get('at04', 0):.4f} 是**正确**读数："
              f"该题有若干格只要求「抛异常且不留文件」")
    bad += 0 if a4 else 1

    # ── A5 冻结文件 == 生成器重生成 ──────────────────────────────────
    frozen = {k: json.dumps(v, ensure_ascii=False, sort_keys=True)
              for k, v in suite.items()}
    fresh: dict[str, str] = {}
    for r in gen_rows():
        fresh[r["id"]] = json.dumps(r, ensure_ascii=False, sort_keys=True)
    diff = [k for k in frozen
            if k not in fresh or frozen[k] != fresh[k]]
    a5 = not diff
    print(f"\n[A5] 冻结文件 == 生成器重生成："
          f"{'✅ 逐字节相同' if a5 else '❌ 不一致: ' + str(diff[:3])}")
    bad += 0 if a5 else 1

    # ── A6 ⭐ 改坏一个期望值 ⇒ 分数必须掉 ────────────────────────────
    print("\n[A6] 反证：把一条 test 的期望字节改一个字符，分数必须掉")
    a6 = True
    for tid in ("at01", "at08"):
        t = suite[tid]
        tests = list(t["tests"])
        original = tests[0]
        if "== b'" not in original:
            print(f"  {tid:5s} ⚠️ 格式意外，跳过")
            a6 = False
            continue
        head, tail = original.split("== b'", 1)
        mutated = head + "== b'" + ("Z" + tail[1:] if tail else tail)
        base = score(t, t["oracle"])
        after = score(t, t["oracle"], tests=[mutated] + tests[1:])
        ok = after < base
        a6 &= ok
        print(f"  {tid:5s} 原={base:.4f} 改坏一格={after:.4f}  "
              f"{'✅ 掉了' if ok else '❌ 断言恒真'}")
    bad += 0 if a6 else 1

    # ── A7 空答案 ⇒ 0 分 ────────────────────────────────────────────
    print("\n[A7] 空答案 ⇒ 0 分（防「没答案也给分」）")
    a7 = True
    for tid in ("at01", "at06"):
        s = score(suite[tid], "")
        ok = s == 0.0
        a7 &= ok
        print(f"  {tid:5s} {s:.4f}  {'✅' if ok else '❌'}")
    bad += 0 if a7 else 1

    print(f"\n{'✅ 七条全绿' if bad == 0 else f'❌ {bad} 条失败'}")
    if bad == 0:
        print("判读：A4 是这把尺子存在的理由 —— 同一份「返回值完全正确」的实现")
        print("      在 v1~v3 上满分，在这里拿不到 10%。")
        print("⛔ 但它**不**证明「内核会用工具」：判分只看 answer，看不到轨迹。")
    return 0 if bad == 0 else 1


def report() -> int:
    suite = load()
    rows = {r["id"]: r for r in gen_rows()}
    print(f"{'题':6s} {'格':>4s} {'oracle':>8s} {'pure':>7s}  形态")
    tot_p = 0.0
    for tid in sorted(suite):
        t = suite[tid]
        o = score(t, t["oracle"])
        p = score(t, rows[tid].get("pure", ""))
        tot_p += p
        tag = t["prompt"].split("\n")[0][:34]
        print(f"  {tid:5s} {len(t['tests']):4d} {o:8.4f} {p:7.4f}  {tag}")
    print(f"\n  pure 均分 {tot_p / len(suite):.4f}")
    return 0


def main(argv: list[str]) -> int:
    if "--self-check" in argv:
        return self_check()
    return report()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
