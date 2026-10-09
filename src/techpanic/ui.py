"""终端界面：颜色、进度、读数卡。零第三方依赖（只用 ANSI 转义）。

零基础用户的三条硬要求：

1. 任何时候都要能看到「在动」（QVIX 上游实测可能慢到 2 分钟）。
2. 失败必须是人话 + 下一步，不能是 traceback。
3. 默认按 74 列排版，Windows 默认控制台也不会折行错乱。
"""

from __future__ import annotations

import sys

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
CYAN = "\033[36m"
MAGENTA = "\033[35m"

LEVEL_COLOR = {
    "平静": GREEN,
    "常态": BLUE,
    "警戒": YELLOW,
    "恐慌": MAGENTA,
    "极度恐慌": RED,
    "无值": DIM,
}

WIDTH = 74


def _num(value, digits: int = 0) -> str:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return "待发布"
    if f != f:  # NaN
        return "待发布"
    return f"{f:.{digits}f}"


class Ui:
    """把彩色、进度、紧凑输出集中到一处，便于 --no-color / --quiet 统一切换。"""

    def __init__(self, *, color: bool = True, quiet: bool = False) -> None:
        self.color = color
        self.quiet = quiet
        self._progress_active = False

    # ------------------------------------------------------------ 基础
    def _c(self, text: str, code: str) -> str:
        return f"{code}{text}{RESET}" if self.color else text

    def info(self, msg: str = "") -> None:
        if not self.quiet:
            print(msg, flush=True)

    def step(self, msg: str) -> None:
        self._clear_progress()
        if not self.quiet:
            print(self._c(msg, BOLD), flush=True)

    def ok(self, msg: str) -> None:
        if not self.quiet:
            print(f"      {self._c('OK ', GREEN)} {msg}", flush=True)

    def warn(self, msg: str) -> None:
        if not self.quiet:
            print(f"      {self._c('⚠', YELLOW)} {msg}", flush=True)

    def err(self, msg: str) -> None:
        print(f"{self._c('✖', RED)} {msg}", file=sys.stderr, flush=True)

    def hint(self, msg: str) -> None:
        print(f"  {self._c('下一步：', BOLD)}{msg}", flush=True)

    def rule(self, char: str = "-") -> None:
        if not self.quiet:
            print(self._c(char * WIDTH, DIM), flush=True)

    def blank(self) -> None:
        if not self.quiet:
            print(flush=True)

    # ------------------------------------------------------------ 进度
    def progress(self, label: str, got: int, total: int | None, elapsed: float) -> None:
        """单行覆盖式进度；非 TTY 时静默（避免日志被刷屏）。"""
        if self.quiet or not sys.stdout.isatty():
            return
        mb = got / 1024 / 1024
        if total:
            pct = min(100.0, got / total * 100.0)
            filled = int(pct / 100 * 24)
            bar = "#" * filled + "." * (24 - filled)
            text = (
                f"      {label} [{bar}] {pct:5.1f}%  "
                f"{mb:.2f}/{total / 1024 / 1024:.2f} MB  {elapsed:.1f}s"
            )
        else:
            text = f"      {label} 已接收 {mb:.2f} MB  {elapsed:.1f}s"
        print("\r" + text[:110], end="", flush=True)
        self._progress_active = True

    def _clear_progress(self) -> None:
        if self._progress_active:
            print("\r" + " " * 110 + "\r", end="", flush=True)
            self._progress_active = False

    def end_progress(self) -> None:
        self._clear_progress()

    # ------------------------------------------------------------ 读数卡
    def card(self, tr) -> None:
        """打印一个标的的双口径读数卡。"""
        if self.quiet:
            return
        latest = tr.latest_date.date() if tr.latest_date is not None else "-"
        print(f"【{self._c(tr.target.name, BOLD)}】最新交易日 {latest}")
        print()

        if tr.has_full:
            pct = "-" if tr.full_percentile is None else f"{tr.full_percentile:.0f}%"
            level_a = tr.full_level
            print(f"  A | 完整口径（三因子，含期权）  数据日 {tr.full_date.date()}")
            print(
                f"      PI = {tr.full_value:5.1f}  "
                f"【{self._c(level_a, LEVEL_COLOR.get(level_a, ''))}】  历史分位 {pct}"
            )
            s = _num(tr.components.get("S"))
            a = _num(tr.components.get("A"))
            f = _num(tr.components.get("F"))
            print(f"      成分：突发性 {s}  不对称性 {a}  前瞻恐惧 {f}")
        else:
            print("  A | 完整口径（三因子，含期权）  无值")
            print(f"      {self._c('期权隐含波动率尚未发布，今日只有即时口径', YELLOW)}")
        print()

        if tr.has_price:
            pct = "-" if tr.price_percentile is None else f"{tr.price_percentile:.0f}%"
            same = tr.full_date is not None and tr.price_date == tr.full_date
            tag = "（与 A 同日）" if same else "（含最新交易日）"
            level_b = tr.price_level
            print(f"  B | 即时口径（两因子，仅价格）  数据日 {tr.price_date.date()}{tag}")
            print(
                f"      PI = {tr.price_value:5.1f}  "
                f"【{self._c(level_b, LEVEL_COLOR.get(level_b, ''))}】  历史分位 {pct}"
            )
            ret = "-" if tr.ret is None else f"{tr.ret:+.2f}%"
            ret5 = "-" if tr.ret5 is None else f"{tr.ret5:+.2f}%"
            print(f"      当日 {ret}  近5日 {ret5}  方向 {tr.direction}")
            s = _num(tr.components.get("S"))
            a = _num(tr.components.get("A"))
            print(f"      成分：突发性 {s}  不对称性 {a}  前瞻恐惧 待发布")
        else:
            print("  B | 即时口径（两因子，仅价格）  无值")
        print()

        for note in tr.notes:
            print(f"  {self._c('⚠', YELLOW)} {note}")
        if tr.notes:
            print()

    def footer(self) -> None:
        if self.quiet:
            return
        print("=" * WIDTH)
        print("  两个读数怎么用：")
        print("    A 完整口径 = 三因子（含期权隐含波动率）→ 官方读数，QVIX 盘后发布，可能滞后 1 天")
        print("    B 即时口径 = 两因子（只用价格）        → 当日收盘即可算，看最新跳变")
        print("    两者口径不同，差值不单是「今天的冲击」，请勿直接相减解读。")
        print()
        print("  判读：PI 高 + 方向向下 = 真恐慌；PI 高 + 方向向上 = 狂热（不是恐慌）")
        print("  注意：PI 是状态度量（温度计），不预测涨跌方向，不构成投资建议。")
        print("=" * WIDTH)


def elapsed_str(seconds: float) -> str:
    """人类友好的耗时显示。"""
    if seconds < 60:
        return f"{seconds:.1f} 秒"
    return f"{int(seconds // 60)} 分 {seconds % 60:.0f} 秒"
