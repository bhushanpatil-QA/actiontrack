"""actiontrack command-line interface (SDD §8)."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from actiontrack.config import Config, ConfigError, build_config
from actiontrack.followup.compose import compose
from actiontrack.followup.gateway import DryRunGateway, EmitJsonGateway, GatewayError, McpGmailGateway
from actiontrack.ingest import ingest
from actiontrack.resolve.owners import Roster
from actiontrack.store import ItemRow, Store

EXIT_OK, EXIT_USAGE, EXIT_NO_FOLDER, EXIT_GMAIL, EXIT_NO_ITEM, EXIT_AMBIGUOUS, EXIT_NO_EMAIL = 0, 1, 2, 3, 4, 5, 6


class CliError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


# --------------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", default=argparse.SUPPRESS, help="SQLite file (default .actiontrack/actiontrack.db)")
    common.add_argument("--today", default=argparse.SUPPRESS, help="pretend today is YYYY-MM-DD (for tests/demos)")
    common.add_argument("--people", default=argparse.SUPPRESS, help="people directory (default people.toml)")
    common.add_argument("--config", default=argparse.SUPPRESS, help="settings file (default actiontrack.toml)")

    p = argparse.ArgumentParser(prog="actiontrack", parents=[common],
                                description="Track action items from meeting notes and draft follow-up emails.")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("ingest", parents=[common], help="parse every .md meeting note in a folder")
    s.add_argument("folder")
    s.add_argument("--recursive", action="store_true")
    s.add_argument("--json", action="store_true")

    s = sub.add_parser("list", parents=[common], help="show action items")
    s.add_argument("--owner")
    s.add_argument("--status", choices=["open", "done", "overdue", "all"], default="open")
    s.add_argument("--due-before", metavar="YYYY-MM-DD")
    s.add_argument("--unassigned", action="store_true", help="only items with no owner (or a group owner)")
    s.add_argument("--ambiguous", action="store_true", help="only items whose owner name matches several people")
    s.add_argument("--json", action="store_true")

    s = sub.add_parser("done", parents=[common], help="mark an item as done")
    s.add_argument("id", type=int)

    s = sub.add_parser("followup", parents=[common], help="create a Gmail draft per owner (never sends)")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("owner", nargs="?")
    g.add_argument("--all", action="store_true")
    s.add_argument("--to", help="recipient address (overrides people.toml)")
    m = s.add_mutually_exclusive_group()
    m.add_argument("--dry-run", action="store_true", help="print the email, create nothing")
    m.add_argument("--emit-json", action="store_true", help="print the draft as JSON for an agent to create")
    s.add_argument("--force", action="store_true", help="allow a second draft for the same owner today")
    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    try:
        cfg = build_config(getattr(args, "db", None), getattr(args, "today", None),
                           getattr(args, "people", None), getattr(args, "config", None))
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_USAGE
    try:
        return {"ingest": cmd_ingest, "list": cmd_list, "done": cmd_done, "followup": cmd_followup}[args.command](args, cfg)
    except CliError as e:
        print(f"error: {e}", file=sys.stderr)
        return e.code
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_GMAIL if args.command == "followup" else EXIT_USAGE


# --------------------------------------------------------------------------- ingest
def cmd_ingest(args, cfg: Config) -> int:
    folder = Path(args.folder)
    if not folder.is_dir():
        raise CliError(EXIT_NO_FOLDER, f"folder not found: {folder}")
    store = Store(cfg.db)
    try:
        rep = ingest(folder, store, recursive=args.recursive, people=cfg.people)
    finally:
        store.close()
    if args.json:
        print(json.dumps(rep.to_dict(), indent=2, ensure_ascii=False))
        return EXIT_OK

    print(f"Scanned {rep.scanned} files in {folder}/")
    print(f"  Ingested : {len(rep.ingested) + len(rep.unchanged)}"
          + (f"  ({len(rep.unchanged)} unchanged since last run)" if rep.unchanged else ""))
    print(f"  Skipped  : {len(rep.skipped)}")
    w = max((len(f) for f, _, _ in rep.skipped), default=0)
    for f, code, detail in rep.skipped:
        print(f"    {f:<{w}}  {code} ({detail})")
    if rep.ignored:
        print(f"  Ignored  : {len(rep.ignored)} (not .md)  " + ", ".join(rep.ignored))
    print()
    print(f"Items: {rep.items_new} new, {rep.mentions_merged} repeat mentions merged into existing items, "
          f"{rep.closed_by_notes} marked done by notes — {rep.items_total} items tracked")
    if rep.warnings:
        print(f"\nWarnings ({len(rep.warnings)}):")
        cw = max(len(c) for _, _, c, _ in rep.warnings)
        for f, ln, code, msg in rep.warnings:
            where = f"{f}:{ln}" if ln else f
            print(f"  {code:<{cw}}  {where}  {msg}")
    return EXIT_OK


# --------------------------------------------------------------------------- list
def _resolve_person(store: Store, query: str) -> str:
    roster = Roster(store.people_names())
    matches = roster.match(query)
    if not matches:
        raise CliError(EXIT_USAGE, f"no person matches {query!r}. Known: {', '.join(store.people_names())}")
    if len(matches) > 1:
        raise CliError(EXIT_AMBIGUOUS, f"{query!r} matches more than one person: "
                                       f"{', '.join(p.full_name for p in matches)}. Use the full name.")
    return matches[0].full_name


def _filter(items: list[ItemRow], args, today: date, person: str | None) -> list[ItemRow]:
    out = []
    due_before = None
    if args.due_before:
        try:
            due_before = date.fromisoformat(args.due_before).isoformat()
        except ValueError:
            raise CliError(EXIT_USAGE, f"--due-before must be YYYY-MM-DD, got {args.due_before!r}")
    for it in items:
        if person and person not in it.owners:
            continue
        if args.status == "open" and it.status != "open":
            continue
        if args.status == "done" and it.status != "done":
            continue
        if args.status == "overdue" and not it.is_overdue(today):
            continue
        if due_before and not (it.due_date and it.due_date < due_before):
            continue
        if args.unassigned and it.owner_status not in ("unassigned", "group"):
            continue
        if args.ambiguous and it.owner_status != "ambiguous":
            continue
        out.append(it)
    return sorted(out, key=lambda i: (i.due_date is None, i.due_date or "", i.id))


def cmd_list(args, cfg: Config) -> int:
    store = Store(cfg.db)
    try:
        person = _resolve_person(store, args.owner) if args.owner else None
        items = _filter(store.load_items(), args, cfg.today, person)
    finally:
        store.close()
    if args.json:
        print(json.dumps([i.to_dict(cfg.today) for i in items], indent=2, ensure_ascii=False))
        return EXIT_OK
    if not items:
        print("No matching items.")
        return EXIT_OK
    _print_table(items, cfg.today)
    return EXIT_OK


def _status_text(it: ItemRow, today: date) -> str:
    if it.is_overdue(today):
        n = it.days_overdue(today)
        return f"OVERDUE ({n} day{'s' if n != 1 else ''})"
    return it.status


def _print_table(items: list[ItemRow], today: date):
    color = sys.stdout.isatty()
    rows = []
    for it in items:
        task = it.task if len(it.task) <= 48 else it.task[:47] + "…"
        owner = it.owner_display()
        owner = owner if len(owner) <= 34 else owner[:33] + "…"
        src = it.last_source or ""
        if it.mention_count > 1:
            src += f" (+{it.mention_count - 1})"
        due = it.due_date or (f"? {it.due_raw}" if it.due_raw else "—")
        rows.append((str(it.id), owner, task, due, _status_text(it, today), src, it.is_overdue(today)))
    head = ("ID", "Owner", "Task", "Due", "Status", "Source")
    widths = [max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(head)]
    widths[3] = min(widths[3], 24)
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*head))
    for r in rows:
        line = fmt.format(*(c[:widths[i]] for i, c in enumerate(r[:6])))
        print(f"\033[31m{line}\033[0m" if color and r[6] else line)
    print(f"\n{len(rows)} item(s)")


# --------------------------------------------------------------------------- done
def cmd_done(args, cfg: Config) -> int:
    store = Store(cfg.db)
    try:
        result = store.mark_done_manual(args.id)
        row = store.item_raw(args.id)
    finally:
        store.close()
    if result == "missing":
        raise CliError(EXIT_NO_ITEM, f"no item with id {args.id}")
    if result == "already":
        print(f"Item #{args.id} is already done: {row['task']}")
    else:
        print(f"Marked #{args.id} done: {row['task']}")
    return EXIT_OK


# --------------------------------------------------------------------------- followup
def cmd_followup(args, cfg: Config) -> int:
    store = Store(cfg.db)
    try:
        items = store.load_items()
        if args.all:
            people = sorted({p for i in items if i.status == "open" and i.owner_status == "resolved" for p in i.owners})
        else:
            people = [_resolve_person(store, args.owner)]
        if args.to and len(people) > 1:
            raise CliError(EXIT_USAGE, "--to can only be used with a single owner")

        gateway = None
        if not (args.dry_run or args.emit_json):
            gateway = McpGmailGateway(cfg.gmail)   # fails fast if not configured / unsafe tool name

        worst = EXIT_OK
        for person in people:
            email = compose(person, items, cfg.today, cfg.sender_name)
            if email is None:
                print(f"{person}: nothing to follow up")
                continue
            to = args.to or store.person_email(person) or cfg.people.get(person)
            if args.dry_run:
                DryRunGateway(print).create_draft(to or "<no email on file>", email.subject, email.body)
                continue
            if not to:
                print(f"error: no email address for {person}. Add it to people.toml or pass --to.", file=sys.stderr)
                worst = worst or EXIT_NO_EMAIL
                continue
            if args.emit_json:
                EmitJsonGateway(print, email.item_ids).create_draft(to, email.subject, email.body)
                continue
            if store.followup_today(person, cfg.today) and not args.force:
                print(f"{person}: a draft was already created today — use --force to create another")
                continue
            try:
                draft_id = gateway.create_draft(to, email.subject, email.body)
            except GatewayError as e:
                raise CliError(EXIT_GMAIL, str(e))
            store.record_followup(person, draft_id, email.item_ids, cfg.today)
            print(f"{person}: Gmail draft created ({email.overdue} overdue, {email.open} open"
                  + (f", {email.confirm} to confirm" if email.confirm else "") + f") — draft id {draft_id}. Not sent.")
        return worst
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
