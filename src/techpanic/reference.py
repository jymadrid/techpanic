"""离线同对象参考校验；声明出处并不自动获得官方认证。"""

from __future__ import annotations
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit
import numpy as np
import pandas as pd

IDENTITY = ("instrument", "instrument_type", "price_basis", "unit")
OFFICIAL_DOMAINS = ("sse.com.cn", "szse.cn", "csindex.com.cn", "cnindex.com.cn")


def validate_reference(
    frame: pd.DataFrame,
    metadata: dict,
    reference_csv: Path,
    reference_metadata: Path,
    *,
    tolerance=None,
):
    result = {
        "status": "insufficient_evidence",
        "scope": "agreement_with_provided_reference",
        "official_authenticity": "not_independently_authenticated",
    }
    if not reference_csv.is_file() or not reference_metadata.is_file():
        return dict(result, reasons=["缺少参考值或出处元数据"])
    raw, meta_raw = reference_csv.read_bytes(), reference_metadata.read_bytes()
    meta = json.loads(meta_raw.decode("utf-8-sig"))
    result.update(
        reference_sha256=hashlib.sha256(raw).hexdigest(),
        metadata_sha256=hashlib.sha256(meta_raw).hexdigest(),
    )
    if any(not metadata.get(k) or not meta.get(k) for k in IDENTITY):
        return dict(result, reasons=["缺少对象/价格口径/单位身份"])
    if any(metadata[k] != meta[k] for k in IDENTITY):
        return dict(result, status="failed", reasons=["参考和被验数据不是相同经济对象"])
    evidence_url = urlsplit(str(meta.get("evidence_url", "")))
    host = (evidence_url.hostname or "").lower()
    official = any(host == d or host.endswith("." + d) for d in OFFICIAL_DOMAINS)
    if (
        evidence_url.scheme not in {"http", "https"}
        or not official
        or not meta.get("publisher")
        or meta.get("independent_from_vendor") is not True
    ):
        return dict(result, reasons=["未提供独立官方出处声明，不能以另一行情商作为真值"])
    precision = meta.get("decimal_places")
    vendor_precision = metadata.get("decimal_places")
    if (
        not isinstance(precision, int)
        or isinstance(precision, bool)
        or not 0 <= precision <= 12
        or not isinstance(vendor_precision, int)
        or isinstance(vendor_precision, bool)
        or not 0 <= vendor_precision <= 12
    ):
        return dict(result, reasons=["双方精度未声明，不能事后放宽容差"])
    derived = 0.5 * (10.0**-precision + 10.0**-vendor_precision)
    tol = derived if tolerance is None else float(tolerance)
    if not np.isfinite(tol) or tol < 0 or tol > derived + 1e-12:
        raise ValueError("容差不可超过双方声明的舍入精度")
    refs = pd.read_csv(reference_csv, encoding="utf-8-sig")
    pairs = []
    for data in (frame, refs):
        if not {"date", "close"}.issubset(data):
            raise ValueError("参考比较缺少 date/close")
        d = data[["date", "close"]].copy()
        market_dates = pd.to_datetime(d["date"], errors="raise")
        if market_dates.dt.tz is not None:
            from .sessions import BEIJING

            market_dates = market_dates.dt.tz_convert(BEIJING).dt.tz_localize(None)
        d["date"] = market_dates.dt.normalize()
        d["close"] = pd.to_numeric(d["close"], errors="raise")
        if (
            d["date"].isna().any()
            or d["date"].duplicated().any()
            or not np.isfinite(d["close"]).all()
            or (d["close"] <= 0).any()
        ):
            raise ValueError("参考或被验行情包含无效/重复值")
        pairs.append(d)
    merged = pairs[1].merge(pairs[0], on="date", how="left", suffixes=("_reference", "_vendor"))
    if merged.empty:
        return dict(result, reasons=["参考样本为空"])
    missing = merged["close_vendor"].isna()
    delta = (merged["close_vendor"] - merged["close_reference"]).abs()
    passed = bool(not missing.any() and (delta <= tol + 1e-12).all())
    rows = [
        {
            "date": str(row.date.date()),
            "reference": float(row.close_reference),
            "vendor": None if pd.isna(row.close_vendor) else float(row.close_vendor),
            "absolute_error": None
            if pd.isna(row.close_vendor)
            else abs(float(row.close_vendor - row.close_reference)),
            "within_tolerance": bool(
                not pd.isna(row.close_vendor)
                and abs(row.close_vendor - row.close_reference) <= tol + 1e-12
            ),
        }
        for row in merged.itertuples()
    ]
    return dict(
        result,
        status="passed" if passed else "failed",
        tolerance=tol,
        rows=rows,
        reference_rows=len(refs),
        compared_rows=int((~missing).sum()),
        missing_rows=int(missing.sum()),
        reasons=[] if passed else ["存在超容差差异或参考日期缺失"],
        scope="sample_agreement_only; not all-history authenticity",
    )
