"""Due-date and meeting-date parsing (SDD §6.1, §6.4).

Everything here is pure: text + anchor date in, ``date | None`` out.
Weeks start on Monday.
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta

WEEKDAYS = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}
MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sept": 9, "sep": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}

_WD = "(?P<wd>" + "|".join(sorted(WEEKDAYS, key=len, reverse=True)) + r")\.?"
_MON = "(?P<mon>" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\.?"
_DAY = r"(?P<day>\d{1,2})(?:st|nd|rd|th)?"
_YEAR = r"(?P<year>\d{4})"

_ISO = re.compile(r"(?P<iso>\d{4}-\d{2}-\d{2})")
_DAY_MON = re.compile(rf"(?:{_WD},?\s+)?{_DAY}\s+(?:of\s+)?{_MON}(?:,?\s+{_YEAR})?")
_MON_DAY = re.compile(rf"(?:{_WD},?\s+)?{_MON}\s+{_DAY}(?:,?\s+{_YEAR})?")
_WEEKDAY = re.compile(rf"(?P<next>next\s+|this\s+)?{_WD}")
_TODAY = re.compile(r"(?:today|eod|end of (?:the )?day|cob|close of business)(?:\s+today)?")
_TOMORROW = re.compile(r"(?:eod\s+tomorrow|tomorrow(?:\s+eod)?|end of (?:the )?day tomorrow)")
_END_WEEK = re.compile(r"(?:the\s+)?end of (?:the |this )?week")
_END_NEXT_WEEK = re.compile(r"(?:the\s+)?end of next week|next week")
_END_MONTH = re.compile(r"(?:the\s+)?end of (?:the |this )?month")
_END_NEXT_MONTH = re.compile(r"(?:the\s+)?end of next month")
_MID = re.compile(rf"mid[- ]?{_MON}")
_MONTH_ONLY = re.compile(rf"(?:early |late |end of )?{_MON}(?:\s+{_YEAR})?")

_TRAILING = re.compile(r"[\s.,;:!)\]]+$")

ROLLOVER_DAYS = 60


@dataclass(frozen=True)
class WeekdayCheck:
    """Set when a date carried an explicit weekday that does not match."""
    stated: str
    actual: str


def _full(rx: re.Pattern, text: str):
    return rx.fullmatch(text)


def _with_year(day: int, month: int, year: int | None, anchor: date | None) -> date | None:
    if year is None:
        if anchor is None:
            return None
        year = anchor.year
        try:
            d = date(year, month, day)
        except ValueError:
            return None
        if (anchor - d).days > ROLLOVER_DAYS:
            try:
                d = date(year + 1, month, day)
            except ValueError:
                return None
        return d
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _friday_of_week(d: date) -> date:
    return d - timedelta(days=d.weekday()) + timedelta(days=4)


def _last_day(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def parse_absolute(text: str, anchor: date | None = None) -> tuple[date | None, WeekdayCheck | None]:
    """Parse an absolute date (ISO, '9 September 2026', 'Monday, 14 September 2026', 'Sep 14, 2026',
    or '10 Sep' when an anchor supplies the year). Returns (date, weekday_mismatch)."""
    t = _clean(text)
    if m := _full(_ISO, t):
        try:
            return date.fromisoformat(m["iso"]), None
        except ValueError:
            return None, None
    for rx in (_DAY_MON, _MON_DAY):
        if m := _full(rx, t):
            year = int(m["year"]) if m["year"] else None
            d = _with_year(int(m["day"]), MONTHS[m["mon"]], year, anchor)
            if d is None:
                return None, None
            check = None
            if m["wd"] and WEEKDAYS[m["wd"]] != d.weekday():
                check = WeekdayCheck(m["wd"], d.strftime("%A"))
            return d, check
    return None, None


def _clean(text: str) -> str:
    t = text.strip().lower()
    t = _TRAILING.sub("", t)
    return re.sub(r"\s+", " ", t)


def resolve_due(expr: str, meeting: date) -> date | None:
    """Resolve the text that follows a due trigger ('Friday', 'EOD tomorrow', '10 Sep', ...)."""
    t = _clean(expr)
    if not t:
        return None
    d, _ = parse_absolute(t, meeting)
    if d:
        return d
    if _full(_TOMORROW, t):
        return meeting + timedelta(days=1)
    if _full(_TODAY, t):
        return meeting
    if m := _full(_WEEKDAY, t):
        target = WEEKDAYS[m["wd"]]
        if m["next"] and m["next"].strip() == "next":
            next_monday = meeting - timedelta(days=meeting.weekday()) + timedelta(days=7)
            return next_monday + timedelta(days=target)
        delta = (target - meeting.weekday()) % 7 or 7  # strictly after the meeting
        return meeting + timedelta(days=delta)
    if _full(_END_NEXT_WEEK, t):
        return _friday_of_week(meeting) + timedelta(days=7)
    if _full(_END_WEEK, t):
        return _friday_of_week(meeting)
    if _full(_END_NEXT_MONTH, t):
        y, mth = (meeting.year + 1, 1) if meeting.month == 12 else (meeting.year, meeting.month + 1)
        return _last_day(y, mth)
    if _full(_END_MONTH, t):
        return _last_day(meeting.year, meeting.month)
    if m := _full(_MID, t):
        return _with_year(15, MONTHS[m["mon"]], None, meeting)
    return None


def is_month_only(expr: str) -> bool:
    return bool(_full(_MONTH_ONLY, _clean(expr)))


# --------------------------------------------------------------------------- due phrase in a task line

# Triggers whose failure to resolve is worth a warning.
_STRONG = ("no later than", "by", "due", "before", "until")
# Triggers that only count when they resolve (they are common in normal prose).
_WEAK = ("on", "from", "for")


@dataclass
class DuePhrase:
    start: int            # index in the text where the due phrase (trigger) starts
    raw: str              # e.g. "by Friday"
    date: date | None     # resolved date, or None if unresolved


def find_due(text: str, meeting: date) -> DuePhrase | None:
    """Find the due phrase in an item's text.

    Every trigger position is tried; the right-most one whose remainder resolves to a full date
    wins. If none resolves, the right-most *strong* trigger (by/due/before/until) — or a weak
    trigger followed only by a month name ('for November') — is returned as unresolved.
    """
    candidates: list[tuple[int, str, str]] = []
    for m in re.finditer(r"(?<![\w-])(no later than|by|due|before|until|on|from|for)\s+", text, re.IGNORECASE):
        candidates.append((m.start(), m.group(1).lower(), text[m.end():]))

    for start, trig, rest in reversed(candidates):
        if trig == "due":
            rest = re.sub(r"^(?:by|on)\s+", "", rest, flags=re.IGNORECASE)
        d = resolve_due(rest, meeting)
        if d:
            return DuePhrase(start, _clean_raw(text[start:]), d)

    for start, trig, rest in reversed(candidates):
        if trig in _STRONG and not _looks_like_amount(rest):
            return DuePhrase(start, _clean_raw(text[start:]), None)
        if trig in _WEAK and is_month_only(rest):
            return DuePhrase(start, _clean_raw(text[start:]), None)
    return None


def _looks_like_amount(rest: str) -> bool:
    return bool(re.match(r"\s*[\d.]+\s*%", rest))


def _clean_raw(s: str) -> str:
    return _TRAILING.sub("", s.strip())
