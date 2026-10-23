import json
import pandas as pd
from techpanic.cli import main
from techpanic.config import load_config, with_targets_subset
from techpanic.evidence import EvidenceStore
from techpanic.pipeline import run
from conftest import make_prices


def test_default_no_archive_and_explicit_status(seeded_data_dir):
    cfg = with_targets_subset(
        load_config(data_dir=seeded_data_dir, overrides={"offline": True}), include_validation=False
    )
    result = run(cfg)
    assert not cfg.evidence_dir.exists()
    assert all(t.audit["construct_validity"] == "insufficient_evidence" for t in result.targets)
    assert all(t.audit["qvix_identity"] == "proxy_unverified" for t in result.targets)


def test_knowledge_replay_never_uses_current_cache(seeded_data_dir):
    cfg = with_targets_subset(
        load_config(
            data_dir=seeded_data_dir, overrides={"knowledge_as_of": "2026-10-09T17:00:00+08:00"}
        ),
        include_validation=False,
    )
    with EvidenceStore(cfg.evidence_dir) as store:
        for t in cfg.targets:
            p = make_prices()
            p["date"] = pd.bdate_range(end="2026-10-09", periods=len(p))
            raw = store.record_response(
                b"evidence",
                url="https://example.com",
                final_url="https://example.com",
                status=200,
                params={},
                headers={},
                started_at="2026-10-09T16:00:00+08:00",
                received_at="2026-10-09T16:00:00+08:00",
            )
            store.record_frame(
                "index_" + t.symbol,
                "tx",
                p,
                raw_ids=[raw],
                semantics={},
                observed_at="2026-10-09T16:01:00+08:00",
            )
    result = run(cfg)
    assert all(t.price_date.date().isoformat() == "2026-10-09" for t in result.targets)
    assert all(not t.has_full for t in result.targets)  # current QVIX cache prohibited
    assert all(t.audit["calculation_mode"] == "knowledge_as_of" for t in result.targets)


def test_audit_template_cli(capsys):
    assert main(["audit", "protocol-template"]) == 0
    template = json.loads(capsys.readouterr().out)
    assert template["outcome"]["independent_of_pi_qvix_returns"] is False


def test_replay_requires_evidence_not_silent_fallback(seeded_data_dir):
    assert (
        main(["--data-dir", str(seeded_data_dir), "--knowledge-as-of", "2026-10-09T17:00:00+08:00"])
        == 5
    )


def test_calculation_stops_before_latest_price_is_degraded(seeded_data_dir, monkeypatch):
    import numpy as np
    from techpanic import store
    from techpanic.fetch import index_daily
    from datetime import datetime

    cfg = with_targets_subset(
        load_config(data_dir=seeded_data_dir, overrides={"offline": True}), include_validation=False
    )
    p = make_prices()
    p["date"] = pd.bdate_range(end="2026-10-09", periods=len(p))
    # all down returns on final 30 sessions -> zero upper dispersion, current A/S undefined.
    p.loc[p.index[-30:], "close"] = p["close"].iloc[-31] * np.cumprod(
        1 - np.linspace(0.005, 0.02, 30)
    )
    for target in cfg.targets:
        store.atomic_write_csv(p, cfg.index_dir / f"{target.symbol}.csv")
    monkeypatch.setattr(
        index_daily, "beijing_now", lambda: datetime.fromisoformat("2026-10-09T16:00:00+08:00")
    )
    result = run(cfg)
    assert result.exit_code == 2
    assert all(t.price_date < p["date"].max() and t.degraded for t in result.targets)
    assert all(any("不能视作当前读数" in n for n in t.notes) for t in result.targets)


def test_offline_check_json_has_no_network(capsys, monkeypatch):
    from techpanic.fetch import index_daily

    def fail(*args):
        raise AssertionError("offline check must not query sources")

    monkeypatch.setattr(index_daily, "is_index_source_alive", fail)
    assert main(["--offline", "--check", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["financial_validation"] == "insufficient_evidence"
