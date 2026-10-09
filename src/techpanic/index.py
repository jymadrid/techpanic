"""核心算法：科技板块恐慌指数 PI。

三因子结构::

    S 突发性   = ½·z(ΔRV20) + ½·z(RV5/RV20 − 1)
    A 不对称性 = ½·z(下行半方差占比) + ½·z(下跌波动/上涨波动)
    F 前瞻恐惧 = z(QVIX)                      ← 期权盘后发布，当日可能缺失
    PI = 0.40·S + 0.35·A + 0.25·F
    PI_smooth = EMA(PI, 3)

两条独立口径：

* **A 完整口径**（三因子，含期权）：官方读数，但 QVIX 盘后发布，可能滞后 1 个交易日。
* **B 即时口径**（两因子，只用价格，除以 0.75 归一化）：当日收盘即可算。

**两条口径不可相减解读。** 差值是「数据新鲜度」与「成分构成」的叠加，
把它当成「今天的单日冲击」是错的。

所有 z 值都用**扩展窗口分位**（pct rank），严格因果、无前视。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import IndexConfig
from .levels import causal_levels, classify, percentile_of

SOURCE_FULL = "full"
SOURCE_PRICE_ONLY = "price_only"


def pct_rank(series: pd.Series, min_periods: int = 60) -> pd.Series:
    """扩展窗口分位（0~100）。严格因果：第 t 天的值只用到 t 及之前的数据。"""
    return series.expanding(min_periods=min_periods).rank(pct=True) * 100.0


def _trading_day_return(close: pd.Series, n: int) -> pd.Series:
    """按**交易日**计算 n 日涨跌幅（%）。

    不能直接用 close.shift(n)：close 虽已 dropna，但上游缓存仍可能缺某一天。
    一旦缺行，shift(n) 就会跨过 n 个**有效行**而不是 n 个**交易日** ——
    实测过：少一行会让「近5日涨跌」变成按约 26 个交易日计算
    （2020-04-09 由 +6.10% 变 -12.08%，方向由「向上」翻成「向下」，
    按 README 判读表就是「狂热」与「恐慌」的差别，且没有任何警告）。

    这里改为按索引位置回溯：只有当 n 个交易日之前那一行确实存在时才计算，
    否则返回 NaN。宁可显示「未知」，也不给一个口径错误的百分比。
    """
    values = close.to_numpy(dtype=float)
    n = max(int(n), 1)
    prev_pos = np.arange(len(values)) - n
    valid = prev_pos >= 0
    out = np.full(len(values), np.nan, dtype=float)
    if valid.any():
        cur = values[prev_pos[valid] + n]
        prev = values[prev_pos[valid]]
        with np.errstate(divide="ignore", invalid="ignore"):
            out[valid] = (cur / prev - 1.0) * 100.0
    return pd.Series(out, index=close.index, dtype=float)


def compute(
    close: pd.Series,
    qvix: pd.Series | None,
    cfg: IndexConfig,
) -> pd.DataFrame:
    """由收盘价与（可选的）QVIX 计算完整指标表。

    参数
    ----
    close : 按日期升序排列的收盘价（索引为 DatetimeIndex）
    qvix  : 同一索引的期权隐含波动率；缺失的日期用 NaN 表示
    cfg   : 指数参数（权重、窗口、锚点算法）

    返回列：close/ret/rv20/rv5/dRV/RV52/semi/doup/S/A/F/PI_full_raw/PI_full/
    PI_price_raw/PI_price/level_full/level_price/方向/source
    """
    close = pd.to_numeric(close, errors="coerce").astype(float)
    r = close.pct_change()
    ret = r * 100.0
    rv20 = r.rolling(cfg.rv_window).std() * np.sqrt(cfg.annualization) * 100.0
    rv5 = r.rolling(cfg.rv_short).std() * np.sqrt(cfg.annualization) * 100.0

    neg = ret.where(ret < 0, 0.0)
    pos = ret.where(ret > 0, 0.0)

    d = pd.DataFrame({"close": close, "ret": ret, "rv20": rv20, "rv5": rv5})
    if qvix is None:
        d["qvix"] = np.nan
    else:
        q = pd.to_numeric(qvix.reindex(d.index), errors="coerce").astype(float)
        d["qvix"] = q.replace(0.0, np.nan)

    d["dRV"] = d["rv20"].diff()
    d["RV52"] = d["rv5"] / d["rv20"] - 1.0
    d["semi"] = neg.rolling(cfg.rv_window).var() / ret.rolling(cfg.rv_window).var()
    d["doup"] = neg.rolling(cfg.rv_window).std() / pos.rolling(cfg.rv_window).std()

    d = d.dropna(subset=["dRV", "RV52", "semi", "doup"])

    mp = cfg.z_min_periods
    d["S"] = (pct_rank(d["dRV"], mp) + pct_rank(d["RV52"], mp)) / 2.0
    d["A"] = (pct_rank(d["semi"], mp) + pct_rank(d["doup"], mp)) / 2.0
    d["F"] = pct_rank(d["qvix"].dropna(), mp).reindex(d.index)

    d["ret5"] = _trading_day_return(d["close"], cfg.rv_short)
    direction = np.where(d["ret5"] < 0, "向下", "向上")
    d["方向"] = np.where(d["ret5"].isna(), "未知", direction)

    # ---------- A 完整口径 ----------
    d["PI_full_raw"] = d["S"] * cfg.weight_s + d["A"] * cfg.weight_a + d["F"] * cfg.weight_f
    d["PI_full"] = d["PI_full_raw"].ewm(span=cfg.ema_span, adjust=False).mean()
    # EMA 会跳过 NaN 并沿用上一日的内部状态，必须把 QVIX 缺失日显式置回 NaN，
    # 否则用户会看到「数据日」与真实可用数据日不符的陈旧读数（参考实现的历史缺陷）。
    # 回归测试：tests/test_index.py::test_missing_qvix_does_not_carry_forward_full_value
    d.loc[d["F"].isna(), "PI_full"] = np.nan

    # ---------- B 即时口径 ----------
    d["PI_price_raw"] = (d["S"] * cfg.weight_s + d["A"] * cfg.weight_a) / (
        cfg.weight_s + cfg.weight_a
    )
    d["PI_price"] = d["PI_price_raw"].ewm(span=cfg.ema_span, adjust=False).mean()

    d["source"] = np.where(d["F"].notna(), SOURCE_FULL, SOURCE_PRICE_ONLY)

    # ---------- 分级 ----------
    level_full, table_full = causal_levels(
        d["PI_full"], cfg.level_anchor, mp, cfg.frozen_anchors
    )
    level_price, table_price = causal_levels(
        d["PI_price"], cfg.level_anchor, mp, cfg.frozen_anchors
    )
    d["level_full"] = level_full
    d["level_price"] = level_price
    d.attrs["anchors_full"] = table_full
    d.attrs["anchors_price"] = table_price
    return d


def latest_full(d: pd.DataFrame) -> pd.Series | None:
    """最近一条有完整口径值的记录（可能不是最新交易日）。"""
    sub = d.dropna(subset=["PI_full"])
    return None if sub.empty else sub.iloc[-1]


def latest_full_date(d: pd.DataFrame) -> pd.Timestamp | None:
    sub = d.dropna(subset=["PI_full"])
    return None if sub.empty else sub.index[-1]


def latest_price(d: pd.DataFrame) -> pd.Series | None:
    sub = d.dropna(subset=["PI_price"])
    return None if sub.empty else sub.iloc[-1]


def latest_price_date(d: pd.DataFrame) -> pd.Timestamp | None:
    sub = d.dropna(subset=["PI_price"])
    return None if sub.empty else sub.index[-1]


def staleness_trading_days(all_dates: pd.DatetimeIndex, full_date: pd.Timestamp | None) -> int:
    """完整口径相对最新交易日滞后了几个交易日（0 = 同步）。"""
    if full_date is None or len(all_dates) == 0:
        return 0
    return int((all_dates > full_date).sum())


def level_of(d: pd.DataFrame, which: str, value: float) -> str:
    """按当日因果锚点给出单值分级（供读数卡使用）。"""
    key = "anchors_full" if which == "full" else "anchors_price"
    table = d.attrs.get(key)
    if table is None or table.empty:
        return classify(value, (45.0, 55.0, 63.0, 71.0))
    row = table.dropna(how="all")
    if row.empty:
        return classify(value, (45.0, 55.0, 63.0, 71.0))
    return classify(value, row.iloc[-1].to_numpy(dtype=float))


def percentile(d: pd.DataFrame, which: str, value: float) -> float:
    col = "PI_full" if which == "full" else "PI_price"
    return percentile_of(d[col], value)
