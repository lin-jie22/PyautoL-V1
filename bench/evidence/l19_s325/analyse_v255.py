#!/usr/bin/env python3
"""V255 分析：自测的**有效性**（过 oracle）与**判别力**（抓答案 / 抓 partial）。

三类结果必须分开，否则会把三件事混成一句"自测没用"：
  A. 测试自己错     —— 不过 oracle ⇒ 无效（`false_catch` 若同时抓住答案 = 假抓）
  B. 测试太弱       —— 过 oracle，但连 `partial` 都放过 ⇒ 覆盖面不足
  C. 共享盲区       —— 过 oracle、也抓得住 `partial`，却放过**自己那份答案**
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path("/Users/linshaojie/V1")
EV = ROOT / "bench/evidence/l19_s325"
ANALYSE = ROOT / "bench/evidence/l17_s323/analyse_v253.json"


def main() -> int:
    d = json.loads((EV / "readings_v255.json").read_text(encoding="utf-8"))
    v253 = json.loads(ANALYSE.read_text(encoding="utf-8"))
    scores = {k: v["A"]["score"] for k, v in v253["per_cell"].items()}

    rows = d["rows"]
    print("=" * 100)
    print("A. 逐格三主体判决（T=0，单次；同一份自测跑 answer / oracle / partial）")
    print("=" * 100)
    print(f"{'cell':24}{'v253':>7}   {'answer':<9}{'oracle':<9}{'partial':<9}"
          f"{'chars':>6}{'行数':>5}  分类")
    buckets = {"A_test_broken": [], "B_test_weak": [], "C_shared_blind": [],
               "D_caught": [], "gen_fail": []}
    for r in rows:
        c = r["cell"]
        if r.get("truncated") or not r.get("has_check"):
            buckets["gen_fail"].append(c)
            print(f"{c:24}{scores.get(c, -1):7.3f}   {'-':<9}{'-':<9}{'-':<9}"
                  f"{r.get('chars', 0):>6}{'-':>5}  gen_fail")
            continue
        v = r["verdicts"]
        av = v.get("answer", {}).get("verdict", "-")
        ov = v.get("oracle", {}).get("verdict", "-")
        pv = v.get("partial", {}).get("verdict", "-")
        if ov != "pass":
            cls = "A 测试自己错"
            buckets["A_test_broken"].append(c)
        elif av == "pass":
            if pv == "caught":
                cls = "C 共享盲区（抓住 partial，放过自己）"
                buckets["C_shared_blind"].append(c)
            else:
                cls = "B 测试太弱（连 partial 都放过）"
                buckets["B_test_weak"].append(c)
        else:
            cls = "D 抓住了答案"
            buckets["D_caught"].append(c)
        print(f"{c:24}{scores.get(c, -1):7.3f}   {av:<9}{ov:<9}{pv:<9}"
              f"{r.get('chars', 0):>6}{r.get('test_src', '').count(chr(10)) + 1:>5}  {cls}")

    valid = [r["cell"] for r in rows
             if r.get("has_check") and not r.get("truncated")
             and r["verdicts"].get("oracle", {}).get("verdict") == "pass"]
    # ⚠️ 第一版把 `caught` 只取 D 桶，再拿它和 A 桶求交 ⇒ `false_catch` **恒为空**。
    #    真正的定义是「答案被判错」这个事实的**全集**里，扣掉测试自身无效的那些。
    caught_all = [r["cell"] for r in rows
                  if r.get("has_check") and not r.get("truncated")
                  and r["verdicts"].get("answer", {}).get("verdict") == "caught"]
    false_catch = [c for c in caught_all if c not in valid]
    caught = [c for c in caught_all if c in valid]

    print()
    print("=" * 100)
    print("B. 判据对账（PREREG §3/§4）")
    print("=" * 100)
    print(f"生成成功（有 def check）      {sum(1 for r in rows if r.get('has_check'))}/{len(rows)}")
    print(f"gen_fail（截断/无 check）      {len(buckets['gen_fail'])}"
          f"{'：' + str(buckets['gen_fail']) if buckets['gen_fail'] else ''}")
    print(f"valid = 过 oracle 的有效自测   {len(valid)}/{len(rows)}"
          f"{'  ⚠️ <5 ⇒ 按 §4-3 不给方向结论' if len(valid) < 5 else ''}")
    print(f"caught（真抓住，不含假抓）      {len(caught)}/{len(valid)}")
    print(f"false_catch（测试自己错却报错） {len(false_catch)}"
          f"{'：' + str(false_catch) if false_catch else ''}")
    print()
    print(f"M1 押 caught ≤ 3；实测 {len(caught)}/{len(valid)}")
    print(f"M2 押 valid ≥ 7；实测 {len(valid)}/{len(rows)}")
    print()

    print("=" * 100)
    print("C. 三类失败（决定下一刀往哪走）")
    print("=" * 100)
    for k, label in (("A_test_broken", "测试自己错（不过 oracle）"),
                     ("B_test_weak", "测试太弱（过 oracle 但连 partial 都放过）"),
                     ("C_shared_blind", "共享盲区（抓住 partial 却放过自己的答案）"),
                     ("D_caught", "抓住答案")):
        print(f"{label:44} {len(buckets[k])}  {buckets[k]}")

    out = {"valid": valid, "caught": [c for c in caught if c not in false_catch],
           "false_catch": false_catch, "buckets": buckets,
           "m1": {"bet": 3, "got": len(caught), "denom": len(valid)},
           "m2": {"bet": 7, "got": len(valid), "denom": len(rows)}}
    (EV / "analyse_v255.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n⇒ 写 {EV / 'analyse_v255.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
