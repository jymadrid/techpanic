from datetime import datetime
import json
import pandas as pd
import pytest
from techpanic.sessions import load_calendar


def calendar(tmp_path):
    path = tmp_path / "calendar.json"
    path.write_text(
        json.dumps(
            {
                "timezone": "Asia/Shanghai",
                "coverage_start": "2026-10-01",
                "coverage_end": "2026-10-31",
                "sessions": ["2026-10-08", "2026-10-09", "2026-10-10", "2026-10-12"],
                "provenance": {"evidence_url": "https://example.com/synthetic-calendar"},
            }
        )
    )
    return load_calendar(path)


def test_cutoff_holiday_weekend_and_bounds(tmp_path):
    c = calendar(tmp_path)
    assert c.expected_completed(
        datetime.fromisoformat("2026-10-09T14:59:00+08:00")
    ) == pd.Timestamp("2026-10-08")
    assert c.expected_completed(
        datetime.fromisoformat("2026-10-09T15:00:00+08:00")
    ) == pd.Timestamp("2026-10-09")
    assert c.expected_completed(
        datetime.fromisoformat("2026-10-11T16:00:00+08:00")
    ) == pd.Timestamp("2026-10-10")
    assert c.expected_completed(datetime.fromisoformat("2026-11-01T16:00:00+08:00")) is None
    assert c.stale_sessions("2026-10-08", datetime.fromisoformat("2026-10-12T16:00:00+08:00")) == 3
    with pytest.raises(ValueError):
        c.expected_completed(datetime(2026, 10, 12))
    assert c.provenance["verification"] == "provided_not_authenticated"


def test_missing_sessions_not_weekdays(tmp_path):
    c = calendar(tmp_path)
    assert c.missing_sessions(pd.to_datetime(["2026-10-08", "2026-10-12"])) == list(
        pd.to_datetime(["2026-10-09", "2026-10-10"])
    )
