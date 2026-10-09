# 贡献指南

感谢你有兴趣改进 techpanic。本项目最需要帮助的地方在三类：

1. **数据源**：现有 QVIX 上游（`1.optbbs.com`）是一个限速约 8 KB/s 的第三方小站，是最大的脆弱点。任何**不需要账号 / Key** 的替代或补充来源都极有价值。
2. **接口变更**：上游改版导致抓取失败。请附上原始报错与抓取时间。
3. **方法学质疑**：附推导、反例或文献依据。我们欢迎被证伪。

## 开发环境

```bash
git clone https://github.com/OWNER/techpanic.git
cd techpanic
python -m venv .venv
# Windows
.venv`Scripts`activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements-dev.txt
pip install -e . --no-deps
```

依赖版本在 `requirements.txt` 中**精确锁定**。请勿随意放宽版本，读数必须可复现。

## 提交前必须通过

```bash
pytest
ruff check src tests
```

CI 会在这两项之外额外跑一次真实的端到端运行（见 `.github/workflows/ci.yml`）。

## 硬性规则

### 1. 绝不修改 `tests/` 里的期望值来让测试变绿

如果 `tests/test_regression_reference.md` 中的基准失败，只有两种可能：你改坏了算法，或者你**有意**改了算法定义。后者需要：

- 在 PR 描述中说明动机；
- 附上改动前后两份 `latest.json` 的 diff；
- 同步更新 `docs/VALIDATION.md`。

直接改数字的 PR 会被关闭。

### 2. 任何降级都必须在输出中显式标注

不允许「静默回退到缓存」「静默跳过某个标的」「静默把缺失当 0」。新增任何回退路径时，必须同时：

- 在 `TargetResult.notes` 或 `RunResult.warnings` 里留下人话说明；
- 让退出码反映真实状态（成功 / 部分降级 / 无数据）。

### 3. 不要引入前视偏差

分级锚点与 z-score 必须严格因果（只用当日及之前的数据）。新增任何使用「全样本统计量」的逻辑前，请先想清楚它在实时场景下是否可得。

### 4. 不要引入新依赖，除非有充分理由

运行时依赖只有 5 个，且都精确锁定。新增依赖需要说明：

- 为什么标准库或现有依赖做不到；
- 是否有平台相关的原生 wheel（本项目已因此显式声明 `curl_cffi` 与 `mini-racer`）。

### 5. 中文是主语言

用户可见的文案、注释、文档以**简体中文**为主。代码标识符用英文。英文文档（`README.en.md`）保持同步。

## 代码风格

- 行宽 100，ruff 配置见 `pyproject.toml`。
- 类型注解尽量完整；公开函数要有中文 docstring。
- 错误处理走 `techpanic.errors` 中的异常体系，不要裸 `raise Exception`。
- 写盘一律用 `techpanic.store` 的原子写，不要直接 `open(..., "w")`。

## 新增数据源的正确做法

1. 在 `src/techpanic/fetch/` 下新建模块，实现 `fetch_xxx(...) -> 结果对象`。
2. 失败时抛 `NetworkError` 或 `DataQualityError`，由上层决定降级策略。
3. **必须**加结构断言：拒绝「看起来不像该指标」的数据，而不是照单全收。
4. 如果有历史数据可对比，**必须**加交叉校验（参考 `fetch/qvix.py::cross_check_cache`）。
5. 在 `docs/DATA_SOURCES.md` 中登记：URL、字段、实测耗时、已知风险。
6. 加一个用合成数据构造的解析测试（不要依赖真实网络）。

## 报告 Bug

请附：

- 操作系统与 Python 版本（`python -V`）；
- `python -m techpanic --debug` 的完整输出；
- 你运行的确切命令；
- 期望看到什么、实际看到什么。

## 行为准则

参与本项目即表示同意遵守 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。
