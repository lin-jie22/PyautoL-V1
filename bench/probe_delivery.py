#!/usr/bin/env python3
"""交付面探针 —— 量「通道」而不是量「能力」。

为什么要有这个探针
==================
§49 跑云端 `glm-4.7-flash` 那一轮，`attempted=10 / recorded=6`：四道题**一个字符都没交**。
通道读数里有 `抖动重试=65`、`上限被裁短=47`、空响应 5、非法 JSON 6。这些数字说明
**瓶颈在交付**，而"盲发失败"在分数上和"不会做"长得一模一样（7o 定律的通道版本）。

但「瓶颈在交付」这句话本身**不是**修法的依据：它只说"有东西在吞答案"，没说吞它的是谁。
候选有三个，修法完全不同：

  H1 **思考吃预算**：`glm-4.7-flash` 默认把 reasoning 计入 completion tokens ⇒
     4096 的额度被推理吃掉一半 ⇒ `finish_reason=="length"` ⇒ 半截答案。
     修法：请求体加 `thinking:{"type":"disabled"}`（⚠️ 要动 `mw/llm.py`）。
  H2 **就是话多**：模型本身输出长，换什么开关都会撞上限。
     修法：抬 `max_tokens`（改 env 即可，不动内核）。
  H3 **限流**：`1305/429` 每 1.5 次请求撞一次 ⇒ 重试链把墙钟烧光。
     修法：退避/降并发/换端点（与上面两条无关）。

⛔ 三条混在一起时，"加了 thinking 开关后好一点"会被误读成 H1 成立。
本探针的全部设计目的就是**把 H1 与 H2 分开**：同一个 prompt、同一个 `max_tokens`、
只换一个车身字段；再加一条 `max_tokens` 加倍的控制臂。

口径（刻意的，写死在这里而不是靠记忆）
======================================
· 消息对逐字复用直连臂：`run.py:1067` 的
  `[system=DIRECT_SYSTEM, user=task.prompt]`。**从 `run.py` 用 AST 取**，不抄字面量 ——
  抄了就会与内核漂移，而漂移的探针量的是自己的记忆。
· `temperature=0.0`（与 `--temperature` 默认一致）。
· 记录 `finish_reason` 与**完整 `usage` 原样**（不猜哪几个字段叫 reasoning）。

三条边界（⛔ 不许越过）
======================
1. **它量的是通道，不是能力**。`--run` 出来的 cells 只说明"这条路能不能把答案送到打分器"，
   不是"模型会不会做这道题"。跨 `sig` 不许并列（与 §49 同一条边界）。
2. **它不进契约层**（§46.10/§47.6）：只挂 `run_tests.sh` 的 `--self-check`，`--run` 永不进收口。
3. **不改内核**。本文件全部在 `bench/`，`harness_sha` 不变。要改 `mw/llm.py` 必须等
   `--run` 出读数并写进 DESIGN，那是下一轮的事。

用法
====
    python bench/probe_delivery.py --self-check              # 离线八组反证，不联网
    python bench/probe_delivery.py --replay                  # 用本探针的打分器重算 §49 冻结答案
    python bench/probe_delivery.py --run --tasks sg05,sg06,sg07,sg10,sg02 \\
        --out bench/evidence/deliver_s86
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SUITE = ROOT / "bench/specgen/tasks_v2.jsonl"
EVID_S85 = ROOT / "bench/evidence/zhipu_s85"

#: 预注册的三条臂 —— **只换一个车身字段**。
#: `base` 就是 §49 那一轮实际发出去的样子（没有 `thinking` 字段）。
ARMS: dict[str, dict] = {
    "base":   {"thinking": None,       "max_tokens": 4096,
               "note": "与 §49 实际口径一致：不发 thinking 字段"},
    "off":    {"thinking": "disabled", "max_tokens": 4096,
               "note": "只加 thinking.type=disabled（H1 的单变量臂）"},
    "base8k": {"thinking": None,       "max_tokens": 8192,
               "note": "只抬 max_tokens（H2 的单变量臂 / 控制臂）"},
}

#: 预注册预测 —— 跑完先对照这三条，再看别的。
PREDICTIONS = [
    "P1 若 H1 成立：`off` 的 length 次数 < `base`，且 completion_tokens 中位数明显下降",
    "P2 若 H2 成立：`base8k` 的 length 次数同样下降（与 `off` 同向）",
    "P3 若两者都不成立：length 不降 ⇒ 截断既不是思考也不是预算，本轮的三个假设全错",
]


# ---------------------------------------------------------------------------
# 口径来源：从 run.py 里取 DIRECT_SYSTEM（AST），不抄字面量
# ---------------------------------------------------------------------------
def direct_system(src: Path | None = None) -> str:
    """从 `run.py` 的 AST 里取出 `DIRECT_SYSTEM` 的值。

    ⛔ 为什么不 `from run import DIRECT_SYSTEM`：`run.py` 顶层还有一堆只为 CLI 服务的
       构造，import 它会拖着 cfg/env 一起跑；而 AST 只取一个赋值，零副作用。
    ⛔ 为什么不抄字面量：抄下来就与内核解耦，内核改了探针不知道 ⇒ 量的是探针的记忆。
    """
    p = src or (ROOT / "run.py")
    tree = ast.parse(p.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            t = node.targets[0]
            if isinstance(t, ast.Name) and t.id == "DIRECT_SYSTEM":
                # 相邻字符串字面量在 AST 里已经是 Constant(str)（隐式拼接在编译期完成）
                return ast.literal_eval(node.value)
    raise SystemExit("❌ run.py 里找不到 DIRECT_SYSTEM —— 口径来源断了，拒绝继续")


def messages_for(row: dict) -> list[dict]:
    """与 `run.py:1067` 逐字相同的消息对。"""
    return [
        {"role": "system", "content": direct_system()},
        {"role": "user", "content": str(row["prompt"])},
    ]


def body_for(arm: str, model: str, msgs: list[dict]) -> dict:
    """按臂构造请求体。**这是唯一决定"臂之间差什么"的地方**。"""
    a = ARMS[arm]
    body: dict = {
        "model": model,
        "messages": msgs,
        "max_tokens": a["max_tokens"],
        "temperature": 0.0,
    }
    if a["thinking"] is not None:
        body["thinking"] = {"type": a["thinking"]}
    return body


def body_sha(body: dict) -> str:
    raw = json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


# ---------------------------------------------------------------------------
# 传输层：只做一次 POST，重试策略在外面
# ---------------------------------------------------------------------------
class Post:
    """一次 POST 的结果。

    `transient` 的判据与 `mw/llm.py` **同一条**（429/5xx/传输层=抖动，其余=请求本身错），
    ⛔ 不另立一套：两套判据会让"重试了几次"变成不可比的读数。
    """

    def __init__(self, ok: bool, payload: dict | None = None, transient: bool = False,
                 err: str = "") -> None:
        self.ok = ok
        self.payload = payload or {}
        self.transient = transient
        self.err = err


#: 已知的"服务端忙"错误码。⛔ 这张表**只增不减**、且只放实测见过并确认可重试的码；
#: 不在这张表里、也不像限流措辞的 body 错误一律**不重试**（参数错重发只会重错）。
TRANSIENT_CODES = {1301, 1302, 1303, 1304, 1305, 1113, 1002}
_TRANSIENT_WORDS = ("限流", "并发", "繁忙", "busy", "rate", "overload", "timeout", "超时")


def _body_error(payload: dict) -> tuple[bool, str] | None:
    """HTTP 200 但 body 里带 `error` 的形态 → (是否抖动, 描述)；没有 error 则 None。

    ⚠️ 为什么必须单独处理：某些兼容端点把限流放在 **200 + body** 里回。
      不处理的话 `choices` 取不到 ⇒ 会被当成"响应结构异常"（= 不重试）⇒
       读数里看起来像"模型交了空"，实际上是**通道忙**。这是最容易冤枉模型的一种错记。
    """
    if not isinstance(payload, dict) or "error" not in payload:
        return None
    e = payload.get("error")
    code = e.get("code") if isinstance(e, dict) else None
    text = json.dumps(e, ensure_ascii=False)[:240]
    transient = (code in TRANSIENT_CODES) or any(w in text for w in _TRANSIENT_WORDS)
    return transient, f"body error {code}: {text}"


def _post(url: str, key: str, body: dict, timeout_s: float) -> Post:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        return Post(False, transient=(e.code >= 500 or e.code == 429),
                    err=f"HTTP {e.code}: {detail}")
    except Exception as e:  # noqa: BLE001  —— 超时/连接断/DNS 全是传输层抖动
        return Post(False, transient=True, err=f"{type(e).__name__}: {e}")
    be = _body_error(payload)
    if be is not None:
        return Post(False, transient=be[0], err=be[1])
    return Post(True, payload)


def send(url: str, key: str, body: dict, *, timeout_s: float, retries: int,
         backoff_s: float, poster=_post, sleep=time.sleep) -> dict:
    """带重试的一次调用 → 通道读数（**不解析载荷**，解析在 score_of 里）。

    ⛔ 重试只针对 `transient`：4xx 重发同一请求只会得到同一个错，重试只会烧墙钟
       并把"请求本身错"伪装成"抖动"（`mw/llm.py:27` 那段注释是同一条判据）。
    """
    t0 = time.time()
    attempts = 0
    errs: list[str] = []
    while True:
        attempts += 1
        r = poster(url, key, body, timeout_s)
        if r.ok:
            break
        errs.append(r.err)
        if not r.transient or attempts > retries:
            return {"ok": False, "attempts": attempts, "elapsed_s": round(time.time() - t0, 1),
                    "errors": errs, "content": "", "finish_reason": "", "usage": {}, "payload_ok": False}
        sleep(backoff_s * attempts)          # 线性退避：比指数保守，读数里能看出抖动形状
    p = r.payload
    try:
        content = p["choices"][0]["message"]["content"] or ""
        finish = p["choices"][0].get("finish_reason") or ""
    except (KeyError, IndexError, TypeError):
        return {"ok": False, "attempts": attempts, "elapsed_s": round(time.time() - t0, 1),
                "errors": errs + ["响应结构异常"], "content": "", "finish_reason": "",
                "usage": p.get("usage") or {}, "payload_ok": False}
    return {"ok": True, "attempts": attempts, "elapsed_s": round(time.time() - t0, 1),
            "errors": errs, "content": content, "finish_reason": finish,
            "usage": p.get("usage") or {}, "payload_ok": True}


def reasoning_tokens(usage: dict) -> int | None:
    """从 `usage` 里找"思考用了多少 token"。

    ⛔ 不猜字段名：实测里见过 `completion_tokens_details.reasoning_tokens`
       与顶层 `reasoning_tokens` 两种；找不到就返回 `None`（= 这条通道不报），
       **不许**拿 0 冒充"没有思考"。
    """
    for path in (("completion_tokens_details", "reasoning_tokens"), ("reasoning_tokens",)):
        cur: object = usage
        for k in path:
            if not isinstance(cur, dict) or k not in cur:
                cur = None
                break
            cur = cur[k]
        if isinstance(cur, int):
            return cur
    return None


# ---------------------------------------------------------------------------
# 打分：与 bench 真打分器同一口径（逐条断言计数 / 总条数）
# ---------------------------------------------------------------------------
def score_of(content: str, row: dict) -> dict:
    """把一段回答送进真打分器 → {extracted, cells, cells_total, code_len}。

    ⛔ 与 `bench/scorers/code_exec.py` 同一条口径：`passed = Σ run_one(code, t)`。
       自己另写一套判分就等于换了一把尺子（§45.9 混口径）。
    """
    from bench.scorers.code_exec import extract_code, run_one  # noqa: PLC0415

    tests = [str(t) for t in (row.get("tests") or [])]
    code = extract_code(content or "")
    if not code or not tests:
        return {"extracted": bool(code), "cells": 0, "cells_total": len(tests),
                "code_len": len(code)}
    passed = sum(1 for t in tests if run_one(code, t)[0])
    return {"extracted": True, "cells": passed, "cells_total": len(tests),
            "code_len": len(code)}


# ---------------------------------------------------------------------------
# 反证（离线，不联网）
# ---------------------------------------------------------------------------
def _self_check() -> int:
    bad = 0

    def ck(name: str, ok: bool, detail: str = "") -> None:
        nonlocal bad
        print(f"  {'✅' if ok else '❌'} {name}{('  ← ' + detail) if detail and not ok else ''}")
        if not ok:
            bad += 1

    print("(a) 三条臂**只差一个字段**（否则不是单变量）")
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    b = {a: body_for(a, "m", msgs) for a in ARMS}
    diff_off = {k for k in set(b["base"]) | set(b["off"]) if b["base"].get(k) != b["off"].get(k)}
    diff_8k = {k for k in set(b["base"]) | set(b["base8k"])
               if b["base"].get(k) != b["base8k"].get(k)}
    ck("base→off 只动 thinking", diff_off == {"thinking"}, str(diff_off))
    ck("base→base8k 只动 max_tokens", diff_8k == {"max_tokens"}, str(diff_8k))
    ck("三臂车身哈希互不相同", len({body_sha(v) for v in b.values()}) == 3)
    ck("off 臂的车身确实是 {'type': 'disabled'}",
       b["off"]["thinking"] == {"type": "disabled"}, repr(b["off"].get("thinking")))

    print("(b) 截断检测器有牙：`length` 必须被认出来，`stop` 必须不被认")
    ck("finish_reason=length ⇒ 记截断",
       _is_truncated({"finish_reason": "length", "ok": True}) is True)
    ck("finish_reason=stop ⇒ 不记截断",
       _is_truncated({"finish_reason": "stop", "ok": True}) is False)

    print("(c) 交付检测器有牙：空内容记 empty，有代码块不记")
    ck("content='' ⇒ empty", _is_empty({"ok": True, "content": ""}) is True)
    ck("content='  \\n ' ⇒ empty（空白也算没交）", _is_empty({"ok": True, "content": " \n "}) is True)
    ck("content 有代码块 ⇒ 不 empty",
       _is_empty({"ok": True, "content": "```python\ndef f(): pass\n```"}) is False)

    print("(d) 重试策略：429 重试，400 不重试（同一判据，不许两套）")
    seq = [Post(False, transient=True, err="HTTP 429"), Post(False, transient=True, err="HTTP 429"),
           Post(True, {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                       "usage": {}})]
    calls = {"n": 0}

    def fake429(url, key, body, to):
        calls["n"] += 1
        return seq[min(calls["n"] - 1, len(seq) - 1)]

    r = send("u", "k", {}, timeout_s=1, retries=5, backoff_s=0, poster=fake429, sleep=lambda *_: None)
    ck("429×2 后成功 ⇒ attempts=3 且 ok", r["ok"] and r["attempts"] == 3, str(r["attempts"]))

    calls["n"] = 0

    def fake400(url, key, body, to):
        calls["n"] += 1
        return Post(False, transient=False, err="HTTP 400: bad")

    r2 = send("u", "k", {}, timeout_s=1, retries=5, backoff_s=0, poster=fake400, sleep=lambda *_: None)
    ck("400 ⇒ 一次都不重试", (not r2["ok"]) and r2["attempts"] == 1, str(r2["attempts"]))
    ck("重试次数用尽 ⇒ ok=False 且带全部错误", len(r2["errors"]) == 1, str(r2["errors"]))

    print("(e) body 级错误（HTTP 200 + error）：限流码要重试，参数错不许重试")
    ck("code=1305 ⇒ 抖动",
       (_body_error({"error": {"code": 1305, "message": "并发过高"}}) or (False,))[0] is True)
    ck("措辞含「限流」⇒ 抖动（表里没这个码也认）",
       (_body_error({"error": {"code": 9999, "message": "请求限流，请稍后重试"}}) or (False,))[0] is True)
    ck("参数错 ⇒ **不**抖动",
       (_body_error({"error": {"code": 1210, "message": "参数错误: max_tokens"}}) or (True,))[0] is False)
    ck("没有 error 字段 ⇒ None（正常载荷不许被这条拦住）",
       _body_error({"choices": [{"message": {"content": "x"}}]}) is None)

    print("(f) reasoning token 抽取：没有该字段 ⇒ None（**不许**拿 0 冒充）")
    ck("无字段 ⇒ None", reasoning_tokens({"completion_tokens": 10}) is None)
    ck("details 形态能取到",
       reasoning_tokens({"completion_tokens_details": {"reasoning_tokens": 7}}) == 7)
    ck("顶层形态能取到", reasoning_tokens({"reasoning_tokens": 9}) == 9)

    print("(g) 口径来源：DIRECT_SYSTEM 取自 run.py，且不含题面特有词（无泄漏）")
    ds = direct_system()
    ck("取到了且非空", bool(ds.strip()), repr(ds[:20]))
    ck("长度与 run.py 里的字面量一致（3 句拼接）", 40 < len(ds) < 200, str(len(ds)))
    ck("不含题面词（norm_sg / 断言 / oracle）",
       not any(w in ds for w in ("norm_sg", "assert", "oracle", "def ")), ds[:40])

    print("(h) 打分器口径对齐：用本探针的 score_of 重算 §49 冻结答案，必须与记录逐题相同")
    try:
        same, detail = _replay_check()
        ck("6 题逐题相同（混口径就红）", same, detail)
    except FileNotFoundError as e:
        ck("证据目录缺失 ⇒ 拒跑（不静默跳过）", False, str(e))

    print()
    print(f"  ⇒ {'八组反证全过' if not bad else f'{bad} 组反证失败'}")
    return 0 if not bad else 1


def _is_truncated(rec: dict) -> bool | None:
    """截断判据：只有 `finish_reason == "length"` 才算。

    ⛔ `finish_reason` 空串（有的兼容端点不返回）不许猜成截断，也不许猜成正常 ⇒ `None`。
    """
    if not rec.get("ok"):
        return None
    fr = rec.get("finish_reason")
    if fr == "":
        return None
    return fr == "length"


def _is_empty(rec: dict) -> bool | None:
    if not rec.get("ok"):
        return None
    return not str(rec.get("content") or "").strip()


def _tasks() -> dict[str, dict]:
    return {str(r["id"]): r for r in (json.loads(l) for l in
                                      SUITE.read_text(encoding="utf-8").splitlines() if l.strip())}


def _replay_check() -> tuple[bool, str]:
    """重算 §49 冻结答案的 cells，与 `runs.jsonl` 里记录的逐题分数对比。

    ⚠️ `per_task` **只含 recorded=6 题**（缺的 4 题根本没进去，§49 的 `attempted=10` 是
       header 里数的）。所以对账要分两半做，且两半都必须查：
         · 在 `per_task` 里的：重算 cells 必须**逐题相同**（验打分口径）；
         · 不在里面的：答案原文必须是**空**（验"没记录"= "没交"，不是"交了被丢"）。
       ⛔ 只查前一半会把"交了却被丢"这种最坏情况漏掉。
    """
    answers = [json.loads(l) for l in
               (EVID_S85 / "answers.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    run = [json.loads(l) for l in
           (EVID_S85 / "runs.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()][-1]
    got = {str(t["id"]): int(round(float(t["score"]) * 14)) for t in run["report"]["per_task"]}
    rows = _tasks()
    same = diff = dropped = 0
    msgs: list[str] = []
    for a in answers:
        tid = str(a.get("task_id") or "")
        if not tid or a.get("kind") != "answer":
            continue
        text = str(a.get("answer") or "")
        s = score_of(text, rows[tid])
        if tid in got:
            if s["cells"] == got[tid]:
                same += 1
            else:
                diff += 1
                msgs.append(f"{tid}: 重算 {s['cells']} vs 记录 {got[tid]}")
        elif text.strip():
            dropped += 1                      # 有正文却没进 per_task ⇒ 被丢了
            msgs.append(f"{tid}: 没记录但有 {len(text)} 字正文")
    ok = diff == 0 and dropped == 0 and same >= 5
    return ok, (f"{same} 题一致 / {diff} 题不一致 / {dropped} 题被丢"
                + ("；" + "；".join(msgs[:3]) if msgs else ""))


# ---------------------------------------------------------------------------
# 真跑
# ---------------------------------------------------------------------------
def _run(args) -> int:
    base = os.environ.get("V1_LLM_BASE_URL", "").rstrip("/")
    key = os.environ.get("V1_LLM_API_KEY", "")
    model = os.environ.get("V1_LLM_MODEL", "")
    # ⛔ 缺 key 必须**显式退出**：静默降级会把"没量到"记成"0 分"（`mw/llm.py:166` 同一条）。
    if not (base and key and model):
        print("❌ 缺 V1_LLM_BASE_URL / V1_LLM_API_KEY / V1_LLM_MODEL —— 拒绝在无通道下产出读数")
        return 2
    rows = _tasks()
    ids = [t.strip() for t in args.tasks.split(",") if t.strip()]
    missing = [t for t in ids if t not in rows]
    if missing:
        print(f"❌ 题号不在 {SUITE.name} 里：{missing}")
        return 2
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    recs: list[dict] = []
    t_all = time.time()
    for tid in ids:
        row = rows[tid]
        for arm in args.arms.split(","):
            msgs = messages_for(row)
            body = body_for(arm, model, msgs)
            print(f"[{len(recs) + 1}] {tid} × {arm} …", flush=True)
            r = send(base + "/chat/completions", key, body,
                     timeout_s=args.timeout, retries=args.retries, backoff_s=args.backoff)
            s = score_of(r["content"], row)
            rec = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "task_id": tid, "arm": arm, "model": model,
                "body_sha": body_sha(body), "max_tokens": body["max_tokens"],
                "thinking": body.get("thinking"),
                "ok": r["ok"], "attempts": r["attempts"], "elapsed_s": r["elapsed_s"],
                "finish_reason": r["finish_reason"],
                "truncated": _is_truncated(r), "empty": _is_empty(r),
                "usage": r["usage"], "reasoning_tokens": reasoning_tokens(r["usage"]),
                "content_len": len(r["content"] or ""),
                "cells": s["cells"], "cells_total": s["cells_total"],
                "extracted": s["extracted"], "errors": r["errors"][:3],
            }
            recs.append(rec)
            with (out / "raw.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")   # 逐条落盘：中断也有证据
            print(f"     ok={rec['ok']} attempts={rec['attempts']} {rec['elapsed_s']}s "
                  f"finish={rec['finish_reason'] or '-'} len={rec['content_len']} "
                  f"cells={rec['cells']}/{rec['cells_total']}", flush=True)
    _report(recs, out, wall=round(time.time() - t_all, 1))
    return 0


def _report(recs: list[dict], out: Path, *, wall: float) -> None:
    arms = list(dict.fromkeys(r["arm"] for r in recs))
    print()
    print("  一、逐格读数")
    print(f"  {'题':<7}{'臂':<8}{'墙钟':>7}{'试':>4}  {'finish':<8}{'截断':>5}{'空':>4}"
          f"{'字数':>7}{'思考':>6}{'格':>8}")
    for r in recs:
        print(f"  {r['task_id']:<7}{r['arm']:<8}{r['elapsed_s']:>7.1f}{r['attempts']:>4}  "
              f"{(r['finish_reason'] or '-'):<8}{('是' if r['truncated'] else '-'):>5}"
              f"{('是' if r['empty'] else '-'):>4}{r['content_len']:>7}"
              f"{(r['reasoning_tokens'] if r['reasoning_tokens'] is not None else '-'):>6}"
              f"{str(r['cells']) + '/' + str(r['cells_total']):>8}")

    print()
    print("  二、按臂汇总（分母 = 该臂跑了几题）")
    print(f"  {'臂':<8}{'交付':>7}{'可打分':>8}{'截断':>6}{'空':>4}{'429/失败':>10}"
          f"{'墙钟中位':>10}{'输出中位':>10}{'思考中位':>10}{'格':>8}")
    agg: dict[str, dict] = {}
    for a in arms:
        rs = [r for r in recs if r["arm"] == a]
        dl = sum(1 for r in rs if r["ok"] and not r["empty"])
        ex = sum(1 for r in rs if r["extracted"])
        tr = sum(1 for r in rs if r["truncated"])
        em = sum(1 for r in rs if r["empty"])
        fail = sum(r["attempts"] - 1 for r in rs) + sum(1 for r in rs if not r["ok"])
        el = sorted(r["elapsed_s"] for r in rs)
        # ⛔ 输出中位数只用**交付成功**的格：把空响应算进中位数会让"吞答案"看起来像"话少"
        cl = sorted(r["content_len"] for r in rs if r["ok"] and not r["empty"]) or [0]
        rt = [r["reasoning_tokens"] for r in rs if r["reasoning_tokens"] is not None]
        cells = sum(r["cells"] for r in rs)
        tot = sum(r["cells_total"] for r in rs)
        agg[a] = {"delivered": dl, "n": len(rs), "extracted": ex, "truncated": tr, "empty": em,
                  "attempts_loss": fail, "wall_med": el[len(el) // 2] if el else 0,
                  "cells": cells, "cells_total": tot}
        print(f"  {a:<8}{str(dl) + '/' + str(len(rs)):>7}{str(ex) + '/' + str(len(rs)):>8}"
              f"{tr:>6}{em:>4}{fail:>10}{agg[a]['wall_med']:>10.1f}{cl[len(cl) // 2]:>10}"
              f"{(str(sorted(rt)[len(rt) // 2]) if rt else '-'):>10}"
              f"{str(cells) + '/' + str(tot):>8}")

    print()
    print("  三、假设裁决（预注册三条，先看它们）")
    for p in PREDICTIONS:
        print(f"    · {p}")
    b, o, k = agg.get("base"), agg.get("off"), agg.get("base8k")
    if b and o and k:
        h1 = o["truncated"] < b["truncated"] and o["attempts_loss"] <= b["attempts_loss"] + 1
        h2 = k["truncated"] < b["truncated"]
        print(f"    ⇒ H1(思考吃预算) {'支持' if h1 else '不支持'}："
              f"截断 {b['truncated']} → {o['truncated']}")
        print(f"    ⇒ H2(话多撞上限) {'支持' if h2 else '不支持'}："
              f"截断 {b['truncated']} → {k['truncated']}")
        if not h1 and not h2:
            print("    ⇒ 三条假设全错 ⇒ 截断既不是思考也不是预算 ⇒ **不许改内核**，先查通道报文")
    print()
    print(f"  墙钟合计 {wall}s｜记录 {len(recs)} 条 ⇒ {out / 'raw.jsonl'}")
    (out / "report.log").write_text(
        "\n".join([f"{r['task_id']}\t{r['arm']}\t{r['elapsed_s']}\t{r['attempts']}\t"
                   f"{r['finish_reason']}\t{r['content_len']}\t{r['cells']}/{r['cells_total']}"
                   for r in recs]) + "\n", encoding="utf-8")


def _control(args) -> int:
    """极小控制实验：`max_tokens=8` 让思考**必然**吃满预算。

    为什么这条比 15 次大请求更值得单独立一条：它把「思考占输出预算」这句话
    变成一个**不依赖任务、不依赖题面、20 秒可复现**的事实。
    期望形态：`content=''`、`finish_reason='length'`、`completion_tokens == reasoning_tokens`。
    ⛔ 这不是能力读数：它只证明**预算被谁吃了**。
    """
    base = os.environ.get("V1_LLM_BASE_URL", "").rstrip("/")
    key = os.environ.get("V1_LLM_API_KEY", "")
    model = os.environ.get("V1_LLM_MODEL", "")
    if not (base and key and model):
        print("❌ 缺 V1_LLM_* —— 拒绝在无通道下产出读数")
        return 2
    body = {"model": model, "messages": [{"role": "user", "content": "说一个字"}],
            "max_tokens": 8, "temperature": 0.0}
    r = send(base + "/chat/completions", key, body,
             timeout_s=args.timeout, retries=args.retries, backoff_s=args.backoff)
    rt = reasoning_tokens(r["usage"])
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "kind": "control", "model": model,
           "max_tokens": 8, "body_sha": body_sha(body), "ok": r["ok"],
           "finish_reason": r["finish_reason"], "content": r["content"],
           "content_len": len(r["content"] or ""), "usage": r["usage"],
           "reasoning_tokens": rt, "elapsed_s": r["elapsed_s"], "errors": r["errors"][:3]}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with (out / "control.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"  max_tokens=8 ⇒ content={r['content']!r} finish={r['finish_reason'] or '-'} "
          f"completion={r['usage'].get('completion_tokens')} reasoning={rt} "
          f"（{r['elapsed_s']}s）")
    if r["ok"] and r["finish_reason"] == "length" and not (r["content"] or "").strip():
        print("  ⇒ 思考把 8 个输出 token 全吃了，正文 0 字：**预算归属已确认**")
    else:
        print("  ⚠️ 形态与预期不同 —— 先查通道，不许拿这条当证据")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="交付面探针（量通道，不量能力）")
    ap.add_argument("--self-check", action="store_true", help="离线八组反证")
    ap.add_argument("--replay", action="store_true", help="重算 §49 冻结答案的分数口径")
    ap.add_argument("--run", action="store_true", help="真跑（要 key，花钱）")
    ap.add_argument("--control", action="store_true",
                    help="极小控制实验：max_tokens=8，验「思考吃满输出预算」")
    ap.add_argument("--tasks", default="sg05,sg06,sg07,sg10,sg02")
    ap.add_argument("--arms", default="base,off,base8k")
    ap.add_argument("--out", default="bench/evidence/deliver_s86")
    ap.add_argument("--timeout", type=float, default=600.0)
    ap.add_argument("--retries", type=int, default=6)
    ap.add_argument("--backoff", type=float, default=10.0)
    a = ap.parse_args()
    if a.self_check:
        return _self_check()
    if a.replay:
        ok, detail = _replay_check()
        print(f"  {'✅' if ok else '❌'} 重算与 §49 记录逐题一致：{detail}")
        return 0 if ok else 1
    if a.run:
        return _run(a)
    if a.control:
        return _control(a)
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
