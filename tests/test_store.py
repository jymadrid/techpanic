"""缓存守卫：原子写、备份、校验、BOM 兼容。"""

from __future__ import annotations

import pandas as pd

from techpanic import store


def test_atomic_write_leaves_no_tmp(tmp_path):
    df = pd.DataFrame({"date": ["2026-01-01"], "close": [1.0]})
    path = tmp_path / "x.csv"
    store.atomic_write_csv(df, path)
    assert path.is_file()
    assert list(tmp_path.glob("*.tmp")) == []


def test_backup_rotates(tmp_path):
    path = tmp_path / "x.csv"
    for i in range(5):
        store.backup(path)
        store.atomic_write_csv(pd.DataFrame({"date": [f"2026-01-0{i + 1}"], "close": [i]}), path)
    assert (tmp_path / "x.csv.bak.1").is_file()
    assert (tmp_path / "x.csv.bak.3").is_file()
    assert not (tmp_path / "x.csv.bak.4").is_file()


def test_check_frame_rejects_short_series():
    df = pd.DataFrame({"date": pd.bdate_range("2026-01-01", periods=10), "close": [1.0] * 10})
    res = store.check_frame(df, name="t", min_rows=500)
    assert not res.ok
    assert "少于下限" in res.message


def test_check_frame_rejects_zero_values():
    n = 600
    close = [1.0] * n
    close[-100:] = [0.0] * 100
    df = pd.DataFrame({"date": pd.bdate_range("2020-01-01", periods=n), "close": close})
    res = store.check_frame(df, name="t", min_rows=500)
    assert not res.ok
    assert "0 值占比" in res.message


def test_check_frame_rejects_unsorted_dates():
    n = 600
    dates = list(pd.bdate_range("2020-01-01", periods=n))
    dates[10], dates[11] = dates[11], dates[10]
    df = pd.DataFrame({"date": dates, "close": [1.0] * n})
    res = store.check_frame(df, name="t", min_rows=500)
    assert not res.ok
    assert "升序" in res.message


def test_bom_columns_are_stripped(tmp_path):
    """pandas 2.x 读取 utf-8-sig 时不会剥离 BOM，必须由我们处理。"""
    path = tmp_path / "x.csv"
    pd.DataFrame({"date": ["2026-01-01"], "close": [1.0]}).to_csv(
        path, index=False, encoding="utf-8-sig"
    )
    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    df = store.read_csv_any(path)
    assert df is not None
    assert list(df.columns) == ["date", "close"]


def test_load_csv_checked_missing_file_is_not_an_error(tmp_path):
    df, res = store.load_csv_checked(tmp_path / "nope.csv", name="t", min_rows=10)
    assert df is None
    assert res.level == "info"


def test_manifest_roundtrip(tmp_path):
    man = tmp_path / "_manifest.json"
    f = tmp_path / "a.csv"
    store.atomic_write_csv(pd.DataFrame({"date": ["2026-01-01"], "close": [1.0]}), f)
    store.update_manifest(man, "index_sh000688", rows=1, last_date="2026-01-01", source="sina", file_path=f)
    entry = store.manifest_entry(man, "index_sh000688")
    assert entry is not None and entry["rows"] == 1 and entry["source"] == "sina"
    assert store.last_success_date(man, "index_sh000688") == "2026-01-01"
