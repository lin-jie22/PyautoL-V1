#!/usr/bin/env python3
"""V111：分数与行为 —— 桶的边界该按「是否进入执行」切。

零模型、零网络。26 个实现 × 5 条 test 的探针。

§0 探针：assert EXPR == EXPECT  →  print(repr(EXPR))，拿行为指纹
§1 交叉校验：分数必须能由指纹重算出来（装置自检）
§2 每题：分数档 / 盆 / 同分不同盆
§3 0.0 的两种：没跑起来 vs 跑了算错
§4 判决 X1/X2/X3/X5
"""
from __future__ import annotations

import ast
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import collections

HERE = pathlib.Path(__file__).resolve().parent
EV = HERE.parent
ROOT = EV.parent.parent
sys.path.insert(0, str(EV / "v6_s198"))
import idiom_transfer as I  # noqa: E402

TIMEOUT = 8.0
_ASSERT = re.compile(r"^\s*assert\s+(.+?)\s*==\s*(.+?)\s*$", re.S)
#: `assert f(x) is True` / `is None` —— 语料里 g7e1 全用这个形态
_ASSERT_IS = re.compile(r"^\s*assert\s+(.+?)\s+is\s+(True|False|None)\s*$", re.S)
_EXC = re.compile(r"^(\w*(?:Error|Exception))\b", re.M)


def parse_test(t: str):
    """⇒ (EXPR, EXPECT 的字面量)；解析不了返回 (None, None)。"""
    m = _ASSERT_IS.match(t)
    if m:
        return m.group(1).strip(), {"True": True, "False": False, "None": None}[m.group(2)]
    m = _ASSERT.match(t)
    if not m:
        return None, None
    expr, exp = m.group(1).strip(), m.group(2).strip()
    try:
        val = ast.literal_eval(exp)
    except Exception:
        return expr, ("<unparsed>", exp)
    return expr, val


def run_snippet(code: str, tmpl: str) -> tuple[int, str, str]:
    """在隔离子进程里跑 `code + tmpl`。返回 (rc, stdout, 异常类名或 '')。"""
    wd = tempfile.mkdtemp(prefix="v111_")
    try:
        p = subprocess.run([sys.executable, "-I", "-c", f"{code}\n\n{tmpl}\n"],
                           capture_output=True, text=True, timeout=TIMEOUT, cwd=wd)
        exc = ""
        if p.returncode != 0:
            hits = _EXC.findall(p.stderr or "")
            exc = hits[-1] if hits else (f"rc={p.returncode}")
        return p.returncode, (p.stdout or "").strip(), exc
    except subprocess.TimeoutExpired:
        return -9, "", "Timeout"
    finally:
        shutil.rmtree(wd, ignore_errors=True)


def fingerprint(tid: str, code: str) -> list:
    """每题返回长度 5 的列表：输出值的 repr，或 'ERROR:<类型>'。"""
    t = I.task_of(tid)
    out = []
    for test in t["tests"]:
        expr, _exp = parse_test(str(test))
        if expr is None:
            out.append("UNPARSED")
            continue
        rc, so, exc = run_snippet(code, f"print(repr({expr}))")
        out.append(so if rc == 0 else f"ERROR:{exc}")
    return out


def main() -> None:
    corpus = I.harvest()
    tasks = sorted(corpus)

    print("## §0 探针与期望值解析")
    total = 0
    unparsed = []
    for tid in tasks:
        t = I.task_of(tid)
        for test in t["tests"]:
            total += 1
            expr, exp = parse_test(str(test))
            if expr is None or isinstance(exp, tuple):
                unparsed.append((tid, str(test)[:60]))
    print(f"  test 总数 {total}，解析失败 {len(unparsed)} {unparsed}")

    print("\n## §1 交叉校验：分数必须能由指纹重算（装置自检）")
    data = {}
    bad = []
    for tid in tasks:
        t = I.task_of(tid)
        exps = [parse_test(str(x))[1] for x in t["tests"]]
        for h, code in sorted(corpus[tid].items()):
            fp = fingerprint(tid, code)
            passed = 0
            for got, exp in zip(fp, exps):
                if got.startswith("ERROR:") or got == "UNPARSED":
                    continue
                try:
                    if ast.literal_eval(got) == exp:
                        passed += 1
                except Exception:
                    pass
            score = round(passed / len(exps), 4)
            data[(tid, h)] = dict(fp=fp, score=score)
    # 与 code_exec 比
    v109scores = json.loads((EV / "v6_s198" / "idioms.json").read_text(encoding="utf-8"))["scores"]
    for (tid, h), d in data.items():
        old = v109scores.get(tid, {}).get(h)
        if old is not None and abs(old - d["score"]) > 1e-9:
            bad.append((tid, h, old, d["score"]))
    print(f"  与 V109 分数不一致的格子 {len(bad)} 个 {bad[:5]}")
    print(f"  ⇒ {'✅ 装置自检通过（分数可由指纹重算）' if not bad else '⛔ 装置失效，后续读数作废'}")

    # ---------- §2 每题：分数档 / 盆 ----------
    print("\n## §2 每题：分数档 vs 盆（盆 = 行为指纹相同）")
    stats = {}
    for tid in tasks:
        hs = [h for (t, h) in data if t == tid]
        by_score = collections.defaultdict(list)
        by_fp = collections.defaultdict(list)
        for h in hs:
            by_score[data[(tid, h)]["score"]].append(h)
            by_fp[tuple(data[(tid, h)]["fp"])].append(h)
        n_s = len(by_score)
        n_p = len(by_fp)
        pairs = sum(len(v) * (len(v) - 1) // 2 for v in by_score.values())
        # 同分不同盆对
        diff_pairs = 0
        for sc, group in by_score.items():
            for i in range(len(group)):
                for j in range(i + 1, len(group)):
                    a, b = group[i], group[j]
                    if data[(tid, a)]["fp"] != data[(tid, b)]["fp"]:
                        diff_pairs += 1
        stats[tid] = dict(n_impl=len(hs), n_score=n_s, n_basin=n_p,
                          pairs=pairs, diff_pairs=diff_pairs)
        print(f"\n  **{tid}**　实现 {len(hs)}　分数档 {n_s}　盆 {n_p}　"
              f"同分对 {pairs}（其中不同盆 {diff_pairs}）")
        print(f"    分数 → 实现：")
        for sc in sorted(by_score):
            print(f"      {sc:<5} {sorted(by_score[sc])}")
        print(f"    盆 → 代表实现 / 分数：")
        for fp, group in sorted(by_fp.items(), key=lambda kv: -len(kv[1])):
            scs = sorted({data[(tid, h)]["score"] for h in group})
            print(f"      盆{len(group)}个 分{scs}　{sorted(group)}")
            print(f"        指纹 {list(fp)}")

    # ---------- §3 0.0 的两种 ----------
    print("\n## §3 ⭐ 0.0 的两种：没跑起来 vs 跑了算错")
    for tid in tasks:
        t = I.task_of(tid)
        n_t = len(t["tests"])
        for h in [h for (x, h) in data if x == tid]:
            d = data[(tid, h)]
            if d["score"] != 0.0:
                continue
            errs = [x for x in d["fp"] if x.startswith("ERROR:")]
            kinds = collections.Counter(x.split(":", 1)[1] for x in errs)
            never = len(errs) == n_t and not any(
                k == "AssertionError" for k in kinds)
            print(f"    {tid.split('_')[0]:6s} {h}　错误 {dict(kinds)}　"
                  f"{'⛔ 没跑起来（全异常）' if never else '跑了但算错'}")

    # ---------- §4 判决 ----------
    print("\n## §4 判决")
    any_diff = [(t, s) for t, s in stats.items() if s["diff_pairs"] > 0]
    print(f"\n  **X1 同分不同盆存在**（分母 = 4 道题）")
    print(f"    有同分不同盆的题 {len(any_diff)}/4：{[(t.split('_')[0], s['diff_pairs']) for t, s in any_diff]}")
    print(f"    ⇒ {'✅ 押中' if any_diff else '⛔ 破：处处单射'}")

    never_ran = 0
    for (tid, h), d in data.items():
        n_t = len(I.task_of(tid)["tests"])
        errs = [x for x in d["fp"] if x.startswith("ERROR:")]
        if d["score"] == 0.0 and len(errs) == n_t and not any(
                "AssertionError" in x for x in errs):
            never_ran += 1
    print(f"\n  **X2 0.0 里有「没跑起来」**（分母 = 全部 26 个实现）")
    print(f"    没跑起来的实现 {never_ran} 个")
    print(f"    ⇒ {'✅ 押中' if never_ran else '⛔ 破'}")

    ge = all(s["n_basin"] >= s["n_score"] for s in stats.values())
    print(f"\n  **X3 盆数 ≥ 分数档数**")
    for tid, s in stats.items():
        print(f"    {tid.split('_')[0]:6s} 盆 {s['n_basin']} vs 档 {s['n_score']}　"
              f"{'✅' if s['n_basin'] >= s['n_score'] else '⛔'}")
    print(f"    ⇒ {'✅ 押中（每题都成立）' if ge else '⛔ 破'}")

    P = sum(s["pairs"] for s in stats.values())
    D = sum(s["diff_pairs"] for s in stats.values())
    print(f"\n  **X5 同分不同盆占比 ≥ 30%**（分母 = 全部同分对 {P}）")
    print(f"    {D}/{P} = {D / P:.1%}" if P else "    分母 0")
    print(f"    ⇒ {'✅ 押中' if P and D / P >= 0.3 else '⛔ 破'}")

    print("\n## §5 净收益口径")
    print("  ⭐ 关键不是「几个盆」，而是**分数漏掉了什么**：")
    for tid, s in stats.items():
        if s["n_basin"] > s["n_score"]:
            print(f"    {tid.split('_')[0]:6s} 盆 {s['n_basin']} > 档 {s['n_score']} ⇒ "
                  f"分数把 {s['n_basin']} 种行为压成 {s['n_score']} 个数")
        else:
            print(f"    {tid.split('_')[0]:6s} 盆 {s['n_basin']} = 档 {s['n_score']} ⇒ 单射")

    (HERE / "behaviour.json").write_text(json.dumps(
        {f"{t}::{h}": v for (t, h), v in data.items()}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    (HERE / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
