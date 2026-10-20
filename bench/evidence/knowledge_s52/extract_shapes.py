#!/usr/bin/env python3
"""★7u 定向取证（零模型）：g7e2_truncate_tail 在三臂下的**通道**与**答案形态**。

为什么单独做这个：分析器只给分（0.2/1.0），分不能告诉你"走了哪条通道"。
A 臂窗内 0.2/1.0/0.2 的摇摆，不看通道就无法区分"模型 flaky"与"路径分叉"。
"""
import json
import re

D = "/tmp/v1_7u"


def resp_of(evs, i):
    for j in range(i + 1, min(i + 3, len(evs))):
        if "#resp" in evs[j]["path"]:
            try:
                return json.loads(evs[j]["body"])["choices"][0]["message"]["content"]
            except Exception:
                return str(evs[j]["body"])[:200]
    return ""


for arm in ("A", "B", "C"):
    evs = [json.loads(l) for l in open(f"{D}/cap{arm}.jsonl", encoding="utf-8")]
    print(f"===== 臂 {arm} =====")
    for i, ev in enumerate(evs):
        if not (ev["path"].endswith("/chat/completions") and "#resp" not in ev["path"]):
            continue
        msgs = json.loads(ev["body"]).get("messages", [])
        if not msgs or "只输出最终答案本身" not in msgs[0].get("content", ""):
            continue
        u = msgs[-1]["content"]
        if "truncate" not in u:
            continue
        r = resp_of(evs, i)
        # 判据焦点：取的是最后 n 个字符（对）还是 n-3（错）；是否渗漏 ZK1 的归一化
        tail = re.findall(r"s\[-([^\]]+)\]", r)
        strip = "strip().lower()" in r
        print(f"  req{i:>4}  尾部={tail or '—'}  ZK1渗漏={'是' if strip else '否'}  "
              f"指令句={'消歧' if '只采用与本次目标直接相关' in u else ('中性' if '按原文顺序依次阅读' in u else '无')}")
        print("        " + r.replace("\n", " ⏎ ")[:220])
