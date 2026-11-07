"""Unit tests for the filter logic in fetch.py.  Run: python -m pytest -q"""
import json
from pathlib import Path

import fetch

CFG = json.loads((Path(__file__).parent / "config.json").read_text())
PATS, SENDERS = fetch.rules(CFG)


def m(text, name="Director"):
    return {"id": "1", "name": name, "text": text, "created_at": 0, "attachments": []}


def test_keeps_13u_variants():
    for t in ["13U practice at 6", "13u tourney", "13-U parents", "13 U travel info", "all 13s teams"]:
        assert fetch.why(m(t), PATS, SENDERS), t


def test_keeps_club_wide_wording():
    for t in ["ALL TEAMS: picture day", "Reminder for all age groups", "Everyone please pay dues", "Every team must RSVP"]:
        assert fetch.why(m(t), PATS, SENDERS), t


def test_drops_other_age_groups():
    for t in ["15U bracket posted", "12U practice moved", "14s and 16s play Sunday", "130 chairs needed"]:
        assert fetch.why(m(t), PATS, SENDERS) is None, t


def test_no_false_hit_inside_words():
    assert fetch.why(m("Court 113u is closed"), PATS, SENDERS) is None


def test_sender_whitelist_is_case_insensitive():
    coach = CFG["senders"][0]
    assert fetch.why(m("hi", name=coach.upper()), PATS, SENDERS) == f"from {coach.upper()}"


def test_reason_names_the_match():
    assert fetch.why(m("13U practice"), PATS, SENDERS) == 'matched "13U"'


def test_empty_text_is_dropped():
    assert fetch.why({"id": "1", "name": "x", "text": None, "created_at": 0}, PATS, SENDERS) is None


def test_slim_keeps_attachment_urls():
    msg = {"id": "9", "name": "A", "created_at": 5, "text": "x",
           "attachments": [{"type": "image", "url": "https://i.groupme.com/x"}, {"type": "mentions"}],
           "favorited_by": ["1", "2"]}
    s = fetch.slim(msg, "Club", "r")
    assert s["attachments"] == ["https://i.groupme.com/x"] and s["likes"] == 2 and s["group"] == "Club"
