#!/usr/bin/env python3
r"""骨架形态探针（第 109 轮 / tasks_v8）：模型拿到骨架后**补了没有 / 改了没有**（零模型）。

## 读数（每题按 `(题, sha)` 去重后一份唯一交付）

| 读数 | 定义 | 为什么它有用 |
|---|---|---|
| `import_lines` | 交付里 `^\s*(import|from)\s` 形态的行数 | `ni` 组里 >0 且与骨架不同 = **模型自己补了** |
| `added` | `import_lines − 骨架自带行数` | `ni` 组 `added > 0` ⇒ 模型自己补了 ⇒ 机会被消解 |
| `names` | `mw.verify.undefined_names(交付)` | 非空 = 机会在这份交付里**仍然存在** |
| `keep_ratio` | 骨架里「TODO 区块之外」的非空行，有多少条**逐字**出现在交付里 | 1.0 = 没重写；< 1 = 改了给定代码 |
| `lost` | `keep_ratio < 1` 时**具体丢了哪几行**（最多印 4 条） | 让"改了骨架"从比例变成一个**可指认的行** |

## 本轮新增（§78.9 的 V12）：`keep_ratio` 从**描述**变成**判据**

§78 的 `p1ni` 是这条判据存在的理由：它在**报名字的臂**上补对了 `import re`
（`added = +1`），却把骨架里 `_SEP = " \\t"` 那一行**整条丢了**（保留率 0.82）
⇒ `NameError: _SEP` ⇒ 0/16。**补对了缺陷那一处、同时改坏了另一处。**

⇒ 本探针不再只是"报一个数"，而是打一条判定：`⚠️ 动了骨架外的行`。
判据：`keep_ratio < 1.0`（严格小于；浮点比较用 `round(…, 4)` 后再比，防精度假阳性）。

## ⛔ 本探针只读冻结交付物与题面，不读 `oracle`

读答案键的探针才挂 `run_tests.sh` 的 7i（§75 的规矩）。这里的骨架来自
`gen_v8.skeleton_of(sp)` —— 那是**题面侧**的东西（题面里印的就是它），不是答案。

用法：
    python3 bench/evidence/v5_s109/probe_shape_v8.py --answers DIR/answers.jsonl
    python3 bench/evidence/v5_s109/probe_shape_v8.py --selftest
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
for p in (ROOT, ROOT / "bench" / "scorers", ROOT / "bench" / "specgen"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from mw.verify import undefined_names          # noqa: E402
from code_exec import extract_code            # noqa: E402

WI = ("e1wi", "e2wi", "e3wi")
NI = ("e1ni", "e2ni", "e3ni")
_IMP = re.compile(r"(?m)^\s*(?:import|from)\s")
_BEGIN = "# >>> BEGIN TODO"
_END = "# >>> END TODO"


def skeleton_of(tid: str) -> str:
    """从生成器拿该题的骨架（**只读题面侧**，不碰 oracle）。"""
    import gen_v8                                   # noqa: WPS433
    for sp in gen_v8.SPECS:
        if sp.id == tid:
            return gen_v8.skeleton_of(sp)
    return ""


def outside_lines(skeleton: str) -> list[str]:
    """骨架里 **TODO 区块之外** 的非空、非注释行（去首尾空白后比较）。"""
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


def lost_lines(skeleton: str, answer_code: str, limit: int = 4) -> list[str]:
    """骨架里**不再逐字出现**的行（给"动了骨架"一个可指认的对象）。"""
    have = {ln.strip() for ln in answer_code.splitlines()}
    return [w for w in outside_lines(skeleton) if w not in have][:limit]


def keep_ratio(skeleton: str, answer_code: str) -> float:
    want = outside_lines(skeleton)
    if not want:
        return 1.0
    have = {ln.strip() for ln in answer_code.splitlines()}
    hit = sum(1 for w in want if w in have)
    return hit / len(want)


def _compiles(code: str) -> bool:
    try:
        compile(code, "<ans>", "exec")
        return True
    except SyntaxError:
        return False


def probe_one(tid: str, answer: str) -> dict:
    code = extract_code(answer or "")
    names = undefined_names(code)
    got = sorted({nm for _fn, nm in (names or [])})
    skel = skeleton_of(tid)
    have_imp = len(_IMP.findall(code))
    skel_imp = len(_IMP.findall(skel))
    kr = round(keep_ratio(skel, code), 4) if skel else None
    return {
        "task": tid,
        "group": "wi" if tid in WI else "ni",
        "chars": len(code),
        "compiles": _compiles(code),
        "import_lines": have_imp,
        "skeleton_import_lines": skel_imp,
        "added": have_imp - skel_imp,
        "names": got,
        "names_undecidable": names is None,
        "keep_ratio": kr,
        "dirty": (kr is not None and kr < 1.0),        #: ⭐ V12 的判据本体
        "lost": lost_lines(skel, code) if kr is not None and kr < 1.0 else [],
        "outside_n": len(outside_lines(skel)) if skel else None,
    }


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
    order = {t: i for i, t in enumerate(WI + NI)}
    return sorted(seen.values(), key=lambda d: (order.get(d["task"], 99), d["sha"]))


def table(rows: list[dict]) -> str:
    out = [f"  {'题':6s} {'组':3s} {'sha':8s} {'字符':>5s} {'可编译':>6s} "
           f"{'import行':>8s} {'骨架自带':>8s} {'新增':>5s} {'缺名字':>10s} "
           f"{'保留率':>7s} {'动了骨架':>8s}",
           "  " + "-" * 104]
    for d in rows:
        kr = f"{d['keep_ratio']:.2f}" if d["keep_ratio"] is not None else "—"
        out.append(f"  {d['task']:6s} {d['group']:3s} {d['sha']:8s} {d['chars']:5d} "
                   f"{'✅' if d['compiles'] else '❌':>6s} {d['import_lines']:8d} "
                   f"{d['skeleton_import_lines']:8d} {d['added']:+5d} "
                   f"{(','.join(d['names']) or '—'):>10s} {kr:>7s} "
                   f"{'⚠️' if d['dirty'] else '·':>8s}")
    for d in rows:
        if d["lost"]:
            out.append(f"        └─ {d['task']}/{d['sha']} 丢掉骨架行：{d['lost']}")
    return "\n".join(out)


def summary(rows: list[dict]) -> str:
    lines = []
    for grp in ("wi", "ni"):
        sub = [d for d in rows if d["group"] == grp]
        if not sub:
            lines.append(f"  {grp}: 无交付")
            continue
        added = [d for d in sub if d["added"] > 0]
        withname = [d for d in sub if d["names"]]
        badsyn = [d for d in sub if not d["compiles"]]
        undec = [d for d in sub if d["names_undecidable"]]
        dirty = [d for d in sub if d["dirty"]]
        lines.append(f"  {grp}: {len(sub)} 份唯一交付 | **自己补了 import** {len(added)} | "
                     f"缺名字 {len(withname)} | 语法错 {len(badsyn)} | 不可判 {len(undec)}")
        kr = [d["keep_ratio"] for d in sub if d["keep_ratio"] is not None]
        if kr:
            lines.append(f"      骨架保留率：min={min(kr):.2f} "
                         f"mean={sum(kr) / len(kr):.2f} max={max(kr):.2f}")
        # ⭐ V12 判据（不是描述）：单独打一条
        lines.append(f"      ⭐ V12 `keep_ratio < 1.00`：动了骨架外的行的交付 "
                     f"**{len(dirty)}/{len(sub)}** 份"
                     + (f"  {[d['task'] for d in dirty]}" if dirty else ""))
        if dirty:
            for d in dirty:
                lines.append(f"         ⚠️ {d['task']}/{d['sha']} keep={d['keep_ratio']:.2f} "
                             f"丢掉 {d['lost']}")
        if grp == "ni":
            lines.append(f"      ⭐ 机会归零读数：{len(added)}/{len(sub)} 份交付自己补了 import"
                         f"（补了 ⇒ 这份交付里没有机会；没补 ⇒ 机会仍在）")
    return "\n".join(lines)


def selftest() -> int:
    """反证：假交付必须分别被判出「没补」「补了」「wi 自带」「重写了」「丢了某一行」。"""
    ni = skeleton_of("e1ni")
    wi = skeleton_of("e1wi")
    assert ni and wi, "❌ 拿不到骨架 —— 生成器路径不对"
    cases: list[tuple[str, str, dict]] = [
        ("S1 ni 原样骨架 ⇒ 新增 0、缺 re、保留率 1.0、没动骨架",
         "e1ni", ni, {"added": 0, "names": ["re"], "keep_ratio": 1.0, "dirty": False}),
        ("S2 ni + 顶部补 import ⇒ 新增 +1、不缺名字、仍没动骨架",
         "e1ni", "import re\n\n" + ni,
         {"added": 1, "names": [], "keep_ratio": 1.0, "dirty": False}),
        ("S3 wi 原样骨架 ⇒ 新增 0、不缺名字（骨架自带那一行）",
         "e1wi", wi, {"added": 0, "names": [], "keep_ratio": 1.0, "dirty": False}),
        ("S4 整个重写（与骨架几乎无关）⇒ 保留率明显 < 0.5",
         "e1ni", "def clean_lines(items, path):\n    open(path,'w').write('')\n",
         {"keep_ratio": 0.0}),
    ]
    bad = 0
    for why, tid, src, want in cases:
        got = probe_one(tid, f"```python\n{src}\n```")
        # S4 只要求 < 0.5（重写里总有几行与骨架逐字相同，不可能归零）
        if "keep_ratio" in want and want["keep_ratio"] == 0.0:
            ok = got["keep_ratio"] < 0.5 and got["dirty"] is True
            detail = {"keep_ratio": got["keep_ratio"], "dirty": got["dirty"]}
        else:
            ok = all(got.get(k) == v for k, v in want.items())
            detail = {k: got.get(k) for k, v in want.items()}
        print(f"  {'✅' if ok else '❌'} {why}  实测={detail}")
        bad += 0 if ok else 1

    # S5 反证 V12：删掉骨架一行 ⇒ 保留率必须 < 1 且 dirty 必须变 True
    victim = "def _strip_tags(text):"
    mutated = ni.replace(victim + "\n", "")
    r = probe_one("e1ni", mutated)
    ok = r["keep_ratio"] < 1.0 and r["dirty"] is True and victim in r["lost"]
    print(f"  {'✅' if ok else '❌'} S5 删掉骨架一行 ⇒ 保留率 <1 且 dirty=True 且 lost 里出现它"
          f"（实测 keep={r['keep_ratio']} dirty={r['dirty']} lost={r['lost']}）")
    bad += 0 if ok else 1

    # S6 ⚠️ 期望写错过一次，所以把这条性质钉在这里：
    #    骨架里用的是 `re.sub(...)`（属性访问），`from re import sub` 只绑定了 `sub`，
    #    **没有**绑定模块名 `re` ⇒ `undefined_names` 仍报 `re`（这本来就是对的）。
    #    ⇒ **绑成员 ≠ 绑模块名**，所以"补 import"是否有效不能只看 import 行数。
    r2 = probe_one("e1ni", "from re import sub\n\n" + ni)
    ok2 = r2["added"] == 1 and r2["names"] == ["re"]
    print(f"  {'✅' if ok2 else '❌'} S6 `from re import sub` 新增 1 行、但名字仍缺 re"
          f"（绑成员 ≠ 绑模块名）（实测 added={r2['added']} names={r2['names']}）")
    bad += 0 if ok2 else 1

    # S7 ⭐ 反证 V12 的判据：keep == 1.0 的交付必须判 dirty=False。
    #    少了这条，`dirty` 可能是恒真（比如实现写成 `kr is not None`）。
    clean = probe_one("e1ni", "import re\n\n" + ni)
    ok3 = clean["keep_ratio"] == 1.0 and clean["dirty"] is False
    print(f"  {'✅' if ok3 else '❌'} S7 ⭐ 反证：严格没动骨架的交付必须 dirty=False"
          f"（实测 keep={clean['keep_ratio']} dirty={clean['dirty']}）")
    bad += 0 if ok3 else 1

    # S8 ⭐ 补对名字 + 丢了一行骨架 = §78 的 p1ni 形态，必须同时被两个读数抓到
    p1ni_like = "import re\n\n" + ni.replace('    return re.sub(r"@[A-Za-z0-9]+", "", text)\n', "")
    r4 = probe_one("e1ni", p1ni_like)
    ok4 = r4["added"] == 1 and r4["names"] == [] and r4["dirty"] is True and r4["lost"]
    print(f"  {'✅' if ok4 else '❌'} S8 ⭐ p1ni 形态（补对名字 + 丢一行骨架）两个读数都要抓到"
          f"（实测 added={r4['added']} names={r4['names']} dirty={r4['dirty']}）")
    bad += 0 if ok4 else 1

    print(f"{'✅ 自证 8/8' if bad == 0 else f'❌ {bad} 条自证失败'}")
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
