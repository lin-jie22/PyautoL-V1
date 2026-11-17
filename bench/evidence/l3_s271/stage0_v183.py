"""V183 STAGE0：同一批 8 题上，三种判据源 × 三种实现。

读数口径（每题一行）：
  v79_true  V79 的 22 条断言是否放行 oracle
  v79_bite  同一批断言是否拒绝 partial
  rel_true  关系库是否放行 oracle
  rel_bite  关系库是否拒绝 partial
  rel_stub  关系库是否拒绝空实现（拒绝=好；放行=恒真）
判别力 = true ∧ bite。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import relations as RL  # noqa: E402
import substrate as S  # noqa: E402

T = S.tasks()
A = S.v79_asserts()


def judge_rel(code: str, src: str) -> str:
    return S.judge(code + "\n\n" + src, ["assert _rel()"])


def main() -> int:
    rows = []
    print(f"{'task':24s} {'impls':>5s} {'v79_t':>6s} {'v79_b':>6s} {'rel_t':>6s} "
          f"{'rel_b':>6s} {'rel_s':>6s}  rel_break")
    for tid in T:
        t = T[tid]
        rels = [r for r in RL.RELATIONS if r["task"] == tid]
        assert rels, f"{tid} 没有关系"
        out = {}
        for tag, code in (("oracle", t["oracle"]), ("partial", t["partial"]),
                          ("stub", S.stub(t))):
            ok = [judge_rel(code, r["src"]) == "" for r in rels]
            out[tag] = ok
        v79_t = S.judge(t["oracle"], A[tid]) == ""
        v79_b = S.judge(t["partial"], A[tid]) != ""
        rel_t = all(out["oracle"])
        rel_b = not all(out["partial"])
        rel_s = not all(out["stub"])
        brk = [r["name"] for r, ok in zip(rels, out["oracle"]) if not ok]
        print(f"{tid:24s} {len(rels):5d} {str(v79_t):>6s} {str(v79_b):>6s} {str(rel_t):>6s} "
              f"{str(rel_b):>6s} {str(rel_s):>6s}  {','.join(brk) or '-'}")
        rows.append({"id": tid, "n_rel": len(rels),
                     "v79_true": v79_t, "v79_bite": v79_b,
                     "rel_true": rel_t, "rel_bite": rel_b, "rel_stub_bite": rel_s,
                     "rel_fails_on_oracle": brk,
                     "detail": {k: v for k, v in out.items()},
                     "memory_dependent": tid in S.MEMORY_DEPENDENT})

    n = len(rows)
    print()
    for tag in ("v79", "rel"):
        d = sum(1 for r in rows if r[f"{tag}_true"] and r[f"{tag}_bite"])
        print(f"{tag}: 过正确 {sum(1 for r in rows if r[f'{tag}_true'])}/{n} "
              f"咬错 {sum(1 for r in rows if r[f'{tag}_bite'])}/{n} 判别力 {d}/{n}")
    sub = [r for r in rows if not r["memory_dependent"]]
    print(f"题面自足 {len(sub)} 题：v79 判别力 "
          f"{sum(1 for r in sub if r['v79_true'] and r['v79_bite'])}/{len(sub)}，"
          f"rel 判别力 {sum(1 for r in sub if r['rel_true'] and r['rel_bite'])}/{len(sub)}")
    mem = [r for r in rows if r["memory_dependent"]]
    print(f"题面不完备 {len(mem)} 题：v79 判别力 "
          f"{sum(1 for r in mem if r['v79_true'] and r['v79_bite'])}/{len(mem)}，"
          f"rel 判别力 {sum(1 for r in mem if r['rel_true'] and r['rel_bite'])}/{len(mem)}")

    (HERE / "stage0_v183.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
