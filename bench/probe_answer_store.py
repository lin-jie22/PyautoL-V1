#!/usr/bin/env python3
"""答案本体落盘的自检：**零模型调用**，含一次离线真跑（`--solver oracle`）。

## 为什么它必须存在

`bench/answer_store.py` 是量具。量具坏了不会让任何东西崩，只会让结论变假：
`answers.jsonl` 里照样有行、照样有 `task_id`，只是**内容被切过**
（DESIGN §42 记的四处截断就是这个形态）。所以每一项都要配反证。

## 最关键的两项

- **P6/P7 是接线证明**：不是「代码里有 `AnswerLog`」这种静态锚，而是真的起一个
  `run.py` 子进程（`--solver oracle`，离线、零模型调用），然后要求
  `answers.jsonl` 的 `run_id` **能在 `runs.jsonl` 里找到**、且最长答案 **> 200 字符**。
  静态锚只能说「写了这行」，这两项说「它真的跑了」。
- **P9 是反证**：把 cap 设成 200（模拟那处历史缺陷）之后，P1 的判据必须变红。
  喂坏样本不命中 ⇒ 判据是死代码。

## 它不做什么

⛔ 不跑模型、不改台账、不写仓库里的任何文件 —— 所有落盘都进 `tempfile`
（真跑那一步用 `V1_BENCH_DIR` / `V1_STATE_DIR` 指过去）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bench import answer_store as AS                               # noqa: E402

_fail: list[str] = []


def ck(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'✅' if cond else '❌'} {label}" + (f"  ({detail})" if detail else ""))
    if not cond:
        _fail.append(label)


def main() -> int:
    print("=" * 74)
    print("答案本体落盘自检（bench/answer_store.py）")
    print("=" * 74)

    # ── P1/P2：全文 + 指纹必须与正文一致 ─────────────────────────────────
    long_text = "".join(f"第{i:04d}行：def norm_sg05(rows): return rows  # 填充\n" for i in range(120))
    with tempfile.TemporaryDirectory(prefix="v1_ans_") as td:
        p = Path(td) / "answers.jsonl"
        log = AS.AnswerLog(p)
        log.add("sg05", long_text)
        w = log.save(run_id="1@2")
        rows = AS.AnswerLog.read(p)
        got = rows[0]["answer"] if rows else ""
        ck("P1 默认不截断：读回来的正文与写入的**逐字相同**",
           got == long_text and len(long_text) > 2000,
           f"写入 {len(long_text)} 字符 ｜ 读回 {len(got)} 字符 ｜ 落盘 max={w['max_chars']}")
        ck("P2 `chars` / `sha256` 与正文自洽（`verify_rows` 零坏行）",
           AS.verify_rows(rows) == {"n": 1, "bad_len": 0, "bad_sha": 0},
           str(AS.verify_rows(rows)))

        # ── P3：截断必须**自陈**（四处历史截断真正的毛病是「截了不说」）────
        p2 = Path(td) / "capped.jsonl"
        lg2 = AS.AnswerLog(p2, cap=200)
        lg2.add("sg05", long_text)
        w2 = lg2.save(run_id="3@4")
        r2 = AS.AnswerLog.read(p2)
        ck("P3 设了 cap 就截断，且记录里写 `truncated: true`（不许静默切）",
           len(r2[0]["answer"]) == 200 and r2[0]["truncated"] is True and w2["truncated"] == 1,
           f"chars={r2[0]['chars']} truncated={r2[0]['truncated']}")

        # ── P4：空缓冲也要写 header ───────────────────────────────────────
        p3 = Path(td) / "empty.jsonl"
        AS.AnswerLog(p3).save(run_id="5@6")
        lines = [json.loads(x) for x in p3.read_text().splitlines() if x.strip()]
        ck("P4 一条答案都没有时**仍然**写 header（「没答案」≠「没写过」）",
           len(lines) == 1 and lines[0]["kind"] == "header" and lines[0]["n_answers"] == 0,
           f"{len(lines)} 行 ｜ kind={lines[0].get('kind')}")

        # ── P5：没有 run_id 直接拒 ────────────────────────────────────────
        try:
            AS.AnswerLog(Path(td) / "bad.jsonl").save(run_id="  ")
            p5 = "没有抛错"
        except ValueError as e:
            p5 = f"ValueError: {str(e)[:34]}"
        ck("P5 空 `run_id` ⇒ 抛错（否则「答案属于哪次运行」不可判定）",
           p5.startswith("ValueError"), p5)

        # ── P8：边界① 落盘记录里不许有分数 / 判据字段 ────────────────────
        banned = {"score", "passed", "verdict", "ok", "keep"}
        ck("P8 落盘键里**没有** score / passed / verdict 这类字段（它不是分数通道）",
           not (banned & set(rows[0].keys())),
           f"键={sorted(rows[0].keys())}")

        # ── P10：半行不许带走整个读回（被 kill 在写盘中途）─────────────────
        p4 = Path(td) / "half.jsonl"
        lg4 = AS.AnswerLog(p4)
        lg4.add("a", "x" * 10)
        lg4.save(run_id="7@8")
        with p4.open("a", encoding="utf-8") as f:
            f.write('{"kind": "answer", "task_id": "b", "ans')
        ck("P10 读回时跳过半行（被 kill 在写盘中途）而不崩",
           len(AS.AnswerLog.read(p4)) == 1, f"读回 {len(AS.AnswerLog.read(p4))} 条")

        # ── P9：反证 —— 把 cap 当默认，P1 的判据必须变红 ──────────────────
        p6 = Path(td) / "regress.jsonl"
        lg6 = AS.AnswerLog(p6, cap=200)
        lg6.add("sg05", long_text)
        lg6.save(run_id="9@10")
        back = AS.AnswerLog.read(p6)[0]["answer"]
        ck("P9 反证：模拟历史缺陷（默认 cap=200）⇒ 「逐字相同」这条判据必然为假",
           back != long_text and len(back) == 200,
           f"读回 {len(back)} 字符 ≠ 写入 {len(long_text)} 字符")

        # ── P11：边界③ 内核侧零引用（静态，但必须是**不存在**性结论）───────
        hits = [p.name for p in (ROOT / "mw").glob("*.py")
                if "answer_store" in p.read_text(encoding="utf-8")]
        ck("P11 `mw/` 里零引用 `answer_store`（判据侧不许读答案本体，AO11）",
           hits == [], f"命中={hits}")

        # ── P6/P7：接线证明 —— 真的起一个 run.py 子进程（离线 oracle）───────
        env = dict(os.environ)
        env["V1_BENCH_DIR"] = str(Path(td) / "bench")
        env["V1_STATE_DIR"] = str(Path(td) / "state")
        env.pop("V1_LLM_API_KEY", None)
        cp = subprocess.run(
            [sys.executable, "run.py", "--bench",
             "--suite", "bench/specgen/tasks_v1.jsonl",
             "--solver", "oracle", "--repeat", "1", "--no-ledger"],
            cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=300)
        bench = Path(td) / "bench"
        runs_p, ans_p = bench / "runs.jsonl", bench / "answers.jsonl"
        run_ids: set[str] = set()
        if runs_p.exists():
            for line in runs_p.read_text().splitlines():
                if line.strip():
                    run_ids.add(str(json.loads(line).get("run_id")))
        hdrs = []
        ans_rows = []
        if ans_p.exists():
            for line in ans_p.read_text().splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                (hdrs if row.get("kind") == "header" else ans_rows).append(row)
        hdr = hdrs[-1] if hdrs else {}
        longest = max((int(r["chars"]) for r in ans_rows), default=0)
        # ⛔ 逐行 run_id 是「按轮取答案」的前提：第一版只写在 header 上，
        #    `read(run_id=...)` 于是过滤出空列表（实测真跑当场 KeyError）。
        _by_run = AS.AnswerLog.read(ans_p, run_id=str(hdr.get("run_id"))) if hdr else []
        ck("P6 真跑一次 `run.py`：`answers.jsonl` 的 run_id **能在 runs.jsonl 里找到**，"
           "且**按轮取**能取到全部 6 条",
           bool(hdr) and hdr.get("run_id") in run_ids and len(_by_run) == 6,
           f"exit={cp.returncode} ｜ answers run_id={hdr.get('run_id')} ｜ "
           f"runs 里 {len(run_ids)} 个 id ｜ 按轮取 {len(_by_run)} 条")
        ck("P7 真跑的最长答案 **> 200 字符**（这条通道没被 cap 切过）",
           longest > 200 and len(ans_rows) == 6,
           f"{len(ans_rows)} 条 ｜ 最长 {longest} 字符")

    print()
    print("=" * 74)
    if _fail:
        print(f"❌ 失败 {len(_fail)} 项：" + "；".join(_fail))
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
