from datetime import date

import pytest

from actiontrack.resolve.dates import find_due, parse_absolute, resolve_due

D = date.fromisoformat


@pytest.mark.parametrize("meeting, text, expected", [
    ("2026-09-01", "send the revised quote to Acme by Friday", "2026-09-04"),        # Tue → Fri
    ("2026-09-01", "share first wireframes by Thursday", "2026-09-03"),
    ("2026-09-18", "audit colour contrast on the dashboard by Mon", "2026-09-21"),   # Fri → next Mon
    ("2026-09-03", "draft the test plan for invoice rounding by end of next week", "2026-09-11"),
    ("2026-09-03", "export last quarter invoices by 10 Sep", "2026-09-10"),
    ("2026-09-14", "collect 5 customer quotes for the landing page by end of month", "2026-09-30"),
    ("2026-09-14", "finalise the welcome-email copy by Wed 16 Sep", "2026-09-16"),
    ("2026-09-21", "write the postmortem by EOD tomorrow", "2026-09-22"),
    ("2026-10-08", "circulate the go/no-go summary by EOD", "2026-10-08"),
    ("2026-09-10", "pull Acme's usage report for the last 6 months by tomorrow", "2026-09-11"),
    ("2026-09-30", "propose the on-call rotation by mid-October", "2026-10-15"),
    ("2026-09-30", "finalise the Q4 sales targets by Oct 3rd", "2026-10-03"),
    ("2026-09-30", "run the customer survey, results due 15 October", "2026-10-15"),
    ("2026-09-30", "submit their Q4 goals by 7 Oct", "2026-10-07"),
    ("2026-10-05", "start SSO spike by next Tuesday", "2026-10-13"),
    ("2026-10-02", "cut cloud costs by 10% — proposal by 2026-10-09", "2026-10-09"),
    ("2026-09-29", "freeze invoice edits from 9 Oct", "2026-10-09"),
    ("2026-09-29", "schedule cutover for Saturday 10 Oct", "2026-10-10"),
    ("2026-10-06", "demo the portal to Acme on 2026-10-14", "2026-10-14"),
    ("2026-12-15", "send the plan by 5 Jan", "2027-01-05"),                         # year rollover
])
def test_due_resolution(meeting, text, expected):
    due = find_due(text, D(meeting))
    assert due is not None and due.date == D(expected)


@pytest.mark.parametrize("text, raw", [
    ("snapshot the production DB before cutover", "before cutover"),
    ("review the prototype before the next review", "before the next review"),
    ("schedule the quarterly business review for November", "for November"),
])
def test_unresolved_due_is_kept_raw(text, raw):
    due = find_due(text, D("2026-09-03"))
    assert due is not None and due.date is None and due.raw == raw


@pytest.mark.parametrize("text", [
    "share the VPN guide",
    "review the welcome email for accessibility",
    "audit colour contrast on the dashboard",
    "cut cloud costs by 10%",
])
def test_no_due(text):
    assert find_due(text, D("2026-09-03")) is None


def test_weekday_strictly_after_meeting():
    assert resolve_due("Friday", D("2026-10-09")) == D("2026-10-16")  # meeting is a Friday


@pytest.mark.parametrize("text, expected", [
    ("2026-09-01", "2026-09-01"), ("9 September 2026", "2026-09-09"),
    ("Monday, 14 September 2026", "2026-09-14"), ("Sep 14, 2026", "2026-09-14"),
])
def test_meeting_dates(text, expected):
    assert parse_absolute(text)[0] == D(expected)


def test_meeting_date_garbage():
    assert parse_absolute("sometime last week")[0] is None


def test_weekday_mismatch_detected():
    d, check = parse_absolute("Tuesday, 14 September 2026")
    assert d == D("2026-09-14") and check is not None
