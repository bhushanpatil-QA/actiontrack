"""Acceptance tests from SDD §10 (AT-01 … AT-18), run against the sample notes with today = 2026-10-22."""
import json
import shutil
import sys
from pathlib import Path

from conftest import FIXTURES


def items(run, *args):
    code, out, err = run("list", "--json", *args)
    assert code == 0, err
    return json.loads(out)


def test_at01_ingest_report(run):
    code, out, _ = run("ingest", str(FIXTURES), "--json")
    rep = json.loads(out)
    assert code == 0
    assert rep["ingested"] == 31
    assert {s["file"]: s["reason"] for s in rep["skipped"]} == {
        "2026-09-09-notes-final-v2.md": "EMPTY_FILE", "scratch.md": "NOT_TEXT",
        "untitled.md": "MISSING_HEADER", "2026-10-03-sales-pipeline.md": "BAD_DATE"}
    assert rep["ignored"] == ["2026-09-19-offsite-draft.txt"]


def test_at02_ingest_is_idempotent(ingested):
    before = items(ingested, "--status", "all")
    code, out, _ = ingested("ingest", str(FIXTURES), "--json")
    rep = json.loads(out)
    assert rep["items"]["new"] == 0 and rep["items"]["merged_mentions"] == 0
    assert items(ingested, "--status", "all") == before


def test_at03_duplicates_merge(ingested):
    quote = [i for i in items(ingested, "--status", "all", "--owner", "Priya Nair") if "revised quote" in i["task"]]
    assert len(quote) == 1
    assert quote[0]["status"] == "done" and quote[0]["mentions"] == 3


def test_e2_e3_due_date_on_merge(ingested):
    all_items = items(ingested, "--status", "all")
    quotes = next(i for i in all_items if i["task"].startswith("Collect 5 customer quotes"))
    one_pager = next(i for i in all_items if i["task"].startswith("Prepare the renewal one-pager"))
    assert quotes["due_date"] == "2026-10-02" and quotes["mentions"] == 2      # later due wins
    assert one_pager["due_date"] == "2026-10-02" and one_pager["mentions"] == 2  # later mention has no due → kept


def test_distinct_tasks_do_not_merge(ingested):
    tasks = [i["task"] for i in items(ingested, "--status", "all", "--owner", "Anita Desai")]
    assert "Share first wireframes" in tasks and "Share updated wireframes" in tasks


def test_at04_ambiguous_owner_filter(run, ingested):
    code, _, err = ingested("list", "--owner", "Priya")
    assert code == 5 and "Priya Nair" in err and "Priya Shah" in err


def test_at05_ambiguous_items(ingested):
    amb = items(ingested, "--ambiguous")
    by_task = {i["task"]: sorted(i["candidates"]) for i in amb}
    assert by_task["Sign off the cutover checklist"] == ["Priya Nair", "Priya Shah"]
    assert by_task["Review Anita's PR"] == ["Rahul Mehta", "Rahul Verma"]


def test_at06_unassigned(ingested):
    tasks = {i["task"] for i in items(ingested, "--unassigned")}
    assert {"Update the CRM notes", "Write the design-system changelog"} <= tasks


def test_at08_overdue_for_sam(ingested):
    got = {(i["task"], i["due_date"]) for i in items(ingested, "--owner", "Sam Okafor", "--status", "overdue")}
    assert got == {
        ("Run the dry-run migration on staging", "2026-09-22"), ("Write the postmortem", "2026-09-22"),
        ("Schedule cutover", "2026-10-10"), ("Cut cloud costs by 10% — proposal", "2026-10-09"),
        ("Confirm rollback plan", "2026-10-09"), ("Propose the on-call rotation", "2026-10-15"),
        ("Enable MFA on all admin accounts", "2026-10-16")}


def test_at09_fake_tasks_never_ingested(ingested):
    tasks = " | ".join(i["task"] for i in items(ingested, "--status", "all"))
    assert "hardware tokens" not in tasks and "chase the vendor" not in tasks and "Book the venue" not in tasks
    budget = [i for i in items(ingested, "--status", "all", "--owner", "Sam Okafor") if "cloud budget" in i["task"]]
    assert budget[0]["status"] == "done"


def test_at10_due_before(ingested):
    got = items(ingested, "--status", "all", "--due-before", "2026-09-15")
    assert got and all(i["due_date"] and i["due_date"] < "2026-09-15" for i in got)


def test_at11_at12_done(ingested):
    target = items(ingested, "--owner", "Sam Okafor")[0]["id"]
    assert ingested("done", str(target))[0] == 0
    assert target in [i["id"] for i in items(ingested, "--status", "done")]
    code, out, _ = ingested("done", str(target))
    assert code == 0 and "already done" in out
    assert ingested("done", "99999")[0] == 4


def test_e24_manual_done_is_sticky(ingested, tmp_path):
    folder = tmp_path / "notes"
    shutil.copytree(FIXTURES, folder)
    ingested("ingest", str(folder))
    vpn = next(i for i in items(ingested, "--owner", "Sam Okafor") if "VPN" in i["task"])
    ingested("done", str(vpn["id"]))
    (folder / "2026-10-20-standup.md").write_text(
        "# Meeting: Team standup\nDate: 2026-10-20\nAttendees: Sam Okafor\n\n## Action items\n"
        "- [ ] Sam to share the VPN guide\n", encoding="utf-8")
    code, out, _ = ingested("ingest", str(folder), "--json")
    rep = json.loads(out)
    assert "REOPEN_CONFLICT" in {w["code"] for w in rep["warnings"]}
    still = next(i for i in items(ingested, "--status", "all") if i["id"] == vpn["id"])
    assert still["status"] == "done"


def test_at13_dry_run_email(ingested):
    code, out, _ = ingested("followup", "Sam Okafor", "--dry-run")
    assert code == 0
    assert "7 overdue, 5 open" in out
    assert out.index("Overdue") < out.index("Open") < out.index("No due date yet")


def test_ambiguous_items_in_confirm_section(ingested):
    code, out, _ = ingested("followup", "Priya Nair", "--dry-run")
    assert "Please confirm" in out and "Sign off the cutover checklist" in out


def test_at16_no_email(ingested):
    code, _, err = ingested("followup", "Lena Fischer", "--emit-json")
    assert code == 6 and "no email" in err


def test_emit_json_payload(ingested):
    code, out, _ = ingested("followup", "Lena Fischer", "--emit-json", "--to", "lena@example.com")
    payload = json.loads(out)
    assert code == 0 and payload["to"] == "lena@example.com" and payload["item_ids"]


def test_at17_nothing_to_follow_up(ingested, tmp_path):
    folder = tmp_path / "solo"
    folder.mkdir()
    (folder / "n.md").write_text("# Meeting: X\nDate: 2026-10-01\nAttendees: Zed Zulu\n\n- [x] Zed to do it\n",
                                 encoding="utf-8")
    ingested("ingest", str(folder))
    code, out, _ = ingested("followup", "Zed Zulu", "--dry-run")
    assert code == 0 and "nothing to follow up" in out


def _mcp_config(tmp_path: Path, tool: str = "create_draft") -> Path:
    log = tmp_path / "calls.jsonl"
    server = Path(__file__).parent / "fake_gmail_mcp.py"
    cfg = tmp_path / "actiontrack.toml"
    cfg.write_text(
        "[gmail_mcp]\ntransport = \"stdio\"\n"
        f"command = {json.dumps(sys.executable)}\nargs = [{json.dumps(str(server))}]\n"
        f"env = {{ FAKE_MCP_LOG = {json.dumps(str(log))} }}\n"
        f"draft_tool = {json.dumps(tool)}\n", encoding="utf-8")
    return log


def test_at14_only_draft_tool_is_called(ingested, tmp_path):
    log = _mcp_config(tmp_path)
    code, out, err = ingested("followup", "Sam Okafor", "--to", "sam@example.com")
    assert code == 0, err
    assert "draft-001" in out and "Not sent" in out
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert [c["tool"] for c in calls] == ["create_draft"]
    assert calls[0]["to"] == "sam@example.com" and "7 overdue" in calls[0]["subject"]
    # second draft on the same day needs --force
    code, out, _ = ingested("followup", "Sam Okafor", "--to", "sam@example.com")
    assert "already created today" in out and len(log.read_text().splitlines()) == 1


def test_at15_send_tool_is_refused(ingested, tmp_path):
    log = _mcp_config(tmp_path, tool="send_email")
    code, _, err = ingested("followup", "Sam Okafor", "--to", "sam@example.com")
    assert code == 3 and "never sends" in err
    assert not log.exists()


def test_at18_bad_files_do_not_crash(run, tmp_path):
    folder = tmp_path / "bad"
    folder.mkdir()
    (folder / "rand.md").write_bytes(bytes(range(256)) * 40)
    (folder / "front.md").write_text("---\ndate: [unclosed\n---\n- [ ] x\n", encoding="utf-8")
    (folder / "ok.md").write_text("# Meeting: ok\nDate: 2026-10-01\nAttendees: Sam Okafor\n\n- [ ] Sam to go by Friday\n",
                                  encoding="utf-8")
    code, out, _ = run("ingest", str(folder), "--json")
    rep = json.loads(out)
    assert code == 0 and rep["ingested"] == 1 and len(rep["skipped"]) == 2


def test_folder_not_found(run):
    assert run("ingest", "does-not-exist")[0] == 2
