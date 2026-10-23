# techpanic · 科技板块 PI

公开数据驱动的**相对市场压力**观测工具。沿用历史项目名称，不是官方恐慌指数、心理恐慌概率或投资建议。金融有效性目前为 **证据不足**；工程测试与经济验证分开报告。

## 安装和运行

需要 Python 3.12+，数据接口无需个人账号或API Key。

```powershell
git clone https://github.com/jymadrid/techpanic.git
cd techpanic
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe -m techpanic
```

Windows 双击 start.bat；macOS start.command；Linux bash start.sh。Windows 缺 pip 时用内置 ensurepip 修复。首次联网抓取；仓库不携带数据。

```powershell
python -m techpanic --offline --json
python -m techpanic --refresh
python -m techpanic --date 2026-10-09
python -m techpanic audit --help
```

## 来源与口径

在线查询东方财富、腾讯、新浪全部三源，有效日线末日期较新者优先，同日 em > tx > sina。三源不是真值多数表决；全不可用才缓存。不混源补齐，来源和历史覆盖差异会改变分位。

- A 完整口径：S/A与第三方QVIX代理，期权标的身份未证实，日期可能更早。
- B 价格口径：取得最终收盘数据后才能给当天值。
- 两口径不可相减，分级低位/中低位/中高位/高位/极高位只表示历史位置。方向是收益符号，不证明恐慌或狂热。
- 日历未知时会话滞后为 unknown，不能自称覆盖最新官方交易日。计算停在旧日即使请求成功仍降级。

## 审计深化

权重 .40/.35/.25、EMA3及原截断收益中心化方差公式冻结；修复过滤后回溯收益、缺口函数缺返回和非有限因子边界。包含独立算术oracle、前缀不变和对抗测试。

原始响应默认不留存。仅明确允许时使用：

```powershell
python -m techpanic --archive-evidence
python -m techpanic --knowledge-as-of 2026-10-23T17:00:00+08:00
python -m techpanic --calendar data/audit_inputs/calendar.json
python -m techpanic --reference-dir data/audit_inputs/references
```

证据只存本地，不入Git。--date是当前历史回算；--knowledge-as-of只读使用当时已留存版本，不联网、不退回当前缓存、不伪造采集前可得时间。历史/重放写独立目录。

审计命令：reference同对象参考样本；source-impact来源影响；protocol-template/freeze-protocol/study预注册和连续留出时间块评估。没有官方材料/独立盘口目标时必须报未知或证据不足。本地哈希不是官方签名或外部公证。

详见 [审计协议](docs/AUDIT_PROTOCOL.md)、[方法学](docs/METHODOLOGY.md)、[数据源](docs/DATA_SOURCES.md)。旧报告只记录旧版本事实，不是当前金融有效性证明。

## 状态与许可

0运行成功，2降级，3无数据，4环境问题，5输入错误，1未预期错误，130中断。审计JSON另分 passed/failed/insufficient_evidence/reported。运行成功不等于金融验证通过。

无市场数据入仓、无快照或Release。MIT只覆盖代码，不能自动授权上游数据采集/再分发。所有新增在指定项目目录，WorkBuddy不动。
