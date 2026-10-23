#!/usr/bin/env python3
"""生成型题集的**产物源**：`bench/specgen/tasks_v1.jsonl`。

为什么产物不进 `bench/tasks/`：那个目录被三处 glob 吃（AK 组的族聚类、
跨领域冒烟、指标索引）。本轮要证的是**准入器**本身，不是把新题混进旧读数 ——
混进去会静默改变既有断言的输入，而那种改变正是本项目一直在防的"顺手换尺子"。
要跑这批题：`python run.py --suite bench/specgen/tasks_v1.jsonl`。

⚠️ 「这 6 道题能被造出来且合格」≠「这 6 道题能分辨强弱」。
   后者要真模型读数，本轮**没有**（见 DESIGN §34 的边界）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import taskspec as T                                              # noqa: E402

V1 = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# 用例：**每一道题都用这一组**。
# 为什么共用：A5 要求"题面写了参数 ⇒ 用例里必须有一处能把它打出来"。
# 把用例按题定制，就会变成"为这道题的参数挑一条能打出来的用例"，
# 于是 A5 从"题合格"退化成"用例挑得好"。共用一组，是让用例不可被优化。
# 覆盖：大小写重复（fold）、重复行（keep）、长度差异（sort key/len）、
#       超长行（truncate）、含制表符与连续空白（collapse）、空列表（边界）。
# ---------------------------------------------------------------------------
CASES: list[list[str]] = [
    ["  Alpha Beta ", "gamma", "ALPHA BETA", "delta\tepsilon", "Zeta"],
    ["one two  three", "One Two Three", "FOUR", "five five", "six"],
    ["x"],
    [],
    ["Mixed CASE line", "mixed case line", "  Mixed   Case   LINE  ",
     "tail-end token here", "T"],
    ["a b c d e f g", "abcdefg", "A B C D E F G", "short",
     "much longer line than the limit"],
    ["same line", "same line", "  same   line  "],
]

SPECS: list[T.Spec] = [
    {"id": "sg01", "fn": "norm_sg01", "rules": [
        {"op": "strip"},
        {"op": "dedupe", "keep": "last", "fold": True},
        {"op": "collapse"},
        {"op": "sort", "key": "len", "desc": True},
        {"op": "truncate", "n": 8},
    ]},
    {"id": "sg02", "fn": "norm_sg02", "rules": [
        {"op": "collapse"},
        {"op": "strip"},
        {"op": "prefix", "text": "[v2] "},
        {"op": "dedupe", "keep": "first", "fold": True},
        {"op": "sort", "key": "text", "desc": True},
        {"op": "truncate", "n": 10},
    ]},
    {"id": "sg03", "fn": "norm_sg03", "rules": [
        {"op": "strip"},
        {"op": "dedupe", "keep": "first", "fold": True},
        {"op": "prefix", "text": "> "},
        {"op": "sort", "key": "len", "desc": False},
        {"op": "truncate", "n": 6},
    ]},
    # ⚠️ `lower` 与"折叠去重"放在同一道题里会互相抵消：转小写之后，
    #    "Alpha Beta" 与 "ALPHA BETA" 变成同一行 ⇒「保留哪一次」不可观测，
    #    A5 会当场判这道题不合格（实测第一次跑就是这么红的）。
    #    ⇒ 转小写的题不带折叠去重，这不是偷懒，是准入器逼出来的结构。
    {"id": "sg04", "fn": "norm_sg04", "rules": [
        {"op": "lower"},
        {"op": "collapse"},
        {"op": "sort", "key": "text", "desc": True},
        {"op": "truncate", "n": 7},
        {"op": "prefix", "text": "#"},
    ]},
    {"id": "sg05", "fn": "norm_sg05", "rules": [
        {"op": "strip"},
        {"op": "truncate", "n": 9},
        {"op": "dedupe", "keep": "last", "fold": True},
        {"op": "sort", "key": "len", "desc": True},
        {"op": "prefix", "text": "· "},
    ]},
    {"id": "sg06", "fn": "norm_sg06", "rules": [
        {"op": "collapse"},
        {"op": "dedupe", "keep": "first", "fold": True},
        {"op": "sort", "key": "text", "desc": True},
        {"op": "truncate", "n": 5},
        {"op": "prefix", "text": "· "},
    ]},
]


def _line(spec: T.Spec) -> str:
    return json.dumps({
        "id": spec["id"],
        "prompt": T.render_prompt(spec),
        "tests": T.tests_of(spec, CASES),
        "oracle": T.compile_oracle(spec),
        "partial": T.compile_oracle(spec, naive=True),
        "null": "",
        "meta": {"tags": "specgen,row-pipeline", "level": "mid",
                 "spec": spec},
    }, ensure_ascii=False)


def main() -> int:
    ok = True
    rows: list[str] = []
    print(f"{'题':6s} {'规则':4s} {'题面字':5s} {'常识分':7s} 准入")
    for spec in SPECS:
        fails = T.admit(spec, CASES)
        hit, n = T.naive_hits(spec, CASES)
        # 常识实现必须**严格居中**：全过 ⇒ 这题用常识就能做，没有分辨点；
        # 全不过 ⇒ 这题只是"和常识完全不同"，也不是分辨点（任何写法都错）。
        if not 0 < hit < n:
            fails = fails + [f"{spec['id']} 常识分 {hit}/{n} 没严格居中"]
        ok = ok and not fails
        print(f"{spec['id']:6s} {len(spec['rules']):4d} "
              f"{len(T.render_prompt(spec)):5d} {f'{hit}/{n}':7s} "
              f"{'✅' if not fails else '❌ ' + fails[0]}")
        for f in fails[1:]:
            print(f"       {f}")
        rows.append(_line(spec))
    if not ok:
        return 1

    # 铸出来的新题也必须过同一条准入 —— 否则"无限题集"就是一句口号。
    minted = [T.mint(seed, f"s{seed:02d}") for seed in range(1, 9)]
    m_fail = [f for sp in minted for f in T.admit(sp, CASES)]
    if m_fail:
        print("❌ 按种子铸的题没过准入：")
        for f in m_fail[:5]:
            print("   ", f)
        return 1
    print(f"✅ 按种子铸的 {len(minted)} 道新题全部通过同一条准入（A3–A6）")

    out = V1 / "bench" / "specgen" / "tasks_v1.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(rows) + "\n", encoding="utf-8")
    print(f"写入 {out}  题数={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
