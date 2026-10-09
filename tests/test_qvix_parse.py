"""QVIX 宽表解析：结构断言与交叉校验（三道防线里的核心两道）。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from techpanic.errors import DataQualityError
from techpanic.fetch import qvix as qv


def _wide(
    rows: int = 400,
    cols: int = 90,
    value: float = 25.0,
    key: str = "kcb",
    *,
    dense_ohlc: bool = True,
) -> str:
    """构造一张合成宽表。

    ## 位置学（很容易搞错，务必看清）

    上游文件**没有表头行**，每行第 0 个字段就是日期。因此：

        parts = line.split(",")
        parts[0]      = 日期
        parts[k]      = 上游第 k 个数据格   (k >= 1)

    代码里的 QVIX_OHLC[key] 用的正是这个 parts 位置（= akshare 的 iloc 列号）。
    所以构造一行时，想写第 col 个数据格，就要写到 cells[col]（cells[0] 是日期）。

    cols = 数据格数（不含日期）；生成的 parts 长度为 cols + 1。

    ## 稀疏性

    除目标品种的四列以外全部填 "#NUM!"，以复现上游「数值占比只有 ~28%」的特征。
    真实上游每个品种的**四列都是有值的**，只有别的品种的格子才稀疏 ——
    所以默认 dense_ohlc=True 把四列都填上真实形态的开/高/低/收。
    若只填收盘价一列，结构不变量拿不到成组四元组，测的就不是真实数据形态。

    dense_ohlc=False 保留旧的"仅收盘价"行为，用于稀疏场景。
    """
    parts_header = [""] + [str(i) for i in range(1, cols + 1)]
    out = [",".join(parts_header)]
    o, h, low, c = qv.QVIX_OHLC[key]
    for i in range(rows):
        day = pd.Timestamp("2024-01-01") + pd.Timedelta(days=i)
        cells = ["#NUM!"] * (cols + 1)  # cells[0] = 日期
        cells[0] = f"{day.year}/{day.month}/{day.day}"
        base = value + i * 0.01
        if dense_ohlc:
            for col, val in ((o, base - 0.3), (h, base + 0.3), (low, base - 0.05), (c, base)):
                if 1 <= col <= cols:
                    cells[col] = f"{val:.4f}"
        elif 1 <= c <= cols:
            cells[c] = f"{base:.4f}"
        out.append(",".join(cells))
    return "\n".join(out)
def test_parse_wide_rejects_missing_columns():
    """上游删列后必须显式拒绝，而不是静默取到错误的列。"""
    text = _wide(cols=20)  # 目标列 85 根本不存在
    with pytest.raises(DataQualityError):
        qv.parse_wide(text, "kcb")


def test_assert_structure_accepts_sparse_wide_table():
    """上游大量单元格是 #NUM!，数值占比只有 ~28%，这**不是**取错列的信号。"""
    text = _wide(rows=400)
    df = qv.parse_wide(text, "kcb")
    qv.assert_structure(text, "kcb", df)  # 不应抛异常


def test_assert_structure_rejects_implausible_median():
    text = _wide(rows=400, value=900.0)
    df = qv.parse_wide(text, "kcb")
    with pytest.raises(DataQualityError):
        qv.assert_structure(text, "kcb", df)


def test_assert_structure_rejects_too_few_rows():
    text = _wide(rows=100)
    df = qv.parse_wide(text, "kcb")
    with pytest.raises(DataQualityError):
        qv.assert_structure(text, "kcb", df)


def _series(values, start="2023-01-02"):
    return pd.DataFrame({"date": pd.bdate_range(start, periods=len(values)), "close": values})


def test_cross_check_cache_detects_level_shift():
    """整体量级取错（读到了波动水平完全不同的相邻品种）必须被拦住。"""
    rs = np.random.RandomState(11)
    n = 500
    base = np.abs(rs.normal(25, 3, n)) + 10
    with pytest.raises(DataQualityError):
        qv.cross_check_cache(_series(base * 2.0), _series(base), "kcb")


def test_cross_check_cache_detects_unrelated_series():
    """最危险的一类故障：取到走势完全无关的序列（水平却可能接近）。"""
    rs = np.random.RandomState(12)
    n = 500
    a = 25 + rs.normal(0, 4, n)
    b = 25 + np.sin(np.arange(n) / 7.0) * 3
    with pytest.raises(DataQualityError):
        qv.cross_check_cache(_series(a), _series(b), "kcb")


def test_cross_check_cache_tolerates_upstream_recomputation():
    """**真实上游行为**：每次请求都会重算历史值，重叠日中位偏差 1%~2%，
    且日度变化相关系数会掉到 0.5~0.7。这**不是**取错列，必须放行。

    参数按实测复现：平稳水平约 30、日度波动约 0.5，加约 2% 的独立修订。
    """
    rs = np.random.RandomState(13)
    n = 800
    level = 30 + np.cumsum(rs.normal(0, 0.5, n))
    cached = level.copy()
    fresh = level * (1.0 + rs.normal(-0.02, 0.02, n))  # 系统性略低 + 逐点修订
    qv.cross_check_cache(_series(fresh), _series(cached), "kcb")


def test_cross_check_cache_tolerates_flat_and_revised_series():
    """极端情况：原序列几乎不动（典型日度波动接近 0），此时用绝对上限兜底。"""
    rs = np.random.RandomState(14)
    n = 300
    cached = np.full(n, 20.0) + np.arange(n) * 0.001
    fresh = cached + rs.normal(0, 0.01, n)
    qv.cross_check_cache(_series(fresh), _series(cached), "kcb")


def test_cross_check_cache_passes_on_identical_data():
    text = _wide(rows=400)
    fresh = qv.parse_wide(text, "kcb")
    qv.cross_check_cache(fresh, fresh.copy(), "kcb")


def test_cross_check_skips_when_too_little_overlap():
    text = _wide(rows=400)
    fresh = qv.parse_wide(text, "kcb")
    small = fresh.iloc[:10].copy()
    qv.cross_check_cache(fresh, small, "kcb")  # 重叠不足，静默跳过


def test_read_cache_handles_bom(tmp_path):
    p = tmp_path / "kcb.csv"
    pd.DataFrame({"date": ["2026-09-30"], "close": [34.57]}).to_csv(p, index=False, encoding="utf-8-sig")
    df = qv.read_cache(p)
    assert df is not None and list(df.columns) == ["date", "close"]


def test_zero_and_negative_close_are_dropped(tmp_path):
    """上游用 0 表示缺失；波动率不可能为负。两者都必须从样本里剔除。"""
    p = tmp_path / "kcb.csv"
    pd.DataFrame(
        {"date": ["2026-09-28", "2026-09-29", "2026-09-30"], "close": [-1.0, 0.0, 34.57]}
    ).to_csv(p, index=False, encoding="utf-8-sig")
    df = qv.read_cache(p)
    assert df is not None
    assert len(df) == 1
    assert float(df["close"].iloc[0]) == pytest.approx(34.57)
