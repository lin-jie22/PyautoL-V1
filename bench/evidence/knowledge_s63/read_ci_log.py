#!/usr/bin/env python3
"""把 CI 的**绿勾**翻译成**读数**（第六十轮）。

为什么需要这个文件：`conclusion=success` 只说"退出码是 0"，不说"三档读数相同"。
项目纪律里"绿勾不是读数"对 CI 同样成立 —— 所以这里把日志抓下来，
逐 job 抽 `PASS=` 行与文档核对行，并打印每份日志的字符数。

    GH_TOKEN=... python3 bench/evidence/knowledge_s63/read_ci_log.py 39728371572

⛔ 日志包里的文件名含中文，macOS 自带 `unzip` 会按 CP437 解出乱码并交互式报错
（`write error (disk full?)` 那次就是这样炸的，不是真的磁盘满）⇒ 全程在内存里读 zip。
"""

from __future__ import annotations

import io
import json
import os
import re
import sys
import urllib.request
import zipfile

API = "https://api.github.com/repos/{repo}/actions/runs/{rid}"


def _get(url: str, token: str) -> bytes:
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    })
    with urllib.request.urlopen(req, timeout=120) as r:     # noqa: S310
        return r.read()


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    rid = argv[1]
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        print("❌ 需要 GH_TOKEN")
        return 2
    repo = os.environ.get("REPO", "lin-jie22/PyautoL-V1")

    meta = json.loads(_get(API.format(repo=repo, rid=rid), token))
    print(f"run {rid}: {meta['name']}  head={meta['head_sha'][:8]}  "
          f"{meta['status']}/{meta['conclusion']}")
    print()

    z = zipfile.ZipFile(io.BytesIO(_get(f"{API.format(repo=repo, rid=rid)}/logs", token)))
    want = re.compile(r"PASS=\d+  FAIL=\d+|全部一致（核对 \d+ 处声明|全部通过。|有步骤失败")
    stamp = re.compile(r"^\d{4}-\d\d-\d\dT\S+Z\s?")
    for n in sorted(z.namelist()):
        if not n.endswith(".txt"):
            continue
        job = n.split("/")[0]
        if "七步" not in n and "verify" not in n.lower():
            continue
        txt = z.read(n).decode("utf-8", "replace")
        print(f"── {job} ── 日志 {len(txt)} 字符")
        for line in txt.splitlines():
            s = stamp.sub("", line)
            if want.search(s):
                print("   ", s.strip())
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
