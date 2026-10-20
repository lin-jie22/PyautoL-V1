"""`python -m mw` 入口（§19 R-b）。

单一事实源仍是 run.py 的 main()：本文件只转发，不复制任何逻辑
（复制 = 两套 CLI 真相，迟早漂移）。

双场景成立性（DESIGN §19 R-b）：
  · 源码目录：`python -m mw` 时 cwd 在 sys.path ⇒ `run` 可 import；
  · 已安装环境：run.py 作为 py-module 装进 site-packages ⇒ 同样可 import。
"""
import sys

from run import main

if __name__ == "__main__":
    sys.exit(main())
