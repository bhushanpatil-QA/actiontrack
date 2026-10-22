"""Owner phrase → OwnerResolution (SDD §6.3).

Same-first-name people are never guessed: if both namesakes attended the meeting the item
is 'ambiguous' and keeps the candidate list.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

GROUP_WORDS = {"everyone", "everybody", "team", "the team", "all", "whole team"}
PLACEHOLDER_WORDS = {"someone", "somebody", "tbd", "tba", "owner", "anyone", "nobody", "?", "unassigned", "n/a"}

_NAME = re.compile(r"^(?P<first>[A-Za-z][A-Za-z'\-]*)(?:\s+(?:(?P<initial>[A-Za-z])\.?|(?P<last>[A-Z][A-Za-z'\-]+)))?$")
_WITH = re.compile(r"\(\s*(?:with|w/|and|\+)\s+(?P<who>[^)]+)\)", re.IGNORECASE)
_HANDLE_LINE = re.compile(r"^@(?P<handle>[A-Za-z][\w.\-]*)\s*:?\s+(?P<task>.+)$")
_TO_LINE = re.compile(r"^(?P<owner>.{1,60}?)\s+to\s+(?P<task>.+)$")


@dataclass(frozen=True)
class Person:
    full_name: str

    @property
    def first(self) -> str:
        return self.full_name.split()[0]

    @property
    def last(self) -> str | None:
        parts = self.full_name.split()
        return parts[-1] if len(parts) > 1 else None


class Roster:
    def __init__(self, names: list[str] | None = None):
        self._people: dict[str, Person] = {}
        for n in names or []:
            self.add(n)

    def add(self, full_name: str) -> Person:
        full_name = " ".join(full_name.split())
        key = full_name.lower()
        if key not in self._people:
            self._people[key] = Person(full_name)
        return self._people[key]

    def get(self, full_name: str) -> Person | None:
        return self._people.get(" ".join(full_name.split()).lower())

    def all(self) -> list[Person]:
        return sorted(self._people.values(), key=lambda p: p.full_name)

    def by_first(self, first: str, pool: list[Person] | None = None) -> list[Person]:
        pool = self.all() if pool is None else pool
        return [p for p in pool if p.first.lower() == first.lower()]

    def match(self, query: str) -> list[Person]:
        """Match a user-typed name: full name, 'First L.', or first name."""
        q = " ".join(query.split())
        if exact := self.get(q):
            return [exact]
        m = _NAME.match(q)
        if not m:
            return []
        if m["last"]:
            return [p for p in self.all() if p.first.lower() == m["first"].lower()
                    and (p.last or "").lower() == m["last"].lower()]
        cands = self.by_first(m["first"])
        if m["initial"]:
            cands = [p for p in cands if (p.last or "").lower().startswith(m["initial"].lower())]
        return cands


@dataclass
class OwnerResolution:
    status: str                         # resolved | ambiguous | group | unassigned
    raw: str | None
    owners: list[Person] = field(default_factory=list)
    candidates: list[Person] = field(default_factory=list)
    collaborators: list[Person] = field(default_factory=list)
    warnings: list[tuple[str, str]] = field(default_factory=list)

    def signature(self) -> str:
        """Key used by dedup: two mentions can only merge when this is equal."""
        if self.status == "resolved":
            return "P:" + "|".join(sorted(p.full_name for p in self.owners))
        if self.status == "ambiguous":
            return ("A:" + (self.raw or "").lower() + ":" + "|".join(sorted(p.full_name for p in self.owners))
                    + ":" + "|".join(sorted(p.full_name for p in self.candidates)))
        return self.status[0].upper() + ":" + (self.raw or "").lower()

    def display(self) -> str:
        if self.status == "resolved":
            return " & ".join(p.full_name for p in self.owners)
        if self.status == "ambiguous":
            known = " & ".join(p.full_name for p in self.owners)
            cands = " | ".join(p.full_name for p in self.candidates)
            return (known + " & " if known else "") + f"{self.raw}? ({cands})"
        if self.status == "group":
            return f"[{self.raw}]"
        return "(unassigned)"


def split_owner(text: str) -> tuple[str | None, str]:
    """Split item text into (owner phrase, rest). Owner phrase is None when there is none."""
    if m := _HANDLE_LINE.match(text):
        return m["handle"], m["task"]
    if m := _TO_LINE.match(text):
        owner = m["owner"].strip()
        if _is_owner_phrase(owner):
            return owner, m["task"]
    return None, text


def _is_owner_phrase(owner: str) -> bool:
    low = owner.lower()
    if low in GROUP_WORDS or low in PLACEHOLDER_WORDS:
        return True
    owner = _WITH.sub("", owner).strip()
    parts = _split_names(owner)
    return bool(parts) and all(_NAME.match(p) and p[0].isupper() for p in parts)


def _split_names(owner: str) -> list[str]:
    return [p.strip() for p in re.split(r"\s*(?:&|\band\b|,|/|\+)\s*", owner) if p.strip()]


def resolve_owner(owner_phrase: str | None, attendees: list[Person], roster: Roster) -> OwnerResolution:
    if owner_phrase is None:
        return OwnerResolution("unassigned", None)
    raw = owner_phrase.strip()
    low = raw.lower()
    if low in GROUP_WORDS:
        return OwnerResolution("group", raw)
    if low in PLACEHOLDER_WORDS:
        return OwnerResolution("unassigned", raw)

    res = OwnerResolution("resolved", raw)
    collab_phrase = None
    if m := _WITH.search(raw):
        collab_phrase = m["who"]
        raw_main = _WITH.sub("", raw).strip()
    else:
        raw_main = raw

    for name in _split_names(raw_main):
        status, people, warns = _resolve_name(name, attendees, roster)
        res.warnings.extend(warns)
        if status == "resolved":
            res.owners.extend(people)
        else:
            res.status = "ambiguous"
            res.candidates.extend(people)
            res.raw = name if len(_split_names(raw_main)) == 1 else raw
    if collab_phrase:
        for name in _split_names(collab_phrase):
            status, people, _ = _resolve_name(name, attendees, roster)
            if status == "resolved":
                res.collaborators.extend(people)
    return res


def _resolve_name(name: str, attendees: list[Person], roster: Roster) -> tuple[str, list[Person], list[tuple[str, str]]]:
    m = _NAME.match(name)
    first = m["first"].capitalize() if m else name
    initial = m["initial"] if m else None
    last = m["last"] if m else None
    warns: list[tuple[str, str]] = []

    if last:
        p = roster.get(f"{first} {last}")
        if p:
            return "resolved", [p], warns
    if initial:
        pool = [p for p in roster.by_first(first) if (p.last or "").lower().startswith(initial.lower())]
        at = [p for p in pool if p in attendees]
        pick = at if len(at) == 1 else pool
        if len(pick) == 1:
            return "resolved", pick, warns
        if len(pick) > 1:
            warns.append(("AMBIGUOUS_OWNER", f'"{name}" → ' + " | ".join(p.full_name for p in pick)))
            return "ambiguous", pick, warns

    in_meeting = roster.by_first(first, attendees)
    if len(in_meeting) == 1:
        return "resolved", in_meeting, warns
    if len(in_meeting) > 1:
        warns.append(("AMBIGUOUS_OWNER", f'"{name}" → ' + " | ".join(p.full_name for p in in_meeting)))
        return "ambiguous", in_meeting, warns

    everywhere = roster.by_first(first)
    if len(everywhere) == 1:
        if attendees:
            warns.append(("OWNER_NOT_ATTENDEE", f"{everywhere[0].full_name} is not in this meeting's attendees"))
        return "resolved", everywhere, warns
    if len(everywhere) > 1:
        warns.append(("AMBIGUOUS_OWNER", f'"{name}" → ' + " | ".join(p.full_name for p in everywhere)))
        return "ambiguous", everywhere, warns

    person = roster.add(first if not last else f"{first} {last}")
    warns.append(("UNKNOWN_PERSON", f'"{name}" is not in any attendee list'))
    return "resolved", [person], warns
