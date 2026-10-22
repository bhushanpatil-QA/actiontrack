"""Build the follow-up email for one owner (SDD §8.5)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from actiontrack.store import ItemRow


@dataclass
class Email:
    person: str
    subject: str
    body: str
    item_ids: list[int]
    overdue: int
    open: int
    confirm: int


def _fmt(d: str | None) -> str:
    if not d:
        return ""
    x = date.fromisoformat(d)
    return f"{x.day} {x.strftime('%b')}"  # no %-d: it does not work on Windows


def _context(it: ItemRow) -> str:
    when = _fmt(it.last_meeting)
    title = it.last_title or "meeting"
    return f"({title}, {when})" if when else f"({title})"


def _line(it: ItemRow, today: date, with_due: bool = True) -> str:
    due = ""
    if with_due and it.due_date:
        due = f" — due {_fmt(it.due_date)}"
        if it.is_overdue(today):
            n = it.days_overdue(today)
            due += f", {n} day{'s' if n != 1 else ''} overdue"
    elif it.due_raw:
        due = f" — {it.due_raw}"
    return f"  • {it.task}{due} {_context(it)}"


def compose(person: str, items: list[ItemRow], today: date, sender: str) -> Email | None:
    mine = [i for i in items if i.status == "open" and person in i.owners and i.owner_status == "resolved"]
    confirm = [i for i in items if i.status == "open" and person in i.candidates]
    if not mine and not confirm:
        return None
    key = lambda i: (i.due_date or "9999-99-99", i.id)
    overdue = sorted([i for i in mine if i.is_overdue(today)], key=key)
    dated = sorted([i for i in mine if not i.is_overdue(today) and i.due_date], key=key)
    undated = sorted([i for i in mine if not i.due_date], key=lambda i: i.id)
    confirm = sorted(confirm, key=key)

    first = person.split()[0]
    parts = [f"Hi {first},", "", "Here's a quick roundup of your action items from recent meetings.", ""]
    if overdue:
        parts += ["Overdue"] + [_line(i, today) for i in overdue] + [""]
    if dated:
        parts += ["Open"] + [_line(i, today) for i in dated] + [""]
    if undated:
        parts += ["No due date yet"] + [_line(i, today) for i in undated] + [""]
    if confirm:
        parts += ["Please confirm — is this yours?",
                  "  (the notes only say the first name, and more than one person with that name was there)"]
        parts += [_line(i, today) for i in confirm] + [""]
    parts += ["Could you reply with an update, or a new date for anything that has slipped?", "",
              "Thanks,", sender]

    open_count = len(dated) + len(undated)
    bits = []
    if overdue:
        bits.append(f"{len(overdue)} overdue")
    if open_count:
        bits.append(f"{open_count} open")
    if confirm and not bits:
        bits.append(f"{len(confirm)} to confirm")
    subject = f"Action items follow-up — {', '.join(bits)} (as of {today.day} {today.strftime('%b %Y')})"
    ids = [i.id for i in overdue + dated + undated + confirm]
    return Email(person, subject, "\n".join(parts), ids, len(overdue), open_count, len(confirm))
