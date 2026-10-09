"""主流水线：确保数据 → 计算 → 输出。

降级阶梯（每一步都明确告知用户，绝不静默）：

1. 指数抓取失败但有缓存 → 用缓存并标注；指数完全不可用 → 该标的跳过。
2. QVIX 抓取失败但有缓存 → 用缓存并标注「滞后 N 个交易日」，读数降级。
3. QVIX 完全缺失 → 只出即时口径（price_only）。
4. 两个口径都算不出来 → 该标的无读数。
5. 全部标的都无读数 → 退出码 3。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

import pandas as pd

from . import index as index_mod
from . import store
from .config import AppConfig, Target
from .errors import EXIT_DEGRADED, EXIT_NO_DATA, EXIT_OK
from .fetch import index_daily
from .fetch import qvix as qvix_fetch

MIN_INDEX_ROWS = 500
MIN_QVIX_ROWS = 300

SayFn = Callable[[str], None]
ProgressFn = Callable[[str, int, int | None, float], None]


@dataclass
class TargetResult:
    target: Target
    frame: pd.DataFrame | None
    latest_date: pd.Timestamp | None
    index_source: str
    index_rows: int
    qvix_source: str
    qvix_rows: int
    qvix_last_date: pd.Timestamp | None
    qvix_stale_days: int = 0
    notes: list[str] = field(default_factory=list)
    degraded: bool = False
    full_value: float | None = None
    full_date: pd.Timestamp | None = None
    full_level: str = "无值"
    full_percentile: float | None = None
    price_value: float | None = None
    price_date: pd.Timestamp | None = None
    price_level: str = "无值"
    price_percentile: float | None = None
    direction: str = "未知"
    ret: float | None = None
    ret5: float | None = None
    # 「即时口径那一行」的 S/A/F。注意它的 F 可能是 NaN：
    # 最新交易日通常还没有 QVIX，所以这一行算不出 F。
    components: dict[str, float] = field(default_factory=dict)
    # 「完整口径那一行」的 S/A/F —— full_value 正是用这三个数算出来的。
    # 两个口径的数据日可能不同（QVIX 滞后），必须分开存，否则读数卡/JSON
    # 会出现「用 9-30 的 F 算出 A 值，却展示 10-08 的 F=None」这类不自洽。
    full_components: dict[str, float] = field(default_factory=dict)

    @property
    def has_full(self) -> bool:
        return self.full_value is not None

    @property
    def has_price(self) -> bool:
        return self.price_value is not None


@dataclass
class RunResult:
    targets: list[TargetResult]
    exit_code: int
    started_at: datetime
    finished_at: datetime
    data_dir: Path
    output_files: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    fetched_online: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == EXIT_OK

    @property
    def elapsed(self) -> float:
        return (self.finished_at - self.started_at).total_seconds()


def _stale_trading_days(all_dates: pd.DatetimeIndex, last: pd.Timestamp | None) -> int:
    if last is None or len(all_dates) == 0:
        return 0
    return int((all_dates > last).sum())


def _cache_is_fresh(cfg: AppConfig, dataset: str, cache_path: Path | None = None) -> bool:
    """缓存是否在 TTL 内已成功抓取过。

    动机：QVIX 上游实测要 2~4 分钟，同一天内反复运行不该反复下载。
    --refresh 会绕过这个判断。
    """
    if cfg.refresh or cfg.network.cache_ttl_hours <= 0:
        return False
    if cache_path is not None and not cache_path.is_file():
        return False
    entry = store.manifest_entry(cfg.manifest_path, dataset)
    if entry is None:
        return False
    stamp = entry.get("fetched_at")
    if not stamp:
        return False
    try:
        fetched = datetime.fromisoformat(str(stamp))
    except ValueError:
        return False
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=datetime.now().astimezone().tzinfo)
    age_hours = (datetime.now().astimezone() - fetched).total_seconds() / 3600.0
    return age_hours < cfg.network.cache_ttl_hours


def _union_dates(frames: dict[str, pd.DataFrame]) -> pd.DatetimeIndex:
    acc: set[pd.Timestamp] = set()
    for f in frames.values():
        acc.update(pd.to_datetime(f["date"]).tolist())
    return pd.DatetimeIndex(sorted(acc))


def run(
    cfg: AppConfig,
    *,
    say: SayFn | None = None,
    progress: ProgressFn | None = None,
) -> RunResult:
    """执行一次完整运行。"""
    started = datetime.now()
    cfg.ensure_dirs()
    emit: SayFn = say or (lambda _msg: "")

    results: list[TargetResult] = []
    warnings: list[str] = []
    output_files: list[Path] = []
    fetched_online = False

    # ------------------------------------------------ 1) 指数
    emit("[1/4] 指数日线")
    index_frames: dict[str, pd.DataFrame] = {}
    index_source: dict[str, str] = {}
    # 指数用了陈旧缓存 / 拒绝过损坏缓存 → 本次结果不完整，必须反映到退出码。
    # 以前这种情况退出码仍是 0（＝「全部数据均为最新」），是明确的误报。
    degraded_index = False

    for t in cfg.targets:
        # 缓存文件名优先用完整代码（sh000688.csv）；兼容早期只写裸代码（000688.csv）的缓存
        candidates = [cfg.index_dir / f"{t.symbol}.csv", cfg.index_dir / f"{t.symbol[2:]}.csv"]
        cache_path = next((p for p in candidates if p.is_file()), candidates[0])
        frame: pd.DataFrame | None = None
        source = "none"
        fetched = False
        skipped_fetch = False

        if not cfg.offline:
            fresh = _cache_is_fresh(cfg, f"index_{t.symbol}", cache_path)
            if not fresh:
                res = index_daily.fetch_index(t, cfg.network)
                if res.frame is not None:
                    frame, source, fetched = res.frame, res.source, True
            else:
                skipped_fetch = True

        if frame is None:
            cached, chk = store.load_csv_checked(
                cache_path, name=f"指数 {t.name}", min_rows=MIN_INDEX_ROWS
            )
            if cached is not None:
                # 缓存完整性：manifest 记录过 sha1/bytes 就必须对得上，
                # 否则宁可不用它（外部改动过的缓存会静默改变读数）。
                integ = store.verify_integrity(cache_path, cfg.manifest_path, f"index_{t.symbol}")
                if not integ.ok:
                    emit(f"      {t.name}：{integ.detail} → 拒绝使用该缓存")
                    warnings.append(f"{t.name}：{integ.detail}，已拒绝该缓存")
                    cached = None
            if cached is not None:
                frame, source = cached, "cache"
                if skipped_fetch:
                    emit(f"      {t.name}：缓存新鲜，跳过抓取（{chk.rows} 行，截至 {chk.last_date}）")
                    skipped_fetch = False
                else:
                    emit(
                        f"      {t.name}：抓取失败，使用本地缓存"
                        f"（{chk.rows} 行，截至 {chk.last_date}）"
                    )
                    warnings.append(f"{t.name}：指数使用本地缓存，可能不是最新交易日")
            else:
                emit(f"      {t.name}：无可用指数数据 → 跳过该标的")
                warnings.append(f"{t.name}：无可用指数数据，未产出读数")

        if frame is None:
            continue

        frame = frame.copy()
        frame["date"] = pd.to_datetime(frame["date"])
        frame = frame.sort_values("date").drop_duplicates("date").reset_index(drop=True)
        if len(frame) < MIN_INDEX_ROWS:
            emit(f"      {t.name}：仅 {len(frame)} 行，少于 {MIN_INDEX_ROWS} 行下限 → 跳过")
            warnings.append(f"{t.name}：指数行数不足，未产出读数")
            continue

        index_frames[t.key] = frame
        index_source[t.key] = source
        # 用了本地缓存（说明抓取失败或缓存新鲜）→ 不是「全部数据最新」
        if source == "cache":
            degraded_index = True
        if fetched:
            fetched_online = True
            emit(f"      {t.name}：{source} OK  {len(frame)} 行  截至 {frame['date'].iloc[-1].date()}")
            store.backup(cache_path)
            store.atomic_write_csv(frame, cache_path, encoding="utf-8-sig")
            store.update_manifest(
                cfg.manifest_path,
                f"index_{t.symbol}",
                rows=len(frame),
                last_date=str(frame["date"].iloc[-1].date()),
                source=source,
                file_path=cache_path,
            )

    all_dates = _union_dates(index_frames)

    # ------------------------------------------------ 2) QVIX
    emit("[2/4] 期权隐含波动率 QVIX")
    emit(
        "      上游 1.optbbs.com 为第三方小站，实测限速约 8 KB/s（文件 914 KB），"
        "通常 2~4 分钟；若超时会自动改用本地缓存"
    )
    qvix_frames: dict[str, pd.DataFrame] = {}
    qvix_meta: dict[str, tuple[str, int, pd.Timestamp | None]] = {}
    keys = tuple(sorted({t.qvix for t in cfg.targets}))
    fetched_qvix: dict[str, qvix_fetch.QvixFetchResult] = {}

    qvix_cache_fresh = bool(keys) and all(
        _cache_is_fresh(cfg, f"qvix_{k}", cfg.qvix_dir / f"{k}.csv") for k in keys
    )
    if not cfg.offline and not qvix_cache_fresh:
        def _progress(got: int, total: int | None, elapsed: float) -> None:
            if progress is not None:
                progress("QVIX 下载", got, total, elapsed)

        fetched_qvix = qvix_fetch.fetch_qvix(
            keys,
            cfg.network,
            cache_paths={k: cfg.qvix_dir / f"{k}.csv" for k in keys},
            progress=_progress,
        )
    elif not cfg.offline:
        emit(
            f"      缓存仍在 {cfg.network.cache_ttl_hours:.0f} 小时有效期内，"
            "跳过下载（要强制重抓请加 --refresh）"
        )

    for key in keys:
        got = fetched_qvix.get(key)
        if got is not None and got.frame is not None:
            fetched_online = True
            last = pd.to_datetime(got.frame["date"].iloc[-1])
            stale = _stale_trading_days(all_dates, last)
            qvix_frames[key] = got.frame
            qvix_meta[key] = ("optbbs", len(got.frame), last)
            cache_path = cfg.qvix_dir / f"{key}.csv"
            store.backup(cache_path)
            store.atomic_write_csv(got.frame, cache_path, encoding="utf-8-sig")
            store.update_manifest(
                cfg.manifest_path,
                f"qvix_{key}",
                rows=len(got.frame),
                last_date=str(last.date()),
                source="optbbs",
                file_path=cache_path,
                stale_days=stale,
            )
            emit(f"      {key}：optbbs OK  {len(got.frame)} 行  截至 {last.date()}  （{got.elapsed:.1f}s）")
            continue

        if got is not None and got.error:
            emit(f"      {key}：抓取失败 — {str(got.error).splitlines()[0][:70]}")
        cached = qvix_fetch.read_cache(cfg.qvix_dir / f"{key}.csv")
        if cached is not None and len(cached) >= MIN_QVIX_ROWS:
            last = pd.to_datetime(cached["date"].iloc[-1])
            stale = _stale_trading_days(all_dates, last)
            qvix_frames[key] = cached
            qvix_meta[key] = ("cache", len(cached), last)
            emit(f"      {key}：使用本地缓存  {len(cached)} 行  截至 {last.date()}（滞后 {stale} 个交易日）")
            warnings.append(f"QVIX({key})：使用本地缓存，滞后 {stale} 个交易日")
        else:
            qvix_meta[key] = ("none", 0, None)
            emit(f"      {key}：无可用数据 → 该标的只能出即时口径")
            warnings.append(f"QVIX({key})：无可用数据，完整口径缺值")

    # ------------------------------------------------ 3) 计算
    emit("[3/4] 计算三因子与分级")
    degraded = False
    for t in cfg.targets:
        frame = index_frames.get(t.key)
        if frame is None:
            continue

        close = frame.set_index("date")["close"].astype(float)
        qv = qvix_frames.get(t.qvix)
        q_series = None if qv is None else qv.set_index("date")["close"].astype(float)

        source, rows, q_last = qvix_meta.get(t.qvix, ("none", 0, None))
        # 口径必须统一：QVIX 的「滞后几个交易日」要拿**全市场交易日集合**算，
        # 不能用单个标的自己的索引（那样会把该标的自身的停牌日也算成滞后）。
        stale = _stale_trading_days(all_dates, q_last) if q_last is not None else 0

        d = index_mod.compute(close, q_series, cfg.index)
        if cfg.as_of:
            d = d.loc[d.index <= pd.to_datetime(cfg.as_of)]
        if d.empty:
            emit(f"      {t.name}：样本不足（{len(close)} 行原始数据），跳过")
            warnings.append(f"{t.name}：样本不足，未产出读数")
            continue

        tr = TargetResult(
            target=t,
            frame=d,
            latest_date=d.index[-1],
            index_source=index_source.get(t.key, "unknown"),
            index_rows=len(close),
            qvix_source=source,
            qvix_rows=rows,
            qvix_last_date=q_last,
            qvix_stale_days=stale,
        )

        fv_row = index_mod.latest_full(d)
        fdate = index_mod.latest_full_date(d)
        pv_row = index_mod.latest_price(d)
        pdate = index_mod.latest_price_date(d)

        if fv_row is not None:
            tr.full_value = float(fv_row["PI_full"])
            tr.full_date = fdate
            tr.full_level = str(fv_row["level_full"])
            tr.full_components = {
                "S": float(fv_row["S"]) if pd.notna(fv_row["S"]) else float("nan"),
                "A": float(fv_row["A"]) if pd.notna(fv_row["A"]) else float("nan"),
                "F": float(fv_row["F"]) if pd.notna(fv_row["F"]) else float("nan"),
            }
            tr.full_percentile = index_mod.percentile(d, "full", tr.full_value)
            if stale > 0:
                # QVIX 滞后是**正常现象**（盘后发布），但它让「完整口径」不是最新的，
                # 因此按约定报「部分降级」（退出码 2），而不是假装一切完备。
                tr.notes.append(f"QVIX 未发布，完整口径停在 {fdate.date()}（滞后 {stale} 个交易日）")
                tr.degraded = True
        else:
            tr.notes.append("QVIX 缺失或样本不足，无法给出完整口径")
            tr.degraded = True

        if pv_row is not None:
            tr.price_value = float(pv_row["PI_price"])
            tr.price_date = pdate
            tr.price_level = str(pv_row["level_price"])
            tr.price_percentile = index_mod.percentile(d, "price", tr.price_value)
            tr.direction = str(pv_row["方向"])
            tr.ret = float(pv_row["ret"]) if pd.notna(pv_row["ret"]) else None
            tr.ret5 = float(pv_row["ret5"]) if pd.notna(pv_row["ret5"]) else None
            tr.components = {
                "S": float(pv_row["S"]) if pd.notna(pv_row["S"]) else float("nan"),
                "A": float(pv_row["A"]) if pd.notna(pv_row["A"]) else float("nan"),
                "F": float(pv_row["F"]) if pd.notna(pv_row["F"]) else float("nan"),
            }
        else:
            tr.notes.append("连即时口径都算不出来（样本不足）")
            tr.degraded = True

        if tr.degraded:
            degraded = True
            warnings.extend(f"{t.name}：{n}" for n in tr.notes)
        results.append(tr)
        emit(f"      {t.name}：完成（{len(d)} 行序列）")

    # 任一指数的日线取自本地缓存（抓取失败，或缓存被判定为"仍然新鲜"）
    # → 本次读数不是「全部数据最新」，必须置降级。
    # 以前这种情况退出码仍是 0（脚本里等价于「全部数据均为最新」），是明确误报：
    # CI/定时任务据此判断会以为数据是新的，实际可能已经陈旧数日。
    if degraded_index:
        degraded = True

    # ------------------------------------------------ 4) 输出
    emit("[4/4] 写出结果")
    from . import report as report_mod

    for tr in results:
        output_files.extend(report_mod.write_outputs(tr, cfg))

    started_finished = datetime.now()
    if not results or not any(t.has_price or t.has_full for t in results):
        return RunResult(
            results, EXIT_NO_DATA, started, started_finished, cfg.data_dir, output_files, warnings,
            fetched_online,
        )

    # 离线本身不算降级：只要缓存里的两个口径都在且与缓存最新日一致，就算成功。
    # 真正的降级来自「QVIX 缺值 / 使用了陈旧缓存 / 某标的算不出来」。
    exit_code = EXIT_DEGRADED if degraded else EXIT_OK
    return RunResult(
        results, exit_code, started, started_finished, cfg.data_dir, output_files, warnings,
        fetched_online,
    )
