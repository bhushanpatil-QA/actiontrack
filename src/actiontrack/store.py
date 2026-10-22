"""SQLite persistence (SDD §7)."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
  id            INTEGER PRIMARY KEY,
  path          TEXT UNIQUE NOT NULL,
  sha256        TEXT,
  title         TEXT,
  meeting_date  TEXT,
  state         TEXT NOT NULL CHECK (state IN ('ingested','skipped')),
  skip_reason   TEXT,
  ingested_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS people (
  id          INTEGER PRIMARY KEY,
  full_name   TEXT UNIQUE NOT NULL,
  first_name  TEXT NOT NULL,
  last_name   TEXT,
  email       TEXT
);
CREATE TABLE IF NOT EXISTS items (
  id             INTEGER PRIMARY KEY,
  task           TEXT NOT NULL,
  task_key       TEXT NOT NULL,
  owner_status   TEXT NOT NULL CHECK (owner_status IN ('resolved','ambiguous','group','unassigned')),
  owner_raw      TEXT,
  owner_sig      TEXT NOT NULL,
  due_date       TEXT,
  due_raw        TEXT,
  status         TEXT NOT NULL CHECK (status IN ('open','done')),
  done_source    TEXT CHECK (done_source IN ('note','manual')),
  done_at        TEXT,
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS item_people (
  item_id   INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  person_id INTEGER NOT NULL REFERENCES people(id),
  role      TEXT NOT NULL CHECK (role IN ('owner','candidate','collaborator')),
  PRIMARY KEY (item_id, person_id, role)
);
CREATE TABLE IF NOT EXISTS mentions (
  id           INTEGER PRIMARY KEY,
  item_id      INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  note_id      INTEGER NOT NULL REFERENCES notes(id),
  line_no      INTEGER NOT NULL,
  parent_line  INTEGER,
  raw_line     TEXT NOT NULL,
  task         TEXT NOT NULL,
  task_key     TEXT NOT NULL,
  checked      INTEGER NOT NULL,
  due_resolved TEXT,
  due_raw      TEXT,
  meeting_date TEXT NOT NULL,
  UNIQUE (note_id, line_no)
);
CREATE TABLE IF NOT EXISTS warnings (
  id INTEGER PRIMARY KEY, run_id TEXT NOT NULL, path TEXT, line_no INTEGER,
  code TEXT NOT NULL, message TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS followups (
  id INTEGER PRIMARY KEY, person_id INTEGER, draft_id TEXT, item_ids TEXT,
  created_on TEXT NOT NULL, created_at TEXT NOT NULL
);
"""


@dataclass
class ItemRow:
    id: int
    task: str
    task_key: str
    owner_status: str
    owner_raw: str | None
    owner_sig: str
    due_date: str | None
    due_raw: str | None
    status: str
    done_source: str | None
    done_at: str | None
    owners: list[str] = field(default_factory=list)
    candidates: list[str] = field(default_factory=list)
    collaborators: list[str] = field(default_factory=list)
    first_source: str | None = None
    last_source: str | None = None
    last_title: str | None = None
    last_meeting: str | None = None
    mention_count: int = 0

    def is_overdue(self, today: date) -> bool:
        return self.status == "open" and self.due_date is not None and self.due_date < today.isoformat()

    def days_overdue(self, today: date) -> int:
        return (today - date.fromisoformat(self.due_date)).days if self.is_overdue(today) else 0

    def owner_display(self) -> str:
        if self.owner_status == "resolved":
            return " & ".join(self.owners)
        if self.owner_status == "ambiguous":
            known = " & ".join(self.owners)
            return (known + " & " if known else "") + f"{self.owner_raw}? ({' | '.join(self.candidates)})"
        if self.owner_status == "group":
            return f"[{self.owner_raw}]"
        return "(unassigned)"

    def to_dict(self, today: date) -> dict:
        return {
            "id": self.id, "task": self.task, "owner": self.owner_display(),
            "owner_status": self.owner_status, "owners": self.owners, "candidates": self.candidates,
            "collaborators": self.collaborators, "due_date": self.due_date, "due_raw": self.due_raw,
            "status": "overdue" if self.is_overdue(today) else self.status,
            "days_overdue": self.days_overdue(today), "done_source": self.done_source,
            "first_source": self.first_source, "last_source": self.last_source,
            "mentions": self.mention_count,
        }


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(SCHEMA)

    def close(self):
        self.db.commit()
        self.db.close()

    def commit(self):
        self.db.commit()

    # ----------------------------------------------------------------- people
    def upsert_person(self, full_name: str, email: str | None = None) -> int:
        parts = full_name.split()
        first, last = parts[0], (parts[-1] if len(parts) > 1 else None)
        row = self.db.execute("SELECT id, email FROM people WHERE lower(full_name) = lower(?)", (full_name,)).fetchone()
        if row:
            if email and row["email"] != email:
                self.db.execute("UPDATE people SET email = ? WHERE id = ?", (email, row["id"]))
            return row["id"]
        cur = self.db.execute("INSERT INTO people (full_name, first_name, last_name, email) VALUES (?,?,?,?)",
                              (full_name, first, last, email))
        return cur.lastrowid

    def person_id(self, full_name: str) -> int | None:
        row = self.db.execute("SELECT id FROM people WHERE lower(full_name) = lower(?)", (full_name,)).fetchone()
        return row["id"] if row else None

    def people_names(self) -> list[str]:
        return [r["full_name"] for r in self.db.execute("SELECT full_name FROM people ORDER BY full_name")]

    def person_email(self, full_name: str) -> str | None:
        row = self.db.execute("SELECT email FROM people WHERE lower(full_name) = lower(?)", (full_name,)).fetchone()
        return row["email"] if row else None

    # ----------------------------------------------------------------- notes
    def note(self, path: str):
        return self.db.execute("SELECT * FROM notes WHERE path = ?", (path,)).fetchone()

    def upsert_note(self, path: str, sha: str | None, title: str | None, meeting: str | None,
                    state: str, reason: str | None) -> int:
        row = self.note(path)
        if row:
            self.db.execute("UPDATE notes SET sha256=?, title=?, meeting_date=?, state=?, skip_reason=?, ingested_at=? "
                            "WHERE id=?", (sha, title, meeting, state, reason, now_iso(), row["id"]))
            return row["id"]
        cur = self.db.execute("INSERT INTO notes (path, sha256, title, meeting_date, state, skip_reason, ingested_at) "
                              "VALUES (?,?,?,?,?,?,?)", (path, sha, title, meeting, state, reason, now_iso()))
        return cur.lastrowid

    def delete_mentions_of_note(self, note_id: int) -> set[int]:
        items = {r["item_id"] for r in self.db.execute("SELECT item_id FROM mentions WHERE note_id=?", (note_id,))}
        self.db.execute("DELETE FROM mentions WHERE note_id=?", (note_id,))
        return items

    # ----------------------------------------------------------------- items
    def items_for_matching(self) -> list[dict]:
        rows = self.db.execute("SELECT id, owner_sig, task_key FROM items ORDER BY id").fetchall()
        out = []
        for r in rows:
            keys = {m["task_key"] for m in self.db.execute("SELECT task_key FROM mentions WHERE item_id=?", (r["id"],))}
            keys.add(r["task_key"])
            out.append({"id": r["id"], "sig": r["owner_sig"], "keys": keys})
        return out

    def create_item(self, task: str, key: str, owner_status: str, owner_raw: str | None, sig: str) -> int:
        ts = now_iso()
        cur = self.db.execute(
            "INSERT INTO items (task, task_key, owner_status, owner_raw, owner_sig, status, created_at, updated_at) "
            "VALUES (?,?,?,?,?, 'open', ?, ?)", (task, key, owner_status, owner_raw, sig, ts, ts))
        return cur.lastrowid

    def add_mention(self, item_id: int, note_id: int, line_no: int, parent_line: int | None, raw_line: str,
                    task: str, key: str, checked: bool, due: str | None, due_raw: str | None, meeting: str):
        self.db.execute(
            "INSERT INTO mentions (item_id, note_id, line_no, parent_line, raw_line, task, task_key, checked, "
            "due_resolved, due_raw, meeting_date) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (item_id, note_id, line_no, parent_line, raw_line, task, key, int(checked), due, due_raw, meeting))

    def mention_exists(self, note_id: int, line_no: int) -> bool:
        return self.db.execute("SELECT 1 FROM mentions WHERE note_id=? AND line_no=?", (note_id, line_no)).fetchone() is not None

    def mention_item(self, note_id: int, line_no: int) -> int | None:
        row = self.db.execute("SELECT item_id FROM mentions WHERE note_id=? AND line_no=?", (note_id, line_no)).fetchone()
        return row["item_id"] if row else None

    def mentions_of(self, item_id: int) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT m.*, n.path, n.title FROM mentions m JOIN notes n ON n.id = m.note_id "
            "WHERE m.item_id = ? ORDER BY m.meeting_date, n.path, m.line_no", (item_id,)).fetchall()

    def item_raw(self, item_id: int):
        return self.db.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()

    def update_item(self, item_id: int, **fields):
        fields["updated_at"] = now_iso()
        cols = ", ".join(f"{k}=?" for k in fields)
        self.db.execute(f"UPDATE items SET {cols} WHERE id=?", (*fields.values(), item_id))

    def set_item_people(self, item_id: int, role: str, names: list[str]):
        self.db.execute("DELETE FROM item_people WHERE item_id=? AND role=?", (item_id, role))
        for n in names:
            pid = self.upsert_person(n)
            self.db.execute("INSERT OR IGNORE INTO item_people (item_id, person_id, role) VALUES (?,?,?)",
                            (item_id, pid, role))

    def delete_item(self, item_id: int):
        self.db.execute("DELETE FROM item_people WHERE item_id=?", (item_id,))
        self.db.execute("DELETE FROM items WHERE id=?", (item_id,))

    def mark_done_manual(self, item_id: int) -> str:
        row = self.item_raw(item_id)
        if row is None:
            return "missing"
        if row["status"] == "done":
            return "already"
        self.update_item(item_id, status="done", done_source="manual", done_at=now_iso())
        return "done"

    def load_items(self) -> list[ItemRow]:
        items = []
        for r in self.db.execute("SELECT * FROM items ORDER BY id").fetchall():
            it = ItemRow(r["id"], r["task"], r["task_key"], r["owner_status"], r["owner_raw"], r["owner_sig"],
                         r["due_date"], r["due_raw"], r["status"], r["done_source"], r["done_at"])
            for p in self.db.execute(
                    "SELECT p.full_name, ip.role FROM item_people ip JOIN people p ON p.id = ip.person_id "
                    "WHERE ip.item_id = ? ORDER BY p.full_name", (r["id"],)):
                {"owner": it.owners, "candidate": it.candidates, "collaborator": it.collaborators}[p["role"]].append(p["full_name"])
            ms = self.mentions_of(r["id"])
            if ms:
                it.first_source = f"{Path(ms[0]['path']).name}:{ms[0]['line_no']}"
                it.last_source = f"{Path(ms[-1]['path']).name}:{ms[-1]['line_no']}"
                it.last_title = ms[-1]["title"]
                it.last_meeting = ms[-1]["meeting_date"]
            it.mention_count = len(ms)
            items.append(it)
        return items

    # ----------------------------------------------------------------- warnings / followups
    def add_warning(self, run_id: str, path: str | None, line_no: int | None, code: str, message: str):
        self.db.execute("INSERT INTO warnings (run_id, path, line_no, code, message) VALUES (?,?,?,?,?)",
                        (run_id, path, line_no, code, message))

    def followup_today(self, person: str, today: date) -> bool:
        pid = self.person_id(person)
        return self.db.execute("SELECT 1 FROM followups WHERE person_id=? AND created_on=?",
                               (pid, today.isoformat())).fetchone() is not None

    def record_followup(self, person: str, draft_id: str, item_ids: list[int], today: date):
        self.db.execute("INSERT INTO followups (person_id, draft_id, item_ids, created_on, created_at) VALUES (?,?,?,?,?)",
                        (self.person_id(person), draft_id, json.dumps(item_ids), today.isoformat(), now_iso()))
        self.db.commit()
