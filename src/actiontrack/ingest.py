"""`actiontrack ingest` orchestration: read → extract → resolve → dedup → store (SDD §4.1)."""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from actiontrack.dedup import similar, task_key
from actiontrack.notes.extract import extract
from actiontrack.notes.reader import Note, Skipped, read_note
from actiontrack.resolve.dates import find_due
from actiontrack.resolve.owners import OwnerResolution, Roster, resolve_owner, split_owner
from actiontrack.store import Store

_TASK_TRAIL = re.compile(r"[\s,;:.\-–—]+$")


@dataclass
class IngestReport:
    folder: str
    scanned: int = 0
    ingested: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str, str]] = field(default_factory=list)   # (file, code, detail)
    ignored: list[str] = field(default_factory=list)
    items_new: int = 0
    mentions_merged: int = 0
    closed_by_notes: int = 0
    items_total: int = 0
    warnings: list[tuple[str, int | None, str, str]] = field(default_factory=list)  # (file, line, code, msg)

    def to_dict(self) -> dict:
        return {
            "folder": self.folder, "scanned": self.scanned,
            "ingested": len(self.ingested) + len(self.unchanged), "changed_files": self.ingested,
            "unchanged_files": self.unchanged,
            "skipped": [{"file": f, "reason": c, "detail": d} for f, c, d in self.skipped],
            "ignored": self.ignored,
            "items": {"new": self.items_new, "merged_mentions": self.mentions_merged,
                      "closed_by_notes": self.closed_by_notes, "total": self.items_total},
            "warnings": [{"file": f, "line": ln, "code": c, "message": m} for f, ln, c, m in self.warnings],
        }


@dataclass
class _Mention:
    note: Note
    note_id: int
    line_no: int
    parent_line: int | None
    raw_line: str
    task: str
    key: str
    checked: bool
    due: str | None
    due_raw: str | None
    owner: OwnerResolution
    new: bool


def ingest(folder: Path, store: Store, recursive: bool = False, people: dict[str, str | None] | None = None) -> IngestReport:
    report = IngestReport(str(folder))
    run_id = uuid.uuid4().hex[:12]
    files = sorted(p for p in (folder.rglob("*") if recursive else folder.iterdir()) if p.is_file())
    report.scanned = len(files)

    notes: list[Note] = []
    for p in files:
        if p.suffix.lower() != ".md":
            report.ignored.append(_rel(p, folder))
            continue
        res = read_note(p)
        if isinstance(res, Skipped):
            name = _rel(p, folder)
            report.skipped.append((name, res.reason, res.detail))
            prev = store.note(str(p.resolve()))
            if prev and prev["state"] == "ingested":
                report.warnings.append((name, None, "STALE_SOURCE",
                                        "previously ingested; keeping its items from the last good version"))
            else:
                store.upsert_note(str(p.resolve()), res.sha256, None, None, "skipped", f"{res.reason}: {res.detail}")
            continue
        notes.append(res)

    # Roster = people already known + people.toml + every attendee in this folder.
    roster = Roster(store.people_names())
    for name, email in (people or {}).items():
        roster.add(name)
        store.upsert_person(name, email)
    for n in notes:
        for a in n.attendees:
            roster.add(a)

    notes.sort(key=lambda n: (n.meeting_date, n.path.name))
    mentions: list[_Mention] = []
    for note in notes:
        name = _rel(note.path, folder)
        for w in note.warnings:
            report.warnings.append((name, w.line_no, w.code, w.message))
        prev = store.note(str(note.path.resolve()))
        unchanged = bool(prev and prev["state"] == "ingested" and prev["sha256"] == note.sha256)
        if prev and not unchanged:
            store.delete_mentions_of_note(prev["id"])
        note_id = store.upsert_note(str(note.path.resolve()), note.sha256, note.title,
                                    note.meeting_date.isoformat(), "ingested", None)
        (report.unchanged if unchanged else report.ingested).append(name)

        raw, warns = extract(note)
        for w in warns:
            report.warnings.append((name, w.line_no, w.code, w.message))
        attendees = [roster.add(a) for a in note.attendees]
        for rm in raw:
            owner_phrase, rest = split_owner(rm.text)
            owner = resolve_owner(owner_phrase, attendees, roster)
            for code, msg in owner.warnings:
                report.warnings.append((name, rm.line_no, code, msg))
            due = find_due(rest, note.meeting_date)
            task = rest[:due.start] if due else rest
            task = _TASK_TRAIL.sub("", task).strip() or rest.strip()
            if due and due.date is None:
                report.warnings.append((name, rm.line_no, "UNRESOLVED_DUE", f'"{due.raw}" is not a calendar date'))
            mentions.append(_Mention(
                note, note_id, rm.line_no, rm.parent_line, rm.raw_line, task[:1].upper() + task[1:], task_key(task),
                rm.checked, due.date.isoformat() if due and due.date else None, due.raw if due else None,
                owner, new=not (unchanged and store.mention_exists(note_id, rm.line_no))))

    for p in roster.all():
        store.upsert_person(p.full_name)

    # Attach new mentions to items (dedup), in meeting order.
    candidates = store.items_for_matching()
    touched: set[int] = set()
    latest_owner: dict[int, OwnerResolution] = {}
    new_unchecked: dict[int, list[_Mention]] = {}
    for m in mentions:
        sig = m.owner.signature()
        if not m.new:
            existing_id = store.mention_item(m.note_id, m.line_no)
            if existing_id is not None:
                latest_owner[existing_id] = m.owner
            continue
        match = next((c for c in candidates if c["sig"] == sig and any(similar(m.key, k) for k in c["keys"])), None)
        if m.new:
            if match is None:
                item_id = store.create_item(m.task, m.key, m.owner.status, m.owner.raw, sig)
                match = {"id": item_id, "sig": sig, "keys": {m.key}}
                candidates.append(match)
                report.items_new += 1
            else:
                match["keys"].add(m.key)
                report.mentions_merged += 1
            store.add_mention(match["id"], m.note_id, m.line_no, m.parent_line, m.raw_line, m.task, m.key,
                              m.checked, m.due, m.due_raw, m.note.meeting_date.isoformat())
            touched.add(match["id"])
            if not m.checked:
                new_unchecked.setdefault(match["id"], []).append(m)
        latest_owner[match["id"]] = m.owner

    # Items whose note changed may have lost mentions.
    for it in store.items_for_matching():
        if not store.mentions_of(it["id"]):
            store.delete_item(it["id"])
            touched.discard(it["id"])

    for item_id in sorted(touched | set(latest_owner)):
        _recompute(store, item_id, latest_owner.get(item_id), new_unchecked.get(item_id, []), report, folder)

    report.items_total = len(store.load_items())
    for f, ln, code, msg in report.warnings:
        store.add_warning(run_id, f, ln, code, msg)
    store.commit()
    return report


def _recompute(store: Store, item_id: int, owner: OwnerResolution | None, new_unchecked: list[_Mention],
               report: IngestReport, folder: Path):
    row = store.item_raw(item_id)
    ms = store.mentions_of(item_id)
    if row is None or not ms:
        return
    latest = ms[-1]
    with_due = [m for m in ms if m["due_resolved"]]
    with_raw = [m for m in ms if m["due_raw"]]
    due = with_due[-1]["due_resolved"] if with_due else None
    due_raw = with_due[-1]["due_raw"] if with_due else (with_raw[-1]["due_raw"] if with_raw else None)

    fields = dict(task=latest["task"], task_key=latest["task_key"], due_date=due, due_raw=due_raw)
    if row["done_source"] == "manual":
        for m in new_unchecked:
            report.warnings.append((_rel(m.note.path, folder), m.line_no, "REOPEN_CONFLICT",
                                    f"item #{item_id} was marked done manually; note lists it as open — keeping it done"))
    elif latest["checked"]:
        if row["status"] != "done":
            report.closed_by_notes += 1
        fields.update(status="done", done_source="note", done_at=latest["meeting_date"])
    else:
        fields.update(status="open", done_source=None, done_at=None)

    if owner is not None:
        fields.update(owner_status=owner.status, owner_raw=owner.raw, owner_sig=owner.signature())
        store.set_item_people(item_id, "owner", [p.full_name for p in owner.owners])
        store.set_item_people(item_id, "candidate", [p.full_name for p in owner.candidates])
        store.set_item_people(item_id, "collaborator", [p.full_name for p in owner.collaborators])
    store.update_item(item_id, **fields)


def _rel(p: Path, folder: Path) -> str:
    try:
        return str(p.relative_to(folder))
    except ValueError:
        return p.name
