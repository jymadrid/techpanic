"""缓存读写：原子写、备份、校验、清单（manifest）。

这些守卫不是洁癖，而是踩过的坑：早期版本在上游返回空表时，会把一个只有表头的
文件覆盖到 801 行的缓存上，下一次运行直接崩，且没有备份可回滚。

规则：
* 任何写盘都先写 `.tmp`，flush + fsync 后 `os.replace`（同分区原子替换）。
* 覆盖前把旧文件复制为 `.bak.1/.2/.3`，最多保留 3 份。
* 每次读盘都做校验；校验不过就**拒绝使用**并降级，绝不静默使用脏数据。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


MANIFEST_VERSION = 1
BACKUP_KEEP = 3


@dataclass
class CheckResult:
    ok: bool
    level: str          # "info" | "warn" | "error"
    message: str = ""
    rows: int = 0
    last_date: str | None = None


def strip_bom_columns(df: pd.DataFrame) -> pd.DataFrame:
    """剥离列名开头的 BOM。

    pandas 3.x 在 encoding="utf-8-sig" 时自动剥离 BOM，但 pandas 2.x 不会，
    于是首列名会变成 "\ufeffdate"，导致后续找不到 date 列。
    为兼容 2.x/3.x（本项目声明支持 pandas>=2），这里统一防御。
    """
    df.columns = [str(c).lstrip("\ufeff") for c in df.columns]
    return df


def read_csv_any(path: Path, encoding_candidates: tuple[str, ...] = ("utf-8-sig", "utf-8", "gbk")):
    """按候选编码依次尝试读取 CSV，并统一剥离 BOM。失败返回 None。"""
    for enc in encoding_candidates:
        try:
            return strip_bom_columns(pd.read_csv(path, encoding=enc))
        except UnicodeDecodeError:
            continue
        except Exception:  # noqa: BLE001
            return None
    return None


def sha1_of(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def atomic_write_csv(df: pd.DataFrame, path: Path, encoding: str = "utf-8-sig") -> None:
    """原子写 CSV。任何异常都不会留下半个文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.to_csv(tmp, index=False, encoding=encoding)
    with open(tmp, "rb+") as fh:
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def atomic_write_text(text: str, path: Path, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding=encoding, newline="\n") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def atomic_write_json(obj: Any, path: Path) -> None:
    atomic_write_text(json.dumps(obj, ensure_ascii=False, indent=2), path, encoding="utf-8")


def backup(path: Path, keep: int = BACKUP_KEEP) -> None:
    """覆盖前轮转备份：xxx.csv → xxx.csv.bak.1（旧的依次后移）。"""
    if not path.is_file():
        return
    for i in range(keep, 1, -1):
        src = path.with_suffix(path.suffix + f".bak.{i - 1}")
        dst = path.with_suffix(path.suffix + f".bak.{i}")
        if src.is_file():
            shutil.copy2(src, dst)
    shutil.copy2(path, path.with_suffix(path.suffix + ".bak.1"))


def cleanup_backups(path: Path, keep: int = BACKUP_KEEP) -> None:
    for i in range(keep + 1, keep + 6):  # 清理历史遗留的更深层备份
        extra = path.with_suffix(path.suffix + f".bak.{i}")
        if extra.is_file():
            extra.unlink()


def check_frame(
    df: pd.DataFrame,
    *,
    name: str,
    min_rows: int,
    date_col: str = "date",
    close_col: str = "close",
    require_date_sorted: bool = True,
    max_zero_ratio: float = 0.0,
) -> CheckResult:
    """通用数据校验。返回 CheckResult；调用方决定降级还是抛错。"""
    if df is None or len(df) == 0:
        return CheckResult(False, "error", f"{name}: 数据为空", 0, None)
    if date_col not in df.columns:
        return CheckResult(False, "error", f"{name}: 缺少日期列 {date_col}", 0, None)
    if close_col not in df.columns:
        return CheckResult(False, "error", f"{name}: 缺少数值列 {close_col}", 0, None)

    d = pd.to_datetime(df[date_col], errors="coerce")
    if d.isna().any():
        return CheckResult(False, "error", f"{name}: 存在无法解析的日期", len(df), None)
    c = pd.to_numeric(df[close_col], errors="coerce")
    if c.isna().all():
        return CheckResult(False, "error", f"{name}: 数值列全为空", len(df), None)

    if len(df) < min_rows:
        return CheckResult(
            False, "error", f"{name}: 仅 {len(df)} 行，少于下限 {min_rows} 行", len(df), None
        )

    zero_ratio = float((c.fillna(0) == 0).mean())
    if zero_ratio > max_zero_ratio:
        return CheckResult(
            False,
            "error",
            f"{name}: 0 值占比 {zero_ratio:.1%}，超过上限 {max_zero_ratio:.1%}",
            len(df),
            None,
        )

    if require_date_sorted:
        if d.duplicated().any():
            return CheckResult(
                False, "error", f"{name}: 日期存在重复（{int(d.duplicated().sum())} 条）", len(df), None
            )
        if not d.is_monotonic_increasing:
            return CheckResult(False, "error", f"{name}: 日期未按升序排列", len(df), None)

    last = d.iloc[-1]
    return CheckResult(True, "info", f"{name}: 校验通过", len(df), str(last.date()))


def load_csv_checked(
    path: Path,
    *,
    name: str,
    min_rows: int,
    date_col: str = "date",
    close_col: str = "close",
    encoding_candidates: tuple[str, ...] = ("utf-8-sig", "utf-8", "gbk"),
) -> tuple[pd.DataFrame | None, CheckResult]:
    """读缓存并校验。文件不存在时返回 (None, 空结果) 而不是抛错。"""
    if not path.is_file():
        return None, CheckResult(False, "info", f"{name}: 本地无缓存（{path.name}）", 0, None)

    df = read_csv_any(path, encoding_candidates)
    if df is None:
        return None, CheckResult(False, "error", f"{name}: 读取失败或编码无法识别", 0, None)

    res = check_frame(df, name=name, min_rows=min_rows, date_col=date_col, close_col=close_col)
    if not res.ok:
        return None, res
    return df, res


# ---------------------------------------------------------------- manifest


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def load_manifest(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"version": MANIFEST_VERSION, "datasets": {}}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001  # pragma: no cover - 损坏的清单不致命
        return {"version": MANIFEST_VERSION, "datasets": {}}
    raw.setdefault("version", MANIFEST_VERSION)
    raw.setdefault("datasets", {})
    return raw


def update_manifest(
    path: Path,
    dataset: str,
    *,
    rows: int,
    last_date: str | None,
    source: str,
    file_path: Path | None = None,
    stale_days: int = 0,
) -> dict[str, Any]:
    """记录某个数据集的最后一次成功抓取，用于断点续抓与新鲜度告警。"""
    man = load_manifest(path)
    entry: dict[str, Any] = {
        "rows": int(rows),
        "last_date": last_date,
        "source": source,
        "fetched_at": _now_iso(),
        "stale_trading_days": int(stale_days),
    }
    if file_path is not None and file_path.is_file():
        entry["sha1"] = sha1_of(file_path)
        entry["bytes"] = file_path.stat().st_size
    man["datasets"][dataset] = entry
    man["updated_at"] = entry["fetched_at"]
    atomic_write_json(man, path)
    return man


def manifest_entry(path: Path, dataset: str) -> dict[str, Any] | None:
    return load_manifest(path).get("datasets", {}).get(dataset)


def last_success_date(path: Path, dataset: str) -> str | None:
    entry = manifest_entry(path, dataset)
    return None if not entry else entry.get("last_date")


@dataclass
class IntegrityResult:
    ok: bool
    detail: str = ""


def verify_integrity(cache_file: Path, manifest_path: Path, dataset: str) -> IntegrityResult:
    """校验缓存文件是否与 manifest 记录的一致。

    为什么值得做：manifest 早就写下了 sha1 与字节数，但**从来没被读过**。
    于是手工替换或外部程序截断一个缓存文件后，程序会照常读它算出读数 ——
    实测把 798 行的缓存换成 400 行后，读数从 54.40 变 51.29、分位从 50.7 变 48.5，
    而退出码与告警**毫无变化**。用户没有任何途径察觉。

    只在 manifest 有记录且本地文件存在时校验；无记录（首次运行）时放行。
    """
    if not cache_file.is_file():
        return IntegrityResult(False, f"{dataset}：缓存文件不存在")
    entry = manifest_entry(manifest_path, dataset)
    if not entry:
        return IntegrityResult(True, "")  # 首次运行，没有可比对的基准

    expected_sha = entry.get("sha1")
    expected_bytes = entry.get("bytes")
    if expected_bytes is not None:
        actual_bytes = cache_file.stat().st_size
        if int(expected_bytes) != actual_bytes:
            return IntegrityResult(
                False,
                f"{dataset}：缓存已被外部改动（记录 {expected_bytes} 字节，"
                f"实际 {actual_bytes} 字节）",
            )
    if expected_sha:
        actual_sha = sha1_of(cache_file)
        if actual_sha != expected_sha:
            return IntegrityResult(
                False, f"{dataset}：缓存内容与记录不一致（sha1 不匹配）"
            )
    return IntegrityResult(True, "")


def snapshot_dir(cache_dir: Path) -> Path:
    """回滚点目录：每次运行前把将要覆盖的文件复制到这里（保留 7 天）。"""
    return cache_dir / "_snapshots"


def rollback_available(cache_dir: Path) -> list[Path]:
    root = snapshot_dir(cache_dir)
    if not root.is_dir():
        return []
    return sorted([p for p in root.iterdir() if p.is_dir()], reverse=True)


def prune_snapshots(cache_dir: Path, keep: int = 7) -> None:
    for old in rollback_available(cache_dir)[keep:]:
        shutil.rmtree(old, ignore_errors=True)


def ensure_finite(series: pd.Series) -> pd.Series:
    """把 inf 视为 NaN，避免污染下游计算。"""
    return series.replace([np.inf, -np.inf], np.nan)
