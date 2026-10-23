from techpanic.source_impact import compare_sources


def test_same_input_zero_effect(prices_df):
    report = compare_sources(prices_df, prices_df)
    assert report["status"] == "reported"
    assert report["common_support"]["PI_price"]["max"] == 0
    assert report["common_support"]["label_flip_count"] == 0


def test_production_and_common_history_separated(prices_df):
    shorter = prices_df.iloc[200:].copy()
    shorter["close"] *= 1.0001
    report = compare_sources(prices_df, shorter)
    assert "production_histories" in report and "common_support" in report
    assert report["price_absolute"]["max"] > 0
    assert report["common_rows"] == len(shorter)


def test_zero_qvix_not_claimed_full_comparison(prices_df):
    q = prices_df.copy()
    q["close"] = 0
    report = compare_sources(prices_df, prices_df, qvix=q)
    assert report["full_comparison"] == "insufficient_evidence_no_full_pairs"


def test_constant_closes_have_no_comparison(prices_df):
    d = prices_df.copy()
    d["close"] = 100.0
    assert compare_sources(d, d)["status"] == "insufficient_evidence"
