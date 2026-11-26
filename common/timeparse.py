"""Duration and date/time parsing with timezone support.

Timezones use zoneinfo (Windows needs the small `tzdata` package, listed in requirements.txt).
If zoneinfo is unavailable, fixed offsets such as +05:30 / IST / UTC still work.
"""
import re
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # pragma: no cover
    ZoneInfo, ZoneInfoNotFoundError = None, Exception

DEFAULT_TZ = "Asia/Kolkata"
ALIASES = {"ist": "Asia/Kolkata", "india": "Asia/Kolkata", "utc": "UTC", "gmt": "UTC", "est": "America/New_York",
           "edt": "America/New_York", "pst": "America/Los_Angeles", "pdt": "America/Los_Angeles", "cst": "America/Chicago",
           "bst": "Europe/London", "uk": "Europe/London", "cet": "Europe/Paris", "jst": "Asia/Tokyo", "aest": "Australia/Sydney",
           "pkt": "Asia/Karachi", "gst": "Asia/Dubai", "sgt": "Asia/Singapore"}
FIXED = {"Asia/Kolkata": timedelta(hours=5, minutes=30), "UTC": timedelta(0)}
DUR_RE = re.compile(r"(\d+)\s*(w|d|h|m|s)", re.I)
UNIT = {"w": 604800, "d": 86400, "h": 3600, "m": 60, "s": 1}


def get_tz(name):
    """Return a tzinfo for an IANA name, alias, or +HH:MM offset. Raises ValueError if unknown."""
    raw = (name or DEFAULT_TZ).strip()
    key = ALIASES.get(raw.casefold(), raw)
    m = re.fullmatch(r"(?:utc|gmt)?\s*([+-])(\d{1,2})(?::?(\d{2}))?", key, re.I)
    if m:
        sign = 1 if m.group(1) == "+" else -1
        delta = timedelta(hours=int(m.group(2)), minutes=int(m.group(3) or 0))
        if delta > timedelta(hours=14):
            raise ValueError("Offset out of range.")
        return timezone(sign * delta, name=key)
    if ZoneInfo is not None:
        try:
            return ZoneInfo(key)
        except (ZoneInfoNotFoundError, ValueError, OSError):
            pass
    if key in FIXED:
        return timezone(FIXED[key], name=key)
    raise ValueError(f"Unknown timezone `{raw}`. Use a name like `Asia/Kolkata`, `Europe/London`, or an offset like `+05:30`.")


def tz_name(tz):
    return getattr(tz, "key", None) or str(tz)


def parse_duration(text, *, minimum=1, maximum=None):
    """'1h30m', '45m', '2d', or a bare number of minutes. Returns timedelta or None."""
    text = (text or "").strip().lower()
    if not text:
        return None
    if text.isdigit():
        seconds = int(text) * 60
    else:
        parts = DUR_RE.findall(text)
        if not parts or DUR_RE.sub("", text).strip():
            return None
        seconds = sum(int(n) * UNIT[u.lower()] for n, u in parts)
    if seconds < minimum or (maximum is not None and seconds > maximum):
        return None
    return timedelta(seconds=seconds)


def fmt_delta(seconds):
    seconds = int(max(0, seconds))
    parts = []
    for label, size in (("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
        value, seconds = divmod(seconds, size)
        if value:
            parts.append(f"{value}{label}")
    return " ".join(parts[:3]) or "0s"


def parse_when(text, tz, now=None):
    """Parse a future moment. Accepts durations ('in 2h', '90m'), 'YYYY-MM-DD HH:MM', 'DD/MM HH:MM', 'HH:MM',
    'tomorrow HH:MM', with optional am/pm. Returns an aware UTC datetime or raises ValueError."""
    now = now or datetime.now(timezone.utc)
    raw = (text or "").strip().lower()
    raw = re.sub(r"^in\s+", "", raw)
    dur = parse_duration(raw)
    if dur is not None:
        return now + dur
    local_now = now.astimezone(tz)
    day = local_now.date()
    explicit_day = False
    m = re.match(r"^(today|tomorrow)\s*(.*)$", raw)
    if m:
        explicit_day = True
        day = day + timedelta(days=1 if m.group(1) == "tomorrow" else 0)
        raw = m.group(2).strip() or "09:00"
    date_part, time_part = None, raw
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})\s*(.*)$", raw)
    if m:
        date_part = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
        time_part = m.group(4).strip() or "09:00"
    else:
        m = re.match(r"^(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\s*(.*)$", raw)
        if m:
            year = int(m.group(3)) if m.group(3) else day.year
            year = year + 2000 if year < 100 else year
            date_part = (year, int(m.group(2)), int(m.group(1)))
            time_part = m.group(4).strip() or "09:00"
    tm = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", time_part)
    if not tm:
        raise ValueError("I couldn't read that time. Try `in 2h`, `18:30`, `tomorrow 9am`, or `2026-12-31 20:00`.")
    hour, minute, ampm = int(tm.group(1)), int(tm.group(2) or 0), tm.group(3)
    if ampm:
        if not 1 <= hour <= 12:
            raise ValueError("Use 1-12 with am/pm.")
        hour = hour % 12 + (12 if ampm == "pm" else 0)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("That time isn't valid.")
    y, mo, d = date_part if date_part else (day.year, day.month, day.day)
    try:
        naive = datetime(y, mo, d, hour, minute)
    except ValueError:
        raise ValueError("That date isn't valid.") from None
    local = naive.replace(tzinfo=tz)
    if date_part is None and not explicit_day and local <= local_now:
        local += timedelta(days=1)  # bare HH:MM in the past means the next occurrence
    result = local.astimezone(timezone.utc)
    if result <= now:
        raise ValueError("That time is in the past.")
    return result


def next_daily(hour, minute, tz, now=None):
    now = now or datetime.now(timezone.utc)
    local_now = now.astimezone(tz)
    candidate = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= local_now:
        candidate += timedelta(days=1)
    return candidate.astimezone(timezone.utc)


def parse_hhmm(text):
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", text or "")
    if not m or not (0 <= int(m.group(1)) <= 23 and 0 <= int(m.group(2)) <= 59):
        raise ValueError("Use 24-hour HH:MM, e.g. 10:00 or 18:30.")
    return int(m.group(1)), int(m.group(2))
