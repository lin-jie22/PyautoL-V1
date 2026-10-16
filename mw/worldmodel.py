"""上下文内世界模型 —— WorldEvolver(2026-06) 的内核化。

该论文的核心洞察很反直觉，也正好治 V5 的病：
    **不动参数，改上下文。**
冻结的世界模型在真实环境里会因分布漂移而失准；让 LLM 做在线梯度更新
又太贵且会灾难性遗忘。WorldEvolver 的解法是把每一次「预测 vs 实际」的偏差
沉淀进外部记忆：

    episodic  —— 存「真实动作转移」当案例，下次相似状态检索式复用
    semantic  —— 从预测-观测失配里抽出持久启发式（"哪里容易看错"）
    selective foresight —— 低置信度预测**不喂给决策链**，避免把幻觉塞进去

为什么这对 V1 是要害：V5 的失败机理之一是"假设错一步，后面全错"，
即缺少一个能预演动作后果、并因为预演错了而**改变下一次预测**的机制。
本模块就是那个机制 —— 而且是可审计、可回滚的（都在 SQLite 里）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Sequence

from .llm import LLM, Message, parse_json

_MISMATCH_THRESHOLD = 0.34  # 相似度低于此值 ⇒ 记为失配
_FORESIGHT_FLOOR = 0.35     # 置信度低于此值 ⇒ 不进入决策链


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[A-Za-z0-9_\u4e00-\u9fff]{2,}", (text or "").lower()))


def similarity(a: str, b: str) -> float:
    """Jaccard 相似度。故意用最朴素的度量：
    不用嵌入模型（会引入依赖与不透明性），而且这个量只需要"大致对不对"。
    """
    ta, tb = _tokens(a), _tokens(b)
    if not ta and not tb:
        return 1.0
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


@dataclass
class Prediction:
    action: str
    expected: str
    confidence: float
    source: str = "llm"

    @property
    def trustworthy(self) -> bool:
        return self.confidence >= _FORESIGHT_FLOOR


@dataclass
class Mismatch:
    action: str
    expected: str
    actual: str
    agreement: float

    def as_episode(self) -> str:
        return (
            f"预演「{self.action}」时预期：{self.expected[:160]} ｜ "
            f"实际：{self.actual[:160]} ｜ 一致度 {self.agreement:.2f}"
        )

    def as_heuristic(self) -> str:
        """把一次失配提炼成一条持久规则 —— 这是"不带权重的学习"。"""
        key = " ".join(sorted(_tokens(self.action))[:4]) or "该动作"
        return (
            f"执行 [{key}] 时不可假设其结果等同于「{self.expected[:60]}」"
            f"（实测为「{self.actual[:60]}」，一致度 {self.agreement:.2f}）"
        )


class WorldModel:
    def __init__(self, memory: Any, llm: LLM) -> None:
        self.memory = memory
        self.llm = llm
        self.predictions: int = 0
        self.mismatches: int = 0
        self.suppressed: int = 0   # selective foresight 挡下的低置信预测数

    # ------------------------------------------------------------------
    def predict(self, *, state: str, action: str, use_llm: bool = True) -> Prediction:
        """预演动作后果。

        `use_llm=False` 时走**检索式模拟**（从 episodic 里找相似先例）。
        这对应 WorldEvolver 的"检索式模拟复用"——零 LLM 成本，
        也保证了离线/无预算时世界模型仍然在工作而不是消失。
        """
        if not use_llm:
            return self._predict_by_retrieval(state, action)

        self.predictions += 1
        prompt = (
            "你在为一个 agent 做动作前的预演。只输出 JSON。\n"
            f"当前状态：{state[:600]}\n"
            f"准备执行的动作：{action[:300]}\n"
            '输出格式：{"expected": "你预期会发生什么（一句话）", "confidence": 0.0~1.0}\n'
            "confidence 是你对这个预期的把握，不要虚高。"
        )
        raw = self.llm.complete(
            [Message("system", "你是严谨的预测器，宁可说不确定也不要编。"),
             Message("user", prompt)],
            max_tokens=200,
        )
        data = parse_json(raw, fallback=None)
        if not isinstance(data, dict):
            return Prediction(action, "(无法预演)", 0.0, source="parse_failed")
        conf = data.get("confidence", 0.0)
        try:
            conf = max(0.0, min(1.0, float(conf)))
        except (TypeError, ValueError):
            conf = 0.0
        return Prediction(action, str(data.get("expected", ""))[:400], conf)

    def _predict_by_retrieval(self, state: str, action: str) -> Prediction:
        """零成本预演：从情景记忆里找最相似的先例。"""
        episodes = self.memory.episodes(kind="outcome", limit=40)
        best, best_sim = None, 0.0
        for ep in episodes:
            s = similarity(action, ep.content)
            if s > best_sim:
                best, best_sim = ep, s
        if best is None or best_sim < 0.15:
            return Prediction(action, "(无先例可参考)", 0.0, source="retrieval")
        return Prediction(action, best.content[:300], min(0.8, best_sim), source="retrieval")

    # ------------------------------------------------------------------
    def observe(self, prediction: Prediction, actual: str) -> Mismatch | None:
        """比对预演与实测。返回 None 表示一致（无需沉淀）。"""
        agree = similarity(prediction.expected, actual)
        if agree >= _MISMATCH_THRESHOLD:
            return None
        mm = Mismatch(prediction.action, prediction.expected, actual, agree)
        self.mismatches += 1
        self.learn(mm)
        return mm

    def learn(self, mm: Mismatch) -> None:
        """沉淀失配：episodic 存案例，semantic 存启发式。**修记忆，不改权重。**"""
        self.memory.record("mismatch", mm.as_episode(),
                           action=mm.action[:200], agreement=round(mm.agreement, 3))
        self.memory.commit_fact(mm.as_heuristic(), source="worldmodel",
                                confidence=min(0.8, 0.4 + mm.agreement))

    # ------------------------------------------------------------------
    def foresight_filter(self, preds: Sequence[Prediction]) -> list[Prediction]:
        """选择性前瞻：低置信度预测**不得**进入决策链。

        这一步的价值不是"更聪明"，是**防止幻觉污染**——
        一个被喂进决策链的坏预演，会让后面每一步都建立在错误前提上。
        """
        kept = [p for p in preds if p.trustworthy]
        self.suppressed += len(preds) - len(kept)
        return kept

    def guidance(self, *, action: str, k: int = 3) -> str:
        """给规划器的启发式提示：这次动作在哪容易看错。"""
        facts = self.memory.recall(action, k=k)
        wm = [f for f in facts if f.source == "worldmodel"]
        if not wm:
            return ""
        return "已知易错点：\n" + "\n".join(f"- {f.text}" for f in wm)

    def stats(self) -> dict:
        return {
            "predictions": self.predictions,
            "mismatches": self.mismatches,
            "suppressed": self.suppressed,
            "suppress_rate": round(
                self.suppressed / self.predictions, 3) if self.predictions else 0.0,
        }
