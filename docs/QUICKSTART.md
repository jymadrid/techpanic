# 快速上手

本文档假设你**完全没有编程经验**。遇到问题请先看文末的「卡住了怎么办」。

---

## 第一步：确认你装了 Python

打开终端：

- **Windows**：按 `Win + R`，输入 `cmd`，回车。
- **macOS**：打开「终端」（在「启动台 → 其他」里）。
- **Linux**：打开你的终端。

输入：

```
python --version
```

看到 `Python 3.10.x` 或更高（3.11 / 3.12 / 3.13 都行）就对了。

看到「不是内部或外部命令」「command not found」，说明**还没装 Python**：

- Windows：去 <https://www.python.org/downloads/> 下载安装包。**安装时务必勾选最下面那个 "Add Python to PATH"**，否则后面用不了。
- macOS：`brew install python@3.12`，或去官网下载。
- Ubuntu/Debian：`sudo apt install python3 python3-venv python3-pip`

> 装完把终端**关掉重开**，再试一次 `python --version`。

---

## 第二步：拿到本仓库

**方法 A：Git（推荐）**

```
git clone https://github.com/OWNER/techpanic.git
cd techpanic
```

**方法 B：不用 Git**

在 GitHub 页面点绿色的 **Code → Download ZIP**，解压到任意目录，然后在该文件夹里打开终端。

---

## 第三步：一键运行

| 系统 | 操作 |
|---|---|
| Windows | 双击 `start.bat` |
| macOS | 双击 `start.command`（若提示不安全：右键 → 打开 → 打开） |
| Linux | `bash start.sh` |

脚本会依次：

1. 检查 Python 版本；
2. 创建独立环境 `.venv`（不污染你的系统 Python）；
3. 自动安装依赖（首次约 2–5 分钟）；
4. 抓取数据、计算、打印读数，并把结果写进 `data/output/`。

**首次运行可能要等 5 分钟以上**，其中大部分时间花在下载 QVIX 期权数据上（上游限速约 8 KB/s，正常现象）。之后运行会快很多。

---

## 第四步：看结果

屏幕上会出现这样的卡片：

```
【科创50】最新交易日 2026-10-08

  A | 完整口径（三因子，含期权）  数据日 2026-09-30
      PI =  53.7  【常态】  历史分位 49%

  B | 即时口径（两因子，仅价格）  数据日 2026-10-08（含最新交易日）
      PI =  61.5  【警戒】  历史分位 75%
      当日 -4.82%  近5日 -12.31%  方向 向下
```

**怎么读这两行？** 记住三句话：

1. **A 是含期权的完整读数，但它的数据日通常比今天早一天。** QVIX 期权数据是盘后发布的，滞后是常态，不是错误。
2. **B 只用价格，所有当日收盘的数据都在里面。** 想看「今天到底怎么了」，看 B。
3. **不要用 A 减 B。** 它们口径不同，差值无法解释。要比就比同一个口径的今天和昨天。

然后看 `方向`：**PI 高 + 方向向下才是恐慌；PI 高 + 方向向上是狂热。**

结果文件在 `data/output/`：

| 文件 | 怎么看 |
|---|---|
| `summary.md` | 用记事本或任何 Markdown 阅读器打开 |
| `panic_index_*.csv` | **双击用 Excel 打开**（已处理好中文编码） |
| `latest.json` | 给程序看的，人不用管 |

---

## 卡住了怎么办

### 「双击 start.bat 一闪而过」

说明脚本自己报错退出了。改为手动运行以便看到信息：在文件夹地址栏输入 `cmd` 回车，然后输入 `start.bat` 回车。

### 「pip 安装失败 / 超时」

用国内镜像重装：

```
.venv\Scripts\python.exe -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

macOS/Linux 把 `.venv\Scripts\python.exe` 换成 `.venv/bin/python`。

### 一直卡在 QVIX 下载

上游确实慢。两个选择：

- **等**：它最多等 300 秒，超时会自动降级用缓存。
- **跳过网络**：python -m techpanic --offline（前提是之前成功跑过至少一次）。
- **不必等第二次**：默认 6 小时内已成功抓取过就直接用缓存跳过下载；要强制重抓加 --refresh。

### 「没有拿到任何数据」（退出码 3）

1. 确认能上网：浏览器打开 <https://www.baidu.com>；
2. 运行 `python -m techpanic --check`，它会告诉你是环境问题还是数据源问题；
3. 公司网络可能需要代理，见 [CONFIGURATION.md](CONFIGURATION.md) 的 `[network] proxy`。

### 想看更详细的报错

```
python -m techpanic --debug
```

会打印完整的 Python 堆栈。报 Issue 时请附上这段输出。

### 「A 和 B 差好多，是不是坏了」

不是。A 含期权且数据日更早，B 只用价格且数据日最新。

如果 **B 的数值**本身看起来离谱（比如接近 0 或 100），才可能是数据问题，请附上 `--debug` 输出开 Issue。

---

## 不用命令行：Google Colab

打开 [notebooks/quickstart_colab.ipynb](../notebooks/quickstart_colab.ipynb)，上传到 <https://colab.research.google.com/>，点「全部运行」。它会在云端装好一切并打印同样的读数。

---

## 下一步

- 想改配置 → [CONFIGURATION.md](CONFIGURATION.md)
- 想懂公式 → [METHODOLOGY.md](METHODOLOGY.md)
- 想知道数据和风险 → [DATA_SOURCES.md](DATA_SOURCES.md)
- 想确认结果可信 → [VALIDATION.md](VALIDATION.md)
