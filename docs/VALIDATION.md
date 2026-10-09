# 验证

本文档回答一个问题：**凭什么相信这些数字？**

分四部分：与参考实现的数值对照、测试覆盖、已知差异的归因、以及你可以自己做的复核。

---

## 1. 与参考实现的数值对照

### 对照方法

用**同一份输入数据**（2026-10-08 采集的指数日线与 QVIX 缓存）分别跑：

- 参考实现 `build_panic_index.py`（原研究代码）；
- 本项目 `techpanic.index.compute`。

逐列对比 739 个交易日的输出。

### 结果

| 列 | 最大绝对差 | 结论 |
|---|---|---|
| `S`（突发性） | **0.0** | 完全一致 |
| `A`（不对称性） | **0.0** | 完全一致 |
| `F`（前瞻恐惧） | **0.0** | 完全一致 |
| `PI_price`（即时口径） | **0.0** | 完全一致 |
| 分级标签（即时口径） | — | 100% 一致（科创50、上证50）；99.95%（创业板指） |
| `PI_full`（完整口径） | 1.0476 | **仅 14 / 739 天不同**，见下节 |

### 2026-10-08 两个标的的核对结果

| 字段 | 科创50 | 创业板指 |
|---|---:|---:|
| B 即时口径 PI_price | 61.5013887 | 70.9551740 |
| B 分级 | 警戒 | 极度恐慌 |
| B 历史分位 | 74.7% | 91.0% |
| S（突发性） | 75.3555968 | 76.152 |
| A（不对称性） | 71.0265925 | 79.053 |
| F（前瞻恐惧） | NaN（QVIX 未发布） | NaN |
| 当日涨跌 | −4.8163084% | −3.145% |
| 近5日涨跌 | −12.3147786% | −10.148% |
| A 完整口径 PI_full | 53.6526（数据日 09-30） | 65.8021（数据日 09-30） |
| A 分级 | 常态 | 恐慌 |

这些数值被锁定在 [tests/test_regression_reference.md](../tests/test_regression_reference.md)
与 [tests/test_regression.py](../tests/test_regression.py) 中。

---

## 2. 唯一的差异：PI_full 在 QVIX 缺值日的前向携带

### 现象

739 个交易日中有 **14 天** 的 `PI_full` 不同，最大差 1.0476，
且差异按约 **1/3 逐日衰减**（衰减比 = `1 − 1/span`，span=3）。

「倍率恰好等于 EMA 的衰减系数」是一个强信号：**差异来自 EMA 状态处理**，
不是来自公式或数据。

### 根因

参考实现直接输出 `ewm(span=3)` 的结果。pandas 的 `ewm`
在遇到 NaN 时会**跳过它并保持内部状态**，于是下一个有效观测到来时，
输出值里仍然混着缺值日之前的状态。

后果：在 QVIX 缺失的交易日，参考实现的「完整口径」会返回一个数——
但那个数来自上一个有 QVIX 的日子。用户会看到：

- 显示「PI 完整口径 = 53.7」
- 但那一行对应的日期是 QVIX 缺失日

**数据日与读数不匹配。** 这是一个真实的、会影响使用的缺陷。

### 本项目的处理

```python
d["PI_full"] = d["PI_full_raw"].ewm(span=cfg.ema_span, adjust=False).mean()
d.loc[d["F"].isna(), "PI_full"] = np.nan   # 缺值日置回 NaN
```

对应的回归测试：
`tests/test_index.py::test_missing_qvix_does_not_carry_forward_full_value`

### 为什么确认这是「修正」而非「退步」

判据是**内部一致性**：

- 本项目的完整口径在**内部**可以被严格复算：`PI_full_raw` 恰好等于
  `0.40·S + 0.35·A + 0.25·F`（误差 < 1e-12，由
  `test_pi_price_formula_exact` 锁定）。
- 参考实现公开的列**做不到**这一点：用它自己的 `S`、`A`、
  `F` 与权重算出来的值，与它发布的 `PI完整口径` 在缺值日附近对不上。

⚠️ **注意口径，别说过了头。** 上面说的是**内部**的 `PI_full_raw`。
对外发布的 CSV 里那一列叫 `PI完整口径`，它是 `PI_full_raw` 经过
`span=3` 的 EMA 平滑后的值，而且 CSV 会四舍五入到 4 位小数。
所以**不能**拿 CSV 里的 `突发性/不对称性/前瞻恐惧` 三列直接乘权重去核对
CSV 里的 `PI完整口径` —— 那样对不上是正常的，不是缺陷。

（这条澄清来自一次外部审查：审查者按字面理解去复算发布的 CSV，
736 行只有 1 行相等、最大差 26.66，于是把文档的表述判为"不成立"。
表述本身确实有歧义，此处已改准。想严格复算请用代码里的 `PI_full_raw`。）

### 对结论的实际影响

**对即时口径（B）零影响**，因为 B 不使用 QVIX。
**对完整口径（A）**：只有 QVIX 缺值的那几天不同，且当 QVIX 恢复发布后会立即收敛。

由于 A 的用途是「研究/引用」，而这 14 天恰好是「QVIX 没有数据」的日子，
本项目改为显式缺值反而更诚实：**没有期权数据时，就不该声称有完整口径。**

---

## 3. 测试覆盖（109 个测试）

运行：

```bash
pip install -r requirements-dev.txt
pytest
```

### 3.1 算法正确性

| 测试 | 验证什么 |
|---|---|
| `test_pi_price_formula_exact` | `PI = 0.40S+0.35A+0.25F` 与 `PI_price = (0.40S+0.35A)/0.75` 的口径恒等式（1e-12） |
| `test_smoothing_is_ema_span3` | 平滑确实且仅是 span=3 的 EMA |
| `test_pi_full_is_nan_where_f_is_nan` | 缺值日完整口径必须为 NaN |
| `test_scores_are_bounded` | S/A/F 落在 0–100 |
| `test_weights_change_output` | 改权重确实改变读数（防止权重被硬编码忽略） |
| `test_price_only_is_normalized_by_remaining_weights` | 归一化分母正确 |

### 3.2 因果性（防前视偏差）

| 测试 | 验证什么 |
|---|---|
| `test_expanding_anchors_are_causal` | 在序列尾部接上极端未来值后，**历史锚点必须逐位不变** |
| `test_causal_ranking_unaffected_by_future_data` | 分位标准化同样不受未来数据影响 |
| `test_full_sample_anchors_do_look_ahead` | **反证**：全样本锚点确实会变（证明前一个测试不是因为恒等而通过） |

> 第三行是关键设计：一个「永远不会失败的因果性测试」等于没有测试。
> 所以我们同时测了「全样本版本**会**被未来数据改变」，用来证明测试本身有分辨力。

### 3.3 数据质量防线

| 测试 | 验证什么 |
|---|---|
| `test_assert_structure_accepts_sparse_wide_table` | 稀疏（23-34% 数值率）的宽表**不应**被误杀 |
| `test_assert_structure_rejects_implausible_median` | 中位数不像波动率 → 拒绝 |
| `test_assert_structure_rejects_too_few_rows` | 样本过少 → 拒绝 |
| `test_parse_wide_rejects_missing_columns` | 上游删列 → 拒绝，而非静默取错列 |
| `test_cross_check_cache_detects_column_shift` | **列号偏移被拦住**（最危险的失效模式） |
| `test_cross_check_skips_when_too_little_overlap` | 重叠不足时静默跳过，不误报 |
| `test_zero_and_negative_close_are_dropped` | 0 与负值被剔除 |

### 3.4 端到端与「坏数据」负例

| 测试 | 验证什么 |
|---|---|
| `test_offline_run_succeeds_and_writes_outputs` | 离线跑通并产出全部产物 |
| `test_csv_is_utf8_bom_and_readable_by_pandas` | CSV 带 BOM（Excel 中文不乱码）且可被 pandas 读回 |
| `test_run_is_deterministic` | **两次运行输出逐字节相同** |
| `test_json_summary_fields` | JSON schema 字段完整 |
| `test_no_cache_no_network_exits_3_with_guidance` | **负例 1**：无数据 → 退出码 3 + 中文下一步，且**无 Traceback** |
| `test_negative_test_zero_qvix_tail_is_treated_as_missing` | **负例 2**：尾部清零 → 当缺失剔除并降级 |
| `test_negative_test_negative_qvix_is_rejected` | **负例 3**：负值 → 拒绝整段，完整口径缺值 |
| `test_negative_test_truncated_index_self_heals` | **负例 4**：指数被截断 → 该标的跳过，另一标的仍正常 |
| `test_reference_data_absent_by_design` | 仓库内确实没有任何数据文件（守卫生效） |

> 为什么负例这么重要：最难发现的故障不是「程序崩溃」，
> 而是「**程序正常退出，但数据是错的**」。负例专门守这条线。

### 3.5 缓存与工程

原子写不残留临时文件、备份轮转保留 3 份、短序列被拒、0 值占比过高被拒、
日期乱序被拒、BOM 被剥离、manifest 往返读写、
`staleness_trading_days` 计数正确。

---

## 4. 你可以自己做的复核

### 4.1 确认算法确定性

```bash
python -m techpanic --offline
cp data/output/panic_index_tech_kcb.csv /tmp/a.csv
python -m techpanic --offline
diff /tmp/a.csv data/output/panic_index_tech_kcb.csv && echo "逐字节一致"
```

注意：这验证的是**给定同一份缓存，输出逐字节相同**（算法确定性，
由测试 `test_run_is_deterministic` 锁定）。

加上 `--refresh` 重新抓取时，如果上游确实改了历史行，输出自然会变。
但请注意：早期文档把「两次抓取有 1%~2% 偏差」解释成**上游重算历史值**，
**那个解释是错的**（真实原因是我们取错了列，见
[DATA_SOURCES.md](DATA_SOURCES.md) 第 3 节第 (4) 条）。
上游并不重算历史值；实测相隔 24 小时的两次下载逐字节相同。
需要严格复现时请留档当时的 `data/cache/`。

### 4.2 手工复核即时口径

从输出的 CSV 里取最后一行，验证：

```
PI即时口径 == (0.40 × 突发性 + 0.35 × 不对称性) / 0.75
```

注意：CSV 里的 `PI即时口径` 是 EMA 平滑后的值，
`突发性` / `不对称性` 是当日的原始值。
要严格验证恒等式，请用 `PI即时口径` 的**原始**版本，
或在代码里调 `compute()` 后比较 `PI_price_raw` 列
（测试 `test_pi_price_formula_exact` 已经这样做了）。

### 4.3 与公开行情交叉验证指数输入

打开任意行情网站，核对 CSV 里最近几个交易日的收盘价。

### 4.4 验证「完整口径滞后」不是 bug

在 QVIX 缺失的日子（例如长假前最后一个交易日之后），
观察输出里的 ⚠ 提示与 `data_date` 字段。
它应该等于 QVIX 缓存里的最后一个日期，而不是指数的最新日期。

---

## 5. 尚未验证的部分

诚实列出本项目**没有**验证的东西：

| 未验证项 | 原因 | 风险 |
|---|---|---|
| PI 对未来收益的预测力 | 超出本项目设计意图 | 无——本项目不主张预测力 |
| 权重的统计最优性 | 权重是研究文档给的约定值 | 可能不是最优；改权重会改变定义 |
| 上游数据的准确性 | 无权威交叉源 | **中高**；请自行验证 |
| 其他市场/资产类别的适用性 | 未测试 | 未知 |
| Windows 7 / Python 3.12 早期小版本 | 未测试 | 低 |
| 长时间运行（数月不清理缓存） | 未测试 | 低——缓存按天覆盖写 |
| 上游长期是否会改动历史行 | 仅做过 24 小时对照（逐字节相同） | 中——引用前请留档当时的缓存 |

如果你发现任何一项出了问题，欢迎开 Issue。
