import json
import pandas as pd
from techpanic.reference import validate_reference


def files(tmp_path, close=100.0):
    frame = pd.DataFrame({"date": ["2026-10-09"], "close": [close]})
    path = tmp_path / "reference.csv"
    frame.to_csv(path, index=False)
    meta = {
        "instrument": "sh000688",
        "instrument_type": "index",
        "price_basis": "price_index",
        "unit": "index_points",
        "decimal_places": 2,
        "publisher": "declared SSE",
        "evidence_url": "https://www.sse.com.cn/reference",
        "independent_from_vendor": True,
    }
    mp = tmp_path / "reference-meta.json"
    mp.write_text(json.dumps(meta))
    return frame, path, mp, meta


def test_reference_agreement_scope_not_authentication(tmp_path):
    frame, path, mp, meta = files(tmp_path)
    result = validate_reference(frame, meta, path, mp)
    assert result["status"] == "passed"
    assert result["official_authenticity"] == "not_independently_authenticated"
    assert result["tolerance"] == 0.01


def test_mismatch_identity_and_values(tmp_path):
    frame, path, mp, meta = files(tmp_path)
    frame.loc[0, "close"] = 101.0
    assert validate_reference(frame, meta, path, mp)["status"] == "failed"
    meta["unit"] = "currency"
    assert validate_reference(frame, meta, path, mp)["status"] == "failed"


def test_vendor_reference_is_not_truth(tmp_path):
    frame, path, mp, meta = files(tmp_path)
    meta["evidence_url"] = "https://finance.qq.com/data"
    mp.write_text(json.dumps(meta))
    assert validate_reference(frame, meta, path, mp)["status"] == "insufficient_evidence"


def test_null_dates_cannot_pass(tmp_path):
    import pytest

    frame, path, mp, meta = files(tmp_path)
    frame.loc[0, "date"] = None
    frame.to_csv(path, index=False)
    with pytest.raises(ValueError):
        validate_reference(frame, meta, path, mp)
