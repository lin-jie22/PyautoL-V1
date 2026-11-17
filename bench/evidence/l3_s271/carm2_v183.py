"""V183 R 臂：槽校验 + **一次**修复（C 臂的同一提示词 + 结构化回错）。

⛔ 只修一次、不循环；⛔ 不替模型补槽；⛔ 校验器不执行任何模型写的代码。
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
       "EMPTY_IFF_EQUAL 是 `[a, b]`；BOUND/TAIL 是 `[字符串, 整数]`，"
       "**每个元素本身是一个长度为 2 的列表**（例：[[\"abcdef\", 3]]）。\n"
       "⛔ inputs 只写**输入**，不许写题目要求的输出。")

ASK = "题目：\n{goal}\n\n函数名：{fn}\n请给出 JSON。"
FIX = ("你上一次的输出是：\n{prev}\n\n结构校验没过：{err}\n\n"
       "请只输出修正后的 JSON（同一个算子也可以，只要把槽填对）。")

JS = re.compile(r"\{.*\}", re.S)


def parse(txt: str) -> dict | None:
    m = JS.search(txt or "")
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def attempt(spec: dict, fn: str) -> tuple[str, str]:
    """(可跑源码, 校验错误)。校验不过时源码为空。"""
    if not spec:
        return "", "输出不是可解析的 JSON"
    kind = str(spec.get("kind") or "")
    err = TP.validate(kind, spec)
    if err:
        return "", err
    return TP.build(kind, spec, spec["inputs"], fn), ""


def main() -> int:
    llm = HTTPLLM(LLMConfig())
    T = S.tasks()
    out = []
    for tid in T:
        t = T[tid]
        fn = S.fn_name(t)
        raw1 = llm.complete([Message("system", SYS),
                             Message("user", ASK.format(goal=t["prompt"], fn=fn))],
                            max_tokens=700, temperature=0.0) or ""
        spec = parse(raw1)
        src, err = attempt(spec, fn)
        rec = {"id": tid, "kind": (spec or {}).get("kind"), "repaired": False,
               "err1": err, "raw1": raw1[:400]}
        if not src:
            raw2 = llm.complete([Message("system", SYS),
                                 Message("user", ASK.format(goal=t["prompt"], fn=fn)),
                                 Message("assistant", raw1),
                                 Message("user", FIX.format(prev=raw1, err=err))],
                                max_tokens=700, temperature=0.0) or ""
            spec2 = parse(raw2)
            src2, err2 = attempt(spec2, fn)
            rec.update({"repaired": True, "kind": (spec2 or {}).get("kind"),
                        "err2": err2, "raw2": raw2[:400]})
            src = src2
        if src:
            rec["admit"] = MC.admit(src)[0]
            rec["pass_oracle"] = S.judge(t["oracle"] + "\n\n" + src, ["assert _rel()"]) == ""
            rec["why_oracle"] = S.judge(t["oracle"] + "\n\n" + src, ["assert _rel()"])[:200]
            rec["bite_partial"] = S.judge(t["partial"] + "\n\n" + src, ["assert _rel()"]) != ""
            rec["bite_stub"] = S.judge(S.stub(t) + "\n\n" + src, ["assert _rel()"]) != ""
            rec["disc"] = rec["pass_oracle"] and rec["bite_partial"]
            rec["src"] = src
        else:
            rec.update({"admit": False, "pass_oracle": False, "bite_partial": False,
                        "bite_stub": False, "disc": False, "why_oracle": "NO_BUILD"})
        out.append(rec)
        print(f"{tid:24s} kind={str(rec['kind'])[:16]:16s} fix={'Y' if rec['repaired'] else 'N'} "
              f"过正确={'✅' if rec['pass_oracle'] else '❌'} "
              f"咬错={'✅' if rec['bite_partial'] else '❌'} "
              f"判别={'✅' if rec['disc'] else '❌'}")
        if rec["why_oracle"] and rec["why_oracle"] != "NO_BUILD":
            print(f"    oracle: {rec['why_oracle'][:140]}")
        elif rec["why_oracle"] == "NO_BUILD":
            print(f"    err: {rec.get('err2') or rec['err1']}")

    n = len(out)
    print(f"\nR 臂：可跑 {sum(1 for r in out if 'src' in r)}/{n} "
          f"过正确 {sum(1 for r in out if r['pass_oracle'])}/{n} "
          f"咬错 {sum(1 for r in out if r['bite_partial'])}/{n} "
          f"判别力 {sum(1 for r in out if r['disc'])}/{n} "
          f"发生修复 {sum(1 for r in out if r['repaired'])}/{n}")
    (HERE / "readings_v183_r.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
