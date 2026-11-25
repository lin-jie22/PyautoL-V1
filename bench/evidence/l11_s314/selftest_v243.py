#!/usr/bin/env python3
"""V243（§315）自检：同分多实现的账本 best 定义。**不跑模型**。

四条判据（PREREG_v243 §4）：Q1 同分异构、Q2 缺 vec 不冒充、Q3 旧读数不变、Q4 提示不变。
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import sys

V1 = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(V1))

D = pathlib.Path(__file__).resolve().parent
#: 候选实现落在 V239~V241 那一轮的证据目录里（内容寻址，跨轮复用）
CAND = V1 / "bench" / "evidence" / "l9_s310" / "cand"
OBS = V1 / "bench" / "evidence" / "l9_s310"
FAILS: list[str] = []


def ck(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'✅' if ok else '❌'} {name}{('  ' + detail) if detail else ''}")
    if not ok:
        FAILS.append(name)


def main() -> int:
    import run as R
    # ⚠️ 账本里存的是 **exec_sha**（判分器执行的那份代码），不是原文 sha
    #    （§310 的口径）。用 `impl_sha_of` 当缓存键会取不回、且 Q3 的比对
    #    会拿两个不同口径的 sha 互比 —— V243 自检第一版就是这么错的。
    from mw.scoreinfo import exec_sha_of as IS

    A = (CAND / "09d0e23ddc7dd5ac.py").read_text(encoding="utf-8")   # vec 011111
    B = (CAND / "90579ecebe633352.py").read_text(encoding="utf-8")   # vec 111011
    sa, sb = IS(A), IS(B)
    print(f"  exec_sha: A={sa} B={sb}")
    cache = D / "_tie_cache"
    # ⚠️ 沙箱的 mkdir 拦截层对 `exist_ok=True` 仍抛 EEXIST ⇒ 先判存在
    if not cache.is_dir():
        cache.mkdir(parents=True)
    (cache / f"{sa}.py").write_text(A, encoding="utf-8")
    (cache / f"{sb}.py").write_text(B, encoding="utf-8")

    cell = "t01_parse_duration"
    #: ⚠️ 顺序故意**先 A 后 B**：旧规矩（first）会取 A，新规矩（farlast）必须取 B。
    obs = [
        {"task_id": cell, "score": 0.8333, "impl_sha": sb, "vec": [True]*3 + [False] + [True]*2,
         "source": "x"},
        {"task_id": cell, "score": 0.8333, "impl_sha": sa, "vec": [False] + [True]*5,
         "source": "y"},
    ]
    obs = [obs[1], obs[0]]          # 先 A（011111）后 B（111011）
    fb = "# fallback\ndef parse_duration(s):\n    return None\n"

    c_far = R._resolve_start(obs, cell, fb, 0.5, cache, policy="ledger", tie="farlast")
    c_first = R._resolve_start(obs, cell, fb, 0.5, cache, policy="ledger", tie="first")
    ck("Q1a farlast 取失败位序最靠后的那份（111011）", c_far[0] == B and c_far[1] == 0.8333,
       f"src={c_far[2]} note={c_far[3][:36]}")
    ck("Q1b first 仍取先出现的那份（011111）", c_first[0] == A, f"src={c_first[2]}")
    ck("Q1c farlast 的日志里写明「同分几份、按什么取」",
       "同分 2 份" in c_far[3] and "fail_pos=3" in c_far[3], repr(c_far[3][:60]))

    # ---- Q2：缺 vec 的行不许冒充「洞在最前」 ----
    obs2 = [
        {"task_id": cell, "score": 0.8333, "impl_sha": sa, "source": "no-vec"},
        {"task_id": cell, "score": 0.8333, "impl_sha": sb,
         "vec": [True]*3 + [False] + [True]*2, "source": "has-vec"},
    ]
    c2 = R._resolve_start(obs2, cell, fb, 0.5, cache, policy="ledger", tie="farlast")
    ck("Q2 有 vec 的行优先于缺 vec 的行", c2[0] == B, f"src={c2[2]}")

    # ---- Q3：真实账本（V239 的 obs_d / obs_d2）两种策略选出同一份 ----
    #  ⚠️ 这条**预注册判据**原样保留、不许事后改。它在这里是红的，而且红得有内容：
    #     前提「真实账本最高分 = 0.8333、同分有多份」本身就是错的（最高分 1.0）。
    #     押注失败如实登记，⛔ 不删、不改成「通过」。
    for f in ("obs_d.jsonl", "obs_d2.jsonl"):
        rows = [json.loads(x) for x in (OBS / f).read_text(encoding="utf-8").splitlines() if x.strip()]
        r_far = R._resolve_start(rows, cell, fb, 0.5, cache, policy="ledger", tie="farlast")
        r_fst = R._resolve_start(rows, cell, fb, 0.5, cache, policy="ledger", tie="first")
        ck(f"Q3 {f}：farlast 与 first 选同一份（{sb[:8]}）",
           r_far[0] == r_fst[0] == B,
           f"{IS(r_far[0])[:8]}({r_far[2]}) / {IS(r_fst[0])[:8]}({r_fst[2]})"
           f" 账本最高分={max(r['score'] for r in rows)}")

    # ---- R 段：押注失败后的**诊断**（⛔ 不是预注册判据，只用来定位红在哪） ----
    print("\n  ── R 段（诊断，非预注册判据）──")
    for f in ("obs_d.jsonl", "obs_d2.jsonl"):
        rows = [json.loads(x) for x in (OBS / f).read_text(encoding="utf-8").splitlines() if x.strip()]
        # 裁到「最高分那一档」= 复现 V239 当时的处境（1.0 那行是之后才出现的）
        top = max(r["score"] for r in rows)
        sub = [r for r in rows if r["score"] == top]
        a = R._resolve_start(sub, cell, fb, 0.5, cache, policy="ledger", tie="farlast")
        b = R._resolve_start(sub, cell, fb, 0.5, cache, policy="ledger", tie="first")
        print(f"  {f} 最高分档={top} 共 {len(sub)} 行："
              f"farlast→{IS(a[0])[:8]} / first→{IS(b[0])[:8]}"
              f" {'（同一份）' if a[0] == b[0] else '（**不同**）'}")
        # 把 1.0 行剔掉、只看 0.8333 —— 这才是「同分多实现」真正会发生的地方
        lo = sorted({r["score"] for r in rows if r["score"] < top})
        if lo:
            sub2 = [r for r in rows if r["score"] == lo[-1]]
            a2 = R._resolve_start(sub2, cell, fb, 0.5, cache, policy="ledger", tie="farlast")
            b2 = R._resolve_start(sub2, cell, fb, 0.5, cache, policy="ledger", tie="first")
            print(f"  {f} 次高分档={lo[-1]} 共 {len(sub2)} 行："
                  f"farlast→{IS(a2[0])[:8]} / first→{IS(b2[0])[:8]}"
                  f" {'（同一份）' if a2[0] == b2[0] else '（**不同**）'}")
            for r in sub2:
                print(f"      {r['impl_sha'][:8]} vec={r.get('vec')} src={r.get('source')}")

    # ---- Q6：默认档（tie=first）与 V243 之前的旧比较**逐字节等价** ----
    #  旧逻辑原样抄在这里当参照实现：`if best is None or sc > best[0]`。
    #  ⛔ 不许「看一眼觉得一样」：用合成账本穷举，比「选中的行下标」。
    def _old_pick(rows, cell):
        best = None
        for i, o in enumerate(rows or ()):
            if not isinstance(o, dict) or o.get("task_id") != cell:
                continue
            try:
                sc = float(o["score"])
            except (KeyError, TypeError, ValueError):
                continue
            if best is None or sc > best[0]:
                best = (sc, o, i)
        return None if best is None else best[2]

    def _new_pick(rows, cell):
        best, bk = None, None
        for i, o in enumerate(rows or ()):
            if not isinstance(o, dict) or o.get("task_id") != cell:
                continue
            try:
                sc = float(o["score"])
            except (KeyError, TypeError, ValueError):
                continue
            k = R._start_rank(sc, o, i, "first")
            if bk is None or k > bk:
                bk, best = k, (sc, o, i)
        return None if best is None else best[2]

    bad = []
    _scs = (0.0, 0.5, 0.8333, 1.0, None, "x")
    _vecs = (None, [True] * 6, [False] + [True] * 5, [True] * 3 + [False] + [True] * 2)
    import itertools
    for n in (1, 2, 3):                      # 行数
        for combo in itertools.product(itertools.product(_scs, _vecs), repeat=n):
            rows = []
            for sc, vc in combo:
                r = {"task_id": cell, "impl_sha": "deadbeef%08d" % len(rows)}
                if sc is not None:
                    r["score"] = sc
                if vc is not None:
                    r["vec"] = list(vc)
                rows.append(r)
            if _old_pick(rows, cell) != _new_pick(rows, cell):
                bad.append(rows)
    ck("Q6 默认档（tie=first）与旧比较在合成账本上穷举等价", not bad,
       f"不一致 {len(bad)} 例" if bad else "穷举 6^1+6^2+6^3 组合，选中行下标全同")

    # ---- Q4：默认档提示逐字节不变（跨轮锚） ----
    from mw.reflect import build_proposal_prompt, failed_actuals
    task = json.loads((V1 / "bench/evidence/l5_s266/suite_one/t01.jsonl")
                      .read_text(encoding="utf-8").splitlines()[0])
    why = ("当前最好的一次是 0.8333（共 6 条断言）。"
           "请给出**把剩下的失败断言一起修好**的完整实现。")
    p = build_proposal_prompt(task["prompt"], B, why, failed_actuals(B, task["tests"]))
    sha = hashlib.sha256(p.encode()).hexdigest()[:16]
    ck("Q4 默认档提示仍是 3b33bcb8b2276580", sha == "3b33bcb8b2276580", sha)

    # ---- Q5：非法 tie 构造即抛 ----
    bad = []
    for v in ("", "newest", None, 1):
        try:
            R._resolve_start([], cell, fb, 0.5, cache, tie=v)
            bad.append(repr(v))
        except ValueError:
            pass
    ck("Q5 非法 tie 值 ⇒ ValueError", not bad, f"漏={bad}")

    print(f"\n{'全绿' if not FAILS else '红: ' + str(FAILS)}  失败 {len(FAILS)}")
    return 0 if not FAILS else 1


if __name__ == "__main__":
    raise SystemExit(main())
