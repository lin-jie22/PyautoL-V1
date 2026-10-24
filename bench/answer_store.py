"""答案本体落盘 —— 唯一目的：让「它到底写错了什么」这件事可复核。

## 为什么需要它（第七十九轮量出来的四处截断）

同一形态的缺陷在四个地方各出现一次，全部是「够人看一眼，不够复现」：

| 位置 | 截断 | 后果 |
|---|---|---|
| `mw/bench.py:801` `sc.why[:200]` | 200 字符 | 写着「通过 7/14」却说不出是哪 7 条 |
| `mw/critique.py:42` `_MAX_ANSWER = 200` | 200 字符 | 唯一的答案落盘通道也被切掉 |
| `run.py` 日志 `✅ 收尾:` | 打印 5 行 | 看到 `def norm_sg05(rows): ... def prep` 就断了 |
| `bench_out/runs.jsonl` | 只存 `report` | **没有答案本体** |

实测代价：`sg05` 在 15 次运行里 `0/14` 全部相同，失败原因是
`AttributeError: 'odict_values' object has no attribute 'sort'` ——
但要看它**到底怎么写的**、错在第几行，四处都拿不到。于是只能拿合成样本做反证
（DESIGN §41.2）。**能证明「自检看不见这类错」，不能证明「模型就是这么写的」。**
这个差别决定下一轮该改什么。

## 三条边界（越一条它就变成作弊通道）

① **只写答案文本本身**，不写分数、不写判据、不写「对不对」。
   它不是分数通道，也不是判据通道 —— 谁想在判据里读它，先看 AO11。
② **整轮跑完之后才写**（与 `ScoreLedger` / `WorldModel` 同理）：
   轮内写会让第 N 题读到第 N-1 题，`repeat` 的意义当场消失。
③ **不参与任何判定**：`bench/mutation_check.py`、`bench/noise_floor.py`、
   `mw/*` 都不读它。读它的只有分析脚本与探针。

## 为什么 `cap` 默认是 `NO_CAP` 且截断必须自陈

上面四处截断的共同点不是「截了」，而是**截了不说**：读的人以为手里是全的。
所以这里留一个 `cap` 参数（测试与极端场景可用），但一旦发生截断，
记录里会写 `truncated: true` —— 让「我手里这份是残的」成为可读事实。
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

#: 不截断。写成常量而不是到处写 `[:0]`，是为了让它能被断言：
#: 「读回来的长度 == 写入的长度」那条判据，靠的就是这个默认值。
NO_CAP = 0

#: 落在 `paths.bench`（`bench_out/`，已被 `.gitignore` 忽略）下的文件名。
DEFAULT_NAME = "answers.jsonl"


def sha256_text(text: str) -> str:
    """答案文本的指纹。用于「读回来的是不是同一份」这件事的可复核比对。"""
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def records_path(bench_dir: str | os.PathLike[str]) -> Path:
    return Path(bench_dir) / DEFAULT_NAME


def make_run_id(*, pid: int | None = None, ts: int | None = None) -> str:
    """与 `run.py` 落 `runs.jsonl` 时**逐字相同**的构造。

    ⛔ 这不是「另一套 id」：两份文件必须能靠 `run_id` 直接 join，
    否则「这次答案属于哪次运行」又只能靠人记 —— 那正是第七十二轮
    给 `runs.jsonl` 加 `run_id` 要解决的问题。
    """
    return f"{os.getpid() if pid is None else int(pid)}@{int(time.time()) if ts is None else int(ts)}"


class AnswerLog:
    """缓冲运行期答案，跑完后一次性追加到 jsonl。

    用法（`run.py`）：

        log = AnswerLog(records_path(bench_dir))
        ...                       # 每题跑完 log.add(task_id, answer)
        log.save(run_id=同一个 run_id, meta={"sig": caliber.signature()})
    """

    def __init__(self, path: str | os.PathLike[str], *, cap: int = NO_CAP) -> None:
        self.path = Path(path)
        self.cap = int(cap)
        self._buf: list[dict[str, Any]] = []

    # ── 收集 ────────────────────────────────────────────────────────────
    def add(self, task_id: str, answer: Any, *, rep: int = 1,
            extra: Mapping[str, Any] | None = None) -> None:
        """记一条。`answer` 为空也算一条 —— 「没产出答案」本身是读数。"""
        text = "" if answer is None else str(answer)
        stored = text if self.cap <= NO_CAP else text[: self.cap]
        row: dict[str, Any] = {
            "kind": "answer",
            "task_id": str(task_id),
            "rep": int(rep),
            "chars": len(stored),
            "sha256": sha256_text(stored),
            # ⛔ 截断必须自陈。上面四处截断真正的毛病是「截了不说」。
            "truncated": stored != text,
            "answer": stored,
        }
        if extra:
            row.update(dict(extra))
        self._buf.append(row)

    def __len__(self) -> int:
        return len(self._buf)

    # ── 落盘 ────────────────────────────────────────────────────────────
    def save(self, *, run_id: str, meta: Mapping[str, Any] | None = None,
             extra_lines: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
        """追加一行 header + 每条答案一行。返回**可打印的**统计。

        空缓冲也写 header：`answers.jsonl` 里「这次运行一条答案都没有」
        与「这个文件根本没被写过」是两件事，后者会让复核者以为是接线坏了。
        """
        if not str(run_id or "").strip():
            raise ValueError("run_id 不能为空：没有它，这些答案属于哪次运行不可判定")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        ts = time.time()
        lines: list[dict[str, Any]] = [{
            "kind": "header", "run_id": str(run_id), "ts": ts,
            "n_answers": len(self._buf),
            "cap": self.cap,
            "meta": dict(meta or {}),
        }]
        # ⛔ 每一条答案行都带上 `run_id` —— 第一版只在 header 上写，
        #    于是 `read(run_id=...)` 过滤出来是**空的**（实测 KeyError），
        #    而「按轮取答案」正是这个文件的主要用法。逐行写才 join 得动。
        lines.extend({**row, "run_id": str(run_id)} for row in self._buf)
        lines.extend({**dict(r), **({} if "run_id" in r else {"run_id": str(run_id)})}
                     for r in extra_lines)
        with self.path.open("a", encoding="utf-8") as f:
            for row in lines:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        total = sum(int(r["chars"]) for r in self._buf)
        return {
            "path": str(self.path),
            "run_id": str(run_id),
            "n_answers": len(self._buf),
            "chars": total,
            "max_chars": max((int(r["chars"]) for r in self._buf), default=0),
            "truncated": sum(1 for r in self._buf if r.get("truncated")),
            "cap": self.cap,
        }

    # ── 读回（分析脚本与探针用） ────────────────────────────────────────
    @staticmethod
    def read(path: str | os.PathLike[str], *, run_id: str | None = None) -> list[dict[str, Any]]:
        """读回所有**答案**行（header 不算）。给了 `run_id` 就只取那一轮。"""
        p = Path(path)
        if not p.exists():
            return []
        want = None if run_id is None else str(run_id)
        out: list[dict[str, Any]] = []
        with p.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue          # 半行（被 kill 在写盘中途）不许带走整个读回
                if row.get("kind") != "answer":
                    continue
                if want is not None and str(row.get("run_id")) != want:
                    continue
                out.append(row)
        return out


def verify_rows(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """对读回来的行做**自洽核对**：`chars` 与正文一致、`sha256` 与正文一致。

    这是给探针用的最小反证装置：任何「写的时候被截断」或「读的时候被加工」
    都会在这里露出来 —— 因为指纹是对着**落盘的正文**算的。
    """
    bad_len = bad_sha = 0
    for r in rows:
        text = str(r.get("answer", ""))
        if int(r.get("chars", -1)) != len(text):
            bad_len += 1
        if str(r.get("sha256", "")) != sha256_text(text):
            bad_sha += 1
    return {"n": len(rows), "bad_len": bad_len, "bad_sha": bad_sha}
