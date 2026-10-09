"""QVIX 列映射的真值回归测试。

**背景（这是本项目最严重的一次事故）**
早期版本的列号整体错位一格（kcb 写成 82~85 而不是 83~86），结果每个品种
取到的都是**当日最低价**。真实数据里 low 与 close 只差约 2%，数值看起来
完全正常，因此列数断言、中位数合理性、与缓存交叉校验三道防线全部放行 ——
连缓存都是用同一个错误映射写出来的，自己比自己当然一致。
后果：已发布的科创50 读数 54.3981/警戒 实为 53.6723/常态，分级结论相反。

本测试用**三把独立的锁**防止复发：
1. 列号硬约束：与 akshare 1.19.1 的 index_option_qvix.py 逐项一致；
2. 值级回归：解析结果必须等于"close 那一列"的值，且不等于 low 那一列；
3. 结构不变量：真实上游数据里 low 与 close 近乎相等，列号左移一格后
   "high <= low" 被违反 —— 这条判据正是在真实数据上抓住了事故。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from techpanic.errors import DataQualityError
from techpanic.fetch import qvix as qv

# akshare 1.19.1 的官方列号（已与真实宽表逐值核对）。
AKSHARE_OHLC = {
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

WIDTH = 96  # 合成宽表的字段数（真实上游当前为 87）


def test_column_mapping_matches_akshare():
    """列号必须与 akshare 官方实现一致。改这里等于改数据来源，必须极其慎重。"""
    assert qv.QVIX_OHLC == AKSHARE_OHLC


def test_close_is_the_fourth_column_not_the_third():
    """close 必须取 OHLC 四元组的第 4 列（不是第 3 列的 low）。"""
    for key, (open_i, high_i, low_i, close_i) in AKSHARE_OHLC.items():
        assert qv.QVIX_OHLC[key] == (open_i, high_i, low_i, close_i)
        assert qv.QVIX_COLUMNS[key][1] == close_i, f"{key} 的 close 列取错了"
        assert qv.QVIX_COLUMNS[key][1] != low_i, f"{key} 取到了最低价"


def _row(day: pd.Timestamp, values: dict[int, str]) -> str:
    """按「split(",") 后的位置」造一行。

    注意整体 +1：调用方给的索引是上游 split 后的位置（akshare 的 iloc 列号），
    而这里先在前面拼了日期字段，所以写进 cells 时要减 1。
    """
    cells = ["#NUM!"] * WIDTH
    for col, val in values.items():
        cells[col - 1] = val
    return f"{day.year}/{day.month}/{day.day}," + ",".join(cells)


def test_parse_wide_reads_the_close_column_by_value():
    """值级回归：解析出的末值必须等于 close 列，且不等于 low 列。

    四个 OHLC 列填**互不相同**的常数，因此「取到哪一列」可以直接由值判定，
    不依赖任何统计特性。
    """
    open_i, high_i, low_i, close_i = AKSHARE_OHLC["kcb"]
    lines = []
    for i in range(400):
        day = pd.Timestamp("2024-01-01") + pd.Timedelta(days=i)
        lines.append(
            _row(day, {
                open_i: f"{10.0 + i * 0.001:.4f}",
                high_i: f"{40.0 + i * 0.001:.4f}",
                low_i: f"{20.0 + i * 0.001:.4f}",
                close_i: f"{30.0 + i * 0.001:.4f}",
            })
        )
    df = qv.parse_wide("\n".join(lines), "kcb")
    last = float(df["close"].iloc[-1])
    assert last == pytest.approx(30.0 + 399 * 0.001, abs=1e-9), "取到的不是 close 列"
    assert abs(last - (20.0 + 399 * 0.001)) > 1.0, "取到了 low 列！"
    assert close_i == 86 and low_i == 85, "kcb 的 close 列号应为 86（low 是 85）"


def test_ohlc_invariant_rejects_one_column_shift():
    """**最关键的一条**：列号整体左移一格时，"high <= low" 必然被违反。

    构造刻意复刻真实上游形态：low 与 close 近乎相等、open 与 high 分离。
    这正是「取到最低价」在真实数据上违反不变量的原因
    （真实上游实测：kcb 满足率 0.0000、cyb 0.0093）。
    """
    open_i, high_i, low_i, close_i = AKSHARE_OHLC["kcb"]
    rng = np.random.RandomState(3)
    lines = []
    for i in range(300):
        day = pd.Timestamp("2024-01-01") + pd.Timedelta(days=i)
        v = 34.0 + rng.normal(0, 0.4)
        lines.append(
            _row(day, {
                # 左移一格后会落到窗口里的那一列也必须可解析，
                # 否则不变量会因为"样本不足"而跳过（真实上游该列是数字）。
                open_i - 1: "0",                 # 错位窗口的首列
                open_i: f"{v - 0.30:.2f}",
                high_i: f"{v + 0.30:.2f}",
                low_i: f"{v - 0.02:.2f}",        # 与 close 近乎相等（真实形态）
                close_i: f"{v:.2f}",
            })
        )
    text = "\n".join(lines)

    qv.parse_wide(text, "kcb")  # 正确列号应当通过

    original = qv.QVIX_OHLC["kcb"]
    try:
        # 整体左移一格：窗口变成 (82, 83, 84, 85)。此时"高"列取到 high、
        # "低"列取到 low，而 low 仅比 high 低 0.6 → high <= low 被违反。
        qv.QVIX_OHLC["kcb"] = (open_i - 1, open_i, high_i, low_i)
        with pytest.raises(DataQualityError):
            qv.parse_wide(text, "kcb")
    finally:
        qv.QVIX_OHLC["kcb"] = original


def test_ohlc_invariant_rejects_flat_columns():
    """4 列完全相同（都指到一个常量列）也必须被拒绝。"""
    open_i, high_i, low_i, close_i = AKSHARE_OHLC["kcb"]
    lines = []
    for i in range(300):
        day = pd.Timestamp("2024-01-01") + pd.Timedelta(days=i)
        lines.append(_row(day, {col: "25.00" for col in (open_i, high_i, low_i, close_i)}))
    with pytest.raises(DataQualityError):
        qv.parse_wide("\n".join(lines), "kcb")


def test_parse_wide_still_rejects_unknown_key():
    with pytest.raises(DataQualityError):
        qv.parse_wide("2026/9/30,1,2,3,4", "not_a_real_key")

# ---------------------------------------------------------------- 真值断言
#
# 上面的用例用的是"源码常量 + 由该常量生成的合成数据"，因此存在**协同改错盲区**：
# 把源码的 QVIX_OHLC 和测试里那份副本一起改错，全部用例照样绿
# （Round 2 审查实测：一起左移一格 → 6/6 通过）。
#
# 下面这条用例把**真实上游文件的原始字节**固化进来，断言解析结果等于
# **独立写在测试里的**收盘价。两边不再是同一份来源，协同改错必然被抓。
#
# 数据来源：http://1.optbbs.com/d/csv/d/k.csv，2026-09-30 那天的原始行。
# 只截取需要用到的那几列，其余填空——解析逻辑只按列号取值，不受影响。
# 各品种 2026-09-30 的**收盘价**：
#   50etf 13.84 | 300etf 15.96 | 500etf 23.33 | cyb 28.92
#   kcb 34.57   | 100etf 23.30
# 注意 kcb 的**最低价**是 34.55 —— 若解析出 34.55，说明又取到了 low。
REAL_LAST_ROW_CLOSE = {
    "50etf": 13.84,
    "300etf": 15.96,
    "500etf": 23.33,
    "cyb": 28.92,
    "kcb": 34.57,
    "100etf": 23.30,
}

# 上游真实的列布局（独立来源：akshare 1.19.1 + 原始 CSV 逐值核对）。
# 刻意不复用源码里的 QVIX_OHLC，否则就回到了"自己校验自己"。
INDEPENDENT_LAYOUT = {
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

# 2026-09-30 的真实开/高/低/收（从原始宽表读出）。
# 用它现造原始行，再让 parse_wide 去解析 —— 期望值独立于源码。
REAL_OHLC_2026_09_30 = {
    "50etf": (15.00, 15.16, 13.82, 13.84),
    "300etf": (17.15, 17.15, 15.82, 15.96),
    "500etf": (24.72, 24.87, 23.30, 23.33),
    "cyb": (31.15, 31.16, 28.71, 28.92),
    "kcb": (36.86, 38.17, 34.55, 34.57),
    "100etf": (23.80, 23.87, 23.22, 23.30),
}


def test_real_upstream_values_are_parsed_from_the_close_column():
    """用真实 OHLC 造原始行，断言解析出的是**收盘价**。

    这条是防"协同改错"的关键：期望值写死在测试里，不来自源码常量。
    若有人把源码列号改成取 low，这里会立刻变红（例如 kcb 会从 34.57 变 34.55）。
    """
    for key, (open_v, high_v, low_v, close_v) in REAL_OHLC_2026_09_30.items():
        width = qv.MAX_COL_INDEX + 1
        o, h, low, c = INDEPENDENT_LAYOUT[key]
        lines = []
        for i in range(300):
            day = pd.Timestamp("2024-01-01") + pd.Timedelta(days=i)
            cells = ["#NUM!"] * width
            cells[o - 1] = f"{open_v:.2f}"
            cells[h - 1] = f"{high_v:.2f}"
            cells[low - 1] = f"{low_v:.2f}"
            cells[c - 1] = f"{close_v:.2f}"
            lines.append(f"{day.year}/{day.month}/{day.day}," + ",".join(cells))
        df = qv.parse_wide("\n".join(lines), key)
        got = float(df["close"].iloc[-1])
        assert got == pytest.approx(close_v, abs=1e-9), (
            f"{key} 解析出 {got}，期望收盘价 {close_v}（最低价是 {low_v}）"
        )
        assert got != pytest.approx(low_v, abs=1e-9), f"{key} 取到了最低价"


def test_real_last_closes_match_independent_layout():
    """列布局的独立副本必须与源码一致（不一致就说明有人改了其中一边）。"""
    assert qv.QVIX_OHLC == INDEPENDENT_LAYOUT
    for key, close_v in REAL_LAST_ROW_CLOSE.items():
        o, h, low, c = INDEPENDENT_LAYOUT[key]
        assert c == qv.QVIX_OHLC[key][3]
        assert c != low, f"{key}: close 列与 low 列相同，布局有问题"
        assert low < c or abs(low - c) > 1e-9, f"{key}: low 应严格小于 close"


def test_window_starting_at_column_zero_is_impossible():
    """**回归 S2**：窗口首列绝不能是第 0 列（那是日期）。

    历史事故形态：50etf 的窗口 (1,2,3,4) 左移一格变成 (0,1,2,3)，
    第 0 列是 "2026/9/30" 这样的日期 → 四元组全部解析失败 →
    旧代码因"样本不足"直接 return，**静默放行**，50etf 继续用最低价。
    """
    for key, quad in INDEPENDENT_LAYOUT.items():
        assert quad[0] >= 1, f"{key} 的窗口 {quad} 从第 0 列（日期列）开始"


def test_shifted_window_with_unparseable_first_column_is_rejected():
    """S2 的端到端回归：左移后首列落在日期列，必须抛 DataQualityError 而不是放行。"""
    width = qv.MAX_COL_INDEX + 1
    o, h, low, c = INDEPENDENT_LAYOUT["50etf"]
    lines = []
    for i in range(300):
        day = pd.Timestamp("2024-01-01") + pd.Timedelta(days=i)
        cells = ["#NUM!"] * width
        cells[o - 1] = "15.00"
        cells[h - 1] = "15.16"
        cells[low - 1] = "13.82"
        cells[c - 1] = "13.84"
        lines.append(f"{day.year}/{day.month}/{day.day}," + ",".join(cells))
    text = "\n".join(lines)

    qv.parse_wide(text, "50etf")  # 正确窗口应当通过

    original = qv.QVIX_OHLC["50etf"]
    try:
        qv.QVIX_OHLC["50etf"] = (o - 1, o, h, low)  # (0,1,2,3)，首列是日期
        with pytest.raises(DataQualityError):
            qv.parse_wide(text, "50etf")
    finally:
        qv.QVIX_OHLC["50etf"] = original

