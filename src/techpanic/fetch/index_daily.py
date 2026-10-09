"""指数日线双源：两源都请求，较新日期优先，同日优先东方财富。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
import json

import numpy as np
import pandas as pd

from ..config import NetworkConfig, Target
from .http import HttpClient

MIN_INDEX_ROWS = 500
BEIJING = timezone(timedelta(hours=8))
SOURCE_NAMES = {"em": "东方财富", "sina": "新浪"}


@dataclass
class IndexFetchResult:
    symbol: str
    frame: pd.DataFrame | None
    source: str
    error: str | None = None
    source_dates: dict[str, str] = field(default_factory=dict)
    source_errors: dict[str, str] = field(default_factory=dict)
    selection_reason: str = ""


def beijing_now() -> datetime:
    return datetime.now(BEIJING)


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    """拒绝无效日期/价格与不足样本；不拼接两源，不冒用盘中日线。"""
    out = df.copy()
    out.columns = [str(c).lstrip("\ufeff") for c in out.columns]
    out = out.rename(columns={"日期": "date", "开盘": "open", "最高": "high",
                              "最低": "low", "收盘": "close", "成交量": "volume"})
    if out.columns.duplicated().any() or not {"date", "close"}.issubset(out.columns):
        raise ValueError("缺少或重复 date/close 列")
    out = out[[c for c in ("date", "open", "high", "low", "close", "volume") if c in out]]
    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.tz_localize(None).dt.normalize()
    if out["date"].isna().any():
        raise ValueError("包含无效日线日期")
    for col in out.columns.drop("date"):
        out[col] = pd.to_numeric(out[col], errors="coerce")
    if not np.isfinite(out["close"]).all() or (out["close"] <= 0).any():
        raise ValueError("收盘价包含缺失、非有限值或非正数")
    if (out.groupby("date")["close"].nunique() > 1).any():
        raise ValueError("同一天存在冲突收盘价")
    now = beijing_now()
    today = pd.Timestamp(now.date())
    if (out["date"] > today).any():
        raise ValueError("包含未来日线日期")
    if now.time().replace(tzinfo=None) < time(15):
        out = out.loc[out["date"] < today]
    out = out.sort_values("date").drop_duplicates("date").reset_index(drop=True)
    if len(out) < MIN_INDEX_ROWS:
        raise ValueError(f"有效日线仅 {len(out)} 行，少于 {MIN_INDEX_ROWS} 行下限")
    return out


def _fetch_em(target: Target, client: HttpClient, budget: float) -> pd.DataFrame:
    if target.symbol[:2] not in {"sh", "sz"} or not target.symbol[2:].isdigit():
        raise ValueError("东方财富适配器需要 sh/sz + 指数代码")
    code = target.symbol[2:]
    market = "1" if target.symbol.startswith("sh") else "0"
    response = client.get_bytes(
        "https://push2his.eastmoney.com/api/qt/stock/kline/get",
        params={"secid": f"{market}.{code}", "klt": "101", "fqt": "0",
                "beg": "19900101", "end": beijing_now().strftime("%Y%m%d"), "lmt": "100000",
                "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"},
        headers={"Referer": "https://quote.eastmoney.com/"},
        total_budget=budget, progress_label=f"东方财富 {target.symbol}",
    )
    data = json.loads(response.content).get("data")
    if not isinstance(data, dict) or not data.get("klines"):
        raise ValueError("东方财富返回空日线")
    if str(data.get("code")) != code:
        raise ValueError("东方财富返回的标的代码不匹配")
    return pd.DataFrame([row.split(",") for row in data["klines"]], columns=[
        "date", "open", "close", "high", "low", "volume", "amount",
        "amplitude", "pct_change", "change", "turnover",
    ])


def _fetch_sina(target: Target, client: HttpClient, budget: float) -> pd.DataFrame:
    # 仅复用 AKShare 的公开新浪压缩历史格式/解码器；HTTP 由有超时的客户端处理。
    from akshare.index.cons import zh_sina_index_stock_hist_url
    from akshare.stock.cons import hk_js_decode
    import py_mini_racer

    response = client.get_text(
        zh_sina_index_stock_hist_url.format(target.symbol), params={"d": "2020_2_4"},
        total_budget=budget, progress_label=f"新浪 {target.symbol}",
    )
    encoded = response.split("=", 1)[1].split(";", 1)[0].replace('"', "").strip()
    with py_mini_racer.MiniRacer() as context:
        context.eval(hk_js_decode)
        rows = context.call("d", encoded)
    return pd.DataFrame(rows)


def fetch_index(target: Target, net: NetworkConfig) -> IndexFetchResult:
    """无论第一源成功与否都抓两源；截止日较新者优先，同日使用东方财富。"""
    frames: dict[str, pd.DataFrame] = {}
    dates: dict[str, str] = {}
    errors: dict[str, str] = {}
    client = HttpClient(timeout_connect=net.timeout_connect, timeout_read=net.timeout_read,
                        retries=net.retries, backoff=net.backoff, jitter=net.jitter,
                        proxy=net.proxy, user_agent=net.user_agent)
    budget = (net.timeout_connect + net.timeout_read) * max(net.retries, 1) + sum(net.backoff)
    # 兼容旧配置：source_index 仅决定请求先后，不能覆盖日期/同日东方财富优先规则。
    order = ("sina", "em") if net.source_index == "sina" else ("em", "sina")
    try:
        for source in order:
            try:
                raw = (_fetch_em if source == "em" else _fetch_sina)(target, client, budget)
                frame = _normalize(raw)
                frames[source] = frame
                dates[source] = frame["date"].iloc[-1].date().isoformat()
            except Exception as exc:  # noqa: BLE001
                detail = " ".join(str(exc).splitlines())[:240] or "无详细消息"
                errors[source] = f"{type(exc).__name__}: {detail}"
    finally:
        client.close()
    if not frames:
        return IndexFetchResult(target.symbol, None, "none", "；".join(
            f"{SOURCE_NAMES[s]}：{error}" for s, error in errors.items()), dates, errors)
    source = max(frames, key=lambda s: (dates[s], s == "em"))
    if len(frames) == 2:
        reason = ("新浪截止日期更新，采用新浪" if source == "sina" else
                  "东方财富截止日期不落后，优先采用东方财富")
    else:
        reason = f"另一源不可用，采用{SOURCE_NAMES[source]}"
    return IndexFetchResult(target.symbol, frames[source], source, None, dates, errors, reason)


def is_index_source_alive(net: NetworkConfig) -> tuple[bool, str]:
    res = fetch_index(Target("probe", "探活", "sh000688", "kcb"), net)
    return (False, res.error or "两源均不可用") if res.frame is None else (
        True, f"{res.source} / {len(res.frame)} 行；{res.selection_reason}")
