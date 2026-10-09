"""示例 1：三行代码拿到今天的读数（库用法）。

运行：
    python examples/01_hello_pi.py
"""

from techpanic.config import load_config
from techpanic.pipeline import run

cfg = load_config()  # 默认 ./data，全部参数走默认值
result = run(cfg, say=print)

print()
for tr in result.targets:
    if tr.frame is None:
        continue
    print(f"{tr.target.name}  最新交易日 {tr.latest_date.date()}")
    if tr.has_full:
        print(f"  A 完整口径 {tr.full_value:.1f}  {tr.full_level}")
    if tr.has_price:
        print(f"  B 即时口径 {tr.price_value:.1f}  {tr.price_level}  方向 {tr.direction}")
print()
print("退出码：", result.exit_code)
