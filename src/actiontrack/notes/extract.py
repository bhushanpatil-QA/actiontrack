"""Turn a Note's body into raw action-item mentions (SDD §6.2)."""
from __future__ import annotations

import re
from dataclasses import dataclass

from actiontrack.notes.reader import Note, Warning_

CHECKBOX = re.compile(r"^(?P<indent>\s*)[-*+]\s+\[(?P<mark>[ xX])\]\s+(?P<text>\S.*?)\s*$")
FENCE = re.compile(r"^\s*(```|~~~)")
ACTION_PREFIX = re.compile(r"^\s*(?:[-*+]\s+)?(?:\*\*)?(?:action|todo|ai)(?:\*\*)?\s*:\s*\S", re.IGNORECASE)
TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}")


@dataclass
class RawMention:
    line_no: int
    raw_line: str
    text: str
    checked: bool
    parent_line: int | None


def extract(note: Note) -> tuple[list[RawMention], list[Warning_]]:
    mentions: list[RawMention] = []
    warnings: list[Warning_] = []
    in_fence = False
    in_comment = False
    table_is_actions = False
    parents: list[tuple[int, int]] = []   # (indent width, line_no) stack for nesting

    for line_no, line in note.body:
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue

        line, in_comment = _strip_comments(line, in_comment)
        if not line.strip():
            continue
        if line.lstrip().startswith(">"):
            continue  # quoted text is someone else's words, not an agreed action

        if TABLE_ROW.match(line):
            if TABLE_SEP.match(line):
                continue
            cells = [c.strip().lower() for c in line.strip().strip("|").split("|")]
            if any(c in ("owner", "action", "action item", "who") for c in cells):
                table_is_actions = True
                continue
            if table_is_actions:
                warnings.append(Warning_("UNSUPPORTED_ACTION_FORMAT", f"table row not ingested: {line.strip()}", line_no))
            continue
        table_is_actions = False

        m = CHECKBOX.match(line)
        if m:
            indent = len(m["indent"].expandtabs(4))
            while parents and parents[-1][0] >= indent:
                parents.pop()
            parent = parents[-1][1] if parents else None
            parents.append((indent, line_no))
            mentions.append(RawMention(line_no, line.rstrip(), m["text"], m["mark"] in "xX", parent))
            continue

        if ACTION_PREFIX.match(line):
            warnings.append(Warning_("UNSUPPORTED_ACTION_FORMAT", f"'ACTION:' line not ingested: {line.strip()}", line_no))
    return mentions, warnings


def _strip_comments(line: str, in_comment: bool) -> tuple[str, bool]:
    out = []
    i = 0
    while i < len(line):
        if in_comment:
            end = line.find("-->", i)
            if end == -1:
                return "".join(out), True
            i = end + 3
            in_comment = False
        else:
            start = line.find("<!--", i)
            if start == -1:
                out.append(line[i:])
                break
            out.append(line[i:start])
            i = start + 4
            in_comment = True
    return "".join(out), in_comment
