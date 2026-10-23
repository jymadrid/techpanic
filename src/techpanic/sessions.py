"""带出处和覆盖范围的本地会话日历；不凭工作日猜测交易日。"""

from dataclasses import dataclass
from datetime import datetime, time, timezone, timedelta
import hashlib
import json
from pathlib import Path
import pandas as pd

BEIJING = timezone(timedelta(hours=8))


@dataclass(frozen=True)
class SessionCalendar:
    sessions: pd.DatetimeIndex
    coverage_start: pd.Timestamp
    coverage_end: pd.Timestamp
    provenance: dict
    digest: str
    close_time: time = time(15)

    def expected_completed(self, report_at: datetime):
        if report_at.tzinfo is None:
            raise ValueError("报告时间必须有时区")
        report_at = report_at.astimezone(BEIJING)
        day = pd.Timestamp(report_at.date())
        if day < self.coverage_start or day > self.coverage_end:
            return None
        eligible = self.sessions[
            self.sessions <= day
            if report_at.time().replace(tzinfo=None) >= self.close_time
            else self.sessions < day
        ]
        return eligible[-1] if len(eligible) else None

    def stale_sessions(self, data_date, report_at: datetime):
        expected = self.expected_completed(report_at)
        if expected is None or data_date is None:
            return None
        date = pd.Timestamp(data_date).normalize()
        if date < self.coverage_start or date > expected or date not in self.sessions:
            return None
        return int(((self.sessions > date) & (self.sessions <= expected)).sum())

    def missing_sessions(self, dates):
        dates = pd.DatetimeIndex(dates).normalize()
        if not len(dates):
            return []
        expected = self.sessions[(self.sessions >= dates.min()) & (self.sessions <= dates.max())]
        return list(expected.difference(dates))


def load_calendar(path: Path) -> SessionCalendar:
    raw = Path(path).read_bytes()
    data = json.loads(raw.decode("utf-8-sig"))
    if data.get("timezone") != "Asia/Shanghai" or not data.get("provenance", {}).get(
        "evidence_url"
    ):
        raise ValueError("日历必须声明 Asia/Shanghai、覆盖范围及出处 URL")
    dates = pd.DatetimeIndex(pd.to_datetime(data["sessions"], errors="raise"))
    if (
        dates.tz is not None
        or not dates.is_monotonic_increasing
        or dates.has_duplicates
        or (dates != dates.normalize()).any()
    ):
        raise ValueError("日历日期必须无时区、唯一升序的日期")
    start, end = pd.Timestamp(data["coverage_start"]), pd.Timestamp(data["coverage_end"])
    if (
        start.tzinfo is not None
        or end.tzinfo is not None
        or start != start.normalize()
        or end != end.normalize()
        or start > end
        or not len(dates)
        or dates.min() < start
        or dates.max() > end
    ):
        raise ValueError("日历覆盖范围无效")
    close = time.fromisoformat(data.get("close_time", "15:00:00"))
    if close.tzinfo is not None:
        raise ValueError("日历收盘时刻由 Asia/Shanghai 解释，不能另带时区")
    return SessionCalendar(
        dates,
        start,
        end,
        dict(data["provenance"], verification="provided_not_authenticated"),
        hashlib.sha256(raw).hexdigest(),
        close,
    )
