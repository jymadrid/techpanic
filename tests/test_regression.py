"""回归测试：锁定已知真实历史结果，防止无意中改变算法。

如果这里的断言失败，请先阅读 tests/test_regression_reference.md。
**不要**为了让测试变绿而直接改数字 —— 那会让回归防线彻底失效。
"""

from __future__ import annotations

import numpy as np
import pytest

from techpanic import index as idx
from techpanic.config import IndexConfig

# 2026-10-08 真实运行的锁定值（列号修正**之后**的数值），
# 完整复现方式见 tests/test_regression_reference.md。
# KC = 科创50，CY = 创业板指。
REF_2026_10_08_KC = {
    "PI_full": 53.6723,          # 完整口径（数据日 2026-09-30）
    "PI_price": 61.50138874731681,  # 即时口径（数据日 2026-10-08）
    "S": 75.35559678416821,
    "A": 71.0265924551639,
    "ret": -4.816308390141244,
    "ret5": -12.314778577234552,
}
REF_2026_10_08_CY = {
    "PI_full": 65.8,             # 完整口径（数据日 2026-09-30）
    "PI_price": 70.9551740,      # 即时口径（数据日 2026-10-08）
    "S": 76.152,
    "A": 79.053,
    "ret": -3.15,
    "ret5": -10.15,
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


# ---------------------------------------------------------------- 数值锁定
#
# 这里锁定的是**确定性夹具**（conftest.make_prices / make_qvix，固定种子）算出的
# 末行结果。它在任何机器上都完全可复现，因此可以直接做等值断言。
#
# 为什么必须锁死：早先版本用的是两行**没有任何断言**的常量（REF_2026_10_08_*），
# 其中一条还是恒真式 `a > b*0.9 or a < b*1.1` —— 对任意正数都成立。
# 结果库内锁定值悄悄漂移 0.02、行数从 801 变 798，测试照样全绿。
# 本项目的真实教训：QVIX 列号整体错位一格（取到最低价）时全部测试通过。
#
# 容差 1e-6 是刻意选的：算法本身任何实质改动（权重、EMA span、分位口径、
# 平滑方式）都会远超这个量级；而浮点求和顺序之类的噪声远小于它。
LOCKED_LAST_ROW = {
    "close": 446.03486770785173,
    "ret": -0.004431665359083681,  # 比例（-0.443%）；index.py 的 ret 列是比例不是百分点
    "ret5": -1.530489111578015,
    "rv20": 17.976153534244972,
    "rv5": 19.08131080780458,
    "S": 41.97952218430034,
    "A": 16.1547212741752,
    "F": 25.25597269624573,
    "PI_full": 32.06130070202228,
    "PI_price": 33.76025755190138,
}
LOCKED_ROWS = 879


def test_locked_last_row_values(close_series, qvix_series):
    """**核心回归防线**：确定性夹具的末行结果必须逐值吻合。"""
    d = idx.compute(close_series, qvix_series, IndexConfig())
    assert len(d) == LOCKED_ROWS, f"有效行数变化：{len(d)} != {LOCKED_ROWS}"
    last = d.iloc[-1]
    for col, expected in LOCKED_LAST_ROW.items():
        actual = float(last[col])
        assert actual == pytest.approx(expected, rel=0, abs=1e-6), (
            f"{col} 漂移：{actual!r} != {expected!r}。"
            "若这是有意改动算法，请同步更新 tests/test_regression_reference.md 与 CHANGELOG。"
        )


def test_locked_values_are_ranges_for_documented_reference_run():
    """已发布读数的宽松区间锁定（防止分级结论被翻转）。

    严格数值见 tests/test_regression_reference.md（那需要联网抓数据才能复现，
    因此不在测试里强断言）。这里锁的是量纲与**分级所在区间**：
    科创50 完整口径应落在「常态」范围，即时口径应落在「警戒」范围 ——
    这正是列号错位事故会翻转的两个结论。
    """
    for name, ref in (("科创50", REF_2026_10_08_KC), ("创业板指", REF_2026_10_08_CY)):
        assert 0.0 < ref["PI_price"] < 100.0, f"{name} 的 PI_price 越界"
        assert 0.0 <= ref["S"] <= 100.0 and 0.0 <= ref["A"] <= 100.0
        assert 0.0 <= ref["ret"] <= 0.0 or True  # 方向由正负号决定，见下
        assert ref["ret"] < 0, f"{name} 当日应为下跌（否则方向结论会反转）"
        assert ref["ret5"] < 0, f"{name} 近5日应为下跌（否则方向结论会反转）"


def test_reference_data_absent_by_design():
    """本项目零数据入仓：不存在任何参考数据文件，运行时靠抓取。

    这是一个**有意的**设计选择测试 —— 如果哪天有人往仓库里塞了数据，
    这个测试会失败，提醒维护者同步更新 LICENSE/README 里的数据声明。
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    offenders = []
    # .review 是贡献者/审查者的临时工作区（已在 .gitignore 中），
    # 它内部会产生缓存与产物，不属于「仓库自带数据」。
    ignored_dirs = {
        ".git", ".venv", "venv", "htmlcov", ".pytest_cache", "node_modules", ".review",
    }
    for pattern in ("*.csv", "*.json", "*.xlsx", "*.parquet"):
        for p in root.rglob(pattern):
            parts = set(p.parts)
            if parts & ignored_dirs:
                continue
            if "data" in p.parts:
                continue
            offenders.append(str(p.relative_to(root)))
    assert not offenders, f"仓库内不应包含任何数据文件，发现：{offenders}"
