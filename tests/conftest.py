from pathlib import Path

import pytest

from actiontrack.cli import main

FIXTURES = Path(__file__).parent / "fixtures" / "notes"
TODAY = "2026-10-22"


@pytest.fixture
def run(tmp_path, capsys, monkeypatch):
    """Run the CLI in an isolated folder with its own database. Returns (exit_code, stdout, stderr)."""
    monkeypatch.chdir(tmp_path)

    def _run(*args: str):
        code = main(["--db", str(tmp_path / "at.db"), "--today", TODAY, *args])
        out = capsys.readouterr()
        return code, out.out, out.err

    return _run


@pytest.fixture
def ingested(run):
    code, out, _ = run("ingest", str(FIXTURES))
    assert code == 0, out
    return run
