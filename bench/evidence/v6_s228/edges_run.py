#!/usr/bin/env python3
"""V139 装置：在真实负载下记录 mw/ 的**实际调用边**（caller -> callee）。

与 v6_s227/trace_run.py 同一套 wrap / 归一化 / 签名，只多一件事：进函数时压栈、出栈时弹栈，
把 (caller, callee) 记进集合。这样得到的是**真跑出来的图**，不是我猜的静态图。

用法：
    python3 edges_run.py <worker_dir> <out.json>
"""
from __future__ import annotations

import hashlib
import importlib.abc
import json
import os
import re
import runpy
import sys
import types
from pathlib import Path

_REC: dict[str, list] = {}
_TOTAL = [0]
_EDGES: set[tuple[str, str]] = set()
_STACK: list[str] = []

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
        caller = _STACK[-1] if _STACK else "<root>"
        if caller != qual:
            _EDGES.add((caller, qual))
        _STACK.append(qual)
        try:
            r = fn(*a, **kw)
        except BaseException as e:  # noqa: BLE001
            _STACK.pop()
            _rec(qual, "!raise:" + type(e).__name__)
            raise
        else:
            _STACK.pop()
            _rec(qual, _sig(r))
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
            spec.loader.exec_module = exec_module
        except Exception:
            pass
        return spec


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
        per = {n: {"n": c, "h": h.hexdigest()[:16], "raise": nr}
               for n, (c, h, nr) in _REC.items()}
        out.write_text(json.dumps({
            "exit": code,
            "n_calls": _TOTAL[0],
            "n_fns": len(per),
            "per_fn": per,
            "edges": sorted([a, b] for a, b in _EDGES),
            "max_depth": len(_STACK),
        }, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
