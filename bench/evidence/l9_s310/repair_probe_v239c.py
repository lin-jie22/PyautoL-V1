"""V239c 探针：两份 0.8333 的「洞」到底哪种更可修（纯离线，不调模型）。

## 这个脚本回答什么

V239 留下两句观察没做结论（RESULTS §3.3）：
  90579ece（vec=111011，挂 '10m'）—— 一行索引错
  09d0e23d（vec=011111，挂 '1h30m'）—— 结构缺一半
     ⇒「洞越显眼越会改，洞越隐蔽越照抄」，n 小，不做结论。

「更靠谱」不是单值。本脚本把它拆成**四个可数的量**，每个都真跑：

  M1 判分复现    —— 6 条断言重跑，vec 与账本逐位相同（装置可信）
  M2 单点可修性  —— 枚举「单 token 替换 / 单行删除 / 单行插入 / 单下标改动」，
                    判**是否存在**到 6/6 的路径（对应唯一通路『局部改』的最小形态）
  M3 修补陷阱    —— 朴素全局替换（改对一处同时改坏另一处）能否把 6/6 变回 5/6
  M4 洞的显眼度  —— 洞命中的用例在**题面 prompt 里的列举序号**（题面点了 4 个例子）

## 口径（写死，防我事后改）

- 候选 token 池 = 三份文件里出现过的标识符/字面量 ∪ 两份 6/6 实现里的 ∪ 题面字面量。
  ⛔ 不由我手写「正确修法」清单 —— 那等于把答案预先塞进搜索空间。
- 单点 = 一次编辑（替换一个 token / 删一行 / 插一行 / 改一个下标数字）。
- 「可修」= 存在至少一个单点编辑使 6 条断言全过。
- 12 份候选全跑，vec 与 obs_*.jsonl 对拍；对不上就判装置失效，后面读数不采信。
"""

from __future__ import annotations

import contextlib
import io
import itertools
import json
import re
import signal
import sys
import warnings
from pathlib import Path

warnings.simplefilter("ignore")
signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError()))

#: 单个候选的墙钟上限（秒）。超出即判「不通过」并计数 —— 枚举里确实会出现
#: 语义怪但语法合法的候选；⛔ 不许因为怪就让整条枚举停摆。
CAND_TIMEOUT = 0.25

D = Path(__file__).resolve().parent
CAND = D / "cand"

TESTS = [
    "assert parse_duration('1h30m') == 5400",
    "assert parse_duration('45s') == 45",
    "assert parse_duration('2h') == 7200",
    "assert parse_duration('10m') == 600",
    "assert parse_duration('') is None",
    "assert parse_duration('abc') is None",
]

#: 题面列举顺序（prompt: '1h30m'、'45s'、'2h'、'10m'）
PROMPT_ORDER = ["1h30m", "45s", "2h", "10m"]


#: 计时/超时统计（超时的候选不是「失败」，是「跑不动」—— 分开记）
TIMEOUTS = [0]


def judge(src: str) -> tuple[list[bool], float]:
    """跑 6 条断言。返回 (vec, score)。语法错/超时以外一律按断言判。"""
    ns: dict = {}
    signal.setitimer(signal.ITIMER_REAL, CAND_TIMEOUT)
    try:
        # 枚举里会出现「把 int 换成 parse_duration」这种候选 ⇒ 无限递归。
        # 压栈深 + 兜 Exception，避免整条枚举被一次爆栈带走。
        sys.setrecursionlimit(200)
        with contextlib.redirect_stdout(io.StringIO()):
            exec(compile(src, "<cand>", "exec"), ns)
            fn = ns.get("parse_duration")
            if not callable(fn):
                return [False] * 6, 0.0
            vec = []
            for t in TESTS:
                try:
                    exec(t, {"parse_duration": fn})
                    vec.append(True)
                except Exception:
                    vec.append(False)
            return vec, round(sum(vec) / 6, 4)
    except TimeoutError:
        TIMEOUTS[0] += 1
        return [False] * 6, 0.0
    except Exception:
        return [False] * 6, 0.0
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z_0-9]*|\d+")


def tokens_of(srcs: list[str]) -> list[str]:
    seen: dict[str, None] = {}
    for s in srcs:
        for t in TOKEN_RE.findall(s):
            seen.setdefault(t, None)
    out = list(seen)
    # 少量语法/字面量补充（只补**结构**词，不补答案）：见口径段
    for extra in ("0", "1", "2", "3", "m", "h", "s"):
        if extra not in seen:
            out.append(extra)
    # ⛔ 剔除自身函数名：替换成它只会造成无限递归/爆栈，那不是「修补」。
    # ⛔ 剔除 I/O 与反射类内建：替换成它们会把候选代码变成「去读 stdin / 打屏 / 开文件」，
    #    与修补无关，且会让枚举**永久阻塞**（实测 `len`->`input` 直接挂住整条枚举）。
    #    这条是**排除**，不是「把正确修法塞进空间」—— 被排除数记进读数。
    BANNED = {"parse_duration", "input", "print", "open", "exit", "quit", "eval", "exec", "compile"}
    kept = [t for t in out if t not in BANNED]
    return kept, len(out) - len(kept)


def single_edit_space(src: str, pool: list[str]):
    """产出 (描述, 新源码) —— 只做「一个 token 整体替换」。"""
    for m in TOKEN_RE.finditer(src):
        old = m.group(0)
        a, b = m.span()
        for new in pool:
            if new == old:
                continue
            yield f"tok {old!r}->{new!r}@{a}", src[:a] + new + src[b:]


def line_ops(src: str):
    lines = src.splitlines(keepends=True)
    for i, ln in enumerate(lines):
        yield f"delline {i}:{ln.strip()[:40]!r}", "".join(lines[:i] + lines[i + 1 :])


def insert_ops(src: str, donors: list[str]):
    lines = src.splitlines(keepends=True)
    dlines: dict[str, None] = {}
    for d in donors:
        for ln in d.splitlines(keepends=True):
            if ln.strip():
                dlines.setdefault(ln, None)
    for i in range(len(lines) + 1):
        for ln in dlines:
            yield f"ins@{i}:{ln.strip()[:40]!r}", "".join(lines[:i] + [ln] + lines[i:])


def main() -> int:
    # 双保险：任何残留的 I/O 类候选都只会读到 EOF，不会挂住枚举。
    sys.stdin = open("/dev/null")  # noqa: SIM115
    obs = []
    for f in ("obs_d.jsonl", "obs_d2.jsonl"):
        p = D / f
        if p.exists():
            obs += [json.loads(x) for x in p.read_text().splitlines() if x.strip()]
    ledger = {(o.get("impl_sha") or "")[:16]: o for o in obs if o.get("impl_sha")}

    files = sorted(CAND.glob("*.py"))
    srcs = {p.stem: p.read_text() for p in files}

    print("== M1 判分复现 ==")
    bad = 0
    for sha, src in srcs.items():
        vec, sc = judge(src)
        v = "".join("1" if x else "0" for x in vec)
        o = ledger.get(sha)
        exp = "".join("1" if x else "0" for x in (o.get("vec") or [])) if o else "?"
        esc = o.get("score") if o else None
        ok = (o is None) or (v == exp and abs(sc - (esc or 0)) < 1e-6)
        bad += 0 if ok else 1
        print(f"  {sha}  vec={v} score={sc}  账本={exp}/{esc}  {'OK' if ok else 'MISMATCH'}")
    print(f"  → 对拍失败 {bad} 份")

    full = [sha for sha in srcs if judge(srcs[sha])[1] == 1.0]
    donors = [srcs[s] for s in full]
    pool, n_excluded = tokens_of(list(srcs.values()) + donors)
    print(f"  token 池 {len(pool)} 个（剔除自身函数名 {n_excluded} 个）")

    print(f"\n== 6/6 实现 {len(full)} 份：{full} ==")

    targets = ["90579ecebe633352", "09d0e23ddc7dd5ac"]
    for sha in targets:
        src = srcs[sha]
        vec, sc = judge(src)
        print(f"\n== {sha}  起始 vec={''.join('1' if x else '0' for x in vec)} score={sc} ==")

        # M2a 单 token 替换 + M5 邻域地形（分数直方图）
        hits_tok: list[str] = []
        hist: dict[float, int] = {}
        for desc, new in single_edit_space(src, pool):
            (D / "last_cand.txt").write_text(desc, encoding="utf-8")
            sc2 = judge(new)[1]
            hist[sc2] = hist.get(sc2, 0) + 1
            if sc2 == 1.0:
                hits_tok.append(desc)
        # M2b 单行删除
        hits_del = [d for d, new in line_ops(src) if judge(new)[1] == 1.0]
        # M2c 单行插入（供体 = 6/6 实现的所有行）
        hits_ins = [d for d, new in insert_ops(src, donors) if judge(new)[1] == 1.0]

        print(f"  M2a 单token替换命中 {len(hits_tok)} 个  例: {hits_tok[:5]}")
        print(f"  M2b 单行删除命中   {len(hits_del)} 个  例: {hits_del[:5]}")
        print(f"  M2c 单行插入命中   {len(hits_ins)} 个  例: {hits_ins[:5]}")
        top = sorted(hist.items(), key=lambda kv: -kv[1])
        print(f"  M5 单点邻域 {sum(hist.values())} 个候选的分数分布: "
              + "  ".join(f"{k:.4f}×{v}" for k, v in top[:5]))
        same = hist.get(sc, 0)
        print(f"     ↳ 与起点同分（{sc}）占 {same}/{sum(hist.values())}"
              f" = {same / max(1, sum(hist.values())):.1%}；升到 1.0 的只有 {len(hits_tok)} 个")

        # M3 陷阱：把「同形行」分别做「只改第一处 / 两处都改」
        if sha.startswith("90579ece"):
            one = src.replace("parts[1].split('m')[0]", "parts[0].split('m')[0]", 1)
            allx = src.replace("parts[1].split('m')[0]", "parts[0].split('m')[0]")
            for tag, v in (("只改第1处", one), ("两处都改", allx)):
                tv, ts = judge(v)
                print(f"  M3 {tag}: vec={''.join('1' if x else '0' for x in tv)} score={ts}"
                      f"   （同形出现 {src.count('parts[1].split')} 次）")
        else:
            trap = src.replace("s[:-1]", "s.split('h')[0]")
            tv, ts = judge(trap)
            print(f"  M3 最省事改法 s[:-1]->s.split('h')[0]: vec={''.join('1' if x else '0' for x in tv)} score={ts}")

        # M4 洞的显眼度
        miss = [i for i, ok in enumerate(vec) if not ok]
        for i in miss:
            lit = re.search(r"'([^']*)'", TESTS[i]).group(1)
            pos = PROMPT_ORDER.index(lit) + 1 if lit in PROMPT_ORDER else None
            print(f"  M4 挂的格 = 第{i+1}条 {TESTS[i]}  ← 题面列举序号 {pos}/{len(PROMPT_ORDER)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
