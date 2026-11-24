"""V239c 附表：11 份实现按「洞的属性」排序 —— 回答「哪条洞更靠谱」。

每份算四个量（全部真跑，不靠读代码猜）：
  hit   单点邻域里能升到 6/6 的候选数（0 = 该洞不存在局部修复）
  nbr   单点邻域大小
  flat  与自身同分的邻居占比（越高 = 梯度越平）
  pos   挂的格在题面列举里的序号（1 = 首例最显眼，4 = 末例最隐蔽；— = 不在列举里）

另记「动作类型」：与起点的关系是照抄 / 单点改 / 整段重写（按行数变化与公共前缀判）。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import repair_probe_v239c as P  # noqa: E402

D = P.D
PROMPT_ORDER = P.PROMPT_ORDER

#: 起点归因（只认 obs 里的 source 链）：
#:   seed  = 账本里那条 0.8333（无更早起点）
#:   act   = 该轮 act 从题面独立生成（⛔ 不把它算成「从 seed 改」）
#:   swap  = 换路产出，起点 = 该轮日志里的 best_code
ORIGIN = {
    "90579ecebe633352": "",
    "09d0e23ddc7dd5ac": "",
    "18a457feff34dbb5": "",
    "1eb1b0ab346e9757": "",
    # obs_d（auto 组，起点 09d0e23d）
    "9454d0d2e8796619": "09d0e23ddc7dd5ac",
    "6f47167d825afd44": "09d0e23ddc7dd5ac",
    "5c07ad8e55de908d": "09d0e23ddc7dd5ac",
    "3b692d5865a7248c": "09d0e23ddc7dd5ac",
    # obs_d2（ledger 组，起点 90579ece）
    "fda36030e884e1cb": "90579ecebe633352",
    "303b3bb6f43c4633": "90579ecebe633352",
    "6925d6b93292ba7b": "90579ecebe633352",
}


def limb(src: str, base: str) -> str:
    """与起点的字符级相似度（difflib，0~1）。⛔ 不用「行数差」——行数相同也可能是重写。"""
    if not base:
        return "—"
    if src == base:
        return "1.000"
    import difflib

    return f"{difflib.SequenceMatcher(None, base, src).ratio():.3f}"


def main() -> int:
    sys.stdin = open("/dev/null")
    files = sorted(P.CAND.glob("*.py"))
    srcs = {p.stem: p.read_text() for p in files}
    full = [s for s in srcs if P.judge(srcs[s])[1] == 1.0]
    donors = [srcs[s] for s in full]
    pool, _ = P.tokens_of(list(srcs.values()) + donors)

    rows = []
    for sha, src in srcs.items():
        vec, sc = P.judge(src)
        if sc == 1.0:
            continue
        hit, nbr = 0, 0
        for _desc, new in P.single_edit_space(src, pool):
            nbr += 1
            if P.judge(new)[1] == 1.0:
                hit += 1
        same = 0
        # 同分率用较小采样（步长 7）以控时；写了步长，可复算
        for i, (_d, new) in enumerate(P.single_edit_space(src, pool)):
            if i % 7:
                continue
            if P.judge(new)[1] == sc:
                same += 1
        n_samp = (nbr + 6) // 7
        miss = [i for i, ok in enumerate(vec) if not ok]
        pos = "—"
        if len(miss) == 1:
            m = re.search(r"'([^']*)'", P.TESTS[miss[0]])
            lit = m.group(1) if m else ""
            if lit in PROMPT_ORDER:
                pos = f"{PROMPT_ORDER.index(lit) + 1}/4"
        base = srcs.get(ORIGIN.get(sha, ""), "")
        rows.append({
            "sha": sha[:16], "vec": "".join("1" if x else "0" for x in vec), "score": sc,
            "hit": hit, "nbr": nbr, "flat": round(same / max(1, n_samp), 3),
            "pos": pos, "act": limb(src, base) if base else "—",
            "lines": len(src.splitlines()), "bytes": len(src.encode()),
        })

    rows.sort(key=lambda r: (-r["score"], r["hit"]))
    print(f"{'sha':17s} {'vec':7s} {'score':6s} {'hit':>4s} {'nbr':>6s} {'flat':>6s} "
          f"{'pos':>5s} {'sim':>5s} {'行':>3s}")
    for r in rows:
        print(f"{r['sha']:17s} {r['vec']:7s} {r['score']:<6.4f} {r['hit']:4d} {r['nbr']:6d} "
              f"{r['flat']:6.3f} {r['pos']:>5s} {r['act']:>5s} {r['lines']:3d}")
    (D / "table_v239c.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
