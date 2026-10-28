#!/usr/bin/env python3
"""骨架形态探针（第 107 轮 §77）：模型拿到骨架后**有没有自己补 import / 有没有重写**。

## 它回答的问题（预注册 Q1 的直接读数，零模型）

§76 的结论是「缺名字只出现在整份答案一行 import 都没有的交付物里」。
本轮把缺 import 做成了**题面的给定状态**，于是问题变成它的反面：

> 模型拿到一份**没有 import 的骨架**时，会不会自己补上？
> 会 ⇒ 构造失效（§76.8 判负条件）；不会 ⇒ 机会真的被题面控制住了。

## 三个读数（每题一份）

| 读数 | 定义 | 为什么它有用 |
|---|---|---|
| `import_lines` | 交付里 `^(import|from)` 形态的行数 | 0 = 模型没补；>0 = 补了 |
| `names` | `mw.verify.undefined_names(交付)` | 非空 = 机会真的存在 |
| `keep_ratio` | 骨架里「TODO 区块之外」的非空行，有多少条**逐字**出现在交付里 | 1.0 = 没重写；< 1 = 改了给定代码 |

`keep_ratio` 是「改动半径」的直接代理：骨架题面的约束是"区块之外逐字保留"，
所以这个比例就是**模型违约束的程度**。它不判分，只作描述。

⛔ 本探针**只读冻结交付物与题面**，不读 `oracle`（§76 的规矩：读答案键的探针才挂 7i）。

用法：
    python3 bench/evidence/v4_s107/probe_skeleton_edit.py --answers DIR/answers.jsonl
    python3 bench/evidence/v4_s107/probe_skeleton_edit.py --selftest
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench" / "scorers"))
sys.path.insert(0, str(ROOT / "bench" / "specgen"))

from mw.verify import undefined_names          # noqa: E402
from code_exec import extract_code            # noqa: E402

BF = ("p1bf", "p2bf", "p3bf")
SK = ("p1sk", "p2sk", "p3sk")
_IMP = re.compile(r"(?m)^\s*(?:import|from)\s")
_BEGIN = "# >>> BEGIN TODO"
_END = "# >>> END TODO"


def skeleton_of(tid: str) -> str:
    """从生成器拿该题的骨架（**只读题面侧**，不碰 oracle）。"""
    from gen_v6 import SPECS       # noqa: WPS433
    for sp in SPECS:
        if sp.id == tid:
            return sp.skeleton
    return ""


def outside_lines(skeleton: str) -> list[str]:
    """骨架里 **TODO 区块之外** 的非空行（去首尾空白后比较）。

    ⚠️ 空行与纯注释行不计：模型重排空行是常事，把它算成"违反约束"会让这个比例
       变成噪音而不是信号。TODO 区块**内**的注释当然也不算（那正是让改的地方）。
    """
    out: list[str] = []
    inside = False
    for line in skeleton.splitlines():
        s = line.strip()
        if _BEGIN in s:
            inside = True
            continue
        if _END in s:
            inside = False
            continue
        if inside or not s or s.startswith("#"):
            continue
        out.append(s)
    return out


def keep_ratio(skeleton: str, answer_code: str) -> float:
    want = outside_lines(skeleton)
    if not want:
        return 1.0
    have = {ln.strip() for ln in answer_code.splitlines()}
    hit = sum(1 for w in want if w in have)
    return hit / len(want)


def probe_one(tid: str, answer: str) -> dict:
    code = extract_code(answer or "")
    names = undefined_names(code)
    got = sorted({nm for _fn, nm in (names or [])})
    skel = skeleton_of(tid) if tid in SK else ""
    return {
        "task": tid,
        "group": "sk" if tid in SK else "bf",
        "chars": len(code),
        "compiles": _compiles(code),
        "import_lines": len(_IMP.findall(code)),
        "names": got,
        "names_undecidable": names is None,
        "keep_ratio": round(keep_ratio(skel, code), 4) if skel else None,
        "outside_n": len(outside_lines(skel)) if skel else None,
    }


def _compiles(code: str) -> bool:
    try:
        compile(code, "<ans>", "exec")
        return True
    except SyntaxError:
        return False


def collect(answers_path: Path) -> list[dict]:
    """按 `(题, sha256)` 去重 —— 重复交付不是独立样本（§76.6.4）。"""
    rows = [json.loads(l) for l in answers_path.read_text().splitlines() if l.strip()]
    rows = [r for r in rows if r.get("kind") == "answer"]
    seen: dict[tuple[str, str], dict] = {}
    for r in rows:
        tid = r.get("task_id")
        sha = (r.get("sha256") or "")[:8]
        if (tid, sha) in seen:
            continue
        seen[(tid, sha)] = {**probe_one(tid, r.get("answer") or ""), "sha": sha}
    order = {t: i for i, t in enumerate(BF + SK)}
    return sorted(seen.values(), key=lambda d: (order.get(d["task"], 99), d["sha"]))


def table(rows: list[dict]) -> str:
    out = [f"  {'题':6s} {'组':3s} {'sha':8s} {'字符':>5s} {'可编译':>6s} "
           f"{'import行':>8s} {'缺名字':>16s} {'保留率':>7s}",
           "  " + "-" * 74]
    for d in rows:
        kr = f"{d['keep_ratio']:.2f}" if d["keep_ratio"] is not None else "—"
        out.append(f"  {d['task']:6s} {d['group']:3s} {d['sha']:8s} {d['chars']:5d} "
                   f"{'✅' if d['compiles'] else '❌':>6s} {d['import_lines']:8d} "
                   f"{(','.join(d['names']) or '—'):>16s} {kr:>7s}")
    return "\n".join(out)


def summary(rows: list[dict]) -> str:
    lines = []
    for grp in ("bf", "sk"):
        sub = [d for d in rows if d["group"] == grp]
        if not sub:
            lines.append(f"  {grp}: 无交付")
            continue
        withimp = [d for d in sub if d["import_lines"] > 0]
        withname = [d for d in sub if d["names"]]
        badsyn = [d for d in sub if not d["compiles"]]
        undec = [d for d in sub if d["names_undecidable"]]
        lines.append(f"  {grp}: {len(sub)} 份唯一交付 | 有 import 行 {len(withimp)} | "
                     f"缺名字 {len(withname)} | 语法错 {len(badsyn)} | 不可判 {len(undec)}")
        if grp == "sk":
            kr = [d["keep_ratio"] for d in sub if d["keep_ratio"] is not None]
            if kr:
                lines.append(f"      骨架保留率：min={min(kr):.2f} "
                             f"mean={sum(kr) / len(kr):.2f} max={max(kr):.2f}")
    return "\n".join(lines)


def selftest() -> int:
    """反证：构三份假交付，探针必须分别判出「补了 import」「没补」「重写了」。"""
    skel = skeleton_of("p1sk")
    assert skel, "❌ 拿不到 p1sk 骨架 —— 生成器路径不对"
    fixed = "import os\nimport re\n" + skel.replace(
        "    # >>> BEGIN TODO\n    # 在这里补全：遍历 items，按规则 1~4 生成 seen（处理后的字符串列表）。\n"
        "    # 上面的 _TAG 是一个编译好的正则，可以直接用。\n    # >>> END TODO\n",
        "    for raw in items:\n        t = _TAG.sub('', raw)\n        seen.append(t)\n")
    cases: list[tuple[str, str, dict]] = [
        ("S1 原样骨架（没补 import）⇒ import 行 0、缺 os 和 re、保留率 1.0",
         "p1sk", skel,
         {"import_lines": 0, "names": ["os", "re"], "keep_ratio": 1.0}),
        ("S2 骨架 + 补上 import ⇒ import 行 2、不缺名字",
         "p1sk", fixed,
         {"import_lines": 2, "names": [], "keep_ratio": 1.0}),
        ("S4 bf 题不产保留率（没有骨架可比）",
         "p1bf", "import os\ndef tag_report(items, path):\n    return None\n",
         {"keep_ratio": None}),
    ]
    bad = 0
    for why, tid, src, want in cases:
        got = probe_one(tid, f"```python\n{src}\n```")
        ok = all(got.get(k) == v for k, v in want.items())
        detail = {k: got.get(k) for k in want}
        print(f"  {'✅' if ok else '❌'} {why}  实测={detail}")
        bad += 0 if ok else 1

    # ⚠️ S3 第一版把期望写成 `keep_ratio == 0.0` —— **写错了**。
    #    重写的答案里 `def tag_report(items, path):` / `if folder:` / `return None`
    #    这几行本来就与骨架**逐字相同**，所以保留率不可能归零（实测 0.2）。
    #    ⇒ 期望改成"明显 < 0.5"，并把这个数写进报告：**保留率不是"改没改"的二值函数**。
    r3 = probe_one("p1sk",
                   "```python\nimport os\ndef tag_report(items, path):\n"
                   "    return None\n```")
    ok3 = r3["keep_ratio"] < 0.5
    print(f"  {'✅' if ok3 else '❌'} S3' 整个重写 ⇒ 保留率 < 0.5（实测 {r3['keep_ratio']}）")
    bad += 0 if ok3 else 1
    # 反证「删掉一行骨架就会降保留率」：证明这个比例不是恒 1
    mutated = skel.replace("        os.makedirs(folder, exist_ok=True)\n", "")
    r = probe_one("p1sk", mutated)
    ok = r["keep_ratio"] < 1.0
    print(f"  {'✅' if ok else '❌'} S5 删掉骨架一行 ⇒ 保留率必须 < 1（实测 "
          f"{r['keep_ratio']}）")
    bad += 0 if ok else 1
    print(f"{'✅ 自证 5/5' if bad == 0 else f'❌ {bad} 条自证失败'}")
    return 0 if bad == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--answers")
    ap.add_argument("--json")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.answers:
        print("缺 --answers（或 --selftest）", file=sys.stderr)
        return 2
    rows = collect(Path(a.answers))
    print("[骨架形态] 按 (题, sha) 去重后的唯一交付")
    print(table(rows))
    print("\n[汇总]")
    print(summary(rows))
    if a.json:
        Path(a.json).write_text(json.dumps(rows, ensure_ascii=False, indent=1),
                                encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
