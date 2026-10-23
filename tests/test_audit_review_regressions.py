"""独立第二轮复审反例，合并为正式离线回归。"""

import hashlib
import json
import socket
from datetime import datetime

import pandas as pd
import pytest

from conftest import make_prices, make_qvix
from techpanic import pipeline, store
from techpanic.config import AppConfig, DEFAULT_TARGETS, load_config
from techpanic.evidence import EvidenceStore
from techpanic.fetch import index_daily, qvix as qvix_fetch
from techpanic.reference import validate_reference
from techpanic.study import evaluate_study, freeze_protocol
from test_study import study


@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Live network forbidden in review")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)


def seed(root, targets=DEFAULT_TARGETS[:2]):
    cfg = AppConfig(root, None, targets=targets, offline=True, as_of="2026-10-09")
    cfg.ensure_dirs()
    p = make_prices()
    p["date"] = pd.bdate_range(end="2026-10-09", periods=len(p))
    q = make_qvix(p["date"])
    for t in targets:
        store.atomic_write_csv(p, cfg.index_dir / (t.symbol + ".csv"))
        store.atomic_write_csv(q, cfg.qvix_dir / (t.qvix + ".csv"))
    return cfg, p, q


def fake_sources(monkeypatch, p, q, fail_qvix=False):
    monkeypatch.setattr(
        index_daily, "beijing_now", lambda: datetime.fromisoformat("2026-10-09T16:00:00+08:00")
    )
    monkeypatch.setattr(
        index_daily,
        "fetch_index",
        lambda t, net, **kw: index_daily.IndexFetchResult(
            t.symbol, p.copy(), "tx", source_dates={"tx": "2026-10-09"}
        ),
    )
    monkeypatch.setattr(
        qvix_fetch,
        "fetch_qvix",
        lambda keys, net, **kw: {
            k: qvix_fetch.QvixFetchResult(
                k,
                None if fail_qvix else q.copy(),
                "none" if fail_qvix else "optbbs",
                "synthetic timeout" if fail_qvix else None,
            )
            for k in keys
        },
    )


def ref_files(
    tmp_path, reference_date="2026-10-09", evidence_url="https://www.sse.com.cn/reference"
):
    meta = dict(
        instrument="sh000688",
        instrument_type="index",
        price_basis="price_index",
        unit="index_points",
        decimal_places=2,
        publisher="declared SSE",
        evidence_url=evidence_url,
        independent_from_vendor=True,
    )
    csv = tmp_path / "reference.csv"
    pd.DataFrame({"date": [reference_date], "close": [100.0]}).to_csv(csv, index=False)
    mp = tmp_path / "reference.json"
    mp.write_text(json.dumps(meta), encoding="utf-8")
    return csv, mp, meta


def refreeze(p):
    frozen = freeze_protocol(p)
    frozen["frozen_at"] = "2025-01-01T00:00:00+08:00"
    encoded = json.dumps(
        p, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()
    frozen["envelope_sha256"] = hashlib.sha256(encoded + frozen["frozen_at"].encode()).hexdigest()
    return frozen


def test_missing_requested_target_cannot_return_success(tmp_path):
    cfg, p, q = seed(tmp_path, targets=DEFAULT_TARGETS[:1])
    cfg = AppConfig(tmp_path, None, targets=DEFAULT_TARGETS[:2], offline=True, as_of="2026-10-09")
    r = pipeline.run(cfg)
    print("MISSING TARGET", r.exit_code, [t.target.key for t in r.targets], r.warnings)
    assert r.exit_code == 2


def test_failed_qvix_fetch_same_date_cache_cannot_return_success(tmp_path, monkeypatch):
    _, p, q = seed(tmp_path, targets=DEFAULT_TARGETS[:1])
    cfg = AppConfig(tmp_path, None, targets=DEFAULT_TARGETS[:1])
    fake_sources(monkeypatch, p, q, fail_qvix=True)
    r = pipeline.run(cfg)
    print("QVIX FAILURE", r.exit_code, r.targets[0].qvix_source, r.targets[0].degraded, r.warnings)
    assert r.exit_code == 2


def test_historical_run_does_not_overwrite_current_qvix_cache(tmp_path, monkeypatch):
    cfg, p, q = seed(tmp_path, targets=DEFAULT_TARGETS[:1])
    cfg = AppConfig(tmp_path, None, targets=DEFAULT_TARGETS[:1], as_of="2026-10-08", refresh=True)
    for t in cfg.targets:
        store.update_manifest(
            cfg.manifest_path,
            "index_" + t.symbol,
            rows=len(p),
            last_date="2026-10-09",
            source="tx",
            file_path=cfg.index_dir / (t.symbol + ".csv"),
        )
        store.update_manifest(
            cfg.manifest_path,
            "qvix_" + t.qvix,
            rows=len(q),
            last_date="2026-10-09",
            source="optbbs",
            file_path=cfg.qvix_dir / (t.qvix + ".csv"),
        )
    paths = [cfg.manifest_path, cfg.index_dir / "sh000688.csv", cfg.qvix_dir / "kcb.csv"]
    before = {path: path.read_bytes() for path in paths}
    # Valid stale upstream table ending at the historical date, one row behind cache.
    changed = q.iloc[:-1].copy()
    fake_sources(monkeypatch, p, changed)
    r = pipeline.run(cfg)
    mutations = [path.name for path in paths if path.read_bytes() != before[path]]
    print(
        "HISTORICAL CACHE MUTATIONS",
        mutations,
        "reading",
        str(r.targets[0].price_date.date()),
        "cache old rows",
        len(q),
        "cache new rows",
        len(pd.read_csv(cfg.qvix_dir / "kcb.csv")),
    )
    assert not mutations


def test_same_knowledge_different_market_cutoff_outputs_are_isolated(tmp_path):
    cfg1 = load_config(
        data_dir=tmp_path,
        overrides={"knowledge_as_of": "2026-10-09T17:00:00+08:00", "as_of": "2026-10-08"},
    )
    cfg2 = load_config(
        data_dir=tmp_path,
        overrides={"knowledge_as_of": "2026-10-09T17:00:00+08:00", "as_of": "2026-10-09"},
    )
    at = "2026-10-09T16:00:00+08:00"
    p = make_prices()
    p["date"] = pd.bdate_range(end="2026-10-09", periods=len(p))
    q = make_qvix(p["date"])
    with EvidenceStore(cfg1.evidence_dir) as e:
        raw = e.record_response(
            b"synthetic",
            url="https://example.com",
            final_url="https://example.com",
            status=200,
            headers={},
            params={},
            started_at=at,
            received_at=at,
        )
        for t in cfg1.targets:
            e.record_frame(
                "index_" + t.symbol, "tx", p, raw_ids=[raw], semantics={}, observed_at=at
            )
            e.record_frame(
                "qvix_" + t.qvix, "optbbs", q, raw_ids=[raw], semantics={}, observed_at=at
            )
    a = pipeline.run(cfg1)
    first_path = a.output_files[0]
    first = first_path.read_bytes()
    b = pipeline.run(cfg2)
    print(
        "REPLAY OUTPUT DIRS",
        str(cfg1.output_dir),
        str(cfg2.output_dir),
        "same target file",
        first_path == b.output_files[0],
        "first file overwritten",
        first != first_path.read_bytes(),
    )
    assert cfg1.output_dir != cfg2.output_dir


def test_intraday_rows_cannot_satisfy_minimum_study_sessions():
    d, frozen = study()
    mask = d["date"] >= frozen["protocol"]["holdout_start"]
    d.loc[mask, "date"] = pd.date_range("2025-03-03", periods=int(mask.sum()), freq="s")
    d.loc[mask, "available_at"] = "2025-03-03T15:30:00+08:00"
    d.loc[mask, "report_at"] = "2025-03-03T16:00:00+08:00"
    try:
        r = evaluate_study(d, frozen)
    except ValueError:
        return
    print(
        "INTRADAY STUDY",
        r["status"],
        r.get("holdout_sessions"),
        "actual unique days",
        d.loc[mask, "date"].dt.normalize().nunique(),
        r.get("episodes"),
    )
    assert r["status"] != "passed"


def test_unsupported_protocol_claim_cannot_pass():
    d, frozen = study()
    p = frozen["protocol"]
    p["schema"] = "not-a-study-schema"
    p["claim"] = "tomorrow_returns_prediction"
    r = evaluate_study(d, refreeze(p))
    print("UNSUPPORTED PROTOCOL", r["status"], r["claim"], r["scope"])
    assert r["status"] != "passed"


def test_invalid_protocol_source_cannot_pass():
    d, frozen = study()
    p = frozen["protocol"]
    p["registration_evidence_url"] = "not a URL"
    p["outcome"]["source"] = " "
    p["outcome"]["definition"] = " "
    p["baseline_definition"] = " "
    r = evaluate_study(d, refreeze(p))
    print("INVALID PROTOCOL SOURCE", r["status"], r["reasons"])
    assert r["status"] != "passed"


def test_same_time_utc_beijing_reference_agrees(tmp_path):
    csv, mp, meta = ref_files(tmp_path, reference_date="2026-10-09T15:00:00+08:00")
    d = pd.DataFrame({"date": ["2026-10-09T07:00:00Z"], "close": [100.0]})
    r = validate_reference(d, meta, csv, mp)
    print("SAME TIME REFERENCE", r["status"], r.get("rows"))
    assert r["status"] == "passed"


def test_invalid_reference_url_cannot_pass(tmp_path):
    csv, mp, meta = ref_files(tmp_path, evidence_url="javascript://www.sse.com.cn/reference")
    r = validate_reference(pd.DataFrame({"date": ["2026-10-09"], "close": [100.0]}), meta, csv, mp)
    print("INVALID REFERENCE URL", r["status"])
    assert r["status"] != "passed"


def test_same_time_utc_beijing_frame_market_dates_match(tmp_path):
    at = "2026-10-09T16:00:00+08:00"
    with EvidenceStore(tmp_path) as e:
        raw = e.record_response(
            b"synthetic",
            url="https://example.com",
            final_url="https://example.com",
            status=200,
            headers={},
            params={},
            started_at=at,
            received_at=at,
        )
        frames = []
        for src, date in [("tx", "2026-10-09T00:00:00+08:00"), ("em", "2026-10-08T16:00:00Z")]:
            e.record_frame(
                "index_sh000688",
                src,
                pd.DataFrame({"date": [date], "close": [100.0]}),
                raw_ids=[raw],
                semantics={},
                observed_at=at,
            )
            frame, meta = e.replay_frame("index_sh000688", at, src)
            frames.append(frame.iloc[0]["date"])
        print("SAME TIME EVIDENCE DATES", frames)
        assert frames[0] == frames[1]


def test_empty_archive_local_integrity_scope_control(tmp_path):
    with EvidenceStore(tmp_path) as e:
        r = e.verify_all()
    print("EMPTY ARCHIVE", r, "(control: scope is local integrity only, not presence of evidence)")
    assert (
        r["status"] == "passed" and r["scope"] == "local_integrity_only" and r["observations"] == 0
    )


def test_provided_later_close_time_prevents_intraday_current_reading(tmp_path, monkeypatch):
    _, p, q = seed(tmp_path, targets=DEFAULT_TARGETS[:1])
    calendar_path = tmp_path / "calendar.json"
    calendar_path.write_text(
        json.dumps(
            dict(
                timezone="Asia/Shanghai",
                coverage_start=str(p["date"].min().date()),
                coverage_end="2026-10-31",
                sessions=[str(x.date()) for x in p["date"]],
                close_time="15:30:00",
                provenance={"evidence_url": "https://example.com/synthetic"},
            )
        ),
        encoding="utf-8",
    )
    cfg = AppConfig(
        tmp_path, None, targets=DEFAULT_TARGETS[:1], offline=True, calendar_path=calendar_path
    )
    monkeypatch.setattr(
        index_daily, "beijing_now", lambda: datetime.fromisoformat("2026-10-09T15:15:00+08:00")
    )
    r = pipeline.run(cfg)
    t = r.targets[0]
    print(
        "CALENDAR LATER CLOSE",
        r.exit_code,
        str(t.price_date.date()),
        t.audit["calendar"]["expected_completed"],
        t.audit["freshness_price"],
    )
    assert t.price_date == pd.Timestamp("2026-10-08")


def test_timezone_instant_controls(tmp_path):
    from techpanic.evidence import timestamp
    from techpanic.sessions import load_calendar

    assert timestamp("2026-10-09T16:00:00+08:00") == timestamp("2026-10-09T08:00:00Z")
    path = tmp_path / "control-calendar.json"
    path.write_text(
        json.dumps(
            dict(
                timezone="Asia/Shanghai",
                coverage_start="2026-10-08",
                coverage_end="2026-10-09",
                sessions=["2026-10-08", "2026-10-09"],
                provenance={"evidence_url": "https://example.com/control"},
            )
        ),
        encoding="utf-8",
    )
    c = load_calendar(path)
    assert c.expected_completed(
        datetime.fromisoformat("2026-10-09T07:00:00Z")
    ) == c.expected_completed(datetime.fromisoformat("2026-10-09T15:00:00+08:00"))
    d, frozen = study()
    for col in ("available_at", "report_at"):
        d[col] = pd.to_datetime(d[col], utc=True).astype(str)
    assert evaluate_study(d, frozen)["status"] == "passed"


def test_audit_cli_rejects_malformed_protocol_source(tmp_path, capsys):
    from techpanic.audit_cli import main

    d, frozen = study()
    frozen["protocol"]["registration_evidence_url"] = "not a URL"
    frozen["protocol"]["outcome"]["source"] = " "
    frozen["protocol"]["outcome"]["definition"] = " "
    frozen["protocol"]["baseline_definition"] = " "
    frozen = refreeze(frozen["protocol"])
    data_path = tmp_path / "study.csv"
    protocol_path = tmp_path / "frozen.json"
    d.to_csv(data_path, index=False)
    protocol_path.write_text(json.dumps(frozen), encoding="utf-8")
    exit_code = main(["study", "--data", str(data_path), "--protocol", str(protocol_path)])
    result = json.loads(capsys.readouterr().out)
    print("AUDIT CLI MALFORMED SOURCE", exit_code, result["status"])
    assert exit_code != 0
