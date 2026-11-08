"""Best-time windows, quiet hours and local-day arithmetic (pure, no database)."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

Window = tuple[time, time]
SEARCH_DAYS = 8


def valid_timezone(name: str) -> bool:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return False
    return True


def zone_for(name: str) -> ZoneInfo:
    return ZoneInfo(name if valid_timezone(name) else "UTC")


def _parse_time(text: str) -> time:
    hours, _sep, minutes = text.strip().partition(":")
    if not (hours.isdigit() and minutes.isdigit()):
        raise ValueError(f"“{text.strip()}” is not a HH:MM time.")
    hour, minute = int(hours), int(minutes)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"“{text.strip()}” is not a valid time of day.")
    return time(hour, minute)


def parse_windows(value: str) -> list[Window]:
    """``"09:00-11:00, 18:00-21:00"`` -> [(09:00, 11:00), (18:00, 21:00)]."""
    windows: list[Window] = []
    for part in value.split(","):
        if not part.strip():
            continue
        start_text, sep, end_text = part.partition("-")
        if not sep:
            raise ValueError(f"“{part.strip()}” must look like 09:00-11:00.")
        start, end = _parse_time(start_text), _parse_time(end_text)
        if start >= end:
            raise ValueError(f"“{part.strip()}” must end after it starts.")
        windows.append((start, end))
    return windows


def in_quiet(moment: time, start: time | None, end: time | None) -> bool:
    if start is None or end is None or start == end:
        return False
    if start < end:
        return start <= moment < end
    return moment >= start or moment < end


def _in_windows(moment: time, windows: list[Window]) -> bool:
    return not windows or any(start <= moment < end for start, end in windows)


def allowed(
    local_moment: datetime,
    windows: list[Window],
    quiet_start: time | None,
    quiet_end: time | None,
) -> bool:
    clock = local_moment.timetz().replace(tzinfo=None)
    return _in_windows(clock, windows) and not in_quiet(clock, quiet_start, quiet_end)


def next_allowed(
    moment: datetime,
    *,
    tz_name: str,
    windows: list[Window],
    quiet_start: time | None = None,
    quiet_end: time | None = None,
) -> datetime:
    """The earliest instant >= ``moment`` inside a window and outside quiet hours.

    The allowed set is a union of intervals, so the answer is ``moment`` itself or the
    start of a window / the end of quiet hours; those boundaries are tried in order.
    """
    zone = zone_for(tz_name)
    current = moment.astimezone(zone)
    if allowed(current, windows, quiet_start, quiet_end):
        return moment
    boundaries: list[datetime] = []
    for offset in range(SEARCH_DAYS):
        day = (current + timedelta(days=offset)).date()
        starts = [start for start, _end in windows]
        if quiet_end is not None:
            starts.append(quiet_end)
        for clock in starts:
            boundaries.append(datetime.combine(day, clock, tzinfo=zone))
    for boundary in sorted(b for b in boundaries if b >= current):
        if allowed(boundary, windows, quiet_start, quiet_end):
            return boundary.astimezone(moment.tzinfo)
    return moment


def local_day_bounds(moment: datetime, tz_name: str) -> tuple[datetime, datetime]:
    """UTC start and end of the local calendar day containing ``moment``."""
    zone = zone_for(tz_name)
    local = moment.astimezone(zone)
    start = datetime.combine(local.date(), time(0, 0), tzinfo=zone)
    return start, start + timedelta(days=1)
