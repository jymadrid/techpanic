"""配置装配：默认值 ← 用户 TOML ← 环境变量 ← 命令行。

只依赖标准库 tomllib；按 utf-8-sig 读取，避免记事本另存后带 BOM 解析失败。
配置文件默认位于 <数据目录>/config.toml，全部字段可选。
"""

from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .errors import ConfigError

ENV_PREFIX = "TECHPANIC_"


@dataclass(frozen=True)
class Target:
    """一个跟踪标的：指数行情 + 对应的期权隐含波动率序列。"""

    key: str
    name: str
    symbol: str
    qvix: str
    role: str = "tech"


DEFAULT_TARGETS: tuple[Target, ...] = (
    Target("tech_kcb", "科创50", "sh000688", "kcb", "tech"),
    Target("tech_cyb", "创业板指", "sz399006", "cyb", "tech"),
    Target("sse50", "上证50（非科技，仅方法验证）", "sh000016", "50etf", "validation"),
)


@dataclass(frozen=True)
class IndexConfig:
    """指数定义。改动这些参数会改变历史序列，必须在 CHANGELOG 中记录。"""

    weight_s: float = 0.40
    weight_a: float = 0.35
    weight_f: float = 0.25
    ema_span: int = 3
    z_min_periods: int = 60
    rv_window: int = 20
    rv_short: int = 5
    annualization: int = 252
    level_anchor: str = "expanding"
    frozen_anchors: tuple[float, float, float, float] = (45.0, 55.0, 63.0, 71.0)

    def xcheck(self) -> None:
        total = self.weight_s + self.weight_a + self.weight_f
        if abs(total - 1.0) > 1e-9:
            raise ConfigError(f"[index] 三个权重之和必须为 1.0，当前为 {total:.6f}")
        if self.level_anchor not in {"expanding", "full_sample", "frozen"}:
            raise ConfigError(
                "[index] level_anchor 只能是 expanding / full_sample / frozen，"
                f"当前为 {self.level_anchor!r}"
            )
        if self.z_min_periods < 2:
            raise ConfigError("[index] z_min_periods 至少为 2")
        if self.rv_window < 2 or self.rv_short < 2:
            raise ConfigError("[index] rv_window 与 rv_short 至少为 2")


@dataclass(frozen=True)
class NetworkConfig:
    source_index: str = "sina"
    timeout_connect: float = 5.0
    timeout_read: float = 15.0
    # QVIX 上游实测：914 KB 文件耗时 1.7s~123s，服务端限速约 8 KB/s 且不支持压缩。
    # 因此「单次读超时」给足 85 秒，「总预算」封顶 240 秒（含重试）——
    # 超预算立刻降级用缓存，而不是让用户无限等待。
    timeout_qvix: float = 150.0
    budget_qvix: float = 300.0
    # 缓存新鲜度：在此小时数内成功抓取过的数据集不再重复下载。
    # 主要意义是省掉 QVIX 那 2~4 分钟的慢下载；用 --refresh 可强制忽略。
    cache_ttl_hours: float = 6.0
    retries: int = 3
    retries_qvix: int = 2
    backoff: tuple[float, ...] = (0.5, 1.5, 4.0)
    jitter: float = 0.30
    proxy: str | None = None
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )


@dataclass(frozen=True)
class OutputConfig:
    formats: tuple[str, ...] = ("csv", "json")
    encoding: str = "utf-8-sig"
    json_schema: str = "v1"


@dataclass(frozen=True)
class UIConfig:
    lang: str = "zh-CN"
    color: str = "auto"
    progress: bool = True


@dataclass(frozen=True)
class AppConfig:
    data_dir: Path
    config_path: Path | None
    targets: tuple[Target, ...] = DEFAULT_TARGETS
    index: IndexConfig = field(default_factory=IndexConfig)
    network: NetworkConfig = field(default_factory=NetworkConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    ui: UIConfig = field(default_factory=UIConfig)
    offline: bool = False
    refresh: bool = False
    as_of: str | None = None
    quiet: bool = False
    verbose: bool = False
    json_stdout: bool = False

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def index_dir(self) -> Path:
        return self.cache_dir / "index_daily"

    @property
    def qvix_dir(self) -> Path:
        return self.cache_dir / "qvix"

    @property
    def output_dir(self) -> Path:
        return self.data_dir / "output"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def manifest_path(self) -> Path:
        return self.cache_dir / "_manifest.json"

    def tech_targets(self) -> tuple[Target, ...]:
        return tuple(t for t in self.targets if t.role == "tech")

    def ensure_dirs(self) -> None:
        for d in (self.cache_dir, self.index_dir, self.qvix_dir, self.output_dir, self.log_dir):
            d.mkdir(parents=True, exist_ok=True)


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ConfigError(f"无法读取配置文件 {path}：{exc}") from exc
    try:
        return tomllib.loads(raw.decode("utf-8-sig"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ConfigError(
            f"配置文件 {path} 格式有误：{exc}\n"
            "   提示：请确认它是合法的 TOML（不要用记事本另存为 ANSI 编码）。"
        ) from exc


def _env(name: str) -> str | None:
    return os.environ.get(ENV_PREFIX + name)


def _env_float(name: str, default: float) -> float:
    raw = _env(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"环境变量 {ENV_PREFIX}{name} 必须是数字，当前为 {raw!r}") from exc


def _targets_from_toml(raw: dict[str, Any]) -> tuple[Target, ...]:
    tbl = raw.get("targets")
    if not isinstance(tbl, dict) or not tbl:
        return DEFAULT_TARGETS
    targets: list[Target] = []
    for key, spec in tbl.items():
        if not isinstance(spec, dict):
            raise ConfigError(f"[targets.{key}] 必须是一个表（table）")
        missing = {"name", "symbol", "qvix"} - set(spec)
        if missing:
            raise ConfigError(f"[targets.{key}] 缺少必填字段：{', '.join(sorted(missing))}")
        targets.append(
            Target(
                key=key,
                name=str(spec["name"]),
                symbol=str(spec["symbol"]),
                qvix=str(spec["qvix"]),
                role=str(spec.get("role", "tech")),
            )
        )
    return tuple(targets)


def load_config(
    *,
    data_dir: str | os.PathLike[str] | None = None,
    config_path: str | os.PathLike[str] | None = None,
    overrides: dict[str, Any] | None = None,
) -> AppConfig:
    """按「默认值 ← 用户 TOML ← 环境变量 ← 命令行」组装配置。"""

    overrides = dict(overrides or {})

    resolved_data_dir = Path(
        overrides.pop("data_dir", None) or _env("DATA_DIR") or data_dir or (Path.cwd() / "data")
    ).expanduser()

    raw_path = config_path or _env("CONFIG") or None
    resolved_config = Path(raw_path).expanduser() if raw_path else resolved_data_dir / "config.toml"
    raw: dict[str, Any] = _read_toml(resolved_config) if resolved_config.is_file() else {}

    net_tbl = raw.get("network", {}) or {}
    if not isinstance(net_tbl, dict):
        raise ConfigError("[network] 必须是一个表（table）")
    # 关键：这里的回退默认值必须取自 NetworkConfig 的字段默认值本身，
    # 否则改了 dataclass 默认值却忘记改这里，配置会被静默覆盖回旧值
    # （本项目踩过：timeout_qvix 一直是 45 秒，导致 QVIX 反复超时）。
    _nd = NetworkConfig()
    backoff_raw = net_tbl.get("backoff", _nd.backoff)
    network = NetworkConfig(
        source_index=str(_env("SOURCE_INDEX") or net_tbl.get("source_index", _nd.source_index)),
        timeout_connect=float(net_tbl.get("timeout_connect", _nd.timeout_connect)),
        timeout_read=float(net_tbl.get("timeout_read", _nd.timeout_read)),
        timeout_qvix=_env_float(
            "TIMEOUT_QVIX", float(net_tbl.get("timeout_qvix", _nd.timeout_qvix))
        ),
        budget_qvix=float(net_tbl.get("budget_qvix", _nd.budget_qvix)),
        cache_ttl_hours=float(net_tbl.get("cache_ttl_hours", _nd.cache_ttl_hours)),
        retries=int(net_tbl.get("retries", _nd.retries)),
        retries_qvix=int(net_tbl.get("retries_qvix", _nd.retries_qvix)),
        backoff=tuple(float(x) for x in backoff_raw),
        jitter=float(net_tbl.get("jitter", _nd.jitter)),
        proxy=_env("PROXY") or net_tbl.get("proxy") or None,
    )

    idx_tbl = raw.get("index", {}) or {}
    if not isinstance(idx_tbl, dict):
        raise ConfigError("[index] 必须是一个表（table）")
    weights = idx_tbl.get("weights", {}) or {}
    anchors = idx_tbl.get("frozen_anchors", {}) or {}
    index_cfg = IndexConfig(
        weight_s=float(weights.get("S", 0.40)),
        weight_a=float(weights.get("A", 0.35)),
        weight_f=float(weights.get("F", 0.25)),
        ema_span=int(idx_tbl.get("ema_span", 3)),
        z_min_periods=int(idx_tbl.get("z_min_periods", 60)),
        rv_window=int(idx_tbl.get("rv_window", 20)),
        rv_short=int(idx_tbl.get("rv_short", 5)),
        annualization=int(idx_tbl.get("annualization", 252)),
        level_anchor=str(idx_tbl.get("level_anchor", "expanding")),
        frozen_anchors=(
            float(anchors.get("p25", 45.0)),
            float(anchors.get("p50", 55.0)),
            float(anchors.get("p75", 63.0)),
            float(anchors.get("p90", 71.0)),
        )
        if anchors
        else (45.0, 55.0, 63.0, 71.0),
    )
    index_cfg.xcheck()

    out_tbl = raw.get("output", {}) or {}
    ui_tbl = raw.get("ui", {}) or {}
    output_cfg = OutputConfig(
        formats=tuple(str(x) for x in out_tbl.get("formats", ("csv", "json"))),
        encoding=str(out_tbl.get("encoding", "utf-8-sig")),
        json_schema=str(out_tbl.get("json_schema", "v1")),
    )
    ui_cfg = UIConfig(
        lang=str(ui_tbl.get("lang", "zh-CN")),
        color=str(ui_tbl.get("color", "auto")),
        progress=bool(ui_tbl.get("progress", True)),
    )

    cfg = AppConfig(
        data_dir=resolved_data_dir,
        config_path=resolved_config if resolved_config.is_file() else None,
        targets=_targets_from_toml(raw),
        index=index_cfg,
        network=network,
        output=output_cfg,
        ui=ui_cfg,
        offline=bool(overrides.pop("offline", _env("OFFLINE") in {"1", "true", "yes"})),
        refresh=bool(overrides.pop("refresh", False)),
        as_of=overrides.pop("as_of", None),
        quiet=bool(overrides.pop("quiet", False)),
        verbose=bool(overrides.pop("verbose", False)),
        json_stdout=bool(overrides.pop("json_stdout", False)),
    )
    if overrides:
        raise ConfigError(f"未知配置项：{', '.join(sorted(overrides))}")
    return cfg


def with_targets_subset(cfg: AppConfig, *, include_validation: bool) -> AppConfig:
    """按 --with-sse50 决定是否纳入非科技验证标的。"""
    if include_validation:
        return cfg
    return replace(cfg, targets=tuple(t for t in cfg.targets if t.role != "validation"))


def is_tty() -> bool:
    try:
        return sys.stdout.isatty()
    except Exception:  # noqa: BLE001  # pragma: no cover
        return False
