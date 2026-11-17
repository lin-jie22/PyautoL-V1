"""V189 补臂 D：**三轮但候选集永不收窄** —— 用来分离「多跑几轮」与「收窄」。

## 为什么必须有这一臂（预注册 §4 的 `F4`）

B 臂的 pass 从 3/8 涨到 4/8。但 B 与 A 同时差了两件事：
  ① 多跑了两轮；② 每轮候选集在变小（目录变短 + 硬拒）。
没有 D 臂就无法说清是①还是②。

D = 三轮，round 2/3 的 system **逐字重放 round 1**（`sys_b(n)`），不删任何算子。
- 若 D 与 A 逐格相同 ⇒ 同 prompt 重跑不产生变化 ⇒ B 的差异只能来自收窄；
- 若 D ≠ A ⇒ 「多轮」本身就有变化（温度或服务端不严格确定）⇒ A→B 的归因必须打折。

⛔ 这是**预注册之外**补的对照，目的只是分离归因；它不改变 §3 的任何押注。
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
import arms_v187 as A7  # noqa: E402
import arms_v189 as A9  # noqa: E402
from mw import critgen as CG  # noqa: E402
from mw.config import LLMConfig  # noqa: E402
from mw.llm import HTTPLLM, Message  # noqa: E402

MAX_R = 3


def run(llm, t: dict) -> dict:
    fn = S.fn_name(t)
    n = CG.arity(t["oracle"], fn)
    rec = {"arm": "D", "id": t["id"], "n_args": n, "rounds": [],
           "note": "system 每轮逐字相同；候选集不收窄"}
    for rnd in range(1, MAX_R + 1):
        system = A7.sys_b(n)          # ← 与 round 1 逐字相同，不换目录
        txt = llm.complete([Message("system", system),
                            Message("user", A4.ASK_TYPED.format(goal=t["prompt"], fn=fn, n=n))],
                           max_tokens=700, temperature=0.0) or ""
        spec = A4.parse(txt)
        r = {"round": rnd, "raw": txt[:500],
             "system_sha1": __import__("hashlib").sha1(system.encode()).hexdigest()[:12]}
        if not spec:
            r.update({"built": False, "fail": "NO_JSON", "signal": "STRUCT:OTHER"})
            rec["rounds"].append(r)
            break
        r.update(A9._eval9(t, spec, n))
        r["chosen"] = str(spec.get("kind") or "")
        rec["rounds"].append(r)
        #: ⛔ 这里**故意**不调用 `narrow()`，也不 break —— 三轮跑满。
    return rec


def main() -> int:
    llm = HTTPLLM(LLMConfig())
    T = S.tasks()
    prev = json.load(open(HERE / "readings_v189.json"))
    a = {r["id"]: r for r in prev if r["arm"] == "A"}
    out: list[dict] = []
    for tid, t in T.items():
        rec = run(llm, t)
        out.append(rec)
        kinds = [x.get("chosen") for x in rec["rounds"]]
        raws = [x.get("raw") for x in rec["rounds"]]
        shas = [x.get("system_sha1") for x in rec["rounds"]]
        print(f"{tid:24s} kinds={kinds} system_sha 全同={len(set(shas)) == 1} "
              f"raw 全同={len(set(raws)) == 1}", flush=True)

    print("\n== D vs A：同 prompt 重跑会产生变化吗 ==")
    bad = []
    for r in out:
        p = a.get(r["id"]) or {}
        pa = p["rounds"][0] if p.get("rounds") else {}
        mine = r["rounds"][0]
        same = ((pa.get("kind") == mine.get("chosen"))
                and (bool(pa.get("pass_oracle")) == bool(mine.get("pass_oracle")))
                and (bool(pa.get("bite_partial")) == bool(mine.get("bite_partial"))))
        if not same:
            bad.append((r["id"], pa.get("kind"), mine.get("chosen")))
        print(f"  {r['id']:24s} A={str(pa.get('kind')):16s} D.r1={str(mine.get('chosen')):16s} same={same}")
    print(f"  不一致 = {len(bad)}/{len(out)}")

    print("\n== D 内部：三轮彼此相同吗 ==")
    diff = [r["id"] for r in out
            if len({(x.get("chosen"), bool(x.get("pass_oracle")), bool(x.get("bite_partial")))
                    for x in r["rounds"]}) > 1]
    print(f"  三轮有差异的题 = {diff or '无'}")

    dst = HERE / "readings_v189_d.json"
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"\n落盘 {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
