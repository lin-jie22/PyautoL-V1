"""V189 三臂：A（1 轮）/ B（≤3 轮，`scope="one"`）/ C（≤3 轮，`scope="class"`）。

## 唯一变量

round 1 三臂的 system **逐字相同**（`arms_v187.sys_b(n)`）。
round k≥2 的 system 只把 `{cat}`/`{skel}` 换成**收窄后**的（`catalogue(allow=...)` /
`skeleton(allow=...)`），措辞逐字不动。

B 与 C 的差别**只有一个参数**：`CG.narrow(..., scope=)`。⛔ 不是两套代码（纪律 65）。

## 硬拒

模型写了不在当前候选集里的算子 ⇒ `OUT_OF_CANDIDATE`，⛔ 不计 pass。
候选集空 ⇒ `EXHAUSTED`，从 pass 分母剔出（PREREG §2.2 / `F2`）。

## 为什么 A 也要跑

`I1`：A 的读数必须与 **V187 B 臂**逐格相同（同 `sys_b(n)`、同 8 题、无记忆）。
它对不上 ⇒ 装置焊错了，三臂读数全部作废。
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
sys.path.insert(0, str(HERE.parent / "l3_s274"))
sys.path.insert(0, str(HERE.parent / "l3_s275"))
sys.path.insert(0, str(ROOT))

import substrate as S  # noqa: E402
import arms_v184 as A4  # noqa: E402
import arms_v187 as A7  # noqa: E402  ← _TMPL_B / _eval / sys_b / parse 的唯一来源
from mw import critgen as CG  # noqa: E402
from mw.config import LLMConfig  # noqa: E402
from mw.llm import HTTPLLM, Message  # noqa: E402

MAX_R = 3
EMPTY = {"built": False, "pass_oracle": False, "bite_stub": False,
         "bite_partial": False, "disc": False, "admit": False, "oracle_const": None}


def sys_narrowed(n: int, allow: dict) -> str:
    """`_TMPL_B` 逐字，只换目录与骨架 —— 两者都按收窄后的集合渲染。

    ⚠️ 文件头已说明：A 臂 ⛔ 不用活函数（V187 的教训），但 B/C 臂收窄后**必须**用
       活函数，因为收窄结果就是这一步的产物。这两个臂的读数是「收窄生效时」的读数，
       ⛔ 不是「与 V187 比」的读数（那是 A 臂的职责）。
    """
    return A7._TMPL_B.format(cat=CG.catalogue(allow=allow),
                             skel=CG.skeleton(n, allow=allow))


def _eval9(t: dict, spec: dict, n: int) -> dict:
    """V187 的 `_eval` 逐字 + 补 `oracle_const` + 折出 `signal`。"""
    r = A7._eval(t, spec, n)
    if r.get("built"):
        fn = S.fn_name(t)
        ins = CG.inputs_for(r["kind"], r["types"])
        legal = CG.legal_indexes(t["oracle"], fn, ins)
        use = [ins[i] for i in legal] if legal else []
        r["oracle_const"] = CG.constant_on(t["oracle"], fn, use) if use else None
    else:
        r["oracle_const"] = None
    r["signal"] = CG.read_signal(
        built=bool(r.get("built")), fail=r.get("fail") or "",
        pass_oracle=bool(r.get("pass_oracle")), bite_stub=bool(r.get("bite_stub")),
        bite_partial=bool(r.get("bite_partial")), oracle_const=r.get("oracle_const"))
    return r


def run(llm, t: dict, arm: str) -> dict:
    fn = S.fn_name(t)
    n = CG.arity(t["oracle"], fn)
    cand = CG.ops_for(n)
    rec = {"arm": arm, "id": t["id"], "n_args": n, "cand0": len(cand), "rounds": []}

    for rnd in range(1, MAX_R + 1):
        if not cand:
            rec["rounds"].append({"round": rnd, "fail": "EXHAUSTED", "cand": 0})
            break
        system = A7.sys_b(n) if rnd == 1 else sys_narrowed(n, cand)
        txt = llm.complete([Message("system", system),
                            Message("user", A4.ASK_TYPED.format(goal=t["prompt"], fn=fn, n=n))],
                           max_tokens=700, temperature=0.0) or ""
        spec = A4.parse(txt)
        r = {"round": rnd, "cand": len(cand), "ops": sorted(cand),
             "system_sha1": __import__("hashlib").sha1(system.encode()).hexdigest()[:12],
             "raw": txt[:500]}
        if not spec:
            r.update(EMPTY)
            r["fail"] = "NO_JSON"
            r["signal"] = "STRUCT:OTHER"
            rec["rounds"].append(r)
            break
        kind = str(spec.get("kind") or "")
        if kind not in cand:
            #: 硬拒：目录挡不住，只有这一步挡得住（`F3`）。
            r.update(EMPTY)
            r["fail"] = "OUT_OF_CANDIDATE"
            r["chosen"] = kind
            r["signal"] = "STRUCT:OTHER"
            rec["rounds"].append(r)
            break
        r.update(_eval9(t, spec, n))
        r["chosen"] = kind
        rec["rounds"].append(r)
        if arm == "A" or r["signal"] == "OK":
            break
        nxt, why = CG.narrow(r["signal"], n, kind,
                             r.get("types") or [], t["oracle"], fn,
                             scope=("one" if arm == "B" else "class"),
                             from_ops=cand)
        r["narrow"] = why
        #: 单调性自检：收窄后的集合必须是**上一轮的子集**。
        #: 初版没有这一条，`narrow()` 每轮从 `ops_for(n)` 重来 ⇒ 第二轮删掉的算子在第三轮
        #: 又回来了（`k02`: `INVARIANT → CONSERVE → INVARIANT`）。那叫抖动，不叫收窄。
        if not set(nxt) <= set(cand):
            raise SystemExit(f"收窄不单调 {r['id']}/{arm} r{rnd}: "
                             f"{sorted(cand)} -> {sorted(nxt)}")
        cand = nxt
        if rnd == MAX_R:
            break
    return rec


def last(rec: dict) -> dict:
    return rec["rounds"][-1] if rec["rounds"] else {"fail": "NO_ROUND", "signal": "?"}


def main() -> int:
    llm = HTTPLLM(LLMConfig())
    T = S.tasks()
    out: list[dict] = []
    for tid, t in T.items():
        recs = [run(llm, t, a) for a in ("A", "B", "C")]
        out += recs
        def _f(rec):
            r = last(rec)
            if r.get("fail") in ("EXHAUSTED", "OUT_OF_CANDIDATE", "NO_JSON"):
                return f"×{r['fail']}"
            return (f"{r.get('kind')}/{'P' if r.get('pass_oracle') else 'x'}"
                    f"{'B' if r.get('bite_partial') else 'x'}")
        print(f"{tid:24s} A={_f(recs[0]):26s} B={_f(recs[1]):26s} C={_f(recs[2]):26s} "
              f"| 轮数 {[len(r['rounds']) for r in recs]}", flush=True)

    print("\n== 汇总（末轮口径）==")
    live = {tid: t for tid, t in T.items()}
    for arm in ("A", "B", "C"):
        rs = [r for r in out if r["arm"] == arm]
        ok = [r for r in rs if last(r).get("fail") not in ("EXHAUSTED",)]
        print(f"{arm} 过正确 {sum(bool(last(r).get('pass_oracle')) for r in rs)}/{len(rs)} "
              f"判别 {sum(bool(last(r).get('disc')) for r in rs)}/{len(rs)} "
              f"候选耗尽/拒 {sum(1 for r in rs if last(r).get('fail') in ('EXHAUSTED','OUT_OF_CANDIDATE'))} "
              f"（分母含全部 {len(ok)} 格）")

    print("\n== P6：B 与 C 的选择序列 ==")
    diff = []
    for tid in T:
        b = [x.get("chosen") for x in next(r for r in out if r["id"] == tid and r["arm"] == "B")["rounds"]]
        c = [x.get("chosen") for x in next(r for r in out if r["id"] == tid and r["arm"] == "C")["rounds"]]
        if b != c:
            diff.append((tid, b, c))
        print(f"  {tid:24s} B={b}\n  {'':24s} C={c}  same={b == c}")
    print(f"  不同 = {len(diff)}/{len(T)}")

    print("\n== I1：A 臂 vs V187 B 臂 ==")
    prev = json.load(open(HERE.parent / "l3_s275" / "readings_v187.json"))
    pk = {r["id"]: r for r in prev if r["arm"] == "B"}
    bad = []
    for r in out:
        if r["arm"] != "A":
            continue
        p = pk.get(r["id"]) or {}
        mine = last(r)
        same = ((p.get("kind") == mine.get("kind"))
                and (bool(p.get("pass_oracle")) == bool(mine.get("pass_oracle")))
                and (bool(p.get("bite_partial")) == bool(mine.get("bite_partial"))))
        if not same:
            bad.append((r["id"], p.get("kind"), mine.get("kind"),
                        p.get("pass_oracle"), mine.get("pass_oracle")))
        print(f"  {r['id']:24s} v187B={str(p.get('kind')):16s} A={str(mine.get('kind')):16s} same={same}")
    print(f"  不一致 = {len(bad)}（必须 0，否则三臂读数作废）")

    print("\n== I3：built 与 fail 说得通吗 ==")
    contradictions = []
    for r in out:
        for x in r["rounds"]:
            if not x.get("built") and x.get("fail") in ("", "SEMANTIC"):
                contradictions.append((r["arm"], r["id"], x.get("round"), x.get("fail")))
    print(f"  矛盾行 = {len(contradictions)}（必须 0）")

    dst = HERE / "readings_v189.json"
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"\n落盘 {dst}（{len(out)} 条）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
