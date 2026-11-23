#!/usr/bin/env python3
"""V237 探针：**两体同喂**能不能同时修两处（组合失败模式）。

⛔ 测量脚本，不是产品代码。判分走产品同一个打分器子进程（`ExternalScorer`），
   因为判据是「分数 / 行为向量」—— 必须与账本同一把尺子。
⛔ 判据写在 `PREREG_v237.md`，跑之前写好的。

用法：
    python3 probe_v237.py selftest   # P1 正例闸 / P2 互补 / P3 单变量
    V237_N=1 python3 probe_v237.py run   # 小样
    python3 probe_v237.py run            # 按 §4 的预算（7b 9 次 + 14b 2 次）
"""
from __future__ import annotations

import ast
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

from mw.bench import ExternalScorer                                   # noqa: E402
from mw.reflect import alt_block, build_proposal_prompt, failed_actuals, parse_proposal, sha_of  # noqa: E402
from free_v237 import bodies, make_scorer, task                       # noqa: E402

BASE_URL = os.environ.get("V1_LLM_BASE_URL", "http://localhost:11434/v1")
TEMP = float(os.environ.get("V1_LLM_TEMPERATURE", "1.0"))
MAXTOK = 900
OUT = Path(os.environ.get("V237_OUT") or (HERE / "probe_v237.jsonl"))
RAW = HERE / "raw_v237"

#: 预注册 §4 写死的臂与预算。⛔ 不许中途加。
#: (tag, code槽, alt槽, 次数, 模型)
ARMS = [
    ("S_A",  "A", "",  2, "qwen2.5:7b"),
    ("S_B",  "B", "",  2, "qwen2.5:7b"),
    ("D_AB", "A", "B", 4, "qwen2.5:7b"),   # 主臂
    ("D_BA", "B", "A", 1, "qwen2.5:7b"),   # 位序控制
]
#: 14b 小样：破速度闸（9.26 tok/s），只做描述性读数，不参与判据（PREREG §4/§5）。
SMALL = [("D_AB14", "A", "B", 2, "qwen2.5:14b")]
#: ★ 预注册后修订 §8.2（正例控制）：第二体换成**正确答案** `A'`。
#: 目的：把「读了第二体但不用」与「第二体对输出零影响」分开。没有这一条，
#: `D_AB` 全抄 A 这件事**有两种解释**，而我只报了一种（纪律 62：每闸要有正例路径）。
EXTRA = [("D_AAfix", "A", "A'", 2, "qwen2.5:7b")]
#: ★ 预注册后修订 §8.4（**探索性**，不作判决）：并列式框架 —— 两份实现**对称**，
#: 没有「当前代码」这个权威槽。只在 §8.2 判定为 (b)「零影响」时才跑（条件触发）。
SYM = [("SYM", "A", "B", 2, "qwen2.5:7b")]


def sym_prompt() -> str:
    """并列式：两份实现对称，各自带自己的诊断。⛔ 只用于探索臂，不进产品。"""
    t = task()
    A, B, _A_fix, _B_fix = bodies()
    sc = make_scorer()
    ra, rb = sc.score(t, A), sc.score(t, B)
    return (
        "下面有**两份** Python 实现，它们都**没有**通过全部测试，且失败的断言不同。\n"
        "请给出**修正后的完整函数**（可以取其中任意部分，也可以都不用）。\n"
        f"【题目】\n{t['prompt']}\n"
        f"【实现 1】\n{A}\n"
        f"【实现 1 的测试结果】\n{ra.why or ''}\n"
        f"【实现 2】\n{B}\n"
        f"【实现 2 的测试结果】\n{rb.why or ''}\n"
        "要求：\n"
        "1) 只输出一个 ```python 代码块，里面是**完整的**函数定义（不要省略任何行）；\n"
        "2) 保持函数名与签名不变；\n"
        "3) 不要解释，不要输出任何其他文字。")


def prompts() -> dict[str, str]:
    """每个臂的 prompt —— 单一构造点，自检与运行共用。"""
    t = task()
    A, B, A_fix, _B_fix = bodies()
    code = {"A": A, "B": B, "A'": A_fix}
    sc = make_scorer()
    out: dict[str, str] = {}
    for tag, ck, ak, _n, _m in ARMS + SMALL + EXTRA + SYM:
        if tag == "SYM":
            out[tag] = sym_prompt()
            continue
        cur = code[ck]
        sr = sc.score(t, cur)                       # 每臂的 why/actuals 取自**它自己**那份代码
        out[tag] = build_proposal_prompt(
            t["prompt"], cur, sr.why or "",
            failed_actuals(cur, t["tests"]),
            alt_code=code[ak] if ak else "")
    return out


def vec_str(v) -> str:
    return "".join("1" if x else "0" for x in (v or ()))


def ast_eq(a: str, b: str) -> bool:
    try:
        return ast.dump(ast.parse(a)) == ast.dump(ast.parse(b))
    except SyntaxError:
        return False


def selftest() -> int:
    t = task()
    sc = make_scorer()
    A, B, A_fix, B_fix = bodies()
    ok = True

    print("=" * 74)
    print("P1 正例闸：判据看得见满分？")
    for tag, src in (("A'", A_fix), ("B'", B_fix)):
        r = sc.score(t, src)
        good = abs(r.score - 1.0) < 1e-9
        ok = ok and good
        print(f"  {tag}: score={r.score} vec={vec_str(r.vec)}  {'✅' if good else '❌'}")

    print("=" * 74)
    print("P2 互补：两个 5/6 的失败格是否不相交？")
    ra, rb = sc.score(t, A), sc.score(t, B)
    va, vb = ra.vec, rb.vec
    both_bad = [i for i in range(len(va)) if not va[i] and not vb[i]]
    good = (vec_str(va) == "111011" and vec_str(vb) == "111110" and not both_bad)
    ok = ok and good
    print(f"  A vec={vec_str(va)}  B vec={vec_str(vb)}  共同失败格={both_bad}  {'✅' if good else '❌'}")

    print("=" * 74)
    print("P3 单变量：删掉第二体段后是否逐字节回到对照臂？")
    ps = prompts()
    strip_ok = (ps["D_AB"].replace(alt_block(B), "") == ps["S_A"]
                and ps["D_BA"].replace(alt_block(A), "") == ps["S_B"])
    base = build_proposal_prompt("G", "def f(): pass", "W", ("a1", "a2"))
    sha_ok = sha_of(base) == "2818c5673cc0884d"
    ok = ok and strip_ok and sha_ok
    print(f"  strip(D_AB)==S_A 且 strip(D_BA)==S_B : {strip_ok}")
    print(f"  默认路径 sha={sha_of(base)} == 2818c5673cc0884d : {sha_ok}")
    for tag in ps:
        print(f"  {tag}: prompt {len(ps[tag])}B sha={sha_of(ps[tag])}")

    print("=" * 74)
    print("SELFTEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def complete(model: str, prompt: str, max_tokens: int = MAXTOK) -> tuple[str, dict]:
    body = json.dumps({"model": model,
                       "messages": [{"role": "user", "content": prompt}],
                       "temperature": TEMP, "max_tokens": max_tokens}).encode()
    req = urllib.request.Request(
        BASE_URL + "/chat/completions", data=body,
        headers={"Content-Type": "application/json", "Authorization": "Bearer ollama"})
    t0 = time.time()
    r = json.loads(urllib.request.urlopen(req, timeout=900).read())
    wall = time.time() - t0
    ch = (r.get("choices") or [{}])[0]
    txt = (ch.get("message", {}) or {}).get("content", "") or ""
    u = dict(r.get("usage") or {})
    u["wall_s"] = round(wall, 1)
    u["finish"] = ch.get("finish_reason", "")
    return txt, u


def run() -> int:
    t = task()
    sc = make_scorer()
    A, B, _A_fix, _B_fix = bodies()
    ps = prompts()
    n_cap = int(os.environ.get("V237_N", "0") or 0)
    only = [x.strip() for x in (os.environ.get("V237_ARMS") or "").split(",") if x.strip()]
    plan = ARMS + SMALL + (EXTRA if os.environ.get("V237_EXTRA") else []) \
        + (SYM if os.environ.get("V237_SYM") else [])
    #: V237b（§308.4）按臂改预算：`V237_BUDGET=S_B:4,D_BA:4`。
    #: ⛔ 只允许**调大**，且新预算写进该轮自己的 PREREG（不许边跑边改）。
    over = {}
    for kv in (os.environ.get("V237_BUDGET") or "").split(","):
        if ":" in kv:
            k, v = kv.split(":", 1)
            over[k.strip()] = int(v)
    if not RAW.exists():
        RAW.mkdir()

    for tag, ck, ak, n, model in plan:
        if only and tag not in only:
            continue
        k = min(n, n_cap) if n_cap > 0 else n
        k = over.get(tag, k)
        p = ps[tag]
        psha = sha_of(p)
        trunc = 0
        for i in range(k):
            try:
                txt, u = complete(model, p)
            except Exception as e:                       # noqa: BLE001
                print(json.dumps({"arm": tag, "i": i, "err": str(e)[:160]}, ensure_ascii=False))
                continue
            code, fenced = parse_proposal(txt)
            (RAW / f"out_{tag}_{i:02d}.txt").write_text(txt, encoding="utf-8")
            rec = {"arm": tag, "model": model, "i": i, "temp": TEMP,
                   "prompt_sha": psha, "prompt_chars": len(p),
                   "out_chars": len(txt), "code_chars": len(code), "fenced": bool(fenced),
                   "code_sha": sha_of(code),
                   "tok": u.get("completion_tokens"), "wall_s": u.get("wall_s"),
                   "finish": u.get("finish")}
            trunc = trunc + 1 if u.get("finish") != "stop" else 0
            if not code.strip():
                rec["err"] = "empty"
                print(json.dumps(rec, ensure_ascii=False))
                if trunc >= 2:
                    print(f"  ⛔ {tag} 连续 {trunc} 次未正常结束 ⇒ 按 PREREG P8 停该臂")
                    break
                continue
            sr = sc.score(t, code)
            vec = list(getattr(sr, "vec", None) or [])
            rec["score"] = round(float(sr.score), 4)
            rec["hit6"] = bool(abs(sr.score - 1.0) < 1e-9)
            rec["vec"] = vec
            #: P5：抄没抄。⛔ 只报读数，不作判据（纪律 148：装置会规范化，两层都报）
            rec["EXACT_A"] = sha_of(code) == sha_of(A)
            rec["EXACT_B"] = sha_of(code) == sha_of(B)
            rec["AST_EQ_A"] = ast_eq(code, A)
            rec["AST_EQ_B"] = ast_eq(code, B)
            #: P6：修了几处。格 3 = `'10m'`（A 挂）、格 5 = `'abc'`（B 挂）。
            #: ⚠️ 首版字段叫 `q`，取值 `only3`/`only5` 的语义是「**只通过**那一格」——
            #:    而人一眼读成「只修好那一格」，正好反号（AZ162g 的「标签撒谎」同型）。
            #:    改成 `gate3`/`gate5` 两个布尔 + `fixed` 四值，语义写在名字里。
            if len(vec) >= 6:
                rec["gate3"] = bool(vec[3])      # 格 3 = `'10m'` 通过
                rec["gate5"] = bool(vec[5])      # 格 5 = `'abc'` 通过
                rec["fixed"] = ("fix3+fix5" if vec[3] and vec[5] else
                                "fix3only" if vec[3] else
                                "fix5only" if vec[5] else "neither")
            rec["why"] = (sr.why or "")[:120]
            with open(OUT, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            print(json.dumps(rec, ensure_ascii=False))
            if trunc >= 2:
                print(f"  ⛔ {tag} 连续 {trunc} 次未正常结束 ⇒ 按 PREREG P8 停该臂")
                break
    return 0


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "selftest"
    raise SystemExit(selftest() if mode == "selftest" else run())
