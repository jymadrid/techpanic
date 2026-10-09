"""示例 2：用配置对象自定义，不碰任何配置文件。

运行：
    python examples/02_custom_config.py

这是库用法推荐的姿势：显式构造配置，而不是依赖环境。
"""

from dataclasses import replace

from techpanic.config import load_config
from techpanic.pipeline import run

cfg = load_config(data_dir="./data")

# 1) 换成固定阈值分级：读数可复现、可对外引用，不随新数据漂移
cfg = replace(
    cfg,
    index=replace(
        cfg.index,
        level_anchor="frozen",
        frozen_anchors=(30.0, 42.0, 58.0, 72.0),
    ),
)

# 2) 离线运行，避免这次示例去等 QVIX 下载
cfg = replace(cfg, offline=True)

result = run(cfg, say=lambda m: None)

print(f"锚点算法：{cfg.index.level_anchor}")
print(f"阈值：{cfg.index.frozen_anchors}")
print()
for tr in result.targets:
    if tr.frame is None:
        continue
    line = f"{tr.target.name}: "
    line += "A=无值  " if not tr.has_full else f"A={tr.full_value:.1f}({tr.full_level})  "
    line += "B=无值" if not tr.has_price else f"B={tr.price_value:.1f}({tr.price_level})"
    print(line)
