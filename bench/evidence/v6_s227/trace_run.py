#!/usr/bin/env python3
"""V138 装置：在真实负载（631 条契约的执行过程）下记录 mw/ 全部函数的返回值轨迹。

要回答的问题不是「我改对了吗」，而是「**我改的东西真的进了执行路径吗**」。
判据（631 条断言）是二值的；轨迹是稠密的。同一份负载、同一次运行，两个读数。

用法：
    python3 trace_run.py <worker_dir> <out.json>

输出 JSON：
    {"exit":0, "n_calls":N, "digest":"...", "per_fn":{name:{n, h, raise}}}

⛔ 不改被测代码。全部经 import hook 在模块加载后包一层记录器。
⭐ 轨迹按函数分别累积哈希（保留函数内调用顺序，跨函数交错顺序丢弃）⇒ 内存 O(#函数)，
   可以记全量调用，不用截断；且能逐函数定位「谁变了」。
"""
from __future__ import annotations

import hashlib
import importlib.abc
import json
import re
import runpy
import sys
import types
from pathlib import Path

_REC: dict[str, list] = {}   # name -> [count, sha1, n_raise]
_TOTAL = [0]

_PAT = [
    (re.compile(r"/private/var/folders/[\w./-]*"), "<TMPP>"),
    (re.compile(r"/var/folders/[\w./-]*"), "<TMPP>"),
    (re.compile(r"/tmp/[\w./-]*"), "<TMP>"),
    (re.compile(r"v1_contract_[A-Za-z0-9_]+"), "<TDIR>"),
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"), "<UUID>"),
    (re.compile(r"\d{4}-\d\d-\d\d[T ]\d\d:\d\d:\d\d(?:\.\d+)?"), "<TS>"),
    (re.compile(r"0x[0-9a-f]{6,}"), "<ADDR>"),
]


def _norm(s: str) -> str:
    for pat, rep in _PAT:
        s = pat.sub(rep, s)
    return s


def _sig(v, depth: int = 2) -> str:
    t = type(v)
    if v is None or t is bool:
        return repr(v)
    if t is int:
        return f"i{v}"
    if t is float:
        return f"f{v:.6g}"
    if t is str:
        return f"s{len(v)}:{hashlib.sha1(_norm(v).encode()).hexdigest()[:10]}"
    if t is bytes:
        return f"b{len(v)}:{hashlib.sha1(v).hexdigest()[:10]}"
    if depth <= 0:
        try:
            return f"{t.__name__}#{len(v)}"
        except Exception:
            return t.__name__
    if t in (list, tuple):
        inner = "|".join(_sig(x, depth - 1) for x in list(v)[:8])
        return f"{t.__name__}{len(v)}:{hashlib.sha1(inner.encode()).hexdigest()[:8]}"
    if t in (set, frozenset):
        # ⚠️ 集合迭代顺序随 PYTHONHASHSEED 变 ⇒ 必须先全量排序再截断，
        #    否则取到的是任意子集，两个空转之间就会不同（V138 自踩）
        sigs = sorted(_sig(x, depth - 1) for x in v)
        inner = "|".join(sigs[:8])
        return f"{t.__name__}{len(v)}:{hashlib.sha1(inner.encode()).hexdigest()[:8]}"
    if t is dict:
        keys = sorted(v, key=str)[:8]
        inner = "|".join(f"{k}={_sig(v[k], depth - 1)}" for k in keys)
        return f"d{len(v)}:{hashlib.sha1(inner.encode()).hexdigest()[:8]}"
    try:
        return f"{t.__name__}#{len(v)}"
    except Exception:
        return t.__name__


_DUMP = {x.strip() for x in __import__("os").environ.get("TRACE_DUMP", "").split(",") if x.strip()}
_DUMPED: dict[str, list] = {}


def _rec(name: str, sig: str) -> None:
    e = _REC.get(name)
    if e is None:
        e = _REC[name] = [0, hashlib.sha1(), 0]
    e[0] += 1
    if sig[0] == "!":
        e[2] += 1
    e[1].update(sig.encode())
    _TOTAL[0] += 1


def _wrap(fn, qual):
    def w(*a, **kw):
        try:
            r = fn(*a, **kw)
        except BaseException as e:  # noqa: BLE001
            _rec(qual, "!raise:" + type(e).__name__)
            raise
        _rec(qual, _sig(r))
        if qual in _DUMP:
            _DUMPED.setdefault(qual, []).append(_norm(repr(r))[:220])
        return r
    try:
        w.__name__ = getattr(fn, "__name__", "w")
        w.__qualname__ = getattr(fn, "__qualname__", w.__name__)
        w.__doc__ = getattr(fn, "__doc__", None)
        w.__module__ = getattr(fn, "__module__", None)
        w.__wrapped__ = fn
    except Exception:
        pass
    return w


_ACCEPT = (types.FunctionType,)


def _wrap_module(mod):
    mname = mod.__name__
    for attr, val in list(vars(mod).items()):
        if isinstance(val, _ACCEPT) and getattr(val, "__module__", None) == mname:
            if getattr(val, "__wrapped__", None) is not None:
                continue
            try:
                setattr(mod, attr, _wrap(val, f"{mname}.{attr}"))
            except Exception:
                pass
        elif isinstance(val, type) and getattr(val, "__module__", None) == mname:
            for an, av in list(vars(val).items()):
                if isinstance(av, _ACCEPT):
                    try:
                        setattr(val, an, _wrap(av, f"{mname}.{val.__name__}.{an}"))
                    except Exception:
                        pass
                elif isinstance(av, (staticmethod, classmethod)):
                    inner = av.__func__
                    if isinstance(inner, _ACCEPT):
                        try:
                            setattr(val, an,
                                    type(av)(_wrap(inner, f"{mname}.{val.__name__}.{an}")))
                        except Exception:
                            pass


class _Finder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if not (fullname == "mw" or fullname.startswith("mw.")):
            return None
        spec = None
        for f in sys.meta_path:
            if f is self:
                continue
            fs = getattr(f, "find_spec", None)
            if fs is None:
                continue
            spec = fs(fullname, path, target)
            if spec is not None:
                break
        if spec is None or spec.loader is None:
            return spec
        orig = spec.loader.exec_module

        def exec_module(module, _orig=orig):
            _orig(module)
            _wrap_module(module)

        try:
            spec.loader.exec_module = exec_module  # 实例属性覆盖类方法
        except Exception:
            pass
        return spec


_NOISE = {x.strip() for x in __import__("os").environ.get("TRACE_NOISE", "").split(",") if x.strip()}


def main() -> int:
    root = Path(sys.argv[1]).resolve()
    out = Path(sys.argv[2])
    sys.meta_path.insert(0, _Finder())
    sys.dont_write_bytecode = True

    code = 0
    try:
        runpy.run_path(str(root / "tests" / "contract_test.py"), run_name="__main__")
    except SystemExit as e:
        code = int(e.code or 0)
    finally:
        per = {}
        for name, (n, h, nr) in _REC.items():
            per[name] = {"n": n, "h": h.hexdigest()[:16], "raise": nr}
        g = hashlib.sha1(
            "\n".join(f"{k}\t{v['n']}\t{v['h']}" for k, v in sorted(per.items())).encode()
        ).hexdigest()
        gc = hashlib.sha1(
            "\n".join(f"{k}\t{v['n']}\t{v['h']}"
                      for k, v in sorted(per.items()) if k not in _NOISE).encode()
        ).hexdigest()
        out.write_text(json.dumps({
            "exit": code,
            "n_calls": _TOTAL[0],
            "n_fns": len(per),
            "digest": g,
            "digest_clean": gc,
            "dump": _DUMPED,
            "per_fn": per,
        }, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
