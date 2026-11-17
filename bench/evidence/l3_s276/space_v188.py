"""V188 装置空间矩阵：**不含模型**，只问「(算子 × 类型组合) 这格是证据还是噪声」。

    python3 bench/evidence/l3_s276/space_v188.py

为什么要这一张（PREREG §五 P4）：
  P1/P2 量的是「模型这 24 格撞上了几次」—— 那是**命中率**，分母是模型的输出。
  这张矩阵量的是**空间里有多少格天生就是坏的** —— 分母是 (算子 × 类型组合)。
  两个分母必须分开报（纪律 4/67）：前者说「模型踩了几次」，后者说「这块地有多烂」。

枚举口径（写进 RESULTS 的分母名里）：
  · 二元题（`k01` / `g7e2`）：`EMPTY_IFF_EQUAL` × 36 个类型组合（6×6）、
    `BOUND` × 6、`TAIL` × 6 × 3 个 keep 槽
  · 一元题（另 6 题）：`ROUNDTRIP@json.loads` / `CONSERVE` / `SUBSEQ` / `INVARIANT` × 6 个类型
  · ⛔ 排除 `NUMFMT`：它的 24 个槽组合已由 `AZ150a` 单列；混进来会让分母变成
    「槽组合数」而不是「类型组合数」。排除量在输出里写清。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent / "l3_s271"))
sys.path.insert(0, str(HERE.parent / "l3_s272"))
sys.path.insert(0, str(ROOT))

import substrate as S                                    # noqa: E402
import arms_v184 as A4                                   # noqa: E402
from mw import critgen as NEW                            # noqa: E402


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


OLD = _load("critgen_v187", HERE / "_critgen_v187.py")


def cell(t: dict, dev, kind: str, types: list, slots: dict) -> dict:
    fn = S.fn_name(t)
    n = len(types)
    out = {"built": False, "fail": "", "pass_oracle": None,
           "bite_stub": None, "const": None, "cls": "SKIP"}
    d = dev.diagnose(kind, types, slots, n)
    if not d["ok"]:
        out["fail"] = d["code"]
        out["msg"] = d["msg"][:80]
        return out
    allin = dev.inputs_for(kind, types)
    legal = dev.legal_indexes(t["oracle"], fn, allin)
    if legal is None:
        out["fail"] = "PROBE_FAULT"
        return out
    if not legal:
        out["fail"] = "OUT_OF_DOMAIN"
        return out
    use = [allin[i] for i in legal]
    src = dev.build(kind, types, slots, fn, use)
    if not src:
        out["fail"] = "SHAPE"
        return out
    out["built"] = True
    ok = S.judge(t["oracle"] + "\n\n" + src, ["assert _rel()"]) == ""
    stub = S.judge(S.stub(t) + "\n\n" + src, ["assert _rel()"]) != ""
    out.update({"pass_oracle": ok, "bite_stub": stub,
                "const": dev.constant_on(t["oracle"], fn, use)
                if hasattr(dev, "constant_on") else None})
    out["cls"] = ("INFORMATIVE" if ok and stub else
                  "VACUOUS" if ok else
                  "MIXED" if stub else "HARMFUL")
    return out


def main() -> int:
    tasks = S.tasks()
    rows = []
    for tid, t in tasks.items():
        n = NEW.arity(t["oracle"], S.fn_name(t))
        if n == 2:
            for a in NEW.TYPES:
                for b in NEW.TYPES:
                    rows.append((t, "EMPTY_IFF_EQUAL", [a, b], {}))
            for a in NEW.TYPES:
                rows.append((t, "BOUND", [a, "int"], {}))
                for k in [x for x in NEW.KEEPS if x]:
                    rows.append((t, "TAIL", [a, "int"], {"keep": k}))
        elif n == 1:
            for a in NEW.TYPES:
                rows.append((t, "ROUNDTRIP", [a], {"decode": "json.loads"}))
                rows.append((t, "CONSERVE", [a], {}))
                rows.append((t, "SUBSEQ", [a], {}))
                rows.append((t, "INVARIANT", [a], {}))
    out = []
    for t, kind, types, slots in rows:
        o = cell(t, OLD, kind, types, slots)
        nn = cell(t, NEW, kind, types, slots)
        out.append({"id": t["id"], "op": kind, "types": types, "slots": slots,
                    "old": o, "new": nn})
    (HERE / "space_v188.json").write_text(
        json.dumps({"n": len(out), "cells": out}, ensure_ascii=False, indent=1),
        encoding="utf-8")

    # ── 汇总 ────────────────────────────────────────────────────────────
    def tab(which: str, op: str | None = None, same_type: bool | None = None) -> dict:
        cnt: dict[str, int] = {}
        for r in out:
            if op and r["op"] != op:
                continue
            if same_type is not None:
                if r["op"] != "EMPTY_IFF_EQUAL":
                    continue
                if (r["types"][0] == r["types"][1]) != same_type:
                    continue
            cnt[r[which]["cls"]] = cnt.get(r[which]["cls"], 0) + 1
        return cnt

    print(f"空间格数 = {len(out)}（排除 `NUMFMT` 的 24 槽组合，理由见文件头）\n")
    print(f"{'切片':<34}{'INFORMATIVE':>12}{'VACUOUS':>10}{'MIXED':>8}{'HARMFUL':>9}{'SKIP':>6}")
    for label, kw in [
        ("全部（旧装置 / 新装置）", None),
        ("EMPTY_IFF 全 36 组合", dict(op="EMPTY_IFF_EQUAL")),
        ("EMPTY_IFF 异类型（旧）", dict(op="EMPTY_IFF_EQUAL", same_type=False)),
        ("EMPTY_IFF 同类型（旧）", dict(op="EMPTY_IFF_EQUAL", same_type=True)),
        ("非 EMPTY_IFF（旧）", dict(op="__OTHER__")),
        ("非 EMPTY_IFF（新）", dict(op="__OTHER__")),
    ]:
        which = "new" if "新" in label else "old"
        if kw is None:
            c = tab(which)
            print(f"{label:<34}{c.get('INFORMATIVE',0):>12}{c.get('VACUOUS',0):>10}"
                  f"{c.get('MIXED',0):>8}{c.get('HARMFUL',0):>9}{c.get('SKIP',0):>6}")
            continue
        if kw.get("op") == "__OTHER__":
            c = {}
            for r in out:
                if r["op"] == "EMPTY_IFF_EQUAL":
                    continue
                c[r[which]["cls"]] = c.get(r[which]["cls"], 0) + 1
        else:
            c = tab(which, **kw)
        print(f"{label:<34}{c.get('INFORMATIVE',0):>12}{c.get('VACUOUS',0):>10}"
              f"{c.get('MIXED',0):>8}{c.get('HARMFUL',0):>9}{c.get('SKIP',0):>6}")

    # 逐算子
    print()
    ops = sorted({r["op"] for r in out})
    print(f"{'算子':<18}{'旧 VACUOUS':>11}{'新 VACUOUS':>11}{'旧 MIXED':>9}{'新 MIXED':>9}"
          f"{'拒绝':>6}{'const':>7}")
    for op in ops:
        rs = [r for r in out if r["op"] == op]
        ov = sum(1 for r in rs if r["old"]["cls"] == "VACUOUS")
        nv = sum(1 for r in rs if r["new"]["cls"] == "VACUOUS")
        om = sum(1 for r in rs if r["old"]["cls"] == "MIXED")
        nm = sum(1 for r in rs if r["new"]["cls"] == "MIXED")
        rej = sum(1 for r in rs if r["new"]["cls"] == "SKIP" and r["old"]["cls"] != "SKIP")
        cst = sum(1 for r in rs if r["new"].get("const") is True)
        print(f"{op:<18}{ov:>11}{nv:>11}{om:>9}{nm:>9}{rej:>6}{cst:>7}")

    # 关键切片：异类型 EMPTY_IFF 的证据强度
    # ⚠️ 分母只能是**可建格**（`cls != SKIP`）：`legal_indexes()` 会把越界组合剔掉，
    #    拿 6×6 当分母就把「装置根本没判」算成了「判了但通过」（纪律 67 的教科书形态）。
    diff = [r for r in out if r["op"] == "EMPTY_IFF_EQUAL" and r["types"][0] != r["types"][1]]
    same = [r for r in out if r["op"] == "EMPTY_IFF_EQUAL" and r["types"][0] == r["types"][1]]

    def _stat(rs: list, which: str) -> str:
        built = [r for r in rs if r[which]["cls"] != "SKIP"]
        c: dict[str, int] = {}
        for r in built:
            c[r[which]["cls"]] = c.get(r[which]["cls"], 0) + 1
        return (f"可建 {len(built)}/{len(rs)}：INFORMATIVE {c.get('INFORMATIVE', 0)} / "
                f"VACUOUS {c.get('VACUOUS', 0)} / MIXED {c.get('MIXED', 0)} / "
                f"HARMFUL {c.get('HARMFUL', 0)}")

    print(f"\nEMPTY_IFF 异类型（旧）: {_stat(diff, 'old')}")
    print(f"EMPTY_IFF 同类型（旧）: {_stat(same, 'old')}")
    print(f"EMPTY_IFF 异类型（新）: {_stat(diff, 'new')}")
    print(f"新装置以 TYPES 拒掉的异类型格: "
          f"{sum(1 for r in diff if r['new'].get('fail')=='TYPES')}/{len(diff)}")
    print("\n⚠️ 新装置**没有**清掉的那一族（旧=新，仍 VACUOUS）：")
    for r in out:
        if r["new"]["cls"] == "VACUOUS":
            print(f"   {r['id']:<20}{r['op']:<12}{r['types']}  slots={r['slots']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
