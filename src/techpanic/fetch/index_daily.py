"""指数日线：新浪为主源，其它源仅作兜底。

实测（2026-10-09）：

* \u0060akshare.stock_zh_index_daily('sh000688')\u0060 → 0.84 秒、1637 行、末日期 2026-10-08，**可用**。
* 东方财富 \u0060push2his/push2\u0060 全线 RemoteDisconnected（0.14~6.10 秒内失败），**当前不可用**。
* \u0060hq.sinajs.cn\u0060 实时快照 0.1 秒可用（本模块不用它：它给的是当日快照，不是历史序列）。

因此默认 \u0060source_index = "sina"\u0060，只在失败时尝试 akshare 的其它封装。
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from ..config import NetworkConfig, Target
from ..errors import NetworkError

SINA_SYMBOLS = {
    "sh000688": "科创50",
    "sz399006": "创业板指",
    "sh000016": "上证50",
}


@dataclass
class IndexFetchResult:
    symbol: str
    frame: pd.DataFrame | None
    source: str
    error: str | None = None


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    """统一列名与类型：date / open / high / low / close / volume。"""
    out = df.copy()
    out.columns = [str(c).lstrip("\ufeff") for c in out.columns]
    rename = {
        "日期": "date", "开盘": "open", "最高": "high", "最低": "low",
        "收盘": "close", "成交量": "volume", "成交额": "amount",
        "date": "date", "open": "open", "high": "high", "low": "low",
        "close": "close", "volume": "volume",
    }
    out = out.rename(columns={k: v for k, v in rename.items() if k in out.columns})
    keep = [c for c in ("date", "open", "high", "low", "close", "volume") if c in out.columns]
    out = out[keep]
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    out = out.dropna(subset=["date", "close"])
    for col in ("open", "high", "low", "close", "volume"):
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    return out.sort_values("date").drop_duplicates("date").reset_index(drop=True)


def fetch_index(target: Target, net: NetworkConfig) -> IndexFetchResult:
    """抓取单个指数的完整历史日线。"""
    try:
        import akshare as ak
    except Exception as exc:  # noqa: BLE001
        raise NetworkError(
            "缺少 akshare 依赖，无法抓取指数数据。\n"
            f"   详细信息：{type(exc).__name__}: {exc}"
        ) from exc

    errors: list[str] = []
    order = ["sina", "em"]
    if net.source_index == "em":
        order = ["em", "sina"]

    for src in order:
        try:
            if src == "sina":
                raw = ak.stock_zh_index_daily(symbol=target.symbol)
            else:
                code = target.symbol[2:]
                raw = ak.index_zh_a_hist(symbol=code, period="daily", start_date="19900101")
            df = _normalize(raw)
            if len(df) == 0:
                raise ValueError("返回空表")
            return IndexFetchResult(target.symbol, df, src)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{src}: {type(exc).__name__} {str(exc)[:90]}")

    return IndexFetchResult(target.symbol, None, "none", "；".join(errors))


def is_index_source_alive(net: NetworkConfig) -> tuple[bool, str]:
    """轻量探活：只抓一个标的，用于启动时的友好提示。"""
    probe = Target("probe", "探活", "sh000688", "kcb")
    res = fetch_index(probe, net)
    if res.frame is None:
        return False, res.error or "未知错误"
    return True, f"{res.source} / {len(res.frame)} 行"
