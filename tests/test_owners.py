import pytest

from actiontrack.resolve.owners import Roster, resolve_owner, split_owner

ROSTER = ["Priya Nair", "Priya Shah", "Rahul Mehta", "Rahul Verma", "Anita Desai", "Sam Okafor",
          "Meera Iyer", "Karan Joshi", "Lena Fischer"]


def resolve(text, attendees):
    roster = Roster(ROSTER)
    owner, rest = split_owner(text)
    return resolve_owner(owner, [roster.get(a) for a in attendees], roster), rest


def test_first_name_unique_in_meeting():
    r, _ = resolve("Priya to draft the test plan", ["Karan Joshi", "Priya Shah", "Rahul Verma"])
    assert r.status == "resolved" and [p.full_name for p in r.owners] == ["Priya Shah"]


def test_both_namesakes_attended_is_ambiguous():
    r, _ = resolve("Priya to sign off the cutover checklist", ["Priya Shah", "Priya Nair", "Sam Okafor"])
    assert r.status == "ambiguous"
    assert sorted(p.full_name for p in r.candidates) == ["Priya Nair", "Priya Shah"]


@pytest.mark.parametrize("text, who", [("Priya N. to notify customers", "Priya Nair"),
                                       ("Priya S. to run the tests", "Priya Shah")])
def test_initial_disambiguates(text, who):
    r, _ = resolve(text, ["Priya Shah", "Priya Nair"])
    assert r.status == "resolved" and r.owners[0].full_name == who


def test_no_attendees_falls_back_to_roster():
    sam, _ = resolve("Sam to rotate the staging credentials", [])
    rahul, _ = resolve("Rahul to rebase the feature branch", [])
    assert sam.status == "resolved" and sam.owners[0].full_name == "Sam Okafor"
    assert rahul.status == "ambiguous"


def test_handle_and_co_owners_and_helper():
    r, rest = resolve("@anita: audit colour contrast by Mon", ["Anita Desai"])
    assert r.owners[0].full_name == "Anita Desai" and rest.startswith("audit")
    r, _ = resolve("Anita & Rahul to agree on the component API", ["Anita Desai", "Rahul Mehta"])
    assert [p.full_name for p in r.owners] == ["Anita Desai", "Rahul Mehta"]
    r, _ = resolve("Lena (with Priya) to collect user feedback", ["Lena Fischer", "Priya Nair"])
    assert [p.full_name for p in r.owners] == ["Lena Fischer"]
    assert [p.full_name for p in r.collaborators] == ["Priya Nair"]


@pytest.mark.parametrize("text, status", [
    ("Someone to update the CRM notes", "unassigned"), ("TBD to write the changelog", "unassigned"),
    ("Everyone to review the prototype", "group"), ("Team to decide on the launch date", "group"),
    ("Book the venue", "unassigned"), ("Send the slides to Priya", "unassigned"),
])
def test_no_owner(text, status):
    r, _ = resolve(text, ["Priya Nair"])
    assert r.status == status


def test_roster_match_for_cli():
    roster = Roster(ROSTER)
    assert len(roster.match("Priya")) == 2
    assert roster.match("Priya S.")[0].full_name == "Priya Shah"
    assert roster.match("karan")[0].full_name == "Karan Joshi"
    assert roster.match("rahul verma")[0].full_name == "Rahul Verma"
