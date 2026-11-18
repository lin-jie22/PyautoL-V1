#!/usr/bin/env python3
"""V203：池子天花板 vs 匹配器上限。

三个匹配器同一池子、同一批题、同一 k=6：
  M0 部署默认（FTS，char_level=0）
  M1 字面二级（V1_RECALL_CHAR=2）
  M2 向量（nomic-embed-text, cos top-k）

⛔ 不改产品代码：M2 只在装置进程内算分排序。
⛔ 不调 LLM 生成：只调 embedding。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.request
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

DB = Path("/tmp/v202_full/memory.db")
SUITE = ROOT / "bench/tasks/coding_small.jsonl"
OUT = Path(__file__).resolve().parent
K = 6
EMB_URL = "http://127.0.0.1:11434/api/embed"
EMB_MODEL = "nomic-embed-text"

# 预注册冻结的结构词表（α 口径）
STRUCT = {
    "t01_parse_duration": ["空串", "None", "秒", "解析"],
    "t02_dedupe": ["重复", "顺序", "首次", "保序"],
    "t03_commas": ["千分位", "逗号"],
    "t04_merge_ranges": ["重叠", "区间", "起点", "升序", "合并"],
    "t05_top_k": ["次数", "排序", "升序", "前 k"],
    "t06_rle": ["游程", "编码", "连续"],
}
# 自指：讲系统自己的流程，不讲题目结构
SELF = re.compile(
    r"交付前检查|自检|预演|停滞|模型调用失败|编译|通信|外部 agent|重构|提交|"
    r"函数定义完整|函数实现|依赖函数|必需函数|基础函数|功能实现|"
    r"补充缺失信息|资源|网络连接|输入数据是否|当前网络|语言标签|题目要求的语言"
)


def embed(texts: list[str]) -> list[list[float]]:
    req = urllib.request.Request(
        EMB_URL,
        data=json.dumps({"model": EMB_MODEL, "input": texts}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=180) as fh:
        return json.load(fh)["embeddings"]


def cos(a: list[float], b: list[float]) -> float:
    s = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return s / (na * nb) if na and nb else 0.0


def clusters(texts: list[str], thr: float = 0.72) -> list[int]:
    """difflib 归簇，返回每条的簇 id（V202 同口径）。"""
    n = len(texts)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(n):
        for j in range(i + 1, n):
            if SequenceMatcher(None, texts[i], texts[j]).ratio() >= thr:
                a, b = find(i), find(j)
                if a != b:
                    parent[b] = a
    return [find(i) for i in range(n)]


def main() -> int:
    # ---- 装置自检：库对不对（预注册 §5.4）----
    import sqlite3

    if not DB.exists():
        print(f"⛔ 池子不存在：{DB}")
        return 2
    mt = DB.stat().st_mtime
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    facts = [dict(r) for r in con.execute(
        "select id,text,hits from facts where source='abstract' order by id")]
    con.close()
    print(f"[自检] 池子 {DB} mtime={mt:.0f} 条数={len(facts)}")
    if len(facts) != 67:
        print(f"⛔ 条数不是 67（{len(facts)}）⇒ 中止，不猜。")
        return 2

    from mw.bench import Suite
    su = Suite.load(SUITE)
    tasks = su.tasks
    print(f"[自检] 题数={len(tasks)} k={K}")

    texts = [f["text"] for f in facts]
    cl = clusters(texts)
    print(f"[自检] 归簇：{len(set(cl))} 簇（difflib≥0.72，V202 口径）")

    # ---- M0 / M1：真 recall ----
    from mw.memory import MemoryStore

    from mw.abstractor import abstract_only

    def run_literal(char_level: int) -> dict:
        """⭐ 必须走 abstract_only —— 部署在 ABSTRACT_MODE 下会过滤（core.py:2242）。
        第一版漏了这一步，M0 拿到 1 条 `encoding`（source=selftest），是装置错不是通道错。"""
        os.environ["V1_RECALL_CHAR"] = str(char_level)
        out = []
        with MemoryStore(str(DB)) as mem:
            for t in tasks:
                got = mem.recall(t.prompt, k=K)
                raw = [{"text": f.text, "source": f.source} for f in got]
                kept = [{"text": f.text, "source": f.source} for f in abstract_only(got)]
                out.append({"id": t.id, "items": kept, "n_raw": len(raw)})
        return {"char_level": char_level, "per_task": out,
                "n_total": sum(len(x["items"]) for x in out),
                "n_raw_total": sum(x["n_raw"] for x in out)}

    m0 = run_literal(0)
    m1 = run_literal(2)
    os.environ.pop("V1_RECALL_CHAR", None)
    print(f"\n[M0] 部署默认：合计命中 {m0['n_total']}")
    print(f"[M1] 字面二级：合计命中 {m1['n_total']}")

    # ---- M2：向量 ----
    prompts = [t.prompt for t in tasks]
    vecs = embed(texts + prompts)
    fv, tv = vecs[:len(texts)], vecs[len(texts):]
    sim = [[cos(tv[i], fv[j]) for j in range(len(texts))] for i in range(len(tasks))]

    m2 = []
    ranks = {}          # (task_i, fact_j) -> 排名（1-based）
    for i in range(len(tasks)):
        t = tasks[i]
        order = sorted(range(len(texts)), key=lambda j: -sim[i][j])
        for r_, j in enumerate(order, 1):
            ranks[(i, j)] = r_
        top = order[:K]
        # 去重 top-k：按簇，每簇只留最高分
        seen, dedup = set(), []
        for j in order:
            if cl[j] in seen:
                continue
            seen.add(cl[j])
            dedup.append(j)
            if len(dedup) >= K:
                break
        m2.append({
            "id": t.id,
            "top": [{"idx": j, "fid": facts[j]["id"], "text": texts[j],
                     "cos": round(sim[i][j], 4), "cluster": cl[j],
                     "self": bool(SELF.search(texts[j]))} for j in top],
            "dedup_top": [{"idx": j, "text": texts[j], "cos": round(sim[i][j], 4)}
                          for j in dedup],
            "max_cos": round(sim[i][order[0]], 4),
            "mean_cos": round(sum(sim[i]) / len(texts), 4),
        })

    # ---- P3：池内同质 vs 池-题 ----
    pairs = []
    for i in range(len(texts)):
        for j in range(i + 1, len(texts)):
            pairs.append(cos(fv[i], fv[j]))
    pool_mean = sum(pairs) / len(pairs)
    cross = [sim[i][j] for i in range(len(tasks)) for j in range(len(texts))]
    cross_mean = sum(cross) / len(cross)
    print(f"\n[P3] 池内两两平均 cos = {pool_mean:.4f}（{len(pairs)} 对）")
    print(f"     池-题平均 cos     = {cross_mean:.4f}（{len(cross)} 格）")

    # ---- α：结构词覆盖（机器口径，只报数）----
    def alpha_hit(tid: str, text: str) -> list[str]:
        return [w for w in STRUCT.get(tid, []) if w in text]

    alpha = {}
    for row in m2:
        tid = row["id"]
        alpha[tid] = [{"idx": x["idx"], "hit": alpha_hit(tid, x["text"])}
                      for x in row["top"]]
    alpha_n = sum(1 for row in m2 for x in alpha[row["id"]] if x["hit"])
    # P5：池子里能覆盖任意题结构词的条数
    pool_cov = [j for j in range(len(texts))
                if any(alpha_hit(tid, texts[j]) for tid in STRUCT)]
    print(f"\n[α] M2 36 格中结构词命中 {alpha_n} 格（有假阳，只作辅证）")
    print(f"[P5] 池内覆盖任意题结构词的条数 = {len(pool_cov)}/{len(texts)}")

    # ---- P4：M2 top-6 自指占比 ----
    self_n = sum(1 for row in m2 for x in row["top"] if x["self"])
    print(f"[P4] M2 top-6（36 格）自指 {self_n} 格 = {self_n/36:.1%}")

    # ---- 新增①：语义分辨率 = top1 与 top6 的分差 ----
    print("\n[分辨率] 每题 top1−top6 分差（同一条通道内能分开多少）")
    resol = []
    for i, row in enumerate(m2):
        d_ = row["top"][0]["cos"] - row["top"][K - 1]["cos"]
        resol.append(round(d_, 4))
        print(f"  {row['id']:20s} top1={row['top'][0]['cos']:.4f} "
              f"top6={row['top'][K-1]['cos']:.4f} 差={d_:.4f}")
    print(f"  平均分差 = {sum(resol)/len(resol):.4f}")

    # ---- 新增②：'泛化度' vs 入选次数 ----
    # 泛化度 = 该条对 6 题的平均 cos（越泛 ⇒ 与谁都像）
    gen = [sum(sim[i][j] for i in range(len(tasks))) / len(tasks)
           for j in range(len(texts))]
    pick_cnt = [sum(1 for i in range(len(tasks)) if ranks[(i, j)] <= K)
                for j in range(len(texts))]
    # Pearson
    n_ = len(texts)
    mg = sum(gen) / n_
    mp = sum(pick_cnt) / n_
    num = sum((gen[j] - mg) * (pick_cnt[j] - mp) for j in range(n_))
    den = (sum((gen[j] - mg) ** 2 for j in range(n_)) ** 0.5
           * sum((pick_cnt[j] - mp) ** 2 for j in range(n_)) ** 0.5)
    r_gen = num / den if den else 0.0
    print(f"\n[泛化偏好] corr(该条对 6 题平均 cos, 被选进 top-6 的次数) = {r_gen:+.3f}")
    top_gen = sorted(range(n_), key=lambda j: -gen[j])[:5]
    print("  最泛的 5 条（cos 均值 / 入选次数）：")
    for j in top_gen:
        print(f"    {gen[j]:.4f} / {pick_cnt[j]}  {texts[j]}")

    # ---- 新增③：M1 命中的强相关条，在 M2 里的排名 ----
    print("\n[对照] M1 命中条在 M2 里的排名（排名 >k 即被向量路丢掉）")
    cross_tab = []
    for ti, t in enumerate(tasks):
        m1_texts = [x["text"] for x in m1["per_task"][ti]["items"]]
        for txt in m1_texts:
            if txt not in texts:
                cross_tab.append({"task": t.id, "text": txt, "rank": None,
                                  "note": "非 abstract"})
                continue
            j = texts.index(txt)
            cross_tab.append({"task": t.id, "text": txt, "rank": ranks[(ti, j)],
                              "cos": round(sim[ti][j], 4)})
    for x in cross_tab:
        print(f"  {x['task']:20s} rank={x['rank']} cos={x.get('cos')} {x['text'][:40]}")

    # ---- 打印逐格原文，供人工 β 判读 ----
    print("\n" + "=" * 78)
    print("逐格原文（每格：cos ｜ 簇 ｜ 自指 ｜ 原文）")
    for row in m2:
        print(f"\n== {row['id']}  max={row['max_cos']}  mean={row['mean_cos']}")
        for r, x in enumerate(row["top"], 1):
            flag = "S" if x["self"] else " "
            print(f"  #{r} {x['cos']:.4f} c{x['cluster']:<3d}{flag} {x['text']}")
        print("  -- 去重 top-6 --")
        for x in row["dedup_top"]:
            print(f"     {x['cos']:.4f} {x['text']}")

    reading = {
        "pool": {"db": str(DB), "mtime": mt, "n_facts": len(facts),
                 "n_clusters": len(set(cl))},
        "m0": m0, "m1": m1, "m2": m2,
        "p3": {"pool_pair_mean": round(pool_mean, 4),
               "cross_mean": round(cross_mean, 4),
               "n_pairs": len(pairs), "n_cross": len(cross)},
        "alpha": {"cells_hit": alpha_n, "detail": alpha, "pool_cov": len(pool_cov),
                  "struct": STRUCT},
        "p4_self_cells": self_n,
        "resolution": {"per_task": resol, "mean": round(sum(resol) / len(resol), 4)},
        "generality": {"pearson_r": round(r_gen, 4),
                       "gen": [round(g, 4) for g in gen],
                       "pick_cnt": pick_cnt},
        "cross_tab": cross_tab,
        "k": K, "emb_model": EMB_MODEL, "emb_dim": len(fv[0]),
    }
    (OUT / "readings_v203.json").write_text(
        json.dumps(reading, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n→ {OUT/'readings_v203.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
