"""V186 两臂：`typed`（V185 逐字重放，全量候选）/ `typed_filtered`（候选集按 arity 过滤）。

唯一变量：**system prompt 里的算子目录**。
  · 臂 `typed` 的 system = `arms_v184.SYS_TYPED`，逐字（import 复用）；
  · 臂 `typed_filtered` 的 system = 同一串，**只把目录那一段**换成 `catalogue(n_args)`。
    ⇒ 措辞、骨架、说明、user 消息全部逐字相同，⛔ 没有第二处改动。

⛔ 无重选（V185 已证重试不改变结构）；⛔ 不动算子库 / 记忆 / 模型。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent / "l3_s271"))
sys.path.insert(0, str(HERE.parent / "l3_s272"))
sys.path.insert(0, str(HERE.parent / "l3_s273"))
sys.path.insert(0, str(ROOT))

import substrate as S  # noqa: E402
import arms_v184 as A4  # noqa: E402  ← 首轮 prompt / parse / judge4 的唯一来源
from mw import critgen as CG  # noqa: E402
from mw import metacrit as MC  # noqa: E402
from mw.config import LLMConfig  # noqa: E402
from mw.llm import HTTPLLM, Message  # noqa: E402

EMPTY = {"built": False, "pass_oracle": False, "bite_partial": False,
         "bite_stub": False, "disc": False, "admit": False}


def sys_filtered(n_args: int) -> str:
    """把 `SYS_TYPED` 里的目录段换掉 —— 其余**逐字不动**。

    ⚠️ 用 `replace(全量目录, 过滤目录)` 而不是重写一遍模板：重写就会引入第二处差异，
       而那正是「两臂差在措辞」的经典假对照（V96 的老坑）。
    """
    full = CG.catalogue()
    return A4.SYS_TYPED.replace(full, CG.catalogue(n_args))


def _eval(t: dict, spec: dict, n: int) -> dict:
    fn = S.fn_name(t)
    kind = str(spec.get("kind") or "")
    types = spec.get("types") if isinstance(spec.get("types"), list) else []
    slots = {"decode": spec.get("decode") or "", "keep": spec.get("keep") or ""}
    d = CG.diagnose(kind, types, slots, n)
    out = dict(EMPTY)
    if not d["ok"]:
        out["fail"] = d["code"]
        out["err"] = d["msg"]
        out["kind"], out["types"] = kind, types
        return out
    allin = CG.inputs_for(kind, types)
    legal = CG.legal_indexes(t["oracle"], fn, allin)
    out["domain"] = [len(legal) if legal is not None else -1, len(allin)]
    if legal is None:
        out["fail"] = "PROBE_FAULT"
        out.update({"kind": kind, "types": types})
        return out
    if not legal:
        out["fail"] = "OUT_OF_DOMAIN"
        out.update({"kind": kind, "types": types})
        return out
    src = CG.build(kind, types, slots, fn, [allin[i] for i in legal])
    if not src:
        out["fail"] = "SHAPE"
        out.update({"kind": kind, "types": types})
        return out
    out["built"] = True
    out.update(A4.judge4(t, src))
    out["disc"] = out["pass_oracle"] and out["bite_partial"]
    out["admit"] = MC.admit(src)[0]
    out["src"] = src
    out["kind"], out["types"] = kind, types
    out["fail"] = "" if out["pass_oracle"] else "SEMANTIC"
    return out


def run(llm, t: dict, arm: str) -> dict:
    fn = S.fn_name(t)
    n = CG.arity(t["oracle"], fn)
    system = A4.SYS_TYPED if arm == "typed" else sys_filtered(n)
    txt = llm.complete([Message("system", system),
                        Message("user", A4.ASK_TYPED.format(goal=t["prompt"], fn=fn, n=n))],
                       max_tokens=700, temperature=0.0) or ""
    spec = A4.parse(txt)
    rec = {"arm": arm, "id": t["id"], "n_args": n,
           "ops": list(CG.ops_for(n)), "cand_n": len(CG.ops_for(n)),
           "raw": txt[:500]}
    if not spec:
        rec.update(EMPTY)
        rec["fail"] = "NO_JSON"
        rec["kind"] = None
        return rec
    rec.update(_eval(t, spec, n))
    return rec


def main() -> int:
    llm = HTTPLLM(LLMConfig())
    T = S.tasks()
    out: list[dict] = []
    for tid, t in T.items():
        ra = run(llm, t, "typed")
        rb = run(llm, t, "typed_filtered")
        out += [ra, rb]
        def _f(r):
            return (f"{r['kind']}/{'P' if r['pass_oracle'] else 'x'}"
                    f"{'B' if r['bite_partial'] else 'x'}") if r["built"] else f"×{r['fail']}"
        print(f"{tid:24s} n={ra['n_args']} cand={ra['cand_n']}  "
              f"typed={_f(ra):22s} filtered={_f(rb):22s}")

    n = len(T)
    print("\n== 汇总 ==")
    for arm in ("typed", "typed_filtered"):
        rows = [r for r in out if r["arm"] == arm]
        print(f"{arm:16s} 可跑 {sum(r['built'] for r in rows)}/{n} "
              f"过正确 {sum(r['pass_oracle'] for r in rows)}/{n} "
              f"咬错 {sum(r['bite_partial'] for r in rows)}/{n} "
              f"判别 {sum(r['disc'] for r in rows)}/{n} "
              f"准入 {sum(r['admit'] for r in rows)}/{n}")
        codes = {}
        for r in rows:
            codes[r["fail"] or "OK"] = codes.get(r["fail"] or "OK", 0) + 1
        print(f"{'':16s} 失败分类：{codes}")
        print(f"{'':16s} ARITY 计数 = {sum(1 for r in rows if r['fail'] == 'ARITY')}")

    print("\n== P6：其余 7 题的算子选择是否与 V185 相同 ==")
    prev = json.load(open(HERE.parent / "l3_s273" / "readings_v185_rep.json"))
    prev_kind = {(r["id"], r["arm"]): r.get("kind") for r in prev}
    changed = []
    for r in out:
        if r["arm"] != "typed_filtered":
            continue
        old = prev_kind.get((r["id"], "typed"))
        same = (old == r["kind"])
        if not same:
            changed.append((r["id"], old, r["kind"]))
        print(f"  {r['id']:24s} v185={str(old):18s} v186={str(r['kind']):18s} same={same}")
    print(f"  变化 = {changed}")

    print("\n== 自检：`built` 与 `fail` 必须互相说得通 ==")
    bad = [r for r in out if r["built"] != (r["fail"] in ("", "SEMANTIC"))]
    print(f"  矛盾行 = {len(bad)}（必须为 0）")
    if bad:
        raise SystemExit("built/fail 不一致：汇总口径已坏，读数不可用")

    dst = HERE / "readings_v186.json"
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"\n落盘 {dst}（{len(out)} 行）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
