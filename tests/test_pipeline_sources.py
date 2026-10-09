"""双源流水线：不被指数 TTL 阻挡，输出选择依据与准确日期。"""
from datetime import datetime

import pandas as pd

from techpanic import pipeline, report, store
from techpanic.config import load_config, with_targets_subset
from techpanic.fetch import index_daily
from conftest import make_prices


def setup_fetch(monkeypatch, cfg, last="2026-10-09", fail=False):
    frame = make_prices()
    frame["date"] = pd.bdate_range(end=last, periods=len(frame))
    calls = []
    def fake(target, net):
        calls.append(target.symbol)
        if fail:
            return index_daily.IndexFetchResult(target.symbol, None, "none", "both failed",
                                                source_errors={"em": "em failed", "sina": "sina failed"})
        return index_daily.IndexFetchResult(target.symbol, frame, "sina", source_dates={
            "em": "2026-10-08", "sina": last}, selection_reason="新浪截止日期更新，采用新浪")
    monkeypatch.setattr(index_daily, "fetch_index", fake)
    monkeypatch.setattr(index_daily, "beijing_now", lambda: datetime(2026, 10, 9, 16, tzinfo=index_daily.BEIJING))
    monkeypatch.setattr(pipeline.qvix_fetch, "fetch_qvix", lambda *a, **kw: {})
    return calls


def test_recent_manifest_does_not_skip_dual_fetch(monkeypatch, seeded_data_dir):
    cfg = with_targets_subset(load_config(data_dir=seeded_data_dir), include_validation=False)
    for target in cfg.targets:
        path = cfg.index_dir / f"{target.symbol}.csv"
        store.update_manifest(cfg.manifest_path, f"index_{target.symbol}", rows=900,
                              last_date="2023-06-14", source="sina", file_path=path)
    calls = setup_fetch(monkeypatch, cfg)
    messages = []
    result = pipeline.run(cfg, say=messages.append)
    assert calls == ["sh000688", "sz399006"]
    assert all(t.price_date.date().isoformat() == "2026-10-09" for t in result.targets)
    assert any("东方财富 返回有效日线，截至 2026-10-08" in m for m in messages)
    assert any("新浪截止日期更新" in m for m in messages)
    payload = report.build_payload(result.targets, cfg, result.exit_code)
    for item in payload["results"]:
        assert item["price_only"]["source"] == "sina"
        assert item["index_data"]["source_dates"] == {"em": "2026-10-08", "sina": "2026-10-09"}


def test_old_online_data_is_warned_not_called_latest(monkeypatch, seeded_data_dir):
    cfg = with_targets_subset(load_config(data_dir=seeded_data_dir), include_validation=False)
    setup_fetch(monkeypatch, cfg, last="2026-10-08")
    result = pipeline.run(cfg)
    assert result.exit_code == 2
    assert all(any("尚未取得今天日线" in n for n in t.notes) for t in result.targets)


def test_both_failed_fall_back_to_cache_with_notes(monkeypatch, seeded_data_dir):
    cfg = with_targets_subset(load_config(data_dir=seeded_data_dir), include_validation=False)
    calls = setup_fetch(monkeypatch, cfg, fail=True)
    result = pipeline.run(cfg)
    assert len(calls) == 2
    assert result.exit_code == 2
    assert all(t.index_source == "cache" for t in result.targets)
    assert all(any("三源均不可用" in n for n in t.notes) for t in result.targets)


def test_offline_never_calls_sources(monkeypatch, seeded_data_dir):
    cfg = with_targets_subset(load_config(data_dir=seeded_data_dir, overrides={"offline": True}),
                              include_validation=False)
    calls = setup_fetch(monkeypatch, cfg)
    messages = []
    pipeline.run(cfg, say=messages.append)
    assert calls == []
    assert any("离线模式，使用本地缓存" in m for m in messages)
    assert not any("抓取失败" in m for m in messages)
