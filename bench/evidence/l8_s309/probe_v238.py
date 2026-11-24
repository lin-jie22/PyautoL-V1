#!/usr/bin/env python3
"""V238（§309）探针：**走产品通道**（`HTTPLLM`）测 ollama 原生 think 开关。

⛔ 与 V236 探针的差别（刻意的）：V236 自己写 HTTP 旁路 `/v1`；
   本轮要测的是**产品能不能用**，所以只调 `HTTPLLM.complete`。
   若这里另写一份 HTTP，就又在测「我手写的通道」而不是产品通道。

用法：
    python3 probe_v238.py speed     # 速度闸（纪律 116）
    python3 probe_v238.py probe     # P3 / P6 / 正例反面，各 1 次
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))

from mw.bench import ExternalScorer                       # noqa: E402
from mw.config import LLMConfig                           # noqa: E402
from mw.llm import HTTPLLM, Message                       # noqa: E402
from mw.reflect import (build_proposal_prompt, failed_actuals,  # noqa: E402
                        parse_proposal, sha_of)

OUT = HERE / "probe_v238.jsonl"
RAW = HERE / "raw_v238"

#: 温度与 V236 同口径（=1.0）。⛔ 这不是产品默认（0.2）：
#: 判据是「4B 的行为类能否在**产品通道**上复现 V236 的旁路读数」，
#: 所以除「通道」外的条件必须与 V236 逐项相同，否则两个变量缠在一起。
TEMP = 1.0
MAXTOK = 900


def task_and_start():
    task = None
    for ln in open(HERE.parents[0] / "l5_s266" / "suite_one" / "t01.jsonl", encoding="utf-8"):
        task = json.loads(ln)
    start = (HERE.parents[0] / "l6_s307" / "start_answer.py").read_text(encoding="utf-8")
    return task, start


def make_scorer():
    sp = ROOT / "bench" / "scorers" / "code_exec.py"
    return ExternalScorer([sys.executable, str(sp)], source=sp)


def cfg_for(spec: str) -> LLMConfig:
    """`model[@provider[:thinking]]` ⇒ LLMConfig。**复用产品的解析器**，不另写一份。"""
    from mw.config import parse_model_spec
    model, provider, thinking = parse_model_spec(spec)
    kw = dict(base_url="http://localhost:11434/v1", api_key="ollama",
              model=model, temperature=TEMP, offline=False, timeout_s=900.0)
    if provider is not None:
        kw["provider"] = provider
    if thinking is not None:
        kw["thinking"] = thinking
    return LLMConfig(**kw)


def speed():
    """纪律 116：跑批量前量真实 tok/s。用产品通道，量的是改完之后的通路。"""
    print("== 速度闸（热身 1 次 + 取 2 次最好；含模型加载的读数作废）==")
    for spec in ("Qwen3.5-4B@ollama:disabled", "qwen2.5:7b@ollama:disabled"):
        cfg = cfg_for(spec)
        h = HTTPLLM(cfg)
        msg = [Message("user", "写一行 python：把列表 lst 中所有偶数平方后求和。只输出一行代码。")]
        try:
            h.complete(msg, max_tokens=32)                       # 热身
        except Exception as e:  # noqa: BLE001
            print(f"  {spec}: 热身 ERR {e}")
            continue
        best, rows = 0.0, []
        t0all = time.time()
        for _ in range(2):
            t0 = time.time()
            try:
                txt = h.complete(msg, max_tokens=220)
            except Exception as e:  # noqa: BLE001
                print(f"  {spec}: ERR {e}")
                continue
            wall = time.time() - t0
            #: 原生通道的 usage 不在响应里（我们只返回正文）⇒ 用字符数/4 估算 token，
            #: 这是**下界估计**，只用于过闸，不作读数。
            est = max(1, len(txt) // 4)
            tps = est / wall if wall > 0 else 0.0
            best = max(best, tps)
            rows.append((len(txt), round(wall, 1), round(tps, 2)))
        print(f"  {spec}: 各次(字符,墙钟s,≈tok/s)={rows} ⇒ 取最好 {best:.2f} tok/s "
              f"gate={'PASS' if best >= 15 else 'FAIL(<15)'} 总耗时 {time.time()-t0all:.0f}s")


def probe():
    task, start = task_and_start()
    sc = make_scorer()
    r0 = sc.score(task, start)
    vec0 = "".join("1" if x else "0" for x in (getattr(r0, "vec", None) or ()))
    why = (f"当前最好的一次是 {float(r0.score):.4f}（共 {len(task['tests'])} 条断言）。"
           f"请给出**把剩下的失败断言一起修好**的完整实现。")
    prompt = build_proposal_prompt(task["prompt"], start, why,
                                   failed_actuals(start, task["tests"]))
    print(f"起点 score={r0.score} vec={vec0}")
    print(f"prompt chars={len(prompt)} sha={sha_of(prompt)}"
          f"（V236 是 3b33bcb8b2276580 —— 相同即「只有通道变了」）")

    #: 三臂。B 是**正例的反面**：不设 think ⇒ 应复现 V236 的截断。
    arms = [
        ("P3_4b_think0", "Qwen3.5-4B@ollama:disabled"),
        ("NEG_4b_thinkunset", "Qwen3.5-4B@ollama"),
        ("P6_7b_think0", "qwen2.5:7b@ollama:disabled"),
    ]
    recs = []
    for tag, spec in arms:
        cfg = cfg_for(spec)
        h = HTTPLLM(cfg)
        t0 = time.time()
        rec = {"arm": tag, "spec": spec, "prompt_sha": sha_of(prompt)}
        try:
            txt = h.complete([Message("user", prompt)], max_tokens=MAXTOK)
        except Exception as e:  # noqa: BLE001
            rec.update({"err": f"{type(e).__name__}: {e}"[:200],
                        "wall_s": round(time.time() - t0, 1)})
            print(f"  {tag:<18} ERR {rec['err'][:110]}")
            recs.append(rec)
            continue
        cand, fenced = parse_proposal(txt)
        rec.update({"raw_chars": len(txt), "code_chars": len(cand),
                    "fenced": bool(fenced), "wall_s": round(time.time() - t0, 1)})
        #: 原始正文落盘 —— 判「截断」还是「话多」必须看文本本身，
        #: 只看长度会把两种完全不同的失败混成一个数（纪律 131）。
        RAW.mkdir(exist_ok=True)
        (RAW / f"{tag}.txt").write_text(txt, encoding="utf-8")
        if cand.strip():
            s = sc.score(task, cand)
            v = getattr(s, "vec", None)
            rec.update({"score": float(getattr(s, "score", 0.0)),
                        "impl_sha": sha_of(cand),
                        "vec": list(v) if v else None})
            rec["hit6"] = bool(all(v)) if v else False
            print(f"  {tag:<18} raw={len(txt)} 字符 code={len(cand)} "
                  f"score={rec['score']} vec={''.join('1' if x else '0' for x in (v or []))} "
                  f"sha={sha_of(cand)[:8]} {rec['wall_s']}s")
        else:
            rec["err"] = "empty-code"
            print(f"  {tag:<18} raw={len(txt)} 字符但**解析不出代码** {rec['wall_s']}s")
        recs.append(rec)
    with open(OUT, "a", encoding="utf-8") as f:
        for rec in recs:
            f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
    print(f"\n落盘 {OUT}")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "probe"
    {"speed": speed, "probe": probe}[mode]()
