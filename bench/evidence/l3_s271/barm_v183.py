"""V183 主体：B 臂 —— 模型自己写**关系型自检**（判决交外部实现）。

与 A 臂（V79 原话「写 assert 检验实现」）的唯一变量是**判据里有没有字面期望值**。
两臂都只喂 `prompt`（⛔ 不喂 tests/oracle/partial）。

⛔ 本脚本不改产品代码；读数落 `readings_v183.json`。
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
from mw.config import LLMConfig  # noqa: E402
from mw.llm import HTTPLLM, Message  # noqa: E402

SYS = ("你是测试工程师。你要写的是一个**自检函数**，它判断某个实现是否符合题目要求。\n"
       "硬性约束（违反即无效）：\n"
       "1. 只用 Python 标准库；\n"
       "2. ⛔ 不许把任何期望输出写成字面量 —— 不许出现 `== '某个固定字符串'`、"
       "`== 某个固定数字/列表/字典` 这类比较；\n"
       "3. 必须把**实现的输出用标准库解回来**，再与**输入**比较（往返/守恒/结构关系）；\n"
       "4. 函数名必须是 `_rel`，不许有参数；通过返回 True，不通过 `raise AssertionError(说明)`；\n"
       "5. 只输出一个 python 代码块，里面只有 `import`/辅助函数/`_rel`。")

ASK = ("题目：\n{goal}\n\n"
       "请写 `_rel()`：它接受一个已定义的函数 `{fn}`（题面要求的函数名），"
       "用标准库把它在若干**你自选的输入**上的输出解回来验证是否自洽。\n"
       "记住：不许写死期望输出，只许判「输出与输入之间的关系」。")

CODE = re.compile(r"```(?:python)?\s*(.*?)```", re.S)


def extract(txt: str) -> str:
    """⚠️ 自踩（第一版）：只留 `def _rel` 起的一段 ⇒ 把之前的 `import`/辅助函数切掉了，
    `k04` 因此报 `NameError: Mapping`，被我差点读成「模型写不出」。⛔ 整块留，
    只切掉末尾的 `_rel()` 自测调用与 `if __name__` 段。"""
    m = CODE.search(txt or "")
    body = (m.group(1) if m else (txt or "")).strip()
    if "def _rel" not in body:
        return ""
    cut = re.search(r"\n(if __name__\s*==|_rel\(\)\s*$|print\()", body, re.M)
    if cut:
        body = body[: cut.start()]
    return body.rstrip()


def literal_hits(src: str) -> list[str]:
    """抓「字面量期望值」：`== '...'` / `== 123` / `== [...]` / `== {...}`（排除与输入比）。"""
    hits = []
    for m in re.finditer(r"==\s*(('[^']*')|(\"[^\"]*\")|(-?\d+(\.\d+)?)|(\[)|(\{))", src):
        hits.append(m.group(1)[:40])
    return hits


def main() -> int:
    llm = HTTPLLM(LLMConfig())
    T = S.tasks()
    out = []
    for tid in T:
        t = T[tid]
        fn = S.fn_name(t)
        txt = llm.complete([Message("system", SYS),
                            Message("user", ASK.format(goal=t["prompt"], fn=fn))],
                           max_tokens=1000, temperature=0.0) or ""
        src = extract(txt)
        rec = {"id": tid, "fn": fn, "ok_src": bool(src), "src": src,
               "literal_hits": literal_hits(src)}
        if src:
            rec["pass_oracle"] = S.judge(t["oracle"] + "\n\n" + src, ["assert _rel()"]) == ""
            rec["why_oracle"] = S.judge(t["oracle"] + "\n\n" + src, ["assert _rel()"])[:200]
            rec["bite_partial"] = S.judge(t["partial"] + "\n\n" + src, ["assert _rel()"]) != ""
            rec["why_partial"] = S.judge(t["partial"] + "\n\n" + src,
                                         ["assert _rel()"])[:200]
            rec["bite_stub"] = S.judge(S.stub(t) + "\n\n" + src, ["assert _rel()"]) != ""
            rec["disc"] = rec["pass_oracle"] and rec["bite_partial"]
        else:
            rec.update({"pass_oracle": False, "bite_partial": False,
                        "bite_stub": False, "disc": False, "why_oracle": "NO_REL"})
        out.append(rec)
        print(f"{tid:24s} src={'Y' if src else 'N'} lit={len(rec['literal_hits'])} "
              f"过正确={'✅' if rec['pass_oracle'] else '❌'} "
              f"咬错={'✅' if rec['bite_partial'] else '❌'} "
              f"过空={'⚠️' if not rec['bite_stub'] else '✅'} "
              f"判别={'✅' if rec['disc'] else '❌'}")
        if rec["why_oracle"]:
            print(f"    oracle: {rec['why_oracle'][:150]}")

    n = len(out)
    print(f"\nB 臂：可执行判据 {sum(1 for r in out if r['ok_src'])}/{n} "
          f"过正确 {sum(1 for r in out if r['pass_oracle'])}/{n} "
          f"咬错 {sum(1 for r in out if r['bite_partial'])}/{n} "
          f"判别力 {sum(1 for r in out if r['disc'])}/{n}")
    hw = [r for r in out if r["id"] in S.MEMORY_DEPENDENT]
    sw = [r for r in out if r["id"] not in S.MEMORY_DEPENDENT]
    print(f"  题面自足 {sum(1 for r in sw if r['disc'])}/{len(sw)}；"
          f"题面不完备 {sum(1 for r in hw if r['disc'])}/{len(hw)}")
    (HERE / "readings_v183.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
