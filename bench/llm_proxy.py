#!/usr/bin/env python3
"""本地转发代理 —— 让「请求次数 / 状态码 / 每次延迟」第一次落盘。

为什么要有这个东西
==================
§49（`zhipu_s85`）跑完 10 题之后，通道读数里**只有 `retries=65` 这一个计数**：

    `_LedgeredLLM` 只累计"重试了几次"，不落报文、不落状态码。

第八十六轮自查把当时那句「65 次里是 1305≈HTTP 429」**撤回了** ——
`run.log` 里一个状态码都没有，7 处 `grep 429` 命中全是 `0.1429` / `895.429` 的误匹配。
⇒ **「为什么重试」在过去是不可判定**，而我当时把它写成了结论。

要补的正是这一格：不是"再猜一次原因"，而是**让原因有地方落盘**。
`mw/llm.py` 里加计数器会断 `harness_sha`（那是另一轮的开销），
而这个代理在**进程外**做同一件事：内核的请求照原样发，代理只多记一行。

它改了什么、没改什么（⛔ 读结论前必须看）
==========================================
· **默认请求体逐字节不变**：不给 `--inject` 时代理不解析、不重写、不注入任何字段。
  `req_sha` 就是客户端发出来的那串字节的 sha256 ⇒ 事后可以逐条核对"代理没偷偷改口径"。
· **响应体逐字节不变**：状态码、body 原样回写（含 429 的 error 报文）。
· **唯一副作用**：客户端看到的 `base_url` 变成 `127.0.0.1:PORT`
  ⇒ 口径签名里的 `endpoint` 字段会变（`mw/config.py` 的 `llm_cond.endpoint`）。
  ⛔ 所以**带代理的读数不能与直连读数并列**，哪怕分数一样。这一条写在这里，
  是因为"反正只加了几毫秒"这种想法正是混口径的入口。

`--inject`：把「协议差异」从「被测对象差异」里拆出来的唯一办法
-------------------------------------------------------------
第九十轮实测：本地两个可用模型**协议不同**——

    127.0.0.1:11434  qwen2.5:7b      message = {role, content}
                     Qwen3.5-4B      message = {role, content, reasoning}
                                     同一句 20 字符的答案：554 vs 12 completion tokens，
                                     23.23s vs 0.57s（40 倍）

直接拿它们比"谁更强"，比的是**模型 + 思考协议 + 速度**三件事，正是本章反复
登记的那类混口径。而关思考的唯一有效写法 `reasoning_effort: \"none\"`
（另外三种写法 `think=false` / `chat_template_kwargs.enable_thinking`
 / `thinking.type=disabled` 被端点**静默忽略**，token 数一个不少）
**不在内核请求体里**，内核里也没法加（改 `mw/llm.py` ⇒ 断 `harness_sha`）。

⇒ 代理给一个显式注入口：两臂发**同一个** `reasoning_effort`，于是
「模型」成为唯一变量。这不是"绕过口径"，恰恰相反：它让"协议"这个变量被
**固定住**，并且**逐条留痕**——

    req_sha = 客户端发来的字节        up_sha = 实际发给上游的字节
    injected = 被并进去的键值（没注入时是 None）

⛔ 三条不许：不许改 `messages`、不许改 `model`、不许注入 `--inject` 以外的任何东西。
⛔ 带 `--inject` 的读数要单独标注：它是「代理装饰过的通道」，与不带注入的读数
   不是同一把尺子（`endpoint` 相同，但请求体不同）。

用法
====
    python bench/llm_proxy.py --self-check                     # 离线十一组反证，不联网
    python bench/llm_proxy.py --upstream https://open.bigmodel.cn \\
        --log /tmp/v1_proxy.jsonl                              # 起代理（前台或后台）
    python bench/llm_proxy.py --report /tmp/v1_proxy.jsonl     # 事后统计
    python bench/llm_proxy.py --upstream http://127.0.0.1:11434 \\
        --log /tmp/v1_proxy.jsonl \\
        --inject '{"reasoning_effort":"none"}'                 # 固定「协议」，让「模型」成为唯一变量

⚠️ `--upstream` 只能是**域名根**，不含任何路径前缀 —— 代理做的是 `upstream + path`
   的**纯拼接**。第一版给了 `.../api/paas/v4`，客户端那边 `base_url` 也是
   `.../api/paas/v4` ⇒ 真请求打成 `/v4/api/paas/v4/chat/completions`，
   拿到 3 个 404，而台账里状态码清清楚楚写着 404（这正是它该有的样子：
   拼错了不会静默成功）。⛔ 不许给代理加"聪明的前缀去重"——那会让
   "客户端发什么，上游收什么"这条唯一的对账关系失效。

⛔ 它不进契约层（§46.10/§47.6：测量装置不进契约）。只挂 `run_tests.sh` 的 `--self-check`。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import socketserver
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# 一条记录的形状（唯一事实源 —— `--report` 与自检都读这里）
# ---------------------------------------------------------------------------
#: 缺失的字段一律 `None`，**不许**用 0 / 空串冒充（§50 的教训：
#: `reasoning_tokens` 缺字段时返回 `None`，才能把"没这个字段"与"用了 0 个"分开）。
REC_KEYS = (
    "n", "t_wall", "method", "path", "status", "elapsed_s",
    "req_bytes", "req_sha", "up_bytes", "up_sha", "injected",
    "model", "max_tokens", "thinking",
    "resp_bytes", "finish_reason", "usage", "error_code", "note",
)


def _new_rec() -> dict:
    return {k: None for k in REC_KEYS}


def _extract(payload: bytes) -> dict:
    """从响应体里尽力取出三样东西。取不到就 `None`，⛔ 不猜。"""
    out: dict = {"finish_reason": None, "usage": None, "error_code": None}
    try:
        obj = json.loads(payload.decode("utf-8", "replace"))
    except Exception:
        return out
    if not isinstance(obj, dict):
        return out
    ch = obj.get("choices")
    if isinstance(ch, list) and ch and isinstance(ch[0], dict):
        fr = ch[0].get("finish_reason")
        out["finish_reason"] = fr if isinstance(fr, str) else None
    us = obj.get("usage")
    if isinstance(us, dict):
        #: 原样保存：**不**在这一层决定"哪个字段叫 reasoning"
        #: （它可能是 `reasoning_tokens`、也可能在 `completion_tokens_details` 里）。
        out["usage"] = us
    err = obj.get("error")
    if isinstance(err, dict):
        code = err.get("code")
        out["error_code"] = str(code) if code is not None else None
    return out


def _req_meta(body: bytes) -> dict:
    """从**已经发出去的字节**里读出三个用于对账的字段（只读，不改）。"""
    meta: dict = {"model": None, "max_tokens": None, "thinking": None}
    try:
        obj = json.loads(body.decode("utf-8", "replace"))
    except Exception:
        return meta
    if not isinstance(obj, dict):
        return meta
    meta["model"] = obj.get("model") if isinstance(obj.get("model"), str) else None
    mt = obj.get("max_tokens")
    meta["max_tokens"] = mt if isinstance(mt, int) else None
    th = obj.get("thinking")
    if isinstance(th, dict):
        t = th.get("type")
        meta["thinking"] = t if isinstance(t, str) else None
    return meta


#: ⛔ 注入**只允许**这几个顶层键。白名单而不是黑名单：黑名单意味着"没列进来的都能改"，
#:    而下一次有人想注 `messages` 时，没有任何东西会拦住他。
#:    这三个是**通道方言**字段（不同端点用不同拼写表达同一件事），不是实验变量本身。
INJECT_ALLOWED: tuple[str, ...] = ("reasoning_effort", "thinking", "chat_template_kwargs")


def parse_inject(spec: str) -> dict:
    """把 `--inject` 的 JSON 解析成一个受限字典。⛔ 非法输入**构造即抛**，不宽容。"""
    if not spec or not spec.strip():
        return {}
    try:
        obj = json.loads(spec)
    except json.JSONDecodeError as e:
        raise ValueError(f"--inject 不是合法 JSON：{e}") from e
    if not isinstance(obj, dict):
        raise ValueError("--inject 必须是 JSON 对象（如 '{\"reasoning_effort\":\"none\"}'）")
    bad = [k for k in obj if k not in INJECT_ALLOWED]
    if bad:
        raise ValueError(f"--inject 里有不允许的键 {bad}；白名单 = {list(INJECT_ALLOWED)}")
    return obj


class InjectError(RuntimeError):
    """注入后的请求体与"原文 + 注入键"不等价 ⇒ **必须 fail-closed**。

    ⛔ 静默不注入是最坏的处置：那一臂会收到"没有 `reasoning_effort`"的请求，
      于是"两臂同协议"这个前提当场变假，而读数上**什么都看不出来**。
    """


def apply_inject(body: bytes, inject: dict) -> tuple[bytes, dict | None]:
    """把 `inject` 并进请求体顶层。

    返回 `(发给上游的字节, 实际注入的键值或 None)`。

    ⛔ `inject` 为空 ⇒ **原字节返回**（连一次 `json.loads` 都不做）：
       这条"零注入 = 零重写"的路必须是**结构性**的，不能是"解析后原样 dump 回去
       碰巧一样" —— 后者会在 JSON 键序/空格/浮点表示上悄悄改动字节，
       而 `req_sha` 的对账能力正是建立在"字节没动"上。

    ★ 非空时**复现客户端的序列化风格**：先试 `ensure_ascii=True`（`json.dumps` 默认，
      内核用的就是它），再试 `False`，哪个能**逐字节复现原文**就用哪个。
      这一条是实测逼出来的：第一版固定用 `ensure_ascii=False`，于是内核发来的中文
      （`\\u53d1\\u51fa` 每字 6 字节）被还原成 UTF-8（3 字节）
      ⇒ 上游收到的包**比客户端发的还小**（5440B → 3141B）。
      语义没错（JSON 转义等价），但"只多了一个键"这句话就再也无法用字节核验了。
    """
    if not inject:
        return body, None
    try:
        obj = json.loads(body.decode("utf-8", "replace"))
    except Exception:
        return body, None                      # 非 JSON 体不注入（GET/健康检查等）
    if not isinstance(obj, dict):
        return body, None
    merged = dict(obj)
    merged.update(inject)

    out: bytes | None = None
    for kw in ({"ensure_ascii": True}, {"ensure_ascii": False}):
        try:
            if json.dumps(obj, **kw).encode("utf-8") == body:
                out = json.dumps(merged, **kw).encode("utf-8")
                break
        except Exception:
            continue
    if out is None:
        #: 原文有额外空白/换行/键序无法复现 ⇒ 退回语义等价形态（并如实记账：
        #: 这次的 `up_bytes` 与 `req_bytes` 之差**不只**是注入键的长度）。
        out = json.dumps(merged, ensure_ascii=False).encode("utf-8")

    #: ★ 逐请求的结构性自查：摘掉注入键之后，必须与原文**逐键等价**。
    #:   放行一个"顺手改了 messages"的 bug，比让代理 500 严重得多。
    try:
        back = json.loads(out.decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        raise InjectError(f"注入后无法解析: {type(e).__name__}") from e
    if not isinstance(back, dict):
        raise InjectError("注入后不是 JSON 对象")
    for k in inject:
        back.pop(k, None)
    if back != obj:
        raise InjectError("摘掉注入键后与原文不等价（夹带了别的改动）")
    return out, dict(inject)


# ---------------------------------------------------------------------------
# 代理本体
# ---------------------------------------------------------------------------
class _Sink:
    """日志落盘器：**每行先 flush**。

    为什么先 flush 再回写响应而不是攒着一起写：跑云端一轮是几十分钟，
    中途被 kill 是常态。攒着写 ⇒ 「跑了 8 题、日志 0 行」，
    而那正是本项目第 79 轮的病（`python` 重定向块缓冲，0 字节 ≠ 没在跑）。
    """

    def __init__(self, path: Path | str | None) -> None:
        self.path = Path(path) if path else None
        self.n = 0
        self._lock = threading.Lock()
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text("", encoding="utf-8")

    def write(self, rec: dict) -> None:
        with self._lock:
            self.n += 1
            rec["n"] = self.n
            if not self.path:
                return
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
                f.flush()


def _opener(use_env_proxy: bool):
    """转发用的 opener。

    ⚠️ 默认**绕过**系统代理（空 `ProxyHandler`）。这不是洁癖，是实测踩出来的：
       第一次写这个文件时，`127.0.0.1:1` 那个"不可达上游"被系统代理接了过去，
       回了 `502 upstream connect failed: Connection refused`，于是
       ① 我的 `except Exception` 分支一次都没走到（走的是 `HTTPError`）；
       ② 那一轮的延迟读数里混进了本机代理的耗时 —— 而延迟正是本轮要量的东西。
       ⇒ 要量通道，必须先把"我们自己的代理"排除掉。
    ⛔ 若某台机器**必须**走系统代理才能出网，显式给 `--use-env-proxy`；
       那时读数要注明"两跳"，且不能与直连的延迟并列。
    """
    if use_env_proxy:
        return urllib.request.build_opener()
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def make_handler(upstream: str, sink: _Sink, *, connect_timeout_s: float = 300.0,
                 use_env_proxy: bool = False, inject: dict | None = None):
    up = upstream.rstrip("/")
    op = _opener(use_env_proxy)
    _inj = dict(inject or {})

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a) -> None:  # noqa: D102  - 静音默认 access log
            return

        def _relay(self) -> None:
            rec = _new_rec()
            rec["t_wall"] = round(time.time(), 3)
            rec["method"] = self.command
            rec["path"] = self.path
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n) if n else b""
            rec["req_bytes"] = len(body)
            rec["req_sha"] = hashlib.sha256(body).hexdigest()
            #: ★ 注入发生在这里，且**只有这里**。两笔 sha 都记账 ⇒ 事后能证明
            #:   "上游收到的东西 = 客户端发的 + 恰好 injected 这几个键"。
            up_body, applied = body, None
            if self.command == "POST":
                try:
                    up_body, applied = apply_inject(body, _inj)
                except InjectError as e:
                    #: fail-closed：宁可不发，也不发一个"不知道差了什么"的请求。
                    rec["note"] = f"inject_error: {e}"
                    rec["status"] = 500
                    sink.write(rec)
                    payload = json.dumps({"error": {"code": "proxy_inject",
                                                    "message": str(e)}}).encode("utf-8")
                    self.send_response(500)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
            rec["up_bytes"] = len(up_body)
            rec["up_sha"] = hashlib.sha256(up_body).hexdigest()
            rec["injected"] = applied
            rec.update(_req_meta(up_body))

            url = up + self.path
            req = urllib.request.Request(url, data=up_body if up_body else None,
                                        method=self.command)
            #: 只透传这两个头。⛔ 不透传 `Host`（会打到 upstream 的 vhost 校验）、
            #: 也不透传 `Content-Length`（由 urllib 自己按 body 重算）。
            for h in ("Content-Type", "Authorization", "Accept"):
                if self.headers.get(h):
                    req.add_header(h, self.headers[h])

            t0 = time.monotonic()
            status, payload = 0, b""
            try:
                with op.open(req, timeout=connect_timeout_s) as resp:
                    status = resp.status
                    payload = resp.read()
            except urllib.error.HTTPError as e:
                #: 4xx/5xx 带 body ⇒ 必须读出来原样回写（429 的诊断全在 body 里）
                status = e.code
                payload = e.read()
            except Exception as e:
                #: 连不上 / 超时：**fail-closed**，回 502 并记账。
                #: ⛔ 不许挂起、不许静默返回 200 空串（那会让"通道断了"看起来像"模型交了空")。
                status, payload = 502, json.dumps(
                    {"error": {"code": "proxy_upstream", "message": f"{type(e).__name__}: {e}"}}
                ).encode("utf-8")
                rec["note"] = "upstream_error"
            rec["elapsed_s"] = round(time.monotonic() - t0, 3)
            rec["status"] = status
            rec["resp_bytes"] = len(payload)
            rec.update(_extract(payload))

            #: ★先落盘，再回写 —— 顺序本身就是判据（见 `_Sink` 的注释）。
            sink.write(rec)

            self.send_response(status)
            self.send_header("Content-Type",
                             "application/json" if payload[:1] in (b"{", b"[")
                             else "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(payload)

        def do_POST(self) -> None:  # noqa: N802
            self._relay()

        def do_GET(self) -> None:  # noqa: N802
            self._relay()

    return H


def serve(upstream: str, log: Path | str | None, listen: str,
          *, use_env_proxy: bool = False, inject: dict | None = None) -> ThreadingHTTPServer:
    host, _, port = listen.rpartition(":")
    sink = _Sink(log)
    httpd = ThreadingHTTPServer(
        (host or "127.0.0.1", int(port)),
        make_handler(upstream, sink, use_env_proxy=use_env_proxy, inject=inject))
    httpd.daemon_threads = True
    httpd.v1_sink = sink            # type: ignore[attr-defined]
    httpd.v1_upstream = upstream    # type: ignore[attr-defined]
    httpd.v1_inject = dict(inject or {})   # type: ignore[attr-defined]
    return httpd


# ---------------------------------------------------------------------------
# 事后统计（读日志，零网络）
# ---------------------------------------------------------------------------
def _pct(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    i = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
    return s[i]


def report(path: Path | str) -> int:
    rows = [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]
    print("=" * 74)
    print(f"  代理台账  {path}  —— {len(rows)} 次请求")
    print("=" * 74)
    if not rows:
        print("  （空）⚠️ 空日志 ≠ 没跑过：先看进程是不是没起来、或 --log 路径写错")
        return 1
    codes: dict[str, int] = {}
    for r in rows:
        k = str(r.get("status"))
        codes[k] = codes.get(k, 0) + 1
    lat = [float(r["elapsed_s"]) for r in rows if r.get("elapsed_s") is not None]
    print(f"  状态码分布：{dict(sorted(codes.items()))}")
    bad = sum(v for k, v in codes.items() if k not in ("200",))
    print(f"  非 200 占比：{bad}/{len(rows)} = {bad / len(rows):.1%}")
    print(f"  延迟：中位 {_pct(lat, 0.5)}s｜p90 {_pct(lat, 0.9)}s｜"
          f"最慢 {max(lat) if lat else None}s｜合计 {sum(lat):.1f}s")
    err = [r.get("error_code") for r in rows if r.get("error_code")]
    print(f"  body 级 error.code：{dict((c, err.count(c)) for c in sorted(set(err))) or '无'}")
    fr = [r.get("finish_reason") for r in rows if r.get("finish_reason")]
    print(f"  finish_reason：{dict((c, fr.count(c)) for c in sorted(set(fr))) or '无'}")
    th = [r.get("thinking") for r in rows]
    print(f"  请求里的 thinking：{dict((str(c), th.count(c)) for c in sorted(set(th), key=str))}")
    mt = [r.get("max_tokens") for r in rows]
    print(f"  请求里的 max_tokens：{dict((str(c), mt.count(c)) for c in sorted(set(mt), key=str))}")
    #: 注入对账：`up_sha != req_sha` 的条数必须**恰好等于**带 `injected` 的条数。
    #: 两者不相等 ⇒ 有别的力量在改字节（或者注入没生效），读数立刻不可信。
    n_inj = sum(1 for r in rows if r.get("injected"))
    n_diff = sum(1 for r in rows if r.get("up_sha") and r.get("req_sha")
                 and r["up_sha"] != r["req_sha"])
    print(f"  注入：{n_inj} 条带 injected｜{n_diff} 条 up_sha≠req_sha"
          f"{'  ✅ 对得上' if n_inj == n_diff else '  ❌ 对不上 ⇒ 有未登记的改字节'}")
    if n_inj:
        uniq = {json.dumps(r["injected"], ensure_ascii=False, sort_keys=True) for r in rows}
        print(f"  注入内容：{sorted(uniq)}")
    #: 逐条延迟明细（方便看"第一发就 429"这种形态）
    print("  ── 逐条 ──")
    for r in rows:
        print(f"   #{r.get('n'):>3} {r.get('status')} {r.get('elapsed_s'):>7}s  "
              f"req={r.get('req_bytes')}B resp={r.get('resp_bytes')}B  "
              f"err={r.get('error_code')}  fr={r.get('finish_reason')}")
    return 0


# ---------------------------------------------------------------------------
# 自检：反证组（离线，零外网）
# ---------------------------------------------------------------------------
def _fake_upstream(mode: dict) -> ThreadingHTTPServer:
    """一个只按剧本回话的"上游"。剧本在 `mode` 里，逐次消费。"""

    class U(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a) -> None:
            return

        def do_POST(self) -> None:  # noqa: N802
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n) if n else b""
            mode.setdefault("seen", []).append(body)
            script = mode.get("script") or [(200, b"{}")]
            status, payload = script[min(len(mode["seen"]) - 1, len(script) - 1)]
            if callable(payload):
                payload = payload(body)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), U)
    httpd.daemon_threads = True
    return httpd


def _serve_in_thread(httpd: ThreadingHTTPServer) -> threading.Thread:
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return t


def _addr(httpd: ThreadingHTTPServer) -> str:
    h, p = httpd.server_address[:2]
    return f"{h}:{p}"


def _post(url: str, body: bytes, timeout: float = 20.0) -> tuple[int, bytes]:
    #: ⛔ 自检全部打 127.0.0.1 ⇒ 必须绕过系统代理，否则本机代理会替我们回答
    #:    （见 `_opener` 的注释：第一版就栽在这里）。
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer TEST"})
    try:
        with op.open(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _self_check() -> int:
    import tempfile

    ok = True

    def chk(name: str, cond: bool, detail: str = "") -> None:
        nonlocal ok
        print(f"  {'✅' if cond else '❌'} {name}" + (f"  —— {detail}" if detail else ""))
        if not cond:
            ok = False

    print("=" * 74)
    print("  本地转发代理自检（十一组反证，零外网、零密钥）")
    print("=" * 74)

    #: 剧本：200 正文 → 429（带 error.code）→ 200（带 usage/finish_reason）
    OK1 = json.dumps({"choices": [{"finish_reason": "stop",
                                   "message": {"content": "收到"}}],
                      "usage": {"completion_tokens": 5,
                                "completion_tokens_details": {"reasoning_tokens": 0}}}).encode()
    R429 = json.dumps({"error": {"code": "1305", "message": "该模型当前访问量过大"}}).encode()
    OK3 = json.dumps({"choices": [{"finish_reason": "length",
                                   "message": {"content": "半截"}}],
                      "usage": {"completion_tokens": 4096,
                                "completion_tokens_details": {"reasoning_tokens": 4050}}}).encode()
    mode: dict = {"script": [(200, OK1), (429, R429), (200, OK3)]}
    up = _fake_upstream(mode)
    _serve_in_thread(up)

    with tempfile.TemporaryDirectory() as td:
        log = Path(td) / "proxy.jsonl"
        px = serve(f"http://{_addr(up)}/v4", log, "127.0.0.1:0")
        _serve_in_thread(px)
        base = f"http://{_addr(px)}/v4/chat/completions"

        b1 = json.dumps({"model": "glm-4.7-flash", "max_tokens": 2048,
                         "thinking": {"type": "disabled"},
                         "messages": [{"role": "user", "content": "只回答两个字：收到"}]},
                        ensure_ascii=False).encode("utf-8")
        s1, p1 = _post(base, b1)
        s2, p2 = _post(base, b1)
        b3 = json.dumps({"model": "glm-4.7-flash", "max_tokens": 4096,
                         "messages": [{"role": "user", "content": "x"}]}).encode("utf-8")
        s3, p3 = _post(base, b3)

        rows = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines() if l.strip()]

        # ── A1 响应逐字节透传 ────────────────────────────────────────────
        chk("A1 200 响应体逐字节相同（代理不改口径）", p1 == OK1 and s1 == 200)
        # ── A2 429 原样透传（不许被吞成 500/空）────────────────────────
        chk("A2 429 原样透传，且 error.code 被记下",
            s2 == 429 and p2 == R429 and rows[1]["error_code"] == "1305",
            f"status={s2} code={rows[1].get('error_code')}")
        # ── A3 请求体逐字节不变 ────────────────────────────────────────
        seen = mode.get("seen") or []
        chk("A3 上游收到的请求体与客户端发出的逐字节相同",
            len(seen) == 3 and seen[0] == b1 and seen[2] == b3,
            f"上游收到 {len(seen)} 次")
        # ── A4 日志条数 == 请求数（失败也算）───────────────────────────
        chk("A4 日志 3 行 == 3 次请求（失败的那次也在）", len(rows) == 3,
            f"{len(rows)} 行")
        # ── A5 字段提取正确，缺字段用 None 不冒充 0 ────────────────────
        r1, r3 = rows[0], rows[2]
        chk("A5a usage 原样保存（不在这层判断哪个字段叫 reasoning）",
            isinstance(r1["usage"], dict) and
            r1["usage"].get("completion_tokens_details", {}).get("reasoning_tokens") == 0)
        chk("A5b finish_reason 逐条对得上",
            r1["finish_reason"] == "stop" and r3["finish_reason"] == "length")
        chk("A5c 请求元信息从**已发出的字节**读（model/max_tokens/thinking）",
            r1["model"] == "glm-4.7-flash" and r1["max_tokens"] == 2048
            and r1["thinking"] == "disabled" and r3["thinking"] is None,
            f"r1.thinking={r1['thinking']!r} r3.thinking={r3['thinking']!r}")
        chk("A5d 缺字段是 None，不是 0/空串（429 那次没有 usage）",
            rows[1]["usage"] is None and rows[1]["finish_reason"] is None)
        # ── A6 上游断了 ⇒ 502 且记账（fail-closed，不许挂起/假 200）─────
        dead = serve("http://127.0.0.1:1/v4", Path(td) / "dead.jsonl", "127.0.0.1:0")
        _serve_in_thread(dead)
        sd, pd = _post(f"http://{_addr(dead)}/v4/chat/completions", b1)
        drows = [json.loads(l) for l in (Path(td) / "dead.jsonl")
                 .read_text(encoding="utf-8").splitlines() if l.strip()]
        chk("A6 上游不可达 ⇒ 502 + 日志记 note=upstream_error",
            sd == 502 and drows and drows[0]["note"] == "upstream_error",
            f"status={sd}")
        # ── A7 反证：不写日志时，A4 必须变假 ───────────────────────────
        #: ⛔ 这一条最重要：没有它，"日志 3 行 == 3 次请求"可能只是**恒真**
        #:    （比如日志文件恰好被别的东西写了 3 行）。
        #: ⚠️ 判据不能写成"目录里一个 jsonl 都没有" —— 同目录下还有 A1–A6 自己的
        #:    台账（第一版就这么写错的：条件恒假，反证看起来永远不成立）。
        before = sorted(p.name for p in Path(td).glob("*.jsonl"))
        silent = serve(f"http://{_addr(up)}/v4", None, "127.0.0.1:0")
        _serve_in_thread(silent)
        _post(f"http://{_addr(silent)}/v4/chat/completions", b1)
        _post(f"http://{_addr(silent)}/v4/chat/completions", b1)
        after = sorted(p.name for p in Path(td).glob("*.jsonl"))
        n_silent = silent.v1_sink.n        # type: ignore[attr-defined]
        chk("A7 反证：`--log` 为空时 2 次请求照跑、但盘上不多一行（A4 不是恒真）",
            n_silent == 2 and before == after,
            f"内存计数={n_silent}，文件数 {len(before)}→{len(after)}")

        # ── A8/A9/A10 注入（第九十轮）：三条必须成对，缺一条就证不出"只多了一个键" ──
        #: ⛔ 为什么必须三条：只有 A9（"上游收到了 reasoning_effort"）时，
        #:    代理可能顺手改了 `messages` 而没人发现。A9b 把注入后的请求体
        #:    减去注入键，**要求逐键等价回客户端原文** —— 这是"只多了一个键"的唯一机械证明。
        #:    A8 是零注入路的结构性证据（原字节直通，不是"解析后原样 dump"）。
        inj = {"reasoning_effort": "none"}
        mode2: dict = {"script": [(200, OK1)]}
        up2 = _fake_upstream(mode2)
        _serve_in_thread(up2)
        log2 = Path(td) / "inject.jsonl"
        px2 = serve(f"http://{_addr(up2)}/v4", log2, "127.0.0.1:0", inject=inj)
        _serve_in_thread(px2)
        b4 = json.dumps({"model": "Qwen3.5-4B:latest", "max_tokens": 2048,
                         "temperature": 0.0,
                         "messages": [{"role": "user", "content": "只回答：收到"}]},
                        ensure_ascii=False).encode("utf-8")
        _post(f"http://{_addr(px2)}/v4/chat/completions", b4)
        seen2 = mode2.get("seen") or []
        rows2 = [json.loads(l) for l in log2.read_text(encoding="utf-8").splitlines() if l.strip()]
        got = seen2[0] if seen2 else b""
        got_obj = json.loads(got.decode("utf-8"))
        src_obj = json.loads(b4.decode("utf-8"))
        chk("A9 上游确实收到了注入键（`reasoning_effort=none`）",
            got_obj.get("reasoning_effort") == "none",
            f"up_bytes={len(got)} vs req_bytes={len(b4)}")
        #: A9b —— 把注入键摘掉，剩下的必须与客户端原文**逐键等价**。
        #:   比对象相等而不是比字节：注入必然重排/追加键，字节本来就会变；
        #:   要证明的是"语义只多了一个键"，不是"字节没变"。
        stripped = {k: v for k, v in got_obj.items() if k != "reasoning_effort"}
        chk("A9b 摘掉注入键后与客户端原文逐键等价（没夹带别的改动）",
            stripped == src_obj,
            f"多的键={sorted(set(got_obj) - set(src_obj))} 少的键={sorted(set(src_obj) - set(got_obj))}")
        #: A9c —— **字节级最小性**：上游包比客户端包多出来的长度，必须**恰好**等于
        #:   一个 `, "k": "v"` 的长度。这一条挡的是"重排/换转义风格"造成的隐性改字节：
        #:    第一版固定 `ensure_ascii=False`，于是内核发的中文 `\u53d1`（6 字节）
        #:    被还原成 UTF-8（3 字节），上游包比客户端包**还小** ⇒ 读数是"注入了"，
        #:    实际连原始请求都没原样送到。
        _expect = len(json.dumps(inj)[1:-1]) + 2       # 逗号 + 空格 + 一组键值
        chk("A9c 上游包比客户端包多的字节数 == 注入键的文本长度（复现了客户端的转义风格）",
            len(got) - len(b4) == _expect,
            f"{len(got)}−{len(b4)}={len(got) - len(b4)}，期望 {_expect}")
        #: A12 —— 不许**覆盖**客户端已有的键。覆盖 = 悄悄改口径；
        #:    而"两臂同协议"这个前提一旦被覆盖破坏，读数上完全看不出来。
        _overlap = json.dumps({"model": "Qwen3.5-4B:latest", "messages": []}).encode("utf-8")
        try:
            apply_inject(_overlap, {"model": "x"})
            over_ok = False
        except InjectError:
            over_ok = True
        chk("A12 反证：注入键与客户端已有键重叠 ⇒ InjectError（不覆盖、不静默放行）", over_ok)
        chk("A8 零注入时**原字节直通**（up_sha == req_sha，不是重排后碰巧一样）",
            rows[0]["up_sha"] == rows[0]["req_sha"] and rows[0]["injected"] is None
            and seen[0] == b1)
        #: A10 反证：同一份 body、去掉 --inject ⇒ 上游收到的东西必须**逐字节**等于客户端原文。
        #:   没有这条，A9/A9b 可能只是"代理本来就会重排 JSON"造成的假象。
        chk("A10 反证：同一请求去掉 --inject ⇒ 上游收到的字节与客户端逐字节相同",
            len(seen) >= 1 and seen[0] == b1 and rows2[0]["injected"] == inj,
            f"注入那次的 up_sha={rows2[0]['up_sha'][:12]}… 与 req_sha="
            f"{rows2[0]['req_sha'][:12]}… {'不同✅' if rows2[0]['up_sha'] != rows2[0]['req_sha'] else '相同❌'}")

    #: A11：白名单必须能拒 —— 允许注 `messages` 就等于允许改题目。
    for bad_spec in ('{"messages": []}', '{"model": "x"}', '[1,2]', '{bad json'):
        try:
            parse_inject(bad_spec)
            good = False
        except ValueError:
            good = True
        chk(f"A11 反证：`--inject {bad_spec}` 被拒（白名单外/非法 ⇒ 构造即抛）", good)

    print()
    print(f"  结论：{'✅ 十一组全部成立' if ok else '❌ 有反证没成立 ⇒ 代理不可信，读数作废'}")
    return 0 if ok else 1


# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="本地转发代理：让请求次数/状态码落盘")
    ap.add_argument("--upstream", default="", help="上游 base url，如 https://open.bigmodel.cn/api/paas/v4")
    ap.add_argument("--listen", default="127.0.0.1:8787", help="监听地址")
    ap.add_argument("--log", default="", help="台账 jsonl（每行一次请求）")
    ap.add_argument("--report", default="", help="读台账并统计（零网络）")
    ap.add_argument("--use-env-proxy", action="store_true",
                    help="转发时走系统代理（默认绕过；见 _opener 的注释）")
    ap.add_argument("--inject", default="",
                    help="注入的顶层字段（JSON 对象，白名单见 INJECT_ALLOWED）。"
                         "如 '{\"reasoning_effort\":\"none\"}' —— 用来把「协议」固定住，"
                         "让「模型」成为唯一变量。⛔ 不给 ⇒ 原字节直通")
    ap.add_argument("--self-check", action="store_true", help="离线反证组")
    a = ap.parse_args()

    if a.self_check:
        return _self_check()
    if a.report:
        return report(a.report)
    if not a.upstream:
        ap.error("要么 --self-check / --report，要么给 --upstream")
    try:
        _inj = parse_inject(a.inject)
    except ValueError as e:
        ap.error(str(e))
        return 2
    httpd = serve(a.upstream, a.log or None, a.listen,
                  use_env_proxy=a.use_env_proxy, inject=_inj)
    print(f"  代理已起：http://{a.listen}  →  {a.upstream}")
    print(f"  台账：{a.log or '（未落盘）'}")
    print(f"  注入：{_inj or '（无，原字节直通）'}")
    print("  ⛔ 带代理的读数 endpoint 会变 ⇒ 不能与直连读数并列（见文件头）")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print(f"\n  收尾：共 {httpd.v1_sink.n} 次请求")   # type: ignore[attr-defined]
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
