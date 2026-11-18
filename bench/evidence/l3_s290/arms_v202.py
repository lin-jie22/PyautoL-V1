#!/usr/bin/env python3
"""V202 装置：给累积的**生产端**通电 —— distill 会产出什么，读侧又取不取得到。

预注册见同目录 `PREREG_v202.md`。三段：
  A 原材料普查（免费）  —— limit=500 的截断量、kind×形态
  B 真 LLM 蒸馏（付费） —— DistillStats 全字段 + 逐条产出原文
  C 读侧探针（免费）    —— 直接 recall 探针 + `Core(memory_mode="abstract")` 的 `[经验]` 块

⛔ 不改内核。⛔ 不动 `state/memory.db`（只 shutil.copy 一份到 /tmp 再开，纪律 31）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
import collections
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

SRC_DB = ROOT / "state" / "memory.db"
SUITE = ROOT / "bench" / "tasks" / "coding_small.jsonl"

#: P5 口径：什么算「具体约束词」。⛔ 先写口径再数（纪律 89）。
_MECH = re.compile(r"[0-9０-９]|排序|去重|边界|空|重叠|进制|符号|前缀|后缀|长度|顺序|精度|格式|转义|编码")
_CONSTRAINT_WORDS = ("排序", "去重", "边界", "空", "重叠", "进制", "符号",
                     "前缀", "后缀", "长度", "顺序", "精度", "格式", "转义", "编码")


def fp(p: Path) -> dict:
    st = p.stat()
    return {"size": st.st_size, "mtime": round(st.st_mtime, 3),
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest()[:16]}


# ---------------------------------------------------------------- 段 A
def seg_a() -> dict:
    import sqlite3
    c = sqlite3.connect(f"file:{SRC_DB}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    total = c.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
    kinds_all = {r["kind"]: r["n"] for r in
                 c.execute("SELECT kind, COUNT(*) n FROM episodes GROUP BY kind")}
    vis = [dict(r) for r in c.execute(
        "SELECT id, kind, content FROM episodes ORDER BY id DESC LIMIT 500")]
    vis.sort(key=lambda e: (0 if e["kind"] == "failure" else 1, -e["id"]))  # == _source_episodes
    vis_kinds = dict(collections.Counter(e["kind"] for e in vis))
    blank = [e for e in vis if not (e["content"] or "").strip()]
    tiny = [e for e in vis if 0 < len((e["content"] or "").strip()) < 8]
    # 输入形态（⛔ 不是闸口径：闸加在 LLM 输出上）
    in_code = sum(1 for e in vis if "```" in (e["content"] or ""))
    in_ident = sum(1 for e in vis if re.search(r"\b[A-Za-z_][A-Za-z0-9]*_[A-Za-z0-9_]+\b",
                                               e["content"] or ""))
    outcome_txt = collections.Counter((e["content"] or "").strip()[:20]
                                      for e in vis if e["kind"] == "outcome")
    # failure 里「真的是坑」还是「基础设施故障」
    fail = [e for e in vis if e["kind"] == "failure"]
    infra = [e for e in fail if re.search(
        r"timed out|TimeoutError|调用失败|未交付|编译不过|ConnectionError", e["content"] or "")]
    return {
        "src_db": fp(SRC_DB),
        "total": total, "kinds_all": kinds_all,
        "visible_n": len(vis), "visible_ratio": round(len(vis) / total, 4),
        "visible_kinds": vis_kinds,
        "visible_id_lo": min((e["id"] for e in vis), default=0),
        "visible_id_hi": max((e["id"] for e in vis), default=0),
        "blank": len(blank), "tiny": len(tiny),
        "input_side_code": in_code, "input_side_ident": in_ident,
        "outcome_top": outcome_txt.most_common(3),
        "failure_n": len(fail), "failure_infra": len(infra),
        "failure_infra_ratio": round(len(infra) / max(1, len(fail)), 4),
        "first3": [(e["id"], e["kind"], (e["content"] or "")[:80]) for e in vis[:3]],
    }


# ---------------------------------------------------------------- 段 B
class SpyLLM:
    """包一层真后端，把每次 (输入首行, 输出) 记下来 —— 纪律 88 的哑火自检靠它。"""

    def __init__(self, inner):
        self.inner = inner
        self.calls: list[tuple[str, str]] = []

    def complete(self, messages, *, max_tokens=512, temperature=None, timeout_s=None):
        out = self.inner.complete(messages, max_tokens=max_tokens,
                                  temperature=temperature, timeout_s=timeout_s)
        head = ""
        for m in messages:
            if getattr(m, "role", "") == "user":
                head = str(getattr(m, "content", ""))[:120]
        self.calls.append((head, str(out)))
        return out


def seg_b(max_eps: int, mem_dir: Path) -> dict:
    from mw import config as cfg
    from mw.llm import build_llm
    from mw.memory import MemoryStore
    from mw.abstractor import distill, ABSTRACT_SOURCE, ABSTRACT_MODE  # noqa: F401

    mem_dir.mkdir(parents=True, exist_ok=True)
    db = mem_dir / "memory.db"
    if db.exists():
        db.unlink()
    shutil.copy(SRC_DB, db)                      # 纪律 31
    if max_eps > 0:
        import sqlite3
        c = sqlite3.connect(db)
        c.execute("DELETE FROM episodes WHERE id NOT IN "
                  "(SELECT id FROM episodes ORDER BY id DESC LIMIT ?)", (max_eps,))
        c.commit()
        c.close()

    conf = cfg.LLMConfig()
    usable = conf.usable()
    llm = build_llm(conf)
    spy = SpyLLM(llm)
    with MemoryStore(db) as mem:
        before = {r[0]: r[1] for r in mem.conn.execute(
            "SELECT source, COUNT(*) FROM facts GROUP BY source")}
        n_ep = mem.conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
        t0 = time.monotonic()
        st = distill(mem, spy, max_tokens=160)
        dt = time.monotonic() - t0
        after = {r[0]: r[1] for r in mem.conn.execute(
            "SELECT source, COUNT(*) FROM facts GROUP BY source")}
        rows = [dict(r) for r in mem.conn.execute(
            "SELECT id, text, source, confidence FROM facts WHERE source=? ORDER BY id",
            (ABSTRACT_SOURCE,))]
        allfacts = [dict(r) for r in mem.conn.execute("SELECT id,text,source FROM facts")]
        from mw.abstractor import abstract_only
        class _F:
            def __init__(self, d): self.__dict__.update(d)
        picked = abstract_only([_F(d) for d in allfacts])

    outs = [o for _, o in spy.calls]
    mech = [t for t in (r["text"] for r in rows) if _MECH.search(t or "")]
    return {
        "llm_usable": usable, "model": conf.model, "base_url": conf.base_url,
        "episodes_seen": n_ep, "stats": st.as_dict(), "elapsed_s": round(dt, 1),
        "n_calls": len(spy.calls),
        "n_distinct_outputs": len(set(outs)),
        "n_distinct_inputs": len({h for h, _ in spy.calls}),
        "facts_before": before, "facts_after": after,
        "abstract_rows": len(rows),
        "kept_minus_new_rows": st.kept - (after.get(ABSTRACT_SOURCE, 0)
                                         - before.get(ABSTRACT_SOURCE, 0)),
        "abstract_texts": [r["text"] for r in rows],
        "abstract_lens": [len(r["text"] or "") for r in rows],
        "mech_hits": len(mech), "mech_ratio": round(len(mech) / max(1, len(rows)), 4),
        "abstract_only_n": len(picked),
        "examples_dropped": st.examples_dropped_code,
        "samples_out": [o[:120] for o in outs[:8]],
        "samples_out_tail": [o[:120] for o in outs[-4:]],
        "mem_dir": str(mem_dir),
    }


# ---------------------------------------------------------------- 段 C
def _cores(suite_path: Path):
    from mw.bench import Suite
    return Suite.load(suite_path)


def seg_c(b: dict, mem_dir: Path) -> dict:
    from mw import config as cfg
    from mw.memory import MemoryStore
    from mw.core import Core
    from mw.guard import Guard
    from mw.llm import StubLLM
    from mw.abstractor import ABSTRACT_MODE
    from mw.bench import Suite

    su = Suite.load(SUITE)
    goals = [t.prompt for t in su.tasks]
    db = Path(b["mem_dir"]) / "memory.db"

    # C0：直接 recall 探针（不经过 Core，只问「取不取得到」）
    rk = int(cfg.settings().limits.recall_k)
    probe = []
    with MemoryStore(db) as mem:
        for t, g in zip(su.tasks, goals):
            f0, c0, h0 = mem.fts_fallbacks, mem.char_fallbacks, mem.char_hits
            got = mem.recall(g, k=rk)
            probe.append({
                "id": t.id, "n": len(got),
                "sources": [f.source for f in got],
                "abstract": sum(1 for f in got if f.source == "abstract"),
                "fallbacks": mem.fts_fallbacks - f0,
                "char_fallbacks": mem.char_fallbacks - c0,
                "char_hits": mem.char_hits - h0,
                "texts": [f.text[:70] for f in got],
            })

    # C1：走真入口（Core + memory_mode=abstract + StubLLM）
    per = []
    for t in su.tasks:
        mem = MemoryStore(db)
        sc = cfg.settings()
        guard = Guard(allow_shell=False, workspace=sc.paths.workspace,
                      max_consecutive_failures=sc.limits.max_consecutive_failures)
        core = Core(sc, llm=StubLLM(), memory=mem, guard=guard,
                    memory_mode=ABSTRACT_MODE)
        try:
            core.run(t.prompt, task_id=t.id, family=su.name)
        except Exception as e:                    # noqa: BLE001 — 装置不许因单题挂掉
            per.append({"id": t.id, "error": f"{type(e).__name__}: {e}"})
            mem.close()
            continue
        exp = []
        for _, txt in (core.state_log or []):
            for ln in str(txt).splitlines():
                if ln.startswith("[经验]"):
                    exp.append(ln[len("[经验]"):].strip())
        per.append({"id": t.id, "n_exp_blocks": len(exp),
                    "exp_chars": [len(x) for x in exp],
                    "first_exp": (exp[0][:100] if exp else ""),
                    "layer_view": dict(core.memory_layer_view),
                    "steps": len(core.state_log or [])})
        mem.close()
    return {"recall_probe": probe, "core": per}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-eps", type=int, default=0, help="0=全部可见的 500 条")
    ap.add_argument("--mem-dir", default="/tmp/v202_mem")
    ap.add_argument("--tag", default="")
    a = ap.parse_args()

    guard_before = fp(SRC_DB)

    print("[A] 原材料普查")
    A = seg_a()
    print(f"    全库 episodes={A['total']}  {A['kinds_all']}")
    print(f"    distill 可见 {A['visible_n']} 条（{A['visible_ratio']:.1%}，id "
          f"{A['visible_id_lo']}..{A['visible_id_hi']}）{A['visible_kinds']}")
    print(f"    空={A['blank']} 太短(<8)={A['tiny']}  输入侧含代码={A['input_side_code']} "
          f"含标识符={A['input_side_ident']}")
    print(f"    outcome 首 20 字 top3={A['outcome_top']}")
    print(f"    failure={A['failure_n']} 其中基础设施类={A['failure_infra']} "
          f"({A['failure_infra_ratio']:.1%})")

    print("\n[B] 真 LLM 蒸馏")
    B = seg_b(a.max_eps, Path(a.mem_dir))
    print(f"    llm_usable={B['llm_usable']} model={B['model']} 原料={B['episodes_seen']} 条")
    print(f"    stats={B['stats']}")
    print(f"    调用 {B['n_calls']} 次 / 相异输入 {B['n_distinct_inputs']} / "
          f"相异输出 {B['n_distinct_outputs']} / 耗时 {B['elapsed_s']}s")
    print(f"    facts {B['facts_before']} → {B['facts_after']}；abstract 行={B['abstract_rows']}；"
          f"kept−新增行={B['kept_minus_new_rows']}")
    print(f"    abstract_only() 取回 {B['abstract_only_n']} 条")
    for t, ln in zip(B["abstract_texts"][:12], B["abstract_lens"][:12]):
        print(f"      · [{ln:3d}] {t!r}")
    print(f"    含具体约束词 {B['mech_hits']}/{B['abstract_rows']} = {B['mech_ratio']:.1%}")
    if B["examples_dropped"]:
        print(f"    拒收样例: {B['examples_dropped'][:3]}")

    print("\n[C] 读侧")
    C = seg_c(B, Path(a.mem_dir))
    print("    C0 直接 recall 探针：")
    for p in C["recall_probe"]:
        print(f"      {p['id']:20s} 取回={p['n']} abstract={p['abstract']} "
              f"来源={collections.Counter(p['sources'])} 字级={p['char_fallbacks']}/{p['char_hits']}")
        for tx in p["texts"][:2]:
            print(f"          · {tx}")
    print("    C1 Core(memory_mode=abstract)：")
    for x in C["core"]:
        if "error" in x:
            print(f"      {x['id']:20s} ERROR {x['error']}")
        else:
            print(f"      {x['id']:20s} [经验]块={x['n_exp_blocks']} 字符={x['exp_chars']} "
                  f"layer={x['layer_view']} steps={x['steps']}")

    guard_after = fp(SRC_DB)
    clean = guard_before == guard_after
    print(f"\n[隔离] state/memory.db 跑前跑后一致：{clean}")
    if not clean:
        print(f"     ⛔ {guard_before} → {guard_after}")

    tag = f"_{a.tag}" if a.tag else ""
    payload = {"a_material": A, "b_distill": B, "c_read": C,
               "isolation": {"clean": clean, "before": guard_before, "after": guard_after},
               "max_eps": a.max_eps}
    (OUT / f"readings_v202{tag}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n→ {OUT / f'readings_v202{tag}.json'}")
    return 0 if clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
