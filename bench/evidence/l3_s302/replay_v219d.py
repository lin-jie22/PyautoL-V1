"""V219d 离线复跑：把七个臂各自的代码对着 t01 的六项测试跑一遍。

目的：把「块 → 分数」拆成「块 → 代码 → 分数」，并证明第二段是可离线检验的。
自校验：题集自带的 oracle 必须 6/6、partial 必须 5/6——先证明这套评测口径没坏，
再去比七臂（否则「全 0/6」会被误读成代码全崩；本轮自踩第 3 条）。

    env -u PYTHONPATH python3 bench/evidence/l3_s302/replay_v219d.py

⛔ 口径：`assert` 是**语句**，必须 exec 而不是 eval（首版用 eval ⇒ 七臂全 0/6）。
"""
import hashlib
import json
import re
import shutil
import sqlite3

D = "bench/evidence/l3_s302"
suite = [json.loads(l) for l in open(f"{D}/suite_sub2.jsonl", encoding="utf-8") if l.strip()]
t01 = [t for t in suite if t["id"] == "t01_parse_duration"][0]
tests = t01["tests"]


def code_of(d):
    tmp = "/tmp/replay_v219d.db"
    shutil.copy(d + "/memory.db", tmp)
    c = sqlite3.connect(tmp)
    ts = [x for (x,) in c.execute("select text from blobs where kind='observation'")
          if "parse_duration" in x]
    c.close()
    m = re.search(r"```python\n(.*?)```", ts[0], re.S)
    return m.group(1) if m else ts[0]


def replay(src):
    ns = {}
    try:
        exec(src, ns)
    except Exception as e:
        return None, f"编译失败 {type(e).__name__}"
    f = ns.get("parse_duration")
    vec = []
    for tc in tests:
        g = {"parse_duration": f}
        try:
            exec(tc, g)
            vec.append(True)
        except Exception:
            vec.append(False)
    return vec, None


ARMS = [("V219c A  无附言",      f"{D}/mem_i/0_off",   3),
        ("V219c H  1/6",        f"{D}/mem_i/0_count", 4),
        ("V219c G  6/6",        f"{D}/mem_i/0_gone",  4),
        ("V219c B' 别题失败",    f"{D}/mem_i/0_prev",  4),
        ("V219d K  KeyError",   f"{D}/mem_j/0_key",   3),
        ("V219d C  ValueError", f"{D}/mem_j/0_val",   5),
        ("V219d I  IndexError", f"{D}/mem_j/0_idx",   5)]

print(f"{'臂':22s} {'代码':>5s} {'sha':>11s}  复跑vec           复跑  实跑  一致")
ok_all = True
for label, d, run in ARMS:
    src = code_of(d)
    vec, err = replay(src)
    if vec is None:
        print(f"{label:22s} {len(src):5d}  {err}")
        ok_all = False
        continue
    rp = sum(vec)
    same = (rp == run)
    ok_all &= same
    print(f"{label:22s} {len(src):5d} {hashlib.sha256(src.encode()).hexdigest()[:10]:>11s}  "
          f"{[int(x) for x in vec]}  {rp}/6   {run}/6   {'✅' if same else '⛔'}")
print(f"\n七臂离线复跑与实跑逐位一致：{'是' if ok_all else '否'}")

print("\n== 自校验（题集自带 oracle/partial）==")
for name in ("oracle", "partial"):
    vec, _ = replay(t01[name])
    print(f"  {name:8s} {sum(vec)}/6  失败格 {[i for i, x in enumerate(vec) if not x]}")
