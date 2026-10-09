"""真实 cmd 控制流测试；依赖安装输入为临时空清单，不访问网络。"""
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="requires Windows cmd")


@pytest.fixture
def launcher(tmp_path):
    root = tmp_path / "中文 空格 & !"
    root.mkdir()
    shutil.copyfile(ROOT / "start.bat", root / "start.bat")
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(root / ".venv")],
                   check=True, capture_output=True, timeout=60)
    python = root / ".venv" / "Scripts" / "python.exe"
    # Stub only the application, leaving venv/ensurepip/pip/cmd real.
    (root / "techpanic.py").write_text('print("techpanic 1.0.0")\n', encoding="utf-8")
    (root / "requirements.txt").write_text("# empty test requirements\n", encoding="utf-8")
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["PIP_NO_INDEX"] = "1"
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    env["TEMP"] = env["TMP"] = str(root)
    return root, python, env


def _run(launcher):
    root, _, env = launcher
    command = 'cmd /d /s /c ""' + str(root / "start.bat") + '" --version <nul"'
    proc = subprocess.run(command, cwd=root.parent, env=env, capture_output=True, timeout=90)
    out = proc.stdout.decode("utf-8", "strict")
    err = proc.stderr.decode("utf-8", "strict")
    assert "not recognized" not in out + err
    assert "不是内部或外部命令" not in out + err
    return proc.returncode, out, err


def test_missing_pip_bootstraps_and_installs(launcher):
    root, python, env = launcher
    missing = subprocess.run([str(python), "-m", "pip", "--version"], cwd=root,
                             env=env, capture_output=True)
    assert missing.returncode != 0
    code, out, err = _run(launcher)
    assert code == 0, out + err
    assert "ensurepip" in out
    assert "techpanic 1.0.0" in out
    assert (root / ".venv" / ".techpanic-installed").is_file()
    subprocess.run([str(python), "-m", "pip", "--version"], env=env,
                   check=True, capture_output=True, timeout=30)


def test_dependency_failure_has_readable_diagnostics(launcher):
    root, _, _ = launcher
    # Simulate only a deterministic pip-install failure; cmd remains real.
    (root / "pip.py").write_text(
        'import sys\nif "install" in sys.argv:\n'
        '    print("SIMULATED_PIP_FAILURE")\n    sys.exit(1)\n', encoding="utf-8")
    code, out, err = _run(launcher)
    assert code == 4, out + err
    assert "依赖安装失败" in out
    assert "SIMULATED_PIP_FAILURE" in out
    assert not (root / ".venv" / ".techpanic-installed").exists()


def test_ensurepip_failure_exits_before_dependency_install(launcher):
    root, _, _ = launcher
    (root / "ensurepip.py").write_text('import sys\nsys.exit(1)\n', encoding="utf-8")
    code, out, err = _run(launcher)
    assert code == 4, out + err
    assert "内置 ensurepip 无法修复 pip" in out
    assert "耗时取决于网络" not in out
    assert not (root / ".venv" / ".techpanic-installed").exists()


def test_existing_marker_does_not_skip_pip_bootstrap(launcher):
    root, _, _ = launcher
    (root / ".venv" / ".techpanic-installed").write_text("old-marker", encoding="utf-8")
    code, out, err = _run(launcher)
    assert code == 0, out + err
    assert "ensurepip" in out
    assert "依赖已就绪" in out
