#!/usr/bin/env python3
"""R13 撤回态驱动器 —— **逐条证明断言不是恒真**。

## 为什么它必须是一个入库的工具，而不是每轮手搓

第十九、二十轮各手搓过一次，两次都揪出了真问题：

  · 第十七轮 AO4/AO8：撤掉实现后**仍然是绿的**（AO4 因为代码里有两道等长闸；
    AO8 因为 FTS 失配时还有三条兜底会掩盖）；
  · 第十八轮 AP11：`.index()` 撤回后抛 `ValueError` ⇒ 不转红，还带走了汇总行；
  · 第十九轮 AQ4/AQ6/AQ7：一条让异常逃出去、一条按文件名匹配、一条锚在注释上。

⇒ **新写的断言必须逐个做撤回态。抽样做等于没做。**

## 三条硬要求（都是踩出来的）

1. **改文件 → 跑 → `finally` 还原**。第二十轮的第一版驱动器被前台超时 SIGKILL，
   `finally` 没跑，仓库里留下一个**变异过的 `memory.py`** —— 它和原文件几乎一样
   （`append` 换掉了 `replace`），下一次基线跑出 AR8 红、而全量套件是绿的，
   两个读数打架，根因是一个被 kill 的脚本。
   ⇒ 本版：变异前先落盘备份 + 每次启动先还原（幂等）+ **基线不绿就拒绝做撤回**。

2. **目标断言必须真的转红**，而且必须落在**同一组**里。撤 AS 组的实现却让 T 组转红，
   说明锚定跑偏了 —— 那不是"通过"，那是"测到了别的东西"。

3. **汇总行必须在**，且 `Traceback` 必须为 0。撤回态下实现被拿掉，
   很容易抛 `AttributeError` / `TypeError` 把整个测试打断 ——
   那时"转红"是假的（它是崩了，不是判红），而这正是 R13 那条硬坑。

用法：
    python3 bench/withdraw_check.py            # 全部跑一遍
    python3 bench/withdraw_check.py AS7 AS13   # 只跑指定几条
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

V1 = Path(__file__).resolve().parent.parent
BAK = Path("/tmp/v1_withdraw_bak")

#: (断言号, 文件, 原文, 变异成)
#: `原文` 为空串 ⇒ **追加**到文件末尾。
#: ⚠️ 原文必须在文件里**恰好出现一次**：出现两次就会改到别处，
#:    那时"没转红"会被误读成"断言恒真"。
CASES: tuple[tuple[str, str, str, str], ...] = (
    # ── AS1：判据模块不许碰锚层 ────────────────────────────────────────
    ("AS1", "mw/verify.py", "",
     "\nfrom bench.scorers.code_exec import extract_code as _x  # 撤回态\n"),
    # ── AS2：抽取口径必须与打分器同构（去掉"裸围栏"分支 ⇒ 不同构）──────
    ("AS2", "mw/verify.py",
     'r"```(?:python|py)?[ \\t]*\\r?\\n(.*?)```"',
     'r"```python[ \\t]*\\r?\\n(.*?)```"'),
    # ── AS3：派生函数只许收 goal ──────────────────────────────────────
    ("AS3", "mw/verify.py", "def derive(goal: str) -> tuple[Check, ...]:",
     "def derive(goal: str, answer: str = \"\") -> tuple[Check, ...]:"),
    # ── AS4：散文题不许派生 compiles ──────────────────────────────────
    ("AS4", "mw/verify.py", 'if _CODE_ONLY.search(goal or ""):', "if True:"),
    # ── AS5：没有判据不许报通过 ───────────────────────────────────────
    ("AS5", "mw/verify.py",
     'return Verdict(False, ("(无判据)",), "本轮没有派生到任何判据")',
     'return Verdict(True, (), "")'),
    # ── AS6：未知判据类型不许静默放行 ─────────────────────────────────
    ("AS6", "mw/verify.py",
     'return f"未知判据类型 {c.kind!r}（未实现 ⇒ 不能算通过）"', 'return ""'),
    # ── AS7 / AS8：闸必须真的拦（`if v.ok` → `if True` ⇒ 永不拒）────────
    ("AS7", "mw/core.py",
     "        self.verify_stats[\"runs\"] += 1\n        if v.ok:",
     "        self.verify_stats[\"runs\"] += 1\n        if True:"),
    # ── AS9：打回必须有上限 ───────────────────────────────────────────
    ("AS9", "mw/core.py",
     'if self.verify_stats["failed"] > cap:',
     'if False:'),
    # ── AS10：答案必须被保住（把 salvage 置空 ⇒ 停滞出口交付空串）──────
    ("AS10", "mw/core.py", "self._salvage = result.output", 'self._salvage = ""'),
    # ── AS11：关掉必须真的关掉 ───────────────────────────────────────
    ("AS11", "mw/core.py",
     "vfy.derive(goal) if int(self.s.limits.verify_retries) > 0 else ())",
     "vfy.derive(goal))"),
    # ── AS12：开关必须进口径签名（从 ZERO_OK_KEYS 里拿掉）──────────────
    #: ⚠️ 锚点必须带上前面的整段元组：`ENV_KEYS` 与 `ZERO_OK_KEYS` 的**尾部逐字相同**
    #:    （都以 BACKOFF_S + VERIFY_RETRIES + `)` 收尾）。只锚尾部 ⇒ 命中 2 次，
    #:    改到的是 `ENV_KEYS`，而 AS12 恰好不会转红 —— 那时"没转红"会被误读成
    #:    "断言恒真"，实际是驱动器改错了地方。
    ("AS12", "mw/config.py",
     'ZERO_OK_KEYS: tuple[str, ...] = (\n'
     '    "V1_LLM_TEMPERATURE",\n'
     '    "V1_CLOSE_RESERVE_S",\n'
     '    "V1_CLOSE_RESERVE_STEPS",\n'
     '    "V1_LLM_RETRIES",\n'
     '    "V1_LLM_RETRY_BACKOFF_S",\n'
     '    "V1_VERIFY_RETRIES",\n)',
     'ZERO_OK_KEYS: tuple[str, ...] = (\n'
     '    "V1_LLM_TEMPERATURE",\n'
     '    "V1_CLOSE_RESERVE_S",\n'
     '    "V1_CLOSE_RESERVE_STEPS",\n'
     '    "V1_LLM_RETRIES",\n'
     '    "V1_LLM_RETRY_BACKOFF_S",\n)'),
    # ── AS13：闸只许挂在 finish 上（去掉 tool 守卫 ⇒ 每个动作都过闸）────
    ("AS13", "mw/core.py",
     'if cand.tool == "finish" and result.ok and self._checks:',
     "if result.ok and self._checks:"),
    # ── AS14：salvage 只在答案为空时顶替（去掉 `not answer and` 前置条件）──
    #: ⚠️ AS8 没有自己的入口 —— 它与 AS7 锚在**同一行**（`if v.ok:`），
    #:   由 AS7 的撤回触发，靠 `COUPLED["AS7"]` 认账。抽样做撤回 = 没做，
    #:   所以这里逐条点名：AS8 走 AS7，其余每条都有自己的变异。
    ("AS14", "mw/core.py",
     "if not answer and self._salvage:", "if self._salvage:"),
    # ── AS15：汇总行必须念校验读数 ───────────────────────────────────
    #: ⚠️ 锚点**必须打在构造语句上**，不能只把 `{_vf_line}` 从 f-string 里摘掉。
    #:   第一版就是那么写的（把 log 的格式化串里 `{_vf_line}` 删掉），结果 AS15
    #:   **没转红** —— 因为 AS15 判的是 `_finish` **函数体里出现 `校验=` 与 `判据=`**，
    #:   而 `_vf_line = (f" | 校验= ...")` 这行构造语句还在函数体里，字符串仍在。
    #:   ⇒ 那条断言当时是恒真的，撤回态把它照出来了。现在整块换成一句 `_vf_line = ""`。
    ("AS15", "mw/core.py",
     '        _vf = stats["verify"]\n'
     '        _vf_line = (f" | 校验={_vf[\'runs\']}跑/{_vf[\'passed\']}过/{_vf[\'failed\']}打回"\n'
     '                    f"/{_vf[\'exhausted\']}放弃 判据={len(_vf[\'checks\'])}项")\n'
     '        if _vf["checks"]:\n'
     '            _vf_line += f"({\'/\'.join(_vf[\'checks\'])})"\n'
     '        if _vf["unverified_delivery"]:\n'
     '            _vf_line += " ⚠️本次交付未过自检"\n',
     '        _vf_line = ""\n'),
    # ── AS16：判读必须冻结且写着"不主张真模型分数" ────────────────────
    ("AS16", "bench/evidence/verify_s21/verdict.json",
     '"tag": "R5"', '"tag": "R4"'),
    # ── AS17：真模型读数的判定必须是负面的 Q3 ─────────────────────────
    ("AS17", "bench/evidence/verify_s21/real/verdict.json",
     '"tag": "Q3"', '"tag": "成立"'),
)

#: 每条撤回**必然**连带转红的那些断言（同一行实现被两个断言锚定）。
#: ⛔ 写成白名单而不是"允许任意多条"：允许多条就等于不再检查锚定跑偏。
COUPLED: dict[str, frozenset[str]] = {
    # 闸永不拒（`if v.ok` → `if True`）时，连带转红的三条，理由各不相同：
    #   AS8  直接后果 —— 交付出去的就是第一版坏答案（分数停在 0）；
    #   AS9  从未打回 ⇒ `failed` 恒 0 ⇒ "超上限就放弃"这条分支**根本走不到**
    #        （它期望 failed==2）；
    #   AS10 同理 —— 没有打回就没有"重写失败"，`_salvage` 也就无从被触发。
    # ⇒ 这是**合理的连带**（上游断了，下游两条断言自然不成立），不是锚定跑偏。
    "AS7": frozenset({"AS8", "AS9", "AS10"}),
    # salvage 条件去掉 `not answer and` 之后，**正常交付**也会被上一版坏答案顶掉
    # ⇒ AS8（"第二版交对就交付它"）必然红。这正是 AS14 注释里预言的那个静默降级。
    "AS14": frozenset({"AS8"}),
}

_SUMMARY = re.compile(r"PASS=(\d+)\s+FAIL=(\d+)")


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:12]


def _restore_all() -> list[str]:
    """幂等还原。**每次启动先做** —— 上一次可能被 SIGKILL 打断过。"""
    done = []
    if not BAK.is_dir():
        return done
    for b in BAK.glob("**/*"):
        if not b.is_file():
            continue
        rel = b.relative_to(BAK)
        dst = V1 / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(b, dst)
        done.append(str(rel))
    return done


def _backup(rel: str) -> None:
    src = V1 / rel
    dst = BAK / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _run_suite() -> tuple[bool, set[str], str]:
    """跑契约测试。返回 (基线绿?, 判红的断言集合, 原始输出尾部)。"""
    p = subprocess.run([sys.executable, "tests/contract_test.py"], cwd=str(V1),
                       capture_output=True, text=True, timeout=900)
    out = (p.stdout or "") + (p.stderr or "")
    m = _SUMMARY.findall(out)
    summary_ok = bool(m)
    passed, failed = (int(m[-1][0]), int(m[-1][1])) if m else (0, 0)
    red = set(re.findall(r"❌\s*(AS\d+)", out))
    clean = summary_ok and "Traceback" not in out
    return (p.returncode == 0 and failed == 0), red, out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="R13 撤回态驱动器")
    ap.add_argument("tags", nargs="*", help="只跑这些断言号（缺省=全部）")
    args = ap.parse_args(argv)

    restored = _restore_all()
    if restored:
        print(f"  ↩️  启动先还原 {len(restored)} 个残留变异文件：{restored}")

    print("一、基线必须先绿（不绿就拒绝做撤回）")
    base_ok, base_red, base_out = _run_suite()
    m = _SUMMARY.findall(base_out)
    print(f"  {'✅' if base_ok else '❌'} 基线 {m[-1] if m else '无汇总行'} "
          f"红={sorted(base_red) or '无'}")
    if not base_ok:
        print("  ⛔ 基线不是绿的 ⇒ 撤回态的读数没有意义，拒绝继续。")
        print(base_out[-1200:])
        return 2

    want = set(args.tags) if args.tags else None
    rows = []
    ok_all = True
    for tag, rel, old, new in CASES:
        if want and tag not in want:
            continue
        path = V1 / rel
        before = _sha(path)
        try:
            _backup(rel)
            src = path.read_text(encoding="utf-8")
            if old == "":
                path.write_text(src + new, encoding="utf-8")
            else:
                n = src.count(old)
                if n != 1:
                    rows.append((tag, f"❌ 变异锚点出现 {n} 次（必须恰好 1 次）", ""))
                    ok_all = False
                    continue
                path.write_text(src.replace(old, new, 1), encoding="utf-8")
            _, red, out = _run_suite()
            hit = tag in red
            want_red = {tag} | set(COUPLED.get(tag, ()))
            extra = red - want_red
            clean = "Traceback" not in out and bool(_SUMMARY.findall(out))
            good = hit and not extra and clean
            ok_all = ok_all and good
            detail = (f"红={sorted(red) or '无'} 目标={'✔' if hit else '✘'}"
                      f" 多红={sorted(extra) or '无'} 汇总行={'有' if _SUMMARY.findall(out) else '无'}"
                      f" Traceback={'有' if 'Traceback' in out else '无'}")
            rows.append((tag, "✅" if good else "❌", detail))
        finally:
            src_bak = BAK / rel
            if src_bak.exists():
                shutil.copy2(src_bak, path)
            after = _sha(path)
            if after != before:
                rows.append((tag, "❌ 还原失败（sha 不一致）", f"{before} → {after}"))
                ok_all = False

    print("\n二、逐条撤回（目标必须转红，且**只**它转红）")
    for tag, flag, detail in rows:
        print(f"  {flag} {tag:5s} {detail}")

    # 还原之后必须仍然全绿 —— 否则"撤回"留下的痕迹会被当成下一轮的基线。
    print("\n三、还原后再跑一次（防止驱动器自己被写进仓库）")
    back_ok, back_red, back_out = _run_suite()
    m2 = _SUMMARY.findall(back_out)
    print(f"  {'✅' if back_ok else '❌'} 还原后基线 {m2[-1] if m2 else '无汇总行'} "
          f"红={sorted(back_red) or '无'}")
    ok_all = ok_all and back_ok

    print("\n" + "=" * 74)
    print(f"  {'R13 通过：逐条撤回都精确转红' if ok_all else 'R13 失败：见上'}")
    print("=" * 74)
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
