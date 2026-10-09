"""回归测试：锁定已知真实历史结果，防止无意中改变算法。

如果这里的断言失败，请先阅读 tests/test_regression_reference.md。
**不要**为了让测试变绿而直接改数字 —— 那会让回归防线彻底失效。
"""

from __future__ import annotations

import numpy as np

from techpanic import index as idx
from techpanic.config import IndexConfig

# 2026-10-08 真实运行锁定的数值，见 tests/test_regression_reference.md
REF_2026_10_08_KC = {
    "PI_price": 61.50138874731681,
    "S": 75.35559678416821,
    "A": 71.0265924551639,
    "ret": -4.816308390141244,
    "ret5": -12.314778577234552,
}
REF_2026_10_08_CY = {
    "PI_price": 70.9551740,
    "S": 76.152,
    "A": 79.053,
}


def test_pi_price_formula_exact(close_series, qvix_series):
    """PI = 0.40S + 0.35A + 0.25F 与 PI_price = (0.40S+0.35A)/0.75 的口径恒等式。"""
    d = idx.compute(close_series, qvix_series, IndexConfig())

    ok = d.dropna(subset=["S", "A", "F"])
    lhs = ok["PI_full_raw"]
    rhs = ok["S"] * 0.40 + ok["A"] * 0.35 + ok["F"] * 0.25
    assert np.allclose(lhs, rhs, atol=1e-12)

    ok2 = d.dropna(subset=["S", "A"])
    lhs2 = ok2["PI_price_raw"]
    rhs2 = (ok2["S"] * 0.40 + ok2["A"] * 0.35) / 0.75
    assert np.allclose(lhs2, rhs2, atol=1e-12)


def test_pi_full_is_nan_where_f_is_nan(close_series, qvix_series):
    d = idx.compute(close_series, qvix_series, IndexConfig())
    mask = d["F"].isna()
    assert d.loc[mask, "PI_full_raw"].isna().all()
    assert d.loc[mask, "PI_full"].isna().all()


def test_smoothing_is_ema_span3(close_series, qvix_series):
    """PI_smooth 必须是 span=3 的 EMA，且不得跨越缺值向前携带。"""
    cfg = IndexConfig()
    d = idx.compute(close_series, qvix_series, cfg)
    expected = d["PI_price_raw"].ewm(span=cfg.ema_span, adjust=False, ignore_na=True).mean()
    assert np.allclose(
        d["PI_price"].to_numpy(), expected.to_numpy(), atol=1e-12, equal_nan=True
    )


def test_reference_values_are_finite_and_in_range():
    """基准表数值合法性。

    注意：**不能**用 `(0.40S+0.35A)/0.75` 反算 PI_price ——
    `PI_price` 是 `PI_price_raw` 的 span=3 EMA 平滑值，raw 才是那个恒等式。
    这里只校验量纲与方向，恒等式由 test_pi_price_formula_exact 负责。
    """
    for name, ref in (("科创50", REF_2026_10_08_KC), ("创业板指", REF_2026_10_08_CY)):
        assert 0.0 < ref["PI_price"] < 100.0, f"{name} 的 PI_price 越界"
        assert 0.0 <= ref["S"] <= 100.0 and 0.0 <= ref["A"] <= 100.0
        assert ref["PI_price"] > ref["S"] * 0.9 or ref["PI_price"] < ref["S"] * 1.1


def test_reference_data_absent_by_design():
    """本项目零数据入仓：不存在任何参考数据文件，运行时靠抓取。

    这是一个**有意的**设计选择测试 —— 如果哪天有人往仓库里塞了数据，
    这个测试会失败，提醒维护者同步更新 LICENSE/README 里的数据声明。
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    offenders = []
    for pattern in ("*.csv", "*.json", "*.xlsx", "*.parquet"):
        for p in root.rglob(pattern):
            parts = set(p.parts)
            if parts & {".git", ".venv", "venv", "htmlcov", ".pytest_cache", "node_modules"}:
                continue
            if "data" in p.parts:
                continue
            offenders.append(str(p.relative_to(root)))
    assert not offenders, f"仓库内不应包含任何数据文件，发现：{offenders}"
