#!/usr/bin/env python3
"""★7v-d 形态取证（零模型）：g7e2 的补救答案长什么样，四臂差异在哪。

只看分数看不见"怎么错的" —— 7v-c 已证明同分不同过程（先错后对 vs 一次就对）。
本文件回答：S1/S2/S3 的 g7e2 是**答不出**还是**答成另一种错法**。
"""
import json
import re

D = "/tmp/v1_7vd"
ARMS = ("A", "S1", "S2", "S3", "B")
TAGS = ("n0", "r1", "r2", "r3")

for arm in ARMS:
    print(f"===== 臂 {arm} =====")
    for tag in TAGS:
        p = f"{D}/cap{arm}_{tag}.jsonl"
        try:
            evs = [json.loads(l) for l in open(p, encoding="utf-8")]
        except FileNotFoundError:
            print(f"  {tag}: 无捕获")
            continue
        seq = []
        for i, ev in enumerate(evs):
            if not (ev["path"].endswith("/chat/completions") and "#resp" not in ev["path"]):
                continue
            msgs = json.loads(ev["body"]).get("messages", [])
            if not msgs or "只输出最终答案本身" not in msgs[0].get("content", ""):
                continue
            if "truncate" not in msgs[-1]["content"]:
                continue
            for j in range(i + 1, min(i + 3, len(evs))):
                if "#resp" in evs[j]["path"]:
                    try:
                        r = json.loads(evs[j]["body"])["choices"][0]["message"]["content"]
                    except Exception:
                        r = str(evs[j]["body"])[:200]
                    tail = re.findall(r"s\[-\(?([^\]]+?)\)?\]", r)
                    has_strip = "strip().lower()" in r
                    # ⛔ 切片文本是 `s[-n:]` / `s[-(n-3):]` ⇒ 捕获含冒号：'n:' 才是对的。
                    seq.append(("OK" if tail == ["n:"] else "BAD") +
                               f"(尾={tail or '—'}{',去重渗漏' if has_strip else ''})")
                    break
        last = seq[-1].startswith("OK") if seq else None
        print(f"  {tag}: {len(seq)} 次补救 → " + " ".join(seq) +
              ("   ← 末次对" if last else "   ← 末次错（决定得分）"))
