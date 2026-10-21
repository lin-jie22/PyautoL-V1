#!/usr/bin/env python3
"""★7w 形态取证（零模型）：g7e2 的补救序列 —— 反馈有没有让它"少走一轮/改对"。

7v-e 的教训：分数看不见"先错后对"与"从不纠正"。本轮额外记**每次补救是否带了反馈**
（只有第 2 次及以后才带），以便把"第 1 次就错"与"第 2 次改对"分开。
"""
import json
import re

D = "/tmp/v1_7w"
ARMS = ("A", "B0", "W1", "W2", "W3")
TAGS = ("n0", "r1", "r2", "r3")
FAIL_MARK = "上一次答案未通过："
ANS_MARK = "上一次的答案（已被否决）："

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
            u = msgs[-1]["content"]
            fb = ("f" if FAIL_MARK in u else "") + ("a" if ANS_MARK in u else "")
            for j in range(i + 1, min(i + 3, len(evs))):
                if "#resp" in evs[j]["path"]:
                    try:
                        r = json.loads(evs[j]["body"])["choices"][0]["message"]["content"]
                    except Exception:
                        r = str(evs[j]["body"])[:200]
                    tail = re.findall(r"s\[-\(?([^\]]+?)\)?\]", r)
                    has_strip = "strip().lower()" in r
                    seq.append(("OK" if tail == ["n:"] else "BAD") +
                               f"[{fb or '-'}](尾={tail or '—'}{',去重渗漏' if has_strip else ''})")
                    break
        last = seq[-1].startswith("OK") if seq else None
        print(f"  {tag}: {len(seq)} 次补救 → " + " ".join(seq) +
              ("   ← 末次对" if last else ("   ← 末次错（决定得分）" if seq else "   ← 无补救")))
