"""V145 跑前取证：把 `ledger_audit` 的一次调用拆成四段，量规模曲线。

⛔ 合成账本全部落 `/tmp`：真实账本只**读**，绝不写。
⛔ 合成曲线只用来量「阶」，任何「真实读数」只来自 `bench_out/runs.jsonl`。
"""

import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from mw.bench import _read_records, audit_measurement  # noqa: E402

REAL = Path("bench_out/runs.jsonl")
OUT = []


def emit(s=""):
    print(s)
    OUT.append(s)


def t(fn, n=15):
    """取中位数秒。n 次热身+计时混跑对短任务够用（抖动由中位数压掉）。"""
    ts = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        ts.append(time.perf_counter() - t0)
    return statistics.median(ts)


def split_cost(path: Path, run_id: str):
    """四段分解。每段独立计时的和会略大于真实调用（缓存效应），
    所以同时报「整段实测」做对照。"""
    text_holder = {}

    def do_read():
        text_holder["t"] = path.read_text(encoding="utf-8")

    def do_parse():
        out = []
        for ln in text_holder["t"].splitlines():
            s = ln.strip()
            if s:
                out.append(json.loads(s))
        text_holder["recs"] = out

    def do_locate():
        for rec in reversed(text_holder["recs"]):
            if rec.get("run_id") == run_id:
                text_holder["hit"] = rec
                break

    def do_arith():
        audit_measurement(text_holder["hit"])

    def do_all():
        recs = _read_records(path)
        hit = None
        for rec in reversed(recs):
            if rec.get("run_id") == run_id:
                hit = rec
                break
        audit_measurement(hit)

    seg = {"read": t(do_read), "parse": t(do_parse),
           "locate": t(do_locate), "arith": t(do_arith)}
    do_read(); do_parse(); do_locate()          # 保证 arith 与 all 用的数据齐
    seg["arith"] = t(do_arith)
    seg["TOTAL"] = t(do_all)
    return seg


def build_synthetic(real_lines, n_rows, dst: Path):
    """把真实行按整行复制到 n_rows，并把 `run_id` 改成唯一值。
    每个合成 `run_id` 的后缀与行号一一对应 ⇒ 目标行可精确指定。"""
    out = []
    i = 0
    while len(out) < n_rows:
        ln = real_lines[i % len(real_lines)]
        rec = json.loads(ln)
        rec["run_id"] = f"9{i:06d}@0"
        out.append(json.dumps(rec, ensure_ascii=False, sort_keys=True))
        i += 1
    dst.write_text("\n".join(out) + "\n", encoding="utf-8")
    return f"9{(n_rows - 1):06d}@0", f"9{(n_rows // 2):06d}@0"


def main():
    real_lines = [ln for ln in REAL.read_text(encoding="utf-8").splitlines() if ln.strip()]
    emit(f"[真实账本] {REAL}  行数={len(real_lines)}  字节={REAL.stat().st_size}")
    recs = [json.loads(ln) for ln in real_lines]
    target = [r for r in recs if r.get("run_id")]
    emit(f"[真实账本] 带 run_id 的行 = {len(target)}，最后一行 run_id = {target[-1]['run_id']}")
    rid = target[-1]["run_id"]

    emit("\n[1] 四段分解（真实账本 145 行，中位数 ×15）")
    seg = split_cost(REAL, rid)
    for k in ("read", "parse", "locate", "arith", "TOTAL"):
        emit(f"    {k:7s} {seg[k]*1000:8.3f} ms")
    parts = seg["read"] + seg["parse"] + seg["locate"] + seg["arith"]
    emit(f"    四段之和 {parts*1000:8.3f} ms ｜ 整段实测 {seg['TOTAL']*1000:8.3f} ms")
    emit(f"    占比：read {seg['read']/parts:.1%}  parse {seg['parse']/parts:.1%}  "
         f"locate {seg['locate']/parts:.1%}  arith {seg['arith']/parts:.1%}")

    emit("\n[2] 规模曲线（合成账本，⛔ 不是真实读数）")
    emit("    N行     字节       read      parse     arith     TOTAL(整份)   TOTAL(行局部)")
    tmpdir = Path(tempfile.mkdtemp(prefix="v145s_"))
    rows = []
    for n in (145, 290, 580, 1450, 2900, 5800):
        p = tmpdir / f"ledger_{n}.jsonl"
        last, mid = build_synthetic(real_lines, n, p)
        s = split_cost(p, last)
        # 行局部化的下界：只读全文 + 只解析命中的那一行（预筛命中 1 行）
        def local():
            txt = p.read_text(encoding="utf-8")
            for ln in txt.splitlines():
                if last in ln:
                    audit_measurement(json.loads(ln))
                    break
        s["LOCAL"] = t(local, n=7)
        rows.append((n, p.stat().st_size, s))
        emit(f"    {n:5d} {p.stat().st_size/1e6:7.2f}MB "
             f"{s['read']*1000:8.3f} {s['parse']*1000:8.3f} {s['arith']*1000:8.3f} "
             f"{s['TOTAL']*1000:12.3f} {s['LOCAL']*1000:13.3f}")

    emit("\n[3] 阶数判定（相邻倍数的耗时比 / 行数比）")
    for (n0, b0, s0), (n1, b1, s1) in zip(rows, rows[1:]):
        k = n1 / n0
        emit(f"    N {n0:5d}→{n1:5d}（×{k:.1f}）  整份 ×{s1['TOTAL']/s0['TOTAL']:.2f}  "
             f"行局部 ×{s1['LOCAL']/s0['LOCAL']:.2f}  parse ×{s1['parse']/s0['parse']:.2f}")

    emit("\n[4] 最坏情况：目标 run_id 不在账本里（必须扫完所有行）")
    for n, b, s in rows[-2:]:
        p = tmpdir / f"ledger_{n}.jsonl"
        absent = lambda: _read_records(p)  # noqa: E731
        emit(f"    N={n:5d}  整份读 = {t(absent, n=7)*1000:.3f} ms")

    emit("\n[5] 坏行的连坐范围（真实账本副本 + 注入一行坏 JSON）")
    p = tmpdir / "tampered.jsonl"
    lines = list(real_lines)
    lines.insert(len(lines) // 2, '{"run_id": "broken", "report": ')   # 截断的 JSON
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    got = _read_records(p)
    emit(f"    非目标行坏 ⇒ _read_records 返回 {got!r} ⇒ 该账本上**任何** run_id 都审不了")

    dst = Path(__file__).with_name("READINGS_probe.txt")
    dst.write_text("\n".join(OUT) + "\n", encoding="utf-8")
    print(f"\n→ {dst}")
    for f in tmpdir.glob("*"):
        os.unlink(f)
    os.rmdir(tmpdir)


if __name__ == "__main__":
    main()
