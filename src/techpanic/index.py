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


def _return_over_n_rows(close: pd.Series, n: int) -> pd.Series:
    """计算 n 日涨跌幅（%）：**按序列中的有效行**回溯 n 行。

    ## 这个函数的能力边界（请务必读完再改）

    它是 `close.shift(n)` 的等价实现，**不是**严格意义上的
    "n 个自然交易日收益"。两者在序列完整时完全一致；一旦序列缺行，
    它跨过的是 n 个**有效行**，实际可能对应更多交易日。

    ## 为什么没有被"修成"真正的交易日口径

    作者尝试过，结论是**做不到，硬做会引入更大的错误**：

    1. 正确算法需要一个**权威交易日历**，而不是上证指数自身的日期序列
       （否则就是循环论证："我们的缓存是完整的，因为我们假设它是完整的"）。
    2. 即使用 `pd.bdate_range`（周一至周五）去近似，也会把**春节、国庆**
       这类真实休市当成"缺数据"。实测在本仓库的缓存上：
         科创50   1637 行，bdate_range 认为"缺" 129 天
         创业板指 3969 行，bdate_range 认为"缺" 299 天
       其中绝大多数是长假休市。若按此置 NaN，会凭空抹掉约 13% 的
       「近5日涨跌」——而用户看到"未知"的原因只是我们用了错误的日历。
    3. A 股交易日历无法从价格数据中**唯一推断**："周一至周五没有数据"
       既可能是"休市"，也可能是"上游漏了一天"。二者不能靠日期形状区分。

    所以这里选择**做能确定的事**，而不是假装能确定：
      * 涨跌幅按有效行计算（序列完整时即等于交易日口径）；
      * 由 `_cache_gaps` 检测"可疑缺口"并在运行时显式告警，
        把判断权交给用户，而不是静默改变数值。
        详见 pipeline.py 里对缓存缺口的告警。

    ## 历史教训

    "近5日涨跌"曾被误当作 26 个交易日口径的原因不是本函数，而是
    `compute()` 里把 `dropna` 后的**过滤表**传了进来 —— 中间缺行会让
    shift 跨得更远。现在传入的是完整序列（缓存写盘时不允许日期乱序或重复，
    见 `store.check_frame`），因此正常路径不会出现这个偏差。
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


# 普通周末的最大间隔是 3 天（周五→周一）。超过 5 个自然日，说明中间
# 至少有一个工作日没有数据 —— 可能是长假休市，也可能是上游漏了一天。
SUSPICIOUS_GAP_DAYS = 5


def detect_cache_gaps(dates) -> list[tuple[str, str, int]]:
    """找出"可疑缺口"：相邻两行间隔超过 5 个自然日的位置。

    返回值是 (前一个日期, 后一个日期, 间隔天数) 的列表，按间隔从大到小。

    **只说事实，不替代日历。** A 股长假休市同样会落在这里，因此调用方
    应当把它当成"值得看一眼"的提示，而不是"数据一定坏了"的判定。
    """
    if len(dates) < 2:
        return []
    ordered = pd.DatetimeIndex(dates).sort_values()
    deltas = np.diff(ordered.values).astype("timedelta64[D]").astype(int)
    out: list[tuple[str, str, int]] = []
    for i in np.nonzero(deltas > SUSPICIOUS_GAP_DAYS)[0]:
        out.append((str(ordered[i].date()), str(ordered[i + 1].date()), int(deltas[i])))
    out.sort(key=lambda t: -t[2])


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

    d["ret5"] = _return_over_n_rows(d["close"], cfg.rv_short)
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
