"""示例 3：把读数导出成一张 Markdown 报告。

运行：
    python examples/03_export_report.py

适合放进周报、笔记或内部看板。
"""

from pathlib import Path

from techpanic.config import load_config
from techpanic.pipeline import run

cfg = load_config(data_dir="./data")
result = run(cfg, say=lambda m: None)

# 写到 data/output/ 而不是仓库根目录，避免在仓库里留下游离文件
out_path = Path(cfg.output_dir) / "report.md"

lines = [
    "# 科技板块恐慌指数 PI 观测记录",
    "",
    f"- 生成时间：{result.finished_at:%Y-%m-%d %H:%M}",
    f"- 耗时：{result.elapsed:.1f} 秒",
    f"- 退出码：{result.exit_code}（0=成功，2=部分降级）",
    "",
    "| 标的 | A 完整口径 | 分级 | 数据日 | B 即时口径 | 分级 | 方向 | 当日 | 近5日 |",
    "|---|---:|---|---|---:|---|---|---:|---:|",
]

for tr in result.targets:
    if tr.frame is None:
        lines.append(f"| {tr.target.name} | — | 无值 | — | — | 无值 | — | — | — |")
        continue
    lines.append(
        "| {n} | {a} | {al} | {ad} | {b} | {bl} | {d} | {r} | {r5} |".format(
            n=tr.target.name,
            a="—" if not tr.has_full else f"{tr.full_value:.1f}",
            al=tr.full_level,
            ad="—" if tr.full_date is None else tr.full_date.date(),
            b="—" if not tr.has_price else f"{tr.price_value:.1f}",
            bl=tr.price_level,
            d=tr.direction,
            r="—" if tr.ret is None else f"{tr.ret:+.2f}%",
            r5="—" if tr.ret5 is None else f"{tr.ret5:+.2f}%",
        )
    )

if result.warnings:
    lines += ["", "## 本次运行的降级说明", ""]
    lines += [f"- {w}" for w in result.warnings]

lines += [
    "",
    "> A 含期权隐含波动率，数据日可能滞后 1 个交易日；B 只用价格，数据日最新。",
    "> **两者口径不同，不可相减解读。**",
    "",
    "> 本指标为研究/观测级度量，不预测涨跌方向，不构成投资建议。",
]

out_path.write_text("\n".join(lines), encoding="utf-8")
print(f"已写出 {out_path.resolve()}")
