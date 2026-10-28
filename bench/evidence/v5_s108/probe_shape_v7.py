#!/usr/bin/env python3
r"""骨架形态探针（第 108 轮 §78）：模型拿到骨架后**补了没有 / 改了没有**（零模型）。

## 三个读数（每题一份唯一交付）

| 读数 | 定义 | 为什么它有用 |
|---|---|---|
| `import_lines` | 交付里 `^\s*(import|from)\s` 形态的行数 | `ni` 组里 >0 且与骨架不同 = **模型自己补了** |
| `names` | `mw.verify.undefined_names(交付)` | 非空 = 机会在这份交付里**仍然存在** |
| `keep_ratio` | 骨架里「TODO 区块之外」的非空行，有多少条**逐字**出现在交付里 | 1.0 = 没重写；< 1 = 改了给定代码 |

⭐ 本轮的**新读数** `added`：交付里的 import 行数 **减去** 骨架里本来就有的行数。
- `wi` 组骨架自带 1 行 ⇒ `added > 0` 意味着模型**又加了一条**（不是本轮关心的）；
- `ni` 组骨架 0 行 ⇒ `added > 0` 就意味着**模型自己把那行补上了** ⇒ 机会被模型消解。

⇒ `ni` 组 `added > 0` 的比例，就是"机会归零"的直接读数（预注册 §6.1 / N-a）。

⛔ 本探针**只读冻结交付物与题面**，不读 `oracle`（§76 的规矩：读答案键的探针才挂 7i）。

用法：
    python3 bench/evidence/v5_s108/probe_shape_v7.py --answers DIR/answers.jsonl
    python3 bench/evidence/v5_s108/probe_shape_v7.py --selftest
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

WI = ("p1wi", "p2wi", "p3wi")
NI = ("p1ni", "p2ni", "p3ni")
_IMP = re.compile(r"(?m)^\s*(?:import|from)\s")
_BEGIN = "# >>> BEGIN TODO"
_END = "# >>> END TODO"


def skeleton_of(tid: str) -> str:
    """从生成器拿该题的骨架（**只读题面侧**，不碰 oracle）。"""
    import gen_v7                                   # noqa: WPS433
    for sp in gen_v7.SPECS:
        if sp.id == tid:
            return gen_v7.skeleton_of(sp)
    return ""


def outside_lines(skeleton: str) -> list[str]:
    """骨架里 **TODO 区块之外** 的非空行（去首尾空白后比较）。"""
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
        "keep_ratio": round(keep_ratio(skel, code), 4) if skel else None,
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
           f"{'import行':>8s} {'骨架自带':>8s} {'新增':>5s} {'缺名字':>12s} {'保留率':>7s}",
           "  " + "-" * 90]
    for d in rows:
        kr = f"{d['keep_ratio']:.2f}" if d["keep_ratio"] is not None else "—"
        out.append(f"  {d['task']:6s} {d['group']:3s} {d['sha']:8s} {d['chars']:5d} "
                   f"{'✅' if d['compiles'] else '❌':>6s} {d['import_lines']:8d} "
                   f"{d['skeleton_import_lines']:8d} {d['added']:+5d} "
                   f"{(','.join(d['names']) or '—'):>12s} {kr:>7s}")
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
        lines.append(f"  {grp}: {len(sub)} 份唯一交付 | **自己补了 import** {len(added)} | "
                     f"缺名字 {len(withname)} | 语法错 {len(badsyn)} | 不可判 {len(undec)}")
        kr = [d["keep_ratio"] for d in sub if d["keep_ratio"] is not None]
        if kr:
            lines.append(f"      骨架保留率：min={min(kr):.2f} "
                         f"mean={sum(kr) / len(kr):.2f} max={max(kr):.2f}")
        if grp == "ni":
            lines.append(f"      ⭐ 机会归零读数：{len(added)}/{len(sub)} 份交付自己补了 import"
                         f"（补了 ⇒ 这份交付里没有机会；没补 ⇒ 机会仍在）")
    return "\n".join(lines)


def selftest() -> int:
    """反证：四份假交付，探针必须分别判出「没补」「补了」「wi 自带」「重写了」。"""
    ni = skeleton_of("p1ni")
    wi = skeleton_of("p1wi")
    assert ni and wi, "❌ 拿不到骨架 —— 生成器路径不对"
    cases: list[tuple[str, str, dict]] = [
        ("S1 ni 原样骨架 ⇒ 新增 0、缺 re、保留率 1.0",
         "p1ni", ni, {"added": 0, "names": ["re"], "keep_ratio": 1.0}),
        ("S2 ni + 顶部补 import ⇒ 新增 +1、不缺名字",
         "p1ni", "import re\n\n" + ni, {"added": 1, "names": [], "keep_ratio": 1.0}),
        ("S3 wi 原样骨架 ⇒ 新增 0、不缺名字（骨架自带那一行）",
         "p1wi", wi, {"added": 0, "names": [], "keep_ratio": 1.0}),
        ("S4 整个重写（与骨架几乎无关）⇒ 保留率明显 < 0.5",
         "p1ni", "def tag_report(items, path):\n    open(path,'w').write('')\n",
         {"keep_ratio": 0.0}),
    ]
    bad = 0
    for why, tid, src, want in cases:
        got = probe_one(tid, f"```python\n{src}\n```")
        # S4 只要求 < 0.5（重写里总有几行与骨架逐字相同，不可能归零）
        if "keep_ratio" in want and want["keep_ratio"] == 0.0:
            ok = got["keep_ratio"] < 0.5
            detail = {"keep_ratio": got["keep_ratio"]}
        else:
            ok = all(got.get(k) == v for k, v in want.items())
            detail = {k: got.get(k) for k, v in want.items()}
        print(f"  {'✅' if ok else '❌'} {why}  实测={detail}")
        bad += 0 if ok else 1

    # 反证：删掉骨架一行 ⇒ 保留率必须 < 1（证明这个比例不是恒 1）
    mutated = ni.replace('    return "".join(f"{i}.{s}\\n" for i, s in enumerate(rows, 1))\n', "")
    r = probe_one("p1ni", mutated)
    ok = r["keep_ratio"] < 1.0
    print(f"  {'✅' if ok else '❌'} S5 删掉骨架一行 ⇒ 保留率必须 < 1（实测 {r['keep_ratio']}）")
    bad += 0 if ok else 1

    # ⚠️ S6 第一版期望写成 `names == []` —— **写错了**，而且错得有意思：
    #    骨架里用的是 `re.sub(...)`（属性访问），`from re import sub` 只绑定了 `sub`，
    #    **没有**绑定模块名 `re` ⇒ `undefined_names` 仍报 `re`（这本来就是对的）。
    #    ⇒ 期望改成"新增 1 行、但名字仍缺"，并把这条性质钉在这里：
    #    **绑成员 ≠ 绑模块名**，所以"补 import"是否有效不能只看 import 行数。
    r2 = probe_one("p1ni", "from re import sub\n\n" + ni)
    ok2 = r2["added"] == 1 and r2["names"] == ["re"]
    print(f"  {'✅' if ok2 else '❌'} S6 `from re import sub` 新增 1 行、但名字仍缺 re"
          f"（绑成员 ≠ 绑模块名）（实测 added={r2['added']} names={r2['names']}）")
    bad += 0 if ok2 else 1

    print(f"{'✅ 自证 6/6' if bad == 0 else f'❌ {bad} 条自证失败'}")
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
