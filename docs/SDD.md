# actiontrack — Spec-Driven Design (SDD)

| Field | Value |
|---|---|
| Product | `actiontrack` — CLI that turns meeting notes into tracked action items and Gmail follow-up drafts |
| Version | 1.0 (spec baseline) |
| Author | Bhushan Patil |
| Date | 2026-10-22 |
| Status | Implemented (v1.0) — 75 tests passing |
| Target stack | Python 3.11+, SQLite, `mcp` Python SDK (Gmail connector), pytest |
| Sample data | `notes/` — 36 files (35 `.md`, 1 `.txt`), 2026-09-01 → 2026-10-14 |

---

## 1. Problem and goals

Meeting notes pile up as markdown files. Action items get lost in them, and follow-up emails go out late or never.

**Goals**

1. Pull every action item out of a folder of notes, reliably, even when notes are messy.
2. Keep one tracked record per real-world task, even when the task is repeated across several meetings.
3. Show who owns what, what is due, and what is overdue.
4. Draft (never send) a follow-up email per owner in Gmail, for a human to review.

**Non-goals (v1)**

- Sending email, or any write to Gmail other than creating a draft.
- Syncing with Jira, Asana, or calendars.
- NLP/LLM extraction of action items from free prose. v1 reads explicit checkbox lines only (see §6.2).
- Multi-user / server deployment. This is a local, single-user CLI.

---

## 2. Glossary

| Term | Meaning |
|---|---|
| **Note** | One `.md` file describing one meeting. |
| **Meeting date** | The `Date:` of the note. The anchor for resolving relative dates ("by Friday"). |
| **Mention** | One checkbox line in one note. |
| **Item** | A tracked action item. One item can have many mentions (deduplicated). |
| **Owner** | The person responsible for an item. Can be resolved, ambiguous, group, or unassigned. |
| **Roster** | Every person known from `Attendees` lines across all notes, plus `people.toml`. |
| **Overdue** | Derived state: status is `open` and `due_date < today`. Never stored. |
| **Today** | System date, or `--today YYYY-MM-DD` / `ACTIONTRACK_TODAY` for repeatable tests. |

---

## 3. Users and user stories

| ID | As a… | I want to… | So that… |
|---|---|---|---|
| US-1 | Team lead | ingest a whole notes folder in one command | I don't read 30 files by hand |
| US-2 | Team lead | see open/overdue items, filtered by owner or date | I know what to chase |
| US-3 | Item owner | mark my item done | the list stays accurate |
| US-4 | Team lead | get a ready-to-review follow-up draft per owner in Gmail | follow-ups go out on time and I still control what is sent |
| US-5 | Team lead | get a clear report of skipped files and doubtful items | nothing is silently lost or silently wrong |

---

## 4. Functional requirements

### 4.1 `actiontrack ingest <folder>`

| ID | Requirement |
|---|---|
| FR-1.1 | Scan `<folder>` (non-recursive by default; `--recursive` optional) for files ending in `.md` (case-insensitive). Other files are listed as *ignored*, not as errors. |
| FR-1.2 | Parse each note's header: title, meeting date, attendees (see §6.1). |
| FR-1.3 | Extract action items from checkbox lines (see §6.2). |
| FR-1.4 | For every item, store: owner, task text, due date (resolved to a real `YYYY-MM-DD`), raw due phrase, source file + line number, status (`open`/`done`). |
| FR-1.5 | Resolve owners against the meeting's attendees, then the global roster (§6.3). |
| FR-1.6 | Resolve relative due dates against the meeting date (§6.4). |
| FR-1.7 | Merge duplicate items repeated across meetings into one item (§6.5). |
| FR-1.8 | A malformed note is **skipped and reported**; ingest never crashes because of note content (§6.6). |
| FR-1.9 | Ingest is idempotent: running it twice on unchanged files changes nothing and creates no duplicates. Unchanged files are detected by SHA-256. |
| FR-1.10 | Print an ingest report: files scanned / ingested / skipped (with reason) / ignored, items new / merged / status-changed, and warnings. `--json` gives the same report as JSON. |
| FR-1.11 | Exit code `0` when the run completes, even if some notes were skipped. Non-zero only for usage or system errors (§8.4). |

### 4.2 `actiontrack list`

| ID | Requirement |
|---|---|
| FR-2.1 | Show items as a table: `ID`, `Owner`, `Task`, `Due`, `Status`, `Source`. |
| FR-2.2 | `--owner <name>` accepts full name ("Priya Shah"), first name if unique ("Karan"), or first name + initial ("Priya S."). If a first name matches more than one person, exit with code 5 and list the candidates. |
| FR-2.3 | `--status open\|done\|overdue`. `open` includes overdue items; `overdue` = open and due before today. Default: `open`. `--status all` shows everything. |
| FR-2.4 | `--due-before <YYYY-MM-DD>`: items with a due date strictly before that date. Items with no due date are excluded by this filter. |
| FR-2.5 | Overdue items are clearly marked: status shows `OVERDUE (n days)`, and in a TTY the row is red. Plain-text marker is always present so it works without colour. |
| FR-2.6 | Filters combine with AND. Default sort: due date ascending, items without a due date last, then ID. |
| FR-2.7 | `--unassigned` and `--ambiguous` show items that need a human decision. `--json` for machine-readable output. |

### 4.3 `actiontrack done <id>`

| ID | Requirement |
|---|---|
| FR-3.1 | Set the item's status to `done`, with `done_at` = now and `done_source = manual`. |
| FR-3.2 | Already done → print a notice, exit `0` (idempotent). Unknown ID → exit `4`. |
| FR-3.3 | A manual `done` is sticky: re-ingesting notes never re-opens it. If a later note lists the same item as `[ ]` again, report a warning `REOPEN_CONFLICT` and keep it done. |

### 4.4 `actiontrack followup <owner>`

| ID | Requirement |
|---|---|
| FR-4.1 | Build one email for the owner covering their **overdue** and **open** items (done items excluded). `followup --all` does this once per owner who has open items. |
| FR-4.2 | Create the email as a **Gmail draft through the Gmail MCP connector**. The tool must **never send**. |
| FR-4.3 | Recipient address comes from `people.toml`. If the owner has no address, stop for that owner with a clear message unless `--to <email>` is given. |
| FR-4.4 | Items where this owner is one of several ambiguous candidates appear in a separate "Please confirm — is this yours?" section. |
| FR-4.5 | Owner with nothing open → print "nothing to follow up", create no draft. |
| FR-4.6 | `--dry-run` prints the email and creates nothing. `--emit-json` prints the draft payload so an agent (the Claude skill) can create the draft itself. |
| FR-4.7 | Record each created draft (owner, draft ID, item IDs, time). A second draft for the same owner on the same day needs `--force`. |
| FR-4.8 | The ambiguous-name rule of FR-2.2 applies to `<owner>`. |

---

## 5. Architecture

```
            ┌──────────────────────── CLI (argparse) ────────────────────────┐
            │  ingest        list          done          followup             │
            └────┬────────────┬─────────────┬──────────────┬──────────────────┘
                 │            │             │              │
        ┌────────▼──────┐     │             │      ┌───────▼────────┐
        │ NoteReader    │     │             │      │ FollowupComposer│
        │ (header, body)│     │             │      └───────┬────────┘
        └────────┬──────┘     │             │              │
        ┌────────▼──────┐     │             │      ┌───────▼────────────┐
        │ ItemExtractor │     │             │      │ DraftGateway        │
        └────────┬──────┘     │             │      │  ├ McpGmailGateway  │──► Gmail MCP server
   ┌─────────────┼──────┐     │             │      │  ├ EmitJsonGateway  │    (create_draft only)
   ▼             ▼      ▼     │             │      │  └ DryRunGateway    │
OwnerResolver DueDateResolver Deduplicator  │      └─────────────────────┘
        └─────────────┬───────┘             │
                ┌─────▼─────────────────────▼─────┐
                │ Store (SQLite, single file)     │
                └─────────────────────────────────┘
```

Each box is a plain module with pure functions where possible. Parsing and resolving never touch the database or network, so they are unit-testable with just strings and a date.

### 5.1 Project layout

```
actiontrack/
├── pyproject.toml                 # console_scripts: actiontrack = actiontrack.cli:main
├── people.example.toml
├── src/actiontrack/
│   ├── cli.py                     # argparse, exit codes, output formatting
│   ├── config.py                  # paths, --today, people.toml, MCP settings
│   ├── notes/reader.py            # file → Note(header, lines) or SkipReason
│   ├── notes/extract.py           # Note → [Mention]
│   ├── resolve/owners.py          # owner phrase → OwnerResolution
│   ├── resolve/dates.py           # due phrase + meeting date → date | None
│   ├── dedup.py                   # task normalisation + duplicate matching
│   ├── ingest.py                  # orchestration + ingest report (text/JSON)
│   ├── store.py                   # SQLite schema + queries
│   └── followup/
│       ├── compose.py             # items → subject + body
│       └── gateway.py             # DraftGateway + MCP / emit / dry-run
├── tests/
│   ├── fixtures/notes/            # copy of the sample notes folder
│   ├── test_reader_extract.py  test_owners.py  test_dates.py
│   ├── test_cli.py                # acceptance tests AT-01 … AT-18
│   └── fake_gmail_mcp.py          # fake Gmail MCP server (also offers send_email, never called)
├── .claude/skills/actiontrack/SKILL.md
└── docs/SDD.md                    # this document
```

### 5.2 Configuration

| Setting | Default | Override |
|---|---|---|
| Database | `./.actiontrack/actiontrack.db` | `--db`, `ACTIONTRACK_DB` |
| Today | system date | `--today`, `ACTIONTRACK_TODAY` |
| People directory | `./people.toml` | `--people` |
| Gmail MCP server | from `actiontrack.toml` `[gmail_mcp]` | — |

```toml
# actiontrack.toml
[gmail_mcp]
transport  = "stdio"                 # or "http"
command    = "npx"                   # stdio: how to start the Gmail MCP server
args       = ["-y", "<gmail-mcp-server-package>"]
# url      = "https://…/mcp"         # http transport
draft_tool = "create_draft"          # name of the connector's draft tool

# people.toml
[[person]]
name  = "Priya Nair"
email = "priya.nair@example.com"
```

---

## 6. Detailed design

### 6.1 Reading a note (header)

Two header styles exist in the sample and both must work:

| Style | Example | Files |
|---|---|---|
| Markdown header | `# Meeting: Quote portal kickoff` / `Date: 2026-09-01` / `Attendees: A, B, C` | most |
| YAML front matter | `---` `title:` `date:` `attendees: [A, B]` `---` | `2026-09-16-standup.md`, `2026-09-30-standup.md` |

**Meeting date formats accepted**: `2026-09-01`, `9 September 2026`, `Monday, 14 September 2026`, `14 Sep 2026`, `Sep 14, 2026`. A leading weekday is optional; if present and wrong for the date, keep the date and warn `WEEKDAY_MISMATCH`.

**The filename date is never used as a fallback.** If the header date is missing or unreadable the note is malformed. (Reason: silently guessing the anchor date would make every relative due date in that note wrong.)

**Attendees**: comma-separated full names. Missing attendees is a **warning** (`MISSING_ATTENDEES`), not a skip — the note is still ingested and owners are resolved against the global roster (e.g. `2026-09-23-standup.md`).

### 6.2 Extracting action items

**A line is an action item when** it matches (after stripping indentation):

```
^[-*+]\s+\[( |x|X)\]\s+(?P<text>.+)$
```

- Bullet `-`, `*` or `+`; checkbox `[ ]` (open) or `[x]` / `[X]` (done).
- Indented (nested) checkboxes are separate items. The parent line number is kept as `parent_line` for context.
- Items are taken from anywhere in the body, not only under `## Action items`, because real notes put them in other sections (see `2026-09-30-q4-offsite.md`).

**Lines that must be ignored** (they look like tasks but are not agreed actions):

| Context | Example in sample | Rule |
|---|---|---|
| Blockquote | `> - [ ] Karan to refund affected customers by Friday` (`2026-09-21`) | ignore lines starting with `>` |
| Fenced code block | auditor template inside ```` ``` ```` (`2026-10-09`) | ignore everything between fences |
| HTML comment | `<!-- - [ ] Sam to buy hardware tokens … -->` (`2026-10-09`) | ignore everything inside `<!-- -->`, including multi-line |
| YAML front matter | — | not part of the body |

**Action-like lines in other formats** are *not* ingested in v1 but are reported as `UNSUPPORTED_ACTION_FORMAT` so a human can decide:

- Table rows under an `Owner | Action | Due` header (`| Anita | Refresh the brand kit | 2026-10-09 |`).
- `ACTION:` prefix lines (`ACTION: Karan to circulate the offsite summary tomorrow`).

> Design decision: the assignment defines items as checkbox lines. Silently ingesting other formats risks false positives; silently dropping them risks lost items. Reporting them is the safe middle.

**Splitting the text** into owner phrase, task and due phrase:

```
<owner phrase> to <task> [<due phrase>]
@<name>: <task> [<due phrase>]
```

- Owner phrase = text before the first ` to ` (word-bounded), or the `@handle` before `:`.
- If neither pattern matches (e.g. `Book the venue`), the whole text is the task and the owner is **unassigned**.
- The due phrase is the **last** trigger phrase that resolves to a real date (see §6.4). It is removed from the task text for display and dedup, and kept as `due_raw`.

### 6.3 Owner resolution

Output: `OwnerResolution(status, people[], raw, candidates[])`, where status is one of `resolved`, `ambiguous`, `group`, `unassigned`.

**Owner phrase patterns**

| Phrase | Example | Result |
|---|---|---|
| First name | `Karan` | lookup (below) |
| First name + initial | `Priya N.`, `Priya S.` | match surname initial → resolved |
| Handle | `@anita:` | case-insensitive first-name lookup |
| Two people | `Anita & Rahul`, `Anita and Rahul` | one item, **co-owners** (each resolved separately) |
| With helper | `Lena (with Priya)` | owner = Lena; Priya stored as `collaborator` (not an owner) |
| Group | `Everyone`, `Team`, `All` | status `group`; never in a personal follow-up |
| Placeholder | `Someone`, `TBD`, `Owner`, `?` | status `unassigned` |
| None | `Book the venue` | status `unassigned` |

**Lookup order for a first name** (this is how the two Priyas and two Rahuls are handled):

1. Explicit initial (`Priya N.`) → the roster person with that first name and surname initial.
2. Attendees of **this** meeting with that first name: exactly one → `resolved`.
3. More than one attendee with that first name → `ambiguous`, candidates = those attendees.
4. No attendee matches (or note has no attendees) → global roster: exactly one → `resolved` (warn `OWNER_NOT_ATTENDEE` if the note had attendees); several → `ambiguous`; none → `resolved` to a new roster entry with first name only, warn `UNKNOWN_PERSON`.

Ambiguous items are **never guessed**. They are listed with `--ambiguous`, shown in follow-ups as "Please confirm", and can be fixed by editing the note (e.g. `Priya` → `Priya N.`) and re-ingesting.

**Roster from the sample data** (9 people): Priya Nair, Priya Shah, Rahul Mehta, Rahul Verma, Anita Desai, Sam Okafor, Meera Iyer, Karan Joshi, Lena Fischer.

### 6.4 Due-date resolution

Input: due phrase + meeting date. Output: `date` or `None` (with `due_raw` kept). Weeks start on **Monday**. All results are real calendar dates.

**Trigger words** that start a due phrase: `by`, `due`, `before`, `on`, `from`, `until`, `no later than`, `for` (only when followed by a specific day/date). The parser tries each trigger in the line and keeps the **last one that resolves**. This matters for `Sam to cut cloud costs by 10% — proposal by 2026-10-09`: `by 10%` does not resolve, `by 2026-10-09` does.

| Expression | Rule | Example (meeting date → due) |
|---|---|---|
| ISO date | as is | `by 2026-09-08` → 2026-09-08 |
| Day + month | meeting year (see rollover) | `by 10 Sep` → 2026-09-10; `by Oct 3rd` → 2026-10-03; `15 October` → 2026-10-15 |
| Weekday + day + month | use the date, warn if weekday wrong | `by Wed 16 Sep` → 2026-09-16 |
| Weekday name / abbreviation | first such day **strictly after** the meeting date | Tue 2026-09-01 `by Friday` → 2026-09-04; Fri 2026-09-18 `by Mon` → 2026-09-21 |
| `next <weekday>` | that weekday in the **following** Mon–Sun week | Mon 2026-10-05 `by next Tuesday` → 2026-10-13 |
| `today`, `EOD`, `end of day` | meeting date | Thu 2026-10-08 `by EOD` → 2026-10-08 |
| `tomorrow`, `EOD tomorrow` | meeting date + 1 | Mon 2026-09-21 `by EOD tomorrow` → 2026-09-22 |
| `end of (the) week` | Friday of meeting week | — |
| `end of next week` | Friday of the following week | Thu 2026-09-03 → 2026-09-11 |
| `end of (the) month` | last day of meeting month | Mon 2026-09-14 → 2026-09-30 |
| `mid-<Month>` | 15th of that month | `by mid-October` → 2026-10-15 |
| `from <date>` | that date (the action must be in place then) | `from 9 Oct` → 2026-10-09 |
| Month only (`for November`), event-relative (`before cutover`, `before the next review`) | **unresolved** → `due_date = NULL`, warn `UNRESOLVED_DUE` | — |

**Year rollover**: a day+month without a year takes the meeting's year; if that lands more than 60 days *before* the meeting date, use the next year (handles a December meeting saying `by 5 Jan`).

**No due phrase** → `due_date = NULL`, no warning (this is normal: `Sam to share the VPN guide`).

### 6.5 Deduplication (items repeated across meetings)

Notes repeat the same task week after week, often reworded slightly and eventually ticked. Each repeat must update **one** item, not create a new one.

**Normalised task key**

1. Take the task text without the owner phrase and the due phrase.
2. Lower-case, strip punctuation and dashes (`—`), collapse whitespace.
3. Drop stop-words: `the, a, an, to, for, with, of, on, our, their`.
4. Normalise spelling variants: `finalise/finalize`, `colour/color`.

**Match rule**: a new mention matches an existing item when **all** are true:

- Same owner set (resolved person IDs; for ambiguous owners, same raw name + same candidate set; for unassigned/group, same raw phrase).
- Token-set similarity of normalised keys ≥ **0.85** (Jaccard on tokens), or one key is a subset of the other with at least 3 tokens.
- The existing item came from a meeting on or before this one.

**Merge policy** (mentions processed in meeting-date order, then line order):

| Field | Rule |
|---|---|
| `status` | Latest mention wins (`[x]` later → done, `done_source = note`). Exception: manual `done` is sticky (FR-3.3). |
| `due_date` | Latest mention **that has** a due date wins. A later mention with no due date keeps the previous one. |
| `task` (display) | Latest wording. |
| sources | Every mention is kept in `mentions` (file, line, raw text). `list` shows the first and latest source. |

**Distinct on purpose** (must *not* merge): `share first wireframes` vs `share updated wireframes`; `pull Acme's usage report` vs `share the usage report with Meera`; `collect 5 customer quotes` vs `publish the customer quotes`; Rahul Mehta's `fix the pagination bug` vs Rahul Verma's `fix the top three critical bugs`.

### 6.6 Malformed notes

Each file is processed inside its own `try`; one bad file can never stop the run.

| Reason code | Detection | Sample file |
|---|---|---|
| `EMPTY_FILE` | 0 bytes or only whitespace | `2026-09-09-notes-final-v2.md` |
| `NOT_TEXT` | not valid UTF-8, or contains NUL / binary signature (e.g. PNG header) | `scratch.md` |
| `MISSING_HEADER` | no title and no date header / front matter | `untitled.md` |
| `BAD_DATE` | date line present but unparseable | `2026-10-03-sales-pipeline.md` (`Date: sometime last week`) |
| `BAD_FRONT_MATTER` | `---` block that is not valid YAML | — |
| `READ_ERROR` | OS error (permissions, file vanished) | — |

A skipped note contributes **no** items. A file that was previously ingested and is now malformed keeps its earlier items and is reported as `STALE_SOURCE`.

Non-`.md` files (`2026-09-19-offsite-draft.txt`) are **ignored**, listed once in the report, and not counted as malformed.

---

## 7. Data model (SQLite)

```sql
CREATE TABLE notes (
  id            INTEGER PRIMARY KEY,
  path          TEXT UNIQUE NOT NULL,
  sha256        TEXT NOT NULL,
  title         TEXT,
  meeting_date  TEXT,                 -- YYYY-MM-DD, NULL if skipped
  state         TEXT NOT NULL CHECK (state IN ('ingested','skipped')),
  skip_reason   TEXT,
  ingested_at   TEXT NOT NULL
);

CREATE TABLE people (
  id          INTEGER PRIMARY KEY,
  full_name   TEXT UNIQUE NOT NULL,
  first_name  TEXT NOT NULL,
  last_name   TEXT,
  email       TEXT
);

CREATE TABLE items (
  id             INTEGER PRIMARY KEY,           -- the <id> used by `done`
  task           TEXT NOT NULL,                 -- latest display wording
  task_key       TEXT NOT NULL,                 -- normalised dedup key
  owner_status   TEXT NOT NULL CHECK (owner_status IN ('resolved','ambiguous','group','unassigned')),
  owner_raw      TEXT,                          -- e.g. 'Priya', 'Anita & Rahul', 'TBD'
  due_date       TEXT,                          -- YYYY-MM-DD or NULL
  due_raw        TEXT,                          -- e.g. 'by Friday', 'before cutover'
  status         TEXT NOT NULL CHECK (status IN ('open','done')),
  done_source    TEXT CHECK (done_source IN ('note','manual')),
  done_at        TEXT,
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL
);

CREATE TABLE item_people (                       -- owners, candidates, collaborators
  item_id   INTEGER REFERENCES items(id),
  person_id INTEGER REFERENCES people(id),
  role      TEXT NOT NULL CHECK (role IN ('owner','candidate','collaborator')),
  PRIMARY KEY (item_id, person_id, role)
);

CREATE TABLE mentions (                          -- one row per checkbox line
  id           INTEGER PRIMARY KEY,
  item_id      INTEGER REFERENCES items(id),
  note_id      INTEGER REFERENCES notes(id),
  line_no      INTEGER NOT NULL,
  raw_line     TEXT NOT NULL,
  checked      INTEGER NOT NULL,                 -- 0/1
  due_resolved TEXT,
  UNIQUE (note_id, line_no)
);

CREATE TABLE warnings (
  id INTEGER PRIMARY KEY, note_id INTEGER, line_no INTEGER,
  code TEXT NOT NULL, message TEXT NOT NULL, run_id TEXT NOT NULL
);

CREATE TABLE followups (
  id INTEGER PRIMARY KEY, person_id INTEGER, draft_id TEXT,
  item_ids TEXT, created_at TEXT NOT NULL
);
```

`overdue` is computed at query time: `status = 'open' AND due_date IS NOT NULL AND due_date < :today`.

---

## 8. Interfaces

### 8.1 Command synopsis

```
actiontrack [--db PATH] [--today YYYY-MM-DD] <command> …

  ingest <folder> [--recursive] [--json]
  list   [--owner NAME] [--status open|done|overdue|all] [--due-before YYYY-MM-DD]
         [--unassigned] [--ambiguous] [--json]
  done   <id>
  followup (<owner> | --all) [--to EMAIL] [--dry-run | --emit-json] [--force]
```

### 8.2 Example ingest report (sample folder)

```
Scanned 36 files in notes/
  Ingested : 31
  Skipped  : 4
    2026-09-09-notes-final-v2.md  EMPTY_FILE (file is empty)
    2026-10-03-sales-pipeline.md  BAD_DATE ("sometime last week")
    scratch.md                    NOT_TEXT (binary content)
    untitled.md                   MISSING_HEADER (no title, no date)
  Ignored  : 1 (not .md)  2026-09-19-offsite-draft.txt

Items: 90 new, 23 repeat mentions merged into existing items, 13 marked done by notes — 90 items tracked

Warnings (12):
  UNRESOLVED_DUE             2026-09-03-billing-migration-sync.md:12  "before cutover" is not a calendar date
  AMBIGUOUS_OWNER            2026-09-17-billing-migration-sync.md:9  "Priya" → Priya Shah | Priya Nair
  MISSING_ATTENDEES          2026-09-23-standup.md  no attendees listed
  UNSUPPORTED_ACTION_FORMAT  2026-09-30-q4-offsite.md:22  table row not ingested: | Anita | Refresh the brand kit | 2026-10-09 |
  …
```
(Actual output of the v1.0 implementation on the sample folder.)

### 8.3 Example `list`

```
$ actiontrack --today 2026-10-22 list --owner "Sam Okafor" --status overdue
ID   Owner       Task                                         Due         Status              Source
28   Sam Okafor  Run the dry-run migration on staging         2026-09-22  OVERDUE (30 days)   2026-09-17-billing-migration-sync.md:10
34   Sam Okafor  Write the postmortem                         2026-09-22  OVERDUE (30 days)   2026-09-21-incident-review.md:14
…
```

### 8.4 Exit codes

| Code | Meaning |
|---|---|
| 0 | Success (including runs where some notes were skipped) |
| 1 | Usage error (bad flag, bad date format in a filter) |
| 2 | Folder not found / not readable |
| 3 | Gmail MCP error (connect, auth, tool call failed) |
| 4 | Item ID not found |
| 5 | Ambiguous owner name — candidates printed |
| 6 | No email address for owner (and no `--to`) |

### 8.5 Follow-up email format

```
To: sam.okafor@example.com
Subject: Action items follow-up — 7 overdue, 5 open (as of 22 Oct 2026)

Hi Sam,

Here's a quick roundup of your action items from recent meetings.

Overdue
  • Run the dry-run migration on staging — due 22 Sep (Billing migration sync, 17 Sep)
  • Write the postmortem — due 22 Sep (Incident review, 21 Sep)
  • …

Open
  • Decommission the old billing DB — due 30 Oct (Billing post-cutover, 12 Oct)

No due date yet
  • Share the VPN guide (Team standup, 2 Sep)
  • …

Could you reply with an update or a new date for anything that's slipped?

Thanks,
<sender name from config>
```

Rules: overdue first (oldest due first), then open by due date, then no-due-date items, then "Please confirm — is this yours?". Each line names the meeting and its date so the reader can find the context.

### 8.6 Gmail MCP integration (draft only)

```python
class DraftGateway(Protocol):
    def create_draft(self, to: str, subject: str, body: str) -> str: ...  # returns draft id
```

| Adapter | Used when | Behaviour |
|---|---|---|
| `McpGmailGateway` | default | Opens an MCP `ClientSession` (stdio or HTTP from `actiontrack.toml`), calls `list_tools`, checks `draft_tool` exists, calls it with `{to, subject, body}`. Returns the draft ID. |
| `EmitJsonGateway` | `--emit-json` | Prints `{"to","subject","body","item_ids"}`; an agent (Claude skill) calls the Gmail connector's draft tool. |
| `DryRunGateway` | `--dry-run` | Prints the email. No network. |

**Never-send guarantee (defence in depth)**

1. The only MCP tool the code may call is the configured `draft_tool`; the name is checked against an allowlist before every call.
2. Any tool name containing `send` is rejected even if configured (`ConfigError`).
3. There is no `send` code path or flag in the CLI.
4. A unit test with a fake MCP server asserts that only the draft tool is ever called, and that a config pointing at a send tool fails.

---

## 9. Edge cases → behaviour (from the sample folder)

| # | Edge case | Where | Expected behaviour |
|---|---|---|---|
| E1 | Same item repeated across meetings | Revised quote to Acme (09-01, 09-08, 09-15) | 1 item, owner Priya Nair, `done` (ticked on 09-15) |
| E2 | Repeat with due date changing | Lena's 5 customer quotes: `end of month` (09-14) → `by 2 Oct` (09-24) | 1 item, due 2026-10-02 |
| E3 | Repeat where later mention has no due date | Priya's renewal one-pager: `by Friday` (09-28) → no date (10-14) | 1 item, due 2026-10-02 kept |
| E4 | Repeat that is later ticked | Rahul's beta deploy (10-01 → 10-06 → `[x]` 10-13) | 1 item, Rahul Mehta, done |
| E5 | Item with no owner | `Someone to update the CRM notes`, `TBD to write the design-system changelog` | `unassigned`, shown by `--unassigned` |
| E6 | Group owner | `Everyone to review the prototype…`, `Team to decide on the launch date`, `Everyone to submit their Q4 goals` | `group`, not in personal follow-ups |
| E7 | No due date | `Sam to share the VPN guide` | `due_date NULL`, no warning |
| E8 | Due phrase that can't be dated | `before cutover`, `before the next review`, `for November` | `due_date NULL`, `UNRESOLVED_DUE` warning |
| E9 | Two people, same first name — resolvable | 09-03 billing sync: `Priya` with only Priya Shah attending | Priya Shah |
| E10 | Same first name — explicit initial | 09-29: `Priya S.` / `Priya N.` | Priya Shah / Priya Nair |
| E11 | Same first name — truly ambiguous | 09-17 `Priya to sign off…`; 10-07 `Rahul to review Anita's PR`; 10-08 `Priya to circulate…`; 09-30 `Priya to own the hiring plan`, `Rahul to prepare the architecture overview` | `ambiguous`, candidates listed, never guessed |
| E12 | No attendees line | `2026-09-23-standup.md` | Ingested + `MISSING_ATTENDEES`; `Sam` → Sam Okafor (unique), `Rahul` → ambiguous |
| E13 | Co-owners / helper | `Anita & Rahul to agree on the component API`; `Lena (with Priya) to collect user feedback` | co-owners Anita Desai + Rahul Mehta; owner Lena, collaborator Priya Nair |
| E14 | `@handle:` owner | `@anita: audit colour contrast on the dashboard by Mon` (Fri 09-18) | Anita Desai, due 2026-09-21 |
| E15 | Bullet / checkbox variants | `* [ ]`, `+ [ ]`, `[X]`, nested `  - [ ]` | all parsed |
| E16 | Fake tasks in quote/code/comment | 09-21 blockquote; 10-09 code fence + HTML comment | ignored; the real `Karan to refund affected customers` (Action items) is ingested once |
| E17 | Non-checkbox actions | table row + `ACTION:` line in 09-30 offsite | not ingested, `UNSUPPORTED_ACTION_FORMAT` |
| E18 | Misleading `by` | `cut cloud costs by 10% — proposal by 2026-10-09` | due 2026-10-09 |
| E19 | Front-matter header | 09-16 and 09-30 standups | parsed like normal headers |
| E20 | Long-form dates | `9 September 2026`, `Monday, 14 September 2026` | parsed |
| E21 | Empty / binary / headerless / bad-date files | see §6.6 | skipped with reason; run continues |
| E22 | Non-`.md` file | `2026-09-19-offsite-draft.txt` | ignored, listed |
| E23 | Re-running ingest | whole folder twice | second run: 0 new, 0 merged, unchanged files skipped by hash |
| E24 | Manual done then note re-lists open | `done 12`, then a newer note has `[ ]` for it | stays done, `REOPEN_CONFLICT` warning |

---

## 10. Acceptance tests (golden cases)

All run with `--today 2026-10-22` against `tests/fixtures/notes/`.

| ID | Given / When | Then |
|---|---|---|
| AT-01 | `ingest notes/` | 31 ingested, 4 skipped with codes `EMPTY_FILE`, `NOT_TEXT`, `MISSING_HEADER`, `BAD_DATE`; 1 ignored (`.txt`); exit 0 |
| AT-02 | `ingest notes/` twice | second run reports 0 new items, 0 changes |
| AT-03 | `list --status all --owner "Priya Nair"` | exactly one item matching "revised quote to Acme", status done, 3 mentions |
| AT-04 | `list --owner Priya` | exit 5, prints "Priya Nair, Priya Shah" |
| AT-05 | `list --ambiguous` | contains the 09-17 cutover-checklist item with candidates Priya Shah + Priya Nair, and the 10-07 PR-review item with Rahul Mehta + Rahul Verma |
| AT-06 | `list --unassigned` | contains "update the CRM notes" and "write the design-system changelog" |
| AT-07 | Due resolution | 09-01 `by Friday` → 2026-09-04; 09-03 `end of next week` → 2026-09-11; 09-14 `end of month` → 2026-09-30; 09-21 `by EOD tomorrow` → 2026-09-22; 09-30 `by mid-October` → 2026-10-15; 10-05 `by next Tuesday` → 2026-10-13; 10-02 `by 10% — proposal by 2026-10-09` → 2026-10-09 |
| AT-08 | `list --owner "Sam Okafor" --status overdue` | 7 items: dry-run migration (09-22), postmortem (09-22), schedule cutover (10-10), cloud-cost proposal (10-09), rollback plan (10-09), on-call rotation (10-15), MFA (10-16) |
| AT-09 | `list --owner "Sam Okafor" --status all` | does **not** contain "buy hardware tokens" (HTML comment) or "chase the vendor" (malformed file); cloud budget item is done |
| AT-10 | `list --due-before 2026-09-15 --status all` | only items with a due date < 2026-09-15; no NULL-due items |
| AT-11 | `done <id>` on an open item, then `list --status done` | item present; running `done <id>` again exits 0 with "already done" |
| AT-12 | `done 99999` | exit 4 |
| AT-13 | `followup "Sam Okafor" --dry-run` | subject counts 7 overdue / 5 open (1 dated + 4 with no due date); sections Overdue → Open → No due date; no MCP call made |
| AT-14 | `followup "Sam Okafor"` with fake MCP server | exactly one call, to `create_draft`; zero calls to any other tool |
| AT-15 | config `draft_tool = "send_email"` | `ConfigError` before any MCP call; exit 3 |
| AT-16 | `followup "Lena Fischer"` with no email in `people.toml` and no `--to` | exit 6, no draft |
| AT-17 | `followup` for an owner with only done items | "nothing to follow up", no draft |
| AT-18 | Feed a random-bytes `.md` file and a 5 MB file | no crash; skipped or ingested; exit 0 |

---

## 11. Non-functional requirements

| ID | Requirement |
|---|---|
| NFR-1 | Ingesting the 36-file sample takes < 2 s on a laptop. |
| NFR-2 | Deterministic: same files + same `--today` → same output (sorted, no random IDs). |
| NFR-3 | No network access except the Gmail MCP call in `followup`. |
| NFR-4 | Never modifies the note files. |
| NFR-5 | Dependencies: stdlib + `PyYAML` (front matter) + `mcp` (followup only). No `dateutil` fuzzy parsing — the date grammar is explicit and tested. |
| NFR-6 | Test coverage ≥ 90 % on `notes/`, `resolve/`, `dedup.py`. |
| NFR-7 | Email addresses and note contents are never logged to stdout except in `--dry-run`/`--emit-json` output the user asked for. |

---

## 12. Implementation plan (task breakdown)

Spec-driven order: each task is done when its tests pass.

| # | Task | Satisfies | Tests |
|---|---|---|---|
| T1 | Project skeleton, `pyproject.toml`, CLI stub, exit codes | §8.1, §8.4 | `test_cli.py::test_help` |
| T2 | `NoteReader`: both header styles, date formats, skip reasons | FR-1.2, FR-1.8, §6.1, §6.6 | AT-01, E19–E22 |
| T3 | `ItemExtractor`: checkbox grammar, ignore zones, unsupported formats | FR-1.3, §6.2 | E15–E17 |
| T4 | `DueDateResolver` | FR-1.6, §6.4 | AT-07, E7, E8, E18 |
| T5 | `OwnerResolver` + roster | FR-1.5, §6.3 | AT-04, AT-05, AT-06, E9–E14 |
| T6 | SQLite store + idempotent ingest | FR-1.4, FR-1.9, §7 | AT-02 |
| T7 | Deduplicator + merge policy | FR-1.7, §6.5 | AT-03, E1–E4 |
| T8 | Ingest report (text + JSON) | FR-1.10 | snapshot test |
| T9 | `list` filters, sorting, overdue marker | FR-2.x | AT-08–AT-10 |
| T10 | `done` + sticky manual done | FR-3.x | AT-11, AT-12, E24 |
| T11 | Follow-up composer | FR-4.1, FR-4.4, FR-4.5, §8.5 | AT-13, AT-17 |
| T12 | `DraftGateway` (MCP, emit, dry-run) + never-send guard | FR-4.2, FR-4.6, §8.6 | AT-14, AT-15, AT-16 |
| T13 | Claude skill (`SKILL.md`) | §13 | manual run-through |
| T14 | README + end-to-end run on sample folder | all | AT-01…AT-18 |

---

## 13. Claude skill

`.claude/skills/actiontrack/SKILL.md` teaches Claude to operate the tool safely: run `ingest`, surface the skipped-file report and ambiguous owners to the user, use `list` to answer questions, and create follow-ups as **Gmail drafts only** — either via `actiontrack followup` (MCP adapter) or via `--emit-json` + the Gmail connector's draft tool. It forbids sending email and forbids guessing between same-first-name people.

---

## 14. Risks and open questions

| # | Item | Current decision | Revisit if… |
|---|---|---|---|
| Q1 | `next Tuesday` said on a Monday — tomorrow or a week later? | Following week (2026-10-13 for a 10-05 meeting) | team usage says otherwise; make it a config flag |
| Q2 | Should table rows / `ACTION:` lines be ingested? | Report only | false-negative rate is high in real notes |
| Q3 | Should `Anita & Rahul` become two items? | One item, two co-owners | owners want separate tracking |
| Q4 | Filename date as fallback for bad header date? | No — skip the note | many notes have broken headers |
| Q5 | Fuzzy dedup threshold 0.85 | Tune on sample; distinct pairs in §6.5 must stay distinct | false merges appear |
| Q6 | Gmail MCP server choice / tool name | Configurable `draft_tool` | connector changes its tool names |

---

## 15. Traceability

| Assignment requirement | Covered by |
|---|---|
| `ingest <folder>` parses every `.md` note | FR-1.1–1.3, §6.1–6.2 |
| Store owner, task, due date (real date), source file, status | FR-1.4, §6.4, §7 |
| `list` with `--owner`, `--status open\|done\|overdue`, `--due-before`; overdue flagged | FR-2.1–2.6 |
| `done <id>` | FR-3.1–3.3 |
| `followup <owner>`: one email per owner, open + overdue, Gmail draft via MCP, never sends | FR-4.1–4.8, §8.5–8.6 |
| Duplicates across meetings | §6.5, E1–E4, AT-03 |
| No owner / no date | §6.3, §6.4, E5–E8, AT-06 |
| Two people with the same first name | §6.3, E9–E13, AT-04, AT-05 |
| Malformed notes: skip and report, don't crash | §6.6, E21, AT-01, AT-18 |
