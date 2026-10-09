"""期权隐含波动率（QVIX）宽表：唯一来源是第三方 HTTP CSV，必须重点防护。

上游：\u0060http://1.optbbs.com/d/csv/d/k.csv\u0060
* 无表头、无文档、GBK 编码、约 914 KB、单行 80+ 列；
* 实测单次耗时在 1.7~123 秒之间剧烈抖动（多次读超时），HTTP 明文，第三方小站；
* 表内有 Excel 错误串（如 \u0060#NUM!\u0060），以及大量历史 0 值；
* 列结构靠硬编码索引定位（与 akshare 一致），上游增删一列就会**静默取错数**。

本模块的四道防线：

1. **流式下载 + 字节进度**：慢上游也能让用户看到"在动"，且能设置足够长的读超时。
2. **列结构断言**：取数后校验日期可解析、总列数足够、以及"请求的列号落在总列数以内"。
3. **OHLC 结构不变量**（最关键）：4 列必须满足 open ≤ high、low ≤ high、low ≤ close ≤ high。
   它能拦住「列号整体错位」——取到最低价时数值看起来完全正常，单看 close 列发现不了。
4. **缓存回退**：抓取失败时保留旧缓存并标注陈旧天数，读数降级为「仅即时口径」。

以上任一断言不通过都抛 DataQualityError（→ 降级用缓存），
绝不返回一份看起来正常的错数据。
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

# 上游宽表每个品种占**连续 4 列**：open / high / low / close（0-based）。
#
# 历史教训（务必保留这段注释）：本项目早期版本把这些索引整体写错了一格
# （例如 kcb 写成 82~85），结果每个品种取到的都是**当日最低价**，
# 而 low 与 close 只差约 2%，看上去完全正常 —— 三道防线（列数断言、
# 中位数合理性、与缓存交叉校验）一道都没拦住，因为缓存本身也是用同一个
# 错误映射写出来的，自己比自己当然一致。真实读数因此偏差约 0.7 点，
# 并且把「常态」错报成「警戒」。
#
# 现在的防错方式是**结构不变量**（见 _assert_ohlc_invariant）：
# 4 列必须满足 open ≤ high、low ≤ high、low ≤ close ≤ high。
#
# 实测区分度（Round 2 独立复核，全部 9 个品种）：
#   正确窗口：满足率 0.9600 ~ 0.9979，high>low 占比 0.9861 ~ 0.9990
#   左移一格：满足率 0.0000 ~ 0.0098
# 阈值取 0.95 / 0.97，两侧没有重叠。
#
# ⚠️ 但要说清它的**能力边界**：它证明的是"这 4 列满足 OHLC 的相对关系"，
#    而不是"这 4 列就是该品种的 OHLC"。实测扫描全部 4 列窗口，有 15 个起点
#    能通过阈值，其中 7/15/23/73 是"起点落在某四列第 3 列"的**退化窗口**
#    （第 4 列落在 high 上，low≤close≤high 退化成 open≤high，恒成立）。
#    也就是说：它对"左移一格"这个具体事故形态非常灵敏（9/9 拒绝），
#    但不构成一般性的证明。裕度最小的正确窗口是 1000index（+0.0100），
#    上游再脏一点就会误杀它 —— 后果只是该品种走缓存，不会出错数。
#
# 索引来源：akshare 1.19.1 的 akshare/index/index_option_qvix.py
# （KCB=83-86、CYB=71-74、50ETF=1-4、300ETF=9-12、500ETF=67-70、
#  100ETF=75-78、300INDEX=17-20、1000INDEX=25-28、50INDEX=79-82），
# 并已在真实宽表上用四元组逐值核对。
#
# ⚠️ 易错点（本项目踩过）：这里的索引是「上游原始行 split(",") 后的位置」。
# 上游文件**没有表头行**，且第 0 列就是日期，所以 parts 的位置恰好与 akshare
# 的 iloc 列号一致 —— 本项目也因此刻意没有把日期列单独摘出来。
# 写测试造数据时要注意：如果先构造 cells 列表再在前面拼日期，就会整体差 1。
# 解析结果自检：kcb 应为 801 行、末值 34.57（2026-09-30），不是 low 的 34.55。
QVIX_OHLC: dict[str, tuple[int, int, int, int]] = {
    "50etf": (1, 2, 3, 4),
    "300etf": (9, 10, 11, 12),
    "300index": (17, 18, 19, 20),
    "1000index": (25, 26, 27, 28),
    "500etf": (67, 68, 69, 70),
    "cyb": (71, 72, 73, 74),
    "100etf": (75, 76, 77, 78),
    "50index": (79, 80, 81, 82),
    "kcb": (83, 84, 85, 86),
}

# 向后兼容的 (首个索引, 收盘索引) 视图；新代码请直接用 QVIX_OHLC。
QVIX_COLUMNS: dict[str, tuple[int, int]] = {
    k: (c[0], c[3]) for k, c in QVIX_OHLC.items()
}

MAX_COL_INDEX = max(c[3] for c in QVIX_OHLC.values())

# 导入期自检：窗口的第 1 列不能是第 0 列。
# 第 0 列是日期（形如 2026/9/30），把它当成价格列会让该行四元组全部
# 解析失败 —— 进而让结构不变量"样本不足"而跳过（这个洞真实存在过）。
# 放在模块级是刻意的：写错列号应该在 import 阶段就炸，而不是等到运行时。
# 注：未来的品种若真的从第 1 列开始（0-based 第 1 列 = 第二个字段），
# 这里不会误伤；只有 <1 才视为错误。
for _key, _quad in QVIX_OHLC.items():
    if _quad[0] < 1:
        raise DataQualityError(
            f"QVIX 列号配置错误：{_key} 的窗口 {_quad} 从第 {_quad[0]} 列开始，"
            "而第 0 列是日期字段，不是价格。"
        )


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
    quads = QVIX_OHLC[key]
    hi = quads[3]  # 收盘价列（OHLC 四元组的最后一列）
    rows: list[tuple[str, float]] = []
    ohlc_rows: list[tuple[float, float, float, float]] = []
    max_cols = 0
    # 窗口首列是否在**任何一行**成功解析成数值。用来区分两种"四元组解析失败"：
    #   * 首列从来没成功过 → 它指向纯文本列（日期），列号错位 → 必须拒绝；
    #   * 首列成功过，只是这个 key 稀疏 → 上游正常特征 → 放行。
    first_col_parsed = False

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

        # 单独记一下首列能否解析（见 first_col_parsed 的说明）
        if not first_col_parsed:
            try:
                float(parts[quads[0]].strip().strip('"'))
                first_col_parsed = True
            except (ValueError, IndexError):
                pass

        # 同行的开/高/低三列也要能解析，供结构不变量检验使用
        try:
            quad = tuple(
                float(parts[c].strip().strip('"')) for c in quads
            )
        except (ValueError, IndexError):
            continue
        ohlc_rows.append(quad)  # type: ignore[arg-type]

    if max_cols < MAX_COL_INDEX:
        raise DataQualityError(
            f"QVIX({key}) 上游结构疑似变更：只有 {max_cols + 1} 列，"
            f"但至少期望 {MAX_COL_INDEX + 1} 列。已拒绝使用该数据。"
        )
    if not rows:
        raise DataQualityError(f"QVIX({key}) 解析后没有任何有效行")

    _assert_ohlc_invariant(
        key,
        ohlc_rows,
        parsed_rows=len(rows),
        first_col_ever_parsed=first_col_parsed,
    )

    df = pd.DataFrame(rows, columns=["date_raw", "close"])
    df["date"] = pd.to_datetime(df["date_raw"], format="mixed", errors="coerce")
    df = df.dropna(subset=["date", "close"])
    df = df[df["close"] > 0]  # 上游历史 0 值一律视为缺失
    df = df.drop(columns=["date_raw"]).sort_values("date").drop_duplicates("date")
    return df.reset_index(drop=True)


# 结构不变量阈值。标定依据（Round 2 独立复核，9 个品种逐一实测）：
#   正确窗口：满足率 0.9600~0.9979，high>low 占比 0.9861~0.9990
#   错位一格：满足率 0.0000~0.0098
# 两组之间没有重叠，阈值取在中间。
# ⚠️ 历史注记：早期注释写的是"错位 0.0%~64.9%"与"cyb 0.0093"，
#    那是把别的窗口/别的位移混进来了，无法复现；已按实际测量改正。
# 
OHLC_MIN_ROWS = 100           # 样本行数下限
OHLC_MIN_SATISFACTION = 0.95  # 不变量满足率下限
OHLC_MIN_SPREAD = 0.97        # 「high > low」占比下限（挡「4 列其实是同一列/近似列」）


def _assert_ohlc_invariant(
    key: str,
    quads: list[tuple[float, float, float, float]],
    *,
    parsed_rows: int = 0,
    first_col_ever_parsed: bool = True,
) -> None:
    """校验取到的 4 列真的是 open/high/low/close。

    这是本项目最重要的一条防线：它能拦住「列号整体错位」这类
    数值看起来完全合理的故障 —— 单看 close 列是发现不了的。

    取到的列若不满足 open ≤ high、low ≤ high、low ≤ close ≤ high，
    说明这 4 列不是一组 OHLC，立即拒绝，绝不用它算读数。
    """
    if len(quads) < OHLC_MIN_ROWS:
        # ⚠️ 这里曾经是一个**静默放行的洞**：50etf 的窗口是 (1,2,3,4)，
        # 整体左移一格后变成 (0,1,2,3) —— 第 0 列是**日期**，
        # float("2026/9/30") 全部失败 → quads 为空 → 函数直接 return，
        # 既不校验也不报错，于是 50etf 会继续用"最低价"算读数。
        # 它是 9 个品种里唯一窗口从第 1 列开始的，所以只有它踩到这个洞。
        # 正确的处理：解析不出四元组的行太多，本身就说明窗口指错了位置。
        if parsed_rows >= OHLC_MIN_ROWS and not first_col_ever_parsed:
            # 窗口首列在整个文件里**一次都没解析出数值** → 它指向的是一列
            # 纯文本（日期、标签），不是价格。这正是"窗口整体错位、首列吃掉
            # 日期字段"的形态，必须拒绝，绝不能因为"样本不足"而放行。
            #
            # 反之若首列解析成功过（只是本 key 稀疏，例如该品种大部分历史
            # 单元格是 #NUM!），那属于上游正常的稀疏特征，放行 —— 这与
            # tests/test_qvix_parse.py::test_assert_structure_accepts_sparse_wide_table
            # 的意图一致。
            raise DataQualityError(
                f"QVIX({key}) 列结构异常：窗口 {QVIX_OHLC[key]} 的首列"
                f"（第 {QVIX_OHLC[key][0]} 列）在全部 {parsed_rows} 个数据行里"
                "都没有一个可以解析成数值 —— 它指向的很可能不是价格列"
                "（例如日期列），说明列号已整体错位。已拒绝使用该数据。"
            )
        return  # 样本确实太少，不变量不具统计意义；交由行数断言处理

    arr = np.asarray(quads, dtype=float)
    open_, high, low, close = arr[:, 0], arr[:, 1], arr[:, 2], arr[:, 3]
    ok = (open_ <= high) & (low <= high) & (low <= close) & (close <= high)
    rate = float(ok.mean())
    spread = float((high > low).mean())

    if rate < OHLC_MIN_SATISFACTION or spread < OHLC_MIN_SPREAD:
        raise DataQualityError(
            f"QVIX({key}) 列结构不变量校验失败：取到的 4 列"
            f"（{QVIX_OHLC[key]}）有 {rate:.2%} 的行满足"
            f"「开≤高、低≤高、低≤收≤高」（下限 {OHLC_MIN_SATISFACTION:.0%}），"
            f"高>低 的占比 {spread:.2%}（下限 {OHLC_MIN_SPREAD:.0%}）。"
            "这通常意味着上游列结构发生变更、本项目的列号已整体错位。"
            "已拒绝使用该数据（宁可降级，也不用看起来正常的错值）。"
        )


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
# ⚠️ 请先读这段——这道防线曾经**完全失效**，而且我们还给它编了一个
#    听起来很合理的解释，把失效说成了"上游的行为"。
#
#    事故原貌：列号整体错位一格，取到的是**当日最低价**。缓存也是用同一个
#    错误映射写出来的，于是"新抓的"和"缓存的"是同一种错误，比值自然为 0，
#    这道防线一声不吭地放行了。当时观察到的 1%~2% 中位偏差、以及
#    "新值系统性略低（科创50 30.4 vs 31.37）"，被我们解释成
#    **"上游每次请求都会重算历史值"**。
#
#    真相（已推翻，有证据）：那个偏差与"重算"毫无关系，就是
#    **最低价 vs 收盘价**的差（科创50 当天 low=34.55、close=34.57；
#    两个中位数 30.4 与 31.37 的差正是 low/close 两列的系统性差）。
#    证据：相隔约 24 小时的两次下载**逐字节相同**——上游并不重算历史值。
#
#    教训：**用同一份有缺陷的实现生成"基准"，再去校验自己，是无效校验。**
#    真正拦住这类故障的是 assert_structure 里的 OHLC 结构不变量
#    （实测对 9/9 个错位窗口全部拒绝，对 9/9 个正确窗口全部放行）。
#
# 这道缓存交叉校验仍然保留，但它的定位已经降级为**辅助**：
#    它只能捕捉"新抓取与缓存不一致"的情形，对"两者同样错"无能为力。
#    阈值按实测标定：正常时两列本该几乎相同（缓存就是上次写入的同一份文件），
#    中位偏差理应接近 0。因此这里给一个宽松的上限，只拦量级明显不对的情况。
CROSS_CHECK_MIN_OVERLAP = 100
CROSS_CHECK_MAX_RATIO = 4.0          # 中位偏差 / 典型日度波动幅度，上限
CROSS_CHECK_ABS_MEDIAN_CAP = 8.0     # 中位绝对偏差的绝对上限（点）


def cross_check_cache(fresh: pd.DataFrame, cached: pd.DataFrame | None, key: str) -> None:
    """与本地缓存交叉校验。

    **辅助防线，不要依赖它**。它只能发现「新抓取与本地缓存不一致」，
    对「两边同样取错列」完全无效（缓存正是用同一份实现写出来的 —— 本项目
    真实事故中它就是这样静默放行的，见上方长注释）。

    真正拦得住列号偏移的是 _assert_ohlc_invariant。
    这里的判据是尺度感知的：中位绝对偏差不得超过典型日度波动幅度的若干倍，
    另加一个绝对上限，用来拦住量级明显不对的情况。
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
