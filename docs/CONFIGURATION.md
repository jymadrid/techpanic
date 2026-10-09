# 配置说明

**你完全可以不改任何配置就用起来。** 所有项都有合理默认值。

本文档写给「想改点什么」的人。

---

## 1. 三层优先级

配置按下面的顺序叠加，**后者覆盖前者**：

```
内置默认值  →  data/config.toml  →  环境变量 TECHPANIC_*  →  命令行参数
   (最低)                                                  (最高)
```

### 配置文件放哪

默认读取 `<数据目录>/config.toml`，数据目录默认是 `./data`。

```bash
cp config.toml.example data/config.toml
```

也可以用 `--config` 指定任意路径，或用环境变量 `TECHPANIC_CONFIG`。

> 如果文件不存在，程序**不会报错**，直接用默认值继续跑。

---

## 2. 命令行参数

```
python -m techpanic [选项]
```

| 参数 | 作用 |
|---|---|
| `--data-dir PATH` | 数据目录（缓存 / 输出），默认 `./data` |
| `--config PATH` | 配置文件路径，默认 `<数据目录>/config.toml` |
| `--offline` | 只用本地缓存，**零 HTTP 请求** |
| `--refresh` | 忽略缓存有效期，强制重新抓取 |
| `--date YYYY-MM-DD` | 只使用该日期及之前的数据（复核 / 复现） |
| `--json` | 把 JSON 结果打印到标准输出 |
| `--with-sse50` | 额外计算上证50（非科技，仅方法验证） |
| `--no-color` | 关闭彩色（重定向到文件时自动关闭） |
| `--quiet` | 只输出最终结果 |
| `--verbose` | 输出更多诊断信息 |
| `--debug` | 失败时打印完整 Python 堆栈（报 Issue 用） |
| `--check` | 只做环境与数据源自检，不计算 |
| `--version` | 打印版本 |

---

## 3. `[network]` 网络

| 键 | 默认 | 含义 |
|---|---|---|
| `source_index` | `"sina"` | 指数日线数据源 |
| `timeout_connect` | `5.0` | 连接超时（秒） |
| `timeout_read` | `15.0` | 一般请求读超时（秒） |
| `timeout_qvix` | `150.0` | QVIX 单次读超时（秒） |
| `budget_qvix` | `300.0` | QVIX **总**预算（秒，含重试） |
| `cache_ttl_hours` | `6.0` | 缓存有效期；期内不重复下载 |
| `retries` | `3` | 一般请求重试次数 |
| `retries_qvix` | `2` | QVIX 重试次数 |
| `backoff` | `[0.5, 1.5, 4.0]` | 重试前的等待秒数 |
| `jitter` | `0.30` | 退避抖动比例（0.3 = ±30%） |
| `proxy` | 空 | HTTP 代理 |

### 关于 `cache_ttl_hours`

这是本项目**最实用的一个配置**。

QVIX 上游要 2~4 分钟才能下完。默认 6 小时内如果已经成功抓过，
就直接用缓存、跳过下载，并在输出里说明。想看最新数据可以：

```bash
python -m techpanic --refresh      # 强制重抓
```

或者把它设为 `0`（每次都抓）：

```toml
[network]
cache_ttl_hours = 0
```

### 关于 `proxy`

公司网络常需要代理：

```toml
[network]
proxy = "http://127.0.0.1:7890"
```

或临时用环境变量：

```powershell
$env:TECHPANIC_PROXY = "http://127.0.0.1:7890"
python -m techpanic
```

---

## 4. `[index]` 指标定义

> ⚠️ **改这一节会让读数不再与历史可比**，也会让所有回归测试失效。
> 改之前请先读 [METHODOLOGY.md](METHODOLOGY.md)。

| 键 | 默认 | 含义 |
|---|---|---|
| `ema_span` | `3` | PI 平滑用的 EMA 跨度 |
| `z_min_periods` | `60` | 分位标准化的最少样本数 |
| `rv_window` | `20` | 长期已实现波动窗口（同时用于半方差） |
| `rv_short` | `5` | 短期已实现波动窗口（同时用于方向判定） |
| `annualization` | `252` | 年化交易日数 |
| `level_anchor` | `"expanding"` | 分级锚点算法（见下） |

### `[index.weights]`

| 键 | 默认 |
|---|---|
| `S` | `0.40` |
| `A` | `0.35` |
| `F` | `0.25` |

三者之和会被校验（必须为 1）。

### `level_anchor` 的三种取值

| 值 | 行为 | 前视偏差 | 什么时候用 |
|---|---|---|---|
| `"expanding"` | 第 t 天只用 t 及之前的数据 | **无** | **默认**，实时使用 |
| `"full_sample"` | 用全部数据算分位 | **有** | 复现历史报告、离线研究 |
| `"frozen"` | 用下面写死的阈值 | 无 | 对外引用、跨版本比较 |

`[index.frozen_anchors]` 只在 `level_anchor = "frozen"` 时生效：

```toml
[index]
level_anchor = "frozen"

[index.frozen_anchors]
p25 = 30.0
p50 = 42.0
p75 = 58.0
p90 = 72.0
```

**为什么推荐对外引用时用 frozen？**
因为 expanding 锚点会随新数据微调，导致同一份报告里的历史分级在几个月后
可能有极细微变化。frozen 完全可复现。

---

## 5. `[output]` 输出

| 键 | 默认 | 含义 |
|---|---|---|
| `formats` | `["csv", "json"]` | 输出哪些格式 |
| `encoding` | `"utf-8-sig"` | 文本输出编码（带 BOM，Excel 友好） |
| `json_schema` | `"v1"` | JSON schema 版本 |

> ⚠️ **不要把 `encoding` 改成 `utf-8`。** 带 BOM 是为了让
> Windows 用户双击 CSV 用 Excel 打开时不出现中文乱码，这是有意的，
> 不是 bug。测试 `test_csv_is_utf8_bom_and_readable_by_pandas` 会守住这一点。

---

## 6. `[ui]` 界面

| 键 | 默认 | 含义 |
|---|---|---|
| `lang` | `"zh-CN"` | 界面语言（当前仅中文） |
| `color` | `"auto"` | `auto` / `always` / `never` |
| `progress` | `true` | 是否显示下载进度条 |

`auto` 的含义：输出到终端时用彩色，重定向到文件时自动关闭。

---

## 7. `[targets.*]` 跟踪标的

默认跟踪两个科技标的：

| key | 名称 | 指数代码 | QVIX |
|---|---|---|---|
| `tech_kcb` | 科创50 | `sh000688` | `kcb` |
| `tech_cyb` | 创业板指 | `sz399006` | `cyb` |

另有 `sse50`（上证50，`sh000016` / `50etf`），
属于 `role = "validation"`，**默认不计算**，需要 `--with-sse50` 显式开启。

### 换成你自己的标的

> ⚠️ 一旦配置文件里出现 `[targets]`，就会**整体替换**默认列表，不是合并。

```toml
[targets.tech_kcb]
name   = "科创50"
symbol = "sh000688"
qvix   = "kcb"
role   = "tech"

[targets.tech_cyb]
name   = "创业板指"
symbol = "sz399006"
qvix   = "cyb"
role   = "tech"
```

字段说明：

| 字段 | 必填 | 说明 |
|---|---|---|
| `name` | ✅ | 显示名称 |
| `symbol` | ✅ | 新浪格式：`sh` / `sz` + 6 位数字 |
| `qvix` | ✅ | QVIX 品种 key |
| `role` | — | `"tech"`（默认）或 `"validation"` |

### 可用的 QVIX key

| key | 品种 |
|---|---|
| `kcb` | 科创50 期权 |
| `cyb` | 创业板期权 |
| `50etf` | 上证50ETF 期权 |
| `300etf` | 沪深300ETF 期权 |
| `500etf` | 中证500ETF 期权 |
| `100etf` | 中证100ETF 期权 |
| `1000index` | 中证1000 指数期权 |
| `300index` | 沪深300 指数期权 |
| `50index` | 上证50 指数期权 |

> 没有对应 QVIX 的指数只能出**即时口径**，程序会明确标注。

---

## 8. 环境变量

所有变量前缀为 `TECHPANIC_`。把下划线换成配置键的层级即可。

| 环境变量 | 对应配置 |
|---|---|
| `TECHPANIC_DATA_DIR` | 数据目录 |
| `TECHPANIC_CONFIG` | 配置文件路径 |
| `TECHPANIC_OFFLINE` | `1` 时等同 `--offline` |
| `TECHPANIC_PROXY` | `[network] proxy` |
| `TECHPANIC_SOURCE_INDEX` | `[network] source_index` |
| `TECHPANIC_TIMEOUT_QVIX` | `[network] timeout_qvix` |

```powershell
# Windows 临时设置
$env:TECHPANIC_OFFLINE = "1"
python -m techpanic

# macOS / Linux
TECHPANIC_OFFLINE=1 python -m techpanic
```

---

## 9. 校验与报错

配置有问题时程序不会崩，而是给出中文说明并返回退出码 5。例如：

- 权重之和不为 1 → 报错并指出当前之和；
- `[network]` 不是表 → 报错；
- `[targets.x]` 缺 `symbol` → 报错并列出缺哪些字段；
- 环境变量该是数字却不是 → 报错并给出原值。

---

## 10. 完整示例

```toml
# data/config.toml

[network]
proxy = "http://127.0.0.1:7890"
cache_ttl_hours = 2
timeout_qvix = 180

[index]
level_anchor = "frozen"

[index.frozen_anchors]
p25 = 30.0
p50 = 42.0
p75 = 58.0
p90 = 72.0

[output]
formats = ["csv", "json"]

[ui]
color = "never"
```

完整可复制的模板见仓库根目录的 [config.toml.example](../config.toml.example)。
