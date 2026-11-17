"""V189 L1/L2：装置空间里「诊断信号」的分布与收窄范围。⛔ 不跑模型。

## L1 要回答什么

「诊断能对多少格说话」—— 这是 L2→L5 通电的**上限**，与模型无关。
枚举：8 题 × `ops_for(n)` 的每个算子 × 该 arity 的可行签名。
槽值取**每个槽表的第一个非空值**（⚠️ 抽样，不是全枚举 —— `NUMFMT` 有 36 组）。

## L2 要回答什么

同一个信号，`scope="one"` 与 `scope="class"` 删掉的算子集合是否不同。
若恒同 ⇒ 诊断码的分类**没有行为后果**（V168 同型：一数替不了两谓词）。

## ⚠️ 分母口径（纪律 67）

- `cells`：枚举出来的格（含结构不可达的）。
- `reachable`：`legal_indexes()` 至少放行一组的格。
- 「信号占比」的分母是 **reachable**，不是 `cells` —— 结构不可达的格本来就没有「判据好不好」这回事。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent / "l3_s271"))
sys.path.insert(0, str(HERE.parent / "l3_s272"))
sys.path.insert(0, str(ROOT))

import substrate as S  # noqa: E402
import arms_v184 as A4  # noqa: E402
from mw import critgen as CG  # noqa: E402

TYPES = list(CG.TYPES)


def type_combos(kind: str, n: int) -> list[list[str]]:
    """该算子在这个 arity 下的**可行签名**（只按算子自己的结构约束，不看实现）。"""
    if n == 1:
        return [[t] for t in TYPES]
    if kind in ("TAIL", "BOUND"):
        return [[t, "int"] for t in TYPES]
    if kind == "EMPTY_IFF_EQUAL":
        return [[t, t] for t in TYPES]      # V188：两输入必须同类型
    return [[a, b] for a in TYPES for b in TYPES]


def first_slots(kind: str) -> dict:
    """每个槽取表里第一个非空值。⚠️ 抽样口径，写进读数里。"""
    out = {}
    for key in CG.OPS[kind]["slots"]:
        vals = [v for v in CG.SLOT_TABLES[key] if v]
        out[key] = vals[0] if vals else ""
    return out


def measure(t: dict, kind: str, types: list, n: int) -> dict:
    fn = S.fn_name(t)
    oracle = t["oracle"]
    slots = first_slots(kind)
    row = {"id": t["id"], "kind": kind, "types": types, "n_args": n, "slots": slots}

    d = CG.diagnose(kind, types, slots, n)
    if not d["ok"]:
        row.update({"sig": "STRUCT:TYPES" if d["code"] == "TYPES" else "STRUCT:OTHER",
                    "struct": d["code"], "reach": False, "const": None,
                    "pass_oracle": None, "bite_stub": None, "bite_partial": None,
                    "cand_before": len(CG.ops_for(n))})
        row["narrow_one"], row["narrow_class"] = _narrow_pair(
            row["sig"], n, kind, types, oracle, fn)
        return row

    ins = CG.inputs_for(kind, types)
    legal = CG.legal_indexes(oracle, fn, ins)
    if legal is None:
        row.update({"sig": "PROBE_FAULT", "struct": "OK", "reach": None, "const": None,
                    "pass_oracle": None, "bite_stub": None, "bite_partial": None,
                    "cand_before": len(CG.ops_for(n))})
        row["narrow_one"], row["narrow_class"] = _narrow_pair(
            row["sig"], n, kind, types, oracle, fn)
        return row
    if not legal:
        row.update({"sig": "STRUCT:TYPES", "struct": "OK", "reach": False, "const": None,
                    "pass_oracle": None, "bite_stub": None, "bite_partial": None,
                    "cand_before": len(CG.ops_for(n))})
        row["narrow_one"], row["narrow_class"] = _narrow_pair(
            row["sig"], n, kind, types, oracle, fn)
        return row

    use = [ins[i] for i in legal]
    const = CG.constant_on(oracle, fn, use)
    src = CG.build(kind, types, slots, fn, use)
    if not src:
        row.update({"sig": "STRUCT:OTHER", "struct": "SHAPE_BUILD", "reach": True,
                    "const": const, "pass_oracle": None, "bite_stub": None,
                    "bite_partial": None, "cand_before": len(CG.ops_for(n))})
        row["narrow_one"], row["narrow_class"] = _narrow_pair(
            row["sig"], n, kind, types, oracle, fn)
        return row

    j = A4.judge4(t, src)
    sig = CG.read_signal(built=True, fail="", pass_oracle=j["pass_oracle"],
                         bite_stub=j["bite_stub"], bite_partial=j["bite_partial"],
                         oracle_const=const)
    row.update({"sig": sig, "struct": "OK", "reach": True, "const": const,
                "pass_oracle": j["pass_oracle"], "bite_stub": j["bite_stub"],
                "bite_partial": j["bite_partial"], "why": str(j.get("why_oracle"))[:90],
                "cand_before": len(CG.ops_for(n))})
    row["narrow_one"], row["narrow_class"] = _narrow_pair(
        sig, n, kind, types, oracle, fn)
    return row


def _narrow_pair(sig, n, kind, types, oracle, fn) -> tuple[dict, dict]:
    """同信号下两臂各自删了谁。`chosen` 就是这一格自己的算子。"""
    o, wo = CG.narrow(sig, n, kind, types, oracle, fn, scope="one")
    c, wc = CG.narrow(sig, n, kind, types, oracle, fn, scope="class")
    return ({"n": len(o), "removed": wo["removed"], "reason": wo["reason"]},
            {"n": len(c), "removed": wc["removed"], "reason": wc["reason"]})


def main() -> int:
    T = S.tasks()
    rows: list[dict] = []
    for tid, t in T.items():
        n = CG.arity(t["oracle"], S.fn_name(t))
        for kind in CG.ops_for(n):
            for types in type_combos(kind, n):
                r = measure(t, kind, types, n)
                rows.append(r)
                print(f"{tid:24s} {kind:16s} {str(types):16s} {r['sig']:14s} "
                      f"one={r['narrow_one']['n']} class={r['narrow_class']['n']} "
                      f"{r['narrow_class']['reason']}", flush=True)

    dst = HERE / "space_v189.json"
    dst.write_text(json.dumps({"rows": rows,
                               "note": "槽值=每个槽表的第一个非空值（抽样，非全枚举）"},
                              ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"\n落盘 {dst}（{len(rows)} 格）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
