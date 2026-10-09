"""示例 4：复核某一天的历史读数（可复现）。

运行：
    python examples/04_backtest_one_day.py 2026-09-30

用途：
    - 报告发布后需要引用某一天的数，事后复核；
    - 确认程序在「只用截止到那天为止的数据」时给出什么结果。

注意：as_of 会**截断**数据，因此该日的分位锚点只用到该日为止，
这与当时实时计算的结果一致 —— 这正是因果性设计的意义。
"""

import sys

from techpanic.config import load_config
from techpanic.pipeline import run

day = sys.argv[1] if len(sys.argv) > 1 else "2026-09-30"

cfg = load_config(data_dir="./data", overrides={"as_of": day})
result = run(cfg, say=lambda m: None)

print(f"截断日期：{day}")
print()
for tr in result.targets:
    if tr.frame is None:
        print(f"{tr.target.name}：无数据")
        continue
    print(f"{tr.target.name}（序列末 {tr.latest_date.date()}）")
    if tr.has_full:
        print(f"  A {tr.full_value:.4f}  {tr.full_level}  数据日 {tr.full_date.date()}")
    if tr.has_price:
        print(f"  B {tr.price_value:.4f}  {tr.price_level}  数据日 {tr.price_date.date()}")
