@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"
if errorlevel 1 exit /b 4

echo.
echo   ============================================================
echo     科技板块恐慌指数 PI  ^|  一键运行
echo   ============================================================
echo.

rem Reuse the project environment before looking for system Python.
set "VPY=%~dp0.venv\Scripts\python.exe"
if exist "%VPY%" goto environment_ready
set "PY="
where py >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if defined PY goto create_environment
where python >nul 2>nul
if not errorlevel 1 set "PY=python"
if not defined PY goto no_python

:create_environment
echo   [1/3] 首次运行，正在创建独立环境...
%PY% -m venv .venv
if errorlevel 1 goto environment_failed
goto check_version

:environment_ready
echo   [1/3] 环境已就绪

:check_version
"%VPY%" -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)"
if errorlevel 1 goto wrong_version

rem Bootstrap pip offline when an existing environment has no pip.
"%VPY%" -m pip --version >nul 2>nul
if not errorlevel 1 goto check_installation
echo   [2/3] 当前环境缺少 pip，正在使用 Python 内置 ensurepip 修复...
"%VPY%" -m ensurepip --upgrade
if errorlevel 1 goto pip_failed
"%VPY%" -m pip --version >nul 2>nul
if errorlevel 1 goto pip_failed

:check_installation
if not exist ".venv\.techpanic-installed" goto install_dependencies
"%VPY%" -m techpanic --version >nul 2>nul
if errorlevel 1 goto install_dependencies
echo   [2/3] 依赖已就绪
goto run_project

:install_dependencies
echo   [2/3] 正在安装依赖，耗时取决于网络...
"%VPY%" -m pip install -r requirements.txt --disable-pip-version-check
if errorlevel 1 goto install_failed
"%VPY%" -m techpanic --version >nul 2>nul
if errorlevel 1 goto install_failed
echo ok> ".venv\.techpanic-installed"

:run_project
echo   [3/3] 开始运行...
echo.
"%VPY%" -m techpanic %*
set "CODE=%ERRORLEVEL%"
echo.
if "%CODE%"=="0" echo   完成。
if "%CODE%"=="2" echo   完成，部分数据降级，请查看上方结果。
if "%CODE%"=="3" echo   没有拿到数据，请检查网络或本地缓存。
if "%CODE%"=="4" echo   运行环境有问题，请查看上方提示。
if "%CODE%"=="5" echo   命令参数有误。
if "%CODE%"=="1" echo   发生未预期错误，可追加 --debug 查看堆栈。
if "%CODE%"=="130" echo   已手动中断。
goto finish

:no_python
echo   [错误] 没找到 Python，请安装 Python 3.12 或更高版本。
echo   https://www.python.org/downloads/
goto environment_error

:environment_failed
echo   [错误] 创建虚拟环境失败，请检查 Python 安装。
goto environment_error

:wrong_version
echo   [错误] 当前环境需要 Python 3.12 或更高版本。
"%VPY%" -V
echo   请使用受支持的 Python 修复虚拟环境。不要删除 data 目录。
goto environment_error

:pip_failed
echo   [错误] 内置 ensurepip 无法修复 pip。
echo   请修复或安装完整的 Python 3.12 以上版本，再重试。
echo   此操作未删除本地缓存或数据。
goto environment_error

:install_failed
echo.
echo   [错误] 依赖安装失败。请检查上方 pip 错误。
echo   网络不通时可手动使用镜像：
echo     "%VPY%" -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
goto environment_error

:environment_error
set "CODE=4"

:finish
echo.
pause
exit /b %CODE%
