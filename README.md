# actiontrack

A small CLI that reads markdown meeting notes, tracks the action items, and drafts follow-up emails in Gmail for you to review. **It never sends email. It only creates drafts.**

- Design spec: [`docs/SDD.md`](docs/SDD.md)
- Claude skill: [`.claude/skills/actiontrack/SKILL.md`](.claude/skills/actiontrack/SKILL.md)
- Sample data: [`notes/`](notes/) (36 files, including deliberately broken ones)
- AI conversation exports: [`conversations/`](conversations/)

## Install

Requires Python 3.11+.

```bash
python -m venv .venv
# Windows:  .venv\Scripts\activate      macOS/Linux:  source .venv/bin/activate
pip install -e ".[dev]"
```

## Use

```bash
actiontrack ingest notes/
actiontrack list                                   # open items (overdue ones are marked)
actiontrack list --status overdue
actiontrack list --owner "Sam Okafor"
actiontrack list --due-before 2026-10-01 --status all
actiontrack list --ambiguous                       # "Priya" when both Priyas were in the room
actiontrack list --unassigned                      # "Someone to…", "TBD to…", "Everyone to…"
actiontrack done 28
actiontrack followup "Sam Okafor" --dry-run        # preview the email
actiontrack followup "Sam Okafor"                  # create the Gmail draft (needs Gmail MCP config)
actiontrack followup --all --dry-run
```

Add `--today 2026-10-22` to any command to get the same results as the tests and examples. Add `--json` to `ingest` and `list` to get output a script can read.

### Sample run (today = 2026-10-22)

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
```

## Gmail drafts (MCP)

`followup` talks to a Gmail MCP server and calls **only** its create-draft tool. Copy the example files and fill them in:

```bash
cp actiontrack.example.toml actiontrack.toml     # which Gmail MCP server to start, and its draft tool name
cp people.example.toml people.toml               # name → email address
```

Safety: the tool name is checked before every call, any tool name containing `send` is refused, and the CLI has no send option. `tests/test_cli.py::test_at14…` and `…test_at15…` prove this against a fake MCP server that also offers a `send_email` tool.

No Gmail MCP server? Use `--emit-json`. It prints `{to, subject, body}`, and Claude (using the skill) creates the draft with the Gmail connector.

## How messy notes are handled

| Situation | What happens |
|---|---|
| Same task repeated across meetings | Merged into one item. The latest mention sets the status and due date. |
| "by Friday", "EOD tomorrow", "end of next week", "mid-October", "10 Sep" | Turned into a real date, counted from the meeting date |
| "before cutover", "for November" | No due date. Kept as text and flagged in the report. |
| "Priya" when Priya Nair and Priya Shah both attended | Marked *ambiguous*, never guessed. Write `Priya N.` in the note to fix it. |
| "Someone / TBD / Everyone / Team to …" | Unassigned or group item |
| Empty, binary, header-less, or bad-date note | Skipped and reported. The run continues. |
| Task-like lines in quotes, code blocks, or HTML comments | Ignored |
| Tables / `ACTION:` lines | Reported as warnings, not tracked |

## Tests

```bash
pytest -q        # 75 tests, including every SDD acceptance test (AT-01 … AT-18)
```
