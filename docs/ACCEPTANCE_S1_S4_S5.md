# S1 / S4 / S5 修复验收

## 追加：Windows 尾部提示隔离

批处理现为纯 ASCII 入口，所有中文文案和退出码提示交给标准库 Python 启动器。用户报告的截断命令在本机未稳定复现，所以不声称具体触发原因已被确认。此改动移除了 cmd 解析中文条件语句的路径。

- 测试 0、1、2、3、4、5、130 和未知退出码，保留原退出码与相应中文提示。
- 批处理纯 ASCII 检查、缺 pip 和安装失败分支测试通过。
- 真实降级计算连续两次退出 2，正确输出尾部提示，stderr 没有命令解析错误。
- 当前完整 Windows 套件 118 passed；ruff 检查范围新增启动辅助脚本。

## 追加：Windows 缺 pip 安装分支修复

前次验收只覆盖当前环境的复用分支，未正确处理当前环境没有 pip 的情况。该遗漏已修正：启动脚本先调用内置 ensurepip 补齐 pip，再执行依赖安装。

- 原有环境缺 pip → 自动 ensurepip → 安装运行依赖 → 模块入口：实测成功。
- 4 个真实 cmd 回归测试：缺 pip、旧安装标记、依赖安装失败、ensurepip 失败全部通过。临时虚拟环境与空依赖清单使测试不依赖网络。
- 中文、空格、& 与 ! 路径以及不同工作目录均覆盖。
- 完整 Windows 测试套件为 109 passed；ruff 和 pip check 通过。
- 错误分支不再出现“not recognized”或“不是内部或外部命令”。
- 没有删除现有环境、缓存或数据。

以下保留前次验收记录，当时的测试数量为 105。

## 范围

本轮验收针对 S1 安装路径、S4 配置优先级和 S5 macOS 启动文件 Git 可执行位。不代表其他评审条目已全部关闭。不推送 GitHub，不生成 Release。

## 修复

- S1：运行依赖清单增加可编辑安装项目本身的条目。三个启动脚本复查包入口，旧安装标记不再掩盖包未安装。
- S4：显式数据目录高于环境变量；未传离线/刷新开关时回退环境变量；显式布尔值以调用方为准。
- S5：macOS 启动脚本在 Git 中记录为 100755。
- 新增三平台文档安装 CI，仅按运行依赖清单安装，不额外补做项目安装。

## 已实测

| 项目 | 结果 |
|---|---|
| 全部回归测试 | 105 passed |
| ruff 检查源码和测试 | All checks passed |
| 全新独立环境按文档安装 | 成功，无 system-site-packages |
| 模块入口与命令行入口 | techpanic 1.0.0，均退出 0 |
| 干净环境 pip check | No broken requirements found |
| 干净环境切换工作目录后离线计算 | 成功，退出 2（数据降级） |
| CLI 数据目录覆盖环境变量 | 通过，未创建环境变量指定的数据目录 |
| 环境变量离线与显式布尔覆盖 | 回归与子进程验收通过 |
| 真实公开数据抓取及计算 | 成功，退出 2 |
| 默认两标的产物 | 正好 7 个文件 |
| 离线 JSON 与 UTF-8 严格解码 | 通过 |
| GBK 环境下帮助和报错 | 可严格按 UTF-8 解码 |
| 非法参数 / 非法日期 | 均退出 5 |
| 无缓存离线 | 退出 3 |
| 重定向人类可读输出 | 无编码崩溃 |
| 历史日期运行 | 日期专属汇总 JSON 正常 |
| Windows 中文路径、不同工作目录启动 | 复用环境分支实测通过，退出 0 |
| macOS Git 可执行位 | 100755，回归通过 |
| git diff --check | 通过 |
| 跟踪 CSV/XLSX/Parquet 文件 | 0 个 |
| WorkBuddy SHA256 基线 | 6919 文件，缺失/大小不符/内容变化/新增均为 0 |

## 复现命令

在仓库根目录执行：

```powershell
python -m venv .review/acceptance/clean-env
.review/acceptance/clean-env/Scripts/python.exe -m pip install -r requirements.txt
.review/acceptance/clean-env/Scripts/python.exe -m pip check
.review/acceptance/clean-env/Scripts/python.exe -m techpanic --version
.review/acceptance/clean-env/Scripts/techpanic.exe --version
python -m ruff check src tests
python -m pytest tests -o addopts='' -q
git ls-files -s start.command
```

运行日志、临时数据、独立环境和包缓存均位于项目内忽略目录，不入 Git。

## 验证边界

本机是 Windows，未实际在 macOS Finder 双击或 Linux 桌面运行。S5 的本地证据是 Git 可执行位与回归测试。新增三平台 GitHub 安装任务尚未在线运行，因为未配置远端或推送。不能将其描述为真实 macOS 或云端 CI 已通过。
