#!/usr/bin/env python3
"""★7s 零模型取证：g7e2(n4) 残留归因 —— 从 7r 捕获对撞 n0/n4 补救响应。"""
import json
import sys

def pairs(path):
    reqs, out = [], []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        ev = json.loads(line)
        if "#resp" in ev["path"]:
            if reqs:
                out.append((reqs.pop(0), json.loads(ev["body"])))
        elif ev["path"].endswith("/chat/completions"):
            reqs.append(json.loads(ev["body"]))
    return out

ANS_SYS = "只输出最终答案本身"
cap = pairs(sys.argv[1])
n_remedy = n_zk4 = 0
for i, (req, resp) in enumerate(cap):
    msgs = req["messages"]
    user = msgs[-1]["content"]
    if ANS_SYS in msgs[0]["content"] and "truncate_tail" in user:
        n_remedy += 1
        multi = "（ZK1）" in user and "（ZK4）" in user
        content = resp["choices"][0]["message"]["content"]
        leak = "strip().lower()" in content          # ZK1 动作渗漏
        drift = "n-3" in content or "n - 3" in content  # 省略号计入 n
        clean = "'…' + s[-n:]" in content or "'…'+s[-n:]" in content
        if multi:
            n_zk4 += 1
        tag = "n4(4条)" if multi else "n0(1条)"
        print(f"#{i} [{tag}] 渗漏={leak} 折中={drift} 正确形状={clean}")
        print(f"    代码: {' '.join(content.split())[:150]}")
print(f"\n补救请求总数={n_remedy}（其中多惯例块={n_zk4}）")
print("判定: n0 全部正确形状 / n4 全部渗漏或折中 ⇒ H-b 成立（跨惯例干扰）")
