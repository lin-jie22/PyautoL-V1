"""V184 三臂：`base`（V183 原装置）/ `typed`（类型化装置）/ `typed_mem`（typed + 记忆）。

臂间单变量（PREREG §四）：
  base ↔ typed     = 装置
  typed ↔ typed_mem = prompt 里有没有 `[语义记忆]`

判据四格与 V183 同名同义：pass_oracle / bite_partial / bite_stub / disc。
⛔ typed 臂的 `inputs` 由装置生成，模型碰不到；⛔ 记忆臂的 `recall` 结果装置不筛。
"""
from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent / "l3_s271"))
sys.path.insert(0, str(ROOT))

import substrate as S  # noqa: E402
import templates as TP  # noqa: E402  ← V183 原物，base 臂用
from mw import critgen as CG  # noqa: E402
from mw import metacrit as MC  # noqa: E402
from mw.config import LLMConfig  # noqa: E402
from mw.llm import HTTPLLM, Message  # noqa: E402

SEED = Path("/tmp/v184_seed/memH/memory.db")
JS = re.compile(r"\{.*\}", re.S)

# ── base 臂（V183 C 臂逐字）──────────────────────────────────────────────
SYS_BASE = ("你是测试工程师。你只能选一个算子并填槽，判定逻辑由框架提供，你不许写。\n"
            "可用算子（选且只选一个）：\n" +
            "\n".join(f"- {k}: {v}" for k, v in TP.KINDS.items()) +
            "\n\n只输出下面这个 JSON（字段一个不少，kind 必填；用不到的可选字段留空串）：\n"
            '{"kind": "<算子名>", "inputs": [<python 字面量>], '
            '"decode": "", "leaves_in": "", "leaves_out": "", "norm": "", '
            '"keep": "", "bound": ""}\n'
            "说明：ROUNDTRIP/CONSERVE/SUBSEQ 的 inputs 元素是**单个**输入；"
            "EMPTY_IFF_EQUAL 是 `[a, b]`；BOUND/TAIL 是 `[字符串, 整数]`。\n"
            "⛔ inputs 只写**输入**，不许写题目要求的输出。")

# ── typed 臂 ────────────────────────────────────────────────────────────
SYS_TYPED = ("你是测试工程师。你只能选一个算子并填槽，判定逻辑由框架提供，你不许写。\n"
             "可用算子（选且只选一个）：\n" + CG.catalogue() +
             "\n\n类型只能是这四个：" + " / ".join(CG.TYPES) + "\n"
             "只输出这个 JSON（字段一个不少）：\n" + CG.skeleton() +
             "\n说明：`types` 与函数的必填参数**一一对应**（长度相等）。\n"
             "⛔ 不要写 inputs —— 输入由框架按 types 生成，你只填值。")

ASK = "题目：\n{goal}\n\n函数名：{fn}\n"
ASK_TYPED = "题目：\n{goal}\n\n函数名：{fn}；必填参数 {n} 个。\n请给出 JSON。"


def parse(txt: str) -> dict | None:
    m = JS.search(txt or "")
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def _mem_block(store, prompt: str) -> str:
    """与部署同一渲染（`core.py`）：`[语义记忆] ` + ` ｜ ` 连接、每条 120 字。"""
    got = store.recall(prompt, k=6)
    return ("[语义记忆] " + " ｜ ".join(f.text[:120] for f in got)) if got else ""


def judge4(t: dict, src: str) -> dict:
    a = S.judge(t["oracle"] + "\n\n" + src, ["assert _rel()"])
    return {"pass_oracle": a == "", "why_oracle": a[:180],
            "bite_partial": S.judge(t["partial"] + "\n\n" + src, ["assert _rel()"]) != "",
            "bite_stub": S.judge(S.stub(t) + "\n\n" + src, ["assert _rel()"]) != ""}


def run_base(llm, t: dict, mem: str) -> dict:
    fn = S.fn_name(t)
    goal = (mem + "\n\n" + t["prompt"]) if mem else t["prompt"]
    txt = llm.complete([Message("system", SYS_BASE), Message("user", ASK.format(goal=goal, fn=fn))],
                       max_tokens=700, temperature=0.0) or ""
    spec = parse(txt)
    rec = {"arm": "base", "id": t["id"], "kind": (spec or {}).get("kind"), "fail": "",
           "raw": txt[:600]}
    src = ""
    if not spec:
        rec["fail"] = "NO_JSON"
    else:
        inputs = spec.get("inputs") or []
        if isinstance(inputs, list) and inputs:
            src = TP.build(str(spec.get("kind")), spec, inputs, fn)
        if not src:
            rec["fail"] = "SHAPE"
    if not src:
        rec.update({"built": False, "pass_oracle": False, "bite_partial": False,
                    "bite_stub": False, "disc": False, "admit": False})
        return rec
    rec["built"] = True
    rec.update(judge4(t, src))
    rec["disc"] = rec["pass_oracle"] and rec["bite_partial"]
    rec["admit"] = MC.admit(src)[0]
    rec["src"] = src
    return rec


def run_typed(llm, t: dict, mem: str, arm: str) -> dict:
    """`arm` 由**调用方**给：⛔ 不许按 `mem` 是否为空来推断 —— 空注入也是记忆臂的一次运行
    （V184 自踩：k04 的 `recall` 返回 0 条 ⇒ 被标成 `typed` ⇒ 汇总分母错）。"""
    fn = S.fn_name(t)
    n = CG.arity(t["oracle"], fn)
    goal = (mem + "\n\n" + t["prompt"]) if mem else t["prompt"]
    txt = llm.complete([Message("system", SYS_TYPED),
                        Message("user", ASK_TYPED.format(goal=goal, fn=fn, n=n))],
                       max_tokens=700, temperature=0.0) or ""
    spec = parse(txt)
    rec = {"arm": arm, "id": t["id"], "n_args": n, "mem_chars": len(mem or ""),
           "kind": (spec or {}).get("kind"), "types": (spec or {}).get("types"),
           "fail": "", "raw": txt[:600]}
    if not spec:
        rec["fail"] = "NO_JSON"
        rec.update({"built": False, "pass_oracle": False, "bite_partial": False,
                    "bite_stub": False, "disc": False, "admit": False})
        return rec
    kind = str(spec.get("kind") or "")
    types = spec.get("types") if isinstance(spec.get("types"), list) else []
    slots = {"decode": spec.get("decode") or "", "keep": spec.get("keep") or ""}
    err = CG.validate(kind, types, slots, n)
    if err:
        rec["fail"] = "UNREACH" if "结构不可达" in err else "SHAPE"
        rec["err"] = err
        rec.update({"built": False, "pass_oracle": False, "bite_partial": False,
                    "bite_stub": False, "disc": False, "admit": False})
        return rec
    allin = CG.inputs_for(kind, types)
    legal = CG.legal_indexes(t["oracle"], fn, allin)
    if legal is None:
        rec["fail"] = "PROBE_FAULT"
    elif not legal:
        rec["fail"] = "OUT_OF_DOMAIN"
    rec["domain"] = [len(legal) if legal is not None else -1, len(allin)]
    if rec["fail"]:
        rec.update({"built": False, "pass_oracle": False, "bite_partial": False,
                    "bite_stub": False, "disc": False, "admit": False})
        return rec
    src = CG.build(kind, types, slots, fn, [allin[i] for i in legal])
    rec["built"] = bool(src)
    if not src:
        rec["fail"] = "SHAPE"
        rec.update({"pass_oracle": False, "bite_partial": False, "bite_stub": False,
                    "disc": False, "admit": False})
        return rec
    rec.update(judge4(t, src))
    rec["disc"] = rec["pass_oracle"] and rec["bite_partial"]
    rec["admit"] = MC.admit(src)[0]
    rec["src"] = src
    if not rec["pass_oracle"]:
        rec["fail"] = "SEMANTIC"
    return rec


def main() -> int:
    llm = HTTPLLM(LLMConfig())
    T = S.tasks()
    store = None
    if SEED.exists():
        tmp = Path(tempfile.mkdtemp(prefix="v184arms-")) / "memory.db"
        shutil.copy(SEED, tmp)                   # 纪律 31
        from mw.memory import MemoryStore
        store = MemoryStore(str(tmp))
    else:
        print("⚠️ 种子库不存在 ⇒ typed_mem 臂不跑（记忆未接）")

    out: list[dict] = []
    for tid, t in T.items():
        rb = run_base(llm, t, "")
        rt = run_typed(llm, t, "", "typed")
        out += [rb, rt]
        rm = None
        if store:
            mem = _mem_block(store, t["prompt"])
            rm = run_typed(llm, t, mem, "typed_mem")
            out.append(rm)
        def _f(r):
            return (f"{r['kind']}({'P' if r['pass_oracle'] else 'x'}"
                    f"{'B' if r['bite_partial'] else 'x'})") if r else "-"
        mc = rm["mem_chars"] if rm else -1
        print(f"{tid:24s} base={_f(rb)}  typed={_f(rt)}  mem({mc})={_f(rm)}  "
              f"fail={rt['fail'] or '-'}/{rm['fail'] if rm else '-'}")

    n = len(T)
    print("\n== 汇总 ==")
    for arm in ("base", "typed", "typed_mem"):
        rows = [r for r in out if r["arm"] == arm]
        if not rows:
            continue
        print(f"{arm:10s} 可跑 {sum(1 for r in rows if r['built'])}/{n} "
              f"过正确 {sum(1 for r in rows if r['pass_oracle'])}/{n} "
              f"咬错 {sum(1 for r in rows if r['bite_partial'])}/{n} "
              f"判别 {sum(1 for r in rows if r['disc'])}/{n} "
              f"准入 {sum(1 for r in rows if r['admit'])}/{n}")
        shape = sum(1 for r in rows if r["fail"] == "SHAPE")
        unre = sum(1 for r in rows if r["fail"] == "UNREACH")
        print(f"{'':10s} 失败分类：SHAPE={shape} UNREACH={unre} "
              f"OUT_OF_DOMAIN={sum(1 for r in rows if r['fail']=='OUT_OF_DOMAIN')} "
              f"NO_JSON={sum(1 for r in rows if r['fail']=='NO_JSON')} "
              f"SEMANTIC={sum(1 for r in rows if r['fail']=='SEMANTIC')}")

    print("\n== 4 道记忆依赖题（逐题三臂）==")
    for tid in S.MEMORY_DEPENDENT:
        cells = []
        for arm in ("base", "typed", "typed_mem"):
            r = next((x for x in out if x["arm"] == arm and x["id"] == tid), None)
            if r:
                cells.append(f"{arm}={r['kind']}/{'P' if r['pass_oracle'] else 'x'}"
                             f"{'B' if r['bite_partial'] else 'x'}")
        print(f"  {tid:24s} {'  '.join(cells)}")

    print("\n== 记忆注入量（0 = 该题 recall 空 ⇒ 这一题的「相同」不构成证据）==")
    for r in out:
        if r["arm"] == "typed_mem":
            print(f"  {r['id']:24s} mem_chars={r['mem_chars']}")

    print("\n== 自足 4 题：typed vs typed_mem 是否逐题相同 ==")
    for tid in T:
        if tid in S.MEMORY_DEPENDENT:
            continue
        a = next((x for x in out if x["arm"] == "typed" and x["id"] == tid), None)
        b = next((x for x in out if x["arm"] == "typed_mem" and x["id"] == tid), None)
        if a and b:
            same = (a["kind"], a["pass_oracle"], a["bite_partial"]) == \
                   (b["kind"], b["pass_oracle"], b["bite_partial"])
            print(f"  {tid:24s} same={same} mem_chars={b['mem_chars']}")

    (HERE / "readings_v184.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"\n落盘 {HERE / 'readings_v184.json'}（{len(out)} 行）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
