#!/usr/bin/env python3
"""V83 —— 语义记忆的**送达率**标定（零 LLM、零臂、`mw/` 零 diff）。

问句：把人写的**完美经验**塞进 `facts` 之后，`recall(goal)` 到底能不能把它取回来？

为什么必须先问这个（纪律 3：押「A 能让 B 发生」前先量 B 存在过没有）：
  V82 判决 `distill()` 在生产路径上 0 个调用点。在修那条边之前，得先确认
  **就算修好了，载荷到不到得了模型眼前**。到不了 ⇒ 要修的是读端入口，
  而且过去几十轮"累积 Δ=0"的归因要重写（不是"记忆无用"，是"从没送到"）。

⛔ 本脚本只读 `mw/`，不写。三种读法里 S1/S2 是**旁路候选**，都建在临时库里。
⛔ 不造新尺子：`recall` 是现成的、题面是现成的（`struct_convert_heldout`）。

用法：
    python3 bench/probe_mem_channel.py --json bench/evidence/v6_s172/channel.json
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mw.memory import MemoryStore, fts_query                      # noqa: E402
from mw.abstractor import looks_like_code, _IDENTIFIER           # noqa: E402

SUITE = ROOT / "bench/heldout/struct_convert_heldout.jsonl"
K = 6

#: 4 条**人写的完美经验**（每题一条）。⛔ 与 PREREG §1 逐字一致，跑完不许改字。
#: 形态要求：泛化、以动词/条件开头、无函数名、无反引号、无代码形态。
RULES: dict[str, str] = {
    "k01_diff_tree":
        "递归比较两份嵌套结构时，先把差异路径按层级用点号连接，再让某一侧缺失的值留空",
    "k02_encode_cookie":
        "拼接键值对之前，先确认多个项之间用分号紧跟一个空格连接，值里的特殊符号要逐字符转义",
    "k03_unflatten":
        "把带下标的扁平键还原成列表时，先取出现过的最大下标加一定长度，空位留空值而不是跳过",
    "k04_to_ini":
        "生成分节文本时，先确认每行末尾都要带换行，包括最后一行；空结构返回空串",
}

#: 8 条同长度量级的**无关事实**（别的领域）。作用：让 top-k 不是"全表"。
DISTRACTORS: list[str] = [
    "处理金额时，先做四舍五入再格式化，避免累积误差被显示成两个不同的值",
    "遍历目录时，先判断是否为符号链接，避免沿着链接绕回父目录造成死循环",
    "解析日期之前，先确认时区信息是否存在，缺失时按本地时区处理并记录下来",
    "写日志轮转时，先按大小切分再按天数清理，避免同时改动同一个文件句柄",
    "合并两个有序序列时，先比较当前元素再决定取哪一边，相等时取左边以保持稳定",
    "读取配置文件前，先判断编码是否带签名，有签名时先剥掉再交给解析器",
    "统计词频之前，先统一大小写并去掉标点，否则同一个词会被算成两条记录",
    "分页查询时，先按唯一键排序再取偏移，避免翻页过程中出现重复或遗漏的结果",
]

_CJK = re.compile(r"[\u4e00-\u9fff]+")
_ASCII = re.compile(r"[A-Za-z][A-Za-z0-9_]*")


def _tasks() -> list[dict]:
    out = []
    for line in SUITE.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def _cjk_bigrams(text: str, cap: int = 24) -> list[str]:
    """中文 2 字链（按出现顺序去重，截断到 cap）。"""
    seen: list[str] = []
    for run in _CJK.findall(text or ""):
        for i in range(len(run) - 1):
            g = run[i:i + 2]
            if g not in seen:
                seen.append(g)
    return seen[:cap]


def _ascii_words(text: str, cap: int = 24) -> list[str]:
    out: list[str] = []
    for w in _ASCII.findall(text or ""):
        w = w.lower()
        if len(w) >= 3 and w not in out:
            out.append(w)
    return out[:cap]


# ---------------------------------------------------------------- 三种读法

def s0_current(mem: MemoryStore, goal: str) -> list[int]:
    """现状：`MemoryStore.recall`（`fts_query` 的 `_TOKEN` OR + 全句 LIKE）。"""
    return [f.id for f in mem.recall(goal, k=K)]


def s1_bigram(mem: MemoryStore, goal: str) -> list[int]:
    """候选一：中文 2 字链 + ASCII 词，OR-LIKE，按**命中条数**排序。"""
    grams = _cjk_bigrams(goal) + _ascii_words(goal)
    rows = mem.conn.execute("SELECT id, text FROM facts").fetchall()
    scored = []
    for r in rows:
        t = r["text"]
        hits = sum(1 for g in grams if g in t)
        if hits:
            scored.append((hits, r["id"]))
    scored.sort(reverse=True)
    return [i for _, i in scored[:K]]


def _spaced(text: str) -> str:
    """把字符逐字分空 ⇒ unicode61 会把每个字当独立 token（2 字短语才可查）。"""
    out = []
    for ch in text or "":
        out.append(" " if ch.isspace() else ch)
        out.append(" ")
    return "".join(out).strip()


def build_char_fts(conn: sqlite3.Connection) -> None:
    conn.executescript(
        "CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts_char USING fts5(text);")
    for r in conn.execute("SELECT id, text FROM facts").fetchall():
        conn.execute("INSERT INTO facts_fts_char(rowid, text) VALUES (?,?)",
                     (r["id"], _spaced(r["text"])))
    conn.commit()


def s2_char_phrase(mem: MemoryStore, goal: str) -> list[int]:
    """候选二：字级索引 + 2 字**短语**查询（`"下 标"`），语义上等于子串命中。"""
    grams = _cjk_bigrams(goal, cap=24) + _ascii_words(goal, cap=8)
    if not grams:
        return []
    q = " OR ".join('"%s"' % " ".join(g) for g in grams)
    try:
        rows = mem.conn.execute(
            "SELECT rowid AS id, bm25(facts_fts_char) AS s FROM facts_fts_char "
            "WHERE facts_fts_char MATCH ? ORDER BY s LIMIT ?", (q, K)).fetchall()
    except sqlite3.OperationalError as e:                    # 留痕，不静默
        return []
    return [r["id"] for r in rows]


# ---------------------------------------------------------------- 主流程

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="语义记忆送达率标定")
    ap.add_argument("--json", default="")
    args = ap.parse_args(argv)

    tasks = _tasks()
    tmp = Path(tempfile.mkdtemp(prefix="v83chan_"))
    mem = MemoryStore(tmp / "memory.db")

    # --- 写端：4 条规则 + 8 条无关事实（走真实 commit_fact ⇒ 真实去重键 + 真实 FTS）
    ids: dict[str, int] = {}
    for tid, text in RULES.items():
        mem.commit_fact(text, source="abstract", confidence=0.5)
        ids[tid] = mem.conn.execute(
            "SELECT id FROM facts WHERE text=?", (text,)).fetchone()["id"]
    distractor_ids: list[int] = []
    for text in DISTRACTORS:
        mem.commit_fact(text, source="abstract", confidence=0.5)
        distractor_ids.append(mem.conn.execute(
            "SELECT id FROM facts WHERE text=?", (text,)).fetchone()["id"])
    build_char_fts(mem.conn)

    rows: list[dict] = []
    for t in tasks:
        tid, goal = t["id"], t["prompt"]
        want = ids[tid]
        rec: dict = {"task": tid, "want": want}
        # 过滤前置：这 4 条必须**过得了**抽象器的两道闸，否则它不是"理想输出"
        rec["rule_passes_filter"] = (not looks_like_code(RULES[tid])
                                     and not _IDENTIFIER.search(RULES[tid]))
        # 词元层面的证据：题面 token 与 fact token 的**精确交集**
        gt = set(re.findall(r"[A-Za-z0-9_\u4e00-\u9fff]+", goal))
        rt = set(re.findall(r"[A-Za-z0-9_\u4e00-\u9fff]+", RULES[tid]))
        rec["token_exact_intersection"] = sorted(gt & rt)
        rec["goal_tokens_n"] = len(gt)
        rec["fts_query_head"] = fts_query(goal)[:160]
        for name, fn in (("s0", s0_current), ("s1", s1_bigram),
                         ("s2", s2_char_phrase)):
            got = fn(mem, goal)
            rec[f"{name}_ids"] = got
            rec[f"{name}_hit"] = want in got
            rec[f"{name}_rank"] = (got.index(want) + 1) if want in got else 0
            rec[f"{name}_distract"] = sum(1 for i in got if i in distractor_ids)
        rows.append(rec)

    # 自命中对照：用经验原文当 query（证明 FTS 活着，不是装置坏了）
    self_hit = {}
    for tid, text in RULES.items():
        got = [f.id for f in mem.recall(text, k=K)]
        self_hit[tid] = ids[tid] in got

    summary = {
        "k": K,
        "pool": {"rules": len(RULES), "distractors": len(DISTRACTORS)},
        "self_hit4": sum(self_hit.values()),
        "self_hit_by_task": self_hit,
        "hit4_s0": sum(r["s0_hit"] for r in rows),
        "hit4_s1": sum(r["s1_hit"] for r in rows),
        "hit4_s2": sum(r["s2_hit"] for r in rows),
        "distract_top6_s0": sum(r["s0_distract"] for r in rows),
        "distract_top6_s1": sum(r["s1_distract"] for r in rows),
        "distract_top6_s2": sum(r["s2_distract"] for r in rows),
        "rule_passes_filter4": sum(r["rule_passes_filter"] for r in rows),
        "token_intersection_nonempty4": sum(
            1 for r in rows if r["token_exact_intersection"]),
        "db": str(tmp / "memory.db"),
    }

    print(f"池子：{len(RULES)} 条人写完美经验 + {len(DISTRACTORS)} 条无关事实，k={K}")
    print(f"过滤前置：4 条规则过抽象器闸 = {summary['rule_passes_filter4']}/4")
    print(f"自命中对照（原文当 query）= {summary['self_hit4']}/4   "
          f"{'✅ 装置活着' if summary['self_hit4'] == 4 else '⛔ 装置有问题，整轮作废'}")
    print()
    print(f"{'题':18s} {'want':>4s} | {'S0':^18s} | {'S1':^18s} | {'S2':^18s} | 词元精确交集")
    for r in rows:
        def cell(n: str) -> str:
            return (f"{'✅' if r[n+'_hit'] else '❌'} r{r[n+'_rank']} "
                    f"杂{r[n+'_distract']}")
        print(f"{r['task']:18s} {r['want']:4d} | {cell('s0'):^18s} | "
              f"{cell('s1'):^18s} | {cell('s2'):^18s} | "
              f"{len(r['token_exact_intersection'])} {r['token_exact_intersection'][:4]}")
    print()
    print(f"hit4  —— S0 现状={summary['hit4_s0']}/4   S1 二串={summary['hit4_s1']}/4   "
          f"S2 字级={summary['hit4_s2']}/4")
    print(f"精度  —— 前 6 名里的无关条数合计：S0={summary['distract_top6_s0']} "
          f"S1={summary['distract_top6_s1']} S2={summary['distract_top6_s2']}")
    print(f"题面 token 与本题经验有**精确交集**的题数 = "
          f"{summary['token_intersection_nonempty4']}/4")

    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(
            json.dumps({"summary": summary, "rows": rows},
                       ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n落盘 {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
