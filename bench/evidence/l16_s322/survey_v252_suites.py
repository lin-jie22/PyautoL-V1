"""V252 普查：现有 9 个套件里，哪些题的答案**在题面内可得**（纯离线，0 次模型）。

## 为什么必须先做这张表

V251 用 `g7d_spec_incomplete` 测「换路」，量到的 3/6 后来被查明是
**猜中题面没写的惯例**（见 `audit_v252_spec.py` 的逐份 diff）。
在所有 6 格上，「抬分」与「猜中一条不可得惯例」一一对应。

⇒ 用「答案不可得」的题测能力，等于拿猜谜率当因变量。
   要判「累积/换路有没有牙」，必须先知道**哪些题的信息在题面里**。

## 判据（写死，先注册后跑）

信息可得性只看**题面本身**，不看 oracle：

  `spec-missing`  —— 题面出现「惯例」「不在题面」「按本项目」等声明，
                     即**明示**答案的一部分不在题面里。
  `spec-inline`   —— 题面没有这类声明；此时按 `spec-incomplete` 的反面处理：
                     断言里出现的输入/输出对就是全部规格（`assert f(x) == y` 自带答案）。

⛔ 这是**文本判据**，不是「模型能不能做对」的判据。两者必须分开：
   题面信息可得，模型仍可能做错（能力问题）；信息不可得，谁都做不对（题的问题）。
⛔ 本脚本不改生产文件，不调模型。
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import signal
import sys
import warnings
from pathlib import Path

warnings.simplefilter("ignore")
signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError()))

ROOT = Path("/Users/linshaojie/V1")
TASKS = ROOT / "bench" / "tasks"
EV = Path(__file__).resolve().parent

CAND_TIMEOUT = 0.3

#: 题面**明示**「规格不全」的措辞。⛔ 不许扩成模糊匹配 —— 这是在给题集分类，
#: 判错一条就会把一道可判题划进「不可判」，下游所有结论跟着错。
MISSING_PAT = re.compile(r"惯例|不在题面|按本项目")


def fn_name_of(tests: list[str]) -> str:
    for t in tests:
        m = re.search(r"\b([a-z_][a-z_0-9]*)\s*\(", t)
        if m:
            return m.group(1)
    raise ValueError("tests 里找不到函数名")


def judge(src: str, tests: list[str], name: str) -> float | None:
    """跑断言，返回分数；语法错 ⇒ None（⛔ 不当 0 分）。"""
    if not (src or "").strip():
        return None
    ns: dict = {}
    try:
        compile(src, "<x>", "exec")
    except SyntaxError:
        return None
    signal.setitimer(signal.ITIMER_REAL, CAND_TIMEOUT)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            exec(compile(src, "<x>", "exec"), ns)
            fn = ns.get(name)
            if not callable(fn):
                return None
            ok = 0
            for t in tests:
                try:
                    exec(t, {name: fn})
                    ok += 1
                except Exception:                            # noqa: BLE001
                    pass
            return round(ok / len(tests), 4)
    except TimeoutError:
        return None
    except Exception:                                        # noqa: BLE001
        return None
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


def main() -> int:
    sys.stdin = open("/dev/null")                            # noqa: SIM115
    rows: list[dict] = []
    for p in sorted(TASKS.glob("*.jsonl")):
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            tests = d.get("tests") or []
            meta = d.get("meta") or {}
            name = fn_name_of(tests) if tests else ""
            prompt = d.get("prompt", "")
            rows.append({
                "suite": p.stem,
                "id": d["id"],
                "tags": meta.get("tags", ""),
                "spec": "spec-missing" if MISSING_PAT.search(prompt) else "spec-inline",
                "n_tests": len(tests),
                "prompt_chars": len(prompt),
                "oracle": judge(d.get("oracle", ""), tests, name) if tests else None,
                "partial": judge(d.get("partial", ""), tests, name) if tests else None,
                "null": judge(d.get("null", ""), tests, name) if tests else None,
            })

    (EV / "survey_v252_suites.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"{'suite':22} {'id':30} {'spec':13} {'nt':>3} {'oracle':>7} {'partial':>8}")
    print("-" * 92)
    for r in rows:
        print(f"{r['suite']:22} {r['id']:30} {r['spec']:13} {r['n_tests']:3d} "
              f"{('-' if r['oracle'] is None else f'{r['oracle']:.2f}'):>7} "
              f"{('-' if r['partial'] is None else f'{r['partial']:.2f}'):>8}")

    print()
    by: dict[str, dict[str, int]] = {}
    for r in rows:
        b = by.setdefault(r["suite"], {"missing": 0, "inline": 0})
        b["missing" if r["spec"] == "spec-missing" else "inline"] += 1
    print(f"{'suite':22} {'spec-missing':>13} {'spec-inline':>12}")
    for s, b in by.items():
        print(f"{s:22} {b['missing']:13d} {b['inline']:12d}")
    bad = [r for r in rows if r["oracle"] is not None and r["oracle"] < 1.0]
    print(f"\n⛔ oracle 不满分的题 {len(bad)} 条（尺子自证失败，读数不采信）: "
          f"{[r['id'] for r in bad]}")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
