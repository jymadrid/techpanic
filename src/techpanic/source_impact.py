"""固定共同样本和各源生产历史的双重影响报告，不混源补齐。"""

from dataclasses import asdict
import hashlib
import numpy as np
import pandas as pd
from .index import compute
from .config import IndexConfig


def summary(values):
    x = np.asarray(values, float)
    x = np.abs(x[np.isfinite(x)])
    return {
        "n": len(x),
        "median": float(np.median(x)) if len(x) else None,
        "p95": float(np.quantile(x, 0.95)) if len(x) else None,
        "max": float(x.max()) if len(x) else None,
    }


def compare_sources(
    left: pd.DataFrame,
    right: pd.DataFrame,
    cfg: IndexConfig | None = None,
    *,
    qvix: pd.DataFrame | None = None,
):
    cfg = cfg or IndexConfig()
    series = []
    digests = []
    for frame in (left, right):
        d = frame[["date", "close"]].copy()
        d["date"] = pd.to_datetime(d["date"])
        d["close"] = pd.to_numeric(d["close"], errors="raise")
        if (
            d["date"].isna().any()
            or (d["date"] != d["date"].dt.normalize()).any()
            or d["date"].duplicated().any()
            or not d["date"].is_monotonic_increasing
            or not np.isfinite(d["close"]).all()
            or (d["close"] <= 0).any()
        ):
            raise ValueError("比较行情须有效且唯一升序")
        digests.append(hashlib.sha256(d.to_csv(index=False).encode()).hexdigest())
        series.append(d.set_index("date")["close"])
    common = series[0].index.intersection(series[1].index)
    report = {
        "status": "insufficient_evidence",
        "scope": "source_sensitivity_not_market_truth",
        "input_sha256": digests,
        "config": asdict(cfg),
        "common_rows": len(common),
    }
    if len(common) < cfg.rv_window + 2 * cfg.z_min_periods:
        return dict(report, reasons=["共同样本不足"])
    a, b = [s.loc[common] for s in series]
    q = None
    if qvix is not None:
        qd = qvix[["date", "close"]].copy()
        qd["date"] = pd.to_datetime(qd["date"])
        if qd["date"].isna().any() or qd["date"].duplicated().any():
            raise ValueError("QVIX 比较日期无效或重复")
        q = qd.set_index("date")["close"]
    paired = [compute(s, q, cfg) for s in (a, b)]
    production = [compute(s, q, cfg) for s in series]

    def scores(frames):
        dates = frames[0].index.intersection(frames[1].index)
        x, y = [d.loc[dates] for d in frames]
        valid = x["PI_price"].notna() & y["PI_price"].notna()
        flips = x.loc[valid, "level_price"] != y.loc[valid, "level_price"]
        return {
            "S": summary(x["S"] - y["S"]),
            "A": summary(x["A"] - y["A"]),
            "PI_price": summary(x["PI_price"] - y["PI_price"]),
            "F": summary(x["F"] - y["F"]),
            "price_percentile_delta": summary(
                x["PI_price"].expanding().rank(pct=True) * 100
                - y["PI_price"].expanding().rank(pct=True) * 100
            ),
            "PI_full": summary(x["PI_full"] - y["PI_full"]),
            "full_label_flip_dates": [
                str(d.date())
                for d in dates
                if pd.notna(x.loc[d, "PI_full"])
                and pd.notna(y.loc[d, "PI_full"])
                and x.loc[d, "level_full"] != y.loc[d, "level_full"]
            ],
            "label_flip_count": int(flips.sum()),
            "label_comparisons": int(valid.sum()),
            "label_flip_dates": [str(d.date()) for d in flips.index[flips]],
            "label_flip_rate": float(flips.mean()) if len(flips) else None,
        }

    common_result = scores(paired)
    return dict(
        report,
        status="reported" if common_result["label_comparisons"] else "insufficient_evidence",
        full_comparison="available"
        if common_result["PI_full"]["n"]
        else "insufficient_evidence_no_full_pairs",
        price_absolute=summary(a - b),
        return_difference_bps=summary(
            (a.pct_change(fill_method=None) - b.pct_change(fill_method=None)) * 10000
        ),
        common_support=common_result,
        production_histories=scores(production),
        caveat="共同日期筛选只用于诊断，不是生产补值；F 仅在提供同一 QVIX 时评价；无官方参考则不证明哪源正确",
    )
