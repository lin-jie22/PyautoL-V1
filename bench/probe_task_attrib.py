#!/usr/bin/env python3
"""失分归因探针 —— 把「为什么错」从读代码变成可量化的消融表（零模型、零网络）。

## 它补的是哪笔账

第 79 轮的记录里写着：`per_task[].why` 被截断在 200 字符，所以
「`sg07` 为什么只拿 5/14」从第八十八轮起一直是 **UNKNOWN**。
完整答案其实就躺在 `answers.jsonl` 里 —— 缺的不是数据，是**读它的尺子**。

## 为什么不许用目测代替它

`sg07` 的落盘答案有两处明显偏离题面：

    B1  空白规则写成 `re.sub(r' +', '-', row)`
        · 下限 1 而不是 2 ⇒ 「不足 2 个的空白原样保留」被违反
        · `' '` 而不是 `\\s` ⇒ 制表符根本不参与
    B2  去重用了**全局** `seen` 集合
        · 题面要求「只合并**紧挨着**的重复行；**不相邻**的重复行必须保留」

题面把 B2 那条规则加粗强调了两遍，目测很容易把它判成主因。
实测（`--ablate`）：**只修 B2 拿回 0 格，只修 B1 拿回 6 格。**
原因是 B1 先把不同的行压成相同的行，全局去重才有东西可删 ——
两条根因在 [1][6][13] 上是**耦合**的，逐项相加（0+6）≠ 一起修（+9）。
⇒ 「最显眼的 bug」不等于「最值钱的 bug」，这件事只能量，不能看。

## 判分口径

通过/失败一律走 `bench/scorers/code_exec.py`（`extract_code` + `run_one`），
即真跑打分器的同一条路径、同一组 rlimit、同一个 8s 超时。
本探针**只额外回答"错在哪"**，绝不另写一套判分 —— 那等于换尺子（§45.9）。

## 用法

    python3 bench/probe_task_attrib.py --task sg07                # 逐条明细
    python3 bench/probe_task_attrib.py --task sg07 --ablate       # 消融表
    python3 bench/probe_task_attrib.py --task sg07 --ablate --full # 全部 2^n 子集
    python3 bench/probe_task_attrib.py --self-check               # 五组反证

⛔ 测量装置不进契约层（§46.10 / §47.6）。本文件只挂 `run_tests.sh` 的 `--self-check`。
"""
from __future__ import annotations

import argparse
import ast
import itertools
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bench" / "scorers"))

from code_exec import extract_code, run_one  # noqa: E402

SUITE = ROOT / "bench" / "specgen" / "tasks_v2.jsonl"
ANSWERS = ROOT / "bench" / "evidence" / "cloud_s88" / "answers.jsonl"
ATTRIB = ROOT / "bench" / "attrib"

#: 与 s88 冻结读数的**对拍锚点**：探针自己算出来的必须逐位相同，
#: 否则说明它换了尺子（而不是"发现了新事实"）。
SG07_FROZEN = 5


# ══════════════════════════════════════════════════════════════════════════
# 一、读输入
# ══════════════════════════════════════════════════════════════════════════

def load_row(path: Path, pred):
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            if pred(r):
                return r
    return None


def load_task(task_id: str, suite: Path = SUITE) -> dict | None:
    return load_row(suite, lambda r: r.get("id") == task_id)


def load_answer(task_id: str, answers: Path = ANSWERS) -> dict | None:
    return load_row(answers, lambda r: r.get("task_id") == task_id
                    and r.get("kind") == "answer")


def load_variants(task_id: str) -> list[dict]:
    """读 `bench/attrib/<task>.json` 里的单项修复表。没有就返回空。"""
    p = ATTRIB / f"{task_id}.json"
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8")).get("variants") or []


# ══════════════════════════════════════════════════════════════════════════
# 二、判分（只有这一条路径）
# ══════════════════════════════════════════════════════════════════════════

def score(code: str, tests: list[str]) -> tuple[int, list[int]]:
    """返回 (通过数, 失败序号 1-based)。"""
    fails = [i for i, t in enumerate(tests, 1) if not run_one(code, t)[0]]
    return len(tests) - len(fails), fails


# ══════════════════════════════════════════════════════════════════════════
# 三、「错在哪」——只在判分失败后才做，不进判分路径
# ══════════════════════════════════════════════════════════════════════════

def split_assert(test: str) -> tuple[ast.expr, ast.expr] | None:
    """`assert <call> == <literal>` → (call 节点, 期望值节点)；其它形态返回 None。"""
    try:
        node = ast.parse(test).body[0]
    except SyntaxError:
        return None
    if not isinstance(node, ast.Assert):
        return None
    t = node.test
    if not isinstance(t, ast.Compare) or len(t.ops) != 1:
        return None
    if not isinstance(t.ops[0], ast.Eq):
        return None
    return t.left, t.comparators[0]


def actual_value(code: str, call_src: str, timeout: float = 8.0) -> tuple[bool, object]:
    """在隔离子进程里求 `<call>` 的值（候选代码不可信，所以不 import 进本进程）。"""
    src = (f"{code}\n\nimport json as _j\n"
           f"_v = {call_src}\n"
           f"print(_j.dumps({{'v': _v}}, ensure_ascii=False))\n")
    try:
        p = subprocess.run([sys.executable, "-I", "-c", src],
                           capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, "<超时>"
    if p.returncode != 0:
        err = (p.stderr or "").strip().splitlines()
        return False, "<异常: " + (err[-1][:120] if err else f"rc={p.returncode}") + ">"
    try:
        return True, json.loads(p.stdout.strip().splitlines()[-1])["v"]
    except Exception as e:  # noqa: BLE001  —— 输出不是 JSON 也是"没测到"，不猜
        return False, f"<无法解析输出: {e}>"


def first_diff(got, want) -> str:
    if not isinstance(got, list) or not isinstance(want, list):
        return f"{got!r} != {want!r}"
    if len(got) != len(want):
        return f"长度 {len(got)} != {len(want)}"
    for i, (a, b) in enumerate(zip(got, want)):
        if a != b:
            return f"第 {i} 项 {a!r} != {b!r}"
    return ""


# ══════════════════════════════════════════════════════════════════════════
# 四、消融
# ══════════════════════════════════════════════════════════════════════════

def apply_fix(code: str, fix: dict) -> str:
    """按单项修复做替换。⛔ 目标不出现就**报错**，不许静默跳过。"""
    old, new = fix["old"], fix["new"]
    if old not in code:
        raise SystemExit(f"⛔ 修复项 {fix['name']!r} 的替换目标在答案里不存在 —— "
                         f"答案文本与注册表不匹配，拒绝继续（静默跳过会伪造出 0 收益）")
    return code.replace(old, new, 1)


def build_variants(base: str, fixes: list[dict], full: bool) -> dict[str, str]:
    out = {"as_is": base}
    for f in fixes:
        out[f"fix_{f['name']}"] = apply_fix(base, f)
    if len(fixes) > 1:
        if full:
            for r in range(2, len(fixes) + 1):
                for combo in itertools.combinations(fixes, r):
                    cur, name = base, "+".join(c["name"] for c in combo)
                    for c in combo:
                        cur = apply_fix(cur, c)
                    out[f"fix_{name}"] = cur
        else:
            cur, name = base, "+".join(f["name"] for f in fixes)
            for c in fixes:
                cur = apply_fix(cur, c)
            out[f"fix_{name}"] = cur
    return out


def ablate(task: dict, answer_row: dict, fixes: list[dict], full: bool) -> int:
    tests = task["tests"]
    base = extract_code(answer_row["answer"])

    # ── 窗口闸门：oracle 不满分，后面任何读数都不许用 ───────────────────
    n_oracle, o_fails = score(task["oracle"], tests)
    print(f"[闸门] oracle 通过 {n_oracle}/{len(tests)}"
          + ("  ✅ 窗口开" if n_oracle == len(tests) else f"  ❌ 窗口关 失败={o_fails}"))
    if n_oracle != len(tests):
        print("⛔ 参考解都不满分 ⇒ 装置或题集坏了，下面读数作废", file=sys.stderr)
        return 1
    if not fixes:
        print("（没有 bench/attrib 注册表 ⇒ 只能给 as_is，跳过消融）")
        return 0

    got = {}
    print(f"\n{'变体':16s} {'通过':>7s} {'分数':>8s}  失败断言序号")
    print("-" * 66)
    for name, code in build_variants(base, fixes, full).items():
        n, fails = score(code, tests)
        got[name] = (n, fails)
        print(f"{name:16s} {n:3d}/{len(tests):<3d} {n / len(tests):8.4f}  {fails or '—'}")

    # ── 逐条归因 ──────────────────────────────────────────────────────
    a_fails = set(got["as_is"][1])
    single = {name: a_fails - set(v[1]) for name, v in got.items()
              if name.startswith("fix_") and "+" not in name}
    all_key = [k for k in got if k.startswith("fix_") and k.count("+") == len(fixes) - 1]
    all_fails = set(got[all_key[0]][1]) if all_key else set()

    owners: dict[int, list[str]] = {i: [] for i in a_fails}
    for name, cleared in single.items():
        for i in cleared:
            owners[i].append(name[4:])
    both = a_fails - all_fails

    print(f"\n共 {len(a_fails)} 条失败：")
    for name in single:
        print(f"  只修 {name[4:]:4s} 即转绿：{sorted(single[name])}")
    print(f"  全部修完才转绿：{sorted(both - set().union(*single.values())) if single else '—'}")
    print(f"  全部修完仍红  ：{sorted(a_fails - both) or '—'}"
          f"   ← 非空 = 还有未登记的根因，归因不完整（别把这张表当结论）")
    print(f"\n各修法净收益（格）：" + "、".join(
        f"{n[4:]} {v[0] - got['as_is'][0]:+d}" for n, v in
        [(k, v) for k, v in got.items() if k.startswith("fix_")]))
    print("⛔ 逐项之和 ≠ 全部修 ⇒ 根因之间存在耦合，不许把单项收益当可加分量再相加。")
    return 0


# ══════════════════════════════════════════════════════════════════════════
# 五、逐条明细
# ══════════════════════════════════════════════════════════════════════════

def detail(task: dict, answer_row: dict) -> int:
    answer = answer_row["answer"]
    code = extract_code(answer)
    tests = task["tests"]
    print(f"{task['id']}：{len(tests)} 条断言 | 答案 {len(answer)} 字符 / "
          f"抽出的代码 {len(code)} 字符 | truncated={answer_row.get('truncated')}\n")

    n_pass = 0
    for i, t in enumerate(tests, 1):
        ok, why = run_one(code, t)
        n_pass += ok
        print(f"[{i:2d}] {'✅' if ok else '❌'} {t[:96]}")
        if ok:
            print()
            continue
        pair = split_assert(t)
        if pair is None:
            print(f"       子进程失败: {why}\n")
            continue
        try:
            want = ast.literal_eval(pair[1])
        except ValueError:
            print(f"       期望值不是字面量: {why}\n")
            continue
        okv, got = actual_value(code, ast.unparse(pair[0]))
        if okv:
            print(f"       实际 = {got!r}")
            print(f"       期望 = {want!r}")
            d = first_diff(got, want)
            if d:
                print(f"       差异 = {d}")
        else:
            print(f"       求值失败: {got}")
        print()
    print(f"=== 通过 {n_pass}/{len(tests)} = {n_pass / len(tests):.4f} ===")
    return 0


# ══════════════════════════════════════════════════════════════════════════
# 六、自检：五组反证
# ══════════════════════════════════════════════════════════════════════════

def _self_check() -> int:
    bad = 0

    def chk(name: str, ok: bool, note: str = "") -> None:
        nonlocal bad
        print(f"  {'✅' if ok else '❌'} {name}" + (f"  [{note}]" if note else ""))
        if not ok:
            bad += 1

    print("── 失分归因探针自检（五组反证）──")

    task = load_task("sg07")
    row = load_answer("sg07")
    if task is None or row is None:
        print("  ❌ 前置：找不到 sg07 的题目或冻结答案（证据文件缺失）")
        return 1
    tests = task["tests"]

    # C1 窗口闸门：参考解必须满分。这一条**同时是反证** —— 装置坏了它先红。
    n_o, _ = score(task["oracle"], tests)
    chk("C1 oracle 满分（窗口闸门本身是反证：装置坏了它先红）",
        n_o == len(tests), f"{n_o}/{len(tests)}")

    # C2 对拍：探针算出的 as_is 必须与 s88 冻结读数逐位一致。
    #    ⛔ 这条是「不换尺子」的唯一机械保证：探针另写一套判分会在这里露出来。
    n_a, fails_a = score(extract_code(row["answer"]), tests)
    chk(f"C2 as_is 复现 s88 冻结读数 {SG07_FROZEN}/{len(tests)}（不换尺子）",
        n_a == SG07_FROZEN, f"实得 {n_a}/14，失败 {fails_a}")

    # C3 反证：故意弄坏参考解一处，分数必须下降（证明打分器有牙、不是恒真）。
    broken = task["oracle"].replace("_x[:8]", "_x[:9]", 1)
    n_b, _ = score(broken, tests)
    chk("C3 反证：把 oracle 的截断 8 改成 9 ⇒ 分数必须下降（打分不是恒真）",
        n_b < len(tests), f"{n_b}/{len(tests)}")

    # C4 反证：修复表的替换目标不存在 ⇒ 必须报错，不许静默跳过。
    #    静默跳过会让「该项 0 收益」变成一个**伪造**的读数。
    try:
        apply_fix(extract_code(row["answer"]), {"name": "X", "old": "___不存在的串___", "new": "y"})
        raised = False
    except SystemExit:
        raised = True
    chk("C4 反证：替换目标不存在必须抛（否则「0 收益」可以是伪造的）", raised)

    # C5 归因算术自洽：全部修 ≥ 任何单项，且 ≤ 满分。
    fixes = load_variants("sg07")
    if len(fixes) < 2:
        chk("C5 注册表至少 2 项（否则消融表没有意义）", False, f"读到 {len(fixes)} 项")
    else:
        vs = build_variants(extract_code(row["answer"]), fixes, full=False)
        nums = {k: score(v, tests)[0] for k, v in vs.items()}
        all_n = max(n for k, n in nums.items() if k.startswith("fix_") and "+" in k)
        chk("C5 全部修 ≥ 任一单项 且 ≤ 满分（归因表算术自洽）",
            all_n >= max(nums.values()) and all_n <= len(tests),
            f"全部修 {all_n}，各变体 {sorted(nums.values())}")

    print(f"\n结论：{'全部成立' if bad == 0 else f'{bad} 条不成立'}")
    return 0 if bad == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", default="", help="题号，如 sg07")
    ap.add_argument("--suite", default=str(SUITE))
    ap.add_argument("--answers", default=str(ANSWERS))
    ap.add_argument("--ablate", action="store_true", help="跑消融表")
    ap.add_argument("--full", action="store_true", help="消融时跑全部 2^n 子集")
    ap.add_argument("--self-check", action="store_true", help="五组反证，零模型零网络")
    a = ap.parse_args()

    if a.self_check:
        return _self_check()
    if not a.task:
        ap.error("要给 --task，或用 --self-check")

    task = load_task(a.task, Path(a.suite))
    row = load_answer(a.task, Path(a.answers))
    if task is None:
        print(f"题集里找不到 {a.task}", file=sys.stderr)
        return 2
    if row is None:
        print(f"答案文件里找不到 {a.task} 的 kind=answer 行", file=sys.stderr)
        return 2

    if a.ablate:
        return ablate(task, row, load_variants(a.task), a.full)
    return detail(task, row)


if __name__ == "__main__":
    raise SystemExit(main())
