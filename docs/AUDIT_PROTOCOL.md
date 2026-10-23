# 可审计的相对市場压力测量

## 当前主张与范围

techpanic 的历史项目名称保留，但 PI 仅是约定权重的相对市场压力合成，不是心理恐慌概率、官方指数或交易预测。高分是相对历史极端，不能自动解释为恐慌或狂热。本阶段不新增市场、行情商、因子，不优化权重。

### 三层状态独立

1. 运行状态：请求/计算成功、降级、无数据。
2. 数据审计：来源/证据/日历/参考和身份是已提供、失败、未知或未运行。
3. 金融有效性：未有独立同期压力证据，始终显示 insufficient_evidence。工程测试通过不能修改这个结论。

## 证据留存与双时态

默认不留存原始响应。只有显式传 --archive-evidence，才表示允许在数据目录内保存 evidence/ 的响应实体字节、请求参数/URL、接收时间、SHA-256及归一化版本血缘。不上传，不进入 Git，不创建 Release。哈希不是上游签名，也不能证明真实行情或数据许可。requests 自动解压后的内容叫响应实体内容，不叫 wire bytes。HTTP QVIX 来源未经传输身份认证，HTTPS也不是市场数值签名。

证据 SQLite 版本追加，不覆盖历史；校验响应和元数据哈希，拒绝凭据字段、空数据版本、未来行情日期和首次观察早于响应时间。规范化数据是独立对象，链接原响应 ID。恶意同时改内容和全部哈希、数据库或本机时钟，不在哈希完整性保证内。

--date 仅用当前取得历史按日期重算，称 retrospective_current_history。--knowledge-as-of 必须有时区，只从证据中取此前已观察版本，不联网、不退回当前缓存、不写证据。采集前历史可得时间不可恢复；没有档案就报证据不足/参数问题，绝不补造。历史/重放结果写在独立目录，不覆盖 latest/badge 或当前价格缓存。

## 日历及身份

--calendar 指向本地 JSON。必须有 timezone=Asia/Shanghai、coverage_start、coverage_end、唯一升序 sessions、provenance.evidence_url；可选 close_time。程序不自动把声明出处认定为官方认证。没有材料或覆盖不够时会话滞后为 unknown，只能描述观测行；日历完整覆盖才重索引缺失会话，缺失保留 NaN，不把两日收益冒充一日。

配置对象明确 index/price_index/index_points 等口径；publisher 未查证则 unverified。指数不是 ETF 价格，QVIX kcb/cyb 字符串不证明对应 ETF/期权或期限。所有当前 QVIX 映射仍 proxy_unverified，不允许在配置里随意写 verified 绕过审计。结构不变量只验证 OHLC 关系，整组换品种仍可能通过。需要交易所/基金/第三方方法论证据后另行认证。

## 独立参考值

--reference-dir 可在运行中校验同对象 CSV及JSON：按 symbol.csv / symbol.json 查找。元数据至少 instrument、instrument_type、price_basis、unit、decimal_places、publisher、evidence_url、independent_from_vendor。提供官方域名不是数值真值证明；结果范围是与用户提供独立参考的样本一致性。运行适配必须在 targets 配置声明 price_decimal_places，否则返回精度证据不足；audit reference 可用实际精度元数据验证，不能事后任意放宽容差。

容差按双方舍入误差之和，超差/参考缺日期即失败；缺身份/独立出处/精度为 insufficient_evidence。不以三商相同或 AKShare 同一解析器当独立真值。

## 来源影响

先固定截止、起点、共同日期、公式和参数，对比价格、收益bp、S/A、PI、分级翻转；再运行各自生产历史以区分价格精度与样本覆盖。可提供同一 QVIX 比较 F/完整口径，缺完整可比值明确 insufficient。报告是诊断，不自动选市场真值，也不混源补齐。任一日期/源变化可能改变历史分位；公式不变不等于统计口径不变。

## 有限金融验证

主问题：现有 PI 是否比预先确定的简单基线更好识别独立观察到的同期下行流动性压力？目标须来自卖压、价差或深度等独立资料，不能用 PI、QVIX 或同一收益尾部作为真值。公开日线本身不足以证明心理恐慌。

先做试点，冻结有限主张、目标、基线、校准结束、连续留出开始、门槛、时间块、种子。模板的500会话/30事件/ΔAP0.05是建议，不是已达到或通用标准。实际冻结时间写入完整性 envelope，晚于留出不得通过；本地文件不是外部公证，伪造全部文件和哈希不可能仅靠本地程序识别。可外部登记协议后再开始未来观察，不允许为旧留出补造预注册。

研究输入含 date,pi,baseline,target(0/1),episode,report_at,available_at。当日价格不可得于15:00前，时间有时区；校准材料必须留出之前可得；事件不可逐阳性行拆分，连续事件只计一个。AP按并列分数组计算；使用配对移动时间块 bootstrap，不IID拆日。留出至少5个时间块，证据不足不能报告通过。ΔAP和95%下界及预定误报预算都需通过；报告目标占比、精确率/召回率、误报/100会话和CI。实际压力目标来源和材料独立性仍需人工审计，不因输入布尔字段而证明。

## 操作命令

```powershell
# 原有离线/联网计算，审计状态随结果显示
python -m techpanic --offline --json
# 默认关闭；只有明确允许本地原响应留存时使用
python -m techpanic --archive-evidence
python -m techpanic --offline --calendar data/audit_inputs/calendar.json
python -m techpanic --knowledge-as-of 2026-10-23T17:00:00+08:00
python -m techpanic audit verify-evidence --directory data/evidence
python -m techpanic audit reference --data data/cache/index_daily/sh000688.csv --metadata data/audit_inputs/vendor.json --reference data/audit_inputs/official.csv --reference-metadata data/audit_inputs/official.json
python -m techpanic audit source-impact --left data/audit_inputs/em.csv --right data/audit_inputs/tx.csv --qvix data/cache/qvix/kcb.csv
python -m techpanic audit protocol-template --output data/audit_inputs/protocol.json
python -m techpanic audit freeze-protocol --protocol data/audit_inputs/protocol.json --output data/audit_inputs/frozen.json
python -m techpanic audit study --data data/audit_inputs/heldout.csv --protocol data/audit_inputs/frozen.json --output data/audit_reports/study.json
```

审计工具 0 表示完成/范围内通过，2 表示失败或证据不足（看 JSON status），5 表示输入无效；不把这些当预测或认证。真实官方日历/收盘参考、原始期权及独立盘口/卖压样本目前未取得，不能声称金融验证完成。数据可公开访问不等于可采集/再分发，MIT 仅覆盖代码；用户应确认留存与衍生输出权限。
