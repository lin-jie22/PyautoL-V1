#!/usr/bin/env python3
"""生成型题集的**产物源**：`bench/specgen/tasks_v1.jsonl` 与 `tasks_v2.jsonl`。

第八十二轮起有两份（§45）：

    tasks_v1.jsonl   6 题 × 14 断言 =  84 格   旧读数（0.6905）都在这把尺子上
    tasks_v2.jsonl  10 题 × 14 断言 = 140 格   旧 6 题 + 新 4 题（sg07~sg10）

⛔ 旧文件**逐字节不改**：它是历史读数的尺子。加题只发生在 v2。
⛔ 难度**不许**用内核分数来筛，只能用 `oracle/partial/null` 三条自检标定
   （见 `taskspec.WHY_NEW_OPS`）。

为什么产物不进 `bench/tasks/`：那个目录被三处 glob 吃（AK 组的族聚类、
跨领域冒烟、指标索引）。本轮要证的是**准入器**本身，不是把新题混进旧读数 ——
混进去会静默改变既有断言的输入，而那种改变正是本项目一直在防的"顺手换尺子"。
要跑这批题：`python run.py --suite bench/specgen/tasks_v2.jsonl`。

⚠️ 「这 10 道题能被造出来且合格」≠「这 10 道题能分辨强弱」。
   后者要真模型读数，见 DESIGN §45 的读数表与边界。
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
#
# ⚠️ 第七十四轮：7 → **14** 条，而且**不是 21**。
#    §36.4 的处方写的是"加密到 21 条/题"，本轮把它量了一遍（`bench/casepower.py`）：
#
#      7 条能分辨 41 种「错误实现」；定义空间（400 条随机输入）上的天花板 75 种；
#      按"新增种类最多"贪心追加，**第 14 条达到 75（饱和）**，
#      再往后加（21 条也一样）种类数**一位不动** —— 只是把 1/N 的分母做大。
#
#    ⇒ 21 是拍板数，不是需求数。**条数不是刻度**：两个错误实现拿到同一个分，
#      多切几刀也不会把它们分开。这是本轮撤回自己处方的地方（DESIGN §37）。
#    前 7 条是第七十一轮冻结的，一个字没动；后 7 条的来源是
#    `python3 bench/select_cases.py`（日志在 `bench/evidence/scale_s74/`），
#    每条后面标的是它带来的**新增可分种类**（不是"新增通过条数"）。
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
    # ── 以下 7 条：贪心选出（新增种类数写在行尾）────────────────────────
    ["  pad  ", "ze ta"],                                  # +10 两行皆带首尾空白
    ["ze ta", "dup DUP"],                                  # +13 内部空格 + 混合大小写
    ["  pad  ", "DUP"],                                    # +4  折叠去重的最小对
    ["delta", "DELTA", "delta"],                           # +4  同键三连（保留哪一次）
    [" ze ta  ", "dup", "same line"],                      # +1  前导空格 + 多词
    ["ALPHA", "GAMMA", "BETA X", "  pad  ", "GAMMA"],      # +1  5 行含重复键
    [" DUP ", "dup"],                                      # +1  折叠去重（无内部空格）
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


# ---------------------------------------------------------------------------
# 第八十二轮（§45）：第二批题 —— **先定后量**，理由见 `taskspec.WHY_NEW_OPS`
#
# ⛔ 这四条**不是**试出来的：它们是在跑任何真模型读数**之前**按"实现面/默认语义"
#    选定的，选完就跑，跑完不删。删哪一条都是拿内核分数筛题（严禁）。
# ⛔ 旧 6 题一个字不改：v2 = 旧 6 + 新 4 ⇒ 逐题读数仍可与旧台账对照（聚合分不可，
#    `suite_sha` 变了，§37 的老规矩）。
# ---------------------------------------------------------------------------
SPECS_V2_EXTRA: list[T.Spec] = [
    # 实现面：要把"1 个空白"与"2 个以上"分开 ⇒ 正确写法几乎必然需要 `re`
    {"id": "sg07", "fn": "norm_sg07", "rules": [
        {"op": "strip"},
        {"op": "squeeze", "min_run": 2},
        {"op": "dedupe_adj", "fold": True},
        {"op": "sort", "key": "len", "desc": True},
        {"op": "truncate", "n": 8},
    ]},
    # 默认语义：截断从**后面**数 + 「去重」是全局去重（题面要求的是折叠去重）
    {"id": "sg08", "fn": "norm_sg08", "rules": [
        {"op": "collapse"},
        {"op": "keep_tail", "n": 7},
        {"op": "dedupe", "keep": "first", "fold": True},
        {"op": "prefix", "text": "[v2] "},
        {"op": "sort", "key": "text"},
    ]},
    # 相邻 vs 全局：`dedupe_adj(fold=False)` 与「不去重」在多数用例上同分
    {"id": "sg09", "fn": "norm_sg09", "rules": [
        {"op": "squeeze", "min_run": 3},
        {"op": "strip"},
        {"op": "dedupe_adj", "fold": False},
        {"op": "sort", "key": "text", "desc": True},
        {"op": "keep_tail", "n": 9},
    ]},
    # 转小写与 squeeze 同现（**不**带 fold 型去重 ⇒ 没有 §34 那个不可观测参数）
    {"id": "sg10", "fn": "norm_sg10", "rules": [
        {"op": "lower"},
        {"op": "squeeze", "min_run": 2},
        {"op": "sort", "key": "len"},
        {"op": "keep_tail", "n": 6},
        {"op": "prefix", "text": "#"},
    ]},
]

#: 题集文件 → 该文件里的题。**唯一**的注册表：`main()` 与契约 AZ76/AZ77 都读它。
SUITES: dict[str, list[T.Spec]] = {
    "tasks_v1.jsonl": SPECS,
    "tasks_v2.jsonl": SPECS + SPECS_V2_EXTRA,
}


def _build(specs: list[T.Spec]) -> tuple[bool, list[str]]:
    ok = True
    rows: list[str] = []
    print(f"{'题':6s} {'规则':4s} {'题面字':5s} {'常识分':7s} 准入")
    for spec in specs:
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
    return ok, rows


def main() -> int:
    ok = True
    written: dict[str, list[str]] = {}
    for name, specs in SUITES.items():
        print(f"── {name}（{len(specs)} 题）──")
        ok_i, rows = _build(specs)
        ok = ok and ok_i
        written[name] = rows

    # 新算子的坏样本（第八十二轮）：**证伪的是"两份手写实现自己跟自己对得上"**
    new_rp = T.reverse_proofs_new_ops(SPECS_V2_EXTRA, CASES)
    for k, want in T.NEW_OP_PROOF_EXPECT.items():
        got = new_rp.get(k) or []
        if not any(want in f for f in got):
            print(f"❌ 新算子坏样本 {k} 没命中 {want} ⇒ 那条证据是死的（清单={got[:1]}）")
            ok = False
        else:
            print(f"✅ 新算子坏样本 {k} ⇒ {want} 命中：{next(f for f in got if want in f)[:70]}")
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

    out_dir = V1 / "bench" / "specgen"
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, rows in written.items():
        out = out_dir / name
        out.write_text("\n".join(rows) + "\n", encoding="utf-8")
        print(f"写入 {out}  题数={len(rows)}  断言={sum(len(json.loads(r)['tests']) for r in rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
