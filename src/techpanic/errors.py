"""异常类型、中文人话与退出码的映射。

设计铁律：任何异常都必须在 CLI 顶层被翻译成「中文说明 + 下一步建议」，
绝不允许把 Python 堆栈丢给用户（零基础用户看到堆栈会直接放弃）。
"""

from __future__ import annotations

EXIT_OK = 0
EXIT_DEGRADED = 2
EXIT_NO_DATA = 3
EXIT_ENV = 4
EXIT_USAGE = 5
EXIT_INTERRUPTED = 130


class TechpanicError(Exception):
    """所有自定义异常的基类。子类用 exit_code 声明退出码。"""

    exit_code: int = 1

    def hint(self) -> str:
        """给零基础用户看的「下一步该做什么」。"""
        return ""


class DependencyError(TechpanicError):
    """依赖缺失或版本不符。"""

    exit_code = EXIT_ENV

    def hint(self) -> str:
        return (
            "请使用项目自带的一键启动脚本：Windows 双击 start.bat，"
            "macOS 右键 start.command，Linux 运行 ./start.sh。"
            "它会自动创建虚拟环境并安装正确版本的依赖。"
        )


class NetworkError(TechpanicError):
    """网络不可达、超时或被代理拦截。"""

    exit_code = EXIT_NO_DATA

    def hint(self) -> str:
        return (
            "1) 检查网络连接是否正常；\n"
            "   2) 如使用代理，可在 config.toml 的 [network] 段填写 proxy；\n"
            "   3) 若只是暂时不通，稍后重试：python -m techpanic"
        )


class NoDataError(TechpanicError):
    """既没有可用缓存，也没有抓到任何数据。"""

    exit_code = EXIT_NO_DATA

    def hint(self) -> str:
        return (
            "1) 确认网络可用后重试：python -m techpanic；\n"
            "   2) 中国大陆网络建议在 config.toml 中设置 proxy；\n"
            "   3) 查看日志了解每个数据源的具体失败原因：data/logs/"
        )


class DataQualityError(TechpanicError):
    """数据校验不通过（行数不足 / 全 0 / 日期倒序 / 列结构异常）。

    不会被静默吞掉：宁可降级并明确告知，也不用脏数据算出一个
    看起来正常、实际错误的读数。
    """

    exit_code = EXIT_DEGRADED

    def hint(self) -> str:
        return (
            "该数据集已拒绝使用，读数会自动降级到可用口径。\n"
            "   如需强制重新抓取：python -m techpanic --refresh"
        )


class ConfigError(TechpanicError):
    """配置文件或命令行参数错误。"""

    exit_code = EXIT_USAGE

    def hint(self) -> str:
        return "运行 python -m techpanic --help 查看全部可用参数。"


EXIT_CODE_BY_TYPE: dict[type, int] = {
    DependencyError: EXIT_ENV,
    NetworkError: EXIT_NO_DATA,
    NoDataError: EXIT_NO_DATA,
    DataQualityError: EXIT_DEGRADED,
    ConfigError: EXIT_USAGE,
}


def exit_code_for(exc: BaseException) -> int:
    """把任意异常映射到约定的退出码。"""
    if isinstance(exc, KeyboardInterrupt):
        return EXIT_INTERRUPTED
    for cls in type(exc).__mro__:
        if cls in EXIT_CODE_BY_TYPE:
            return EXIT_CODE_BY_TYPE[cls]
    return 1
