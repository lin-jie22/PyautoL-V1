#!/usr/bin/env python3
"""★7z 多样性取证（**零模型**）：答案是"生成"出来的，还是"挑"出来的？

第五十七轮（7y）留下一个读数：**354 次交付 = 28 份互异答案**（每道题 6~9 份）。
当时只登记、没解释。本脚本把它拆成三个可判的问句：

    Q1 **采样ularity**：内核到底向模型要了几种不同的请求？（去重后的请求体）
    Q2 **确定性**：同一个请求体重复出现时，模型给了几种不同的回答？
    Q3 **通道差异**：规划通道（propose）与答案通道（answer）各自的多样性是多少？

为什么这三问决定下一步：

  · Q1 小 + Q2 ≈ 1  ⇒ 多样性瓶颈在**内核**（要得少、或者同样地问）
     ⇒ 该动的是候选生成，而不是提示词
  · Q1 大 + Q2 ≈ 1  ⇒ 提示词已经千变万化，答案集合却窄 ⇒ 瓶颈在**模型/解码**
     ⇒ 该动的是采样参数（temperature / n），但那样你就得先有一个选择器，
        而 7y 已经证明：题面派生的判据**不具选择能力**
  · Q2 明显 > 1    ⇒ T=0 也不确定 ⇒ 之前那些"同窗极差 0"要重新读

⛔ 不调模型，只读归档 cap（`bench/evidence/knowledge_*/cap*.jsonl`）。
"""
import glob
import json
import os
from collections import Counter, defaultdict

ROOT = "/Users/linshaojie/V1"
FILES = [p for p in sorted(glob.glob(f"{ROOT}/bench/evidence/knowledge_*/cap*.jsonl"))
         if "knowledge_s60" not in p and "knowledge_s61" not in p]

FUNCS = {"g7d2_dedupe_keep": "dedupe_keep", "g7d3_fmt_amount": "fmt_amount",
         "g7e1_parse_flag": "parse_flag", "g7e2_truncate_tail": "truncate_tail"}


def _sys_of(body):
    try:
        msgs = json.loads(body).get("messages", [])
    except ValueError:
        return ""
    return msgs[0].get("content", "") if msgs else ""


def _user_of(body):
    try:
        msgs = json.loads(body).get("messages", [])
    except ValueError:
        return ""
    return msgs[-1].get("content", "") if msgs else ""


def _resp_text(body):
    try:
        return json.loads(body)["choices"][0]["message"].get("content", "") or ""
    except (KeyError, ValueError, TypeError, IndexError):
        return ""


ANSWER = {}     # (task, body_sha) -> {resp_sha: n}
PLAN = {}       # (task, body_sha) -> {resp_sha: n}
TASKS = Counter()
FIELDS = Counter()
TEMPS = Counter()
N_FIELD = Counter()

for p in FILES:
    pend = None
    for line in open(p, encoding="utf-8"):
        ev = json.loads(line)
        path = ev.get("path", "")
        if "#resp" in path:
            if pend is None:
                continue
            txt = _resp_text(ev.get("body", ""))
            key = (pend[0], pend[1])
            bucket = ANSWER if pend[2] == "answer" else PLAN
            bucket.setdefault(key, Counter())[
                _sha := __import__("hashlib").sha256(
                    txt.encode("utf-8")).hexdigest()[:12]] += 1
            pend = None
            continue
        if not path.endswith("/chat/completions"):
            continue
        body = ev.get("body", "")
        sys0 = _sys_of(body)
        if "只输出最终答案本身" not in sys0 and "规划器" not in sys0:
            pend = None
            continue
        chan = "answer" if "只输出最终答案本身" in sys0 else "plan"
        u = _user_of(body)
        tid = next((t for t, f in FUNCS.items() if f in u), None)
        if tid is None:
            pend = None
            continue
        try:
            b = json.loads(body)
            FIELDS[tuple(sorted(b.keys()))] += 1
            TEMPS[b.get("temperature", "<absent>")] += 1
            N_FIELD[b.get("n", "<absent>")] += 1
        except ValueError:
            pass
        TASKS[tid] += 1
        pend = (tid, __import__("hashlib").sha256(
            body.encode("utf-8")).hexdigest()[:12], chan)

print(f"cap {len(FILES)} 个；命中这四道题的请求 {sum(TASKS.values())} 次")
print()
print("### Q0 传输层实际带了什么")
print(f"  请求体字段组合：{FIELDS.most_common(2)}")
print(f"  temperature   ：{TEMPS.most_common(4)}")
print(f"  n 字段        ：{N_FIELD.most_common(4)}")
print()

for name, bucket in (("答案通道 answer()", ANSWER), ("规划通道 propose()", PLAN)):
    print(f"### {name}")
    print(f"  {'题':22s} {'去重请求':>8s} {'去重响应':>8s} {'响/请':>7s} "
          f"{'重复请求数':>10s} {'其中同请>1答':>12s}")
    tot_req = tot_resp = 0
    for tid in FUNCS:
        rows = defaultdict(Counter)
        for (t, _b), c in bucket.items():
            if t != tid:
                continue
            for r, k in c.items():
                rows[t][r] = rows[t][r] + 0
                rows[t][r] += k
        # 重建：(task, body) -> Counter(resp)
        per_body = defaultdict(Counter)
        for (t, b), c in bucket.items():
            if t != tid:
                continue
            for r, k in c.items():
                per_body[b][r] += k
        uniq_resp = {r for b in per_body for r in per_body[b]}
        repeated = [b for b in per_body if sum(per_body[b].values()) > 1]
        multi = [b for b in repeated if len(per_body[b]) > 1]
        tot_req += len(per_body)
        tot_resp += len(uniq_resp)
        ratio = len(uniq_resp) / max(1, len(per_body))
        print(f"  {tid:22s} {len(per_body):8d} {len(uniq_resp):8d} {ratio:7.2f} "
              f"{len(repeated):10d} {len(multi):12d}")
    print(f"  {'小计':22s} {tot_req:8d} {tot_resp:8d}")
    print()

print("### Q2 直读：**同一个请求体被重复问了多少次，给出几种回答**")
print("（只列重复出现 ≥2 次的请求体；回答种类 1 = 完全确定，>1 = 同请求翻面）")
agg = Counter()
for name, bucket in (("answer", ANSWER), ("plan", PLAN)):
    per_body = defaultdict(Counter)
    for (t, b), c in bucket.items():
        for r, k in c.items():
            per_body[(t, b)][r] += k
    dist = Counter(len(per_body[b]) for b in per_body
                   if sum(per_body[b].values()) > 1)
    tot_bodies = sum(dist.values())
    print(f"  {name:7s} 重复请求体 {tot_bodies:4d} 个 ⇒ 回答种类分布 "
          + ", ".join(f"{k}种×{v}" for k, v in sorted(dist.items())))
    for k, v in dist.items():
        agg[k] += v
print(f"  {'合计':7s} 重复请求体 {sum(agg.values()):4d} 个 ⇒ "
      + ", ".join(f"{k}种×{v}" for k, v in sorted(agg.items())))
one = agg.get(1, 0)
print(f"  ⇒ 同请求得到**完全相同**回答的比例 = "
      f"{one / max(1, sum(agg.values())):.1%}")
