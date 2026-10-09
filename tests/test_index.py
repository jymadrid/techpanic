"""核心算法：结构、因果性、确定性、权重敏感性。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from techpanic import index as idx
from techpanic.config import IndexConfig


def test_compute_produces_expected_columns(close_series, qvix_series):
    d = idx.compute(close_series, qvix_series, IndexConfig())
    for col in ("S", "A", "F", "PI_full", "PI_price", "level_full", "level_price", "source"):
        assert col in d.columns
    assert len(d) > 700


def test_scores_are_bounded(close_series, qvix_series):
    d = idx.compute(close_series, qvix_series, IndexConfig())
    for col in ("S", "A", "F"):
        v = d[col].dropna()
        assert v.min() >= 0.0 and v.max() <= 100.0


def test_missing_qvix_does_not_carry_forward_full_value(close_series, qvix_series):
    """关键回归：EMA 会跳过 NaN 并沿用上一日值，必须把缺值日显式置回 NaN。

    历史缺陷：曾把最新的真实读数显示成上一交易日的旧值。
    """
    qvix = qvix_series.copy()
    qvix.iloc[-5:] = np.nan

    d = idx.compute(close_series, qvix, IndexConfig())
    tail = d.iloc[-5:]
    assert tail["PI_full"].isna().all(), "QVIX 缺失日的完整口径必须是 NaN，不能沿用旧值"
    assert (tail["source"] == idx.SOURCE_PRICE_ONLY).all()
    assert tail["PI_price"].notna().all()


def test_determinism_same_input_same_output(close_series, qvix_series):
    a = idx.compute(close_series, qvix_series, IndexConfig())
    b = idx.compute(close_series, qvix_series, IndexConfig())
    pd.testing.assert_frame_equal(a, b)


def test_price_only_is_normalized_by_remaining_weights(close_series, qvix_series):
    """即时口径 = (0.40S + 0.35A) / 0.75，量纲与完整口径可比。"""
    cfg = IndexConfig()
    d = idx.compute(close_series, qvix_series, cfg)
    expected = (d["S"] * 0.40 + d["A"] * 0.35) / 0.75
    pd.testing.assert_series_equal(d["PI_price_raw"], expected, check_names=False)


def test_weights_change_output(close_series, qvix_series):
    d1 = idx.compute(close_series, qvix_series, IndexConfig())
    d2 = idx.compute(close_series, qvix_series, IndexConfig(weight_s=0.50, weight_a=0.25, weight_f=0.25))
    common = d1["PI_price"].dropna().index.intersection(d2["PI_price"].dropna().index)
    assert not np.allclose(d1.loc[common, "PI_price"], d2.loc[common, "PI_price"])


def test_causal_ranking_unaffected_by_future_data(close_series):
    s1 = idx.pct_rank(close_series.diff(), 60)
    extra = pd.Series(
        [close_series.iloc[-1] * 1.5],
        index=pd.DatetimeIndex([close_series.index[-1] + pd.Timedelta(days=1)]),
    )
    longer = pd.concat([close_series, extra])
    s2 = idx.pct_rank(longer.diff(), 60)
    pd.testing.assert_series_equal(
        s1.dropna(), s2.loc[s1.dropna().index], check_names=False
    )


def test_staleness_counts_trading_days():
    dates = pd.bdate_range("2026-01-01", periods=10)
    assert idx.staleness_trading_days(dates, dates[-1]) == 0
    assert idx.staleness_trading_days(dates, dates[-3]) == 2
    assert idx.staleness_trading_days(dates, None) == 0


def test_latest_helpers_return_last_non_null(close_series, qvix_series):
    qvix = qvix_series.copy()
    qvix.iloc[-3:] = np.nan
    d = idx.compute(close_series, qvix, IndexConfig())
    assert idx.latest_full_date(d) < idx.latest_price_date(d)
