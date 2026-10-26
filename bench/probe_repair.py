"""§60：量「检出之后能不能改对」—— 三态对照，**不改内核**。

=========== 为什么有这个文件 ===========
§59 量到：自检 30/30 一次都没说「不」，而 oracle 判了 52.5% 的格子错；其中
**132 格（失分的 52.4%）** 是「闸说过了，一跑就炸」。§59 的建议是「给自检加一条
可执行性判据」。但 §54 已经量过同类的事：最显眼的根因单独修是 0 格。
⇒ 先量**上界**：如果闸打回并把真实错误信息交回去，模型能不能改对？

=========== 三态 ===========
| 臂   | 第二次请求的反馈 | 信息量 |
| base | 不发，用冻结答案（分数已知） | — |
| P    | 等长的**泛泛建议**（核对边界/返回类型/逐条对照），不含任何错误信息 | 0 |
| R    | 异常类型 + 消息 + 出错行号；或「没报错但返回值与预期不一致」 | 高 |

两条刻意的设计（见 PREREG §2）：
· **P 的长度 = R 的长度**，逐题相等，且不做填充符 —— 用一句中性的真话循环截断到同一字符数。
  机械核验在 `C3`。这样 P 是**强**安慰剂：泛泛建议本身若能提分，净增益会偏低（保守）。
· **R 不泄漏答案**：消息只由「异常类名 + 异常消息 + 行号」拼成，结构上不含测试参数与期望值；
  另有 `C4` 把题面里所有字符串字面量抽出来逐个子串查。

=========== 三条边界（⛔ 越界即作废） ===========
1. **这是上界，不是可实现收益**：R 臂的信息量比任何「内核自造输入冒烟跑」的检测器都多。
2. **不改内核**（`mw/`、`run.py` 一行不动，`harness_sha` 不变）。这是探针，不进契约层。
3. `--run` 会发真请求、随负载抖动 ⇒ **永不进收口**，只做 `--self-check`。

用法
====
    python3 bench/probe_repair.py --self-check                 # 零模型、零网络
    python3 bench/probe_repair.py --plan                       # 只打印 40 条消息，不发请求
    python3 bench/probe_repair.py --run --out /tmp/x.jsonl     # 发请求（需先起代理）

第九十四轮 §61 加的管子（默认值仍是 v3 ⇒ 上面三条用法一字不变）：
    --suite   bench/specgen/tasks_v2.jsonl   换题集
    --answers <首答 jsonl>                   换起点答案（配套 --suite 一起换）
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ⚠️ 以脚本方式运行时 `sys.path[0]` 是 `bench/`（不是仓库根）⇒ 直接
#    `from mw.config import settings` 会 ModuleNotFoundError。第一版就是这么崩的。
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: 反馈消息的目标长度：两份都截到这个字符数 ⇒ 长度不是变量。
BODY_LEN = 72

#: P 臂的中性句子（**不含**任何错误词）。循环截断到 BODY_LEN。
NEUTRAL = ("请仔细核对边界条件、返回值的类型与顺序，"
           "并逐条对照题目要求检查是否有遗漏。")

#: C5 的反证词表：P 臂里出现任何一个即判失败。
LEAK_WORDS = ("Error", "Exception", "Traceback", "报错", "异常", "行号", "第 ")


# ---------------------------------------------------------------------------
# 口径来源：从 run.py 里取 DIRECT_SYSTEM（AST），不抄字面量
# ---------------------------------------------------------------------------
def direct_system(src: Path | None = None) -> str:
    """从 `run.py` 的 AST 里取出 `DIRECT_SYSTEM` 的值。

    ⛔ 不 `from run import ...`：run.py 顶层有一堆只为 CLI 服务的构造，import 会拖着
       cfg/env 一起跑；AST 只取一个赋值，零副作用。
    ⛔ 不抄字面量：抄下来就与内核解耦，内核改了探针不知道 ⇒ 量的是探针的记忆。
    """
    p = src or (ROOT / "run.py")
    tree = ast.parse(p.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            t = node.targets[0]
            if isinstance(t, ast.Name) and t.id == "DIRECT_SYSTEM":
                return ast.literal_eval(node.value)
    raise SystemExit("❌ run.py 里找不到 DIRECT_SYSTEM —— 口径来源断了，拒绝继续")


# ---------------------------------------------------------------------------
# 打分与首个失败
# ---------------------------------------------------------------------------
def code_of(answer: str | None) -> str:
    """从答案里取代码：优先取 ```python 代码块，取不到就用原文。"""
    m = re.search(r"```(?:python)?\n(.*?)```", answer or "", re.S)
    return m.group(1) if m else (answer or "")


def score_of(tests: list[str], code: str) -> int:
    """跑一遍：每条测试在**全新命名空间**里跑，数通过条数。"""
    ok = 0
    for t in tests:
        ns: dict = {}
        try:
            exec(code, ns)
            exec(t, ns)
            ok += 1
        except Exception:
            pass
    return ok


def first_failure(tests: list[str], code: str) -> tuple[str, str]:
    """返回 (kind, detail)。kind ∈ {"EXC","ASSERT","-"}。

    `EXC` 时 detail = `类型: 消息（第 N 行）`；行号只取**提交代码内部**的帧
    —— 测试帧不取（那会泄漏测试写法）。`ASSERT` 时不带任何数值。
    """
    try:
        cobj = compile(code, "<ans>", "exec")
    except SyntaxError as e:
        return "EXC", f"SyntaxError: {e.msg}（第 {e.lineno} 行）"
    for t in tests:
        ns: dict = {}
        try:
            exec(cobj, ns)
            exec(compile(t, "<test>", "exec"), ns)
        except AssertionError as e:
            msg = str(e).strip()
            return "ASSERT", (f"AssertionError: {msg}" if msg else "AssertionError: 返回值与预期不一致")
        except Exception as e:                       # noqa: BLE001 - 探针要抓全部
            lineno = None
            tb = e.__traceback__
            while tb is not None:
                if tb.tb_frame.f_code.co_filename == "<ans>":
                    lineno = tb.tb_lineno
                tb = tb.tb_next
            tail = f"（第 {lineno} 行）" if lineno else ""
            return "EXC", f"{type(e).__name__}: {e}{tail}"
    return "-", ""


# ---------------------------------------------------------------------------
# 两臂的消息（**长度相等**）
# ---------------------------------------------------------------------------
def _clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n - 1] + "…"


def repair_body(detail: str) -> str:
    """R 臂：真实错误信息，截到 BODY_LEN。"""
    return _clip(detail, BODY_LEN)


def placebo_body(n: int) -> str:
    """P 臂：中性句子循环到恰好 n 个字符。⛔ 不含任何错误词（C5 查）。"""
    out = ""
    while len(out) < n:
        out += NEUTRAL
    return out[:n]


def frame(body: str) -> str:
    """两份共同的外框 —— 只有 body 不同，长度也就只由 body 决定。"""
    return f"我按你上面交的代码跑了一遍，没能通过：{body}\n请重新给出完整的 python 代码块。"


#: 满分题用的话术：**不谎称没过**。闸对它们本来就不会响，这一臂量的是
#: 「任何一次强制重写会不会把好答案改坏」（改坏率的成本侧）。
FRAME_OK = ("我按你上面交的代码跑了一遍。请换一种写法重新给出完整的 python 代码块。")


def build_pair(tests: list[str], code: str) -> tuple[str, str, str, str]:
    """返回 (kind, r_user, p_user, detail)。r/p 的 user 消息**字符数相等**。

    ⚠️ 满分题（`kind == "OK"`）两臂**消息完全相同** —— 那里没有「信息量」这个变量，
       只有「被要求重写」这一个干预。所以那 3 题贡献的是**成本侧**读数，不是增益。
    """
    kind, detail = first_failure(tests, code)
    if kind == "-":
        return "OK", FRAME_OK, FRAME_OK, "运行通过（这道题本来就没失分）"
    r_body = repair_body(detail)
    p_body = placebo_body(len(r_body))
    return kind, frame(r_body), frame(p_body), detail


# ---------------------------------------------------------------------------
# 泄漏核验：题面测试里的字符串字面量不许出现在 R 消息里
# ---------------------------------------------------------------------------
def string_literals(tests: list[str]) -> list[str]:
    out: list[str] = []
    for t in tests:
        try:
            tree = ast.parse(t)
        except SyntaxError:
            continue
        for n in ast.walk(tree):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and len(n.value) >= 3:
                out.append(n.value)
    return out


# ---------------------------------------------------------------------------
# 自检（九条反证组 C0~C8 + C9 换题集）—— 零模型、零网络
# ---------------------------------------------------------------------------
def _self_check() -> int:
    ok_all = True

    def chk(name: str, cond: bool, extra: str = "") -> None:
        nonlocal ok_all
        ok_all = ok_all and bool(cond)
        print(f"  {'✅' if cond else '❌'} {name}{('  ' + extra) if extra else ''}")

    print("=" * 74)
    print("  §60/§61 探针自检（零模型、零网络）—— 九条反证组 C0~C9")
    print("=" * 74)

    sysm = direct_system()
    chk("C0 `DIRECT_SYSTEM` 能从 run.py 的 AST 里取到（口径来源没断）",
        isinstance(sysm, str) and "代码" in sysm, f"{len(sysm)} 字符")

    # C1 打分器有牙：oracle 满分
    suite = load_suite()
    t0 = suite[0]
    good = score_of(t0["tests"], code_of(t0["oracle"]))
    chk("C1 反证：oracle 在本题上必须满分（打分器恒真就全表作废）",
        good == len(t0["tests"]), f"{good}/{len(t0['tests'])}")

    # C2 打分器有牙：空实现必须不满分
    bad = score_of(t0["tests"], "pass\n")
    chk("C2 反证：空实现（`pass`）必须不满分", bad < len(t0["tests"]), f"{bad}/{len(t0['tests'])}")

    # C3 长度相等（逐题）
    lens = []
    for t in suite:
        _, r, p, _ = build_pair(t["tests"], code_of(t["oracle"]))
        lens.append((len(r), len(p)))
    same = all(a == b for a, b in lens)
    chk("C3 两臂 user 消息**字符数逐题相等**（长度不是变量）", same,
        f"{lens[0][0]}/{lens[0][1]}" if lens else "")

    # C4 R 消息不泄漏测试里的字符串字面量
    leak = 0
    for t in suite:
        _, r, _, _ = build_pair(t["tests"], code_of(t["oracle"]))
        for lit in string_literals(t["tests"]):
            if lit in r:
                leak += 1
    chk("C4 R 臂消息不含题面测试里的任何字符串字面量（不泄漏答案）", leak == 0, f"泄漏={leak}")

    # C5 P 消息不含任何错误词
    pw = 0
    for t in suite:
        _, _, p, _ = build_pair(t["tests"], code_of(t["oracle"]))
        for w in LEAK_WORDS:
            if w in p:
                pw += 1
    chk("C5 反证：P 臂消息里一个错误词都没有（否则它不是安慰剂）", pw == 0, f"命中={pw}")

    # C6 ASSERT 与 EXC 两种分支都真的走到（否则分支是死代码）
    kinds = set()
    for t in suite:
        k, _ = first_failure(t["tests"], code_of(t["oracle"]))
        kinds.add(k)
    # oracle 走不到 ASSERT/EXC ⇒ 用两段桩把它们逼出来
    k_exc, d_exc = first_failure(["assert f_x(1) == 2"], "def f_x(a):\n    return a.nope\n")
    k_asr, d_asr = first_failure(["assert f_x(1) == 2"], "def f_x(a):\n    return 3\n")
    chk("C6 反证：EXC / ASSERT 两个分支都能被逼出来（不是死代码）",
        k_exc == "EXC" and k_asr == "ASSERT",
        f"{k_exc}={d_exc[:28]!r} / {k_asr}={d_asr!r}")

    # C7 冻结答案的分数必须等于 §59 落盘的读数（逐格）
    got = frozen_scores()
    exp = {"ex01": 16, "ex02": 2, "ex03": 9, "ex04": 4, "ex05": 16,
           "ex06": 1, "ex07": 16, "ex08": 0, "ex09": 9, "ex10": 3}
    diff = {k: (got.get(k), v) for k, v in exp.items() if got.get(k) != v}
    chk("C7 冻结答案的复算分数 == §59 落盘读数（逐题）", not diff, f"不一致={diff}")

    # C8 报告器算术：合成数据（含一条"改坏"）必须算出正的 gain 与负贡献的 loss
    syn = [{"task_id": "a", "delta": 3, "new": 5},
           {"task_id": "a", "delta": -2, "new": 0},
           {"task_id": "b", "delta": 0, "new": 4}]
    s = summarise(syn)
    chk("C8 反证：报告器把「增益 3 / 改坏 2 / 净 +1 / 噪声 5」算对（不是随便加总）",
        (s["gain"], s["loss"], s["net"], s["noise"]) == (3, 2, 1, 5), str(s))

    # C9 第九十四轮新增的 `--suite/--answers` 管子：换题集后 C1/C3 的两条性质仍在
    #    （否则「只换题集」这句话没有任何机制保证 —— 换个题集就可能悄悄换了长度）
    v2p = ROOT / "bench" / "specgen" / "tasks_v2.jsonl"
    if v2p.exists():
        s2 = load_suite(v2p)
        o2 = s2[0]
        good2 = score_of(o2["tests"], code_of(o2["oracle"]))
        bad2 = score_of(o2["tests"], "pass\n")
        eq2 = all(len(pair[0]) == len(pair[1])
                  for pair in (build_pair(t["tests"], code_of(t["oracle"]))[1:3] for t in s2))
        chk("C9 换题集（tasks_v2）后：oracle 满分 / 空实现不满分 / 两臂等长 仍成立",
            good2 == len(o2["tests"]) and bad2 < len(o2["tests"]) and eq2
            and len(s2) == 10 and all(len(t["tests"]) == 14 for t in s2),
            f"{len(s2)}题×{len(o2['tests'])}格 oracle={good2} pass={bad2} 等长={eq2}")
    else:
        chk("C9 tasks_v2 存在（换题集的管子可回归）", False, "文件不存在")

    print("-" * 74)
    print(f"  自检结果：{'全绿（可以起跑）' if ok_all else '有红项 ⇒ 拒绝起跑'}")
    print("=" * 74)
    return 0 if ok_all else 1

# ---------------------------------------------------------------------------
# 数据装载
# ---------------------------------------------------------------------------
def load_suite(path: str | Path | None = None) -> list[dict]:
    p = Path(path) if path else ROOT / "bench" / "specgen" / "tasks_v3.jsonl"
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def load_frozen(path: str | Path | None = None) -> list[dict]:
    p = Path(path) if path else ROOT / "bench" / "evidence" / "ext_s92" / "answers_v3_7b.jsonl"
    rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    # 第 0 行是列表头（tools/atoms 之类），答案从第 1 行起
    return [r for r in rows[1:] if r.get("task_id")]


def frozen_scores() -> dict[str, int]:
    suite = {t["id"]: t for t in load_suite()}
    out: dict[str, int] = {}
    for r in load_frozen():
        tid = r["task_id"]
        out.setdefault(tid, score_of(suite[tid]["tests"], code_of(r.get("answer"))))
    return out


# ---------------------------------------------------------------------------
# --plan：只打印，不发请求
# ---------------------------------------------------------------------------
def _plan(suite_path: str | None = None, answers_path: str | None = None) -> int:
    suite = load_suite(suite_path)
    # ⚠️ 第九十四轮：同一题有多轮答案时**取第一轮**（`setdefault`），
    #    与 `frozen_scores()`（C7 用的那一份）同一个口径。
    #    原来这里是 dict 推导式（后者覆盖前者 ⇒ 取最后一轮）—— 两处口径不同，
    #    多轮不一致时 C7 会通过而 `--run` 用的是另一轮，属于沉默的混口径。
    frozen: dict[str, dict] = {}
    for _r in load_frozen(answers_path):
        frozen.setdefault(_r["task_id"], _r)
    print("=" * 74)
    print("  §60 反馈消息全览（R 臂 / P 臂，长度逐题相等）")
    print("=" * 74)
    for t in suite:
        tid = t["id"]
        code = code_of(frozen[tid].get("answer"))
        kind, r, p, detail = build_pair(t["tests"], code)
        print(f"\n--- {tid}  kind={kind}  旧分={score_of(t['tests'], code)}/{len(t['tests'])}")
        print(f"    R({len(r)}): {r.splitlines()[0]}")
        print(f"    P({len(p)}): {p.splitlines()[0]}")
    return 0


# ---------------------------------------------------------------------------
# --run：发真请求（需先起代理；⛔ 永不进收口）
# ---------------------------------------------------------------------------
def summarise(rows: list[dict]) -> dict:
    """把一个臂的逐条读数折成 (gain, loss, net, noise)。**纯函数**，C8 拿合成数据验它。

    口径与 PREREG §5 逐字对应：
      gain = Σ max(0, 新 − 旧)   loss = Σ max(0, 旧 − 新)   net = gain − loss
      noise = Σ_题 (该题各轮 new 的极差)   ← 「两轮同名臂的逐题极差之和」
    """
    gain = sum(max(0, r["delta"]) for r in rows)
    loss = sum(max(0, -r["delta"]) for r in rows)
    by_task: dict[str, list[int]] = {}
    for r in rows:
        by_task.setdefault(r["task_id"], []).append(r["new"])
    noise = sum(max(v) - min(v) for v in by_task.values() if len(v) > 1)
    return {"n": len(rows), "gain": gain, "loss": loss, "net": gain - loss, "noise": noise}


def _report(path: str) -> int:
    rows = [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]
    if not rows:
        print("❌ 空文件")
        return 2
    arms = sorted({r["arm"] for r in rows})
    reps = sorted({r["rep"] for r in rows})
    tasks = sorted({r["task_id"] for r in rows})

    print("=" * 78)
    print("  §60 读数：检出之后能不能改对（gain/loss/net 逐条按 PREREG §5 的口径）")
    print("=" * 78)
    errs = [r for r in rows if r.get("error")]
    print(f"  条目 {len(rows)}  臂 {arms}  轮 {reps}  带异常 {len(errs)}")

    print("\n  一、分臂分轮")
    print(f"  {'臂':4s} {'轮':3s} {'gain':>5s} {'loss':>5s} {'net':>5s} {'same_code':>10s}")
    agg: dict[str, list[dict]] = {}
    for arm in arms:
        for rep in reps:
            sub = [r for r in rows if r["arm"] == arm and r["rep"] == rep]
            if not sub:
                continue
            s = summarise(sub)
            same = sum(1 for r in sub if r["same_code"])
            agg.setdefault(arm, []).append(s)
            print(f"  {arm:4s} {rep:<3d} {s['gain']:5d} {s['loss']:5d} {s['net']:+5d} "
                  f"{same:4d}/{len(sub):<5d}")

    print("\n  二、逐题（旧分 → 新分，按臂列出各轮）")
    head = f"  {'题':6s} {'旧':>3s}  " + "  ".join(f"{a}{rp}" for a in arms for rp in reps)
    print(head)
    for tid in tasks:
        cells = []
        old = None
        for arm in arms:
            for rep in reps:
                hit = [r for r in rows if r["task_id"] == tid and r["arm"] == arm and r["rep"] == rep]
                if hit:
                    old = hit[0]["old"]
                    cells.append(f"{hit[0]['new']:2d}")
                else:
                    cells.append(" -")
        print(f"  {tid:6s} {old:3d}  " + "   ".join(cells))

    print("\n  三、净增益与噪声（PREREG §5 判定规则）")
    for arm in arms:
        nets = [s["net"] for s in agg.get(arm, [])]
        noises = [s["noise"] for s in agg.get(arm, [])]
        print(f"  {arm}: net 逐轮 {nets}  均值 {sum(nets) / len(nets):+.2f}  "
              f"噪声逐轮 {noises}  最大 {max(noises) if noises else 0}")
    if len(arms) >= 2:
        m = {a: sum(s["net"] for s in agg.get(a, [])) / max(1, len(agg.get(a, []))) for a in arms}
        noise = max([s["noise"] for a in arms for s in agg.get(a, [])] or [0])
        diff = m.get("R", 0) - m.get("P", 0)
        verdict = "可分辨" if abs(diff) > noise else "噪声内（UNKNOWN）"
        print(f"\n  ⭐ 净增益差 R − P = {diff:+.2f} 格/轮；噪声 = {noise} 格 ⇒ **{verdict}**")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="§60 量「检出之后能不能改对」")
    ap.add_argument("--self-check", action="store_true", help="离线反证组（零模型、零网络）")
    ap.add_argument("--plan", action="store_true", help="只打印两臂消息，不发请求")
    ap.add_argument("--run", action="store_true", help="发真请求（需先起代理）")
    ap.add_argument("--out", default="/tmp/v1_s93_repair.jsonl", help="逐条读数落盘")
    ap.add_argument("--reps", type=int, default=2, help="每臂重复次数")
    ap.add_argument("--arms", default="PR", help="要跑的臂，如 'PR' 或 'R'")
    ap.add_argument("--report", default="", help="读 --run 的落盘，算 gain/loss/net（零网络）")
    ap.add_argument("--suite", default="", help="题集 JSONL（默认 tasks_v3）")
    ap.add_argument("--answers", default="", help="首答 JSONL（默认 §59 的 answers_v3_7b）")
    a = ap.parse_args()
    sp = a.suite or None
    apth = a.answers or None
    if a.self_check:
        return _self_check()
    if a.report:
        return _report(a.report)
    if a.plan:
        return _plan(sp, apth)
    if a.run:
        return run_real(a.out, a.reps, a.arms, sp, apth)
    ap.print_help()
    return 0


# ---------------------------------------------------------------------------
# 真请求
# ---------------------------------------------------------------------------
def run_real(out_path: str, reps: int, arms: str,
             suite_path: str | None = None, answers_path: str | None = None) -> int:
    """跑两臂。⛔ 只做读数，不做判定（判定在读完之后另算）。"""
    from mw.config import settings
    from mw.llm import HTTPLLM, Message

    s = settings()
    if s.llm.offline or not s.llm.usable():
        print("❌ 需要真模型：请设 V1_LLM_BASE_URL / V1_LLM_MODEL")
        return 2
    llm = HTTPLLM(s.llm)
    sysm = direct_system()
    suite = load_suite(suite_path)
    # ⚠️ 第九十四轮：同一题有多轮答案时**取第一轮**（`setdefault`），
    #    与 `frozen_scores()`（C7 用的那一份）同一个口径。
    #    原来这里是 dict 推导式（后者覆盖前者 ⇒ 取最后一轮）—— 两处口径不同，
    #    多轮不一致时 C7 会通过而 `--run` 用的是另一轮，属于沉默的混口径。
    frozen: dict[str, dict] = {}
    for _r in load_frozen(answers_path):
        frozen.setdefault(_r["task_id"], _r)

    rows: list[dict] = []
    t_all = time.time()
    for rep in range(1, reps + 1):
        for arm in arms:
            for t in suite:
                tid = t["id"]
                old_ans = frozen[tid].get("answer") or ""
                code = code_of(old_ans)
                kind, r_msg, p_msg, detail = build_pair(t["tests"], code)
                old = score_of(t["tests"], code)
                msg = r_msg if arm == "R" else p_msg
                t0 = time.time()
                try:
                    ans = llm.complete(
                        [Message("system", sysm),
                         Message("user", t["prompt"]),
                         Message("assistant", old_ans),
                         Message("user", msg)],
                        max_tokens=int(s.limits.llm_max_tokens),
                        temperature=0.0,
                    )
                    err = ""
                except Exception as e:                        # noqa: BLE001
                    ans, err = "", f"{type(e).__name__}: {e}"
                el = time.time() - t0
                new = score_of(t["tests"], code_of(ans)) if ans else 0
                same = (code_of(ans).strip() == code.strip())
                rec = {"rep": rep, "arm": arm, "task_id": tid, "kind": kind,
                       "detail": detail, "old": old, "new": new, "delta": new - old,
                       "same_code": same, "chars": len(ans), "elapsed_s": round(el, 1),
                       "error": err, "answer": ans}
                rows.append(rec)
                print(f"  rep{rep} {arm} {tid:5s} kind={kind:6s} {old:2d}→{new:2d} "
                      f"({new - old:+3d}) same={int(same)} {len(ans):4d}字 {el:5.1f}s"
                      f"{('  ⚠️ ' + err[:60]) if err else ''}", flush=True)
    Path(out_path).write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    print(f"\n落盘：{out_path}  {len(rows)} 条  合计 {time.time() - t_all:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
