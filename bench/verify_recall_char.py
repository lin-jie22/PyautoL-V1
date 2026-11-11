#!/usr/bin/env python3
"""V84 三闸/四闸（G1/G2/G3/G4）—— 离线、零 LLM、零臂。

读 PREREG：`bench/evidence/v6_s173/PREREG.md`。判据形态全部按预注册，⛔ 跑完不改线。

装置纪律（V83 的纪律 31）：
  · 所有**归档库**先 `shutil.copy` 到临时目录再 `MemoryStore` 打开 ——
    `MemoryStore.__init__` 会 `executescript(SCHEMA)` 并可能回填字级索引 = 写库。
  · 每个档位用**独立的临时库**，三档互不污染。

用法：
    python3 bench/verify_recall_char.py --json bench/evidence/v6_s173/gates.json
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mw.memory import MemoryStore                                  # noqa: E402

STRUCT = ROOT / "bench/heldout/struct_convert_heldout.jsonl"
G7E = ROOT / "bench/heldout/g7e_heldout.jsonl"
G7J4 = ROOT / "bench/heldout/g7j_n4_heldout.jsonl"
G7J0 = ROOT / "bench/heldout/g7j_n0_heldout.jsonl"
ZK_DB = ROOT / "bench/evidence/knowledge_s39/ev_n0/seed_H.db"
A5_DB = ROOT / "bench/evidence/v6_s159/final_memory.db"
K = 6

#: 与 V83 `probe_mem_channel.RULES` **逐字相同**（⛔ 不改一个字，否则两轮读数不可比）。
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


def _tasks(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _at_level(level: int, db: Path) -> MemoryStore:
    os.environ["V1_RECALL_CHAR"] = str(level)
    return MemoryStore(db)


def _sig(mem: MemoryStore, goal: str) -> dict:
    got = mem.recall(goal, k=K)
    return {"ids": [f.id for f in got], "chars": sum(len(f.text) for f in got)}


def gate1_and_4(tmp: Path) -> dict:
    """G1 送达（struct 4 题）＋ G4 噪音地板（无关库）。"""
    out: dict = {"per_task": {}, "by_level": {}}
    struct = _tasks(STRUCT)
    for level in (0, 1, 2):
        # —— 受控池：4 条经验 + 8 条无关 = 12 条，k=6 ⇒ top-k 不是全表
        db = tmp / f"g1_l{level}.db"
        mem = _at_level(level, db)
        own_ids: dict[str, int] = {}
        for tid, text in RULES.items():
            key, _ = mem.commit_fact(text, source="abstract", confidence=0.6)
            row = mem.conn.execute("SELECT id FROM facts WHERE key=?", (key,)).fetchone()
            own_ids[tid] = int(row["id"])
        for d in DISTRACTORS:
            mem.commit_fact(d, source="abstract", confidence=0.5)
        hit = 0
        per: dict[str, dict] = {}
        for t in struct:
            got = mem.recall(t["prompt"], k=K)
            ok = own_ids[t["id"]] in [f.id for f in got]
            hit += bool(ok)
            per[t["id"]] = {"n": len(got), "own": ok,
                            "chars": sum(len(f.text) for f in got)}
        out["by_level"][f"L{level}"] = {"hit4": hit, "per_task": per,
                                        "char_fallbacks": mem.char_fallbacks,
                                        "char_hits": mem.char_hits}
        mem.close()
        # —— 无关库：A5 真库副本（3 条 worldmodel 事实，与题无关）
        db2 = tmp / f"g4_l{level}.db"
        if A5_DB.exists():
            shutil.copy(A5_DB, db2)
            mem2 = _at_level(level, db2)
            noise = {}
            for t in struct:
                got = mem2.recall(t["prompt"], k=K)
                noise[t["id"]] = len(got)
            out.setdefault("noise", {})[f"L{level}"] = {
                "per_task": noise, "sum": sum(noise.values()),
                "max": max(noise.values()) if noise else 0}
            mem2.close()
    return out


def gate2_and_3(tmp: Path) -> dict:
    """G2/G3 不降级 + 邻域恒定：ZK 库上 g7e / g7j_n4 的 (ids, chars) 必须逐题相同。"""
    out: dict = {"levels": {}, "bad": []}
    tasks = {"g7e": _tasks(G7E), "g7j_n4": _tasks(G7J4), "g7j_n0": _tasks(G7J0)}
    base: dict[str, dict] = {}
    for level in (0, 1, 2):
        db = tmp / f"g2_l{level}.db"
        shutil.copy(ZK_DB, db)
        mem = _at_level(level, db)
        got: dict[str, dict] = {}
        for suite, ts in tasks.items():
            for t in ts:
                got[f"{suite}:{t['id']}"] = _sig(mem, t["prompt"])
        mem.close()
        out["levels"][f"L{level}"] = got
        if level == 0:
            base = got
    # 逐题比 (ids, chars)。
    # ⚠️ 自踩（跑完第一版才发现）：这里第一版拿**全部**题与 L0 比，于是「L0 零送达、
    #   现在送达了」也被记成「不同」⇒ 把**处理本身**读成了降级。预注册 G2/G3 的作用域
    #   写的是「旧读端**已送达**的题」，代码比预注册宽 ⇒ 以预注册为准（纪律 1）。
    scope = [k for k, v in base.items() if v["ids"]]
    out["scope_n"] = len(scope)
    for level in (1, 2):
        cur = out["levels"][f"L{level}"]
        diff = [k for k in scope if cur.get(k) != base.get(k)]
        new = [k for k in base if not base[k]["ids"] and cur.get(k, {}).get("ids")]
        out.setdefault("diff_vs_L0", {})[f"L{level}"] = diff
        out.setdefault("newly_delivered", {})[f"L{level}"] = new
        out["bad"] += diff
    out["delivered"] = {
        "g7e": sum(1 for k, v in base.items() if k.startswith("g7e:") and v["ids"]),
        "g7j_n4": sum(1 for k, v in base.items() if k.startswith("g7j_n4:") and v["ids"]),
    }
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", type=str, default="")
    a = ap.parse_args(argv)
    tmp = Path(tempfile.mkdtemp(prefix="v84_gate_"))
    g14 = gate1_and_4(tmp)
    g23 = gate2_and_3(tmp)
    rep = {"gate1_g4": g14, "gate2_g3": g23}

    print("=" * 74)
    print("G1 送达（struct 4 题，受控池 4 经验 + 8 无关，k=6）")
    for lv, d in g14["by_level"].items():
        print(f"  {lv}: hit4={d['hit4']}/4  fallbacks={d['char_fallbacks']} hits={d['char_hits']}")
        for tid, p in d["per_task"].items():
            print(f"     {tid:20s} n={p['n']} own={p['own']} chars={p['chars']}")

    print("=" * 74)
    print("G4 噪音地板（A5 真库副本，3 条与题无关的 worldmodel 事实）")
    for lv, d in (g14.get("noise") or {}).items():
        print(f"  {lv}: sum={d['sum']} max={d['max']} per_task={d['per_task']}")

    print("=" * 74)
    print("G2/G3 不降级 + 邻域恒定（ZK 库，旧读端已送达的题）")
    print(f"  L0 送达：g7e={g23['delivered']['g7e']}/4  g7j_n4={g23['delivered']['g7j_n4']}/4")
    print(f"  作用域 = 旧读端已送达的题 {g23.get('scope_n')} 道")
    for lv, diff in g23.get("diff_vs_L0", {}).items():
        print(f"  {lv} 作用域内与 L0 不同的题：{diff or '无'}")
        print(f"  {lv} 新送达（L0 零命中、现在非空）：{g23.get('newly_delivered', {}).get(lv)}")

    if a.json:
        Path(a.json).write_text(json.dumps(rep, ensure_ascii=False, indent=1),
                                encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
