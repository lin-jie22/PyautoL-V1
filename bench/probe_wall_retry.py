#!/usr/bin/env python3
"""重试链守墙钟的**正式探针**（第二十五轮，DESIGN §16.24）。

## 它回答的唯一问题

`timeout_s=120` + `llm_retries=2` + `wall_s=120` 时，**一次逻辑调用**到底能烧多久？

第二十四轮实测：361~362 秒（`3 × 120 + 退避 1.5`），而 `wall_s` 也是 120。
日志上只留一个 `LLMError: TimeoutError` 与 `steps=0`，读起来像"模型太慢"，
实际是**重试链把预算超支了 3 倍** —— 实现违反了自己声明的硬上限。

## 为什么必须有**对照臂**（而不只是"新逻辑跑得快"）

"跑得快"本身不是证据：任意把 timeout 调小都能让它变快，而那会一并改掉
能力读数（`timeout_s` 就在口径签名里）。本探针的对照臂**逐字复刻 HEAD
的旧语义**（固定 `cfg.timeout_s`、重试里不查墙钟），两臂吃到的是同一个慢端点、
同一份目标数字。只有"旧/新 ≥ 2"才说明修的不是参数而是**超支**。

## 判据（跑之前写死，见 DESIGN §16.24 ⑥）

  R1  新逻辑耗时 ≤ `wall_s + 5`            ⇒ 单次逻辑调用再也吃不穿整题预算
  R1b 旧 / 新 ≥ 2                          ⇒ 修复前确实随重试次数线性膨胀
  R1c 抛异常后剩余 ≥ `close_reserve_s − 1`  ⇒ 收尾余量还在（不是"准时归零"）

⛔ R1c 的 `−1` 是**实测出来的**：裁剪目标是 `wall_s − reserve` 秒，而超时
   检测本身有 ~0.5s 开销（连接建立 + `urlopen` 判定超时）⇒ 实测剩余
   44.5s 而非 45.0s。收尾判据用的是「剩余 > 一次起步价（5s）」而不是
   「剩余 ≥ 45s」，所以这 0.5s 不改变行为 —— 但**必须在这里写出来**，
   否则下一个人会以为 44.5 是个 bug。（第一版断言用 `>=` 无容差，
   第一次跑就"失败"，而失败的是断言不是实现。）

## 用法

    python3 bench/probe_wall_retry.py            # 8s/20s 小档（约 10 秒）
    python3 bench/probe_wall_retry.py --real     # 事故原参数 120/120（约 75 秒）

⚠️ 不用 `--real` 也能证明同一件事：两臂的比例是 `(retries+1) : 1`，
   与绝对秒数无关。`--real` 的意义只是"和 361s 那次事故逐秒对账"。
"""
from __future__ import annotations

import argparse
import http.server
import json
import socketserver
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

V1 = Path(__file__).resolve().parent.parent
if str(V1) not in sys.path:
    sys.path.insert(0, str(V1))

from mw.budget import Budget, BudgetExhausted, Ledger            # noqa: E402
from mw.config import LLMConfig                                  # noqa: E402
from mw.core import _LedgeredLLM                                 # noqa: E402
from mw.llm import HTTPLLM, Message                              # noqa: E402

HITS: list[float] = []


class _Slow(http.server.BaseHTTPRequestHandler):
    """每次都超时的端点：`DELAY` 秒后才应答（远大于两臂的任何 timeout）。"""

    delay = 30.0

    def do_POST(self) -> None:                                   # noqa: N802
        HITS.append(time.time())
        n = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(n)
        time.sleep(self.delay)
        body = b'{"choices":[{"message":{"content":"ok"}}]}'
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            # 客户端已经超时走了 —— 这是本探针的**预期**路径，不是缺陷。
            pass

    def log_message(self, *a) -> None:                            # noqa: ANN002
        pass


def _serve(delay: float):
    _Slow.delay = delay
    socketserver.TCPServer.allow_reuse_address = True
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Slow)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def old_style(cfg: LLMConfig, ledger: Ledger, retries: int) -> str:
    """**逐字复刻 HEAD 的旧语义**：固定 `cfg.timeout_s`、重试里不查墙钟。

    ⛔ 这是对照臂，不是"参考实现"。它必须**故意**保持缺陷，否则对照就失去意义
    （把对照臂也修好，等于自问自答）。
    """
    last = "?"
    for attempt in range(retries + 1):
        ledger.spend_llm()
        req = urllib.request.Request(
            cfg.base_url.rstrip("/") + "/chat/completions",
            data=json.dumps({"model": cfg.model,
                             "messages": [{"role": "user", "content": "hi"}],
                             "max_tokens": 16, "temperature": 0}).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {cfg.api_key}"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=cfg.timeout_s) as r:
                return json.loads(r.read().decode())
        except Exception as e:                                    # noqa: BLE001
            last = type(e).__name__
            if attempt >= retries:
                break
            time.sleep(0.5 * (attempt + 1))       # 线性退避，与实现同参
    return last


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="重试链守墙钟的正式探针（§16.24）")
    ap.add_argument("--real", action="store_true",
                    help="用事故原参数（timeout_s=wall_s=120, reserve=45）；约 75s")
    ap.add_argument("--retries", type=int, default=2)
    args = ap.parse_args(argv)

    if args.real:
        TO, WALL, RES = 120.0, 120.0, 45.0
        DELAY = 200.0
    else:
        TO, WALL, RES = 8.0, 20.0, 8.0
        DELAY = 30.0
    srv, port = _serve(DELAY)
    base = f"http://127.0.0.1:{port}/v1"
    cfg = LLMConfig(base_url=base, api_key="x", model="slow", timeout_s=TO)
    print(f"参数  timeout_s={TO:g}  wall_s={WALL:g}  reserve={RES:g}  "
          f"retries={args.retries}  端点延迟={DELAY:g}s")
    print(f"      事故比例：timeout_s == wall_s ⇒ 一次逻辑调用理论上可烧 "
          f"{(args.retries + 1) * TO + 0.5 * args.retries * (args.retries + 1) / 2:g}s")

    # ── 对照臂 ────────────────────────────────────────────────────────
    lg_old = Ledger(Budget(steps=8, llm_calls=12, wall_s=WALL, candidates=1))
    t = time.time()
    old_ret = old_style(cfg, lg_old, args.retries)
    d_old = time.time() - t
    n_old = lg_old.llm_calls_used
    print(f"\n① 旧语义（复刻 HEAD：固定 timeout + 重试不查墙钟）")
    print(f"   耗时 = {d_old:.1f}s   发出请求 = {n_old} 次   结局 = {old_ret}")

    # ── 修复臂 ────────────────────────────────────────────────────────
    HITS.clear()
    lg_new = Ledger(Budget(steps=8, llm_calls=12, wall_s=WALL, candidates=1))
    llm = _LedgeredLLM(HTTPLLM(cfg), lg_new, retries=args.retries,
                       backoff_s=0.5, wall_reserve_s=RES)
    t = time.time()
    outcome = ""
    try:
        llm.complete([Message("user", "hi")], max_tokens=16)
        outcome = "意外成功"
    except BudgetExhausted as e:
        outcome = f"BudgetExhausted: {e}"
    except Exception as e:                                        # noqa: BLE001
        outcome = f"{type(e).__name__}: {e}"
    d_new = time.time() - t
    left = WALL - lg_new.elapsed()
    print(f"\n② 修复后（每次尝试前查墙钟 + 按剩余预算裁剪）")
    print(f"   耗时 = {d_new:.1f}s   发出请求 = {lg_new.llm_calls_used} 次   "
          f"结局 = {outcome}")
    print(f"   计数  抖动重试={llm.transient_retries} 重试后仍失败={llm.transient_exhausted} "
          f"上限被裁短={llm.wall_shortened} "
          f"放弃首发={llm.wall_refused_first} 放弃重试={llm.wall_refused_retry}")
    print(f"   剩余 {left:.1f}s（收尾余量声明 {RES:g}s）")

    r1 = d_new <= WALL + 5
    r1b = d_old >= 2 * d_new
    r1c = left >= RES - 1
    ratio = d_old / max(d_new, 0.01)
    print()
    print(f"  {'✅' if r1 else '❌'} R1   新耗时 {d_new:.1f}s ≤ wall_s+5={WALL + 5:g}s"
          f" ⇒ {'成立' if r1 else '不成立'}")
    print(f"  {'✅' if r1b else '❌'} R1b  旧/新 = {ratio:.2f}x ≥ 2 "
          f"⇒ {'成立（修复前确实线性膨胀）' if r1b else '不成立'}")
    print(f"  {'✅' if r1c else '❌'} R1c  剩余 {left:.1f}s ≥ 收尾余量 {RES:g}−1 "
          f"⇒ {'成立（余量还在）' if r1c else '不成立（余量被吃掉了）'}")
    #: ⚠️ 只发过一次就说明"第二次尝试根本没被发起" —— 这正是修复的动作本身。
    print(f"  {'✅' if lg_new.llm_calls_used <= 1 else '❌'} "
          f"R1d  修复臂发出请求 {lg_new.llm_calls_used} 次 ≤ 1 "
          f"⇒ {'成立（拒绝发起注定超时的重试）' if lg_new.llm_calls_used <= 1 else '不成立'}")

    srv.shutdown()
    ok = r1 and r1b and r1c and lg_new.llm_calls_used <= 1
    print()
    print(f"探针结论：{'✅ 通过' if ok else '❌ 不通过'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
