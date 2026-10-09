"""期权隐含波动率（QVIX）宽表：唯一来源是第三方 HTTP CSV，必须重点防护。

上游：\u0060http://1.optbbs.com/d/csv/d/k.csv\u0060
* 无表头、无文档、GBK 编码、约 914 KB、单行 80+ 列；
* 实测单次耗时在 1.7~123 秒之间剧烈抖动（多次读超时），HTTP 明文，第三方小站；
* 表内有 Excel 错误串（如 \u0060#NUM!\u0060），以及大量历史 0 值；
* akshare 用硬编码列号切片（如 \u0060iloc[:, 83:87]\u0060）——上游增删一列就会**静默取错数**。

本模块的三道防线：

1. **流式下载 + 字节进度**：慢上游也能让用户看到"在动"，且能设置足够长的读超时。
2. **列结构断言**：取数后校验日期可解析、数值占比、以及"请求的列号必须落在总列数以内"。
   结构不符时抛 DataQualityError（→ 降级用缓存），绝不返回一份看起来正常的错数据。
3. **缓存回退**：抓取失败时保留旧缓存并标注陈旧天数，读数降级为「仅即时口径」。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import NetworkConfig
from ..errors import DataQualityError, NetworkError
from .http import HttpClient, ProgressFn

QVIX_URL = "http://1.optbbs.com/d/csv/d/k.csv"

# 列号（0-based，含）→ 对应上游宽表的 4 列：开/高/低/收
QVIX_COLUMNS: dict[str, tuple[int, int]] = {
    "kcb": (82, 85),
    "cyb": (70, 73),
    "50etf": (62, 65),
    "300etf": (34, 37),
    "500etf": (54, 57),
    "100etf": (38, 41),
    "1000index": (78, 81),
    "300index": (2, 5),
    "50index": (66, 69),
}

MAX_COL_INDEX = max(hi for _, hi in QVIX_COLUMNS.values())


@dataclass
class QvixFetchResult:
    key: str
    frame: pd.DataFrame | None
    source: str
    error: str | None = None
    bytes_downloaded: int = 0
    elapsed: float = 0.0


def parse_wide(text: str, key: str) -> pd.DataFrame:
    """从上游宽表文本中解析出 date/close 两列。"""
    if key not in QVIX_COLUMNS:
        raise DataQualityError(f"未知的 QVIX 标的：{key}")
    lo, hi = QVIX_COLUMNS[key]
    rows: list[tuple[str, float]] = []
    max_cols = 0

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split(",")
        max_cols = max(max_cols, len(parts) - 1)
        if len(parts) <= hi:
            continue
        date_s = parts[0].strip().strip('"')
        if not date_s or not date_s[0].isdigit():
            continue  # 表头或空行
        value_s = parts[hi].strip().strip('"')
        try:
            value = float(value_s)
        except ValueError:
            continue  # #NUM! / 空值
        rows.append((date_s, value))

    if max_cols < MAX_COL_INDEX:
        raise DataQualityError(
            f"QVIX({key}) 上游结构疑似变更：只有 {max_cols + 1} 列，"
            f"但至少期望 {MAX_COL_INDEX + 1} 列。已拒绝使用该数据。"
        )
    if not rows:
        raise DataQualityError(f"QVIX({key}) 解析后没有任何有效行")

    df = pd.DataFrame(rows, columns=["date_raw", "close"])
    df["date"] = pd.to_datetime(df["date_raw"], format="mixed", errors="coerce")
    df = df.dropna(subset=["date", "close"])
    df = df[df["close"] > 0]  # 上游历史 0 值一律视为缺失
    df = df.drop(columns=["date_raw"]).sort_values("date").drop_duplicates("date")
    return df.reset_index(drop=True)


def assert_structure(text: str, key: str, df: pd.DataFrame) -> None:
    """结构断言。

    注意：**不能**用「数值占比」判断是否取错列。该宽表大量单元格是 Excel 错误串
    （#NUM!）或 0，实测各列数值占比仅 23%~34%（越靠后的列越低），
    这是上游数据的固有特征，不是取错列的信号。

    真正可靠的判据是：
    1. 文件总列数必须覆盖我们要切的列（防上游删列导致静默越界）；
    2. 解析出的有效行数、时间跨度、取值区间必须像一个波动率指数；
    3. 与本地已有缓存的重叠日期必须高度吻合（自校准，见 cross_check_cache）。
    """
    lo, hi = QVIX_COLUMNS[key]
    header_cols = len(text.splitlines()[0].split(",")) - 1 if text.splitlines() else 0
    if header_cols < MAX_COL_INDEX:
        raise DataQualityError(
            f"QVIX({key}) 上游列数不足：首行仅 {header_cols + 1} 列，"
            f"至少需要 {MAX_COL_INDEX + 1} 列。已拒绝使用该数据。"
        )
    if len(df) < 300:
        raise DataQualityError(f"QVIX({key}) 有效行数仅 {len(df)}，少于 300 行的下限")

    values = df["close"].to_numpy(dtype=float)
    if (values <= 0).any():
        raise DataQualityError(f"QVIX({key}) 出现非正的波动率取值，疑似取错列")
    median = float(np.median(values))
    if not (3.0 <= median <= 150.0):
        raise DataQualityError(
            f"QVIX({key}) 中位数为 {median:.1f}，不像一个波动率指数（合理区间 3~150），"
            "疑似上游列结构变更导致取错列。已拒绝使用该数据。"
        )
    span_days = (df["date"].iloc[-1] - df["date"].iloc[0]).days
    if span_days < 180:
        raise DataQualityError(f"QVIX({key}) 时间跨度仅 {span_days} 天，不足以计算分位")


# 交叉校验判据（尺度感知）。
#
# ⚠️ 实测事实：**上游每次请求都会重算历史值。**
#    同一品种、同一列，两次抓取的重叠日期中位偏差约 1%~2%
#    （科创50 实测 2.07%，创业板指 1.11%），
#    且新鲜值往往系统性略低（如科创50 中位数 30.4 vs 31.37）。
#    后果：QVIX 是 10 日平滑量，日度变化本身就小（约 0.5 点），
#    所以哪怕只有 2% 的修订，也足以把「日度变化的相关系数」压到 0.54~0.71。
#
#    因此**不能**用「日度变化相关系数 < 0.95」判为取错列 —— 那会把
#    完全正常的数据误杀，导致永久退回缓存。这个坑本项目踩过。
#
# 正确的判据：把偏差与序列自身的日度波动幅度比较。
#    列号偏移会让偏差达到「波动水平」的量级（例如把中位数 25 的品种
#    读成中位数 35 的品种，偏差 ≈10 点，而典型日度波动只有约 0.5 点）；
#    上游重算造成的偏差则明显小得多。二者相差一个数量级，容易区分。
#
# 阈值标定（实测）：上游重算造成的比值在 1.5~2.5 之间；
# 而列号偏移会让偏差达到波动水平的量级 —— 例如把中位数 25 的品种读成
# 中位数 35 的品种，比值会达到 15~30。取 4.0 作为界，两侧都有数倍余量。
CROSS_CHECK_MIN_OVERLAP = 100
CROSS_CHECK_MAX_RATIO = 4.0          # 中位偏差 / 典型日度波动幅度，上限
CROSS_CHECK_ABS_MEDIAN_CAP = 8.0     # 中位绝对偏差的绝对上限（点）


def cross_check_cache(fresh: pd.DataFrame, cached: pd.DataFrame | None, key: str) -> None:
    """与本地缓存交叉校验。

    这是最有效的一道防线 —— 对任何标的都自动成立，且能捕捉
    「列号悄悄偏移」这类最难发现的故障（数值本身看起来完全正常）。

    判据是**尺度感知**的：中位绝对偏差不得超过典型日度波动幅度的若干倍，
    另加一个绝对上限。这样既能放行上游的历史重算，又能拦住量级取错。
    """
    if cached is None or len(cached) == 0:
        return
    a = fresh.set_index("date")["close"].astype(float)
    b = cached.set_index("date")["close"].astype(float)
    common = a.index.intersection(b.index)
    if len(common) < CROSS_CHECK_MIN_OVERLAP:
        return

    x = a.loc[common]
    y = b.loc[common]

    median_dev = float((x - y).abs().median())
    typical_move = float(y.diff().abs().median())
    if typical_move <= 0:
        typical_move = float(y.std())
    # 尺度退化时比值没有意义（典型日度波动接近 0），只保留绝对上限兜底，
    # 否则会把「几乎不动的序列」误判为取错列。
    ratio = median_dev / typical_move if typical_move >= 0.05 else 0.0

    if median_dev > CROSS_CHECK_ABS_MEDIAN_CAP or ratio > CROSS_CHECK_MAX_RATIO:
        raise DataQualityError(
            f"QVIX({key}) 与本地缓存交叉校验失败：{len(common)} 个重叠日期的"
            f"中位绝对偏差 {median_dev:.2f} 点，是该序列典型日度波动"
            f"（{typical_move:.2f} 点）的 {ratio:.1f} 倍"
            f"（上限 {CROSS_CHECK_MAX_RATIO} 倍，且绝对偏差不超过 "
            f"{CROSS_CHECK_ABS_MEDIAN_CAP:.0f} 点）。"
            "很可能是上游列结构变更导致取错列，已拒绝使用新抓取的数据，改用缓存。"
        )


def download_wide(
    net: NetworkConfig,
    *,
    progress: ProgressFn | None = None,
) -> tuple[str, int, float]:
    """下载上游宽表一次（所有 QVIX 标的共用这一次下载）。"""
    client = HttpClient(
        timeout_connect=net.timeout_connect,
        timeout_read=net.timeout_qvix,
        retries=net.retries_qvix,
        backoff=net.backoff,
        jitter=net.jitter,
        proxy=net.proxy,
        user_agent=net.user_agent,
    )
    try:
        resp = client.get_bytes(
            QVIX_URL,
            progress=progress,
            progress_label="QVIX 上游宽表",
            total_budget=net.budget_qvix,
        )
        text = resp.content.decode("gbk", errors="replace")
    finally:
        client.close()
    return text, len(resp.content), resp.elapsed


def fetch_qvix(
    keys: tuple[str, ...],
    net: NetworkConfig,
    *,
    cache_paths: dict[str, Path] | None = None,
    progress: ProgressFn | None = None,
) -> dict[str, QvixFetchResult]:
    """一次下载，解析出所需的全部 QVIX 序列。"""
    try:
        text, nbytes, elapsed = download_wide(net, progress=progress)
    except NetworkError as exc:
        return {k: QvixFetchResult(k, None, "none", str(exc)) for k in keys}

    out: dict[str, QvixFetchResult] = {}
    for key in keys:
        try:
            df = parse_wide(text, key)
            assert_structure(text, key, df)
            if cache_paths and key in cache_paths:
                cross_check_cache(df, read_cache(cache_paths[key]), key)
            out[key] = QvixFetchResult(key, df, "optbbs", None, nbytes, elapsed)
        except DataQualityError as exc:
            out[key] = QvixFetchResult(key, None, "none", str(exc), nbytes, elapsed)
    return out


def read_cache(path: Path) -> pd.DataFrame | None:
    """读取本地 QVIX 缓存（列：date / close）。"""
    if not path.is_file():
        return None
    from .. import store

    df = store.read_csv_any(path)
    if df is None or "date" not in df.columns or "close" not in df.columns:
        return None
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    # 上游把缺失写成 0，这里视同缺失；同时剔除负值（波动率不可能为负）
    df.loc[df["close"] <= 0, "close"] = np.nan
    df = df.dropna(subset=["date", "close"])
    return df.sort_values("date").drop_duplicates("date").reset_index(drop=True)
