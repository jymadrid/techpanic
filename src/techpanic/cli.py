"""命令行入口：参数解析、顶层异常兜底、退出码。

零基础用户的体验全部收敛在这里：**任何失败都只能看到中文人话 + 下一步建议**，
绝不允许把 Python 堆栈打印出来（除非显式加 --debug）。

退出码约定
----------
0	完全成功，两个口径都是最新数据
2	部分降级：QVIX 未发布 / 使用缓存 / 离线模式
3	无可用数据：无网络且无本地缓存
4	运行环境问题：依赖缺失或版本不符
5	参数错误
130	用户中断（Ctrl+C）
"""

from __future__ import annotations

import argparse
import sys
import traceback

from . import __version__
from .config import load_config, with_targets_subset
from .errors import (
    EXIT_CODE_BY_TYPE,
    EXIT_DEGRADED,
    EXIT_ENV,
    EXIT_INTERRUPTED,
    EXIT_NO_DATA,
    EXIT_OK,
    ConfigError,
    DependencyError,
    TechpanicError,
    exit_code_for,
)

DESCRIPTION = "科技板块恐慌指数 PI：一条命令拿到今天 A 股科技板块的恐慌读数。"

EPILOG = """示例
  python -m techpanic                    抓取公开数据并打印今日读数（默认）
  python -m techpanic --offline          只用本地缓存，零 HTTP 请求
  python -m techpanic --refresh          忽略缓存，强制重新抓取
  python -m techpanic --json             输出机器可读 JSON（供 CI / 徽章）
  python -m techpanic --date 2026-09-30  只使用该日期及之前的数据
  python -m techpanic --with-sse50       额外计算上证50（非科技，仅方法验证）

退出码
  0 成功   2 部分降级   3 无可用数据   4 环境问题   5 参数错误   130 中断

数据来源全部为公开免费接口，不需要任何账号或 API Key。
本工具为研究/观测级指标，不是官方波动率指数，不预测涨跌方向，不构成投资建议。
"""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="techpanic",
        description=DESCRIPTION,
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action="version", version=f"techpanic {__version__}")
    p.add_argument("--data-dir", metavar="PATH", help="数据目录（缓存/输出/日志），默认 ./data")
    p.add_argument("--config", metavar="PATH", help="配置文件路径，默认 <数据目录>/config.toml")
    p.add_argument("--offline", action="store_true", help="只用本地缓存，不发起任何网络请求")
    p.add_argument("--refresh", action="store_true", help="忽略缓存，强制重新抓取")
    p.add_argument("--date", dest="as_of", metavar="YYYY-MM-DD", help="只使用该日期及之前的数据")
    p.add_argument("--json", dest="json_stdout", action="store_true", help="把 JSON 结果打印到标准输出")
    p.add_argument("--with-sse50", action="store_true", help="额外计算上证50（非科技，仅方法验证）")
    p.add_argument("--no-color", action="store_true", help="关闭彩色输出（重定向到文件时自动关闭）")
    p.add_argument("--quiet", action="store_true", help="只输出最终结果")
    p.add_argument("--verbose", action="store_true", help="输出更多诊断信息")
    p.add_argument("--debug", action="store_true", help="失败时打印完整堆栈（报 issue 时使用）")
    p.add_argument(
        "--check",
        action="store_true",
        help="只做环境与数据源自检，不计算",
    )
    return p


def _preflight() -> None:
    """启动前检查关键依赖，缺失时给中文指引而不是 ImportError 堆栈。"""
    missing: list[str] = []
    for module, package in (("numpy", "numpy"), ("pandas", "pandas"), ("requests", "requests")):
        try:
            __import__(module)
        except Exception:  # noqa: BLE001
            missing.append(package)
    if missing:
        raise DependencyError(
            f"缺少依赖：{', '.join(missing)}。\n"
            "   请先安装：pip install -r requirements.txt"
        )
    try:
        import akshare  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        raise DependencyError(
            f"缺少依赖 akshare，无法抓取数据。\n   详细信息：{type(exc).__name__}: {exc}"
        ) from exc


def _want_color(args: argparse.Namespace, cfg) -> bool:
    if args.no_color:
        return False
    if cfg.ui.color == "never":
        return False
    if cfg.ui.color == "always":
        return True
    return sys.stdout.isatty()


def _check_sources(cfg, ui) -> int:
    """--check：不抓数据、只探活并给出结论。"""
    from .fetch import index_daily

    ui.step("环境与数据源自检")
    ui.ok(f"Python {sys.version.split()[0]}")
    try:
        import akshare

        ui.ok(f"akshare {getattr(akshare, '__version__', '未知版本')}")
    except Exception as exc:  # noqa: BLE001
        ui.err(f"akshare 不可用：{exc}")
        return EXIT_ENV

    alive, detail = index_daily.is_index_source_alive(cfg.network)
    if alive:
        ui.ok(f"指数源可用（{detail}）")
    else:
        ui.err(f"指数源不可用：{detail}")
        ui.hint("检查网络，或在 config.toml 的 [network] 段设置 proxy。")

    ui.info()
    ui.info(f"数据目录：{cfg.data_dir}")
    ui.info(f"配置文件：{cfg.config_path or '（未找到，使用默认值）'}")
    ui.info(f"跟踪标的：{', '.join(t.name for t in cfg.targets)}")
    return EXIT_OK if alive else EXIT_DEGRADED


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    from .ui import Ui, elapsed_str

    try:
        cfg = load_config(
            data_dir=args.data_dir,
            config_path=args.config,
            overrides={
                "offline": args.offline,
                "refresh": args.refresh,
                "as_of": args.as_of,
                "quiet": args.quiet,
                "verbose": args.verbose,
                "json_stdout": args.json_stdout,
            },
        )
        cfg = with_targets_subset(cfg, include_validation=args.with_sse50)
    except ConfigError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        print(f"  {exc.hint()}", file=sys.stderr)
        return EXIT_CODE_BY_TYPE[ConfigError]

    ui = Ui(color=_want_color(args, cfg), quiet=args.quiet)

    try:
        _preflight()

        if args.check:
            return _check_sources(cfg, ui)

        from . import report as report_mod
        from .pipeline import run

        ui.blank()
        ui.info("=" * 74)
        ui.info(f"  科技板块恐慌指数 PI  v{__version__}      数据目录 {cfg.data_dir}")
        if cfg.offline:
            ui.info("  模式：离线（只读本地缓存，零 HTTP 请求）")
        ui.info("=" * 74)
        ui.blank()

        result = run(cfg, say=ui.step, progress=ui.progress)
        ui.end_progress()

        ui.blank()
        ui.info("-" * 74)
        ui.info()
        for tr in result.targets:
            if tr.frame is None:
                ui.warn(f"{tr.target.name}：未产出读数")
                continue
            ui.card(tr)
        ui.footer()

        files = list(result.output_files)
        from .pipeline import RunResult  # noqa: F401

        files = report_mod.write_run_files(result.targets, cfg, result.exit_code, result.elapsed)
        ui.info(f"  已保存（{len(files)} 个文件）：")
        for f in files:
            ui.info(f"    {f}")
        ui.info(f"  耗时 {elapsed_str(result.elapsed)}")
        ui.info()

        if result.exit_code == EXIT_NO_DATA:
            ui.err("没有拿到任何数据，本次无法给出读数。")
            ui.blank()
            ui.hint(
                "1) 先确认能上网，然后重新运行：python -m techpanic\n"
                "   2) 如果一直失败，运行 python -m techpanic --check 看是数据源还是网络问题\n"
                "   3) 换用镜像/代理：在 config.toml 的 [network] 段设置 proxy\n"
                "   4) 只想看已有缓存：python -m techpanic --offline"
            )
        elif result.exit_code == EXIT_DEGRADED:
            ui.warn("本次读数有降级项（见上方 ⚠ 说明），这不是程序错误。")
        return result.exit_code

    except KeyboardInterrupt:
        ui.end_progress()
        ui.err("已中断。缓存文件不会被破坏（所有写盘都是原子的）。")
        return EXIT_INTERRUPTED

    except TechpanicError as exc:
        ui.end_progress()
        ui.err(f"{exc}")
        hint = exc.hint()
        if hint:
            ui.hint(hint)
        if args.debug:
            traceback.print_exc()
        return exit_code_for(exc)

    except Exception as exc:  # noqa: BLE001
        ui.end_progress()
        ui.err(f"发生未预期的错误：{type(exc).__name__}: {exc}")
        ui.hint(
            "1) 先重试一次：python -m techpanic\n"
            "   2) 加 --debug 查看完整堆栈；\n"
            "   3) 到 GitHub Issues 反馈，并附上 data/logs/ 下最新的日志文件。"
        )
        if args.debug:
            traceback.print_exc()
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
