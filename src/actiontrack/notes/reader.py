"""Read one note file: header (title, date, attendees) + body lines, or a skip reason (SDD §6.1, §6.6)."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import yaml

from actiontrack.resolve.dates import parse_absolute

BINARY_SIGNATURES = (b"\x89PNG", b"%PDF", b"GIF8", b"\xff\xd8\xff", b"PK\x03\x04")
MAX_HEADER_SCAN = 25

_TITLE = re.compile(r"^#\s+(?:meeting\s*:\s*)?(?P<title>.+?)\s*$", re.IGNORECASE)
_DATE = re.compile(r"^\s*(?:\*\*)?date(?:\*\*)?\s*:\s*(?:\*\*)?\s*(?P<v>.*?)\s*$", re.IGNORECASE)
_ATTENDEES = re.compile(r"^\s*(?:\*\*)?attendees(?:\*\*)?\s*:\s*(?:\*\*)?\s*(?P<v>.*?)\s*$", re.IGNORECASE)


@dataclass
class Warning_:
    code: str
    message: str
    line_no: int | None = None


@dataclass
class Note:
    path: Path
    sha256: str
    title: str | None
    meeting_date: date
    attendees: list[str]
    body: list[tuple[int, str]]          # (1-based line number, text)
    warnings: list[Warning_] = field(default_factory=list)


@dataclass
class Skipped:
    path: Path
    sha256: str | None
    reason: str
    detail: str


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_note(path: Path) -> Note | Skipped:
    try:
        data = path.read_bytes()
    except OSError as e:
        return Skipped(path, None, "READ_ERROR", str(e))
    digest = sha256_of(data)
    try:
        return _parse(path, data, digest)
    except Exception as e:  # never let one file crash the run
        return Skipped(path, digest, "PARSE_ERROR", f"{type(e).__name__}: {e}")


def _parse(path: Path, data: bytes, digest: str) -> Note | Skipped:
    if not data.strip():
        return Skipped(path, digest, "EMPTY_FILE", "file is empty")
    if b"\x00" in data or any(sig in data[:64] for sig in BINARY_SIGNATURES):
        return Skipped(path, digest, "NOT_TEXT", "binary content")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return Skipped(path, digest, "NOT_TEXT", "not valid UTF-8")

    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        return _parse_front_matter(path, digest, lines)
    return _parse_markdown_header(path, digest, lines)


def _parse_front_matter(path: Path, digest: str, lines: list[str]) -> Note | Skipped:
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        return Skipped(path, digest, "BAD_FRONT_MATTER", "front matter is not closed with ---")
    try:
        meta = yaml.safe_load("\n".join(lines[1:end])) or {}
    except yaml.YAMLError as e:
        return Skipped(path, digest, "BAD_FRONT_MATTER", str(e).splitlines()[0])
    if not isinstance(meta, dict):
        return Skipped(path, digest, "BAD_FRONT_MATTER", "front matter is not a mapping")
    meta = {str(k).lower(): v for k, v in meta.items()}

    raw_date = meta.get("date")
    if raw_date is None:
        return Skipped(path, digest, "MISSING_HEADER", "no date in front matter")
    warnings: list[Warning_] = []
    if isinstance(raw_date, datetime):
        meeting = raw_date.date()
    elif isinstance(raw_date, date):
        meeting = raw_date
    else:
        meeting, check = parse_absolute(str(raw_date))
        if meeting is None:
            return Skipped(path, digest, "BAD_DATE", f'"{raw_date}"')
        if check:
            warnings.append(_weekday_warning(check, None))

    att = meta.get("attendees") or []
    attendees = [str(a).strip() for a in att] if isinstance(att, list) else _split_attendees(str(att))
    if not attendees:
        warnings.append(Warning_("MISSING_ATTENDEES", "no attendees listed"))
    body = [(i + 1, lines[i]) for i in range(end + 1, len(lines))]
    title = str(meta["title"]).strip() if meta.get("title") else None
    return Note(path, digest, title, meeting, attendees, body, warnings)


def _parse_markdown_header(path: Path, digest: str, lines: list[str]) -> Note | Skipped:
    title = date_raw = att_raw = None
    date_line = None
    header_end = 0
    for i, line in enumerate(lines[:MAX_HEADER_SCAN]):
        if line.startswith("## "):
            break
        if title is None and (m := _TITLE.match(line)):
            title, header_end = m["title"], i + 1
        elif date_raw is None and (m := _DATE.match(line)):
            date_raw, date_line, header_end = m["v"], i + 1, i + 1
        elif att_raw is None and (m := _ATTENDEES.match(line)):
            att_raw, header_end = m["v"], i + 1

    if date_raw is None:
        what = "no title, no date" if title is None else "no date"
        return Skipped(path, digest, "MISSING_HEADER", what)
    meeting, check = parse_absolute(date_raw)
    if meeting is None:
        return Skipped(path, digest, "BAD_DATE", f'"{date_raw}"')

    warnings: list[Warning_] = []
    if check:
        warnings.append(_weekday_warning(check, date_line))
    attendees = _split_attendees(att_raw or "")
    if not attendees:
        warnings.append(Warning_("MISSING_ATTENDEES", "no attendees listed"))
    body = [(i + 1, lines[i]) for i in range(header_end, len(lines))]
    return Note(path, digest, title, meeting, attendees, body, warnings)


def _split_attendees(raw: str) -> list[str]:
    raw = raw.strip().strip("[]")
    return [a.strip() for a in re.split(r"[,;]", raw) if a.strip()]


def _weekday_warning(check, line_no) -> Warning_:
    return Warning_("WEEKDAY_MISMATCH", f"date says {check.stated} but it is a {check.actual}; using the date", line_no)
