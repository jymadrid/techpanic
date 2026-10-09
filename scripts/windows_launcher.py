"""Windows 一键启动编排；仅使用标准库，安装包之前也可以运行。"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def _run(python: Path, *args: str, quiet: bool = False) -> int:
    return subprocess.run(
        [str(python), *args], cwd=ROOT,
        stdout=subprocess.DEVNULL if quiet else None,
        stderr=subprocess.DEVNULL if quiet else None,
        check=False,
    ).returncode


def _prepare() -> Path | None:
    if sys.version_info < (3, 12):
        print("  [错误] 需要 Python 3.12 或更高版本。", flush=True)
        return None
    python = ROOT / ".venv" / "Scripts" / "python.exe"
    if not python.is_file():
        print("  [1/3] 首次运行，正在创建独立环境...", flush=True)
        if _run(Path(sys.executable), "-m", "venv", str(ROOT / ".venv")):
            print("  [错误] 创建虚拟环境失败，请检查 Python 安装。", flush=True)
            return None
    else:
        print("  [1/3] 环境已就绪", flush=True)
    if _run(python, "-c", "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)", quiet=True):
        print("  [错误] 虚拟环境需要 Python 3.12 或更高版本。", flush=True)
        return None
    if _run(python, "-m", "pip", "--version", quiet=True):
        print("  [2/3] 当前环境缺少 pip，正在使用内置 ensurepip 修复...", flush=True)
        if _run(python, "-m", "ensurepip", "--upgrade") or _run(python, "-m", "pip", "--version", quiet=True):
            print("  [错误] 内置 ensurepip 无法修复 pip，请安装完整 Python 后重试。", flush=True)
            return None
    marker = ROOT / ".venv" / ".techpanic-installed"
    if not marker.is_file() or _run(python, "-m", "techpanic", "--version", quiet=True):
        print("  [2/3] 正在安装依赖，耗时取决于网络...", flush=True)
        if _run(python, "-m", "pip", "install", "-r", "requirements.txt", "--disable-pip-version-check") or _run(python, "-m", "techpanic", "--version", quiet=True):
            print("  [错误] 依赖安装失败，请检查上方 pip 错误。", flush=True)
            print("  网络不通时可手动使用镜像：", flush=True)
            print(f'    "{python}" -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple', flush=True)
            return None
        marker.write_text("ok\n", encoding="ascii")
    else:
        print("  [2/3] 依赖已就绪", flush=True)
    return python


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    os.chdir(ROOT)
    print("\n  ============================================================", flush=True)
    print("    科技板块恐慌指数 PI  |  一键运行", flush=True)
    print("  ============================================================\n", flush=True)
    try:
        python = _prepare()
        if python is None:
            return 4
        print("  [3/3] 开始运行...\n", flush=True)
        code = _run(python, "-m", "techpanic", *(sys.argv[1:] if argv is None else argv))
    except KeyboardInterrupt:
        code = 130
    except OSError as exc:
        print(f"  [错误] 启动环境不可用：{exc}", file=sys.stderr, flush=True)
        code = 4
    messages = {
        0: "完成。",
        2: "完成，部分数据降级，请查看上方结果。",
        3: "没有拿到数据，请检查网络或本地缓存。",
        4: "运行环境有问题，请查看上方提示。",
        5: "命令参数有误。",
        1: "发生未预期错误，可追加 --debug 查看堆栈。",
        130: "已手动中断。",
    }
    print("\n  " + messages.get(code, f"程序退出，退出码 {code}。"), flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
