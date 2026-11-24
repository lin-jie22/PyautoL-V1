"""P1 对拍：同一批配置下，`HTTPLLM.complete` 发出的**请求字节**。

用 `PYTHONPATH` 决定跑哪棵树（HEAD 副本 / 工作区），输出 JSON 供逐字节比对。
⛔ 不联网：把 `urllib.request.urlopen` 换成一个只记录、立刻抛出的桩。
"""
import dataclasses
import hashlib
import json
import sys
import urllib.request

from mw.config import LLMConfig
from mw.llm import HTTPLLM, Message

CAP = []


class _Stop(Exception):
    pass


def _fake_urlopen(req, timeout=None):
    CAP.append({"url": req.full_url, "data": req.data,
                "headers": {k.lower(): v for k, v in req.headers.items()}})
    raise _Stop()


urllib.request.urlopen = _fake_urlopen

_HAS_PROVIDER = any(f.name == "provider" for f in dataclasses.fields(LLMConfig))

CASES = [
    ("default", {}),
    ("thinking_disabled", {"thinking": "disabled"}),
    ("thinking_enabled", {"thinking": "enabled"}),
    ("thinking_empty_explicit", {"thinking": ""}),
    ("model_14b", {"model": "qwen2.5:14b"}),
]

out = []
for name, kw in CASES:
    base = dict(base_url="http://localhost:11434/v1", api_key="k",
                model="qwen2.5:7b", temperature=0.0, thinking="", offline=False)
    base.update(kw)
    cfg = LLMConfig(**base)
    CAP.clear()
    try:
        HTTPLLM(cfg).complete([Message("user", "hi")], max_tokens=7)
    except Exception:  # noqa: BLE001
        #: 桩一定抛（`_Stop` 会被 `mw.llm` 的传输层 `except` 包成 LLMError）——
        #: 这里不看异常类型，只看**它抛之前记下的那一次请求**。
        pass
    if not CAP:
        out.append({"case": name, "error": "urlopen 没被调到"})
        continue
    c = CAP[0]
    out.append({
        "case": name,
        "url": c["url"],
        "data_sha": hashlib.sha256(c["data"]).hexdigest()[:16],
        "data_len": len(c["data"]),
        "data_text": c["data"].decode("utf-8"),
        "auth": c["headers"].get("authorization"),
    })

print(json.dumps({"has_provider": _HAS_PROVIDER, "cases": out},
                 ensure_ascii=False, indent=1))
