from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def test_workflows_do_not_upload_or_schedule_market_data():
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    daily = (ROOT / ".github/workflows/daily.yml").read_text(encoding="utf-8")
    assert "actions/upload-artifact" not in ci + daily
    assert "cron:" not in daily
    assert "inputs.run_live == true" in ci and "inputs.run_live == true" in daily
    assert "continue-on-error: true" not in ci
