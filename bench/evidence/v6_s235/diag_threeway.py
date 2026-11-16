#!/usr/bin/env python3
"""V146 跑后诊断（⛔ 未预注册，不算押中）：把「知识有没有用」判成三值。

主判据 W2/W4b/W5 押破（在线降权在这个语料上没有可测收益）。本诊断回答另一件事：

    「这条知识在这个实现上有没有用」这件事，系统能不能**用自己判过的分**回答？
    如果能，它能不能区分「试了但失败」与「根本试不到」？

三值（系统侧，只用 tries / bscore / fails）：
  OK       bscore ≥ 1.0      —— 试到了
  FAILED   tries > 0 且 bscore < 1.0 —— 试了，最好也没到
  UNTRIED  tries == 0        —— 没试到 ⇒ ⛔ 不知道，不是「有效」

真值口径（写进字段名）：`trace.json` 的**冷启动轨迹**里该键的最高分。
⚠️ 这不是全知真值（冷启动也可能没试到），所以「真值未知」单列一列。
"""
from __future__ import annotations

import ast
import collections
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
EV = HERE.parent
for _d in ("v6_s205", "v6_s208", "v6_s209", "v6_s198"):
    sys.path.insert(0, str(EV / _d))
sys.path.insert(0, str(EV / "v6_s210"))

import idiom_transfer as I     # noqa: E402
import search as S             # noqa: E402
import v119 as V               # noqa: E402
import v120 as W               # noqa: E402
import v121 as X               # noqa: E402
import v146 as F               # noqa: E402


def main() -> None:
    corpus = I.harvest()
    impls = []
    for tid in V.TIDS:
        for h in sorted(corpus[tid]):
            try:
                ast.parse(corpus[tid][h])
            except SyntaxError:
                continue
            impls.append((tid, h))
    mem = json.loads(X.MEM_FILE.read_text(encoding="utf-8"))

    # 真值：冷启动轨迹里每个键的最高分与试过次数
    d = json.loads((HERE / "trace.json").read_text(encoding="utf-8"))
    #: ⛔ 键的粒度必须是 `(tid, h, key)` —— v6_s235 第一版按 `tid` 聚合，
    #:   结果「一个键在**某一个**实现上到过 1.0」被记成「对所有实现都有效」
    #:   ⇒ 真值 INVALID 全 0，混淆矩阵退化成 18/0（纪律 46 的又一种形态）。
    tscore: dict = collections.defaultdict(lambda: -1.0)
    ttries: collections.Counter = collections.Counter()
    for r in d["trace"]:
        kk = (r["tid"], r["h"], tuple(r["key"]))
        ttries[kk] += 1
        if r["score"] > tscore[kk]:
            tscore[kk] = r["score"]

    # 系统侧：online 臂的轨迹
    online = F.run_arm(impls, corpus, mem, "online")

    print("## 混淆矩阵：系统三值 × 真值（口径 = 冷启动轨迹）\n")
    mat: collections.Counter = collections.Counter()
    rows = []
    for tid, h in impls:
        mine = {(a, b) for k in mem[tid] if k["h"] != h for a, b in [tuple(k["act"])]}
        r = online[(tid, h)]
        for k in sorted(mine):
            tr = r.get("tries", {}).get(k, 0)
            bs = r.get("bscore", {}).get(k, -1.0)
            if tr == 0:
                sysv = "UNTRIED"
            elif bs >= 1.0:
                sysv = "OK"
            else:
                sysv = "FAILED"
            # 真值
            if ttries[(tid, h, k)] == 0:
                true = "UNKNOWN"
            elif tscore[(tid, h, k)] >= 1.0:
                true = "VALID"
            else:
                true = "INVALID"
            mat[(sysv, true)] += 1
            rows.append(dict(tid=tid, h=h, key=list(k), system=sysv, truth=true,
                             tries=tr, bscore=bs,
                             cold_tries=ttries[(tid, h, k)]))
    for sv in ("OK", "FAILED", "UNTRIED"):
        line = f"  {sv:8s}"
        for tv in ("VALID", "INVALID", "UNKNOWN"):
            line += f"  {tv}={mat[(sv, tv)]:3d}"
        print(line)
    print(f"\n  合计格数 = {sum(mat.values())}")

    ok = sum(v for k, v in mat.items() if k[0] == "OK" and k[1] == "VALID")
    bad = sum(v for k, v in mat.items() if k[0] == "FAILED" and k[1] == "VALID")
    good = sum(v for k, v in mat.items() if k[0] == "FAILED" and k[1] == "INVALID")
    unknown_ok = sum(v for k, v in mat.items() if k[0] == "UNTRIED")
    print(f"\n  判对(OK↔VALID) = {ok}｜抓对(FAILED↔INVALID) = {good}｜误报(FAILED↔VALID) = {bad}")
    print(f"  没试到(UNTRIED) = {unknown_ok} ⇒ ⛔ 这些是「不知道」，不许算成「有效」")

    dec = ok + good + bad
    if dec:
        print(f"  在**作出判断的 {dec} 格**上，准确 = {ok + good}/{dec}"
              f" = {(ok + good) / dec:.1%}（⛔ 分母不含 UNTRIED）")

    print("\n## 关键对照：g7d3 那条知识（spread=6 useful=2）")
    for tid, h, k in [("g7d3_fmt_amount", hh, ("ret", ".replace(',',' ')"))
                      for hh in sorted(h for t, h in impls if t == "g7d3_fmt_amount")]:
        r = online[(tid, h)]
        tr = r.get("tries", {}).get(k, 0)
        bs = r.get("bscore", {}).get(k, -1.0)
        tv = ("UNKNOWN" if ttries[(tid, h, k)] == 0
              else ("VALID" if tscore[(tid, h, k)] >= 1.0 else "INVALID"))
        print(f"  {h} 系统 tries={tr} bscore={bs:.2f}｜真值 {tv}"
              f"（冷启动 tries={ttries[(tid, h, k)]}）")

    print("\n## g7e1：'on' 与 'true' —— 「没试到」的形态")
    for h in sorted(h for t, h in impls if t == "g7e1_parse_flag"):
        r = online[("g7e1_parse_flag", h)]
        for k in (("T2", "'on'"), ("T2", "'true'")):
            tr = r.get("tries", {}).get(k, 0)
            bs = r.get("bscore", {}).get(k, -1.0)
            tv = ("UNKNOWN" if ttries[("g7e1_parse_flag", h, k)] == 0
                  else ("VALID" if tscore[("g7e1_parse_flag", h, k)] >= 1.0
                        else "INVALID"))
            print(f"  {h} {str(k):16s} tries={tr:3d} bscore={bs:4.1f}｜真值 {tv}")

    (HERE / "diag_threeway.json").write_text(json.dumps(dict(
        matrix={f"{k[0]}|{k[1]}": v for k, v in mat.items()}, rows=rows),
        ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n  已落盘 `diag_threeway.json`")


if __name__ == "__main__":
    main()
