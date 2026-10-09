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
