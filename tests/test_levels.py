"""分级锚点：因果性、单调性、边界。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from techpanic import levels


def test_classify_boundaries():
    a = (10.0, 20.0, 30.0, 40.0)
    assert levels.classify(9.9, a) == "平静"
    assert levels.classify(10.0, a) == "常态"
    assert levels.classify(20.0, a) == "警戒"
    assert levels.classify(30.0, a) == "恐慌"
    assert levels.classify(40.0, a) == "极度恐慌"
    assert levels.classify(float("nan"), a) == levels.NO_VALUE


def test_expanding_anchors_are_causal():
    """核心性质：第 t 天的锚点只能由 t 及之前的数据决定。

    做法：把序列尾部接上一段极端的未来值，若前段锚点不变，说明没有前视。
    """
    rs = np.random.RandomState(3)
    base = pd.Series(rs.normal(50, 8, size=300), index=pd.bdate_range("2020-01-01", periods=300))
    table_before = levels.expanding_anchors(base, min_periods=60)

    future_dates = pd.bdate_range(base.index[-1] + pd.Timedelta(days=1), periods=50)
    extended = pd.concat([base, pd.Series([999.0] * 50, index=future_dates)])
    table_after = levels.expanding_anchors(extended, min_periods=60)

    pd.testing.assert_frame_equal(table_before, table_after.loc[table_before.index], atol=1e-9)


def test_full_sample_anchors_do_look_ahead():
    """反证：全样本锚点会随未来数据变化（这就是为什么默认不用它）。"""
    rs = np.random.RandomState(4)
    base = pd.Series(rs.normal(50, 8, size=300), index=pd.bdate_range("2020-01-01", periods=300))
    extended = pd.concat([base, pd.Series([999.0] * 50)])
    a1 = levels.full_sample_anchors(base, min_periods=60)
    a2 = levels.full_sample_anchors(extended, min_periods=60)
    assert not np.allclose(a1, a2)


def test_expanding_anchors_require_min_periods():
    s = pd.Series([1.0, 2.0, 3.0], index=pd.bdate_range("2020-01-01", periods=3))
    table = levels.expanding_anchors(s, min_periods=60)
    assert table.isna().all().all()


def test_causal_levels_matches_expanding_percentile():
    rs = np.random.RandomState(5)
    s = pd.Series(rs.normal(50, 10, size=200), index=pd.bdate_range("2020-01-01", periods=200))
    labels, table = levels.causal_levels(s, "expanding", 60)
    assert len(table) == len(s)
    assert labels.iloc[:59].eq(levels.NO_VALUE).all(), "前 60 天样本不足，不得给出分级"
    assert labels.iloc[60:].isin(levels.LABELS).all()
