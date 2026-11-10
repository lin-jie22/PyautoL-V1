"""量「记忆里到底有没有可比对的期望值」——开跑前的前置检查。

只读上一轮留下的记忆库（`/tmp/v78/*/mem/memory.db`），不写不改。
问三个数：facts / episodes / blobs 各多少条，其中带 `assert … ==` 的各多少条。
⛔ 若三个数全是 0，那么「给闸加第二个来源」这一臂是**结构上无效**的，不该开跑。
"""
import glob
import re
import sqlite3

PAT = re.compile(r"assert\s+[\w.\[\]'\"]+\([^\n]*\)\s*==")

for db in sorted(glob.glob("/tmp/v78/*/mem/memory.db")):
    tag = db.split("/")[3]
    print(f"===== {tag} =====")
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    for table, col in (("facts", "text"), ("episodes", "content"), ("blobs", "text")):
        try:
            rows = [r[0] for r in con.execute(f"select {col} from {table}")]
        except sqlite3.Error as exc:
            print(f"  {table}: 读失败 {exc}")
            continue
        hits = [r for r in rows if PAT.search(r or "")]
        print(f"  {table}: {len(rows)} 条，其中带 assert== 的 {len(hits)} 条")
        for h in hits[:2]:
            line = PAT.search(h).group(0)
            print(f"    · {line[:110]}")
    # 看看 facts 的 key 长什么样（判断能不能按题召回）
    try:
        keys = [r[0] for r in con.execute("select key from facts limit 8")]
        print(f"  facts.key 样例：{keys}")
    except sqlite3.Error:
        pass
    con.close()
