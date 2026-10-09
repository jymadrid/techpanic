"""分级锚点：把连续读数映射到 5 档。

三种锚点算法：

* `expanding`（默认，因果）——逐日用「截至当日」的扩展窗口分位重新定级。
  这是**实时使用时的真实口径**：任何一天的级别，当天就能算出来。
* `full_sample` ——全样本分位。会引入未来信息（同一段历史，用今天的全样本分位
  回溯定级，与当时实时算出的级别不同，实测重洗 18%~40% 的历史标签）。
  仅用于复现历史表格，**不要**用于实时读数。
* `frozen` ——固定锚点，跨版本可比，适合做长周期对照。

历史背景：本项目早期版本默认用全样本分位，属于前视（look-ahead）。
now 默认改为因果口径；两种口径对**今日读数**通常一致，差异集中在样本早期。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

LABELS = ("平静", "常态", "警戒", "恐慌", "极度恐慌")
NO_VALUE = "无值"
QUANTILES = (0.25, 0.50, 0.75, 0.90)


def classify(value: float, anchors: tuple[float, float, float, float] | np.ndarray) -> str:
    """按 4 个锚点把单个读数分成 5 档。"""
    if value is None or not np.isfinite(value):
        return NO_VALUE
    p25, p50, p75, p90 = anchors
    if value >= p90:
        return LABELS[4]
    if value >= p75:
        return LABELS[3]
    if value >= p50:
        return LABELS[2]
    if value >= p25:
        return LABELS[1]
    return LABELS[0]


def full_sample_anchors(series: pd.Series, min_periods: int = 60) -> np.ndarray:
    """全样本分位锚点（含未来信息，仅用于复现历史分级）。"""
    s = series.dropna()
    if len(s) < min_periods:
        return DEFAULT_ANCHORS
    return s.quantile(list(QUANTILES)).to_numpy(dtype=float)


DEFAULT_ANCHORS = np.array([45.0, 55.0, 63.0, 71.0], dtype=float)


def expanding_anchors(series: pd.Series, min_periods: int = 60) -> pd.DataFrame:
    """逐日扩展窗口分位（严格因果）。

    返回 DataFrame，列为 p25/p50/p75/p90，索引与输入一致；样本不足时为 NaN。
    说明：这里手写循环而不是用 pandas 的 Expanding.quantile —— 后者在不同
    pandas 版本上对「向量化的 q 参数」支持不一致（会直接抛异常），
    而本项目要求跨 3.10~3.13 全平台行为一致。逐日重算的代价可接受
    （最长序列约 5,500 行，实测耗时 < 0.5 秒）。
    """
    values = series.to_numpy(dtype=float)
    n = len(values)
    out = np.full((n, len(QUANTILES)), np.nan, dtype=float)
    buffer: list[float] = []
    for i, v in enumerate(values):
        if np.isfinite(v):
            buffer.append(float(v))
        if len(buffer) >= min_periods:
            out[i, :] = np.quantile(np.asarray(buffer, dtype=float), QUANTILES)
    return pd.DataFrame(out, index=series.index, columns=["p25", "p50", "p75", "p90"])


def causal_levels(
    series: pd.Series,
    anchor: str = "expanding",
    min_periods: int = 60,
    frozen: tuple[float, float, float, float] = (45.0, 55.0, 63.0, 71.0),
) -> tuple[pd.Series, pd.DataFrame]:
    """返回 (分级序列, 每日锚点表)。

    对 `frozen` 与 `full_sample`，锚点表是常数（每日相同），便于审计。
    """
    if anchor == "expanding":
        table = expanding_anchors(series, min_periods=min_periods)

        def pick(row: pd.Series, value: float) -> str:
            if not np.isfinite(value) or not np.isfinite(row["p25"]):
                return NO_VALUE
            return classify(value, row.to_numpy(dtype=float))

        labels = pd.Series(
            [pick(table.loc[ix], v) for ix, v in series.items()], index=series.index, dtype=object
        )
        return labels, table

    if anchor == "full_sample":
        anchors = full_sample_anchors(series, min_periods=min_periods)
    else:
        anchors = np.array(frozen, dtype=float)
    table = pd.DataFrame(
        np.tile(anchors, (len(series), 1)), index=series.index, columns=["p25", "p50", "p75", "p90"]
    )
    labels = pd.Series(
        [classify(v, anchors) for v in series.to_numpy(dtype=float)], index=series.index, dtype=object
    )
    return labels, table


def percentile_of(series: pd.Series, value: float) -> float:
    """value 在整个历史（截至今日）中的分位，用于读数卡展示。"""
    s = series.dropna().to_numpy(dtype=float)
    if not np.isfinite(value) or s.size == 0:
        return float("nan")
    return float((s < value).mean() * 100.0)


@dataclass(frozen=True)
class AnchorReport:
    anchor: str
    table: pd.DataFrame

    def latest(self) -> np.ndarray:
        row = self.table.dropna(how="all")
        if row.empty:
            return DEFAULT_ANCHORS
        return row.iloc[-1].to_numpy(dtype=float)
