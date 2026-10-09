"""techpanic —— 科技板块恐慌指数 PI。

一条命令，得到今天 A 股科技板块的恐慌读数；数据全部来自公开免费源，
不需要任何账号或 API Key。

典型用法::

    python -m techpanic              # 抓取数据并打印今日读数
    python -m techpanic --offline    # 只用本地缓存，零 HTTP 请求

免责声明：本工具为研究/观测级指标，不是官方波动率指数，不预测涨跌方向，
不构成任何投资建议。详见项目根目录 DISCLAIMER.md。
"""

from __future__ import annotations

__all__ = ["__version__"]

try:
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as _version

    try:
        __version__ = _version("techpanic")
    except PackageNotFoundError:
        __version__ = "1.0.0"
except Exception:  # noqa: BLE001
    __version__ = "1.0.0"
