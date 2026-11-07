"""Unit tests for the filter logic in fetch.py.  Run: python -m pytest -q"""
import json
from pathlib import Path

import fetch

CFG = json.loads((Path(__file__).parent / "config.json").read_text())
KEEP, EXCL, SENDERS = fetch.rules(CFG)


def why(text, name="Director", mode="exclude"):
    return fetch.why({"id": "1", "name": name, "text": text, "created_at": 0}, KEEP, EXCL, SENDERS, mode)


def test_untagged_announcements_are_kept():
    for t in ["First Week of Practices", "Picture day is Nov 15", "Director updated the time for the event 'Open Gym'", ""]:
        assert why(t) == "no other age group named", t


def test_other_age_groups_are_dropped():
    for t in ["12u will be split into two teams", "created event '12u Girls Only!'", "15U bracket posted",
              "14-U parents meeting", "the 16s play Sunday", "10U and 11U tryouts"]:
        assert why(t) is None, t


def test_13u_always_kept_even_with_other_ages():
    for t in ["12u and 13u practice at 6", "13U tourney", "13-U parents", "all 13s teams"]:
        assert why(t), t
    assert why("12u and 13u practice").startswith("mentions")


def test_club_wide_wording_overrides_exclusion():
    assert why("ALL TEAMS 12u through 18u: picture day") == 'mentions "ALL TEAMS"'


def test_no_false_hits_inside_words():
    assert why("Court 113u is closed")            # not an age group
    assert why("130 chairs and 115 shirts")       # numbers, not ages


def test_include_mode_requires_a_keyword():
    assert why("First Week of Practices", mode="include") is None
    assert why("13U practice", mode="include") == 'mentions "13U"'


def test_sender_whitelist_is_case_insensitive():
    keep, excl, senders = fetch.rules({"senders": ["Coach Amy"]})
    assert fetch.why({"name": "COACH AMY", "text": "12u only"}, keep, excl, senders) == "from COACH AMY"


def test_slim_keeps_attachment_urls():
    msg = {"id": "9", "name": "A", "created_at": 5, "text": "x",
           "attachments": [{"type": "image", "url": "https://i.groupme.com/x"}, {"type": "mentions"},
                           {"type": "event", "event_id": "E1", "view": "full"}],
           "favorited_by": ["1", "2"]}
    s = fetch.slim(msg, "Club", "r")
    assert s["attachments"] == [{"type": "image", "url": "https://i.groupme.com/x"}, {"type": "event", "event_id": "E1", "view": "full"}]
    assert s["likes"] == 2 and s["group"] == "Club"


def test_event_filter_uses_name_and_description():
    assert fetch.why({"name": "", "text": "Open Gym\nbring water"}, KEEP, EXCL, SENDERS)
    assert fetch.why({"name": "", "text": "12u Girls Only!\n"}, KEEP, EXCL, SENDERS) is None


def test_file_attachments_are_captured():
    msg = {"id": "2", "name": "A", "created_at": 1, "text": "schedule attached",
           "attachments": [{"type": "file", "file_id": "F1", "file_name": "Week1.pdf"}]}
    assert fetch.slim(msg, "Sched", "r")["attachments"] == [{"type": "file", "file_id": "F1", "name": "Week1.pdf"}]
