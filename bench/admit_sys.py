#!/usr/bin/env python3
"""手写套件的准入器（PREREG_v176 §1.2）—— 反证承重，不是又一个「只输出 ✅」的装饰。

## 它回答什么问题

`calibrate()` 证明的是「**尺子**能分辨好坏」；本文件证明的是
「**这套题**考的确实是它声称的东西」：

    A1 oracle_full      参考解必须满分（只 > null 不够 —— 那只证明两极可分）
    A2 null_floor       空答案必须拿不到分
    A3 mid_order        null < partial < oracle 严格（中段有序，不是二值尺子）
    A4 mutants_caught   注册的每个变异体得分 < oracle —— **反真空**：
                        测试若什么都没考，变异体也会满分，A4 当场拒绝这套题
    A5 determinism      同答案复打分逐位相同
    A6 registry_covered 注册表 = 题集；每题 ≥1 变异体、≥1 `wipe_*`
                        （状态/行为抹除）；format=files 另需 ≥1 `flat_*`（格式强制）

fail-open 贯穿到底：**缺任何读数 ⇒ 拒绝**，不许把「没量到」读成「通过」。

## 变异体的手写纪律

⛔ 变异体不自动生成。每个变异体是一句「如果实现退化成这样，测试必须抓到」
的**反证**；自动变异只会生成一堆平凡的语法噪声。
- `wipe_*`：状态/行为抹除（每次调用清空、忽略过期、丢掉过滤条件……）
- `flat_*`：语义等价的**单文件**答案 —— 它本可以得满分，但缺 FILES 格式
  ⇒ 测试必须给 0 分（格式强制是测试承重的，不是打分器特判）

## 它**不**覆盖什么

· 变异体只证明「测试非真空」，不证明「测试完备」—— 抓到 1 个变异体 ≠ 抓到所有 bug。
· A5 只证打分确定性（T=0 可复现），不证模型侧确定性。
· 准入器自身的牙齿在 probe_v176 的 P5（四种坏读数必须全拒）里验，
  本文件不自证（自证要反事实，那是探针的事）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_V1 = Path(__file__).resolve().parent.parent
if str(_V1) not in sys.path:
    sys.path.insert(0, str(_V1))

#: 读数的必需键（fail-open：缺一个就拒）
REQUIRED_KEYS = ("oracle", "null", "partial", "mutants",
                 "oracle_replay", "partial_replay", "format", "kind")

#: 变异体名前缀的受控词表
WIPE_PREFIX = "wipe_"
FLAT_PREFIX = "flat_"

#: 与「满分」比较的容差（浮点判等，⛔ 不用 == 对任意浮点）
_FULL = 1.0
_EPS = 1e-9


# ===========================================================================
# 变异体注册表 —— 手写，逐条是一句反证
# ===========================================================================

#: s01：每次调用清空存储 ⇒ 任何跨调用持久化都必须被测到
_S01_WIPE_STATE = (
    "class KV:\n"
    "    def __init__(self):\n"
    "        self.d = {}\n"
    "    def put(self, k, v, now, ttl=None):\n"
    "        self.d = {}\n"
    "        self.d[k] = (v, None)\n"
    "    def get(self, k, now):\n"
    "        self.d = {}\n"
    "        return None\n"
    "    def keys(self, now):\n"
    "        self.d = {}\n"
    "        return []\n"
    "    def delete(self, k):\n"
    "        self.d = {}\n"
)

#: s01：忽略 ttl ⇒ 过期语义必须被测到（= partial 的已知 bug 形态）
_S01_WIPE_TTL = (
    "class KV:\n"
    "    def __init__(self):\n"
    "        self.d = {}\n"
    "    def put(self, k, v, now, ttl=None):\n"
    "        self.d[k] = v\n"
    "    def get(self, k, now):\n"
    "        return self.d.get(k)\n"
    "    def keys(self, now):\n"
    "        return sorted(self.d)\n"
    "    def delete(self, k):\n"
    "        self.d.pop(k, None)\n"
)

#: s02：每次 hit 清空历史 ⇒ 窗口计数必须被测到
_S02_WIPE_STATE = (
    "class Limiter:\n"
    "    def __init__(self, limit, window):\n"
    "        self.limit = limit\n"
    "        self.window = window\n"
    "        self.hits = {}\n"
    "    def hit(self, key, now):\n"
    "        self.hits = {}\n"
    "        return True\n"
    "    def count(self, key, now):\n"
    "        return 0\n"
)

#: s02：固定窗口 ⇒ 滑窗的边界语义必须被测到（= partial 的已知 bug 形态）
_S02_WIPE_WINDOW = (
    "class Limiter:\n"
    "    def __init__(self, limit, window):\n"
    "        self.limit = limit\n"
    "        self.window = window\n"
    "        self.hits = {}\n"
    "    def hit(self, key, now):\n"
    "        b = now // self.window\n"
    "        ts = [t for t in self.hits.get(key, []) if t // self.window == b]\n"
    "        if len(ts) < self.limit:\n"
    "            self.hits.setdefault(key, []).append(now)\n"
    "            return True\n"
    "        return False\n"
    "    def count(self, key, now):\n"
    "        b = now // self.window\n"
    "        return len([t for t in self.hits.get(key, []) if t // self.window == b])\n"
)

#: s03：读侧全空 ⇒ 「暂存叠加视图 / 持久化」必须被测到
_S03_WIPE_STATE = (
    "class TxError(Exception):\n"
    "    pass\n"
    "class Tx:\n"
    "    def __init__(self):\n"
    "        self.active = False\n"
    "    def begin(self):\n"
    "        if self.active:\n"
    "            raise TxError('already active')\n"
    "        self.active = True\n"
    "    def put(self, k, v):\n"
    "        if not self.active:\n"
    "            raise TxError('no tx')\n"
    "    def delete(self, k):\n"
    "        if not self.active:\n"
    "            raise TxError('no tx')\n"
    "    def get(self, k):\n"
    "        return None\n"
    "    def scan(self):\n"
    "        return []\n"
    "    def commit(self):\n"
    "        if not self.active:\n"
    "            raise TxError('no tx')\n"
    "        self.active = False\n"
    "    def rollback(self):\n"
    "        if not self.active:\n"
    "            raise TxError('no tx')\n"
    "        self.active = False\n"
)

#: s03：rollback 只撤一步 ⇒ 「整事务回滚」必须被测到（= partial 的已知 bug 形态）
_S03_WIPE_ROLLBACK = (
    "class TxError(Exception):\n"
    "    pass\n"
    "class Tx:\n"
    "    def __init__(self):\n"
    "        self.data = {}\n"
    "        self.active = False\n"
    "        self.staged = []\n"
    "    def begin(self):\n"
    "        if self.active:\n"
    "            raise TxError('already active')\n"
    "        self.active = True\n"
    "        self.staged = []\n"
    "    def put(self, k, v):\n"
    "        if not self.active:\n"
    "            raise TxError('no tx')\n"
    "        self.staged.append(('put', k, v))\n"
    "    def delete(self, k):\n"
    "        if not self.active:\n"
    "            raise TxError('no tx')\n"
    "        self.staged.append(('del', k, None))\n"
    "    def get(self, k):\n"
    "        v = self.data.get(k)\n"
    "        for op, kk, vv in self.staged:\n"
    "            if kk == k:\n"
    "                v = vv if op == 'put' else None\n"
    "        return v\n"
    "    def scan(self):\n"
    "        keys = set(self.data) | {kk for _, kk, _ in self.staged}\n"
    "        return [k for k in sorted(keys) if self.get(k) is not None]\n"
    "    def commit(self):\n"
    "        if not self.active:\n"
    "            raise TxError('no tx')\n"
    "        for op, k, v in self.staged:\n"
    "            if op == 'put':\n"
    "                self.data[k] = v\n"
    "            else:\n"
    "                self.data.pop(k, None)\n"
    "        self.staged = []\n"
    "        self.active = False\n"
    "    def rollback(self):\n"
    "        if not self.active:\n"
    "            raise TxError('no tx')\n"
    "        if self.staged:\n"
    "            self.staged.pop()\n"
    "        self.active = False\n"
)

#: m01：丢 min_value 过滤 ⇒ 条件组合必须被测到（= partial 的已知 bug 形态）
_M01_WIPE_MINVALUE = (
    "FILES = {\n"
    "    'models.py': 'class Record:\\n    def __init__(self, name, value, tags=()):\\n"
    "        self.name = name\\n        self.value = value\\n        self.tags = tuple(tags)\\n',\n"
    "    'filter.py': 'from models import Record\\n\\ndef select(records, tag=None, min_value=None):\\n"
    "    return [r for r in records if tag is None or tag in r.tags]\\n',\n"
    "    'stats.py': 'from filter import select\\n\\ndef summary(records, tag=None, min_value=None):\\n"
    "    picked = select(records, tag=tag, min_value=min_value)\\n"
    "    if not picked:\\n        return {\"count\": 0, \"total\": 0, \"mean\": 0.0}\\n"
    "    total = sum(r.value for r in picked)\\n"
    "    return {\"count\": len(picked), \"total\": total, \"mean\": total / len(picked)}\\n',\n"
    "}\n"
)

#: m01：语义等价的单文件答案 ⇒ 缺 FILES 格式必须拿 0（格式强制）
_M01_FLAT_ALL = (
    "class Record:\n"
    "    def __init__(self, name, value, tags=()):\n"
    "        self.name = name\n"
    "        self.value = value\n"
    "        self.tags = tuple(tags)\n"
    "\n"
    "def select(records, tag=None, min_value=None):\n"
    "    out = []\n"
    "    for r in records:\n"
    "        if tag is not None and tag not in r.tags:\n"
    "            continue\n"
    "        if min_value is not None and r.value < min_value:\n"
    "            continue\n"
    "        out.append(r)\n"
    "    return out\n"
    "\n"
    "def summary(records, tag=None, min_value=None):\n"
    "    picked = select(records, tag=tag, min_value=min_value)\n"
    "    if not picked:\n"
    "        return {'count': 0, 'total': 0, 'mean': 0.0}\n"
    "    total = sum(r.value for r in picked)\n"
    "    return {'count': len(picked), 'total': total, 'mean': total / len(picked)}\n"
)

#: m02：不落盘存储 ⇒ 跨操作持久化必须被测到
_M02_WIPE_STATE = (
    "FILES = {\n"
    "    'node.py': 'class File:\\n    def __init__(self, name, content=\"\"):\\n"
    "        self.name = name\\n        self.content = content\\n\\nclass Dir:\\n"
    "    def __init__(self, name):\\n        self.name = name\\n        self.children = {}\\n',\n"
    "    'vfs.py': 'from node import File, Dir\\n\\nclass VFS:\\n    def __init__(self):\\n"
    "        self.root = Dir(\"\")\\n    def mkdir(self, path):\\n        pass\\n"
    "    def write(self, path, content):\\n        pass\\n    def read(self, path):\\n"
    "        return \"\"\\n    def ls(self, path=\"\"):\\n        return []\\n"
    "    def rm(self, path):\\n        pass\\n',\n"
    "}\n"
)

#: m02：write 不自动建中间目录 ⇒ 目录隐式创建语义必须被测到
_M02_WIPE_AUTODIR = (
    "FILES = {\n"
    "    'node.py': 'class File:\\n    def __init__(self, name, content=\"\"):\\n"
    "        self.name = name\\n        self.content = content\\n\\nclass Dir:\\n"
    "    def __init__(self, name):\\n        self.name = name\\n        self.children = {}\\n',\n"
    "    'vfs.py': 'from node import File, Dir\\n\\nclass VFS:\\n    def __init__(self):\\n"
    "        self.root = Dir(\"\")\\n    def _walk(self, path):\\n"
    "        parts = [p for p in path.split(\"/\") if p]\\n        cur = self.root\\n"
    "        for name in parts[:-1]:\\n            if name not in cur.children:\\n"
    "                raise KeyError(path)\\n            cur = cur.children[name]\\n"
    "        return cur, (parts[-1] if parts else \"\")\\n    def mkdir(self, path):\\n"
    "        parent, name = self._walk(path)\\n        if name and name not in parent.children:\\n"
    "            parent.children[name] = Dir(name)\\n    def write(self, path, content):\\n"
    "        parent, name = self._walk(path)\\n        parent.children[name] = File(name, content)\\n"
    "    def read(self, path):\\n        parent, name = self._walk(path)\\n"
    "        node = parent.children.get(name)\\n        if not isinstance(node, File):\\n"
    "            raise KeyError(path)\\n        return node.content\\n    def ls(self, path=\"\"):\\n"
    "        parts = [p for p in path.split(\"/\") if p]\\n        cur = self.root if not parts else self._walk(path)[0].children.get(self._walk(path)[1])\\n"
    "        return sorted(cur.children)\\n    def rm(self, path):\\n"
    "        parent, name = self._walk(path)\\n        del parent.children[name]\\n',\n"
    "}\n"
)

#: m02：语义近似的单文件答案 ⇒ 缺 FILES 格式必须拿 0
_M02_FLAT_ALL = (
    "class File:\n"
    "    def __init__(self, name, content=''):\n"
    "        self.name = name\n"
    "        self.content = content\n"
    "\n"
    "\n"
    "class Dir:\n"
    "    def __init__(self, name):\n"
    "        self.name = name\n"
    "        self.children = {}\n"
    "\n"
    "\n"
    "class VFS:\n"
    "    def __init__(self):\n"
    "        self.root = Dir('')\n"
    "\n"
    "    def mkdir(self, path):\n"
    "        pass\n"
    "\n"
    "    def write(self, path, content):\n"
    "        pass\n"
    "\n"
    "    def read(self, path):\n"
    "        return ''\n"
    "\n"
    "    def ls(self, path=''):\n"
    "        return []\n"
    "\n"
    "    def rm(self, path):\n"
    "        pass\n"
)

#: m03：LRU 不存东西 ⇒ 容量/淘汰/提升必须被测到
_M03_WIPE_STATE = (
    "FILES = {\n"
    "    'lru.py': 'class LRU:\\n    def __init__(self, capacity):\\n"
    "        self.capacity = capacity\\n        self.data = {}\\n    def get(self, k):\\n"
    "        return None\\n    def put(self, k, v):\\n        pass\\n    def keys(self):\\n"
    "        return []\\n',\n"
    "    'tiered.py': 'from lru import LRU\\n\\nclass Tiered:\\n"
    "    def __init__(self, l1_capacity):\\n        self.l1 = LRU(l1_capacity)\\n"
    "        self.l2 = {}\\n    def put(self, k, v):\\n        pass\\n    def get(self, k):\\n"
    "        return None\\n',\n"
    "}\n"
)

#: m03：L2 命中不提升进 L1 ⇒ 提升语义必须被测到
_M03_WIPE_PROMOTE = (
    "FILES = {\n"
    "    'lru.py': 'class LRU:\\n    def __init__(self, capacity):\\n"
    "        self.capacity = capacity\\n        self.data = {}\\n    def get(self, k):\\n"
    "        if k not in self.data:\\n            return None\\n        v = self.data.pop(k)\\n"
    "        self.data[k] = v\\n        return v\\n    def put(self, k, v):\\n"
    "        if k in self.data:\\n            self.data.pop(k)\\n"
    "        elif len(self.data) >= self.capacity:\\n            oldest = next(iter(self.data))\\n"
    "            del self.data[oldest]\\n        self.data[k] = v\\n    def keys(self):\\n"
    "        return list(self.data)\\n',\n"
    "    'tiered.py': 'from lru import LRU\\n\\nclass Tiered:\\n"
    "    def __init__(self, l1_capacity):\\n        self.l1 = LRU(l1_capacity)\\n"
    "        self.l2 = {}\\n    def put(self, k, v):\\n        self.l2[k] = v\\n"
    "        self.l1.put(k, v)\\n    def get(self, k):\\n        if k in self.l2:\\n"
    "            return self.l2[k]\\n        return self.l1.get(k)\\n',\n"
    "}\n"
)

#: m03：语义近似的单文件答案 ⇒ 缺 FILES 格式必须拿 0
_M03_FLAT_ALL = (
    "class LRU:\n"
    "    def __init__(self, capacity):\n"
    "        self.capacity = capacity\n"
    "        self.data = {}\n"
    "    def get(self, k):\n"
    "        return None\n"
    "    def put(self, k, v):\n"
    "        pass\n"
    "    def keys(self):\n"
    "        return []\n"
    "\n"
    "\n"
    "class Tiered:\n"
    "    def __init__(self, l1_capacity):\n"
    "        self.l1 = LRU(l1_capacity)\n"
    "        self.l2 = {}\n"
    "    def put(self, k, v):\n"
    "        pass\n"
    "    def get(self, k):\n"
    "        return None\n"
)

#: 注册表：{task_id: [(变异体名, 答案源码)]}。⛔ 手写，禁止自动生成。
MUTANTS: dict[str, list[tuple[str, str]]] = {
    "s01_kv_expiry": [
        ("wipe_state", _S01_WIPE_STATE),
        ("wipe_ttl", _S01_WIPE_TTL),
    ],
    "s02_limiter": [
        ("wipe_state", _S02_WIPE_STATE),
        ("wipe_window", _S02_WIPE_WINDOW),
    ],
    "s03_tx_journal": [
        ("wipe_state", _S03_WIPE_STATE),
        ("wipe_rollback", _S03_WIPE_ROLLBACK),
    ],
    "m01_records": [
        ("wipe_minvalue", _M01_WIPE_MINVALUE),
        ("flat_all", _M01_FLAT_ALL),
    ],
    "m02_vfs": [
        ("wipe_state", _M02_WIPE_STATE),
        ("wipe_autodir", _M02_WIPE_AUTODIR),
        ("flat_all", _M02_FLAT_ALL),
    ],
    "m03_tiered": [
        ("wipe_state", _M03_WIPE_STATE),
        ("wipe_promote", _M03_WIPE_PROMOTE),
        ("flat_all", _M03_FLAT_ALL),
    ],
}


# ===========================================================================
# verdict —— 纯函数，读数进、裁决出。⛔ 无 I/O、无全局态。
# ===========================================================================

def verdict(readings: dict) -> dict:
    """准入裁决。输入 `{task_id: 读数}`，输出 `{"ok": bool, "failed": [...]}`。

    读数的每个条目需要键：`oracle` `null` `partial` `mutants`（dict 名字→分）
    `oracle_replay` `partial_replay` `format` `kind`。
    fail-open：缺键、类型不对、注册表与题集不一致 ⇒ 一律进 `failed`。
    """
    failed: list[dict] = []

    if not isinstance(readings, dict):
        return {"ok": False, "failed": [{"task": "<suite>", "check": "SHAPE",
                                         "why": f"readings 不是对象: {type(readings).__name__}"}]}

    tids = set(readings)
    reg = set(MUTANTS)
    if tids != reg:
        failed.append({"task": "<suite>", "check": "A6",
                       "why": f"注册表与题集不一致 missing={sorted(reg - tids)} "
                              f"extra={sorted(tids - reg)}"})

    for tid in sorted(tids):
        r = readings.get(tid)
        if not isinstance(r, dict):
            failed.append({"task": tid, "check": "SHAPE", "why": "读数不是对象"})
            continue

        # ---- fail-open：必需键 + 类型 ----
        missing = [k for k in REQUIRED_KEYS if k not in r]
        if missing:
            failed.append({"task": tid, "check": "FAIL_OPEN",
                           "why": f"缺读数 {missing}"})
            continue

        nums_ok = all(isinstance(r.get(k), (int, float)) and not isinstance(r.get(k), bool)
                      for k in ("oracle", "null", "partial"))
        if not nums_ok or not isinstance(r.get("mutants"), dict):
            failed.append({"task": tid, "check": "FAIL_OPEN",
                           "why": "读数类型不对（分数须数值、mutants 须对象）"})
            continue
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                   for v in r["mutants"].values()):
            failed.append({"task": tid, "check": "FAIL_OPEN",
                           "why": "mutants 分数类型不对"})
            continue

        oracle, null, partial = float(r["oracle"]), float(r["null"]), float(r["partial"])

        # ---- A1 oracle_full ----
        if abs(oracle - _FULL) > _EPS:
            failed.append({"task": tid, "check": "A1",
                           "why": f"oracle={oracle:.6f} 不是满分 1.0"})

        # ---- A2 null_floor ----
        if abs(null - 0.0) > _EPS:
            failed.append({"task": tid, "check": "A2",
                           "why": f"null={null:.6f} 不是 0.0"})

        # ---- A3 mid_order（严格） ----
        if not (null < partial < oracle):
            failed.append({"task": tid, "check": "A3",
                           "why": f"中段无序 null={null:.6f} partial={partial:.6f} "
                                  f"oracle={oracle:.6f}"})

        # ---- A4 mutants_caught ----
        reg_names = [n for n, _ in MUTANTS.get(tid, [])]
        muts = r["mutants"]
        for name in reg_names:
            if name not in muts:
                failed.append({"task": tid, "check": "A4",
                               "why": f"变异体 {name} 缺读数（fail-open 拒）"})
            elif float(muts[name]) >= oracle - _EPS:
                failed.append({"task": tid, "check": "A4",
                               "why": f"变异体 {name} 未被抓到：{float(muts[name]):.6f} "
                                      f">= oracle {oracle:.6f}"})
        for name in sorted(muts):
            if name not in reg_names:
                failed.append({"task": tid, "check": "A4",
                               "why": f"未注册的变异体读数 {name}"})

        # ---- A5 determinism ----
        ro, rp = r.get("oracle_replay"), r.get("partial_replay")
        if not isinstance(ro, (int, float)) or not isinstance(rp, (int, float)) \
                or isinstance(ro, bool) or isinstance(rp, bool):
            failed.append({"task": tid, "check": "A5",
                           "why": f"复打分读数类型不对 oracle_replay={ro!r} "
                                  f"partial_replay={rp!r}"})
        elif abs(float(ro) - oracle) > _EPS or abs(float(rp) - partial) > _EPS:
            failed.append({"task": tid, "check": "A5",
                           "why": f"复打分不一致 oracle {oracle:.6f}→{float(ro):.6f} "
                                  f"partial {partial:.6f}→{float(rp):.6f}"})

        # ---- A6 family 面 ----
        if not any(n.startswith(WIPE_PREFIX) for n in reg_names):
            failed.append({"task": tid, "check": "A6",
                           "why": "缺 wipe_* 变异体（状态/行为抹除反证）"})
        if r.get("format") == "files" and not any(n.startswith(FLAT_PREFIX) for n in reg_names):
            failed.append({"task": tid, "check": "A6",
                           "why": "format=files 缺 flat_* 变异体（格式强制反证）"})

    return {"ok": not failed, "failed": failed}


# ===========================================================================
# admit —— 跑真打分器攒读数（每个分数走 ExternalScorer 子进程协议）
# ===========================================================================

def _score(scorer, task: dict, answer: str) -> float:
    return scorer.score(task, answer).score


def admit(suite_path: str | Path,
          scorer_path: str | Path | None = None) -> dict:
    """对整套题跑准入。返回 `{"ok", "failed", "readings"}`（readings 可入档）。

    打分器默认 `bench/scorers/code_exec.py`（与跨领域冒烟同一把尺子）。
    """
    from mw.bench import ExternalScorer, Suite

    sp = Path(scorer_path) if scorer_path else _V1 / "bench" / "scorers" / "code_exec.py"
    scorer = ExternalScorer([sys.executable, str(sp)], source=sp)
    suite = Suite.load(suite_path)

    readings: dict[str, dict] = {}
    for t in suite.tasks:
        d = t.as_dict()
        fmt = dict(t.meta).get("format", "single")
        kind = dict(t.meta).get("kind", "pure")
        o = _score(scorer, d, t.oracle)
        n = _score(scorer, d, t.null)
        p = _score(scorer, d, t.partial)
        muts = {name: _score(scorer, d, src) for name, src in MUTANTS.get(t.id, [])}
        ro = _score(scorer, d, t.oracle)          # 复打分（A5）
        rp = _score(scorer, d, t.partial)
        readings[t.id] = {
            "oracle": o, "null": n, "partial": p,
            "mutants": muts,
            "oracle_replay": ro, "partial_replay": rp,
            "format": fmt, "kind": kind,
        }

    v = verdict(readings)
    return {"ok": v["ok"], "failed": v["failed"], "readings": readings}


def main() -> int:
    suite = sys.argv[1] if len(sys.argv) > 1 else str(_V1 / "bench" / "tasks" / "sys_small.jsonl")
    res = admit(suite)
    print(json.dumps({"ok": res["ok"], "failed": res["failed"]},
                     ensure_ascii=False, indent=2))
    for tid, r in sorted(res["readings"].items()):
        muts = "｜".join(f"{k}={v:.2f}" for k, v in sorted(r["mutants"].items()))
        print(f"  {tid}: oracle={r['oracle']:.2f} partial={r['partial']:.2f} "
              f"null={r['null']:.2f}｜{muts}")
    print(f"准入 {'✅ 通过' if res['ok'] else '❌ 拒绝'}")
    return 0 if res["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
