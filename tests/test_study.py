import hashlib
import json
import numpy as np
import pandas as pd
import pytest
from techpanic.study import protocol_template, freeze_protocol, evaluate_study, average_precision


def study():
    p = protocol_template()
    p.update(
        registered_at="2025-01-01T00:00:00+08:00",
        registration_evidence_url="https://example.com/preregistered",
        calibration_end="2025-02-28",
        holdout_start="2025-03-01",
        minimum_sessions=100,
        minimum_episodes=10,
        bootstrap_replicates=100,
        baseline_definition="fixed baseline",
    )
    p["outcome"] = {
        "source": "independent synthetic flow",
        "definition": "synthetic target",
        "independent_of_pi_qvix_returns": True,
    }
    dates = pd.bdate_range("2025-01-02", periods=260)
    y = (np.arange(260) % 5 == 0).astype(int)
    d = pd.DataFrame(
        {
            "date": dates,
            "pi": y * 100.0,
            "baseline": np.zeros(260),
            "target": y,
            "episode": np.arange(260),
            "report_at": [str(x.date()) + "T16:00:00+08:00" for x in dates],
            "available_at": [str(x.date()) + "T15:01:00+08:00" for x in dates],
        }
    )
    frozen = freeze_protocol(p)
    frozen["frozen_at"] = (
        "2025-01-01T00:00:00+08:00"  # synthetic known historical registration, not real study evidence
    )
    content = json.dumps(
        frozen["protocol"],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    frozen["envelope_sha256"] = hashlib.sha256(content + frozen["frozen_at"].encode()).hexdigest()
    return d, frozen


def test_tied_average_precision_not_order_dependent():
    assert average_precision([1, 0], [1.0, 1.0]) == 0.5
    assert average_precision([0, 1], [1.0, 1.0]) == 0.5


def test_study_protocol_and_independent_holdout():
    d, p = study()
    r = evaluate_study(d, p)
    assert r["status"] == "passed"
    assert r["metrics"]["delta_ap"] > 0.05 and r["metrics"]["paired_block_ci95"][0] > 0
    assert r["scope"].startswith("provided")


def test_missing_independent_evidence_is_inconclusive():
    assert (
        evaluate_study(pd.DataFrame(), freeze_protocol(protocol_template()))["status"]
        == "insufficient_evidence"
    )
    d, p = study()
    p["protocol"]["minimum_episodes"] = 10000
    p = freeze_protocol(p["protocol"])
    p["frozen_at"] = "2025-01-01T00:00:00+08:00"
    content = json.dumps(
        p["protocol"], ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()
    p["envelope_sha256"] = hashlib.sha256(content + p["frozen_at"].encode()).hexdigest()
    assert evaluate_study(d, p)["status"] == "insufficient_evidence"


def test_protocol_tamper_and_future_availability():
    d, p = study()
    p["protocol"]["seed"] = 7
    with pytest.raises(ValueError):
        evaluate_study(d, p)
    d, p = study()
    d.loc[0, "available_at"] = "2025-01-03T16:01:00+08:00"
    assert evaluate_study(d, p)["status"] == "failed"


def test_one_continuous_episode_cannot_be_split():
    d, p = study()
    mask = d["date"] >= p["protocol"]["holdout_start"]
    d.loc[mask, "target"] = 1
    result = evaluate_study(d, p)
    assert result["status"] == "failed"
    assert "不可拆分" in result["reasons"][0]


def test_post_holdout_calibration_availability_is_rejected():
    d, p = study()
    d.loc[0, "report_at"] = "2025-04-01T16:00:00+08:00"
    d.loc[0, "available_at"] = "2025-04-01T15:00:00+08:00"
    assert evaluate_study(d, p)["status"] == "failed"


def test_real_freeze_after_old_holdout_cannot_pass():
    d, p = study()
    p = freeze_protocol(p["protocol"])
    assert evaluate_study(d, p)["status"] == "failed"


def test_frozen_time_edit_is_detected():
    d, p = study()
    p["frozen_at"] = "2024-01-01T00:00:00+08:00"
    with pytest.raises(ValueError, match="冻结时间"):
        evaluate_study(d, p)


def test_registration_timezone_converted_not_stripped():
    d, p = study()
    p["protocol"]["registered_at"] = "2025-02-28T23:30:00-12:00"
    p = freeze_protocol(p["protocol"])
    assert evaluate_study(d, p)["status"] == "failed"


def test_same_day_morning_cannot_be_final_close():
    d, p = study()
    d.loc[0, "available_at"] = "2025-01-02T08:00:00+08:00"
    d.loc[0, "report_at"] = "2025-01-02T09:00:00+08:00"
    assert evaluate_study(d, p)["status"] == "failed"


def test_false_alarm_budget_is_real_pass_gate():
    d, p = study()
    hold = d["date"] >= p["protocol"]["holdout_start"]
    d.loc[hold & d["target"].eq(0), "pi"] = 50
    assert evaluate_study(d, p)["status"] == "failed"


def test_oversized_bootstrap_block_is_insufficient():
    d, p = study()
    p["protocol"]["block_sessions"] = 500
    p = freeze_protocol(p["protocol"])
    p["frozen_at"] = "2025-01-01T00:00:00+08:00"
    encoded = json.dumps(
        p["protocol"], ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()
    p["envelope_sha256"] = hashlib.sha256(encoded + p["frozen_at"].encode()).hexdigest()
    assert evaluate_study(d, p)["status"] == "insufficient_evidence"
