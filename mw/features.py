"""题面**结构特征** —— 让「能力族」不再是「它在哪个文件里」。

---

## 为什么要这个模块

自我模型（`mw/metacog.py`）的核心断言是：

    知道了这道题属于哪个**族**，我能更好地预测我会不会做对。

而族现在的定义是 `family = suite.name`（`mw/bench.py:769`）—— **它在哪个文件里**。

这是**同义反复的温床**，而且已经到了必须拆掉的程度：
题目是我按"数值逻辑 / 结构转换 / 文本操作"三个抽屉分装的，
然后我用数据证明"这三个抽屉的通过率不同、且可预测"。
**这个结论里没有一点关于题目的信息，全是关于我怎么分装的。**

类比：把学生按"张老师带的班 / 李老师带的班"分组，然后宣布
"我能预测哪个班平均分高" —— 那测的是分班，不是学生。

⇒ 合法的族必须由**题面结构**决定：两个题面结构像的题，
即使被我放进不同文件，也必须是同族。

---

## 三条纪律（写死，改之前先读）

1. **只用题面，绝不用答案。** `task_features(task)` 的输入只有
   `prompt` + `tests` + `meta`，**不看 `oracle`**（那是标准答案，
   用它定义族等于用答案定义难度），**更不看 `passed` / `score`**
   （那是被预测的量，用它定义族就是彻底的循环论证 —— 拿结果造输入）。
   ⇒ 契约 AK3/AK4 用源码静态锚钉死这一点。
2. **纯确定性、零 LLM。** 特征必须可手算、可复核、跨进程逐位一致。
   任何随机性都会让"族"变成一个不可复现的幽灵。
3. **版本化 + 事前冻结。** `FEATURES_VERSION` 是特征定义的身份。
   改一个关键词 ⇒ 版本必须变 ⇒ 旧账本上的族标签自动失效。
   ⛔ **禁止为了把读数变绿而回来调特征** —— 那是尺子购物（试十种定义挑最好看的），
   与本项目的整个立论（尺子不可被改写）直接冲突。
   本轮的规则是：**写定 → 跑一次 → 结果不论好坏都入档。**

---

## 聚类的性质

族 = 特征向量的凝聚层次聚类（average linkage，Jaccard 距离），簇数固定 `K_CLUSTERS=3`
（与现有三个套件对齐，便于逐项对照）。

⚠️ 聚类是 **transductive** 的：它看到了全部 15 道题的特征。这在此处合法，
因为聚类**只用了特征、没用通过率** ⇒ 不泄漏被预测的量。
⛔ 但必须记住：换一批题进来，聚类会重算 ⇒ 族标签**不是跨任务集稳定的**。
这是已知边界（见 README 边界清单），不是遗漏。
"""

from __future__ import annotations

import re

#: 特征定义的身份。**改一个关键词就必须改它** —— 否则旧账本上的族标签会静默漂移。
FEATURES_VERSION = "f1"

#: 簇数。固定值，刻意不是"试出来最好看的那个"。
K_CLUSTERS = 3


# ===========================================================================
# 特征规则 —— 每一条都写成"人能逐条复核"的形式
#
# 匹配对象是 `_statement(task)`：prompt + tests 拼起来、转小写。
# 刻意用**子串**而不是正则：子串的语义可以被人一眼验证，正则不能。
# ===========================================================================

#: 输入域 —— 从函数签名的**参数名**推断（题面里唯一不带答案信息的类型线索）
_ARG_RULES: dict[str, tuple[str, ...]] = {
    "in_str":  ("s", "text", "qs", "word", "string"),
    "in_seq":  ("seq", "rows", "words", "intervals", "pairs", "items", "lst"),
    "in_dict": ("d", "dict", "mapping"),
    "in_num":  ("n", "k", "a", "b", "from_base", "to_base"),
}

#: 输出域 —— 从题面里 `返回 / 输出` 之后的表述推断
_OUT_RULES: dict[str, tuple[str, ...]] = {
    "out_bool":  ("true/false", "true / false", "返回 true", "返回 false"),
    "out_str":   ("字符串", "csv 文本", "文本"),
    "out_seq":   ("列表", "序列", "元组"),
    "out_dict":  ("dict", "字典"),
    "out_int":   ("整数", "总秒数", "中位数", "次数"),
    "out_none":  ("none",),
}

#: 操作 —— 题面里出现的动作词
_OP_RULES: dict[str, tuple[str, ...]] = {
    "op_parse":    ("解析",),
    "op_regex":    ("正则", "re.", "[a-za-z]", "percent-deco"),
    "op_sort":     ("排序", "升序", "从高到低", "从低到高"),
    "op_count":    ("次数", "统计", "出现次数", "计数"),
    "op_merge":    ("合并", "折叠"),
    "op_nest":     ("嵌套", "展平", "逐段", "递归", "深层"),
    "op_convert":  ("转成", "转换", "编码"),
    "op_cond":     ("若", "如果", "否则", "缺失"),
}

#: 元信息 —— 出题时声明的难度（人为，但与套件无关 ⇒ 不是同义反复）
_LEVELS: dict[str, int] = {"easy": 0, "medium": 1, "hard": 2}


def _statement(task: dict) -> str:
    """题面全文：prompt + tests。**不含 `oracle`，不含任何得分。**"""
    parts = [str(task.get("prompt") or "")]
    tests = task.get("tests")
    if isinstance(tests, list):
        parts.append(" ".join(str(t) for t in tests))
    else:
        parts.append(str(tests or ""))
    return "\n".join(parts).lower()


def _args(task: dict) -> list[str]:
    """从 `写一个函数 NAME(a, b)` 里抽出参数名列表。"""
    m = re.search(r"函数\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)",
                  str(task.get("prompt") or ""))
    if not m:
        return []
    return [a.strip().split("=")[0].strip().lower()
            for a in m.group(2).split(",") if a.strip()]


def task_features(task: dict) -> dict[str, int]:
    """抽取结构特征。**纯函数**：同输入 ⇒ 同输出，不看答案、不看得分。

    返回值是 `{特征名: 0/1/2}`。值为 2 的只有 `level`（三档难度）。
    """
    text = _statement(task)
    args = _args(task)
    meta = task.get("meta") or {}
    if not isinstance(meta, dict):
        meta = {}
    out: dict[str, int] = {}

    # —— 输入域 ——
    for feat, names in _ARG_RULES.items():
        out[feat] = 1 if any(a in names for a in args) else 0

    # —— 输出域 ——
    for feat, kws in _OUT_RULES.items():
        out[feat] = 1 if any(k in text for k in kws) else 0

    # —— 操作 ——
    for feat, kws in _OP_RULES.items():
        out[feat] = 1 if any(k in text for k in kws) else 0

    # —— 边界用例：tests 里出现空值 ⇒ 这道题要求处理退化输入 ——
    tests = task.get("tests")
    tests_text = " ".join(str(t) for t in tests) if isinstance(tests, list) else str(tests or "")
    out["edge_empty"] = 1 if (("[]" in tests_text) or ('""' in tests_text)
                              or ("none" in tests_text.lower())) else 0

    # —— 规模 ——
    n_tests = len(tests) if isinstance(tests, list) else 0
    out["n_tests_ge5"] = 1 if n_tests >= 5 else 0
    out["level"] = int(_LEVELS.get(str(meta.get("level", "")).lower(), 1))
    return out


def feature_set(task: dict) -> frozenset[str]:
    """把特征压成集合（忽略取值差异），供 Jaccard 距离用。

    `level` 是三档 ⇒ 展开成 `level0/level1/level2` 三个互斥布尔位，
    否则 Jaccard 会把 level=0 与 level=2 当成同一个特征。
    """
    f = task_features(task)
    keys = [k for k, v in f.items() if v and k != "level"]
    keys.append(f"level{int(f['level'])}")
    return frozenset(keys)


def feature_version_hash() -> str:
    """特征定义的内容指纹 —— 规则改一个字它就变。

    这是为了防止"悄悄改规则、旧族标签继续用"。
    """
    import hashlib
    blob = repr((_ARG_RULES, _OUT_RULES, _OP_RULES, _LEVELS,
                 FEATURES_VERSION, K_CLUSTERS))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# ===========================================================================
# 聚类：average-linkage 层次聚类，全部 tie-break 确定性
# ===========================================================================

def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a and not b:
        return 0.0
    return 1.0 - len(a & b) / len(a | b)


def cluster(tasks: list[dict], k: int = K_CLUSTERS) -> dict[str, str]:
    """`{task_id: 族标签}`，族标签形如 `f1c0`。

    ⛔ 只用特征，**绝不用 `passed`/`score`** —— 否则就是拿被预测的量造输入。
    确定性：初始顺序按 `task_id` 排序，并列距离取最小下标对。
    """
    items = sorted(((str(t.get("id")), feature_set(t)) for t in tasks),
                   key=lambda x: x[0])
    clusters: list[list[int]] = [[i] for i in range(len(items))]
    feat = [c for _, c in items]

    def dist(ci: list[int], cj: list[int]) -> float:
        # average linkage
        tot = 0.0
        for i in ci:
            for j in cj:
                tot += _jaccard(feat[i], feat[j])
        return tot / (len(ci) * len(cj))

    while len(clusters) > k:
        best = None
        for a in range(len(clusters)):
            for b in range(a + 1, len(clusters)):
                d = dist(clusters[a], clusters[b])
                if best is None or d < best[0] - 1e-12:
                    best = (d, a, b)
        assert best is not None
        _, a, b = best
        merged = clusters[a] + clusters[b]
        clusters = [c for i, c in enumerate(clusters) if i not in (a, b)] + [merged]

    # 族标签按**簇内最小 task_id**排序后编号 ⇒ 与输入顺序无关
    order = sorted(range(len(clusters)), key=lambda ci: min(items[i][0] for i in clusters[ci]))
    out: dict[str, str] = {}
    for rank, ci in enumerate(order):
        label = f"{FEATURES_VERSION}c{rank}"
        for i in clusters[ci]:
            out[items[i][0]] = label
    return out


# ⛔ 这里**故意不提供** `load_task_index` 之类的读文件工具。
#    本模块是内核模块（`mw/`），它只该做"给一道题算特征"这一件纯计算的事。
#    读 `bench/tasks/*.jsonl` 是**外部锚层**的活：内核里放一个"能读任务集"
#    的函数，就给"内核自己造题、自己打分"留了一扇门 —— 那正是外部锚层
#    存在的理由要挡掉的事。任务集索引放在 `bench/` 侧（探针与契约各自取用）。
