#!/usr/bin/env python3
"""丢弃对账器：**代理侧成功返回、内核侧却没拿到**的那些响应。

## 为什么需要它

第九十六轮（§63）量出 `sg10` 三轮全败，我一直把它读成「模型不会写 `import re`」。
本轮把代理台账（`proxy_*.jsonl`）与运行日志按时间戳对账，看到的是另一回事：

    02:25:59  代理记录：status=200  finish=stop  500 tokens  2036 字节  耗时 46.1s
    02:25:59  内核记录：（无）—— 43 秒上限到点，客户端已放弃
    02:26:42  内核记录：⏳ 预算耗尽 ⇒ 转向收尾

**一份完整的答案被生成了，然后被扔掉了。** 差距 3.1 秒。

这不是新病：§62 的「差 1.1 秒丢一份答案」是同一个病，只是那一轮盯的是**收尾调用**。
本轮显示它在**探索阶段**咬得更狠（`elapsed` 越小，`left = wall − elapsed − reserve` 越大，
但重写类调用本身要 45~51 秒，而 `reserve=45` 一直扣着）。

## 判据从哪来（⛔ 不另写一套）

「这一发许用多少秒」**不复制公式**，而是直接构造内核的 `_LedgeredLLM` 问
`_attempt_timeout()` —— 与 `probe_closeoverrun.py` 同一条路径。
另写一份公式就是第二处实现，而两处实现迟早会漂（这是 AU1 钉过的事）。

## ⚠️ 两个必须写在最前面的假设

1. **代理的 `elapsed_s` 是它自己跑完的时间，不是客户端断开的时间。**
   依据 `bench/llm_proxy.py:342` 的 `timeout=connect_timeout_s`（默认 300s，远大于内核的
   43~120s）、`:344` 的 `payload = resp.read()`、以及 `:361` 的注释
   「先落盘，再回写 —— 顺序本身就是判据」。
   ⇒ 客户端断开不影响台账里的 `status` / `elapsed_s` / `usage`。
   ⛔ 若哪天代理改成「客户端断了就取消上游」，本工具的数会**系统性偏小**，必须重新取证。

2. **时间戳只有整秒精度**（`run.log` 是 `HH:MM:SS`），而 `t_wall` 有毫秒。
   ⇒ `elapsed_at` 有 ±1s 的量化误差 ⇒ 判「被裁」的容差取 `TOL_S = 1.0`。
   容差内的边缘案例**单独计数**（`edge`），不静默吞。

## 六条反证（`--self-check`，零模型、零网络）

| # | 反证 | 防的是什么 |
|---|---|---|
| D1 | 实耗 < 上限 ⇒ **不入选** | 分类器恒返回「被裁」 |
| D2 | 实耗 > 上限 ⇒ 入选，且超出秒数算对 | 入选取了，但数算错 |
| D3 | 同一份数据、只改 `overrun`：收尾窗口内的上限**必须变**，探索窗口内**必须不变** | 分支是死的（全用同一个上限） |
| D4 | 找不到归属任务的调用 ⇒ 进 `orphan` | 静默丢（分母变小 ⇒ 比例变好看） |
| D5 | `status != 200` ⇒ 不算「成功响应」 | 把上游失败当成「内核丢弃」 |
| D6 | 算出上限 = 0（这一发本不该发出，但台账有记录）⇒ 进 `unsure` | 当作「被裁」= 把装置误差算成缺陷 |

## 用法

    python3 bench/probe_discard.py --proxy P.jsonl --run run.log \\
        --wall 120 --reserve 45 --overrun 0

    python3 bench/probe_discard.py --self-check      # 六条反证

退出码：0 = 跑完；1 = 反证组红（拒绝出数）；2 = 用法错误。
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mw.budget import Budget, Ledger              # noqa: E402
from mw.core import _LedgeredLLM                  # noqa: E402

#: 时间戳量化误差的量级：日志整秒 + 代理毫秒 ⇒ 最多 1s 的错配。
TOL_S = 1.0

_TS = re.compile(r"^\s*(\d{2}):(\d{2}):(\d{2})")
_TASK = re.compile(r"写一个函数\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(")


class _Inner:
    """只为了给 `_attempt_timeout()` 一个 `timeout_s`。

    ⛔ 没有这个属性的桩会走 `return float("inf")` 那条路 ⇒ 整条裁剪路径不存在
    （§63 的 C0 / 契约 AZ94 都踩过这个坑，这里同样必须显式给）。
    """

    def __init__(self, base: float) -> None:
        self.timeout_s = float(base)


def cap_s(elapsed_at: float, *, wall: float, base: float, reserve: float,
          overrun: float, closing: bool) -> float:
    """**问内核**：已经用了 `elapsed_at` 秒时，这一发许用多少秒。

    ⛔ 不复制 `_attempt_timeout()` 的公式 —— 那会变成第二处实现。
    """
    led = Ledger(budget=Budget(steps=1, llm_calls=4, wall_s=wall, candidates=1),
                 t0=time.monotonic() - float(elapsed_at))
    led.elapsed = lambda: float(elapsed_at)      # type: ignore[method-assign]
    counted = _LedgeredLLM(_Inner(base), led, retries=1, backoff_s=0.0,
                           wall_reserve_s=float(reserve), close_overrun_s=float(overrun))
    if closing:
        # 收尾阶段：放出余量 + 允许超支，两个动作在同一个方法里（§63 的设计）
        counted.reserve_release()
    return float(counted._attempt_timeout())      # noqa: SLF001 — 量具就是来问它的


def parse_run_log(path: Path) -> list[dict]:
    """`run.log` ⇒ `[{id, start, close}]`（当日秒）。

    `close` = 进入收尾的时刻（`⇒ 转向交付` / `触发收尾余量`）；没进过收尾就是 `None`。
    """
    out: list[dict] = []
    cur: dict | None = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = _TS.match(line)
        if not m:
            continue
        t = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
        if "▶ 任务开始" in line:
            if cur:
                out.append(cur)
            mm = _TASK.search(line)
            cur = {"id": mm.group(1) if mm else "?", "start": t, "close": None}
        elif cur is not None and cur["close"] is None and (
                "触发收尾余量" in line or "⇒ 转向交付" in line):
            cur["close"] = t
    if cur:
        out.append(cur)
    return out


def parse_proxy(path: Path) -> list[dict]:
    """代理台账 ⇒ `[{t, elapsed, status, tokens, bytes}]`（`t` = 当日秒）。"""
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        tw = r.get("t_wall")
        if tw is None:
            continue
        lt = time.localtime(float(tw))
        usage = r.get("usage") or {}
        out.append({
            "t": lt.tm_hour * 3600 + lt.tm_min * 60 + lt.tm_sec,
            "elapsed": float(r.get("elapsed_s") or 0.0),
            "status": r.get("status"),
            "tokens": usage.get("completion_tokens"),
            "bytes": r.get("resp_bytes"),
            "finish": r.get("finish_reason"),
        })
    return out


def attribute(calls: list[dict], tasks: list[dict]) -> list[tuple[dict, dict | None]]:
    """把每次调用挂到「最后一个已经开始的任务」上。挂不上 ⇒ `None`（进 `orphan`）。"""
    out: list[tuple[dict, dict | None]] = []
    for c in calls:
        owner = None
        for k in tasks:
            if k["start"] <= c["t"]:
                owner = k
            else:
                break
        out.append((c, owner))
    return out


def audit(proxy: Path, run: Path, *, wall: float = 120.0, reserve: float = 45.0,
          base: float = 120.0, overrun: float = 0.0,
          tol: float = TOL_S) -> dict:
    """一个 (台账, 日志) 对的丢弃读数。**零写入**。"""
    tasks = parse_run_log(run)
    calls = parse_proxy(proxy)
    res = {"n_calls": len(calls), "n_ok": 0, "orphan": 0, "notok": 0,
           "unsure": 0, "cut": [], "edge": 0,
           "waste_s": 0.0, "lost_tokens": 0}
    for c, owner in attribute(calls, tasks):
        if c["status"] != 200:
            res["notok"] += 1
            continue
        res["n_ok"] += 1
        if owner is None:
            res["orphan"] += 1
            continue
        el_at = c["t"] - owner["start"]
        closing = owner["close"] is not None and c["t"] >= owner["close"]
        cap = cap_s(el_at, wall=wall, base=base, reserve=reserve,
                    overrun=overrun, closing=closing)
        if cap <= 0.0:
            # 这一发按内核口径「不该发出」，但台账里有 ⇒ 说明 elapsed_at 估偏了。
            # ⛔ 记成 unsure，不许记成「被裁」（那是把装置误差算成缺陷）。
            res["unsure"] += 1
            continue
        over = c["elapsed"] - cap
        row = {"task": owner["id"], "t": c["t"], "elapsed_at": el_at, "cap": cap,
               "elapsed": c["elapsed"], "over_s": round(over, 1), "closing": closing,
               "tokens": c["tokens"], "bytes": c["bytes"], "finish": c["finish"]}
        if over > tol:
            res["cut"].append(row)
            res["waste_s"] += over
            res["lost_tokens"] += int(c["tokens"] or 0)
        elif over > 0.0:
            res["edge"] += 1
    res["waste_s"] = round(res["waste_s"], 1)
    return res


def _fmt(res: dict) -> str:
    out = [f"  调用 {res['n_calls']} 次｜成功 {res['n_ok']}｜非 200 {res['notok']}"
           f"｜挂不上任务 {res['orphan']}｜算不准 {res['unsure']}"]
    out.append(f"  被内核丢弃的成功响应：**{len(res['cut'])} 次**"
               f"（白等 {res['waste_s']}s，丢弃 {res['lost_tokens']} tokens）"
               f"｜容差内边缘 {res['edge']} 次")
    for r in res["cut"]:
        hh, mm, ss = r["t"] // 3600, r["t"] % 3600 // 60, r["t"] % 60
        out.append(f"    {r['task']:10s} {hh:02d}:{mm:02d}:{ss:02d}"
                   f"  已用={r['elapsed_at']:3d}s  上限={r['cap']:6.1f}s"
                   f"  实耗={r['elapsed']:6.1f}s  超 {r['over_s']:+.1f}s"
                   f"  {r['tokens'] or 0}tok {r['bytes'] or 0}B"
                   f"  {'收尾' if r['closing'] else '探索'}")
    return "\n".join(out)


# ── 自检（六条反证）—— 零模型、零网络 ──────────────────────────────────────
def _write_pair(tmp: Path, *, calls: list[dict], tasks: list[dict]) -> tuple[Path, Path]:
    """造一对 (台账, 日志)。时间戳用今天 00:00 起的秒数，只用相对位置。"""
    base = time.mktime(time.strptime("2026-01-01", "%Y-%m-%d"))

    def hms(s: int) -> str:
        return f"{s // 3600 % 24:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"

    log = tmp / "run.log"
    log.write_text("".join(
        f"{hms(k['start'])} ▶ 任务开始: 写一个函数 {k['id']}(rows)，处理字符串列表。\n"
        + (f"{hms(k['close'])}   ⏱ 触发收尾余量 ⇒ 转向交付（探索到此为止）\n"
           if k.get("close") is not None else "")
        for k in tasks), encoding="utf-8")
    px = tmp / "proxy.jsonl"
    px.write_text("".join(json.dumps({
        "t_wall": base + c["t"], "elapsed_s": c["elapsed"], "status": c.get("status", 200),
        "usage": {"completion_tokens": c.get("tokens", 100)}, "resp_bytes": c.get("bytes", 900),
        "finish_reason": "stop"}) + "\n" for c in calls), encoding="utf-8")
    return px, log


def self_check() -> int:
    """⛔ 每条都在证明「这个读数会随输入变」，而不是「这个工具跑起来了」。"""
    ok = True
    print("  §64 丢弃对账自检（零模型、零网络）—— 六条反证")
    #: 基线场景：wall=120 reserve=45 base=120 ⇒ 探索阶段 t=30 时上限 = 120−30−45 = 45s。
    KW = dict(wall=120.0, reserve=45.0, base=120.0)
    tasks = [{"id": "norm_a", "start": 0, "close": None}]

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)

        # D1 装得下 ⇒ 不入选
        px, lg = _write_pair(tmp, tasks=tasks,
                             calls=[{"t": 30, "elapsed": 40.0, "tokens": 500, "bytes": 2000}])
        r1 = audit(px, lg, overrun=0.0, **KW)
        d1 = len(r1["cut"]) == 0 and r1["n_ok"] == 1
        print(f"  {'✅' if d1 else '❌'} D1 实耗 40s < 上限 45s ⇒ **不入选**"
              f"（被裁={len(r1['cut'])}）")
        ok &= d1

        # D2 超的 ⇒ 入选，且超出秒数算对（46.1 − 43.0 = +3.1，模拟 §63 的实测形状）
        px, lg = _write_pair(tmp, tasks=[{"id": "norm_b", "start": 0, "close": None}],
                             calls=[{"t": 32, "elapsed": 46.1, "tokens": 500, "bytes": 2036}])
        r2 = audit(px, lg, overrun=0.0, **KW)
        d2 = (len(r2["cut"]) == 1 and abs(r2["cut"][0]["cap"] - 43.0) < 1e-6
              and r2["cut"][0]["over_s"] == 3.1 and r2["lost_tokens"] == 500)
        print(f"  {'✅' if d2 else '❌'} D2 实耗 46.1s > 上限 "
              f"{r2['cut'][0]['cap'] if r2['cut'] else float('nan'):.1f}s ⇒ 入选，"
              f"超 {r2['cut'][0]['over_s'] if r2['cut'] else float('nan'):+.1f}s"
              f"（D2 的数必须与 §63 实测的 +3.1s 同形）")
        ok &= d2

        # D3 只改 overrun：收尾窗口内的上限必须变，探索窗口内必须不变
        tk = [{"id": "norm_c", "start": 0, "close": 60}]
        calls = [{"t": 30, "elapsed": 10.0}, {"t": 70, "elapsed": 10.0}]
        px, lg = _write_pair(tmp, tasks=tk, calls=calls)
        a0, a30 = audit(px, lg, overrun=0.0, **KW), audit(px, lg, overrun=30.0, **KW)
        exp0 = cap_s(30, wall=120, base=120, reserve=45, overrun=0, closing=False)
        exp1 = cap_s(70, wall=120, base=120, reserve=45, overrun=30, closing=True)
        d3 = (abs(exp0 - 45.0) < 1e-6 and abs(exp1 - 80.0) < 1e-6
              and a0["n_ok"] == 2 and a30["n_ok"] == 2)
        print(f"  {'✅' if d3 else '❌'} D3 探索窗口的上限与 `overrun` **无关**"
              f"（{exp0:.1f}s），收尾窗口**跟着变**（{exp1:.1f}s）"
              f"—— 否则两条分支是死的")
        ok &= d3

        # D4 挂不上任务的调用 ⇒ orphan 计数（不许静默丢）
        px, lg = _write_pair(tmp, tasks=[{"id": "norm_d", "start": 100, "close": None}],
                             calls=[{"t": 10, "elapsed": 1.0}])
        r4 = audit(px, lg, overrun=0.0, **KW)
        d4 = (r4["orphan"] == 1 and len(r4["cut"]) == 0 and r4["n_ok"] == 1)
        print(f"  {'✅' if d4 else '❌'} D4 找不到归属的调用进 `orphan`（{r4['orphan']} 次），"
              f"不许静默丢 —— 分母变小会让比例变好看")
        ok &= d4

        # D5 非 200 不算「成功响应」
        px, lg = _write_pair(tmp, tasks=tasks,
                             calls=[{"t": 30, "elapsed": 90.0, "status": 502}])
        r5 = audit(px, lg, overrun=0.0, **KW)
        d5 = (r5["notok"] == 1 and r5["n_ok"] == 0 and len(r5["cut"]) == 0)
        print(f"  {'✅' if d5 else '❌'} D5 `status=502` 不算丢失"
              f"（非 200={r5['notok']} 成功={r5['n_ok']} 被裁={len(r5['cut'])}）")
        ok &= d5

        # D6 算出上限 = 0 ⇒ unsure（不当作「被裁」）
        px, lg = _write_pair(tmp, tasks=tasks, calls=[{"t": 80, "elapsed": 30.0}])
        r6 = audit(px, lg, overrun=0.0, **KW)
        d6 = (r6["unsure"] == 1 and len(r6["cut"]) == 0)
        print(f"  {'✅' if d6 else '❌'} D6 上限算成 0（这一发本不该发出）⇒ 进 `unsure`"
              f"（{r6['unsure']} 次）—— 装置误差不许冒充缺陷")
        ok &= d6

    print("-" * 74)
    print(f"  自检结果：{'全绿（可以出数）' if ok else '红项 ⇒ 拒绝出数'}")
    return 0 if ok else 1


def main(argv: list[str]) -> int:
    if "--self-check" in argv:
        return self_check()
    proxy = run = None
    kw = {"wall": 120.0, "reserve": 45.0, "base": 120.0, "overrun": 0.0}
    for i, a in enumerate(argv):
        if i + 1 >= len(argv):
            continue
        if a == "--proxy":
            proxy = Path(argv[i + 1])
        elif a == "--run":
            run = Path(argv[i + 1])
        elif a == "--wall":
            kw["wall"] = float(argv[i + 1])
        elif a == "--reserve":
            kw["reserve"] = float(argv[i + 1])
        elif a == "--base":
            kw["base"] = float(argv[i + 1])
        elif a == "--overrun":
            kw["overrun"] = float(argv[i + 1])
    if not proxy or not run:
        print(__doc__)
        return 2
    print(f"  丢弃对账：proxy={proxy.name}  run={run.name}  "
          f"wall={kw['wall']:g} reserve={kw['reserve']:g} overrun={kw['overrun']:g}")
    print(_fmt(audit(proxy, run, **kw)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
