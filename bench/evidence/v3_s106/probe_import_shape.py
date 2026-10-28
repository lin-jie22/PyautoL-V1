#!/usr/bin/env python3
"""零模型探针（第 106 轮 §76）：**「机会」是题面的函数，还是答案形态的函数？**

## 为什么要有这一份

本轮先造了一把尺子（`tasks_v5`）：3 道题**强制**用外部模块（`re`/`json`/`datetime` + `os`），
3 道控制题**结构上**零 `import`。预注册 D0 赌的是「强制用模块 ⇒ 模型会忘 import ⇒ 机会 > 0」。

实测：`=1` 臂 **18 次交付 0 次打回、0 个未定义名** ⇒ 机会 = 0/3 题。

于是本轮真正要回答的问题变了：

> 「缺名字」到底是**题目的属性**（题面逼出来），还是**答案的属性**（模型这一份输出长什么样）？

## 判据（可复现，零模型）

对每份**已冻结**的交付物算三件事：

1. `has_import`：抽出的代码里**有没有** `import` / `from ... import` 行（任意缩进）
2. `has_top_import`：有没有**顶层**（行首无缩进）`import`
3. `names = mw.verify.undefined_names(code)`（与内核载荷同源）

然后交叉：**「缺名字」这个事件在「没有 import 行」的答案里占多少、在「有 import 行」的答案里占多少**。

⛔ 这是**相关**，不是「无 import 导致缺名字」的证明：两者都是「模型这次写得潦草」的下游。
   它能支撑的只有一句话：**机会只出现在某一类答案里**，而题面不决定答案属于哪一类。

用法
    python3 bench/evidence/v3_s106/probe_import_shape.py
    python3 bench/evidence/v3_s106/probe_import_shape.py --selftest
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
EVID = ROOT / "bench" / "evidence"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench" / "scorers"))
sys.path.insert(0, str(EVID / "v2_s105"))

import code_exec as CE                     # noqa: E402
from mw.verify import undefined_names      # noqa: E402

_IMPORT_ANY = re.compile(r"(?m)^[ \t]*(?:import|from)\s+[\w.]+")
_IMPORT_TOP = re.compile(r"(?m)^(?:import|from)\s+[\w.]+")


def shape(code: str) -> dict:
    names = undefined_names(code)
    return {
        "has_import": bool(_IMPORT_ANY.search(code)),
        "has_top_import": bool(_IMPORT_TOP.search(code)),
        "undecidable": names is None,
        "names": [n for _f, n in (names or [])],
    }


def collect_deliveries() -> list[dict]:
    """所有含 answer 行的台账 ⇒ 按 (suite, task, sha) 去重（同一份答案可能被多份台账引用）。"""
    import probe_failfam as PF          # 只借它的台账遍历（同一个 collect）
    seen: set[tuple] = set()
    out: list[dict] = []
    for r in PF.collect():
        key = (r["suite"], r["task"], r["sha"])
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def families() -> dict[tuple, str]:
    p = EVID / "v2_s105" / "table.json"
    if not p.exists():
        return {}
    rows = json.loads(p.read_text(encoding="utf-8"))
    return {(r["suite"], r["task"], r["sha"]): r["family"] for r in rows}


def report(rows: list[dict]) -> dict:
    fam = families()
    per_suite: dict[str, list[dict]] = collections.defaultdict(list)
    for r in rows:
        s = shape(CE.extract_code(r["answer"] or ""))
        rec = {**r, **s, "family": fam.get((r["suite"], r["task"], r["sha"]), "?")}
        per_suite[r["suite"]].append(rec)
    return {"per_suite": dict(per_suite)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--json")
    a = ap.parse_args()

    if a.selftest:
        bad = 0
        cases = [
            ("S1 用了 os 且无 import ⇒ has_import=False 且名字=[os]",
             "def f(path):\n    os.makedirs(path)\n",
             {"has_import": False, "names": ["os"]}),
            ("S2 有顶层 import ⇒ has_import=True 且无名",
             "import os\n\ndef f(path):\n    os.makedirs(path)\n",
             {"has_import": True, "names": []}),
            ("S3 函数体内 import 也算有 ⇒ has_import=True / has_top=False",
             "def f(path):\n    import os\n    os.makedirs(path)\n",
             {"has_import": True, "has_top_import": False, "names": []}),
            ("S4 无 import 但也无名 ⇒ 必须与 S1 分开记（不制造假机会）",
             "def f(x):\n    return x + 1\n",
             {"has_import": False, "names": []}),
        ]
        for why, code, want in cases:
            got = shape(code)
            ok = all(got.get(k) == v for k, v in want.items())
            bad += 0 if ok else 1
            print(f"  {'✅' if ok else '❌'} {why}  实测={got}")
        print(f"{'✅ 形态探针自证 4/4' if bad == 0 else f'❌ {bad} 条自证失败'}")
        return 0 if bad == 0 else 1

    rep = report(collect_deliveries())
    print("=" * 84)
    print("  「缺名字」出现在什么形态的答案里（零模型，全部已冻结交付物）")
    print("=" * 84)
    tot = collections.Counter()
    print(f"\n  {'尺子':10s} {'交付':>4s} {'无import':>8s} {'无import且缺名':>14s} "
          f"{'有import且缺名':>14s} {'缺名合计':>8s}")
    for suite, rows in sorted(rep["per_suite"].items()):
        no_imp = [r for r in rows if not r["has_import"]]
        n1 = [r for r in no_imp if r["names"]]
        n2 = [r for r in rows if r["has_import"] and r["names"]]
        print(f"  {suite:10s} {len(rows):4d} {len(no_imp):8d} {len(n1):14d} "
              f"{len(n2):14d} {len(n1) + len(n2):8d}")
        tot["rows"] += len(rows)
        tot["no_imp"] += len(no_imp)
        tot["n1"] += len(n1)
        tot["n2"] += len(n2)
    print(f"  {'合计':10s} {tot['rows']:4d} {tot['no_imp']:8d} {tot['n1']:14d} "
          f"{tot['n2']:14d} {tot['n1'] + tot['n2']:8d}")

    print(f"\n  缺名字的交付物共 {tot['n1'] + tot['n2']} 份，其中：")
    print(f"    整份答案**没有**任何 import 行：{tot['n1']} 份")
    print(f"    有 import 行但仍缺某个名字：    {tot['n2']} 份")

    # §75 那 94 份的分族交叉（PASS 不进分母）
    fam_rows = [r for rs in rep["per_suite"].values() for r in rs if r["family"] != "?"]
    if fam_rows:
        print(f"\n  §75 冻结分类表（{len(fam_rows)} 份）按族 × 形态：")
        tab = collections.Counter((r["family"], r["has_import"]) for r in fam_rows)
        for fam_name in ("PASS", "NO_NAME", "NAME_FIXED", "NO_CODE",
                         "UNDECIDABLE", "NAME_OTHER"):
            if not any(k[0] == fam_name for k in tab):
                continue
            print(f"    {fam_name:13s} 有 import {tab[(fam_name, True)]:3d} 份 / "
                  f"无 import {tab[(fam_name, False)]:3d} 份")

    if a.json:
        slim = {s: [{k: r[k] for k in ("task", "sha", "family", "has_import",
                                       "has_top_import", "names", "undecidable")}
                    for r in rs] for s, rs in rep["per_suite"].items()}
        Path(a.json).write_text(json.dumps(slim, ensure_ascii=False, indent=1),
                                encoding="utf-8")
        print(f"\n已写 {a.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
