"""V187 三臂：`A`（V186 逐字，**字符串冻结**）/ `B`（+新算子/新类型/骨架等长）/ `C`（B + 记忆）。

臂间**唯一**差别（PREREG_v187_v2 §四）：
  A ↔ B = 装置（算子库 6→8、类型 4→6、骨架 types 占位 1→n_args）
  B ↔ C = prompt 前有没有 `[语义记忆]`

⚠️ A 臂的 system **不许** import 活的 `CG.catalogue()` / `CG.skeleton()` ——
   V184/V186 那样写，本轮一改 `OPS`，重放臂就不再是重放（PREREG_v187.md §二）。
   快照来自 `git show a6ba0c2:mw/critgen.py`，生成脚本与结果都在 `v186_snapshot.json`。
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
import arms_v184 as A4  # noqa: E402  ← parse / judge4 / ASK_TYPED 的唯一来源
from mw import critgen as CG  # noqa: E402
from mw import metacrit as MC  # noqa: E402
from mw.config import LLMConfig  # noqa: E402
from mw.llm import HTTPLLM, Message  # noqa: E402

SEED = Path("/tmp/v184_seed/memH/memory.db")
SNAP = json.loads((HERE / "v186_snapshot.json").read_text(encoding="utf-8"))
SLOT_KEYS = ("decode", "keep", "sep", "dec", "neg")

#: A 臂：V186 的 system 模板**逐字**，目录与骨架用快照（⛔ 不用活函数）。
_TMPL_A = ("你是测试工程师。你只能选一个算子并填槽，判定逻辑由框架提供，你不许写。\n"
           "可用算子（选且只选一个）：\n{cat}"
           "\n\n类型只能是这四个：str / int / dict / list\n"
           "只输出这个 JSON（字段一个不少）：\n{skel}"
           "\n说明：`types` 与函数的必填参数**一一对应**（长度相等）。\n"
           "⛔ 不要写 inputs —— 输入由框架按 types 生成，你只填值。")

#: B/C 臂：同一模板，`{cat}`/`{skel}` 换成 V187 的（filtered 目录 + 等长骨架）。
_TMPL_B = ("你是测试工程师。你只能选一个算子并填槽，判定逻辑由框架提供，你不许写。\n"
           "可用算子（选且只选一个）：\n{cat}"
           "\n\n类型只能是这六个：" + " / ".join(CG.TYPES) + "\n"
           "只输出这个 JSON（字段一个不少）：\n{skel}"
           "\n说明：`types` 与函数的必填参数**一一对应**（长度相等，骨架里几个占位就填几个）。\n"
           "⛔ 不要写 inputs —— 输入由框架按 types 生成，你只填值。")


def sys_a(n: int) -> str:
    cat = SNAP["cat1"] if n == 1 else SNAP["cat2"] if n == 2 else SNAP["cat_all"]
    return _TMPL_A.format(cat=cat, skel=SNAP["skel"])


def sys_b(n: int) -> str:
    return _TMPL_B.format(cat=CG.catalogue(n), skel=CG.skeleton(n))


def _mem_block(store, prompt: str) -> str:
    """与部署同一渲染（`core.py`）：`[语义记忆] ` + ` ｜ ` 连接、每条 120 字。"""
    got = store.recall(prompt, k=6)
    return ("[语义记忆] " + " ｜ ".join(f.text[:120] for f in got)) if got else ""


def _slots(spec: dict) -> dict:
    return {k: str(spec.get(k) or "") for k in SLOT_KEYS}


def _eval(t: dict, spec: dict, n: int) -> dict:
    fn = S.fn_name(t)
    kind = str(spec.get("kind") or "")
    types = spec.get("types") if isinstance(spec.get("types"), list) else []
    slots = _slots(spec)
    d = CG.diagnose(kind, types, slots, n)
    out = {"built": False, "pass_oracle": False, "bite_partial": False,
           "bite_stub": False, "disc": False, "admit": False,
           "kind": kind, "types": types, "slots": {k: v for k, v in slots.items() if v}}
    if not d["ok"]:
        out["fail"] = d["code"]
        out["err"] = d["msg"]
        return out
    allin = CG.inputs_for(kind, types)
    legal = CG.legal_indexes(t["oracle"], fn, allin)
    out["domain"] = [len(legal) if legal is not None else -1, len(allin)]
    if legal is None:
        out["fail"] = "PROBE_FAULT"
        return out
    if not legal:
        out["fail"] = "OUT_OF_DOMAIN"
        return out
    src = CG.build(kind, types, slots, fn, [allin[i] for i in legal])
    if not src:
        out["fail"] = "SHAPE"
        return out
    out["built"] = True
    out.update(A4.judge4(t, src))
    out["disc"] = out["pass_oracle"] and out["bite_partial"]
    out["admit"] = MC.admit(src)[0]
    out["src"] = src
    out["fail"] = "" if out["pass_oracle"] else "SEMANTIC"
    return out


def run(llm, t: dict, arm: str, store=None) -> dict:
    fn = S.fn_name(t)
    n = CG.arity(t["oracle"], fn)
    mem = _mem_block(store, t["prompt"]) if (arm == "C" and store is not None) else ""
    goal = (mem + "\n\n" + t["prompt"]) if mem else t["prompt"]
    system = sys_a(n) if arm == "A" else sys_b(n)
    txt = llm.complete([Message("system", system),
                        Message("user", A4.ASK_TYPED.format(goal=goal, fn=fn, n=n))],
                       max_tokens=700, temperature=0.0) or ""
    spec = A4.parse(txt)
    rec = {"arm": arm, "id": t["id"], "n_args": n, "mem_chars": len(mem),
           "cand_n": len(CG.ops_for(n)), "raw": txt[:600]}
    if not spec:
        rec.update({"built": False, "pass_oracle": False, "bite_partial": False,
                    "bite_stub": False, "disc": False, "admit": False,
                    "fail": "NO_JSON", "kind": None, "types": [], "slots": {}})
        return rec
    rec.update(_eval(t, spec, n))
    return rec


def _f(r: dict) -> str:
    if r["built"]:
        return (f"{str(r.get('kind'))[:12]:12s}"
                f"{'P' if r['pass_oracle'] else 'x'}"
                f"{'B' if r['bite_partial'] else 'x'}"
                f"{'S' if r['bite_stub'] else 'x'}")
    return f"×{(r.get('fail') or '?')[:10]}"


def main() -> int:
    from mw.memory import MemoryStore
    store = MemoryStore(str(SEED)) if SEED.exists() else None
    if store is None:
        print("⚠️ 种子库不在 ⇒ C 臂会与 B 臂逐字相同（不是结论，是装置缺失）")
    llm = HTTPLLM(LLMConfig())
    T = S.tasks()
    out: list[dict] = []
    for tid, t in T.items():
        ra, rb = run(llm, t, "A"), run(llm, t, "B")
        rc = run(llm, t, "C", store)
        out += [ra, rb, rc]
        print(f"{tid:24s} n={ra['n_args']} cand={ra['cand_n']}  "
              f"A={_f(ra):18s} B={_f(rb):18s} C={_f(rc):18s} mem={rc['mem_chars']}")

    n = len(T)
    print("\n== 汇总 ==")
    for arm in ("A", "B", "C"):
        rows = [r for r in out if r["arm"] == arm]
        codes: dict[str, int] = {}
        for r in rows:
            codes[r.get("fail") or "OK"] = codes.get(r.get("fail") or "OK", 0) + 1
        print(f"{arm}  可跑 {sum(r['built'] for r in rows)}/{n} "
              f"过正确 {sum(r['pass_oracle'] for r in rows)}/{n} "
              f"咬错 {sum(r['bite_partial'] for r in rows)}/{n} "
              f"咬桩 {sum(r['bite_stub'] for r in rows)}/{n} "
              f"判别 {sum(r['disc'] for r in rows)}/{n} "
              f"准入 {sum(r['admit'] for r in rows)}/{n}  分类={codes}")

    print("\n== 新算子的采用（B/C 臂）==")
    for r in out:
        if r["arm"] in ("B", "C") and r.get("kind") in ("NUMFMT", "INVARIANT"):
            print(f"  {r['arm']} {r['id']:24s} {r['kind']:10s} types={r['types']} "
                  f"slots={r.get('slots')} fail={r.get('fail') or '-'}")

    print("\n== P5/P7：g7d3 的三槽（记忆值 = space / 2 / paren）==")
    memwant = {"sep": "space", "dec": "2", "neg": "paren"}
    for arm in ("A", "B", "C"):
        r = next((x for x in out if x["arm"] == arm and x["id"] == "g7d3_fmt_amount"), None)
        if r:
            got = r.get("slots") or {}
            hit = all(got.get(k) == v for k, v in memwant.items())
            print(f"  {arm}: kind={r.get('kind')} slots={got} 命中记忆值={hit} "
                  f"mem_chars={r['mem_chars']}")

    print("\n== P1/P2：g7d3 的 types 长度与首错 ==")
    for arm in ("A", "B", "C"):
        r = next((x for x in out if x["arm"] == arm and x["id"] == "g7d3_fmt_amount"), None)
        if r:
            print(f"  {arm}: types={r['types']} len={len(r['types'])} "
                  f"fail={r.get('fail') or '-'} code={r.get('err', '')[:60]}")

    print("\n== P4/P8：逐题 pass 变化（A→B→C）==")
    for tid in T:
        vs = {arm: next((x for x in out if x["arm"] == arm and x["id"] == tid), None)
              for arm in ("A", "B", "C")}
        p = {k: bool(v and v["pass_oracle"]) for k, v in vs.items()}
        kd = {k: (v or {}).get("kind") for k, v in vs.items()}
        flag = "" if p["A"] == p["B"] == p["C"] else "  ← 变化"
        print(f"  {tid:24s} pass A/B/C={int(p['A'])}{int(p['B'])}{int(p['C'])}  "
              f"kind A/B/C={kd['A']}/{kd['B']}/{kd['C']}{flag}")

    print("\n== I1/I2 不变量 ==")
    g = next(x for x in out if x["id"] == "g7e1_parse_flag" and x["arm"] == "B")
    print(f"  I1 g7e1 B 臂：built={g['built']} pass={g['pass_oracle']} "
          f"bite_partial={g['bite_partial']} ⇒ 期望 built→pass=T ∧ bite=F")
    bad = [r for r in out if r["pass_oracle"] and r["built"] and not r["bite_stub"]
           and r.get("kind") in ("NUMFMT", "INVARIANT")]
    print(f"  I2 新算子过 oracle 却不咬桩的行 = {len(bad)}（必须 0）："
          f"{[(r['arm'], r['id']) for r in bad]}")

    print("\n== 自检：built 与 fail 必须互相说得通 ==")
    mix = [r for r in out if r["built"] != (r.get("fail") in ("", "SEMANTIC"))]
    print(f"  矛盾行 = {len(mix)}（必须为 0）")
    if mix:
        raise SystemExit("built/fail 不一致：汇总口径已坏，读数不可用")

    (HERE / "readings_v187.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"\n落盘 {HERE / 'readings_v187.json'}（{len(out)} 行）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
