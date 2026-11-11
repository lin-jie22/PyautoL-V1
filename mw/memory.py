"""三层记忆 —— 2026 的 "tri-tier memory" 落地版，零依赖。

依据：RedLineSoft 2026 技术报告 / ASI111 复杂度第一约束 / WorldEvolver(2026-06)
一致收敛到同一拓扑：
    working（工作记忆，窗口内、易失）
    episodic（情景记忆，带时间戳的事件流）
    semantic（语义记忆，去重后的稳定事实 + 全文检索）

三条**必须**遵守的纪律（全部来自实测踩过的坑）：
  1. **写入去重**。曾经出现过同一内容重复存 39 次的情况，
     因为 learner 零去重、auditor 与 learner 解耦。所以 V1 的 semantic
     表用「规范化文本的 sha256」做主键，`INSERT ... ON CONFLICT DO UPDATE`，
     重复写入只累加 hits，不新增行。这是**数据库层面**的保证，不靠调用方自觉。
  2. **来源与可信度必填**。Memory Poisoning 是 OWASP Agentic Top-10 的一条；
     没有来源的记忆无法审计，也没法在冲突时判断谁更可信。
  3. **检索必须容错**。FTS5 的 MATCH 语法遇到自然语言（含 `-` / `"` / `:`）
     会直接抛异常。所以查询先被切成安全 token，失败再退回 LIKE。

存储用 stdlib 的 sqlite3。FTS5 在本机已确认可用（3.50.4）。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# 规范化：去空白折叠 + 大小写无关 —— 否则 "Python 截断" 与 "python  截断" 会被当成两条
class BlobIntegrityError(RuntimeError):
    """原文仓的完整性被破坏（读回来的字节与写侧承诺的 sha/长度不符）。

    ⚠️ 为什么不用 `sqlite3.IntegrityError`：那是 DB 约束错误，与"原文被改过"
    是两件事，混用会让日志里分不清"我插重了"还是"库被人动过"。
    """


_WS = re.compile(r"\s+")
_TOKEN = re.compile(r"[A-Za-z0-9_\u4e00-\u9fff]+")

#: 字级检索用的两个切法（V84 / §153）。⛔ 与 `_TOKEN` **无关**：
#:   `_TOKEN` 把连续中文当一个 token（AZ26 钉死），它在**第一级**里继续负责
#:   「题面明写钥匙就精确命中原条」；这里只服务**第二级**（第一级零命中之后），
#:   把中文 run 切成 2 字链，让「题面」与「经验」在**任意 2 个字相同**时就能接上。
_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")
_ASCII_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_]*")


def char_grams(text: str, *, cap: int = 24, ascii_cap: int = 8) -> list[str]:
    """把自然语言切成「2 字链 + ASCII 词」（按出现顺序去重）。

    ⚠️ 为什么是 2 字而不是 1 字：1 字（"的"/"先"）在任何中文事实里都有 ⇒ 等于全表命中。
    为什么不是整句：V83 实测题面与经验的 `_TOKEN` 精确交集 = 0/4，整句永远对不上。
    """
    out: list[str] = []
    for run in _CJK_RUN.findall(text or ""):
        for i in range(len(run) - 1):
            g = run[i:i + 2]
            if g not in out:
                out.append(g)
    out = out[:cap]
    seen: list[str] = []
    for w in _ASCII_WORD.findall(text or ""):
        w = w.lower()
        if len(w) >= 3 and w not in seen:
            seen.append(w)
    return out + seen[:ascii_cap]


def _spaced(text: str) -> str:
    """逐字分空 ⇒ `unicode61` 把每个字当独立 token，2 字**短语**才可查。"""
    out: list[str] = []
    for ch in text or "":
        out.append(" " if ch.isspace() else ch)
        out.append(" ")
    return "".join(out).strip()


def normalize(text: str) -> str:
    return _WS.sub(" ", (text or "").strip())


def fingerprint(text: str) -> str:
    """语义记忆的去重键：规范化后的 sha256。**这是唯一去重判据**。"""
    return hashlib.sha256(normalize(text).lower().encode("utf-8")).hexdigest()[:32]


def fts_query(text: str) -> str:
    """把自然语言变成 FTS5 安全查询：切成 token，各自加引号，OR 连接。

    不加引号的话，`C++` 的 `+`、`foo-bar` 的 `-`、`a:b` 的 `:` 都会被 FTS5
    当成语法 ⇒ 抛 OperationalError。这里做的是**预防**，不是兜底。
    """
    toks = _TOKEN.findall(text or "")
    if not toks:
        return ""
    return " OR ".join(f'"{t}"' for t in toks[:24])


@dataclass
class Fact:
    id: int
    text: str
    source: str
    confidence: float
    hits: int
    ts: float
    score: float = 0.0

    def as_line(self) -> str:
        return f"[{self.source} c={self.confidence:.2f} x{self.hits}] {self.text}"


@dataclass
class Episode:
    id: int
    ts: float
    kind: str
    content: str
    meta: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Handle:
    """一次观测的**句柄** —— 指向 `blobs` 表里那一整段原文。

    ★ 为什么要有这个东西（第二十轮，DESIGN §16.19）：
      在此之前，工具返回 5000 字，进工作记忆的只有前 300 字，
      其余 4700 字**当场消失**（`perceive>memory` 覆盖率 0.06）。
      而下一步关于上一步的一切都只来自那 300 字。
      `Handle` 把"消失"改成"转存 + 可寻址"：**宽度不变，可回溯性从 0 变 1**。

    ⛔ `chars` 与 `sha` 是**写侧承诺的事实**，读侧要用它们自证：
      `expand()` 必须核验读回来的文本 sha 与这里一致，否则说明库被动过。
    """
    ref: str        # `b#<本会话第几次捕获>`。⛔ **不进 prompt**（V36 起）：
                    #   它是**捕获次序**的函数，不是内容的函数 ⇒ 同一段正文在
                    #   下一轮只要前面多捕获 k 条，句柄就整体后移 k。实测（V35）：
                    #   `b#1`→`b#12`（+1 字符）就能让 `e1wi` 从 0 分翻到 16 分，
                    #   而两条记忆的**正文逐字节相同**。
                    #   模型也**用不到它**：`expand()` 只由内核 `_recall_for()` 用
                    #   进程内的 `handle.ref` 调用，没有任何工具接受 ref。
                    #   ⇒ 它只活在进程内与日志里，`[工作记忆]` 的索引行改用
                    #   `chars` + `sha`（两者都是内容的函数）。⛔ 别把它加回 prompt。
    sha: str        # 全文 sha256 前 12 位
    chars: int      # 全文字符数

    def as_line(self) -> str:
        return f"{self.ref} · {self.chars}字 · sha={self.sha}"


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS episodes (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      REAL NOT NULL,
    kind    TEXT NOT NULL,
    content TEXT NOT NULL,
    meta    TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_episodes_kind ON episodes(kind, ts);

CREATE TABLE IF NOT EXISTS facts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    key        TEXT NOT NULL UNIQUE,
    text       TEXT NOT NULL,
    source     TEXT NOT NULL,
    confidence REAL NOT NULL,
    hits       INTEGER NOT NULL DEFAULT 1,
    ts         REAL NOT NULL,
    updated_ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_facts_source ON facts(source);

CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(text);

-- 字级索引（V84 / §153）：第一级零命中后的**第二级**。
--   ⛔ 它**不替代** `facts_fts`：AZ26 把第一级的命中条数钉死成 [1,1,1,1]/[4,4,4,4]，
--   替换必然让「题面提 1 把钥匙」变成命中多条。追加才不动那条锚。
--   文本逐字分空（`_spaced`）⇒ unicode61 把每个汉字当独立 token，2 字短语可查。
CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts_char USING fts5(text);

-- 观测原文仓（第二十轮）。为什么不复用 episodes：
--   episodes 有 `content`，但它是**事件流**（每条被 `record` 截到 1000 字，
--   且按 kind 排序检索），把原文塞进去会让"事件"与"原文"两种语义混在一张表里。
--   更重要的原因：`episodes` 是**只写不读**的（边界第 11 条），而这张表**有读路径**
--   （`expand`），必须有读路径才能说"可回溯"。
CREATE TABLE IF NOT EXISTS blobs (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    ts    REAL NOT NULL,
    kind  TEXT NOT NULL,
    sha   TEXT NOT NULL,
    chars INTEGER NOT NULL,
    text  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_blobs_sha ON blobs(sha);
"""


class MemoryStore:
    def __init__(self, path: Path | str, *, working_items: int = 24) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.working: deque[tuple[float, str]] = deque(maxlen=max(1, working_items))
        # FTS 降级计数：**不许静默兜底**（R41）。检索退回 LIKE 是允许的，
        # 但必须留痕，否则"检索质量下降"会变成一个没人知道的慢性病。
        self.fts_fallbacks = 0
        # 第二级（字级 2 字链）的两个计数器。⛔ **不许静默兜底**（同 R41）：
        #   `char_fallbacks` = 第一级零命中后**走了**第二级；`char_hits` = 第二级**真的返回了东西**。
        #   两个数分开，否则"试了但空手"与"根本没试"会被读成同一件事。
        self.char_fallbacks = 0
        self.char_hits = 0
        #: 档位：0 = 关（= V83 及之前，第一级零命中就返回 `[]`）；1 = 开（≥1 相异链）；
        #: 2 = 开但收紧到 ≥2 相异链（G4 噪音地板不过时的第二个档，预注册里写死只许试到这一档）。
        #  ⚠️ 它不是 `Limits` 字段 ⇒ **不进 `sig`**：两臂同签、不同行为。
        #    ⛔ 所以「`sig` 相同」不能当「同口径」（`V1_ANSWER_CLEAN` 的同一条坑）。
        try:
            self.char_level = max(0, int(
                (os.environ.get("V1_RECALL_CHAR", "0") or "0").strip() or 0))
        except ValueError:
            self.char_level = 0
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        #: ★ 句柄编号的**分母**：打开这个库时 `blobs` 已经有多少行（§91 治污）。
        #  ⛔ 为什么不能用 `blobs` 的全局自增 id 直接当句柄（§90 实测）：
        #    那个数字会随「库变大」而变，而它**进了 prompt**（`core.py:1110` 的索引行）。
        #    于是「库里有 11 条历史」与「这一格从 0 分变 16 分」之间出现了一条
        #    **与语义完全无关**的因果边：`b#1` → `b#12`（+1 字符）就够翻转。
        #    实测：`s12`（空库 + 预置 11 条 dummy blob）两次独立冷启动都是 64，
        #    而空库对照两次都是 48（§90.6）。
        #  ⇒ 句柄改成**本会话内序**（`rowid - base`），长度一点没变（仍是 `b#1`），
        #    但「库里有几条历史」不再能改动 prompt。
        #  ⚠️ 代价：内序 ref **不能跨进程解析**（重开库后 base 变）。
        #    本系统里 ref 从不落盘：它只活在 `working`（进程级）与 state 快照里，
        #    `expand()` 总与 `capture()` 同进程 ⇒ 本装置内无成本。换装置必须重审。
        self._blobs_base = int(
            self.conn.execute("SELECT COALESCE(MAX(id), 0) FROM blobs").fetchone()[0])
        # 字级索引的**回填**：只比条数，不逐行比内容。
        #   ⚠️ 为什么够：`facts` 的行数只在这里与 `commit_fact` 两处变，两处都维护索引
        #   ⇒ 条数相等就说明没有"漏索引的旧行"。不相等就整表重建（幂等、可重复跑）。
        #   ⚠️ 这会在 `__init__` 里写库 ⇒ 归档证据库必须**先 copy 再开**（纪律 31）。
        if self.conn.execute("SELECT COUNT(*) FROM facts_fts_char").fetchone()[0] != \
                self.conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]:
            self._rebuild_char_index()

    def _rebuild_char_index(self) -> None:
        self.conn.execute("DELETE FROM facts_fts_char")
        for r in self.conn.execute("SELECT id, text FROM facts").fetchall():
            self.conn.execute("INSERT INTO facts_fts_char(rowid, text) VALUES (?,?)",
                              (r["id"], _spaced(r["text"])))
        self.conn.commit()

    # ---------------- working ----------------
    def think(self, text: str) -> None:
        """工作记忆：只活在本轮进程 + 窗口内，**不落盘**。刻意如此。"""
        self.working.append((time.time(), text))

    def working_view(self) -> list[str]:
        return [t for _, t in self.working]

    def reset_working(self) -> int:
        """**题边界**：清空工作记忆，返回被清掉的条数。

        ★ 为什么这件事必须在内核里做（第三十六轮 §17-7h）：
          `working` 是**进程级** deque，而 bench 模式复用**同一个** `Core` 连跑多题。
          于是上一题的工具输出会留在下一题的 `[工作记忆]` 块里 —— 而 `recall` 工具
          成功时返回的是**全部** facts 的拼接（实测 389 字），
          `_state()` 又只取每条输出的**尾 200 字** ⇒ 漏出去的正好是"尾巴那段"，
          取决于上一题恰好返回了什么。实测（`probe_g7n_leak.py`）：第 4 题的 state 里
          出现**非本题**惯例 2/3 条，而 R1 只查 `recall()` 完全看不见这条路。
          ⇒ 改"邻域长度"必然同时改变"漏出去的是哪一段" = 那条轴从来不是单变量。

        ⛔ 清空点必须在**写侧**（题边界）。改在 `_state()` 里过滤会让泄漏**隐身**：
           计数恒 0、泄漏照旧 —— 那是"修完等于没修"的典型形态。
        ⚠️ 返回值**必须被消费**（进 stats）：它是"上一题残留了几条"的唯一读数。
        """
        n = len(self.working)
        self.working.clear()
        return n

    def upgrade_last(self, text: str) -> bool:
        """把工作记忆的**最后一条**换成更宽的版本（第二十轮「按需回看」用）。

        ★ 为什么是"替换"而不是"追加一段"：
          追加会让**同一条观测在上下文里出现两次**（一次截断版、一次回看版），
          那违反本项目最基本的一条纪律：同一事实只声明一处。
          第一次实现就是追加，`AB7` 当场转红 —— 它测出"关掉工作记忆写入之后
          回流率仍有 0.5"，原因是回看块充当了**第二条回边**。
          ⇒ 回看是**对同一条记忆的加宽**，它的物理载体仍是 working 这一层。

        返回 `False` = 没有可升级的条目。调用方**必须计数**，不许静默：
        "取回成功了但无处安放"与"没触发"是两件不同的事。
        """
        if not self.working:
            return False
        ts, _ = self.working[-1]
        self.working[-1] = (ts, str(text or ""))
        return True

    # ---------------- 观测原文（第二十轮） ----------------
    def capture(self, text: str, *, kind: str = "observation") -> Handle:
        """把一段观测原文**整段**存下来，返回可寻址的句柄。

        ⚠️ 它与 `think()` 是**两件事**，必须分开：
          · `think()` 往工作记忆放的是**下一步能看见的那一小段**（宽度 = 调用方给的 cap）；
          · `capture()` 往 `blobs` 放的是**原文全部**，只给"要的时候能要回来"用。
        把两者合并（"反正存下来了就多放点"）会让一步吃掉整个上下文 ——
        那正是 §17-9 明确否掉的修法（"不是把 300 调大"）。
        """
        body = "" if text is None else str(text)
        sha = hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]
        cur = self.conn.execute(
            "INSERT INTO blobs(ts, kind, sha, chars, text) VALUES (?,?,?,?,?)",
            (time.time(), kind, sha, len(body), body),
        )
        self.conn.commit()
        # ★ 句柄用**本会话内序**，不是 `lastrowid`（§91）：后者让「库变大」本身改动 prompt。
        seq = int(cur.lastrowid or 0) - self._blobs_base
        return Handle(ref=f"b#{seq}", sha=sha, chars=len(body))

    def expand(self, ref: str, *, cap: int, where: str = "whole") -> tuple[str, bool]:
        """按句柄取回原文的一窗（`head` / `tail` / `whole`）。

        返回 `(text, truncated)`。**只读**：不写库、不改工作记忆、不动任何计数。

        ⛔ 三条硬纪律：
          1. **不许静默兜底**。句柄不存在 ⇒ 抛 `KeyError`；
             读回来的 sha 与写侧承诺的不一致 ⇒ 抛 `IntegrityError`
             （库里被动过是事实，把它悄悄藏起来比报错更糟）。
          2. **截断必须明示**。`truncated=True` 时返回文本里带可见标记，
             不许让调用方"以为这就是全文"（把 `.txt` 判断错就是这一类）。
          3. `cap` 由**调用方**给，不由本模块给默认值 ——
             默认值会让"上下文预算是谁的"变成一处谁也说不清的暗契约。
        """
        if where not in ("head", "tail", "whole"):
            raise ValueError(f"未知取回窗口 {where!r}；合法值：('head','tail','whole')")
        # ★ 句柄 = **本会话内序** ⇒ 反算库里的全局 id（§91）。
        #  ⛔ `seq < 1` 必须当场判不存在：库里 `id` 从 1 起，而 `base` 可能 >0，
        #    于是 `b#0` 会落到 `id == base` 那一行 —— **别人写的行**。
        #    那样「取回失败」会返回一段看似合理的正文，与成功长得一模一样
        #    （与 `.txt 被误判成功` 同一类事故）。
        key = str(ref).strip()
        try:
            seq = int(key[2:] if key.startswith("b#") else key)
        except (TypeError, ValueError):
            raise KeyError(f"句柄不存在：{ref!r}") from None
        if seq < 1:
            raise KeyError(f"句柄不存在：{ref!r}")
        row = self.conn.execute(
            "SELECT sha, chars, text FROM blobs WHERE id=?",
            (seq + self._blobs_base,),
        ).fetchone()
        if row is None:
            raise KeyError(f"句柄不存在：{ref!r}")
        body = row["text"]
        # 完整性自证：写侧承诺的 sha/chars 必须与读回来的一致。
        if (hashlib.sha256(body.encode("utf-8")).hexdigest()[:12] != row["sha"]
                or len(body) != row["chars"]):
            raise BlobIntegrityError(f"句柄 {ref!r} 的原文与写侧承诺不符（库被动过）")
        cap = max(1, int(cap))
        if len(body) <= cap:
            return body, False
        if where == "head":
            cut = body[:cap]
        elif where == "tail":
            cut = body[-cap:]
        else:
            # whole 但超 cap ⇒ 取头 + 尾（各一半）：
            #   长观测的"决定性信息"既可能在开头（结论）也可能在结尾（报错），
            #   ⚠️ 这条取舍是**启发式**，登记为 unknown（见 DESIGN §16.19 ⑦）。
            half = cap // 2
            cut = body[:half] + f"\n…[中略 {len(body) - 2 * half} 字]…\n" + body[-half:]
        return cut, True

    def blob_stats(self) -> dict:
        """原文仓读数。**有消费者**：`stats()` 与 run.py 的读数行。
        `dedup_sha` 只作诊断（同一段原文被存了几次），不参与任何判定。"""
        r = self.conn.execute(
            "SELECT COUNT(*) c, COALESCE(SUM(chars),0) s FROM blobs").fetchone()
        dup = self.conn.execute(
            "SELECT COUNT(*) c FROM (SELECT sha FROM blobs GROUP BY sha HAVING COUNT(*)>1)"
        ).fetchone()["c"]
        return {"blobs": int(r["c"]), "chars": int(r["s"]), "dedup_sha": int(dup)}

    # ---------------- episodic ----------------
    def record(self, kind: str, content: str, **meta: Any) -> int:
        cur = self.conn.execute(
            "INSERT INTO episodes(ts, kind, content, meta) VALUES (?,?,?,?)",
            (time.time(), kind, content, json.dumps(meta, ensure_ascii=False)),
        )
        self.conn.commit()
        return int(cur.lastrowid or 0)

    def episodes(self, kind: str | None = None, limit: int = 50) -> list[Episode]:
        if kind:
            rows = self.conn.execute(
                "SELECT * FROM episodes WHERE kind=? ORDER BY id DESC LIMIT ?", (kind, limit)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM episodes ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        out = []
        for r in rows:
            try:
                meta = json.loads(r["meta"])
            except (json.JSONDecodeError, TypeError):
                meta = {}
            out.append(Episode(r["id"], r["ts"], r["kind"], r["content"], meta))
        return out

    # ---------------- semantic ----------------
    def commit_fact(self, text: str, *, source: str, confidence: float) -> tuple[str, bool]:
        """写语义记忆。返回 (key, 是否新建)。

        ⚠️ 重复写入**只**累加 hits 并把 confidence 取 max —— 不新增行。
        这是"同一内容存 39 次"的正面修复，且是 DB 约束而非调用方约定。
        """
        text = normalize(text)
        if not text:
            raise ValueError("拒绝写入空的语义记忆")
        if not source:
            raise ValueError("语义记忆必须有 source（否则不可审计）")
        conf = max(0.0, min(1.0, float(confidence)))
        key = fingerprint(text)
        now = time.time()
        self.conn.execute(
            """INSERT INTO facts(key, text, source, confidence, hits, ts, updated_ts)
               VALUES (?,?,?,?,1,?,?)
               ON CONFLICT(key) DO UPDATE SET
                   hits = hits + 1,
                   confidence = MAX(confidence, excluded.confidence),
                   updated_ts = excluded.updated_ts""",
            (key, text, source, conf, now, now),
        )
        # 新建 vs 命中已存在：靠 hits 判定（新建恒为 1，命中必然 ≥2）。
        # 这里读的是**刚写完的同一行**，所以不存在两处真相互相漂移的问题。
        existed = self.conn.execute(
            "SELECT id, hits FROM facts WHERE key=?", (key,)
        ).fetchone()
        is_new = bool(existed) and int(existed["hits"]) == 1
        fid = int(existed["id"]) if existed else 0
        if fid:
            self.conn.execute("DELETE FROM facts_fts WHERE rowid=?", (fid,))
            self.conn.execute("INSERT INTO facts_fts(rowid, text) VALUES (?,?)", (fid, text))
            # 第二级索引同源维护：⛔ 只更新 `facts_fts` 会让"新写的经验永远搜不到它"，
            # 而那条路的症状是「写了但没送达」，正是 V82/V83 花两轮才挖出来的形态。
            self.conn.execute("DELETE FROM facts_fts_char WHERE rowid=?", (fid,))
            self.conn.execute("INSERT INTO facts_fts_char(rowid, text) VALUES (?,?)",
                              (fid, _spaced(text)))
        self.conn.commit()
        return key, is_new

    def recall(self, query: str, k: int = 6) -> list[Fact]:
        """语义检索：FTS5 优先，语法错误/零命中时退回 LIKE。**永不抛异常**。

        ★ 第三十七轮（§17-7i 装置修正⑮）：**删掉了第三级"全表 top-k"兜底**。
        为什么必须删（三条理由，独立性递减）：
          · 它是**静默兜底** —— 无计数、无留痕、与 query 完全无关，违反本项目
            硬规则 6（无静默兜底）；`fts_fallbacks` 只记第二级。
          · 实测（7d–7g 全部冻结证据，**33 次** `recall` 调用）：**每一次**都返回
            **全部 4 条**（389 字）。题面的 token 与 facts 有交集（AZ10 钉住）
            ⇒ `_state()` 走 FTS 拿到 4/4/1/1；而模型自己写的短 query 与 facts
            零交集 ⇒ 落到这一级 ⇒ 拿到全表。于是"1 条族"在第 2 步之后变成
            4 条族，而模型基本都在第 2 步之后才交付。
          · 模型问"如何截断字符串"、拿到"金额格式惯例"是**噪音**，不是记忆能力。
         ⇒ 零命中就返回 `[]`，让模型看到"(语义记忆中无相关条目)"：这个信号比假命中
           更诚实。空/纯空白 query 同样返回 `[]`（`LIKE '%%'` 是同一个静默兜底的
           另一个入口 —— 堵一级不堵另一级 = 假装修好）。

        ★ V84（§153）在这一条上加了一处**收窄**：第一级零命中后不再直接 `[]`，
        而是交给 `_recall_char()`（字级 2 字链，默认关）。⛔ 它**不是**把全表兜底加回来：
        第二级仍然要求「题面的某 2 个字真的出现在那条事实上」，而全表兜底与 query 无关。
        ⚠️ 上一段「零命中比假命中更诚实」的判决仍成立 —— 它针对的是**无关**返回；
        第二级返回的是**有字面证据**的条目，且`char_hits` 单列可审计。
        """
        k = max(1, int(k))
        if not normalize(query).strip():
            return []
        q = fts_query(query)
        if q:
            try:
                rows = self.conn.execute(
                    """SELECT f.*, bm25(facts_fts) AS score
                       FROM facts_fts JOIN facts f ON f.id = facts_fts.rowid
                       WHERE facts_fts MATCH ?
                       ORDER BY score LIMIT ?""",
                    (q, k),
                ).fetchall()
                if rows:
                    return [self._fact(r, r["score"]) for r in rows]
            except sqlite3.OperationalError:
                self.fts_fallbacks += 1   # 留痕，不静默
        like = f"%{normalize(query)[:64]}%"
        rows = self.conn.execute(
            "SELECT *, 0.0 AS score FROM facts WHERE text LIKE ? ORDER BY hits DESC LIMIT ?",
            (like, k),
        ).fetchall()
        if rows:
            return [self._fact(r, r["score"]) for r in rows]
        # ★ 第二级（V84 / §153）：第一级两条路都空 ⇒ 才走字级 2 字链。
        #   ⛔ 顺序不可倒：先字级会让 AZ26 的三个字面量（1/4/0 条）全部漂移。
        return self._recall_char(query, k)

    def _recall_char(self, query: str, k: int) -> list[Fact]:
        """字级 2 字链检索。**只**在第一级零命中后被调用（见 `recall`）。

        ⚠️ 它解决的是一条实测到的断链（V83）：题面与经验的 `_TOKEN` 精确交集 = 0/4，
        而同一份经验拿原文当 query 自命中 4/4 ⇒ 坏的不是检索器，是「题面 → 经验」这一跳。

        ⚠️ 它**必然提高误招**：2 字链在自然中文上极常见（V83 实测 8 条无关事实里
        前 6 名有 12 条是无关的）。所以：
          · 默认**关**（`V1_RECALL_CHAR=0`）⇒ 生产行为与 V83 及之前逐位相同；
          · 开了之后按「相异链命中数」过滤，档 2 = 至少 2 条链，用来把噪音压回地板；
          · 两个计数器照记，不许静默。
        """
        if self.char_level <= 0:
            return []
        grams = char_grams(query)
        if not grams:
            return []
        self.char_fallbacks += 1
        need = 1 if self.char_level == 1 else 2
        q = " OR ".join('"%s"' % " ".join(g) for g in grams)
        try:
            rows = self.conn.execute(
                """SELECT f.*, bm25(facts_fts_char) AS score
                   FROM facts_fts_char JOIN facts f ON f.id = facts_fts_char.rowid
                   WHERE facts_fts_char MATCH ?
                   ORDER BY score LIMIT ?""",
                (q, max(k * 4, k)),
            ).fetchall()
        except sqlite3.OperationalError:
            return []          # 语法失败 ⇒ 空，不抛（与第一级同一条纪律）
        kept: list[tuple[int, float, sqlite3.Row]] = []
        for r in rows:
            text = r["text"] or ""
            low = text.lower()
            hit = sum(1 for g in grams if (g in low))
            if hit >= need:
                kept.append((hit, float(r["score"]), r))
        if not kept:
            return []
        kept.sort(key=lambda x: (-x[0], x[1]))
        self.char_hits += 1
        return [self._fact(r, score) for _hit, score, r in kept[:k]]

    @staticmethod
    def _fact(r: sqlite3.Row, score: float) -> Fact:
        return Fact(r["id"], r["text"], r["source"], r["confidence"], r["hits"], r["ts"], score)

    # ---------------- 体检 ----------------
    def stats(self) -> dict:
        f = self.conn.execute("SELECT COUNT(*) c FROM facts").fetchone()["c"]
        e = self.conn.execute("SELECT COUNT(*) c FROM episodes").fetchone()["c"]
        src = self.conn.execute(
            "SELECT source, COUNT(*) c FROM facts GROUP BY source ORDER BY c DESC"
        ).fetchall()
        return {"facts": f, "episodes": e,
                "by_source": {r["source"]: r["c"] for r in src},
                # 第二十轮：观测原文仓。**必须一起报**，否则"可回溯"是个无读数的声明。
                "blobs": self.blob_stats()}

    def duplicate_rows(self) -> int:
        """恒为 0 —— 这是去重约束的自证读数，测试会断言它。"""
        return self.conn.execute(
            "SELECT COUNT(*) c FROM (SELECT key FROM facts GROUP BY key HAVING COUNT(*)>1)"
        ).fetchone()["c"]

    def pool_size(self) -> int:
        """记忆里**可用**的条数（语义事实 + 工作记忆）。

        消费者是 `FlowMeter` 的 `memory>reason` 边：它要知道分母，
        才能回答"这一回合的上下文用的是池子里的多少"。
        ⚠️ 这是唯一为通量计暴露的方法 —— 不要用它做别的判断，
        "库多大"不是任何决策的输入（那是把量当成质）。
        """
        facts = self.conn.execute("SELECT COUNT(*) c FROM facts").fetchone()["c"]
        return int(facts) + len(self.working)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "MemoryStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
