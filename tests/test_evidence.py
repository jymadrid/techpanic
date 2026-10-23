import sqlite3
import pandas as pd
import pytest
from techpanic.evidence import EvidenceStore


def raw(store, at="2026-10-09T16:00:00+08:00"):
    return store.record_response(
        b"raw public data",
        url="https://example.com/quotes",
        final_url="https://example.com/quotes",
        status=200,
        headers={},
        params={},
        started_at=at,
        received_at=at,
    )


def frame():
    return pd.DataFrame(
        {"date": pd.to_datetime(["2026-10-08", "2026-10-09"]), "close": [100.0, 101.0]}
    )


def test_versions_replay_only_observed_before_time(tmp_path):
    with EvidenceStore(tmp_path) as store:
        ident = raw(store)
        v = store.record_frame(
            "index_test",
            "tx",
            frame(),
            raw_ids=[ident],
            semantics={"instrument": "test"},
            observed_at="2026-10-09T16:00:01+08:00",
        )
        assert store.replay_frame("index_test", "2026-10-09T16:00:00+08:00")[0] is None
        f, m = store.replay_frame("index_test", "2026-10-09T16:01:00+08:00")
        assert len(f) == 2 and m["vintage_id"] == v
        revised = frame()
        revised.loc[1, "close"] = 102.0
        store.record_frame(
            "index_test",
            "tx",
            revised,
            raw_ids=[ident],
            semantics={},
            observed_at="2026-10-10T16:00:01+08:00",
        )
        assert (
            store.replay_frame("index_test", "2026-10-09T17:00:00+08:00")[0].iloc[-1]["close"]
            == 101.0
        )
        assert (
            store.replay_frame("index_test", "2026-10-10T17:00:00+08:00")[0].iloc[-1]["close"]
            == 102.0
        )
        assert store.verify_all()["observations"] == 3


def test_tamper_and_append_only(tmp_path):
    with EvidenceStore(tmp_path) as store:
        raw(store)
        with pytest.raises(sqlite3.IntegrityError):
            store.db.execute("DELETE FROM observations")
        path = next((tmp_path / "objects").iterdir())
        path.write_bytes(b"tampered")
        with pytest.raises(ValueError, match="篡改"):
            store.verify_all()


def test_invalid_availability_and_credentials(tmp_path):
    with EvidenceStore(tmp_path) as store:
        ident = raw(store)
        with pytest.raises(ValueError):
            store.record_frame(
                "index",
                "tx",
                frame(),
                raw_ids=[ident],
                semantics={},
                observed_at="2026-10-09T15:00:00+08:00",
            )
        with pytest.raises(ValueError):
            store.record_frame("index", "tx", frame(), raw_ids=[], semantics={})
        with pytest.raises(ValueError):
            raw(store, "2026-10-09T16:00:00")
        with pytest.raises(ValueError):
            store.record_response(
                b"x",
                url="https://example.com/?token=secret",
                final_url="https://example.com/",
                status=200,
                headers={},
                params={},
                started_at="2026-10-09T16:00:00+08:00",
                received_at="2026-10-09T16:00:00+08:00",
            )


def test_empty_version_rejected(tmp_path):
    with EvidenceStore(tmp_path) as store:
        ident = raw(store)
        with pytest.raises(ValueError, match="空"):
            store.record_frame(
                "index",
                "tx",
                pd.DataFrame(columns=["date", "close"]),
                raw_ids=[ident],
                semantics={},
            )
