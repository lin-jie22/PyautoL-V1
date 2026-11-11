#!/usr/bin/env python3
"""V84 臂的**种子记忆库** —— 4 条人写经验 + 8 条同量级无关事实。

⛔ 4 条经验与 `bench/probe_mem_channel.py` / §152 的 `RULES` **逐字相同**：
   换一个字，两轮读数就不可比（V83 的池子是本轮的基线）。

用法：python3 bench/evidence/v6_s173/make_seed.py /tmp/v84/seed
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from mw.memory import MemoryStore  # noqa: E402

RULES = {
    "k01_diff_tree":
        "递归比较两份嵌套结构时，先把差异路径按层级用点号连接，再让某一侧缺失的值留空",
    "k02_encode_cookie":
        "拼接键值对之前，先确认多个项之间用分号紧跟一个空格连接，值里的特殊符号要逐字符转义",
    "k03_unflatten":
        "把带下标的扁平键还原成列表时，先取出现过的最大下标加一定长度，空位留空值而不是跳过",
    "k04_to_ini":
        "生成分节文本时，先确认每行末尾都要带换行，包括最后一行；空结构返回空串",
}
DISTRACTORS = [
    "处理金额时，先做四舍五入再格式化，避免累积误差被显示成两个不同的值",
    "遍历目录时，先判断是否为符号链接，避免沿着链接绕回父目录造成死循环",
    "解析日期之前，先确认时区信息是否存在，缺失时按本地时区处理并记录下来",
    "写日志轮转时，先按大小切分再按天数清理，避免同时改动同一个文件句柄",
    "合并两个有序序列时，先比较当前元素再决定取哪一边，相等时取左边以保持稳定",
    "读取配置文件前，先判断编码是否带签名，有签名时先剥掉再交给解析器",
    "统计词频之前，先统一大小写并去掉标点，否则同一个词会被算成两条记录",
    "分页查询时，先按唯一键排序再取偏移，避免翻页过程中出现重复或遗漏的结果",
]


def main() -> int:
    d = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/v84/seed")
    d.mkdir(parents=True, exist_ok=True)
    db = d / "memory.db"
    if db.exists():
        db.unlink()
    m = MemoryStore(db)
    for text in RULES.values():
        m.commit_fact(text, source="abstract", confidence=0.6)
    for text in DISTRACTORS:
        m.commit_fact(text, source="abstract", confidence=0.5)
    st = m.stats()
    m.close()
    print(f"seed={db} facts={st['facts']} by_source={st['by_source']}")
    assert st["facts"] == 12, st
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
