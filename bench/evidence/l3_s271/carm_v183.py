"""V183 C 臂：给定算子库，模型只做**选择 + 填槽**（判定由模板算）。

⛔ 模型输出必须是 JSON；⛔ 模板由 `templates.py` 提供，模型碰不到判定逻辑。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))

import substrate as S  # noqa: E402
import templates as TP  # noqa: E402
from mw import metacrit as MC  # noqa: E402
from mw.config import LLMConfig  # noqa: E402
from mw.llm import HTTPLLM, Message  # noqa: E402

SYS = ("你是测试工程师。你只能选一个算子并填槽，判定逻辑由框架提供，你不许写。\n"
       "可用算子（选且只选一个）：\n" +
       "\n".join(f"- {k}: {v}" for k, v in TP.KINDS.items()) +
       "\n\n只输出下面这个 JSON（字段一个不少，kind 必填；用不到的可选字段留空串）：\n"
       '{"kind": "<算子名>", "inputs": [<python 字面量>], '
       '"decode": "", "leaves_in": "", "leaves_out": "", "norm": "", '
       '"keep": "", "bound": ""}\n'
       "说明：ROUNDTRIP/CONSERVE/SUBSEQ 的 inputs 元素是**单个**输入；"
       "EMPTY_IFF_EQUAL 是 `[a, b]`；BOUND/TAIL 是 `[字符串, 整数]`。\n"
       "⛔ inputs 只写**输入**，不许写题目要求的输出。")

ASK = "题目：\n{goal}\n\n函数名：{fn}\n请给出 JSON。"

JS = re.compile(r"\{.*\}", re.S)


def parse(txt: str) -> dict | None:
    m = JS.search(txt or "")
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def main() -> int:
    llm = HTTPLLM(LLMConfig())
    T = S.tasks()
    out = []
    for tid in T:
        t = T[tid]
        fn = S.fn_name(t)
        txt = llm.complete([Message("system", SYS), Message("user", ASK.format(goal=t["prompt"], fn=fn))],
                           max_tokens=700, temperature=0.0) or ""
        spec = parse(txt)
        rec = {"id": tid, "kind": None, "ok_spec": bool(spec), "raw": txt[:600]}
        src = ""
        if spec:
            rec["kind"] = spec.get("kind")
            inputs = spec.get("inputs") or []
            if isinstance(inputs, list) and inputs:
                src = TP.build(str(spec.get("kind")), spec, inputs, fn)
        rec["built"] = bool(src)
        if src:
            ok, why = MC.admit(src)
            rec["admit"] = ok
            rec["admit_reason"] = why
            rec["pass_oracle"] = S.judge(t["oracle"] + "\n\n" + src, ["assert _rel()"]) == ""
            rec["why_oracle"] = S.judge(t["oracle"] + "\n\n" + src, ["assert _rel()"])[:200]
            rec["bite_partial"] = S.judge(t["partial"] + "\n\n" + src, ["assert _rel()"]) != ""
            rec["bite_stub"] = S.judge(S.stub(t) + "\n\n" + src, ["assert _rel()"]) != ""
            rec["disc"] = rec["pass_oracle"] and rec["bite_partial"]
            rec["src"] = src
        else:
            rec.update({"admit": False, "admit_reason": "NO_BUILD", "pass_oracle": False,
                        "bite_partial": False, "bite_stub": False, "disc": False,
                        "why_oracle": "NO_BUILD"})
        out.append(rec)
        print(f"{tid:24s} kind={str(rec['kind'])[:16]:16s} 过正确={'✅' if rec['pass_oracle'] else '❌'} "
              f"咬错={'✅' if rec['bite_partial'] else '❌'} 过空={'⚠️' if not rec['bite_stub'] else '✅'} "
              f"判别={'✅' if rec['disc'] else '❌'} admit={rec['admit']}")
        if rec["why_oracle"]:
            print(f"    oracle: {rec['why_oracle'][:150]}")

    n = len(out)
    print(f"\nC 臂：可跑判据 {sum(1 for r in out if r['built'])}/{n} "
          f"过正确 {sum(1 for r in out if r['pass_oracle'])}/{n} "
          f"咬错 {sum(1 for r in out if r['bite_partial'])}/{n} "
          f"判别力 {sum(1 for r in out if r['disc'])}/{n} "
          f"准入 {sum(1 for r in out if r['admit'])}/{n}")
    sw = [r for r in out if r["id"] not in S.MEMORY_DEPENDENT]
    print(f"  题面自足 {sum(1 for r in sw if r['disc'])}/{len(sw)}")
    (HERE / "readings_v183_c.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
