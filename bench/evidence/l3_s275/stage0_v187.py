"""V187 STAGE0：**先量分母与平坦区，再押注**（纪律 51 / 87）。

三件事，全部是**装置**的性质，⛔ 一个模型调用都不发：

  ① 可达性矩阵：8 题 × arity 相符的算子，量「能渲染 / 过 oracle / 咬 partial / 咬 stub」
  ② `NUMFMT` 的逐槽平坦性：在 `g7d3` 上枚举 sep×dec×neg，看**有多少组合能过 oracle** ——
     若不止一个，槽就不唯一，「槽选对了」不能当判别力
  ③ 池覆盖面：把 `POOL["num"]` 换成小值池（V184 的 `int` 池），看 `partial` 还能不能被咬 ——
     这是「池是判据的一部分」的直接证据

⛔ 本脚本不读 `tests` 字段（隐藏判分例）；⛔ 不从 oracle 反推槽值。
   槽值来自**惯例**（`bench/build_g7d_seed.py::FACTS`），下面 `MEM_SLOTS` 是它的逐字映射。
"""
from __future__ import annotations

import itertools
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent / "l3_s271"))
sys.path.insert(0, str(ROOT))

import substrate as S  # noqa: E402
from mw import critgen as CG  # noqa: E402

#: 惯例 → 槽值的**逐字映射**（事实源：`bench/build_g7d_seed.py::FACTS`）。
#: 左侧是惯例原文的关键短语，右侧是槽值。⛔ 这里没有任何信息来自 oracle。
MEM_SLOTS: dict[str, dict] = {
    # 「千分位用空格分隔，保留两位小数，负数用圆括号包裹、不写负号」
    "g7d3_fmt_amount": {"sep": "space", "dec": "2", "neg": "paren"},
}

#: 探针类型：只为量可行性，⛔ 不是给模型的提示（V184 STAGE0 同型）。
def probe_types(t: dict, n: int) -> list:
    tid = t["id"]
    if tid == "g7d3_fmt_amount":
        return ["num"]
    if tid == "g7e1_parse_flag":
        return ["flag"]
    if tid == "g7e2_truncate_tail":
        return ["str", "int"]
    p = t["prompt"]
    if n == 2:
        return ["str", "int"] if "截" in p or "长度" in p else ["dict", "dict"]
    for key, word in (("dict", "dict"), ("list", "列表"), ("str", "字符串")):
        if word in p:
            return [key]
    return ["str"]


def _slots_for(tid: str, kind: str) -> dict:
    """槽值：记忆有的用记忆；没有的填**每个槽的第一项**（探针值，非答案）。"""
    mem = MEM_SLOTS.get(tid) or {}
    spec = CG.OPS[kind]
    out = {}
    for key in spec["slots"]:
        if key in mem:
            out[key] = mem[key]
        else:
            table = CG.SLOT_TABLES[key]
            out[key] = next((k for k in table if k), "")
    return out


def _real_arity_gate(t: dict, kind: str, n: int) -> dict:
    """单格：可渲染 / 过 oracle / 咬 partial / 咬 stub。"""
    fn = S.fn_name(t)
    types = probe_types(t, n)
    slots = _slots_for(t["id"], kind)
    d = CG.diagnose(kind, types, slots, n)
    row = {"id": t["id"], "kind": kind, "types": types, "slots": slots,
           "code": d["code"], "built": False,
           "pass_oracle": False, "bite_partial": False, "bite_stub": False}
    if not d["ok"]:
        return row
    allin = CG.inputs_for(kind, types)
    legal = CG.legal_indexes(t["oracle"], fn, allin)
    row["domain"] = [len(legal) if legal is not None else -1, len(allin)]
    if not legal:
        row["code"] = "OUT_OF_DOMAIN"
        return row
    src = CG.build(kind, types, slots, fn, [allin[i] for i in legal])
    if not src:
        row["code"] = "SHAPE"
        return row
    row["built"] = True
    row["pass_oracle"] = S.judge(t["oracle"] + "\n\n" + src, ["assert _rel()"]) == ""
    row["bite_partial"] = S.judge(t["partial"] + "\n\n" + src, ["assert _rel()"]) != ""
    row["bite_stub"] = S.judge(S.stub(t) + "\n\n" + src, ["assert _rel()"]) != ""
    row["disc"] = row["pass_oracle"] and row["bite_partial"]
    row["src"] = src
    return row


def matrix(T: dict) -> list[dict]:
    out = []
    for tid, t in T.items():
        n = CG.arity(t["oracle"], S.fn_name(t))
        for kind in CG.ops_for(n):
            out.append(_real_arity_gate(t, kind, n))
    return out


def flatness(t: dict) -> list[dict]:
    """`NUMFMT` 在 `g7d3` 上逐槽枚举 —— 问「有几个组合能过 oracle」。"""
    fn = S.fn_name(t)
    types = ["num"]
    allin = CG.inputs_for("NUMFMT", types)
    legal = CG.legal_indexes(t["oracle"], fn, allin)
    rows = []
    for sep, dec, neg in itertools.product(
            [k for k in CG.SEPS if k], [k for k in CG.DECS if k], [k for k in CG.NEGS if k]):
        slots = {"sep": sep, "dec": dec, "neg": neg}
        src = CG.build("NUMFMT", types, slots, fn, [allin[i] for i in legal])
        if not src:
            rows.append({**slots, "built": False})
            continue
        po = S.judge(t["oracle"] + "\n\n" + src, ["assert _rel()"]) == ""
        bp = S.judge(t["partial"] + "\n\n" + src, ["assert _rel()"]) != ""
        bs = S.judge(S.stub(t) + "\n\n" + src, ["assert _rel()"]) != ""
        rows.append({**slots, "built": True, "pass_oracle": po,
                     "bite_partial": bp, "bite_stub": bs, "disc": po and bp})
    return rows


def pool_cover(t: dict) -> list[dict]:
    """池覆盖面：同一个判据，只换**输入域**，看 partial 还能不能被咬。"""
    fn = S.fn_name(t)
    saved = CG.POOL["num"]
    rows = []
    for name, pool in (("num（V187）", saved), ("int（V184 旧池）", CG.POOL["int"]),
                       ("只小值", (0, 3, 5, 7)), ("只大值", (1234.5, -1234.5))):
        CG.POOL["num"] = pool
        allin = CG.inputs_for("NUMFMT", ["num"])
        legal = CG.legal_indexes(t["oracle"], fn, allin)
        slots = MEM_SLOTS[t["id"]]
        src = CG.build("NUMFMT", ["num"], slots, fn, [allin[i] for i in legal]) if legal else ""
        po = S.judge(t["oracle"] + "\n\n" + src, ["assert _rel()"]) == "" if src else False
        bp = S.judge(t["partial"] + "\n\n" + src, ["assert _rel()"]) != "" if src else False
        rows.append({"pool": name, "n": len(pool), "legal": len(legal or []),
                     "pass_oracle": po, "bite_partial": bp, "disc": po and bp})
    CG.POOL["num"] = saved
    return rows


def main() -> int:
    T = S.tasks()
    print("=" * 78)
    print("① 可达性矩阵（8 题 × arity 相符的算子；槽值：记忆优先，否则探针值）")
    print("=" * 78)
    rows = matrix(T)
    print(f"{'题':24s} {'算子':16s} {'types':14s} {'域':7s} {'oracle':7s} {'partial':8s} {'stub':6s} {'disc':5s}")
    for r in rows:
        dom = f"{r['domain'][0]}/{r['domain'][1]}" if "domain" in r else "-"
        print(f"{r['id']:24s} {r['kind']:16s} {str(r['types'])[:14]:14s} {dom:7s} "
              f"{'P' if r['pass_oracle'] else ('x' if r['built'] else r['code']):7s} "
              f"{'B' if r['bite_partial'] else '-':8s} "
              f"{'S' if r['bite_stub'] else '-':6s} {'D' if r.get('disc') else '-':5s}")

    print("\n" + "=" * 78)
    print("② NUMFMT 逐槽平坦性（g7d3）：**几个组合能过 oracle**")
    print("=" * 78)
    fl = flatness(T["g7d3_fmt_amount"])
    ok = [r for r in fl if r.get("pass_oracle")]
    print(f"  组合总数 {len(fl)}，过 oracle **{len(ok)}** 个 ⇒ {[(r['sep'], r['dec'], r['neg']) for r in ok]}")
    print(f"  其中咬 partial 的 {sum(1 for r in ok if r['bite_partial'])} 个")
    print(f"  ⚠️ 若 >1 ⇒ 槽不唯一，「槽选对」不能单独当判别力")
    for r in fl:
        if r.get("built") and r["pass_oracle"]:
            print(f"    ✓ {r['sep']!r:9s} dec={r['dec']} neg={r['neg']!r:8s} "
                  f"bite_partial={r['bite_partial']} disc={r['disc']}")

    print("\n" + "=" * 78)
    print("③ 池覆盖面（同一个判据，只换输入域）")
    print("=" * 78)
    for r in pool_cover(T["g7d3_fmt_amount"]):
        print(f"  {r['pool']:16s} 池长={r['n']:2d} 圈域={r['legal']:2d} "
              f"过 oracle={'P' if r['pass_oracle'] else 'x'} "
              f"咬 partial={'B' if r['bite_partial'] else '-'} disc={'D' if r['disc'] else '-'}")

    print("\n" + "=" * 78)
    print("④ 分母（⛔ 不许事后改）")
    print("=" * 78)
    per_task: dict[str, dict] = {}
    for r in rows:
        cur = per_task.setdefault(r["id"], {"built": 0, "pass": 0, "disc": 0, "kinds": []})
        cur["kinds"].append(r["kind"])
        cur["built"] += bool(r["built"])
        cur["pass"] += bool(r["pass_oracle"])
        cur["disc"] += bool(r.get("disc"))
    n8 = len(per_task)
    reach = [k for k, v in per_task.items() if v["built"]]
    okk = [k for k, v in per_task.items() if v["pass"]]
    dis = [k for k, v in per_task.items() if v["disc"]]
    for tid, v in per_task.items():
        print(f"  {tid:24s} 算子 {len(v['kinds']):2d} 个  可渲染 {v['built']:2d}  "
              f"过正确 {v['pass']:2d}  判别 {v['disc']:2d}")
    print(f"\n  题级：可渲染 {len(reach)}/{n8}  过正确 {len(okk)}/{n8}  判别 {len(dis)}/{n8}")
    print(f"  缺算子的题（一格都过不了 oracle）：{[k for k in per_task if not per_task[k]['pass']]}")
    (HERE / "stage0_v187.json").write_text(
        json.dumps({"matrix": rows, "flatness": fl,
                    "pool": pool_cover(T["g7d3_fmt_amount"])},
                   ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"\n落盘 {HERE / 'stage0_v187.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
