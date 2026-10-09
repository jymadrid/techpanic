"""S1/S4/S5 发布契约回归测试；输入均在临时目录生成。"""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from techpanic.cli import build_parser
from techpanic.config import load_config

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("flag", ["offline", "refresh"])
@pytest.mark.parametrize("raw,expected", [("1", True), ("TRUE", True), ("yes", True),
                                        (" on ", True), ("0", False), ("false", False)])
def test_unset_cli_flag_uses_environment(monkeypatch, tmp_path, flag, raw, expected):
    monkeypatch.setenv("TECHPANIC_" + flag.upper(), raw)
    args = build_parser().parse_args([])
    assert getattr(args, flag) is None
    cfg = load_config(data_dir=tmp_path, overrides={flag: getattr(args, flag)})
    assert getattr(cfg, flag) is expected


@pytest.mark.parametrize("flag", ["offline", "refresh"])
def test_explicit_cli_flag_overrides_environment(monkeypatch, tmp_path, flag):
    monkeypatch.setenv("TECHPANIC_" + flag.upper(), "0")
    args = build_parser().parse_args(["--" + flag])
    assert getattr(load_config(data_dir=tmp_path, overrides={flag: getattr(args, flag)}), flag)
    monkeypatch.setenv("TECHPANIC_" + flag.upper(), "1")
    assert not getattr(load_config(data_dir=tmp_path, overrides={flag: False}), flag)


def test_explicit_data_dir_overrides_environment(monkeypatch, tmp_path):
    env_dir, cli_dir, override_dir = (tmp_path / n for n in ("env", "cli", "override"))
    monkeypatch.setenv("TECHPANIC_DATA_DIR", str(env_dir))
    assert load_config().data_dir == env_dir
    assert load_config(data_dir=cli_dir).data_dir == cli_dir
    assert load_config(data_dir=cli_dir, overrides={"data_dir": override_dir}).data_dir == override_dir


def test_cli_env_offline_and_data_dir_precedence(monkeypatch, seeded_data_dir, tmp_path):
    """真实子进程；若 env 离线失效，防护代理阻止其意外联网。"""
    env_dir = tmp_path / "unused-env-dir"
    monkeypatch.setenv("TECHPANIC_DATA_DIR", str(env_dir))
    monkeypatch.setenv("TECHPANIC_OFFLINE", "1")
    monkeypatch.setenv("TECHPANIC_PROXY", "http://127.0.0.1:1")
    env = os.environ.copy()
    proc = subprocess.run(
        [sys.executable, "-m", "techpanic", "--json", "--data-dir", str(seeded_data_dir)],
        cwd=ROOT, env=env, capture_output=True, timeout=60,
    )
    assert proc.returncode in (0, 2), proc.stderr.decode("utf-8", "replace")
    payload = json.loads(proc.stdout.decode("utf-8"))
    assert len(payload["results"]) == 2
    assert (seeded_data_dir / "output" / "latest.json").is_file()
    assert not env_dir.exists()


def test_documented_requirements_install_package():
    lines = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    assert "-e ." in [line.strip() for line in lines if not line.startswith("#")]


def test_macos_launcher_is_executable_in_git():
    proc = subprocess.run(["git", "ls-files", "-s", "start.command"], cwd=ROOT,
                          capture_output=True, text=True, check=True)
    assert proc.stdout.startswith("100755 "), proc.stdout


@pytest.mark.parametrize("launcher", ["start.bat", "start.sh", "start.command"])
def test_launchers_recheck_package_even_with_old_marker(launcher):
    text = (ROOT / launcher).read_text(encoding="utf-8")
    assert "-m techpanic --version" in text
    assert "-r requirements.txt" in text
