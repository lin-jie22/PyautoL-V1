"""V32 探针 —— 「可动面是不是一颗骰子」（纯离线，零 LLM、零网络、只读历史产物）

## 为什么要做这个

V31 判 `J_DICE`：`s3`（74 字零信息）与 `s4`（74 字可行动指令）**逐格完全相同**，
而两者与 `s1`（8 字原文）差一对 16 格。当时只能写「内容项 0」。
本轮把 `/tmp/v31` 的答案原文取出来看，机制是**两行代码**：

    s4/e1wi（0 分）      kept = set()  …  kept.add(x); kept.append(x)   ⇒ AttributeError
    s1/e1ni（0 分）      kept = set()  …  _strip_tags 用 re 但没 import re ⇒ NameError

两处都是**代码抽搐**（同一处理既 add 又 append / 忘了 import），不是能力差异。
本探针要回答的是：**这个抽搐在历史全量里是不是一颗骰子**，以及
**Σ 的 ±16 与它是不是同一件事**。

## 读数口径（⛔ 三条都不许换）

1. 分数只取 `runs.jsonl` 的 `report.per_task[].score`（唯一真口径）。
2. 答案只取 `answers.jsonl` 中该 `(run_id, task_id)` 的**最后一行**（行序=尝试序）。
3. 抽搐判据 = `kept = set()`（正则，允许 `kept:set = set()` 之类变体不认 —— 只认真实出现的那种）。

## 它不做什么

- 不打分、不重算 Σ、不改任何文件；只读 `/tmp/v*`。
- 不产生判据。它输出的数是**下一轮预注册的输入**，不是结论。
"""
from __future__ import annotations

import collections
import glob
import json
import os
import re
import sys

ROOTS = sorted(glob.glob("/tmp/v*"))
#: 抽搐判据：容器初始化成 set（之后再用 append 就必炸）
TWITCH = re.compile(r"^\s*kept\s*=\s*set\(\)", re.M)
CLEAN = re.compile(r"^\s*kept\s*=\s*\[\]", re.M)
IMPORT_RE = re.compile(r"^\s*(import\s+re|from\s+re\s+import)", re.M)


def collect():
    """→ [(round, arm, task, score, answer, caliber_sig, suite_sha)]"""
    out = []
    for rj in sorted(glob.glob("/tmp/v*/**/runs.jsonl", recursive=True)):
        d = os.path.dirname(rj)
        arm = os.path.basename(d)
        rnd = os.path.basename(os.path.dirname(d))
        aj = os.path.join(d, "answers.jsonl")
        ans = collections.defaultdict(list)
        if os.path.exists(aj):
            for line in open(aj, encoding="utf-8"):
                line = line.strip()
                if not line:
                    continue
                a = json.loads(line)
                if a.get("kind") == "header":
                    continue
                ans[(a.get("run_id"), a.get("task_id"))].append(a.get("answer", ""))
        for line in open(rj, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            cal = r.get("caliber") or {}
            sig = r.get("caliber_sig") or cal.get("caliber_sig") or "?"
            sha = cal.get("suite_sha") or "?"
            for pt in (r.get("report") or {}).get("per_task", []):
                a = ans.get((r.get("run_id"), pt["id"]))
                out.append((rnd, arm, pt["id"], float(pt.get("score", 0.0)),
                            (a[-1] if a else None), sig, sha))
    return out


def main() -> int:
    rows = collect()
    if not rows:
        print("❌ 一个三元组都没读到 ⇒ 探针失效（不是「没有数据」）")
        return 2
    print("=" * 100)
    print("V32 探针 —— 可动面（e1wi/e1ni）是不是一颗骰子")
    print("=" * 100)
    print(f"读到 (轮,臂,题) 三元组 {len(rows)} 个，有答案原文 "
          f"{sum(1 for r in rows if r[4] is not None)} 个\n")

    e1 = [r for r in rows if r[2] in ("e1wi", "e1ni") and r[4] is not None]

    # ── 一、抽搐判据与分数的决定性
    print("## 一、`kept = set()`（抽搐）⇔ 失分，是不是决定关系")
    print()
    tab = collections.Counter()
    for rnd, arm, task, sc, a, sig, sha in e1:
        kind = ("抽搐" if TWITCH.search(a) else
                "干净list" if CLEAN.search(a) else
                "其它")
        tab[(task, kind, int(sc))] += 1
    for task in ("e1wi", "e1ni"):
        print(f"  {task}")
        kinds = sorted({k[1] for k in tab if k[0] == task})
        for kind in kinds:
            n0 = tab[(task, kind, 0)]
            n1 = tab[(task, kind, 1)]
            print(f"    {kind:9s} 0 分 {n0:4d} | 1 分 {n1:4d}")
        print()

    # ── 二、`import re` 缺失是不是第二个独立坑
    print("## 二、第二个坑：`_strip_tags` 用 re 却没 import re")
    print()
    for task in ("e1wi", "e1ni"):
        c = collections.Counter()
        for rnd, arm, t, sc, a, sig, sha in e1:
            if t != task:
                continue
            uses_re = "re.sub" in a
            has_imp = bool(IMPORT_RE.search(a))
            c[(uses_re, has_imp, int(sc))] += 1
        for k in sorted(c, key=str):
            print(f"  {task}  用re.sub={k[0]!s:5s} 有import={k[1]!s:5s} → score={k[2]}  n={c[k]}")
        print()

    # ── 三、逐轮逐臂：Σ 与抽搐方向
    print("## 三、逐轮逐臂：Σ 与 e1 两题的抽搐方向（同 suite 才并列）")
    print()
    suite_of = collections.defaultdict(set)
    for rnd, arm, task, sc, a, sig, sha in rows:
        suite_of[(rnd, arm)].add(sha)
    groups = collections.defaultdict(list)
    for rnd, arm, task, sc, a, sig, sha in rows:
        groups[(rnd, arm)].append((task, sc, a))
    print(f"  {'轮/臂':28s} {'suite':16s} {'Σ':>3s}  {'e1wi':>5s} {'e1ni':>5s}  抽搐(e1wi/e1ni)")
    for key in sorted(groups):
        items = groups[key]
        total = sum(round(sc * 16) for _, sc, _ in items)
        d = {t: (round(sc * 16), a) for t, sc, a in items}
        def tw(t):
            a = d.get(t, (None, None))[1]
            if not a:
                return "-"
            return "set" if TWITCH.search(a) else ("[]" if CLEAN.search(a) else "?")
        shas = ",".join(sorted(suite_of[key]))
        print(f"  {key[0]+'/'+key[1]:28s} {shas[:16]:16s} {total:3d}  "
              f"{d.get('e1wi',(None,))[0]!s:>5s} {d.get('e1ni',(None,))[0]!s:>5s}  "
              f"{tw('e1wi')}/{tw('e1ni')}")

    # ── 四、骰子的方差：同一条件（同 suite、同 scope 语义下无载荷）的臂之间
    print()
    print("## 四、抽搐率的分组统计（v8 六题；只看 e1 两题 = 每臂 2 个样本）")
    print()
    per_arm = collections.defaultdict(list)
    for rnd, arm, task, sc, a, sig, sha in e1:
        per_arm[(rnd, arm, sha)].append(TWITCH.search(a) is not None)
    rate = collections.Counter()
    for (rnd, arm, sha), bits in per_arm.items():
        rate[(sha, sum(bits), len(bits))] += 1
    for k in sorted(rate, key=str):
        print(f"  suite={k[0][:16]} 抽搐 {k[1]}/{k[2]} 的臂数 = {rate[k]}")
    print()
    print("VERDICT PROBE-OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
