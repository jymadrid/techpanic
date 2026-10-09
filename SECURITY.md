# 安全政策

## 报告漏洞

如果你发现安全问题，请**不要**直接开公开 Issue。

请通过 GitHub 的 [Private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability) 提交，或发邮件给维护者（见仓库主页）。

请在报告中包含：

- 受影响的版本或 commit；
- 复现步骤或 PoC；
- 影响评估（能做什么、不能做什么）。

我们会在 7 天内确认收到，并在修复后于 CHANGELOG 中致谢（除非你要求匿名）。

## 本项目的攻击面

techpanic 是一个**本地只在命令行运行**的工具，没有服务端、没有用户系统、不需要也不存储任何凭据。因此攻击面很小，但仍有三处值得注意：

| 面 | 风险 | 现状 |
|---|---|---|
| HTTP 数据获取 | 上游返回恶意/畸形内容 | 只解析 CSV 文本，不执行任何内容；解析失败即抛 `DataQualityError` |
| 代理配置 | `proxy` 指向恶意中间人 | 用户自行配置；建议只在可信网络下使用 |
| 依赖链 | 传递依赖漏洞 | 版本精确锁定，`requirements.txt` 可审计 |

## 明确的安全设计

- **不收集任何遥测。** 程序不向除数据源之外的任何地址发起请求。
- **不需要任何凭据。** 没有 token、cookie、账号、API Key，因此也没有凭据可泄露。
- **不执行上游内容。** 上游 CSV 只被当作文本解析，不做 eval / exec / 反序列化。
- **写盘原子化。** 所有输出先写临时文件再原子替换，失败不会破坏已有缓存。

## 关于数据源隐私

程序会把请求发往新浪财经与 `1.optbbs.com`（以及你配置的代理）。这些请求会暴露你的 IP。若你在意，请自备代理。
