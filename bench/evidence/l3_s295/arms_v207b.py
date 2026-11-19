"""V207b：**题族**聚簇（V207 漏掉的第三个聚类键）。

复用 arms_v207 的判定与 LLM 调用，只换聚类键。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from arms_v207 import judge, load_rewards, run_arm, task_func_names   # noqa: E402
from mw.config import settings as _settings                          # noqa: E402
from mw.llm import HTTPLLM                                           # noqa: E402

#: 预注册 §1：族 → 函数名前缀/全名。⛔ 读数之前定死。
FAMILY = {
    "解析":     ["parse_"],
    "结构转换": ["flatten", "unflatten", "diff_tree", "to_csv", "csv_row", "to_ini", "encode_"],
    "序列":     ["merge_ranges", "rle", "dedupe", "top_k", "spiral_order",
                 "next_permutation", "median"],
    "字符串":   ["commas", "slugify", "truncate", "normalize_ws", "mask_email",
                 "word_freq", "quote_join"],
    "数值":     ["base_convert", "gcd_lcm", "split_amount", "fmt_amount",
                 "roman_to_int", "min_meeting_rooms"],
    "校验":     ["is_valid_"],
}


def family_of(func: str) -> str:
    """题名 `t01_parse_duration` / `g7s1_flatten_nested` → 族。

    ⚠️ 首版 bug（已修）：条件写成 `p.endswith("_") and cand.startswith(p)`，
    于是不以 `_` 结尾的前缀（`flatten`/`to_csv`）永远不匹配 ⇒ `g7s1_flatten_nested`
    被归成「未归类」。这是「装置自己错了，读数看起来却像模型的错」的典型。
    """
    parts = func.split("_")
    # 从最短后缀开始试（i 越大后缀越短），先命中长前缀
    for i in range(len(parts)):
        cand = "_".join(parts[i:])
        for fam, pres in FAMILY.items():
            if any(cand == p or cand.startswith(p) for p in pres):
                return fam
    return "未归类"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, action="append")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    s = _settings()
    if not s.llm.usable():
        print("❌ 需要真模型环境变量")
        return 2
    llm = HTTPLLM(s.llm)
    names = task_func_names()

    rw: list[dict] = []
    for d in a.db:
        rw.extend(load_rewards(Path(d)))
    fails = [r for r in rw if not r["passed"] and r["failure"].strip()]
    print(f"reward={len(rw)} 失败={len(fails)}")

    by_fam: dict[str, list[dict]] = defaultdict(list)
    for r in fails:
        by_fam[family_of(r["task_id"])].append(r)
    print("族分布：", {k: (len(v), sorted({x['task_id'] for x in v})) for k, v in by_fam.items()})

    units = [(f"族={k}（{len(v)} 条 / {len({x['task_id'] for x in v})} 道题）",
              "\n".join(f"- 题 {x['task_id']}：{x['failure'][:180]}" for x in v[:8]))
             for k, v in sorted(by_fam.items())]

    print(f"\n== 臂 D_family  单元={len(units)}")
    arm = run_arm(llm, units, names, tag="D_family")
    for o in arm["out"]:
        print(f"   [{o.get('gate','ERR')}/{o.get('cls','-')}] {o.get('line','')[:120]}")

    res = {"rewards": len(rw), "fails": len(fails),
           "families": {k: {"n": len(v), "tasks": sorted({x['task_id'] for x in v})}
                        for k, v in by_fam.items()},
           "arm": arm, "names": sorted(names)}
    json.dump(res, open(a.out, "w"), ensure_ascii=False, indent=1)
    print(f"\nD_family 单元={arm['units']} kept={arm['kept']} "
          f"跨题={arm['cross']} 题级={arm['task']} 空话={arm['filler']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
