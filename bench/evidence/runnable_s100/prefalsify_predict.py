"""反证：世界模型的「预演」到底承不承重？

两臂只差一件事：`wm.predict` 是否被换成「恒预测这一步会失败、且置信度足够高」。
若两臂的交付物 sha / 动作序列 / 状态**逐字节相同** ⇒ 预演零决策影响。
"""
import hashlib, json, os, sys, tempfile
from pathlib import Path
sys.path.insert(0, '/Users/linshaojie/V1')
from bench.probe_stallpath import Script, GOAL, isolated_state
from mw import config as cfg
from mw.core import Core
from mw.worldmodel import Prediction

PLANS = [
    '{"steps":[{"tool":"note","args":{"text":"先看看环境","source":"agent","confidence":0.6},"intent":"记一笔"}]}',
    '{"steps":[{"tool":"finish","args":{"answer":"```python\\nprint(1)\\n```"},"intent":"交答案"}]}',
]

def run(force_fail: bool) -> dict:
    with tempfile.TemporaryDirectory() as td:
        with isolated_state(Path(td)):
            os.environ["V1_MAX_CONSECUTIVE_FAILURES"] = "2"
            try:
                s = cfg.settings()
                llm = Script(PLANS, answer="```python\nprint(1)\n```")
                core = Core(s, llm=llm, log=None)
                lines = []
                core.log = lambda m: lines.append(m)
                real = core.wm.predict
                calls = {"n": 0}
                def patched(**kw):
                    calls["n"] += 1
                    if force_fail:
                        return Prediction(action=str(kw.get("action",""))[:80],
                                          expected="这一步会失败", confidence=0.95,
                                          source="llm", expect_ok=False)
                    return real(**kw)
                core.wm.predict = patched
                out = core.run(GOAL)
            finally:
                os.environ.pop("V1_MAX_CONSECUTIVE_FAILURES", None)
    st = out.stats or {}
    return {"status": out.status, "answer": out.answer,
            "answer_sha": hashlib.sha256((out.answer or "").encode()).hexdigest()[:16],
            "predict_calls": calls["n"],
            "confusion": (st.get("worldmodel") or {}).get("confusion"),
            "obs": [l for l in lines if "🔮" in l or "预演" in l]}

a = run(False)
b = run(True)
print("臂 N（真预演）:", json.dumps({k:a[k] for k in ("status","answer_sha","predict_calls","confusion")}, ensure_ascii=False))
print("臂 F（恒预测失败）:", json.dumps({k:b[k] for k in ("status","answer_sha","predict_calls","confusion")}, ensure_ascii=False))
print("\n交付物相同?", a["answer_sha"] == b["answer_sha"])
print("状态相同?", a["status"] == b["status"])
print("预演真被调用?", a["predict_calls"], b["predict_calls"])
print("\n臂F 的预演日志:", b["obs"][:4])
