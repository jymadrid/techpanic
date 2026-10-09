@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo.
echo   ============================================================
echo     科技板块恐慌指数 PI  ^|  一键运行
echo   ============================================================
echo.

rem ---- 1. 找 Python ----
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY ( where python >nul 2>nul && set "PY=python" )
if not defined PY (
  echo   [错误] 没找到 Python。
  echo.
  echo   请先安装 Python 3.10 或更高版本：https://www.python.org/downloads/
  echo   安装时务必勾选 "Add Python to PATH"。
  echo.
  pause
  exit /b 4
)

rem ---- 2. 建/复用虚拟环境 ----
if not exist ".venv\Scripts\python.exe" (
  echo   [1/3] 首次运行，正在创建独立环境（约 1 分钟）...
  %PY% -m venv .venv
  if errorlevel 1 (
    echo   [错误] 创建虚拟环境失败。请确认 Python 安装完整。
    pause
    exit /b 4
  )
) else (
  echo   [1/3] 环境已就绪
)

set "VPY=.venv\Scripts\python.exe"

rem ---- 3. 装依赖（首次） ----
if not exist ".venv\.techpanic-installed" (
  echo   [2/3] 正在安装依赖（首次约 2-5 分钟）...
  "%VPY%" -m pip install --upgrade pip --quiet
  "%VPY%" -m pip install -r requirements.txt --quiet
  if errorlevel 1 (
    echo.
    echo   [错误] 依赖安装失败。常见原因：
    echo     1) 网络不通 —— 试试国内镜像：
    echo        "%VPY%" -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    echo     2) Python 版本过低 —— 需要 3.10 以上。
    echo.
    pause
    exit /b 4
  )
  echo ok> ".venv\.techpanic-installed"
) else (
  echo   [2/3] 依赖已就绪
)

rem ---- 4. 跑 ----
echo   [3/3] 开始获取公开数据并计算...
echo.
"%VPY%" -m techpanic %*

set "CODE=%ERRORLEVEL%"
echo.
if "%CODE%"=="0"  echo   完成（全部数据均为最新）。
if "%CODE%"=="2"  echo   完成（部分降级：期权数据可能滞后 1 个交易日，属正常现象）。
if "%CODE%"=="3"  echo   没有拿到数据 —— 请检查网络后重试。
if "%CODE%"=="4"  echo   运行环境有问题 —— 见上方提示。
if "%CODE%"=="5"  echo   命令参数有误。
if "%CODE%"=="130" echo   已手动中断。
echo.
echo   结果文件在 data\output\ 目录下。
echo.
pause
exit /b %CODE%
