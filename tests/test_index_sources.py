"""指数双源选择规则；模拟响应，不提交或请求行情数据。"""
from datetime import datetime
import json

import pandas as pd
import pytest

from techpanic.config import NetworkConfig, Target
from techpanic.fetch import index_daily as mod
from techpanic.fetch.http import FetchResponse

TARGET = Target("test", "测试指数", "sh000688", "kcb")


def prices(last="2026-10-08", close=100.0, rows=600):
    return pd.DataFrame({"date": pd.bdate_range(end=last, periods=rows), "close": close})


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr(mod, "beijing_now", lambda: datetime(2026, 10, 9, 16, tzinfo=mod.BEIJING))


def sources(monkeypatch, em, sina):
    calls = []
    def fetch(source, value):
        def wrapped(*args):
            calls.append(source)
            if isinstance(value, Exception):
                raise value
            return value
        return wrapped
    monkeypatch.setattr(mod, "_fetch_em", fetch("em", em))
    monkeypatch.setattr(mod, "_fetch_sina", fetch("sina", sina))
    return calls


@pytest.mark.parametrize("em_date,sina_date,expected", [
    ("2026-10-09", "2026-10-09", "em"),
    ("2026-10-08", "2026-10-09", "sina"),
    ("2026-10-09", "2026-10-08", "em"),
    ("2026-10-08", "2026-10-08", "em"),
])
def test_both_fetched_dates_then_em_priority(monkeypatch, em_date, sina_date, expected):
    calls = sources(monkeypatch, prices(em_date, 101), prices(sina_date, 102))
    res = mod.fetch_index(TARGET, NetworkConfig())
    assert calls == ["em", "sina"]
    assert res.source == expected
    assert res.frame.iloc[-1]["close"] == (101 if expected == "em" else 102)
    assert len(res.frame) == 600  # no mixing
    assert res.source_dates == {"em": em_date, "sina": sina_date}


def test_legacy_sina_order_still_uses_em_on_equal_date(monkeypatch):
    calls = sources(monkeypatch, prices(close=101), prices(close=102))
    res = mod.fetch_index(TARGET, NetworkConfig(source_index="sina"))
    assert calls == ["sina", "em"]
    assert res.source == "em"


@pytest.mark.parametrize("bad", [ValueError(), pd.DataFrame(), prices(rows=3),
                                  prices(close=0), prices(close=float("nan")),
                                  prices(close=float("inf")), prices("2026-10-12")])
def test_invalid_em_falls_back_to_sina(monkeypatch, bad):
    calls = sources(monkeypatch, bad, prices())
    res = mod.fetch_index(TARGET, NetworkConfig())
    assert calls == ["em", "sina"]
    assert res.source == "sina"
    assert "em" in res.source_errors


def test_sina_failure_does_not_discard_valid_em(monkeypatch):
    calls = sources(monkeypatch, prices(), RuntimeError("sina failed"))
    res = mod.fetch_index(TARGET, NetworkConfig())
    assert calls == ["em", "sina"]
    assert res.source == "em"
    assert "sina" in res.source_errors


def test_both_invalid_produce_no_frame(monkeypatch):
    calls = sources(monkeypatch, ValueError("bad em"), ValueError("bad sina"))
    res = mod.fetch_index(TARGET, NetworkConfig())
    assert calls == ["em", "sina"]
    assert res.frame is None and res.source == "none"
    assert set(res.source_errors) == {"em", "sina"}


def test_normalize_rejects_conflicting_date(monkeypatch):
    df = prices()
    df.loc[len(df)] = [df.iloc[-1]["date"], 99.0]
    sources(monkeypatch, df, prices())
    assert mod.fetch_index(TARGET, NetworkConfig()).source == "sina"


def test_intraday_row_is_not_used_as_final_close(monkeypatch):
    monkeypatch.setattr(mod, "beijing_now", lambda: datetime(2026, 10, 9, 14, tzinfo=mod.BEIJING))
    frame = mod._normalize(prices("2026-10-09"))
    assert frame["date"].iloc[-1].date().isoformat() == "2026-10-08"


def test_em_request_is_bounded_and_symbol_checked():
    class Client:
        def get_bytes(self, url, **kwargs):
            assert url.startswith("https://push2his.eastmoney.com/")
            assert kwargs["params"]["secid"] == "1.000688"
            assert kwargs["params"]["klt"] == "101"
            assert kwargs["total_budget"] == 30
            data = {"data": {"code": "000688", "klines": ["2026-10-09,99,100,101,98,2,3,4,5,6,7"]}}
            return FetchResponse(json.dumps(data).encode(), 200, 1, url)
    frame = mod._fetch_em(TARGET, Client(), 30)
    assert frame.iloc[-1]["close"] == "100"


def test_em_rejects_wrong_instrument():
    class Client:
        def get_bytes(self, url, **kwargs):
            body = {"data": {"code": "999999", "klines": ["2026-10-09,99,100,101,98,2,3,4,5,6,7"]}}
            return FetchResponse(json.dumps(body).encode(), 200, 1, url)
    with pytest.raises(ValueError, match="代码不匹配"):
        mod._fetch_em(TARGET, Client(), 30)

@pytest.mark.parametrize("zone", ["UTC", "Asia/Shanghai"])
def test_timezone_dates_from_sina_decoder_are_normalized(zone):
    frame = prices()
    frame["date"] = frame["date"].dt.tz_localize(zone)
    result = mod._normalize(frame)
    assert result["date"].dt.tz is None
    assert result["date"].iloc[-1].date().isoformat() == "2026-10-08"
