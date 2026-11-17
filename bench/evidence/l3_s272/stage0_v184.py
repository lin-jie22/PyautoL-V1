"""V184 STAGE0：量分母（⛔ 0 次 LLM）。

三件事（PREREG §三 + v2 补充）：
  1. `arity` 表 —— 8 题各自必填位置参数个数（纯 ast）
  2. 记忆送达 —— 临时种子库里 `recall(prompt)` 逐题取回，报条数与「含自己那条」
  3. 可行性矩阵 —— 6 算子 × 8 题，用 `critgen` 渲染、先圈域再只跑 **oracle**
     ⛔ 不看 partial（判别力是收费的那一半）

矩阵的唯一用途：把「结构上不可能过正确」的格逐格登记（纪律 67）。
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent / "l3_s271"))
sys.path.insert(0, str(ROOT))

import substrate as S  # noqa: E402
from mw import critgen as CG  # noqa: E402

SEED = Path("/tmp/v184_seed/memH/memory.db")

_OWN_HEAD = {
    "g7d2_dedupe_keep": "（ZK1）去重惯例",
    "g7d3_fmt_amount": "（ZK2）金额惯例",
    "g7e1_parse_flag": "（ZK3）开关惯例",
    "g7e2_truncate_tail": "（ZK4）截断惯例",
}


def _own(txt: str, tid: str) -> bool:
    head = _OWN_HEAD.get(tid, "")
    return bool(head) and txt.startswith(head)


def _default_types(kind: str, t: dict, n: int) -> list:
    """STAGE0 的**探针类型**：只用于量可行性，⛔ 不是给模型的提示。

    规则（写死）：TAIL/BOUND → ["str","int"]；其余按题面里出现的第一个类型词，
    缺省 `str`，多参数时同一个词填满。
    """
    if kind in ("TAIL", "BOUND"):
        return ["str", "int"]
    p = t["prompt"]
    one = "str"
    for key, word in (("dict", "dict"), ("list", "列表"), ("str", "字符串"),
                      ("int", "数字"), ("int", "int")):
        if word in p:
            one = key
            break
    return [one] * n


def _default_slots(kind: str) -> dict:
    if kind == "ROUNDTRIP":
        return {"decode": "json.loads"}
    if kind == "TAIL":
        return {"keep": "ellipsis_tail_n"}
    return {}


def main() -> int:
    T = S.tasks()
    out: dict = {"arity": {}, "recall": {}, "matrix": {}, "domain": {}}

    print("== 1. arity（ast，必填位置参数）==")
    for tid, t in T.items():
        n = CG.arity(t["oracle"], S.fn_name(t))
        out["arity"][tid] = n
        print(f"  {tid:24s} fn={S.fn_name(t):16s} arity={n}")

    print("\n== 2. 记忆送达（recall(prompt, k=6)）==")
    if not SEED.exists():
        print("  ❌ 种子库不存在：先跑 bench/build_g7d_seed.py")
        return 1
    tmp = Path(tempfile.mkdtemp(prefix="v184mem-")) / "memory.db"
    shutil.copy(SEED, tmp)                      # 纪律 31：只开副本
    from mw.memory import MemoryStore
    store = MemoryStore(str(tmp))
    for tid, t in T.items():
        got = store.recall(t["prompt"], k=6)
        texts = [f.text[:120] for f in got]
        own = any(_own(txt, tid) for txt in texts)
        out["recall"][tid] = {"n": len(got), "own": bool(own), "chars": sum(map(len, texts))}
        print(f"  {tid:24s} 取回 {len(got)} 条  chars={sum(map(len, texts)):4d}  own={own}")

    print("\n== 3. 可行性矩阵（先圈域，只跑 oracle；6 算子 × 8 题）==")
    for tid, t in T.items():
        n = out["arity"][tid]
        fn = S.fn_name(t)
        row, dom = {}, {}
        for kind in CG.OPS:
            if not CG.reachable(kind, n):
                row[kind] = "UNREACH"
                continue
            types = _default_types(kind, t, n)
            slots = _default_slots(kind)
            err = CG.validate(kind, types, slots, n)
            if err:
                row[kind] = "BAD:" + err[:40]
                continue
            allin = CG.inputs_for(kind, types)
            legal = CG.legal_indexes(t["oracle"], fn, allin)
            if legal is None:
                row[kind] = "PROBE_FAULT"
                continue
            dom[kind] = [len(legal), len(allin)]
            if not legal:
                row[kind] = f"OUT_OF_DOMAIN(0/{len(allin)})"
                continue
            src = CG.build(kind, types, slots, fn, [allin[i] for i in legal])
            why = S.judge(t["oracle"] + "\n\n" + src, ["assert _rel()"])
            tag = "PASS" if why == "" else "FAIL:" + why.splitlines()[-1][:60]
            row[kind] = f"{tag} [{len(legal)}/{len(allin)}]"
        out["matrix"][tid] = row
        out["domain"][tid] = dom
        cells = "  ".join(f"{k}={v.split(':')[0].split()[0]}" for k, v in row.items())
        print(f"  {tid:24s} {cells}")
        for k, v in row.items():
            if v.startswith("FAIL") or v.startswith("OUT_OF"):
                print(f"      {k}: {v}")

    (HERE / "stage0_v184.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    ok = sum(1 for r in out["matrix"].values() for v in r.values() if v.startswith("PASS"))
    tot = sum(1 for r in out["matrix"].values() for v in r.values()
              if not v.startswith("UNREACH"))
    print(f"\n可达格里 oracle 通过 {ok}/{tot}（不可达格 "
          f"{sum(1 for r in out['matrix'].values() for v in r.values() if v == 'UNREACH')}/48）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
