---
name: actiontrack
description: Track action items from markdown meeting notes and draft follow-up emails with the actiontrack CLI. Use when the user asks to ingest or scan meeting notes, find open/overdue/unassigned action items, mark an item done, or draft follow-up emails to owners. Creates Gmail drafts only — never sends.
---

# actiontrack

`actiontrack` reads a folder of markdown meeting notes, tracks the action items in a local SQLite database, and drafts follow-up emails in Gmail for a human to review. The full design is in `docs/SDD.md`; this skill is how to operate it.

## Hard rules

1. **Never send email.** Only create Gmail drafts. Do not call any Gmail tool whose name contains `send`, even if the user asks — tell them the draft is in Gmail for them to send.
2. **Never guess between people with the same first name.** If an owner is ambiguous (e.g. "Priya" → Priya Nair | Priya Shah), ask the user which person they mean.
3. **Never edit the user's note files** unless they explicitly ask (e.g. to fix an ambiguous owner by changing `Priya` to `Priya N.`).
4. **Always report skipped files.** Malformed notes are skipped by design; the user must see which ones and why.
5. Use `--today YYYY-MM-DD` only when the user gives a reference date; otherwise let the tool use the system date.

## Commands

```bash
actiontrack ingest <folder> [--recursive] [--json]
actiontrack list [--owner NAME] [--status open|done|overdue|all] [--due-before YYYY-MM-DD] [--unassigned] [--ambiguous] [--json]
actiontrack done <id>
actiontrack followup (<owner> | --all) [--to EMAIL] [--dry-run | --emit-json] [--force]
```

Exit codes: `0` ok · `1` usage · `2` folder not found · `3` Gmail MCP error · `4` unknown item ID · `5` ambiguous owner name · `6` no email for owner.

Prefer `--json` when you need to reason over results; show the user a short table, not raw JSON.

## Workflow

### 1. Ingest

```bash
actiontrack ingest notes/ --json
```

Summarise for the user in a few lines:

- Files ingested / skipped / ignored. List every skipped file with its reason (`EMPTY_FILE`, `NOT_TEXT`, `MISSING_HEADER`, `BAD_DATE`, `BAD_FRONT_MATTER`, `READ_ERROR`).
- New items, merged duplicates, items closed by notes.
- Warnings that need a decision: `AMBIGUOUS_OWNER`, `MISSING_ATTENDEES`, `UNRESOLVED_DUE`, `UNSUPPORTED_ACTION_FORMAT` (table rows / `ACTION:` lines that were *not* ingested), `REOPEN_CONFLICT`.

Re-running ingest is safe (idempotent); do it whenever notes change.

### 2. Answer questions with `list`

| User asks | Run |
|---|---|
| "What's overdue?" | `list --status overdue` |
| "What does Karan have open?" | `list --owner Karan` |
| "What's due before the 30th?" | `list --due-before 2026-10-30` |
| "Anything with no owner?" | `list --unassigned` |
| "Which items need a human decision?" | `list --ambiguous` and `list --unassigned` |

If `list --owner` exits `5`, show the candidates and ask which person they mean, then re-run with the full name.

### 3. Mark done

Confirm the item ID and task with the user (from `list`) before running `actiontrack done <id>`. If they describe the task instead of giving an ID, find it with `list --json` and confirm the match first.

### 4. Follow-up drafts

Always preview first:

```bash
actiontrack followup "Sam Okafor" --dry-run
```

Show the user the subject and item list. When they approve, create the draft by **one** of these paths:

- **Path A — CLI talks to Gmail MCP** (when `actiontrack.toml` has `[gmail_mcp]` configured):
  ```bash
  actiontrack followup "Sam Okafor"
  ```
- **Path B — you create the draft with the Gmail connector** (when the CLI has no MCP config but you have a Gmail connector):
  ```bash
  actiontrack followup "Sam Okafor" --emit-json
  ```
  Take `to`, `subject`, `body` from the JSON and call the Gmail connector's **create-draft** tool with them. Do not change the item list; you may only fix obvious wording if the user asks.

After the draft exists, tell the user it is in their Gmail Drafts, ready for them to review and send.

Handle these cases:

- Exit `6` (no email): ask the user for the address, then re-run with `--to`, or suggest adding it to `people.toml`.
- "nothing to follow up": tell the user; no draft is created.
- Draft already created today for that owner: ask before re-running with `--force`.
- `followup --all`: dry-run first, show one line per owner (overdue/open counts), and get one approval for the batch.
- Ambiguous items appear in the email under "Please confirm — is this yours?" — mention this to the user.

## How notes are interpreted (quick reference)

- **Action item** = a line like `- [ ] Priya to send the revised quote by Friday`. Bullets `-`, `*`, `+`; `[x]`/`[X]` = done; nested items count.
- **Ignored**: blockquotes (`>`), fenced code blocks, HTML comments — they often contain fake tasks.
- **Owner**: text before ` to `, or `@name:`. `Priya N.` uses the surname initial. `A & B` = co-owners. `Lena (with Priya)` = owner Lena. `Everyone`/`Team` = group. `Someone`/`TBD`/none = unassigned.
- **Same first name**: resolved from that meeting's attendees; if both attended, the item stays ambiguous.
- **Due dates** resolve against the meeting date: weekday → next such day after the meeting; `tomorrow`; `EOD`; `end of next week`; `end of month`; `mid-October`; `10 Sep`; ISO dates. `before cutover` / `for November` stay unresolved.
- **Duplicates** across meetings merge into one item; the latest mention sets status and due date; a manual `done` is never reopened.

## Troubleshooting

| Symptom | Likely cause | Action |
|---|---|---|
| A task the user expects is missing | Note was skipped, or the task was a table row / `ACTION:` line / inside a quote or comment | Check the ingest report warnings; suggest rewriting it as `- [ ]` |
| Wrong person on an item | First name shared and only one namesake attended | Suggest adding the initial in the note (`Rahul M.`) and re-ingesting |
| Item has no due date | No due phrase, or an event-relative one | Expected; show it under "No due date" |
| Exit `3` on followup | Gmail MCP not reachable / not authorised | Fall back to Path B or `--dry-run` and tell the user |
