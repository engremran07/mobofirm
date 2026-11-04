"""
Date/Time Utilities
===================

Pure date/time manipulation functions with no business logic.
"""

from datetime import datetime, timedelta


def now():
    """
    Get current timezone-aware datetime.

    Returns:
        Current datetime (timezone-aware)
    """
    from django.utils import timezone

    return timezone.now()


def is_future(dt: datetime) -> bool:
    """
    Check if datetime is in the future.

    Args:
        dt: Datetime to check

    Returns:
        True if datetime is in future
    """
    return dt > now()


def is_past(dt: datetime) -> bool:
    """
    Check if datetime is in the past.

    Args:
        dt: Datetime to check

    Returns:
        True if datetime is in past
    """
    return dt < now()


def days_until(dt: datetime) -> int:
    """
    Calculate days until a future datetime.

    Args:
        dt: Future datetime

    Returns:
        Number of days (0 if in past)
    """
    delta = dt - now()
    return max(0, delta.days)


def days_since(dt: datetime) -> int:
    """
    Calculate days since a past datetime.

    Args:
        dt: Past datetime

    Returns:
        Number of days
    """
    delta = now() - dt
    return delta.days


def hours_since(dt: datetime) -> int:
    """
    Calculate hours since a past datetime.

    Args:
        dt: Past datetime

    Returns:
        Number of hours
    """
    delta = now() - dt
    return int(delta.total_seconds() / 3600)


def format_relative(dt: datetime) -> str:
    """
    Format datetime as relative string.

    Args:
        dt: Datetime to format

    Returns:
        Relative time string (e.g., '2 hours ago', 'in 3 days')

    Examples:
        >>> format_relative(now() - timedelta(minutes=5))
        '5 minutes ago'
        >>> format_relative(now() + timedelta(days=2))
        'in 2 days'
    """
    delta = now() - dt
    is_future_time = delta.total_seconds() < 0

    if is_future_time:
        delta = -delta
        prefix = "in "
        suffix = ""
    else:
        prefix = ""
        suffix = " ago"

    seconds = int(delta.total_seconds())

    if seconds < 60:
        if is_future_time:
            return "in a moment"
        return "just now"

    minutes = seconds // 60
    if minutes < 60:
        unit = "minute" if minutes == 1 else "minutes"
        return f"{prefix}{minutes} {unit}{suffix}"

    hours = minutes // 60
    if hours < 24:
        unit = "hour" if hours == 1 else "hours"
        return f"{prefix}{hours} {unit}{suffix}"

    days = delta.days
    if days == 1:
        return "tomorrow" if is_future_time else "yesterday"

    if days < 7:
        return f"{prefix}{days} days{suffix}"

    weeks = days // 7
    if weeks < 4:
        unit = "week" if weeks == 1 else "weeks"
        return f"{prefix}{weeks} {unit}{suffix}"

    months = days // 30
    if months < 12:
        unit = "month" if months == 1 else "months"
        return f"{prefix}{months} {unit}{suffix}"

    years = days // 365
    unit = "year" if years == 1 else "years"
    return f"{prefix}{years} {unit}{suffix}"


def start_of_day(dt: datetime | None = None) -> datetime:
    """
    Get start of day (00:00:00).

    Args:
        dt: Datetime (uses now if not provided)

    Returns:
        Start of day
    """
    dt = dt or now()
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def end_of_day(dt: datetime | None = None) -> datetime:
    """
    Get end of day (23:59:59).

    Args:
        dt: Datetime (uses now if not provided)

    Returns:
        End of day
    """
    dt = dt or now()
    return dt.replace(hour=23, minute=59, second=59, microsecond=999999)


def start_of_week(dt: datetime | None = None) -> datetime:
    """
    Get start of week (Monday 00:00:00).

    Args:
        dt: Datetime (uses now if not provided)

    Returns:
        Start of week
    """
    dt = dt or now()
    days_since_monday = dt.weekday()
    monday = dt - timedelta(days=days_since_monday)
    return start_of_day(monday)


def end_of_week(dt: datetime | None = None) -> datetime:
    """
    Get end of week (Sunday 23:59:59).

    Args:
        dt: Datetime (uses now if not provided)

    Returns:
        End of week
    """
    dt = dt or now()
    days_until_sunday = 6 - dt.weekday()
    sunday = dt + timedelta(days=days_until_sunday)
    return end_of_day(sunday)


def start_of_month(dt: datetime | None = None) -> datetime:
    """
    Get start of month (1st day 00:00:00).

    Args:
        dt: Datetime (uses now if not provided)

    Returns:
        Start of month
    """
    dt = dt or now()
    return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def end_of_month(dt: datetime | None = None) -> datetime:
    """
    Get end of month (last day 23:59:59).

    Args:
        dt: Datetime (uses now if not provided)

    Returns:
        End of month
    """
    dt = dt or now()
    # Move to next month, then subtract a day
    if dt.month == 12:
        next_month = dt.replace(year=dt.year + 1, month=1, day=1)
    else:
        next_month = dt.replace(month=dt.month + 1, day=1)

    last_day = next_month - timedelta(days=1)
    return end_of_day(last_day)


def is_today(dt: datetime) -> bool:
    """Check if datetime is today"""
    return dt.date() == now().date()


def is_this_week(dt: datetime) -> bool:
    """Check if datetime is in current week"""
    week_start = start_of_week()
    week_end = end_of_week()
    return week_start <= dt <= week_end


def is_this_month(dt: datetime) -> bool:
    """Check if datetime is in current month"""
    return dt.year == now().year and dt.month == now().month


def age_in_years(birth_date: datetime) -> int:
    """
    Calculate age in years from birth date.

    Args:
        birth_date: Date of birth

    Returns:
        Age in years
    """
    today = now().date()
    bd = birth_date.date() if isinstance(birth_date, datetime) else birth_date

    age = today.year - bd.year

    # Adjust if birthday hasn't occurred this year
    if (today.month, today.day) < (bd.month, bd.day):
        age -= 1

    return age


__all__ = [
    "age_in_years",
    "days_since",
    "days_until",
    "end_of_day",
    "end_of_month",
    "end_of_week",
    "format_relative",
    "hours_since",
    "is_future",
    "is_past",
    "is_this_month",
    "is_this_week",
    "is_today",
    "now",
    "start_of_day",
    "start_of_month",
    "start_of_week",
]
