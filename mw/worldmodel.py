"""上下文内世界模型 —— WorldEvolver(2026-06) 的内核化。

该论文的核心洞察很反直觉，也正好治这类系统的病：
    **不动参数，改上下文。**
冻结的世界模型在真实环境里会因分布漂移而失准；让 LLM 做在线梯度更新
又太贵且会灾难性遗忘。WorldEvolver 的解法是把每一次「预测 vs 实际」的偏差
沉淀进外部记忆：

    episodic  —— 存「真实动作转移」当案例，下次相似状态检索式复用
    semantic  —— 从预测-观测失配里抽出持久启发式（"哪里容易看错"）
    selective foresight —— 低置信度预测**不喂给决策链**，避免把幻觉塞进去

为什么这对 V1 是要害：失败机理之一是"假设错一步，后面全错"，
即缺少一个能预演动作后果、并因为预演错了而**改变下一次预测**的机制。
本模块就是那个机制 —— 而且是可审计、可回滚的（都在 SQLite 里）。

---

## 2026-10-17 修复：缺陷 #12 —— 失配信号**没有分辨力**

首版实现让 `predict` 预演"会发生什么（一句话）"，让 `observe` 拿这句话去和
**整段观测**做 Jaccard。这是**跨种类比较** ⇒ 结构上不可能相似 ⇒
「失配」恒红（实测 9/9 次一致度 ≤ 0.09），而且**反相关**（正确解 0.0714 /
错误解 0.1429）。一个永远不可能"好"的信号 = **没有信号**，
把它读成"因果闭环在工作"就是把 `gain≡0` 读成"每轮全绿"。

修法的三条纪律：
1. **按种类比**：预测的对象决定用哪把尺子（成败 / 文本），绝不混用。
2. **把预测降到内核真能观测的层面**：`result.ok` 是内核直接持有的地面真相。
   ⚠️ 代价是一个**明确的边界**：世界模型检验的是"这一步执行成功了吗"，
   **不是"答案对不对"** —— 后者属于外部打分器，内核不得冒充。
3. **不制造知识**：文本类失配只进情景记忆，**不进语义层**。
   从措辞差异里提炼"规则"是在把测量噪声写成知识。

读数也随之改成**有分母**的形态：`scored / hits / hit_rate / 2×2 混淆矩阵`，
`scored==0` 时 `hit_rate` 是 `None`（没量到）而不是 `0.0`（量到 0）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .llm import LLM, Message, parse_json

_MISMATCH_THRESHOLD = 0.34  # **文本比对**的失配门槛（只在"同种东西"之间用，见 observe）
_FORESIGHT_FLOOR = 0.35     # 置信度低于此值 ⇒ 不进入决策链

#: 外部奖励的**唯一合法来源**。内核自评的一律拒收（见 `record_reward`）。
REWARD_SOURCE = "external_scorer"

#: 检索历史奖励时的最小结构重合度。低于此值 ⇒ 不算"同类题"。
#: ⛔ 这个数不是调出来的旋钮：它是"两道题的特征集合至少要有三成重合"
#: 这句话的直接翻译，与 `_MISMATCH_THRESHOLD`（0.34）刻意取同一个量级。
REWARD_MIN_SIM = 0.30


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


def _as_bool(v: Any) -> bool | None:
    """把模型给的 `expect_ok` 解析成**三态**。

    `None`（说不清 / 没给 / 解析不出）是**一等公民**，不是失败：
    契约③「缺失 ≠ 否定」—— 说不清的预演**不进检验**，也就不会被算成猜错。
    这与"解析失败就默认 False"是两件事：后者会把模型的不作为记成一次错误预测。
    """
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v) if v in (0, 1) else None
    if isinstance(v, str):
        t = v.strip().lower()
        if t in ("true", "yes", "1", "ok", "success", "成功", "会成功"):
            return True
        if t in ("false", "no", "0", "fail", "failure", "失败", "会失败"):
            return False
    return None


@dataclass
class Prediction:
    action: str
    expected: str
    confidence: float
    source: str = "llm"
    #: 预演**的对象**：这一步会不会成功。三态：True / False / None(说不清)。
    #: 这是本模块唯一**能被内核自己检验**的预测内容 —— 见 `observe` 的说明。
    expect_ok: bool | None = None

    @property
    def trustworthy(self) -> bool:
        return self.confidence >= _FORESIGHT_FLOOR

    @property
    def checkable(self) -> bool:
        """这条预演的结论是否**可被观测检验**（有明确成败方向）。"""
        return self.expect_ok is not None


@dataclass
class Mismatch:
    action: str
    expected: str
    actual: str
    agreement: float
    #: "outcome" —— 按**成败**比对（可检验、可迁移）
    #: "text"    —— 按**文本重合度**比对（已知边界，不产生持久规则）
    kind: str = "outcome"
    predicted_ok: bool | None = None
    actual_ok: bool | None = None

    @property
    def teachable(self) -> bool:
        """这次失配能否提炼成**可迁移**的规则。

        ⚠️ 只有 outcome 类可以。text 类的"失配"记的是**措辞差异**，
        不是"哪里容易看错" —— 把它写成持久规则等于**从测量噪声里造知识**。
        V1 之前 10 条 semantic facts 里 9 条形如
        「执行 [10 100 1000 50] 时不可假设其结果等同于「…」（一致度 0.00）」，
        就是这么造出来的，而且**逐字重复**（同一教训记两次、行动一次没变）。
        """
        return self.kind == "outcome"

    def as_episode(self) -> str:
        if self.kind == "outcome":
            return (f"预演「{self.action}」为{'会成功' if self.predicted_ok else '会失败'}"
                    f"，实际{'成功' if self.actual_ok else '失败'} ｜ "
                    f"实测：{self.actual[:160]}")
        return (
            f"预演「{self.action}」时预期：{self.expected[:160]} ｜ "
            f"实际：{self.actual[:160]} ｜ 一致度 {self.agreement:.2f}"
        )

    def as_heuristic(self) -> str:
        """把一次失配提炼成一条持久规则 —— 这是"不带权重的学习"。

        写成**可执行**的形态（"这个动作我预期 X，实际 Y"），
        而不是"不可假设其结果等同于「某段话」" —— 后者只描述了字面差异，
        下一轮读到时无法据此改变任何动作。
        """
        key = " ".join(sorted(_tokens(self.action))[:4]) or "该动作"
        if self.kind == "outcome":
            want = "成功" if self.predicted_ok else "失败"
            got = "成功" if self.actual_ok else "失败"
            detail = " ".join(self.actual.split())[:90]
            return f"动作 [{key}]：我预演「会{want}」，实际{got}。实测：{detail}"
        return (
            f"执行 [{key}] 时不可假设其结果等同于「{self.expected[:60]}」"
            f"（实测为「{self.actual[:60]}」，一致度 {self.agreement:.2f}）"
        )

    def as_line(self) -> str:
        if self.kind == "outcome":
            return (f"{self.action[:60]} 预演={'成功' if self.predicted_ok else '失败'}"
                    f" 实际={'成功' if self.actual_ok else '失败'}")
        return f"{self.action[:60]} 一致度={self.agreement:.2f}"


class WorldModel:
    def __init__(self, memory: Any, llm: LLM, *, allow_learn: bool = True) -> None:
        self.memory = memory
        self.llm = llm
        #: 是否允许把可教学的失配**沉淀成语义事实**（`learn()` 的 commit_fact）。
        #: False 时失配仍记情景（`mismatch` 行，不进检索），只是不进语义层。
        #: 为什么必须可关（第二十八轮 6c 首跑实测）：这是运行期写语义层的**第二条**
        #: 通道（第一条是 `note` 工具，`allow_note` 管）——三臂记忆实验里
        #: 空库臂被它写回了 facts，"种子是唯一处理"被混杂，整组作废。
        #: ⚠️ 与 `allow_note` 一样进口径签名（Limits 字段）。
        self.allow_learn = bool(allow_learn)
        self.predictions: int = 0
        self.suppressed: int = 0   # selective foresight 挡下的低置信预测数
        # —— 分辨力读数（缺陷 #12 的修复带来的）——
        # 分母**必须**是"被检验过的预演数"，不是"预演了多少次"：
        # 拿 predictions 当分母，命中率永远被那些"根本没检验"的预演稀释。
        self.scored: int = 0
        self.hits: int = 0
        self.unscored: int = 0     # 预演可检验、但调用方没给成败 ⇒ 如实计数（不静默降级）
        self.cm: dict[str, int] = {"ok_ok": 0, "ok_fail": 0,
                                   "fail_ok": 0, "fail_fail": 0}
        self.text_compared: int = 0
        self.text_mismatches: int = 0
        # —— 外部奖励通道（§17-5：给学习与泛化一条真实的学习面）——
        self.rewards_recorded: int = 0
        self.rewards_rejected_source: int = 0   # 自评 / 来源不明 ⇒ 拒（防自评分）
        self.rewards_rejected_missing: int = 0  # 缺题号或特征 ⇒ 无法归属 ⇒ 拒
        self.rewards_seen: int = 0              # 检索时被**真正读进上下文**的次数
        self.rewards_skipped_self: int = 0      # 命中但被"防自评"挡下的次数

    @property
    def mismatches(self) -> int:
        """失配总数 —— **从混淆矩阵派生**，不另设计数器（同一事实只声明一处）。"""
        return self.cm["ok_fail"] + self.cm["fail_ok"] + self.text_mismatches

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
        # ⚠️ 必须带上「自己上次错在哪」—— 否则本模块的核心声明是空的。
        #
        # 本模块 docstring 写着："缺少一个能预演动作后果、并因为预演错了而
        # **改变下一次预测**的机制。本模块就是那个机制。"
        # 而实测（2026-10-17，契约 V6）证明**后半句没被实现**：
        # 失配只喂给了 planner（那是改变下一次**规划**），predict **自己的输入
        # 一个字都没变** ⇒ 同一个动作下次照样以同样的方式预测错。
        # "改变下一次规划" ≠ "改变下一次预测"，它们是这条因果链上不同的两环。
        # ⚠️ 离线路径（`use_llm=False`）刻意**不**吃这个：那条路是检索式复用，
        #    它找的是"相似先例会发生什么"，而 mismatch 记的是"错在哪" ——
        #    把警示当成预期喂给决策链是误导。这是**已知边界**，不是遗漏。
        known = self.guidance(action=action, k=3)
        prompt = (
            "你在为一个 agent 做动作前的预演。只输出 JSON。\n"
            f"当前状态：{state[:600]}\n"
            f"准备执行的动作：{action[:300]}\n"
            f"{known}\n"
            '输出格式：{"expect_ok": true, "expected": "你预期会发生什么（一句话）", '
            '"confidence": 0.0~1.0}\n'
            "expect_ok = 这一步**会不会成功**（true 会成功 / false 会失败）。"
            "这是唯一会被事后检验的字段，别乱填。\n"
            "expected 是一句话描述，给人读的。\n"
            "confidence 是你对上述预演的把握，不要虚高。"
        )
        raw = self.llm.complete(
            [Message("system", "你是严谨的预测器，宁可说不确定也不要编。"),
             Message("user", prompt)],
            max_tokens=200,
        )
        data = parse_json(raw, fallback=None)
        if not isinstance(data, dict):
            # ⚠️ 解析失败 ⇒ expect_ok 也是 None ⇒ **不进检验**。
            #    这不是"猜错了"，是"没猜" —— 两者在读数里必须分开（契约③）。
            return Prediction(action, "(无法预演)", 0.0, source="parse_failed")
        conf = data.get("confidence", 0.0)
        try:
            conf = max(0.0, min(1.0, float(conf)))
        except (TypeError, ValueError):
            conf = 0.0
        return Prediction(action, str(data.get("expected", ""))[:400], conf,
                          expect_ok=_as_bool(data.get("expect_ok")))

    def _predict_by_retrieval(self, state: str, action: str) -> Prediction:
        """零成本预演：从情景记忆里找最相似的先例。

        ⚠️ 返回的 `expect_ok` 是 **None**：先例里只有"观测文本"，没有"成败方向"。
        这条路径的预演**不可被成败检验**，只能按文本比对（`_observe_text`），
        而文本类失配不产生持久规则。这是**已知边界**，不是遗漏。
        """
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
    def observe(self, prediction: Prediction, actual: str,
                *, ok: bool | None = None) -> Mismatch | None:
        """比对预演与实测。返回 None 表示一致（无需沉淀）。

        ## 核心纪律：**按种类比，绝不跨种类比**

        缺陷 #12 的根因就在这里：`predict` 预演的是**一句话**（"会输出一段代码"），
        而 `observe` 拿它去和**整段答案**做 Jaccard。这两者不是同一种东西 ——
        **结构上不可能相似**（实测 9/9 次一致度 ≤ 0.09，且**反相关**：
        正确解 0.0714、错误解 0.1429）。于是「失配」这个信号恒为红，
        它不再携带任何信息，却仍然每轮被读成"因果闭环在工作"。
        ⇒ 这是同一种失效形态：**一个永远不可能"好"的信号。**

        修法不是调门槛（那是把尺子下移），而是**把预测的对象降到内核真能观测的层面**：

        | 预演的对象 | 谁来检验 | 可比吗 |
        |---|---|---|
        | 这一步**会不会成功** (`expect_ok`) | `result.ok` —— 内核直接持有 | ✅ 同一种东西 |
        | 会观察到**什么文本** | 只有同源的文本才有可比性 | ⚠️ 仅检索式先例 |

        所以：有成败就**只**比成败；没有才退回文本比对，而文本类失配
        **不产生持久规则**（见 `Mismatch.teachable`）。

        ⚠️ `ok` 缺省为 None，但**不会静默降级成文本比对** ——
        预演说得出成败、调用方却没给成败，那是**接线断了**，要计入 `unscored`
        让它在读数里显形，而不是换一把不合适的尺子把错误藏起来。
        """
        if prediction.expect_ok is not None:
            if ok is None:
                # 预演可检验、但没有观测 —— 记"没量到"，不记"量到 0"。
                self.unscored += 1
                return None
            return self._observe_outcome(prediction, actual, bool(ok))
        return self._observe_text(prediction, actual)

    def _observe_outcome(self, prediction: Prediction, actual: str,
                         ok: bool) -> Mismatch | None:
        """按**成败**比对 —— 这是唯一能让"做对"和"做错"被分开的比法。"""
        self.scored += 1
        hit = prediction.expect_ok == ok
        self.cm[("ok" if prediction.expect_ok else "fail") + "_" + ("ok" if ok else "fail")] += 1
        if hit:
            self.hits += 1
            return None
        mm = Mismatch(prediction.action, prediction.expected, actual, 0.0,
                      kind="outcome", predicted_ok=prediction.expect_ok, actual_ok=ok)
        self.learn(mm)
        return mm

    def _observe_text(self, prediction: Prediction, actual: str) -> Mismatch | None:
        """按**文本重合度**比对 —— 已知边界，只在没有成败可依时使用。"""
        self.text_compared += 1
        agree = similarity(prediction.expected, actual)
        if agree >= _MISMATCH_THRESHOLD:
            return None
        self.text_mismatches += 1
        mm = Mismatch(prediction.action, prediction.expected, actual, agree, kind="text")
        self.learn(mm)
        return mm

    def learn(self, mm: Mismatch) -> None:
        """沉淀失配：episodic 存案例，semantic 存启发式。**修记忆，不改权重。**

        ⚠️ 只有 `teachable` 的失配才进语义层。文本类失配**只记情景**：
        它记的是措辞差异，不是"哪里容易看错"，写成规则就是在造噪声
        （V1 之前 9/10 条 facts 就是这么来的）。
        """
        # ⚠️ meta 键不能叫 `kind`：`record(kind, content, **meta)` 的首个形参就叫 kind，
        #    同名会在**运行期**炸 `got multiple values for argument 'kind'`
        #    —— 这是本次改动的第一次自检就抓到的真 bug（不是测试环境问题）。
        self.memory.record("mismatch", mm.as_episode(),
                           action=mm.action[:200], agreement=round(mm.agreement, 3),
                           mm_kind=mm.kind)
        if not mm.teachable:
            return
        if not self.allow_learn:
            return
        # 成败是**观测到的事实**，不是文本相似度的函数 ⇒ 置信度不跟 agreement 走。
        self.memory.commit_fact(mm.as_heuristic(), source="worldmodel", confidence=0.7)

    # ------------------------------------------------------------------
    # ⛔ 这里曾经有一个 `foresight_filter(preds)` —— 2026-10-17 接线审计发现
    #    它**只有测试在用**（`contract_test.py:271`），生产路径一次都没调用：
    #    内核是**逐候选**预测的，用的是 `Prediction.trustworthy` 这一个属性。
    #    保留一个"只有测试在调"的公开方法，正是本项目反复防的**装饰 API**：
    #    它让覆盖率看起来变好，而真实路径上没有任何人走它。
    #    ⇒ 删掉。门槛从此**只声明一处**：`Prediction.trustworthy`；
    #      抑制计数只由内核在真正跳过时累加（`core.py` 的 `suppressed += 1`）。

    # ------------------------------------------------------------------
    # 外部奖励：**只读输入**（DESIGN §17-5）
    #
    # 这条通道存在的理由是一处结构性空洞，不是优化：
    #   内核的动作只有 `finish`，而 `finish` **永不失败**
    #   ⇒ `result.ok` 恒为 True ⇒ 世界模型**没有可学的失败**
    #   ⇒ 语义记忆 0 条 ⇒ 泛化实验变成"空记忆 vs 空记忆"，Δ=0 是算术必然
    #   （DESIGN §15.2b 实测：修完失配信号后，学到的 facts 从 9 条噪音变成 0 条）。
    # 失败的其实是"答案对不对"，而那只有**外部打分器**知道。
    #
    # 它与已有不变量完全同构：**尺子可被内核读取，不可被内核改写。**
    # 这里把它贯彻到"分数"这一层 —— 内核可以读外部给的分数，绝不许自己给。
    # ------------------------------------------------------------------
    def record_reward(self, *, task_id: str, features: frozenset[str] | set[str],
                      passed: bool, score: float | None = None,
                      failure: str = "", source: str = "") -> bool:
        """把**外部打分器**给的分数作为一条 outcome 写进记忆。

        ⛔ 三条硬边界，每条都有可归因的拒绝计数（不静默）：

        1. `source` 必须是 `REWARD_SOURCE`。内核自评一律拒 ⇒ `rewards_rejected_source`。
           这不是洁癖：内核给自己打分就是**自评分**，而自评分会绕开外部锚层
           —— 整个项目的立论（"变好"要有外部依据）会在这里被掘穿。
        2. `task_id` 与 `features` 缺一不可 ⇒ `rewards_rejected_missing`。
           没有题号就挡不住"当前题读到自己的分数"；没有特征就检索不到同类题。
        3. **只记已答完的题。** 纪律由调用顺序保证（外部在打分**之后**写），
           检索侧再挡一道：`reward_guidance` 排除当前 `task_id`。

        `features` 必须由 `mw.features` 从**题面**算出（不含答案、不含得分）。
        本方法**不校验**它的来源 —— 校验放在 `features.py`（AK1/AK2 静态钉死），
        同一事实只声明一处。
        """
        if source != REWARD_SOURCE:
            self.rewards_rejected_source += 1
            return False
        if not task_id or not features:
            self.rewards_rejected_missing += 1
            return False
        rkey = ",".join(sorted(str(f) for f in features))
        self.memory.record(
            "reward",
            f"题 {task_id}：外部得分 {score if score is not None else 'n/a'}，"
            f"{'通过' if passed else '未通过'}"
            f"{('，失败模式：' + failure) if failure else ''}",
            task_id=str(task_id), rkey=rkey, passed=bool(passed),
            score=(None if score is None else round(float(score), 4)),
            failure=str(failure)[:200], source=source,
        )
        self.rewards_recorded += 1
        return True

    def reward_guidance(self, *, features: frozenset[str] | set[str] | None,
                        task_id: str | None = None, k: int = 3) -> str:
        """按**结构特征**检索历史外部奖励，生成**可执行**的提示。

        为什么按特征而不是按文本：文本检索是词法的，实测给 `int_to_roman`
        检索出的第一条是「执行 [goal query recall …]」这种自指噪音（§15.2 ②）。
        特征重合度是集合运算，与用词无关 —— 契约 AK8 实测它对检索**有效**
        （同结构族内相似度 0.418 vs 跨族 0.226）。

        ⛔ `task_id` 相同的记录**一律跳过**（`rewards_skipped_self` 计数）：
        读到自己上一次在这道题上的分数 = 开卷考同一道题，
        那不是"学习"，那是把测试集答案抄进提示词。
        """
        if not features:
            return ""
        cur = set(str(f) for f in features)
        hits: list[tuple[float, dict]] = []
        for ep in self.memory.episodes(kind="reward", limit=300):
            meta = getattr(ep, "meta", None)
            if not isinstance(meta, dict):
                continue
            tid = str(meta.get("task_id") or "")
            if not tid:
                continue
            if task_id is not None and tid == str(task_id):
                self.rewards_skipped_self += 1
                continue
            hist = set(x for x in str(meta.get("rkey") or "").split(",") if x)
            if not hist:
                continue
            sim = len(cur & hist) / len(cur | hist)
            if sim < REWARD_MIN_SIM:
                continue
            hits.append((sim, meta))
        if not hits:
            return ""
        hits.sort(key=lambda x: -x[0])
        lines = []
        for sim, meta in hits[:k]:
            passed = bool(meta.get("passed"))
            sc = meta.get("score")
            fail = str(meta.get("failure") or "")
            head = f"同类题（结构重合 {sim:.2f}）：历史{'通过' if passed else '未通过'}"
            if isinstance(sc, (int, float)):
                head += f"，得分 {float(sc):.2f}"
            # ⚠️ 写**可执行**形态：给出"先检查什么"，不是"不可假设…"
            #    （后者是 V1 之前 9/10 条 facts 的形态，读了也改不了任何动作）
            if fail:
                head += f" ⇒ 先检查：{fail}"
            else:
                head += " ⇒ 无记录的失败模式"
            lines.append("- " + head)
        self.rewards_seen += len(hits[:k])
        return "历史同类题的外部得分：\n" + "\n".join(lines)

    def guidance(self, *, action: str, k: int = 3,
                 features: frozenset[str] | set[str] | None = None,
                 task_id: str | None = None) -> str:
        """给规划器/预演器的启发式提示：这次动作在哪容易看错。

        只挑 `source=="worldmodel"` 的事实 —— 而那只有 outcome 类失配会写
        （见 `learn`）。所以喂进决策链的**只可能是"动作成败"类教训**，
        不会再把"措辞差异"当成易错点。

        `features` / `task_id` 来自**外部**（题面特征与题号）：给了就把
        外部奖励通道的历史也拼进来（见 `reward_guidance`）。
        ⚠️ 它们是 Optional 而不是必填，因为内核**不认识题**——
        它只认"目标文本"。题面结构与题号属于外部锚层，由调用方持有。
        """
        parts: list[str] = []
        facts = self.memory.recall(action, k=k)
        wm = [f for f in facts if f.source == "worldmodel"]
        if wm:
            parts.append("已知易错点：\n" + "\n".join(f"- {f.text}" for f in wm))
        rg = self.reward_guidance(features=features, task_id=task_id, k=k)
        if rg:
            parts.append(rg)
        return "\n".join(parts)

    def stats(self) -> dict:
        """读数。**没有静默的 0**：

        · `hit_rate` 在 `scored==0` 时是 `None`（没量到），不是 `0.0`（量到 0）。
        · `confusion` 是 2×2 混淆矩阵 —— 一个恒说"会成功"的预测器
          `fail_ok/fail_fail` 恒为 0，一眼就能看出来；单看准确率看不出来。
        """
        return {
            "predictions": self.predictions,
            # —— 分辨力（缺陷 #12 的修复）——
            "scored": self.scored,
            "hits": self.hits,
            "hit_rate": round(self.hits / self.scored, 3) if self.scored else None,
            "confusion": dict(self.cm),
            "unscored": self.unscored,
            # —— 失配（从混淆矩阵派生，不另存）——
            "mismatches": self.mismatches,
            "mismatches_outcome": self.cm["ok_fail"] + self.cm["fail_ok"],
            "mismatches_text": self.text_mismatches,
            "text_compared": self.text_compared,
            # —— selective foresight ——
            "suppressed": self.suppressed,
            "suppress_rate": round(
                self.suppressed / self.predictions, 3) if self.predictions else 0.0,
            # —— 外部奖励通道（只读）——
            "rewards_recorded": self.rewards_recorded,
            "rewards_rejected_source": self.rewards_rejected_source,
            "rewards_rejected_missing": self.rewards_rejected_missing,
            "rewards_seen": self.rewards_seen,
            "rewards_skipped_self": self.rewards_skipped_self,
        }
