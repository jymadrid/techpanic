"""独立的算术 oracle，不调用被验算实现的分位或滚动函数。"""

import numpy as np
import pandas as pd
from techpanic import index
from techpanic.config import IndexConfig


def rank(values, pos, minimum):
    history = np.asarray(values[: pos + 1])
    history = history[np.isfinite(history)]
    value = values[pos]
    if len(history) < minimum or not np.isfinite(value):
        return np.nan
    # pandas rank(method=average) including current observation.
    return 100 * ((history < value).sum() + ((history == value).sum() + 1) / 2) / len(history)


def test_independent_arithmetic_reference():
    n = 240
    returns = 0.012 * np.sin(np.arange(n) * 1.37) + 0.002 * np.cos(np.arange(n) * 0.31)
    close = pd.Series(100 * np.cumprod(1 + returns), index=pd.bdate_range("2020-01-01", periods=n))
    qvix = pd.Series(25 + np.sin(np.arange(n) * 0.1), index=close.index)
    cfg = IndexConfig(z_min_periods=10)
    actual = index.compute(close, qvix, cfg)
    r = np.r_[np.nan, close.to_numpy()[1:] / close.to_numpy()[:-1] - 1]
    rv20 = np.full(n, np.nan)
    rv5 = rv20.copy()
    semi = rv20.copy()
    doup = rv20.copy()
    for i in range(n):
        for w, out in [(20, rv20), (5, rv5)]:
            x = r[max(0, i - w + 1) : i + 1]
            if len(x) == w and np.isfinite(x).all():
                out[i] = np.std(x, ddof=1) * np.sqrt(252) * 100
        x = r[max(0, i - 19) : i + 1] * 100
        if len(x) == 20 and np.isfinite(x).all():
            neg = np.minimum(x, 0)
            pos = np.maximum(x, 0)
            semi[i] = np.var(neg, ddof=1) / np.var(x, ddof=1)
            doup[i] = np.std(neg, ddof=1) / np.std(pos, ddof=1)
    drv = np.r_[np.nan, np.diff(rv20)]
    ratio = rv5 / rv20 - 1
    support = np.flatnonzero(
        np.isfinite(drv) & np.isfinite(ratio) & np.isfinite(semi) & np.isfinite(doup)
    )
    raw = []
    price = []
    smooth = []
    psmooth = []
    for j, i in enumerate(support):
        s = (rank(drv[support], j, 10) + rank(ratio[support], j, 10)) / 2
        a = (rank(semi[support], j, 10) + rank(doup[support], j, 10)) / 2
        f = rank(qvix.to_numpy()[support], j, 10)
        raw.append(0.4 * s + 0.35 * a + 0.25 * f)
        price.append((0.4 * s + 0.35 * a) / 0.75)
        smooth.append(
            raw[-1]
            if not smooth or not np.isfinite(smooth[-1])
            else 0.5 * raw[-1] + 0.5 * smooth[-1]
        )
        psmooth.append(
            price[-1]
            if not psmooth or not np.isfinite(psmooth[-1])
            else 0.5 * price[-1] + 0.5 * psmooth[-1]
        )
    np.testing.assert_allclose(actual["PI_full"].to_numpy(), smooth, atol=1e-6, equal_nan=True)
    np.testing.assert_allclose(actual["PI_price"].to_numpy(), psmooth, atol=1e-6, equal_nan=True)


def test_whole_pipeline_prefix_invariance(close_series, qvix_series):
    short = index.compute(close_series.iloc[:500], qvix_series.iloc[:500], IndexConfig())
    long = index.compute(close_series, qvix_series, IndexConfig())
    pd.testing.assert_frame_equal(short, long.loc[short.index])


def test_original_five_rows_not_filtered(close_series):
    cfg = IndexConfig(rv_window=2, rv_short=2, z_min_periods=2)
    prices = close_series.iloc[:200].copy()
    prices.iloc[80] = np.nan
    frame = index.compute(prices, None, cfg)
    expected = (prices / prices.shift(2) - 1) * 100
    pd.testing.assert_series_equal(frame["ret5"], expected.reindex(frame.index), check_names=False)
    assert not np.isinf(frame.select_dtypes("number")).any().any()


def test_gap_detector_returns_actual_gaps():
    assert index.detect_cache_gaps(pd.to_datetime(["2026-09-30", "2026-10-08"])) == [
        ("2026-09-30", "2026-10-08", 8)
    ]
