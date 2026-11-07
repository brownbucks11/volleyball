#!/usr/bin/env python3
"""
Tournaments and practice slots, parsed from the schedule images the club posts (OCR text in
data/messages.json), merged with your own notes in tournaments.json.

Used by build_site.py.  Also a small CLI, like the Dugout's tournament.py:

  python tournaments.py --list
  python tournaments.py "Rumble at the Rock" --confirmed      # coach confirmed we're playing
  python tournaments.py "Rumble at the Rock" --official       # final schedule posted
  python tournaments.py "Rumble at the Rock" --note "Wave 2, check-in 7:30"
  python tournaments.py "Rumble at the Rock" --dates "Jan 30-31" --location "Rock Hill Sports Center"
  python tournaments.py --add "Extra Tourney" --dates "May 2" --location "Columbia, SC"
  python tournaments.py "Rumble at the Rock" --remove          # hide an auto-parsed one
"""

import argparse
import datetime as dt
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OVERRIDES = ROOT / "tournaments.json"
TEAM = "13U"

MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
MONTH_RE = r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
ORD = r"(?:st|nd|rd|th|\"|'|”|“)?"          # OCR often reads a superscript "th" as a quote mark
DATE_RE = re.compile(rf"\b{MONTH_RE}\.?\s*(\d{{1,2}}){ORD}(?:\s*[-—–]+\s*(?:{MONTH_RE}\.?\s*)?(\d{{1,2}}){ORD})?", re.I)
STATE_RE = re.compile(r",\s*(SC|NC|GA|TN|VA|FL|AL)\b")
VENUE_WORDS = r"(Center|Centre|Arena|Sportsplex|Complex|Gym|Convention|Stadium|Fieldhouse|Galleria|Pavilion|Expo)"
STOP_RE = re.compile(r"^(club fees|very important|the prv fee|additional costs|payment schedule)", re.I)
DAY_RE = re.compile(r"^(Sunday|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday)\s+(\d{1,2}(?::\d{2})?)\s*-\s*(\d{1,2}(?::\d{2})?)\s*(.*)$", re.I)


def norm(name):
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def _year_for(month, season_year, posted):
    """A schedule titled 2027 means the season; Aug–Dec dates in it are the year before."""
    if season_year:
        return season_year - 1 if month >= 8 else season_year
    return posted.year + (1 if month < posted.month - 6 else 0)


def _parse_dates(m, season_year, posted):
    mon = MONTHS[m.group(1).lower()[:3]]
    d1 = int(m.group(2))
    mon2 = MONTHS[m.group(3).lower()[:3]] if m.group(3) else mon
    d2 = int(m.group(4)) if m.group(4) else d1
    y = _year_for(mon, season_year, posted)
    start = dt.date(y, mon, d1)
    end = dt.date(_year_for(mon2, season_year, posted), mon2, d2)
    if end < start:
        end = start
    label = start.strftime("%b %-d") + ("" if end == start else (f"-{end.day}" if end.month == start.month else end.strftime(" – %b %-d")))
    return start.isoformat(), end.isoformat(), label


def _split_no_date(line):
    """'Upstate Challenge Star Center, Spartanburg, SC' -> ('Upstate Challenge', 'Star Center, Spartanburg, SC')."""
    m = re.search(rf"\b((?:[A-Z][\w&'.]*\s+){{0,2}}[A-Z][\w&'.]*\s+{VENUE_WORDS}\b.*)$", line)
    if m and m.start() > 0:
        return line[:m.start()].strip(" |-"), m.group(1).strip()
    if "," in line:
        head, tail = line.split(",", 1)
        words = head.split()
        return " ".join(words[:-2]) or head, (" ".join(words[-2:]) + "," + tail).strip()
    return line.strip(), ""


def parse_schedule(text, posted_ts):
    """Rows of a '... Tournament Schedule' image -> list of tournaments. Empty if it isn't one."""
    if not re.search(r"tournament schedule", text, re.I):
        return []
    posted = dt.datetime.fromtimestamp(posted_ts)
    ym = re.search(r"\b(20\d{2})\b", text.split("\n", 3)[0:3].__str__())
    season_year = int(ym.group(1)) if ym else None
    tentative = bool(re.search(r"tentative", text, re.I))
    out = []
    for raw in text.split("\n"):
        line = re.sub(r"\s+", " ", raw).strip(" |")
        if not line or STOP_RE.match(line):
            if STOP_RE.match(line):
                break
            continue
        if re.search(r"tournament schedule|^tournament names?|^palmetto rage volleyball$|^dates\b", line, re.I):
            continue
        m = DATE_RE.search(line)
        if m:
            name = line[:m.start()].strip(" |-:")
            location = line[m.end():].strip(" |-:,")
            start, end, label = _parse_dates(m, season_year, posted)
        elif STATE_RE.search(line) or re.search(VENUE_WORDS, line):
            name, location = _split_no_date(line)
            start = end = None
            label = "TBA"
        else:
            continue
        if len(name) < 3:
            continue
        out.append({"name": name, "key": norm(name), "dates": label, "start": start, "end": end,
                    "location": location, "status": "tentative" if tentative else "listed"})
    return out


def parse_practices(text):
    """'Tuesday 5-7 13U Madisen & Eli 14U ...' -> [{'day': 'Tuesday', 'time': '5-7 PM', 'coaches': 'Madisen & Eli'}]."""
    if not re.search(r"practice schedule", text, re.I):
        return []
    pm = bool(re.search(r"times are PM", text, re.I))
    out = []
    for raw in text.split("\n"):
        m = DAY_RE.match(raw.strip())
        if not m:
            continue
        rest = m.group(4)
        hit = re.search(rf"\b{TEAM}\b\s*([^|]*?)(?=\s+\d{{1,2}}U\b|$)", rest, re.I)
        if hit:
            out.append({"day": m.group(1).title(), "time": f"{m.group(2)}-{m.group(3)}" + (" PM" if pm else ""),
                        "coaches": hit.group(1).strip(" |")})
    return out


def collect(messages):
    """Scan OCR'd pages in kept messages; latest schedule image wins for tournaments."""
    tournaments, practices, source = [], [], None
    for m in sorted(messages, key=lambda x: x["ts"]):            # oldest first so the newest overwrites
        for a in m.get("attachments", []):
            for p in a.get("pages", []):
                if not p.get("relevant") or not p.get("text"):
                    continue
                t = parse_schedule(p["text"], m["ts"])
                if t:
                    tournaments, source = t, {"url": p["url"], "ts": m["ts"], "by": m["name"], "msg_id": m["id"]}
                pr = parse_practices(p["text"])
                if pr:
                    practices = pr
    return tournaments, practices, source


def apply_overrides(tournaments):
    ov = json.loads(OVERRIDES.read_text()) if OVERRIDES.exists() else {}
    by_key = {t["key"]: t for t in tournaments}
    for name, o in ov.get("tournaments", {}).items():
        k = norm(name)
        t = by_key.get(k)
        if o.get("remove"):
            if t:
                tournaments.remove(t)
            continue
        if not t:
            t = {"name": name, "key": k, "dates": "TBA", "start": None, "end": None, "location": "", "status": "listed"}
            tournaments.append(t)
            by_key[k] = t
        for f in ("dates", "location", "notes", "link", "start", "end"):
            if f in o:
                t[f] = o[f]
        if o.get("official"):
            t["status"] = "official"
        elif o.get("confirmed"):
            t["status"] = "confirmed"
        if "dates" in o and "start" not in o:
            m = DATE_RE.search(o["dates"])
            if m:
                t["start"], t["end"], _ = _parse_dates(m, ov.get("season_year"), dt.datetime.now())
    tournaments.sort(key=lambda t: (t["start"] is None, t["start"] or ""))
    return tournaments


def _load_ov():
    return json.loads(OVERRIDES.read_text()) if OVERRIDES.exists() else {"tournaments": {}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name", nargs="?")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--add", metavar="NAME")
    ap.add_argument("--confirmed", action="store_true")
    ap.add_argument("--not-confirmed", action="store_true")
    ap.add_argument("--official", action="store_true")
    ap.add_argument("--not-official", action="store_true")
    ap.add_argument("--note")
    ap.add_argument("--dates")
    ap.add_argument("--location")
    ap.add_argument("--link")
    ap.add_argument("--remove", action="store_true")
    a = ap.parse_args()

    msgs = json.loads((ROOT / "data" / "messages.json").read_text()) if (ROOT / "data" / "messages.json").exists() else []
    if a.list or not (a.name or a.add):
        ts, pr, src = collect(msgs)
        for t in apply_overrides(ts):
            print(f"{t['dates']:>14}  {t['status']:<10} {t['name']}  — {t['location']}" + (f"  [{t['notes']}]" if t.get("notes") else ""))
        for p in pr:
            print(f"practice: {p['day']} {p['time']} ({p['coaches']})")
        return
    ov = _load_ov()
    name = a.add or a.name
    o = ov.setdefault("tournaments", {}).setdefault(name, {})
    if a.confirmed: o["confirmed"] = True
    if a.not_confirmed: o.pop("confirmed", None)
    if a.official: o["official"] = True
    if a.not_official: o.pop("official", None)
    if a.note is not None: o["notes"] = a.note
    if a.dates: o["dates"] = a.dates
    if a.location: o["location"] = a.location
    if a.link: o["link"] = a.link
    if a.remove: o["remove"] = True
    OVERRIDES.write_text(json.dumps(ov, indent=2) + "\n")
    print(f"updated {OVERRIDES.name}: {name} -> {o}")


if __name__ == "__main__":
    main()
