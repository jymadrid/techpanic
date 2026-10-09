"""离线端到端：退出码、产物、确定性、两个「负例」。"""

from __future__ import annotations

import json

import pandas as pd

from techpanic.cli import main
from techpanic.errors import EXIT_DEGRADED, EXIT_NO_DATA, EXIT_OK


def test_offline_run_succeeds_and_writes_outputs(seeded_data_dir, capsys):
    code = main(["--offline", "--data-dir", str(seeded_data_dir), "--no-color"])
    assert code in (EXIT_OK, EXIT_DEGRADED)

    out = seeded_data_dir / "output"
    assert (out / "latest.json").is_file()
    assert (out / "summary.md").is_file()
    assert (out / "badge.json").is_file()
    for key in ("tech_kcb", "tech_cyb"):
        assert (out / f"panic_index_{key}.csv").is_file()
        assert (out / f"panic_index_{key}.json").is_file()

    payload = json.loads((out / "latest.json").read_text(encoding="utf-8"))
    assert payload["schema"] == "v1"
    assert len(payload["results"]) >= 1


def test_csv_is_utf8_bom_and_readable_by_pandas(seeded_data_dir):
    main(["--offline", "--data-dir", str(seeded_data_dir), "--no-color", "--quiet"])
    path = seeded_data_dir / "output" / "panic_index_tech_kcb.csv"
    assert path.read_bytes().startswith(b"\xef\xbb\xbf"), "CSV 必须带 BOM，否则 Excel 打开中文会乱码"
    df = pd.read_csv(path, encoding="utf-8-sig")
    assert "PI即时口径" in df.columns
    assert len(df) > 700
    last = df.iloc[-1]
    assert 0.0 < float(last["PI即时口径"]) < 100.0


def test_run_is_deterministic(seeded_data_dir):
    out = seeded_data_dir / "output"
    main(["--offline", "--data-dir", str(seeded_data_dir), "--no-color", "--quiet"])
    first = (out / "panic_index_tech_kcb.csv").read_bytes()
    main(["--offline", "--data-dir", str(seeded_data_dir), "--no-color", "--quiet"])
    second = (out / "panic_index_tech_kcb.csv").read_bytes()
    assert first == second, "相同输入必须得到逐字节相同的输出"


def test_json_summary_fields(seeded_data_dir):
    main(["--offline", "--data-dir", str(seeded_data_dir), "--no-color", "--quiet"])
    payload = json.loads(
        (seeded_data_dir / "output" / "panic_index_tech_kcb.json").read_text(encoding="utf-8")
    )
    for key in ("target", "name", "price_only", "full", "components", "qvix", "anchors", "notes"):
        assert key in payload
    assert payload["price_only"]["available"] is True
    assert payload["price_only"]["level"] in ("平静", "常态", "警戒", "恐慌", "极度恐慌")


def test_no_cache_no_network_exits_3_with_guidance(tmp_path, capsys):
    """负例 1：全新用户、离线、无缓存 → 必须是退出码 3 + 中文指引，不能是堆栈。"""
    code = main(["--offline", "--data-dir", str(tmp_path / "empty"), "--no-color"])
    assert code == EXIT_NO_DATA
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "Traceback" not in combined
    assert "python -m techpanic" in combined


def test_negative_test_zero_qvix_tail_is_treated_as_missing(seeded_data_dir):
    """坏数据（一）：上游把收盘列写成 0。

    0 不是合法波动率，必须当缺失剔除 —— 完整口径因此提前结束并降级，
    但**绝不能**把 0 算进 PI（那样 PI 会被系统性压低）。
    """
    from techpanic import store

    path = seeded_data_dir / "cache" / "qvix" / "kcb.csv"
    df = pd.read_csv(path, encoding="utf-8-sig")
    df.loc[df.index[-200:], "close"] = 0.0
    store.atomic_write_csv(df, path)

    code = main(["--offline", "--data-dir", str(seeded_data_dir), "--no-color"])
    assert code == EXIT_DEGRADED, "有 200 行坏数据却报成功，说明坏数据被静默接受了"
    payload = json.loads(
        (seeded_data_dir / "output" / "panic_index_tech_kcb.json").read_text(encoding="utf-8")
    )
    assert payload["full"]["value"] != 0.0
    assert payload["price_only"]["value"] != 0.0
    assert payload["qvix"]["rows"] <= 700, "被置零的行没有从 QVIX 样本中剔除"


def test_negative_test_negative_qvix_is_rejected(seeded_data_dir):
    """坏数据（二）：负数波动率。

    期望：整段数据被拒绝，完整口径缺值，即时口径仍然可用。
    """
    from techpanic import store

    path = seeded_data_dir / "cache" / "qvix" / "cyb.csv"
    df = pd.read_csv(path, encoding="utf-8-sig")
    df["close"] = -df["close"]
    store.atomic_write_csv(df, path)

    code = main(["--offline", "--data-dir", str(seeded_data_dir), "--no-color"])
    assert code == EXIT_DEGRADED
    payload = json.loads(
        (seeded_data_dir / "output" / "panic_index_tech_cyb.json").read_text(encoding="utf-8")
    )
    assert payload["full"]["available"] is False, "全为负数的波动率序列必须被拒绝"
    assert payload["price_only"]["available"] is True


def test_negative_test_truncated_index_self_heals(seeded_data_dir):
    """负例 3：指数缓存被截断到不足 500 行 → 该标的不产出读数，但另一个标的仍正常。"""
    from techpanic import store

    path = seeded_data_dir / "cache" / "index_daily" / "sh000688.csv"
    df = pd.read_csv(path, encoding="utf-8-sig").head(100)
    store.atomic_write_csv(df, path)

    code = main(["--offline", "--data-dir", str(seeded_data_dir), "--no-color"])
    out = seeded_data_dir / "output"
    assert not (out / "panic_index_tech_kcb.csv").exists()
    assert (out / "panic_index_tech_cyb.csv").exists()
    assert code in (EXIT_OK, EXIT_DEGRADED)
