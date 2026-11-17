"""V185 两臂：`typed`（V184 逐字重放）/ `typed_retry`（+ 结构化回错 + 一次重选）。

臂间单变量：**首次失败后有没有一次带结构事实的重选机会**。

⛔ 首轮 prompt 逐字来自 `arms_v184.SYS_TYPED` / `ASK_TYPED`（import 复用，不复制）——
   复制一份就会「两份常量各自漂移」，那是最难查的假对照（纪律 65 同型）。
⛔ 不动算子库、不动记忆、不动模型。
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
import arms_v184 as A4  # noqa: E402  ← 首轮 prompt / parse / judge4 的唯一来源
from mw import critgen as CG  # noqa: E402
from mw import metacrit as MC  # noqa: E402
from mw.config import LLMConfig  # noqa: E402
from mw.llm import HTTPLLM, Message  # noqa: E402

EMPTY = {"built": False, "pass_oracle": False, "bite_partial": False,
         "bite_stub": False, "disc": False, "admit": False}


def _spec_of(txt: str) -> dict | None:
    return A4.parse(txt)


def _first(txt: str, n: int) -> dict:
    """首轮/次轮共用的诊断：先看有没有 JSON，再问 `diagnose()`。"""
    spec = _spec_of(txt)
    if not spec:
        return {"ok": False, "code": "NO_JSON", "kind": None, "types": None,
                "need": None, "got": n, "msg": "输出里没有 JSON"}
    kind = str(spec.get("kind") or "")
    types = spec.get("types") if isinstance(spec.get("types"), list) else []
    slots = {"decode": spec.get("decode") or "", "keep": spec.get("keep") or ""}
    d = CG.diagnose(kind, types, slots, n)
    d["types"] = types
    d["slots"] = slots
    d["spec"] = spec
    return d


def _eval(t: dict, d: dict) -> dict:
    """诊断通过后：圈域 → 渲染 → 四格判分。失败一律折成 EMPTY + fail 名。"""
    fn = S.fn_name(t)
    kind, types = d["kind"], d["types"]
    allin = CG.inputs_for(kind, types)
    legal = CG.legal_indexes(t["oracle"], fn, allin)
    out = dict(EMPTY)
    out["domain"] = [len(legal) if legal is not None else -1, len(allin)]
    if legal is None:
        out["fail"] = "PROBE_FAULT"
        return out
    if not legal:
        out["fail"] = "OUT_OF_DOMAIN"
        return out
    src = CG.build(kind, types, d["slots"], fn, [allin[i] for i in legal])
    if not src:
        out["fail"] = "SHAPE"       # diagnose 过了但 build 拒绝 ⇒ 两层的口径不一致（自检点）
        return out
    out["built"] = True             # ⚠️ V185 自踩 ①：初版漏了这一行 ⇒ 汇总打出「可跑 0/8」而
    #                                 同一行又写「过正确 2/8」—— 两个字段自相矛盾却没人报警。
    #                                 ⇒ 末尾加 `built` 与 `fail` 的一致性自检。
    out.update(A4.judge4(t, src))
    out["disc"] = out["pass_oracle"] and out["bite_partial"]
    out["admit"] = MC.admit(src)[0]
    out["src"] = src
    out["fail"] = "" if out["pass_oracle"] else "SEMANTIC"
    return out


def run(llm, t: dict, arm: str, retry: bool) -> dict:
    fn = S.fn_name(t)
    n = CG.arity(t["oracle"], fn)
    msgs = [Message("system", A4.SYS_TYPED),
            Message("user", A4.ASK_TYPED.format(goal=t["prompt"], fn=fn, n=n))]
    txt = llm.complete(msgs, max_tokens=700, temperature=0.0) or ""

    d1 = _first(txt, n)
    rec = {"arm": arm, "id": t["id"], "n_args": n,
           "a1": {k: d1.get(k) for k in ("ok", "code", "kind", "types")},
           "retry_used": False, "a2": None, "raw1": txt[:500]}
    d = d1
    if not d1["ok"] and retry:
        msgs = msgs + [Message("assistant", txt),
                       Message("user", CG.retry_hint(d1))]
        txt2 = llm.complete(msgs, max_tokens=700, temperature=0.0) or ""
        d2 = _first(txt2, n)
        rec["retry_used"] = True
        rec["a2"] = {k: d2.get(k) for k in ("ok", "code", "kind", "types")}
        rec["raw2"] = txt2[:500]
        rec["hint"] = CG.retry_hint(d1)[:400]
        d = d2

    rec["kind"] = d.get("kind")
    rec["types"] = d.get("types")
    if not d["ok"]:
        rec.update(EMPTY)
        rec["fail"] = d["code"]
        rec["err"] = d.get("msg", "")
        return rec
    rec.update(_eval(t, d))
    return rec


def main() -> int:
    llm = HTTPLLM(LLMConfig())
    T = S.tasks()
    out: list[dict] = []
    for tid, t in T.items():
        ra = run(llm, t, "typed", retry=False)
        rb = run(llm, t, "typed_retry", retry=True)
        out += [ra, rb]
        def _f(r):
            return (f"{r['kind']}/{'P' if r['pass_oracle'] else 'x'}"
                    f"{'B' if r['bite_partial'] else 'x'}") if r["built"] else f"×{r['fail']}"
        print(f"{tid:24s} typed={_f(ra):22s} retry={_f(rb):22s} "
              f"a1={ra['a1']['code']:8s} retry_used={int(rb['retry_used'])} "
              f"-> {rb['a2']['code'] if rb['a2'] else '-'}")

    n = len(T)
    print("\n== 汇总 ==")
    for arm in ("typed", "typed_retry"):
        rows = [r for r in out if r["arm"] == arm]
        print(f"{arm:12s} 可跑 {sum(r['built'] for r in rows)}/{n} "
              f"过正确 {sum(r['pass_oracle'] for r in rows)}/{n} "
              f"咬错 {sum(r['bite_partial'] for r in rows)}/{n} "
              f"判别 {sum(r['disc'] for r in rows)}/{n} "
              f"准入 {sum(r['admit'] for r in rows)}/{n}")
        codes = {}
        for r in rows:
            codes[r["a1"]["code"]] = codes.get(r["a1"]["code"], 0) + 1
        print(f"{'':12s} 首轮 code 分布：{codes}")

    print("\n== P2：retry_used 的题集 是否 == 首轮非 OK 的题集 ==")
    need = {r["id"] for r in out if r["arm"] == "typed_retry" and r["a1"]["code"] != "OK"}
    used = {r["id"] for r in out if r["arm"] == "typed_retry" and r["retry_used"]}
    print(f"  首轮非 OK = {sorted(need)}")
    print(f"  retry_used = {sorted(used)}")
    print(f"  集合相等 = {need == used}")

    print("\n== P5：未重选的 7 题，两臂逐题三元组是否相同 ==")
    same_all = True
    for r in out:
        if r["arm"] != "typed_retry" or r["retry_used"]:
            continue
        a = next(x for x in out if x["arm"] == "typed" and x["id"] == r["id"])
        same = (a["kind"], a["pass_oracle"], a["bite_partial"]) == \
               (r["kind"], r["pass_oracle"], r["bite_partial"])
        same_all &= same
        print(f"  {r['id']:24s} same={same} "
              f"({a['kind']} {a['pass_oracle']} {a['bite_partial']})")
    print(f"  全同 = {same_all}")

    print("\n== P4：过正确题集逐题 ==")
    for tid in T:
        a = next(x for x in out if x["arm"] == "typed" and x["id"] == tid)
        b = next(x for x in out if x["arm"] == "typed_retry" and x["id"] == tid)
        if a["pass_oracle"] or b["pass_oracle"]:
            print(f"  {tid:24s} typed={a['pass_oracle']} retry={b['pass_oracle']} "
                  f"kind={a['kind']}->{b['kind']}")

    print("\n== 自检：`built` 与 `fail` 必须互相说得通 ==")
    bad = [r for r in out if r["built"] != (r["fail"] in ("", "SEMANTIC"))]
    for r in bad:
        print(f"  ✗ {r['arm']} {r['id']} built={r['built']} fail={r['fail']!r}")
    print(f"  矛盾行 = {len(bad)}（必须为 0；V185 自踩 ① 就是这里曾经 16/16 全错）")
    if bad:
        raise SystemExit("built/fail 不一致：汇总口径已坏，读数不可用")

    tag = sys.argv[1] if len(sys.argv) > 1 else ""
    dst = HERE / f"readings_v185{tag}.json"
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"\n落盘 {dst}（{len(out)} 行）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
