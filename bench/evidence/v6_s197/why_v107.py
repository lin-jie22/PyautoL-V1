#!/usr/bin/env python3
"""V108 补：V107 那 56 个跨题候选**到底怎么坏的**（用 code_exec 的 `why` 字段分解失败原因）。

为什么要补
----------
V107（§175.4）我写的机制是「名字要么不存在（NameError），要么指向别的东西」。
但 §1 的 R2 判据被我写坏了：**改名不影响 `compile()`**（未定义的名字语法上完全合法），
所以「编译通过率是否上升」根本测不到「名字是不是主因」。

正确测法：直接看 `code_exec` 的 `why` 里是什么异常。
⛔ 这是对 V107 机制段的可证伪检验，不是新结论。
"""
from __future__ import annotations

import json
import pathlib
import sys
import collections

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import rename_transfer as R  # noqa: E402


def main() -> None:
    import subprocess
    task = R.load_task()
    pt = {"id": task["id"], "prompt": task.get("prompt", ""), "tests": task["tests"]}

    def run(code: str) -> dict:
        payload = json.dumps({"task": pt, "answer": code}, ensure_ascii=False)
        p = subprocess.run([sys.executable, str(R.SCORER)], input=payload,
                           capture_output=True, text=True, timeout=120)
        if not p.stdout.strip():
            return {"score": None, "why": "no-output"}
        return json.loads(p.stdout.strip().splitlines()[-1])

    pool = R.harvest()
    other = {k: v for k, v in pool.items() if not k.startswith(R.TASK + "::")}
    target = next(v for k, v in pool.items() if k == f"{R.TASK}::075b4099")
    tlines = target.splitlines(keepends=True)

    # 复刻 V107 的跨题候选（**不改名**，整行原样搬）
    donor_lines: dict[str, list] = {}
    for k, v in other.items():
        tid = k.split("::")[0]
        for i, l in enumerate(v.splitlines()):
            donor_lines.setdefault(l, []).append(f"{tid}:L{i}")
    combos = []
    for j, tl in enumerate(tlines):
        for dl, srcs in donor_lines.items():
            if dl + "\n" == tl:
                continue
            c = "".join(tlines[:j] + [dl + "\n"] + tlines[j + 1:])
            try:
                compile(c, "<m>", "exec")
            except SyntaxError:
                continue
            combos.append((f"L{j}<-{srcs[0]}", c))
    print(f"## §5 V107 的跨题候选（可编译）{len(combos)} 个，逐个取 `why`")

    kinds = collections.Counter()
    rows = []
    for lab, c in combos:
        r = run(c)
        why = str(r.get("why", ""))
        if "NameError" in why:
            k = "NameError（名字不存在）"
        elif "AssertionError" in why:
            k = "AssertionError（算错）"
        elif "TypeError" in why:
            k = "TypeError（类型不对）"
        elif "AttributeError" in why:
            k = "AttributeError"
        elif "ValueError" in why:
            k = "ValueError"
        elif r.get("score") == 0.0:
            k = "其他异常"
        else:
            k = "非零分/无异常"
        kinds[k] += 1
        rows.append((lab, r.get("score"), k, why[:70]))

    print(f"  {'候选':28s} {'分':>5s}  原因")
    for lab, s, k, why in rows:
        print(f"  {lab:28s} {str(s):>5s}  {k}")
    print("\n  ⇒ 失败原因分布：")
    for k, v in kinds.most_common():
        print(f"     {k}: {v} ({v/len(rows):.0%})")
    ne = kinds.get("NameError（名字不存在）", 0)
    print(f"\n  ⭐ 「名字不存在」占 {ne}/{len(rows)} = {ne/len(rows):.0%}"
          + ("　⇒ V107 §175.4 的机制（名字是主因）**成立**"
             if ne / len(rows) >= 0.5 else
             "　⇒ ⛔ **名字不是主因**，V107 §175.4 的机制段要改写"))

    (R.EV / "v6_s197" / "why_v107.json").write_text(
        json.dumps({"n": len(rows), "kinds": dict(kinds),
                    "rows": [{"lab": l, "score": s, "kind": k, "why": w} for l, s, k, w in rows]},
                   ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
