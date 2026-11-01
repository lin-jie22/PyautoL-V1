"""V33 判决器 —— 等长安慰剂臂（严格按 PREREG 的判据顺序执行，不许改判据）

## 只读三样（⛔ 不看日志标签 —— 纪律 29）

1. `bench_<tag>/runs.jsonl` 的 `report.per_task`（分数唯一真口径）
2. `bench_<tag>/answers.jsonl` 里该 `task_id` 的**最后一行**（行序 = 尝试序）
3. `states_<tag>.jsonl`（同一 `(task, it)` 会写多行 ⇒ 三键 `(task, it, 序号)`，纪律 35）

## 判据顺序（PREREG §4，写死）

`J_CONTENT`（a≥3 且 b=0）→ `J_ANY`（b≥2）→ `J_TIME`（a≤1）→ `UNDECIDED`

用法：`analyze_v33.py /tmp/v33`（只打印，不写回任何证据文件 —— 纪律 34）
"""
from __future__ import annotations

import collections
import json
import re
import sys
from pathlib import Path

W = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/v33")
V32 = Path("/Users/linshaojie/V1/bench/evidence/v6_s122")
TASKS = ["e1wi", "e1ni", "e2wi", "e2ni", "e3wi", "e3ni"]
ARMS = ["c0", "c1", "P"]
TAGS = [f"{a}_r{k}" for k in (1, 2, 3, 4) for a in ARMS]
BLK = re.compile(r"\[外部评审\] (.*?)(?:\n\[|$)", re.S)
TID = re.compile(r"\[tasks_v8/([a-z0-9]+)\]")
IMPORT_RE = re.compile(r"^\s*import\s+re", re.M)
ANY_RE = re.compile(r"import\s+re")
TWITCH = re.compile(r"^\s*kept\s*=\s*set\(\)", re.M)
OUT: list[str] = []


def p(s: str = "") -> None:
    OUT.append(s)
    print(s)


def load_states(tag: str) -> list[tuple[str, int, int, str]]:
    f = W / f"states_{tag}.jsonl"
    if not f.exists():
        return []
    rows, seen = [], collections.Counter()
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        t = d.get("task") or "?"
        it = int(d.get("it") or 0)
        rows.append((t, it, seen[(t, it)], d.get("state", "")))
        seen[(t, it)] += 1
    return rows


def load_scores(tag: str):
    f = W / f"bench_{tag}" / "runs.jsonl"
    if not f.exists():
        return {}, {}, {}
    sc, wh = {}, {}
    cnt = collections.Counter()
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        rep = r.get("report") or {}
        cnt["attempted"] += int(rep.get("attempted") or 0)
        cnt["recorded"] += int(rep.get("recorded") or 0)
        cnt["skipped"] += int(rep.get("skipped_no_answer") or 0)
        cnt["failed"] += int(rep.get("failed_scorer") or 0)
        for pt in rep.get("per_task", []):
            sc[pt["id"]] = float(pt.get("score", 0.0))
            wh[pt["id"]] = pt.get("why") or ""
    return sc, wh, cnt


def last_answers(tag: str) -> dict[str, str]:
    f = W / f"bench_{tag}" / "answers.jsonl"
    if not f.exists():
        return {}
    keep = {}
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        a = json.loads(line)
        if a.get("kind") == "header":
            continue
        keep[a.get("task_id")] = a.get("answer", "")
    return keep


def blk_of(task: str, states) -> str:
    """该题**最后一个**含块的 state 里的块（原样，含 `[外部评审] ` 前缀）。"""
    out = ""
    for t, _it, _i, s in states:
        if t != task:
            continue
        m = BLK.search(s)
        if m:
            out = "[外部评审] " + m.group(1)
    return out


def main() -> int:
    p("=" * 104)
    p("V33 判决 —— 等长安慰剂臂（PREREG: bench/evidence/v6_s123/PREREG.md）")
    p(f"工作目录 {W}")
    p("=" * 104)

    S, Z, A, CNT, ST = {}, {}, {}, {}, {}
    for tag in TAGS:
        sc, wh, cnt = load_scores(tag)
        S[tag] = {t: round(sc.get(t, 0.0) * 16) for t in TASKS}
        Z[tag], A[tag], CNT[tag] = wh, last_answers(tag), cnt
        ST[tag] = load_states(tag)

    # ── 1 分数总表
    p()
    p("## 1 逐轮分数（×16；Σ 上限 48）")
    p()
    p("| 轮 | " + " | ".join(TASKS) + " | Σ | 步数 |")
    p("|---|" + "---|" * (len(TASKS) + 2))
    for tag in TAGS:
        row = S[tag]
        if not row or not ST[tag]:
            p(f"| `{tag}` | — | 缺读数 |")
            continue
        p(f"| `{tag}` | " + " | ".join(str(row[t]) for t in TASKS)
          + f" | **{sum(row.values())}** | {len(ST[tag])} |")

    # ── 2 ⭐ 主判据表
    p()
    p("## 2 ⭐ 主判据 T1：`e1ni` 有没有写 `import re`（PREREG §3）")
    p()
    p("| 轮 | 臂 | `^import re` | 任意 `import re` | `kept=set()` | 分 | 块首条 |")
    p("|---|---|---|---|---|---|---|")
    T1 = {}
    for tag in TAGS:
        a = A[tag].get("e1ni") or ""
        blk = blk_of("e1ni", ST[tag])
        ids = TID.findall(blk)
        first = ids[0] if ids else "（无块）"
        T1[tag] = bool(IMPORT_RE.search(a))
        p(f"| `{tag}` | {tag.split('_')[0]} | {'**有**' if T1[tag] else '无'} | "
          f"{'有' if ANY_RE.search(a) else '无'} | "
          f"{'有' if TWITCH.search(a) else '无'} | {S[tag].get('e1ni', '—')} | `{first}` |")

    # ── 3 e1wi 阴性对照
    p()
    p("## 3 `e1wi` 阴性对照（V32 里它从头到尾不抽搐）")
    p()
    p("| 轮 | `import re` | `kept=set()` | 分 | 块首条 |")
    p("|---|---|---|---|---|")
    for tag in TAGS:
        a = A[tag].get("e1wi") or ""
        ids = TID.findall(blk_of("e1wi", ST[tag]))
        p(f"| `{tag}` | {'有' if IMPORT_RE.search(a) else '无'} | "
          f"{'有' if TWITCH.search(a) else '无'} | {S[tag].get('e1wi', '—')} | "
          f"`{ids[0] if ids else '（无块）'}` |")

    # ── 4 P0 闸
    p()
    p("## 4 P0 前提闸（任一不过 ⇒ `J0`，不解读主判据）")
    p()
    g: dict[str, tuple[bool, str]] = {}

    def e1ni_first(tag: str) -> str:
        ids = TID.findall(blk_of("e1ni", ST[tag]))
        return ids[0] if ids else ""

    def block_has(tag: str, who: str, task: str = "e1ni") -> bool:
        return who in TID.findall(blk_of(task, ST[tag]))

    #: P0-a 通电
    c1_first = [e1ni_first(f"c1_r{k}") for k in (2, 3, 4)]
    p_first = [e1ni_first(f"P_r{k}") for k in (2, 3, 4)]
    c0_has_self = [block_has(f"c0_r{k}", "e1ni") for k in (1, 2, 3, 4)]
    p_has_self = [block_has(f"P_r{k}", "e1ni") for k in (2, 3, 4)]
    p_has_e2ni = [block_has(f"P_r{k}", "e2ni") for k in (2, 3, 4)]
    g["P0-a 通电"] = (c1_first == ["e1ni"] * 3 and p_first == ["e2ni"] * 3
                    and not any(c0_has_self) and not any(p_has_self)
                    and all(p_has_e2ni),
                    f"c1 首条={c1_first} P 首条={p_first} "
                    f"c0 含自己={c0_has_self} P 含自己={p_has_self} P 含 e2ni={p_has_e2ni}")

    #: P0-b 三臂 r1 逐字节
    r1 = {a: ST[f"{a}_r1"] for a in ARMS}
    base = r1["c0"]
    diffs = {}
    for a in ("c1", "P"):
        o = r1[a]
        diffs[a] = (sum(1 for x, y in zip(base, o) if x[3] != y[3])
                    if len(base) == len(o) else -1)
    g["P0-b 起点同"] = (all(v == 0 for v in diffs.values()),
                     f"行数={[len(r1[a]) for a in ARMS]} 逐字节不同={diffs}")

    #: P0-c 等长（逐轮）
    lens = {}
    ok_len = True
    for k in (1, 2, 3, 4):
        lc = len(blk_of("e1ni", ST[f"c1_r{k}"]))
        lp = len(blk_of("e1ni", ST[f"P_r{k}"]))
        lens[k] = (lc, lp)
        if k >= 2:
            ok_len = ok_len and lc == lp
    g["P0-c 等长"] = (ok_len, "c1/P 块长 " + " ".join(f"r{k}:{v[0]}/{v[1]}" for k, v in lens.items()))

    #: P0-d 落盘
    ok_d = all(CNT[tag]["attempted"] == 6 and CNT[tag]["recorded"] == 6
               and CNT[tag]["skipped"] == 0 and CNT[tag]["failed"] == 0 for tag in TAGS)
    g["P0-d 落盘"] = (ok_d, " ".join(f"{tag}:{CNT[tag]['attempted']}/{CNT[tag]['recorded']}"
                                     f"s{CNT[tag]['skipped']}f{CNT[tag]['failed']}" for tag in TAGS))

    #: P0-e 漂移（c0_r1 vs c0_r4，同条件）
    a1, a4 = ST["c0_r1"], ST["c0_r4"]
    d_e = (sum(1 for x, y in zip(a1, a4) if x[3] != y[3]) if len(a1) == len(a4) else -1)
    g["P0-e 漂移"] = (True, f"c0_r1 vs c0_r4 行={len(a1)}/{len(a4)} 逐字节不同={d_e}"
                           f"（此项无论结果都如实报，不挡判据）")

    #: P0-f 旧口径等价（c0_r1 vs V32 c0_r1）
    f32 = V32 / "states_c0_r1.jsonl"
    if f32.exists():
        v32 = []
        seen = collections.Counter()
        for line in f32.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            t = d.get("task") or "?"
            it = int(d.get("it") or 0)
            v32.append((t, it, seen[(t, it)], d.get("state", "")))
            seen[(t, it)] += 1
        d_f = (sum(1 for x, y in zip(a1, v32) if x[3] != y[3]) if len(a1) == len(v32) else -1)
        g["P0-f 旧口径等价"] = (d_f == 0, f"c0_r1 vs V32 c0_r1 行={len(a1)}/{len(v32)} 逐字节不同={d_f}")
    else:
        g["P0-f 旧口径等价"] = (False, f"缺 {f32}")

    #: P0-h 账本未被消融污染
    led = {}
    for a in ARMS:
        f = W / f"crit_{a}.jsonl"
        led[a] = len([x for x in f.read_text(encoding="utf-8").splitlines() if x.strip()]) if f.exists() else -1
    g["P0-h 账本完整"] = (all(v == 6 for v in led.values()), f"crit_c0/c1/P 条数={led}")

    for k, (ok, ev) in g.items():
        p(f"- {'✅' if ok else '❌'} **{k}**：{ev}")

    p0_ok = all(ok for ok, _ in g.values() if not k.startswith("P0-e"))

    # ── 5 判决
    p()
    p("## 5 判决（PREREG §4，机械执行）")
    p()
    a_n = sum(1 for k in (1, 2, 3, 4) if T1[f"c1_r{k}"])
    b_n = sum(1 for k in (1, 2, 3, 4) if T1[f"P_r{k}"])
    c_n = sum(1 for k in (1, 2, 3, 4) if T1[f"c0_r{k}"])
    p(f"- `a` = c1 命中 = **{a_n}/4**（逐轮 {[T1[f'c1_r{k}'] for k in (1,2,3,4)]}）")
    p(f"- `b` = P  命中 = **{b_n}/4**（逐轮 {[T1[f'P_r{k}'] for k in (1,2,3,4)]}）")
    p(f"- `c` = c0 命中 = **{c_n}/4**（逐轮 {[T1[f'c0_r{k}'] for k in (1,2,3,4)]}）")
    p()
    if not p0_ok:
        verdict = "J0（P0 闸未过，不判效应）"
    elif a_n >= 3 and b_n == 0:
        verdict = ("J_CONTENT —— 载荷必须是**关于本题自己**的失败；"
                   "等长/同形的别人的失败不够")
    elif b_n >= 2:
        verdict = ("J_ANY —— 等长的别人失败也够 ⇒ §97.4 必须**降级**为"
                   "「对载荷大小/形状有反应」，不是「学会了那一条」")
    elif a_n <= 1:
        verdict = ("J_TIME —— V32 的效应**本时段不复现** ⇒ §97.4 收回为一次性观察")
    else:
        verdict = "UNDECIDED —— 按 PREREG 如实登记，不硬判"
    p(f"### ⇒ `{verdict}`")

    if a_n == 3 and b_n == 0:
        p()
        p("⚠️ `a=3,b=0` 的 Fisher 单尾 `p=5/70=0.0714` ⇒ **不许写「证实」**，")
        p("   只许写「方向一致、未达 0.05」。PREREG §4 已预声明：可各追加 1 轮。")

    p()
    p("⛔ 无论判成什么，本装置都只说 `import re` 这一行；它**不等于**「得分会涨」"
      "（V32 八轮全 0 分）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
