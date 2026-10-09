"""测试公共夹具。

设计原则：**测试不依赖仓库里的任何数据文件**（本项目选择零数据入仓），
所有输入都在 tmp_path 里用固定随机种子现场生成，因此断言可以精确到小数位。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


def make_prices(n: int = 900, seed: int = 20261008, start: str = "2020-01-02") -> pd.DataFrame:
    """确定性价格序列：几何随机游走 + 两个人造暴跌段。"""
    rs = np.random.RandomState(seed)
    dates = pd.bdate_range(start=start, periods=n)
    shocks = rs.normal(0.0, 0.012, size=n)
    shocks[400:404] -= 0.035
    shocks[700:702] -= 0.028
    close = 1000.0 * np.cumprod(1.0 + shocks)
    return pd.DataFrame({"date": dates, "close": close})


def make_qvix(dates, seed: int = 7, scale: float = 22.0) -> pd.DataFrame:
    """确定性 QVIX：与价格无关的均值回复序列，用于验证计算链路。"""
    rs = np.random.RandomState(seed)
    n = len(dates)
    noise = rs.normal(0.0, 1.2, size=n).cumsum()
    base = scale + 3.0 * np.sin(np.arange(n) / 40.0) + noise * 0.4
    return pd.DataFrame({"date": pd.DatetimeIndex(dates), "close": np.abs(base) + 8.0})


@pytest.fixture
def prices_df() -> pd.DataFrame:
    return make_prices()


@pytest.fixture
def close_series(prices_df) -> pd.Series:
    return prices_df.set_index("date")["close"]


@pytest.fixture
def qvix_series(prices_df) -> pd.Series:
    return make_qvix(prices_df["date"]).set_index("date")["close"]


@pytest.fixture
def seeded_data_dir(tmp_path):
    """构造一个带缓存的 data 目录，供离线端到端测试使用。"""
    from techpanic import store

    root = tmp_path / "data"
    idx = root / "cache" / "index_daily"
    qvx = root / "cache" / "qvix"
    idx.mkdir(parents=True)
    qvx.mkdir(parents=True)

    prices = make_prices()
    store.atomic_write_csv(prices, idx / "sh000688.csv")
    store.atomic_write_csv(prices, idx / "sz399006.csv")
    qvix = make_qvix(prices["date"])
    store.atomic_write_csv(qvix, qvx / "kcb.csv")
    store.atomic_write_csv(qvix, qvx / "cyb.csv")
    return root
