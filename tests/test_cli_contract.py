"""端到端：CLI 契约（退出码、机器可读 stdout）。

这些断言都对应 README / docs/CONFIGURATION.md 里对用户的承诺，
一旦被改坏必须是红灯，而不是等用户发现。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(*args: str, cwd: Path | None = None):
    return subprocess.run(
        [sys.executable, "-m", "techpanic", *args],
        capture_output=True,
        cwd=str(cwd or ROOT),
    )


def test_json_stdout_is_pure_json(seeded_data_dir):
    """--json 承诺「把 JSON 结果打印到标准输出」：stdout 必须能被 json.loads 直接吃下，
    且不得混入横幅、进度条或 ANSI 转义（否则管道给 jq / CI 全部失效）。"""
    p = _run("--offline", "--json", "--data-dir", str(seeded_data_dir))
    assert p.returncode in (0, 2), p.stderr.decode("utf-8", "replace")

    text = p.stdout.decode("utf-8")
    payload = json.loads(text)  # 混入任何人类文本都会在这里炸

    assert payload["schema"] == "v1"
    assert payload["generator"] == "techpanic"
    assert payload["exit_code"] == p.returncode
    assert len(payload["results"]) == 2
    assert b"\x1b" not in p.stdout, "stdout 不得含 ANSI 转义"


def test_json_stdout_has_no_progress_noise(seeded_data_dir):
    """非 TTY 下进度条本就静默；这条锁死「即使以后加了非 TTY 输出也不能污染 stdout」。"""
    p = _run("--offline", "--json", "--data-dir", str(seeded_data_dir))
    text = p.stdout.decode("utf-8")
    for noisy in ("【", "已保存", "耗时", "====", "科创50】"):
        assert noisy not in text, f"stdout 混入了人类文本：{noisy!r}"


def test_quiet_json_is_identical_to_plain_json(seeded_data_dir):
    """--quiet 与 --json 同用时，stdout 必须仍是同一份 JSON（只是人类文本更少）。"""
    a = _run("--offline", "--json", "--data-dir", str(seeded_data_dir))
    b = _run("--offline", "--json", "--quiet", "--data-dir", str(seeded_data_dir))
    da = json.loads(a.stdout.decode("utf-8"))
    db = json.loads(b.stdout.decode("utf-8"))
    # generated_at 会变，比较实质内容
    assert da["results"] == db["results"]
    assert da["schema"] == db["schema"]


def test_exit_code_3_when_no_data_and_no_cache(tmp_path):
    """无网络 + 无缓存 → 退出码 3，且必须给中文下一步而不是 Python 堆栈。"""
    empty = tmp_path / "empty"
    p = _run("--offline", "--data-dir", str(empty))
    assert p.returncode == 3
    merged = (p.stdout + p.stderr).decode("utf-8", "replace")
    assert "Traceback" not in merged
    assert "python -m techpanic" in merged, "缺少可照做的下一步指引"


def test_exit_code_5_on_bad_date(seeded_data_dir):
    """非法日期 → 退出码 5（参数错误），不是崩溃。"""
    p = _run("--offline", "--data-dir", str(seeded_data_dir), "--date", "不是日期")
    assert p.returncode == 5
    merged = (p.stdout + p.stderr).decode("utf-8", "replace")
    assert "Traceback" not in merged


def test_version_flag():
    p = _run("--version")
    assert p.returncode == 0
    assert "techpanic" in p.stdout.decode("utf-8")

# ---------------------------------------------------------------- 编码契约
#
# 下面三条对应一次真实事故：中文 Windows 的默认代码页是 GBK(cp936)。
#   * --json 写出的是 GBK 字节流 → 下游 json.loads(utf-8) 在「科创」处
#     UnicodeDecodeError（0xBF 0xC6 不是合法 UTF-8）；
#   * 界面里的「⚠」在 cp936 里没有映射 → stdout 一旦重定向/接管道，
#     直接抛 UnicodeEncodeError，整个进程退 1、报告只印一半、产物只剩 4/7；
#   * argparse 参数错误默认退 2，与「正常降级」撞码。
# 这些**只在中文 Windows 上复现**，CI 只跑 ubuntu 时永远发现不了。


def test_json_bytes_are_valid_utf8(seeded_data_dir):
    """--json 的 stdout 必须能被严格按 UTF-8 解析。"""
    p = _run("--offline", "--json", "--data-dir", str(seeded_data_dir))
    text = p.stdout.decode("utf-8", errors="strict")  # 严格解码，不宽容
    payload = json.loads(text)
    assert payload["schema"] == "v1"
    assert "科创" in text or "创业板" in text  # 中文必须真的在里面


def test_redirected_human_output_is_complete(seeded_data_dir):
    """人类可读输出被重定向时不得崩溃，且报告必须完整。

    旧实现在这里退 1，且因为「⚠」无法用 cp936 编码而只写出部分报告。
    """
    p = _run("--offline", "--no-color", "--data-dir", str(seeded_data_dir))
    assert p.returncode in (0, 2), f"重定向时异常退出：{p.returncode}"
    body = p.stdout.decode("utf-8", errors="strict")
    assert "恐慌指数" in body
    assert body.count("PI =") >= 2, "报告似乎只打印了一部分"


def test_bad_argument_exits_5_not_2():
    """参数错误必须是 5，不能与「正常降级」的 2 撞码。

    本项目的 2 表示「部分降级」（QVIX 滞后，每天都会发生）。
    撞码会让定时任务把「参数写错」当成「数据略旧」而放过。
    """
    p = _run("--definitely-not-a-flag")
    assert p.returncode == 5, f"参数错误应退 5，实际 {p.returncode}"
    assert "参数错误" in p.stderr.decode("utf-8", errors="replace")

