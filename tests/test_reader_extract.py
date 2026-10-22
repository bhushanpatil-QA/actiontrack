from pathlib import Path

from actiontrack.notes.extract import extract
from actiontrack.notes.reader import Note, Skipped, read_note

from conftest import FIXTURES


def test_skip_reasons():
    expected = {
        "2026-09-09-notes-final-v2.md": "EMPTY_FILE",
        "scratch.md": "NOT_TEXT",
        "untitled.md": "MISSING_HEADER",
        "2026-10-03-sales-pipeline.md": "BAD_DATE",
    }
    for name, code in expected.items():
        res = read_note(FIXTURES / name)
        assert isinstance(res, Skipped) and res.reason == code, name


def test_front_matter_note():
    note = read_note(FIXTURES / "2026-09-16-standup.md")
    assert isinstance(note, Note)
    assert str(note.meeting_date) == "2026-09-16"
    assert note.attendees == ["Anita Desai", "Lena Fischer", "Meera Iyer"]


def test_missing_attendees_is_a_warning_not_a_skip():
    note = read_note(FIXTURES / "2026-09-23-standup.md")
    assert isinstance(note, Note)
    assert [w.code for w in note.warnings] == ["MISSING_ATTENDEES"]


def test_quotes_code_and_comments_are_ignored():
    raw, _ = extract(read_note(FIXTURES / "2026-10-09-security-review.md"))
    texts = [m.text for m in raw]
    assert not any("hardware tokens" in t or "<date>" in t for t in texts)
    assert len(raw) == 4
    raw, _ = extract(read_note(FIXTURES / "2026-09-21-incident-review.md"))
    assert sum("refund" in m.text for m in raw) == 1


def test_bullet_variants_nesting_and_unsupported_formats():
    raw, warns = extract(read_note(FIXTURES / "2026-09-30-q4-offsite.md"))
    assert len(raw) == 7
    nested = next(m for m in raw if m.text.startswith("Karan to validate"))
    assert nested.parent_line is not None
    assert [w.code for w in warns] == ["UNSUPPORTED_ACTION_FORMAT"] * 2


def test_random_bytes_and_huge_file_do_not_crash(tmp_path: Path):
    junk = tmp_path / "junk.md"
    junk.write_bytes(bytes(range(256)) * 50)
    assert isinstance(read_note(junk), Skipped)
    big = tmp_path / "big.md"
    big.write_text("# Meeting: Big\nDate: 2026-10-01\nAttendees: Sam Okafor\n\n" +
                   "filler line of text\n" * 250_000 + "- [ ] Sam to finish by Friday\n", encoding="utf-8")
    raw, _ = extract(read_note(big))
    assert len(raw) == 1
