"""三层记忆 —— 2026 的 "tri-tier memory" 落地版，零依赖。

依据：RedLineSoft 2026 技术报告 / ASI111 复杂度第一约束 / WorldEvolver(2026-06)
一致收敛到同一拓扑：
    working（工作记忆，窗口内、易失）
    episodic（情景记忆，带时间戳的事件流）
    semantic（语义记忆，去重后的稳定事实 + 全文检索）

三条**必须**遵守的纪律（全部来自 V5 实测踩过的坑）：
  1. **写入去重**。V5 的 semantic_knowledge 里同一内容重复存了 39 次，
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
import re
import sqlite3
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# 规范化：去空白折叠 + 大小写无关 —— 否则 "Python 截断" 与 "python  截断" 会被当成两条
_WS = re.compile(r"\s+")
_TOKEN = re.compile(r"[A-Za-z0-9_\u4e00-\u9fff]+")


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
"""


class MemoryStore:
    def __init__(self, path: Path | str, *, working_items: int = 24) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.working: deque[tuple[float, str]] = deque(maxlen=max(1, working_items))
        # FTS 降级计数：**不许静默兜底**（R41）。检索退回 LIKE 是允许的，
        # 但必须留痕，否则"检索质量下降"会变成一个没人知道的慢性病。
        self.fts_fallbacks = 0
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ---------------- working ----------------
    def think(self, text: str) -> None:
        """工作记忆：只活在本轮进程 + 窗口内，**不落盘**。刻意如此。"""
        self.working.append((time.time(), text))

    def working_view(self) -> list[str]:
        return [t for _, t in self.working]

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
        这是 V5 "同一内容存 39 次" 的正面修复，且是 DB 约束而非调用方约定。
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
        self.conn.commit()
        return key, is_new

    def recall(self, query: str, k: int = 6) -> list[Fact]:
        """语义检索：FTS5 优先，语法/空结果时退回 LIKE。**永不抛异常**。"""
        k = max(1, int(k))
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
        if not rows:
            rows = self.conn.execute(
                "SELECT *, 0.0 AS score FROM facts ORDER BY hits DESC, updated_ts DESC LIMIT ?",
                (k,),
            ).fetchall()
        return [self._fact(r, r["score"]) for r in rows]

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
        return {"facts": f, "episodes": e, "by_source": {r["source"]: r["c"] for r in src}}

    def duplicate_rows(self) -> int:
        """恒为 0 —— 这是去重约束的自证读数，测试会断言它。"""
        return self.conn.execute(
            "SELECT COUNT(*) c FROM (SELECT key FROM facts GROUP BY key HAVING COUNT(*)>1)"
        ).fetchone()["c"]

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "MemoryStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
