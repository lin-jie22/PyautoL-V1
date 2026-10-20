# ★§19 R-b PROVENANCE —— 安装形态（第四十五轮，2026-10-20）

- 交付：pyproject.toml（dependencies=[]、scripts.v1=run:main）+ mw/__main__.py（只转发）。
- 契约 530→532（AZ41/AZ42）；撤回态 105→107（+AZ41/AZ42）。
- 安装实测：/tmp 一次性 venv `pip install .` ⇒ `v1 --selftest` 与
  `python -m mw --selftest`（/tmp 下跑，排除源码目录干扰）全绿。
- R13 全量闸门（r13_rb_full.log）：108 条中 107 直接绿；AS4 多红 AZ42 =
  真实连带（AS4 破坏 selftest 端到端 ⇒ AZ42 动态判据连带），登记 COUPLED
  后选择性复跑 AS4/AZ41/AZ42 全绿、还原后基线 532/0。
- 自踩：AZ41 判据子串笔误（">3.11" in ">=3.11" 恒 False）当场修；
  subprocess 须局部导入（contract_test 顶层无 subprocess）。
- FILES.sha256 —— 指纹
