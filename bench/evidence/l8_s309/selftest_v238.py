"""V238（§309）装置自检 —— 离线，不联网。

覆盖预注册 §4 里可以离线判的部分（P2 / P4 + 分支形状 / 截断判据）。
P1（请求字节与 HEAD 逐字节相同）由 `p1_body.py` 的真对拍给出，不在这里复刻。
P3 / P5 / P6 要真调用，见 `probe_v238.py`。
"""
import dataclasses
import json
import sys
import urllib.request
from pathlib import Path

#: ⚠️ 这里必须是 `parents[3]` 而**不是** `parents[2]`：`__file__` 是**文件**，
#:    parents[0] 就是它所在的 `l8_s309/`，所以要退四格才到仓库根。
#:    实测踩坑：`python3 -c` 的 `sys.path[0]` 是 `''`（= cwd），而 `python3 x.py`
#:    的是脚本目录 —— 用 `-c` 验证过的路径，换成脚本跑就差一格。
#: 其他证据脚本同样显式插根（`probe_v236.py`）；⛔ 不靠 `PYTHONPATH` 环境变量，
#: 那种依赖会让「同一命令换个人跑就 ImportError」。
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from mw.config import LLMConfig, parse_model_spec  # noqa: E402
from mw.llm import HTTPLLM, LLMError, Message     # noqa: E402

FAIL = []
N = 0


def chk(name, cond, detail=""):
    global N
    N += 1
    if not cond:
        FAIL.append(f"{name}｜{detail}")
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"｜{detail}" if detail else ""))


def make(**kw):
    base = dict(base_url="http://localhost:11434/v1", api_key="k",
                model="qwen2.5:7b", temperature=0.0, thinking="", offline=False)
    base.update(kw)
    return LLMConfig(**base)


CAP = []


def _fake(req, timeout=None):
    CAP.append(req)
    raise RuntimeError("stop")


urllib.request.urlopen = _fake


def send(cfg, n=7):
    CAP.clear()
    try:
        HTTPLLM(cfg).complete([Message("user", "hi")], max_tokens=n)
    except Exception:  # noqa: BLE001
        pass
    return CAP[0] if CAP else None


# ---------------------------------------------------------------- T1 fail-closed
try:
    make(provider="bogus")
    t1 = False
    d1 = "没抛"
except ValueError as e:
    t1 = "V1_LLM_PROVIDER" in str(e) or "provider" in str(e)
    d1 = str(e)[:50]
chk("T1 拼错的 provider 构造即抛", t1, d1)

# ---------------------------------------------------------------- T2 三元组解析
cases = [
    ("qwen2.5:14b", ("qwen2.5:14b", None, None)),
    ("Qwen3.5-4B@ollama", ("Qwen3.5-4B", "ollama", None)),
    ("Qwen3.5-4B@ollama:disabled", ("Qwen3.5-4B", "ollama", "disabled")),
    ("Qwen3.5-4B@ollama:", ("Qwen3.5-4B", "ollama", "")),
    ("qwen2.5:7b@ollama:enabled", ("qwen2.5:7b", "ollama", "enabled")),
]
bad = 0
for s, want in cases:
    try:
        got = parse_model_spec(s)
    except Exception as e:  # noqa: BLE001
        got = f"raised {e}"
    if got != want:
        bad += 1
        print(f"     ✗ {s!r}: got {got!r} want {want!r}")
chk("T2a 合法五形态（模型名冒号不被切断）", bad == 0, f"{len(cases)-bad}/{len(cases)}")

illegal = ["", "@ollama", "X@nope", "X@ollama:bogus", "X@:disabled"]
nb = 0
for s in illegal:
    try:
        parse_model_spec(s)
        print(f"     ✗ {s!r} 没抛")
    except ValueError:
        nb += 1
chk("T2b 非法五形态全部 fail-closed", nb == len(illegal), f"{nb}/{len(illegal)}")

# ---------------------------------------------------------------- T3 原生请求形状
r = send(make(provider="ollama", thinking="disabled", model="Qwen3.5-4B"), n=900)
body = json.loads(r.data.decode())
chk("T3a URL 剥掉 /v1 并指向 /api/chat",
    r.full_url == "http://localhost:11434/api/chat", r.full_url)
chk("T3b 预算走 options.num_predict（不是 max_tokens）",
    (body.get("options") or {}).get("num_predict") == 900
    and "max_tokens" not in body, json.dumps(body.get("options"), ensure_ascii=False))
chk("T3c thinking=disabled ⇒ think 为**布尔** false",
    body.get("think") is False, repr(body.get("think")))
chk("T3d stream 显式为 False（一次调用一次响应）",
    body.get("stream") is False, repr(body.get("stream")))
chk("T3e 不带 Authorization（原生不需要，少一个「以为在鉴权」）",
    "Authorization" not in {k for k in r.headers}, str(list(r.headers)))

# ---------------------------------------------------------------- T4 默认不发 think
r2 = send(make(provider="ollama", thinking=""), n=100)
b2 = json.loads(r2.data.decode())
chk("T4 thinking 为空 ⇒ 请求体里没有 think 键", "think" not in b2, json.dumps(b2, ensure_ascii=False))
r3 = send(make(provider="ollama", thinking="enabled"), n=100)
chk("T4b thinking=enabled ⇒ think 为 True", json.loads(r3.data.decode()).get("think") is True)

# ---------------------------------------------------------------- T5 截断必须抛
h = HTTPLLM(make(provider="ollama"))
try:
    h._ollama_content({"message": {"content": "", "thinking": "x" * 100},
                       "done_reason": "length", "eval_count": 900})
    t5 = False
    d5 = "没抛（会把装置故障记成「模型不会」）"
except LLMError as e:
    t5 = "截断" in str(e) and "thinking=100" in str(e)
    d5 = str(e)[:70]
chk("T5a done_reason=length ⇒ 抛 LLMError 且带 thinking 长度", t5, d5)
chk("T5b done_reason=stop ⇒ 正常返回正文",
    h._ollama_content({"message": {"content": "abc"}, "done_reason": "stop"}) == "abc")
chk("T5c 旧版只有 done:true（无 done_reason）⇒ 视作正常结束",
    h._ollama_content({"message": {"content": "abc"}, "done": True}) == "abc")

# ---------------------------------------------------------------- T6 口径签名
c0 = make()
c1 = make(provider="ollama")
chk("T6a provider 为空 ⇒ cond() 里没有该键", "provider" not in c0.cond(), str(sorted(c0.cond())))
chk("T6b provider 非空 ⇒ cond() 带上它", c1.cond().get("provider") == "ollama", str(c1.cond()))

print()
print(f"V238 自检：{'PASS' if not FAIL else 'FAIL'}  {N - len(FAIL)}/{N}")
for f in FAIL:
    print("  ✗", f)
sys.exit(0 if not FAIL else 1)
