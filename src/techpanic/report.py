"""输出：CSV 序列、JSON 快照、徽章数据。

CSV 用 \u0060utf-8-sig\u0060 编码（带 BOM），这样 Windows 用户双击用 Excel 打开不会中文乱码。
JSON 输出固定 schema（v1），下游可稳定消费，字段见 :func:`to_json_payload`。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from . import store
from .config import AppConfig
from .pipeline import TargetResult

JSON_SCHEMA = "v1"

CSV_COLUMNS = [
    ("标的", "name"),
    ("PI完整口径", "PI_full"),
    ("分级完整", "level_full"),
    ("PI即时口径", "PI_price"),
    ("分级即时", "level_price"),
    ("方向", "方向"),
    ("突发性", "S"),
    ("不对称性", "A"),
    ("前瞻恐惧", "F"),
    ("RV20", "rv20"),
    ("QVIX", "qvix"),
    ("当日涨跌", "ret"),
    ("近5日涨跌", "ret5"),
    ("口径", "source"),
]

ANCHORS_JSON = ("p25", "p50", "p75", "p90")


def _fmt(value, digits: int = 6):
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return None
    if isinstance(value, (np.floating, float)):
        return round(float(value), digits)
    return value


def to_frame(tr: TargetResult) -> pd.DataFrame:
    """把内部指标表转成对外 CSV 结构。"""
    d = tr.frame
    assert d is not None
    out = pd.DataFrame(index=d.index).reset_index()
    out = out.rename(columns={out.columns[0]: "date"})
    out["标的"] = tr.target.name
    for cn, en in CSV_COLUMNS[1:]:
        out[cn] = d[en].to_numpy() if en in d.columns else np.nan
    return out


def to_json_payload(tr: TargetResult, cfg: AppConfig) -> dict:
    """单个标的的机器可读快照（schema v1）。"""
    anchors = None
    table = tr.frame.attrs.get("anchors_price") if tr.frame is not None else None
    if table is not None and not table.dropna(how="all").empty:
        row = table.dropna(how="all").iloc[-1]
        anchors = {k: _fmt(row[k], 4) for k in ANCHORS_JSON}

    return {
        "target": tr.target.key,
        "name": tr.target.name,
        "role": tr.target.role,
        "symbol": tr.target.symbol,
        "latest_trading_day": None if tr.latest_date is None else str(tr.latest_date.date()),
        "full": {
            "available": tr.has_full,
            "value": _fmt(tr.full_value, 4),
            "level": tr.full_level,
            "percentile": _fmt(tr.full_percentile, 1),
            "data_date": None if tr.full_date is None else str(tr.full_date.date()),
            "source": tr.qvix_source,
        },
        "price_only": {
            "available": tr.has_price,
            "value": _fmt(tr.price_value, 4),
            "level": tr.price_level,
            "percentile": _fmt(tr.price_percentile, 1),
            "data_date": None if tr.price_date is None else str(tr.price_date.date()),
        },
        "direction": tr.direction,
        "return_1d_pct": _fmt(tr.ret, 3),
        "return_5d_pct": _fmt(tr.ret5, 3),
        "components": {k: _fmt(v, 3) for k, v in tr.components.items()},
        "qvix": {
            "series": tr.target.qvix,
            "rows": tr.qvix_rows,
            "last_date": None if tr.qvix_last_date is None else str(tr.qvix_last_date.date()),
            "stale_trading_days": tr.qvix_stale_days,
        },
        "anchors": anchors,
        "notes": tr.notes,
    }


def build_payload(results: list[TargetResult], cfg: AppConfig, exit_code: int) -> dict:
    return {
        "schema": JSON_SCHEMA,
        "generator": "techpanic",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "exit_code": exit_code,
        "config": {
            "weights": {
                "S": cfg.index.weight_s,
                "A": cfg.index.weight_a,
                "F": cfg.index.weight_f,
            },
            "ema_span": cfg.index.ema_span,
            "level_anchor": cfg.index.level_anchor,
            "offline": cfg.offline,
        },
        "results": [to_json_payload(tr, cfg) for tr in results if tr.frame is not None],
    }


def summary_line(tr: TargetResult) -> str:
    """一行式摘要，便于终端与 CI 日志。"""
    parts = [f"{tr.target.name}"]
    if tr.has_full:
        parts.append(f"A {tr.full_value:.1f} {tr.full_level}")
    else:
        parts.append("A 无值")
    if tr.has_price:
        parts.append(f"B {tr.price_value:.1f} {tr.price_level}")
    else:
        parts.append("B 无值")
    parts.append(f"方向{tr.direction}")
    return " ｜ ".join(parts)


def write_outputs(tr: TargetResult, cfg: AppConfig) -> list[Path]:
    """写出一个标的的 CSV 与 JSON 文件，返回写出的路径列表。"""
    written: list[Path] = []
    out_dir = cfg.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    if "csv" in cfg.output.formats:
        path = out_dir / f"panic_index_{tr.target.key}.csv"
        store.atomic_write_csv(to_frame(tr), path, encoding=cfg.output.encoding)
        written.append(path)

    if "json" in cfg.output.formats:
        path = out_dir / f"panic_index_{tr.target.key}.json"
        store.atomic_write_json(to_json_payload(tr, cfg), path)
        written.append(path)

    return written


def write_run_files(
    results: list[TargetResult], cfg: AppConfig, exit_code: int, elapsed: float
) -> list[Path]:
    """写出整次运行的汇总文件：latest.json / badge.json / summary.md。"""
    written: list[Path] = []
    out_dir = cfg.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    payload = build_payload(results, cfg, exit_code)
    payload["elapsed_seconds"] = round(elapsed, 2)
    latest = out_dir / "latest.json"
    store.atomic_write_json(payload, latest)
    written.append(latest)

    tech = [tr for tr in results if tr.target.role == "tech" and tr.has_price]
    if tech:
        lead = max(tech, key=lambda t: t.price_value or 0)
        badge = {
            "schemaVersion": 1,
            "label": "科技恐慌指数",
            "message": f"{lead.price_value:.1f} {lead.price_level}",
            "color": level_color(lead.price_level),
        }
        path = out_dir / "badge.json"
        store.atomic_write_json(badge, path)
        written.append(path)

    lines = [
        "# 科技板块恐慌指数 PI · 本次运行摘要",
        "",
        f"- 生成时间：{payload['generated_at']}",
        f"- 耗时：{elapsed:.1f} 秒",
        f"- 退出码：{exit_code}",
        f"- 锚点算法：{cfg.index.level_anchor}",
        "",
        "| 标的 | A 完整口径 | 分级 | 数据日 | B 即时口径 | 分级 | 方向 | 当日 | 近5日 |",
        "|---|---:|---|---|---:|---|---|---:|---:|",
    ]
    for tr in results:
        lines.append(
            "| {n} | {a} | {al} | {ad} | {b} | {bl} | {d} | {r} | {r5} |".format(
                n=tr.target.name,
                a="—" if not tr.has_full else f"{tr.full_value:.1f}",
                al=tr.full_level,
                ad="—" if tr.full_date is None else tr.full_date.date(),
                b="—" if not tr.has_price else f"{tr.price_value:.1f}",
                bl=tr.price_level,
                d=tr.direction,
                r="—" if tr.ret is None else f"{tr.ret:+.2f}%",
                r5="—" if tr.ret5 is None else f"{tr.ret5:+.2f}%",
            )
        )
    lines += [
        "",
        "> A 完整口径含期权隐含波动率，QVIX 盘后发布，可能滞后 1 个交易日；",
        "> B 即时口径只用价格，当日收盘即可算。**两者口径不同，不可相减解读。**",
        "",
        "> 本指标为研究/观测级度量，不是官方波动率指数，不预测涨跌方向，不构成投资建议。",
    ]
    path = out_dir / "summary.md"
    store.atomic_write_text("\n".join(lines), path, encoding="utf-8")
    written.append(path)
    return written


def level_color(level: str) -> str:
    return {
        "平静": "green",
        "常态": "blue",
        "警戒": "yellow",
        "恐慌": "orange",
        "极度恐慌": "red",
    }.get(level, "lightgrey")


def dump_json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)
