#!/usr/bin/env python3
"""★7v-c 答案形态取证（零模型）：g7e2 的每次补救响应长什么样。

7v-b 的教训：A 与 C1 同分（0.70）但**过程不同**（A 第一次补救错、C1 第一次就对）
⇒ 只记分数会看不见。本文件把三件事逐次打出来：
  1. 尾部切片形态（`s[-n:]` = 对 / `s[-(n-3):]` = 错）；
  2. ZK1（去重）动作是否渗漏进 ZK4（截断）的答案里（`strip().lower()`）；
  3. 四臂的形态**序列**（用于判"同分不同过程"）。
"""
import json
import re

D = "/tmp/v1_7vc"
ARMS = (("A", "capA.jsonl"), ("P1", "capP1.jsonl"),
        ("P2", "capP2.jsonl"), ("B", "capB.jsonl"))
seq = {}

for arm, cap in ARMS:
    evs = [json.loads(l) for l in open(f"{D}/{cap}", encoding="utf-8")]
    print(f"===== 臂 {arm} =====")
    seq[arm] = []
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
                tail = re.findall(r"s\[-([^\]]+)\]", r)
                leak = "是" if "strip().lower()" in r else "否"
                tag = (tail[0] if tail else "—")
                seq[arm].append("OK" if tag.startswith("n:") or tag == "n:" else
                                ("BAD" if tail else "—"))
                print(f"  req{i:>4} 尾部={tail or '—'} ZK1渗漏={leak}")
                print("        " + r.replace("\n", " ⏎ ")[:200])
                break

print("\n===== 形态序列（对/错）=====")
for arm in ("A", "P1", "P2", "B"):
    print(f"  {arm:2s}: {' → '.join(seq[arm]) or '（无补救）'}")
