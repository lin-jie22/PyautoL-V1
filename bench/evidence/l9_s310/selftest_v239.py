#!/usr/bin/env python3
"""V239（§310）装置自检 —— **零模型、零网络**，只验产品代码的四条判据。

判据映射（PREREG_v239 §3）：
  T1 ↔ P1  起点真的换成账本 best 的**实现**（不是只改文案）
  T2 ↔ P3  取不到实现 ⇒ 可见降级（note + why 改口），⛔ 不静默顶替
  T3 ↔ P2  why 里的数字与它给出的**代码**同源（字符串级）
  T4 ↔ P4  `impl_sha` 一列一个口径（用**真实的**「原文≠剥后」样本）
  T5       默认档（`start_src="act"`）文案与 V235~V238 逐字节相同 —— 历史可重放
  T6       缓存往返：落盘→按 sha 取回→同值；空代码不落盘（⛔ 不许用空串冒充"取到了"）

⛔ 不测「best 会不会涨」：那是判读，不是装置。
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve()
#: ⚠️ 必须是 `parents[3]`：`__file__` 是**文件**，`parents[0]` 已是 `l9_s310/`。
#:    （V238 踩过：`python3 -c` 的 `sys.path[0]` 是 `''`＝cwd，换脚本跑就差一格。）
sys.path.insert(0, str(HERE.parents[3]))

import run as R                                                    # noqa: E402
from mw.bench import Suite                                          # noqa: E402
from mw.config import LLMConfig                                     # noqa: E402
from mw.scoreinfo import exec_sha_of, impl_sha_of                   # noqa: E402

EVID = HERE.parent
SUITE = EVID.parent / "l5_s266" / "suite_one" / "t01.jsonl"
P6_RAW = EVID.parent / "l8_s309" / "raw_v238" / "P6_7b_think0.txt"
BARE = EVID.parent / "l6_s307" / "start_answer.py"

RES: list[tuple[str, bool, str]] = []


def ck(tag: str, ok: bool, detail: str = "") -> None:
    RES.append((tag, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {tag}" + (f"  ｜ {detail}" if detail else ""))


# ---------------------------------------------------------------------------
# 假装置：只为把 prompt 逼出来，不产生任何分数结论
# ---------------------------------------------------------------------------
class _Scored:
    def __init__(self, score: float, vec: list[bool]):
        self.score, self.vec = float(score), list(vec)


class FakeScorer:
    """按代码内容给分：`START_SCORE` 那份给固定分，其余给 0.0。

    ⛔ 不给"看起来合理"的分：本自检只关心 **why 里的数字与代码是否同源**，
       共变的分会让同源检查失去判别力。
    """

    def __init__(self, code_scores: dict[str, float]):
        self.code_scores = {exec_sha_of(k): v for k, v in code_scores.items()}

    def score(self, task_dict, cand):                       # noqa: ARG002
        sc = self.code_scores.get(exec_sha_of(cand), 0.0)
        return _Scored(sc, [sc >= 0.9] * 6)


class FakeLLM:
    """回一份固定代码；顺便把入参 prompt 记下来（判据全在 prompt 里）。"""

    def __init__(self, out_code: str, sink: list[str]):
        self.out_code, self.sink = out_code, sink

    def complete(self, messages, max_tokens=900):           # noqa: ARG002
        self.sink.append(messages[-1].content)
        return "```python\n" + self.out_code + "\n```"


class _S:
    def __init__(self, cfg):
        self.llm = cfg


class FakeCore:
    def __init__(self):
        self.s = _S(LLMConfig(model="fake", offline=True, api_key="x"))


def _task():
    return Suite.load(SUITE).tasks[0]


def _run_swap(start_code: str, start_score: float, *, src: str | None,
              hist: float | None, cache: Path, code_scores: dict[str, float]):
    """跑一次 `_swap_candidates`，返回 `(prompts, rows)`。

    ⚠️ patch 的是 `mw.llm.build_llm` 而**不是** `run._bllm`：后者是
    `_swap_candidates` 函数体内的**局部** import（`from mw.llm import build_llm as _bllm`），
    模块上没有这个名字。踩过一次：`AttributeError: module 'run' has no attribute '_bllm'`。
    """
    from mw import llm as llm_mod
    prompts: list[str] = []
    old = llm_mod.build_llm
    llm_mod.build_llm = lambda _cfg: FakeLLM(start_code, prompts)      # noqa: ARG005
    try:
        kw = {} if src is None else {"start_src": src, "start_hist": hist}
        _c, _s, rows = R._swap_candidates(
            FakeCore(), FakeScorer(code_scores), _task(),
            start_code, start_score, 1, **kw)
    finally:
        llm_mod.build_llm = old
    return prompts, rows


def main() -> int:
    task = _task()
    start = BARE.read_text(encoding="utf-8")
    ssha = exec_sha_of(start)
    #: 假的分：让「起点分」与「账本分」可区分（0.3333 vs 0.8333）
    scores = {start: 0.8333}

    with tempfile.TemporaryDirectory() as td:
        cache = Path(td) / "cand"

        # ── T6 缓存往返 ────────────────────────────────────────────────
        sha = R._cand_put(cache, start)
        got = R._cand_get(cache, sha)
        empty_sha = R._cand_put(cache, "")
        ck("T6 缓存：落盘→按 sha 取回→逐字节同；空代码不落盘、返回空串",
           sha == ssha and got == start and empty_sha == ""
           and R._cand_get(cache, "") is None
           and R._cand_get(cache, "deadbeefdeadbeef") is None,
           f"sha={sha} got={'same' if got == start else 'DIFF'} empty={empty_sha!r}")

        # ── T1 起点 = 账本 best 的实现（cache 分支）────────────────────
        obs = [{"task_id": task.id, "score": 0.8333, "impl_sha": ssha}]
        code, score, src, note = R._resolve_start(obs, task.id, "def f():\n    pass\n",
                                                  0.3333, cache)
        ck("T1 起点替换：账本 best 带 sha 且缓存里有 ⇒ 起点=**那份代码**与**那份分**",
           src == "cache" and code == start and abs(score - 0.8333) < 1e-9 and note == "",
           f"src={src} code_sha={exec_sha_of(code)} score={score} note={note!r}")

        # ── T3 why 与代码同源（真判分口径）────────────────────────────
        prompts, _rows = _run_swap(code, score, src=src, hist=0.8333,
                                  cache=cache, code_scores=scores)
        p = prompts[0] if prompts else ""
        ck("T3 同源：why 里的数字 == 对**起点代码**真判分的值（0.8333）",
           "当前最好的一次是 0.8333" in p,
           p.splitlines()[0][:60] if p else "no prompt")

        # ── T5 默认档文案逐字节不变（历史可重放）─────────────────────
        # ⚠️ 比的**不是** cache 分支那份（它起点不同，本来就不该同）。
        #    要证的是「不传新参数」与「传 start_src="act"」逐字节相同 ——
        #    即新参数**默认关**，V235~V238 的调用形式原样可重放。
        prompts2, _ = _run_swap("def f():\n    pass\n", 0.3333, src="act", hist=None,
                                cache=cache, code_scores=scores)
        p2 = prompts2[0] if prompts2 else ""
        #: 旧调用形式（V235~V238 的签名：**不传** start_src / start_hist）
        old_prompts, _ = _run_swap("def f():\n    pass\n", 0.3333, src=None,
                                   hist=None, cache=cache, code_scores=scores)
        p_old = old_prompts[0] if old_prompts else ""
        ck("T5 默认档：不传新参数 与 `start_src=\"act\"` 的 prompt **逐字节相同**"
           "⇒ 新参数默认关，历史可重放",
           p_old != "" and p_old == p2,
           f"len={len(p2)}" if p_old == p2 else f"len {len(p_old)} vs {len(p2)}")
        # ── T7 跨轮逐字节：默认档 prompt 的 sha 必须 == V236/V238 的 3b33bcb8 ──
        # ⛔ 这条才是「历史可重放」的证据。T5 只证了「新旧调用形式一致」，
        #    它们都在**新代码**里跑过一遍 —— 自己跟自己比，抓不到「默认档被悄悄改了」。
        from mw.reflect import sha_of as _sh
        p_hist, _ = _run_swap(start, 0.8333, src="act", hist=None,
                              cache=cache, code_scores=scores)
        _sha_now = _sh(p_hist[0]) if p_hist else ""
        ck("T7 跨轮逐字节：act 分支 + start_answer.py + 0.8333 ⇒ prompt sha "
           "== V236/V238 的 `3b33bcb8b2276580`",
           _sha_now == "3b33bcb8b2276580",
           f"sha={_sha_now or 'none'}")

        #: 顺带钉住「默认档的字面文案」——避免有人悄悄改措辞（V96 已否证改措辞）。
        ck("T5b 默认档文案字面：仍以「当前最好的一次是 0.3333」开头",
           p2.startswith("下面这个 Python 函数") and "当前最好的一次是 0.3333" in p2,
           p2.splitlines()[0][:40] if p2 else "no prompt")

        # ── T2 取不到实现 ⇒ 可见降级 ─────────────────────────────────
        obs2 = [{"task_id": task.id, "score": 0.8333}]        # 无 impl_sha
        code2, score2, src2, note2 = R._resolve_start(obs2, task.id, "def f():\n    pass\n",
                                                     0.3333, cache)
        prompts3, _ = _run_swap(code2, score2, src=src2, hist=0.8333,
                                cache=cache, code_scores=scores)
        p3 = prompts3[0] if prompts3 else ""
        ck("T2 降级：账本 best 无 sha ⇒ 返回本轮产出 + note 点名"
           "「取不回来」，且 why **改口成两句**（⛔ 不再出现「当前最好的一次是 0.8333」）",
           src2 == "degraded" and code2.startswith("def f():") and note2 != ""
           and "取不回来" in note2 and "历史最好成绩是 0.8333" in p3
           and "当前最好的一次是 0.8333" not in p3,
           f"src={src2} note={note2[:40]}…")

        # ── T11 落盘内容与文件名**同口径**（带围栏的输入 ⇒ 盘上是裸代码）──
        # ⛔ 这条是补上来的：首版 12/12 全过，却没测「文件内容的口径」。
        #    实测 `cand/09d0e23d.py` 里躺着带 ```python 围栏的原文，
        #    而文件名是剥后的 sha ⇒ 取回来当 best_code 会往 prompt 塞一对围栏。
        _fenced = "```python\n" + start + "\n```"
        _fsha = R._cand_put(cache, _fenced)
        _fpath = cache / f"{_fsha}.py"
        _ftxt = _fpath.read_text(encoding="utf-8") if _fpath.exists() else ""
        ck("T11 缓存口径：带围栏的输入落盘后是**裸代码**，且盘上内容的 sha == 文件名",
           _fsha == ssha and _ftxt == start and "```" not in _ftxt
           and exec_sha_of(_ftxt) == _fpath.stem,
           f"sha={_fsha} fenced_in_file={'```' in _ftxt}")

        # ── T8 `ledger` 策略：同分也换（这是把「固定起点」显式化的那一维）──
        # 背景（V239 R1 实测）：`auto` 在同分时用本轮产出，而「同分不同体」是
        # 这道题的**常态** ⇒ auto 结构上测不到「起点固定为某个已知实现」。
        _lc, _ls, _lsrc, _ln = R._resolve_start(
            [{"task_id": task.id, "score": 0.8333, "impl_sha": ssha}],
            task.id, "def f():\n    pass\n", 0.8333, cache, policy="ledger")
        _ac, _as_, _asrc, _an = R._resolve_start(
            [{"task_id": task.id, "score": 0.8333, "impl_sha": ssha}],
            task.id, "def f():\n    pass\n", 0.8333, cache, policy="auto")
        ck("T8 `ledger` vs `auto` 同分分歧：ledger ⇒ src=cache 且拿到**那份代码**；"
           "auto ⇒ src=act（用本轮产出）",
           _lsrc == "cache" and _lc == start and abs(_ls - 0.8333) < 1e-9
           and _asrc == "act" and _ac.startswith("def f():"),
           f"ledger={_lsrc} auto={_asrc}")

        # ── T9 `ledger` 不许用**更差**的账本实现（否则「改进」变容易）────
        _wc, _ws, _wsrc, _wn = R._resolve_start(
            [{"task_id": task.id, "score": 0.5000, "impl_sha": ssha}],
            task.id, "def f():\n    pass\n", 0.8333, cache, policy="ledger")
        ck("T9 边界：账本 best=0.5 < 本轮 0.8333 ⇒ ledger 也**不换**（退回 act）"
           "—— ⛔ 用更差的起点会让「更好」变容易",
           _wsrc == "act" and _wc.startswith("def f():") and abs(_ws - 0.8333) < 1e-9,
           f"src={_wsrc} score={_ws}")

        # ── T10 非法策略构造即抛（⛔ 拼错静默退回 auto = 以为固定了起点）──
        try:
            R._resolve_start([], task.id, "x", 0.0, cache, policy="ledgr")
            _bad = False
        except ValueError:
            _bad = True
        ck("T10 非法 `start_from` **直接抛**：拼错不许静默退回 auto",
           _bad, f"raised={_bad}")

        # ── T2b 起点降级的分母：本轮分等于账本分 ⇒ 不算降级 ─────────
        obs3 = [{"task_id": task.id, "score": 0.3333}]
        _c3, _s3, src3, note3 = R._resolve_start(obs3, task.id, "def f():\n    pass\n",
                                                 0.3333, cache)
        ck("T2b 边界：账本 best == 本轮产出分 ⇒ 走普通路径（src=act，不报降级）",
           src3 == "act" and note3 == "", f"src={src3} note={note3!r}")

    # ── T4 口径唯一（用真实样本，⛔ 不用手造字符串）──────────────────
    raw = P6_RAW.read_text(encoding="utf-8")
    bare = BARE.read_text(encoding="utf-8")
    ck("T4 口径：同一份围栏产出 原文≠剥后（`2bcccf94` vs `90579ece`），"
       "裸代码两种算法同值 ⇒ 修后只记**剥后**那个",
       impl_sha_of(raw) != exec_sha_of(raw)
       and exec_sha_of(raw) == exec_sha_of(bare) == impl_sha_of(bare),
       f"raw={impl_sha_of(raw)[:8]} exec={exec_sha_of(raw)[:8]} "
       f"bare={impl_sha_of(bare)[:8]}")

    ok = sum(1 for _t, o, _d in RES if o)
    print(f"\n=== selftest_v239 {ok}/{len(RES)} ===")
    (EVID / "selftest_v239.json").write_text(
        json.dumps([{"tag": t, "ok": o, "detail": d} for t, o, d in RES],
                   ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if ok == len(RES) else 1


if __name__ == "__main__":
    raise SystemExit(main())
