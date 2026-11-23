#!/usr/bin/env python3
"""V237 分析：按 `PREREG_v237.md` 的 P4~P7 出判决。

⚠️ 本轮 jsonl 里的 `q` 字段是**首版标签**（`only3`/`only5` 意为「只通过那一格」），
   极易读反 ⇒ 本脚本**一律按 `vec` 重算**，输出 `gate3`/`gate5`/`fixed`。
   这不是洗数据：`vec` 是打分器原始产出，`q` 只是它的一个说法。

用法：python3 analyse_v237.py
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
J = HERE / "probe_v237.jsonl"
G3, G5 = 3, 5          # 格 3 = `assert parse_duration('10m') == 600`（A 挂）
G3_NAME, G5_NAME = "'10m'", "'abc'"


def load() -> list[dict]:
    if not J.exists():
        raise SystemExit("no readings")
    return [json.loads(x) for x in J.read_text(encoding="utf-8").splitlines() if x.strip()]


def fixed_of(vec: list | None) -> str:
    if not vec or len(vec) < 6:
        return "n/a"
    a, b = bool(vec[G3]), bool(vec[G5])
    return ("fix3+fix5" if a and b else "fix3only" if a else "fix5only" if b else "neither")


def main() -> int:
    rows = load()
    arms: dict[str, list[dict]] = {}
    for r in rows:
        arms.setdefault(r["arm"], []).append(r)

    print("=" * 84)
    print("逐臂读数（标签按 `vec` 重算；`fix3` 指格 3 = %s 通过，`fix5` 指格 5 = %s 通过）"
          % (G3_NAME, G5_NAME))
    print("=" * 84)
    hdr = f"{'臂':<7}{'模型':<12}{'n':>3}{'6/6':>5}{'去重':>5}{'EXACT_A':>9}{'EXACT_B':>9}  分布"
    print(hdr)
    summ: dict[str, dict] = {}
    for tag, rs in arms.items():
        n = len(rs)
        h6 = sum(1 for r in rs if r.get("hit6"))
        uniq = len({r.get("code_sha") for r in rs})
        ea = sum(1 for r in rs if r.get("EXACT_A"))
        eb = sum(1 for r in rs if r.get("EXACT_B"))
        dist: dict[str, int] = {}
        for r in rs:
            dist[fixed_of(r.get("vec"))] = dist.get(fixed_of(r.get("vec")), 0) + 1
        summ[tag] = {"n": n, "hit6": h6, "uniq": uniq, "exact_a": ea, "exact_b": eb,
                     "dist": dist,
                     "scores": [r.get("score") for r in rs],
                     "vecs": ["".join("1" if x else "0" for x in (r.get("vec") or [])) for r in rs]}
        print(f"{tag:<7}{rs[0]['model']:<12}{n:>3}{h6:>5}{uniq:>5}{ea:>9}{eb:>9}  {dist}")

    print("=" * 84)
    print("判据")
    print("=" * 84)
    g = lambda t, k: summ.get(t, {}).get(k, 0)          # noqa: E731

    # P4：主判据（7b）
    da, sa, sb = g("D_AB", "hit6"), g("S_A", "hit6"), g("S_B", "hit6")
    if sa >= 1 or sb >= 1:
        p4 = "UNKNOWN"
        note = "单臂自己就命中 6/6 ⇒ H1 不可判（⛔ 不许写成成立）"
    elif da > max(sa, sb):
        p4 = "成立"
        note = "两体同喂带来了单臂没有的 6/6"
    elif da == 0:
        p4 = "不成立"
        note = "零假设成立：组合投喂也无收益"
    else:
        p4 = "UNKNOWN"
        note = "落进未覆盖的格"
    print(f"P4 主判据（7b）：count6(D_AB)={da} vs max(S_A={sa}, S_B={sb}) ⇒ **{p4}**")
    print(f"   {note}")

    # P5：抄没抄（只报读数）
    for tag in arms:
        d = summ[tag]
        print(f"P5 {tag:<7} EXACT_A={d['exact_a']}/{d['n']} EXACT_B={d['exact_b']}/{d['n']} "
              f"去重实现={d['uniq']}/{d['n']}")

    # P6：修了几处
    print("P6 修了几处（格 3=%s、格 5=%s）" % (G3_NAME, G5_NAME))
    for tag in arms:
        print(f"   {tag:<7} {summ[tag]['dist']}")
    da_dist = summ.get("D_AB", {}).get("dist", {})
    p6 = ("D_AB 出现 `fix3+fix5` ⇒ 真的同时修了两处"
          if da_dist.get("fix3+fix5") else
          "D_AB 只有单格 ⇒ 组合没发生（不管分数）")
    print(f"   ⇒ {p6}")

    # P7：位序
    if "D_BA" in summ and "D_AB" in summ:
        same = summ["D_BA"]["dist"] == summ["D_AB"]["dist"]
        print(f"P7 位序：D_AB={summ['D_AB']['dist']} D_BA={summ['D_BA']['dist']} "
              f"⇒ {'不敏感' if same else '**顺序敏感**（不作因果量，V90）'}")

    # 14b 小样：描述性
    if "D_AB14" in summ:
        d = summ["D_AB14"]
        print(f"小样 D_AB14（14b，破闸，不参与判据）：6/6={d['hit6']}/{d['n']} "
              f"分布={d['dist']} vecs={d['vecs']}")

    out = {"arms": summ, "P4": p4, "P4_note": note,
           "P6": p6, "grid": {"g3": G3_NAME, "g5": G5_NAME}}
    (HERE / "readings_v237.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("=" * 84)
    print("落盘 readings_v237.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
