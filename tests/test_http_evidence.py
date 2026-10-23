import json
from datetime import datetime, timezone
import pandas as pd
import pytest
from techpanic.evidence import EvidenceStore
from techpanic.fetch.http import HttpClient


class Response:
    content = b'[{"date":"2026-10-09","close":100}]'
    status_code = 200
    headers = {"Content-Length": str(len(content)), "Set-Cookie": "not-saved"}
    url = "https://example.com/final"


def test_real_http_client_hook_records_entity_and_linked_frame(tmp_path, monkeypatch):
    with EvidenceStore(tmp_path) as evidence:
        with HttpClient(evidence_store=evidence, retries=1) as client:
            monkeypatch.setattr(client.session, "get", lambda *args, **kwargs: Response())
            response = client.get_bytes("https://example.com/quotes", params={"symbol": "test"})
            assert len(client.evidence_ids) == 1
            raw_id = client.evidence_ids[0]
            data, meta = evidence._read(raw_id)
            assert data == response.content and "Set-Cookie" not in meta["headers"]
            df = pd.DataFrame(json.loads(data))
            evidence.record_frame("test", "em", df, raw_ids=[raw_id], semantics={})
        f, m = evidence.replay_frame("test", datetime.now(timezone.utc).isoformat())
        assert f.iloc[0]["close"] == 100
        assert m["raw_ids"] == [raw_id]


def test_readonly_store_cannot_change_database(tmp_path):
    with EvidenceStore(tmp_path) as store:
        store.verify_all()
    path = tmp_path / "observations.sqlite3"
    before = path.read_bytes()
    with EvidenceStore(tmp_path, read_only=True) as store:
        assert store.verify_all()["observations"] == 0
        with pytest.raises(ValueError):
            store._object(b"x")
    assert path.read_bytes() == before
