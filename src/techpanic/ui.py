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


def force_utf8_stdio() -> None:
    """把 stdout/stderr 切到 UTF-8。

    为什么必须做：中文 Windows 默认代码页是 GBK（cp936）。
    后果有两个，都很严重：

    1. --json 写出的是 GBK 字节流，下游按 UTF-8 读取会 UnicodeDecodeError
       （例如「科创」的 GBK 编码 0xBF 0xC6 不是合法 UTF-8）。
    2. 界面里的「⚠」在 cp936 里**没有**映射，重定向或接管道时会直接抛
       UnicodeEncodeError，整个程序以退出码 1 崩掉。

    errors="replace" 是最后的兜底：即使终端真的不支持某个字符，
    也只退化成一个问号，绝不让程序崩。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):  # pragma: no cover - 极端环境下放弃兜底
            continue


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

    def __init__(
        self,
        *,
        color: bool = True,
        quiet: bool = False,
        stream=None,
        suppress_info: bool = False,
    ) -> None:
        self.color = color
        self.quiet = quiet
        # 人类可读文本的输出去向。--json 时全部改走 stderr，
        # 保证 stdout 上只有一段合法 JSON（可以安全管道给 jq / json.load）。
        self.stream = stream if stream is not None else sys.stdout
        # 纯 JSON 模式下不再打印「已保存 N 个文件」这类附加信息。
        self.suppress_info = suppress_info
        self._progress_active = False

    # ------------------------------------------------------------ 基础
    def _c(self, text: str, code: str) -> str:
        return f"{code}{text}{RESET}" if self.color else text

    def info(self, msg: str = "") -> None:
        if self.suppress_info:
            return
        if not self.quiet:
            print(msg, file=self.stream, flush=True)

    def step(self, msg: str) -> None:
        self._clear_progress()
        if self.suppress_info:
            return
        if not self.quiet:
            print(self._c(msg, BOLD), file=self.stream, flush=True)

    def ok(self, msg: str) -> None:
        if self.suppress_info:
            return
        if not self.quiet:
            print(f"      {self._c('OK ', GREEN)} {msg}", file=self.stream, flush=True)

    def warn(self, msg: str) -> None:
        if self.suppress_info:
            return
        if not self.quiet:
            print(f"      {self._c('⚠', YELLOW)} {msg}", file=self.stream, flush=True)

    def err(self, msg: str) -> None:
        print(f"{self._c('✖', RED)} {msg}", file=sys.stderr, flush=True)

    def hint(self, msg: str) -> None:
        print(f"  {self._c('下一步：', BOLD)}{msg}", file=self.stream, flush=True)

    def rule(self, char: str = "-") -> None:
        if self.suppress_info:
            return
        if not self.quiet:
            print(self._c(char * WIDTH, DIM), file=self.stream, flush=True)

    def blank(self) -> None:
        if self.suppress_info:
            return
        if not self.quiet:
            print(flush=True)

    # ------------------------------------------------------------ 进度
    def progress(self, label: str, got: int, total: int | None, elapsed: float) -> None:
        """单行覆盖式进度；非 TTY 时静默（避免日志被刷屏）。"""
        if self.quiet or not self.stream.isatty():
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
        print("\r" + text[:110], end="", file=self.stream, flush=True)
        self._progress_active = True

    def _clear_progress(self) -> None:
        if self._progress_active:
            print("\r" + " " * 110 + "\r", end="", file=self.stream, flush=True)
            self._progress_active = False

    def end_progress(self) -> None:
        self._clear_progress()

    # ------------------------------------------------------------ 读数卡
    def _p(self, msg: str = "") -> None:
        """读数卡/页脚的统一出口：--json 模式下整块静默。"""
        if self.suppress_info:
            return
        print(msg, file=self.stream, flush=True)

    def card(self, tr) -> None:
        """打印一个标的的双口径读数卡。"""
        if self.quiet or self.suppress_info:
            return
        latest = tr.latest_date.date() if tr.latest_date is not None else "-"
        self._p(f"【{self._c(tr.target.name, BOLD)}】价格数据截止 {latest}")
        self._p()

        if tr.has_full:
            pct = "-" if tr.full_percentile is None else f"{tr.full_percentile:.0f}%"
            level_a = tr.full_level
            self._p(f"  A | 完整口径（三因子，含期权）  数据日 {tr.full_date.date()}")
            self._p(
                f"      PI = {tr.full_value:5.1f}  "
                f"【{self._c(level_a, LEVEL_COLOR.get(level_a, ''))}】  历史分位 {pct}"
            )
            # 用**完整口径那一行**的成分：full_value 正是用这三个数算出来的。
            # 以前这里取的是"最新行"的成分，而那行的 F 通常是 NaN，
            # 于是读数卡显示「前瞻恐惧 待发布」，可读数明明是用某个 F 算出来的 ——
            # 展示与计算不自洽，用户无法复算。
            fc = tr.full_components or tr.components
            s = _num(fc.get("S"))
            a = _num(fc.get("A"))
            f = _num(fc.get("F"))
            self._p(f"      成分：突发性 {s}  不对称性 {a}  前瞻恐惧 {f}")
        else:
            self._p("  A | 完整口径（三因子，含期权）  无值")
            self._p(f"      {self._c('未取得可用的期权隐含波动率数据，只能输出价格口径', YELLOW)}")
        self._p()

        if tr.has_price:
            pct = "-" if tr.price_percentile is None else f"{tr.price_percentile:.0f}%"
            same = tr.full_date is not None and tr.price_date == tr.full_date
            tag = "（与 A 同日）" if same else "（已取得价格数据的最新一日）"
            level_b = tr.price_level
            self._p(f"  B | 即时口径（两因子，仅价格）  数据日 {tr.price_date.date()}{tag}")
            self._p(
                f"      PI = {tr.price_value:5.1f}  "
                f"【{self._c(level_b, LEVEL_COLOR.get(level_b, ''))}】  历史分位 {pct}"
            )
            ret = "-" if tr.ret is None else f"{tr.ret:+.2f}%"
            ret5 = "-" if tr.ret5 is None else f"{tr.ret5:+.2f}%"
            self._p(f"      当日 {ret}  近5日 {ret5}  方向 {tr.direction}")
            s = _num(tr.components.get("S"))
            a = _num(tr.components.get("A"))
            # 即时口径只用 S 和 A；F 在此口径下不被使用，
            # 所以这里写「不参与」而不是「待发布」——后者会让人以为
            # 这个读数还在等 QVIX，实际上它本来就不需要 QVIX。
            self._p(f"      成分：突发性 {s}  不对称性 {a}  （前瞻恐惧不参与本口径）")
        else:
            self._p("  B | 即时口径（两因子，仅价格）  无值")
        self._p()

        for note in tr.notes:
            self._p(f"  {self._c('⚠', YELLOW)} {note}")
        if tr.notes:
            self._p()

    def footer(self) -> None:
        if self.quiet or self.suppress_info:
            return
        self._p("=" * WIDTH)
        self._p("  两个读数怎么用：")
        self._p("    A 完整口径 = 三因子（含期权隐含波动率）→ 本项目完整读数，截止日期取决于当前 QVIX 数据源")
        self._p("    B 即时口径 = 两因子（只用价格）        → 已取得当日收盘价后才能计算当日读数")
        self._p("    两者口径不同，差值不单是「今天的冲击」，请勿直接相减解读。")
        self._p()
        self._p("  判读：PI 高 + 方向向下 = 真恐慌；PI 高 + 方向向上 = 狂热（不是恐慌）")
        self._p("  注意：PI 是状态度量（温度计），不预测涨跌方向，不构成投资建议。")
        self._p("=" * WIDTH)


def elapsed_str(seconds: float) -> str:
    """人类友好的耗时显示。"""
    if seconds < 60:
        return f"{seconds:.1f} 秒"
    return f"{int(seconds // 60)} 分 {seconds % 60:.0f} 秒"
