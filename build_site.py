#!/usr/bin/env python3
"""Turn data/*.json into docs/data.js and docs/ics/13u.ics for the static page."""

import datetime as dt
import json
import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DOCS = ROOT / "docs"
cfg = json.loads((ROOT / "config.json").read_text())

URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")
ACTION_RE = re.compile(r"\b(deadline|due|by (tomorrow|tonight|friday|monday|tuesday|wednesday|thursday|saturday|sunday|\d)|"
                       r"must|required|please (fill|complete|sign|submit|pay|rsvp|make sure)|rsvp|sign[- ]?up|form|order)\b", re.I)


def load(name, default):
    try:
        return json.loads((ROOT / "data" / name).read_text())
    except FileNotFoundError:
        return default


messages = load("messages.json", [])
skipped = load("skipped.json", [])
events = [e for e in load("events.json", []) if e.get("kept") and not e.get("deleted")]

# ---- links: every URL mentioned in a kept message, newest mention first, deduped
links, seen = [], set()
for m in messages:
    for u in URL_RE.findall(m["text"]):
        key = u.rstrip(".,;:!?").rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        host = re.sub(r"^www\.", "", re.match(r"https?://([^/]+)", key).group(1))
        links.append({"url": key, "host": host, "ts": m["ts"], "by": m["name"], "msg_id": m["id"],
                      "context": m["text"][:160].replace("\n", " ")})

for m in messages:
    m["action"] = bool(ACTION_RE.search(m["text"]))

payload = {
    "title": cfg.get("site_title", "13U Announcements"),
    "subtitle": cfg.get("site_subtitle", ""),
    "built": int(time.time()),
    "keywords": cfg.get("keywords", []),
    "pinned": cfg.get("pinned_links", []),
    "ics": "ics/13u.ics",
    "messages": messages,
    "events": events,
    "links": links,
    "skipped": skipped,
}
DOCS.mkdir(exist_ok=True)
(DOCS / "data.js").write_text("window.DATA = " + json.dumps(payload, ensure_ascii=False) + ";\n")

# ---- calendar feed
def ics_dt(s, all_day):
    d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    return (";VALUE=DATE:" + d.strftime("%Y%m%d")) if all_day else (":" + d.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ"))


def esc(s):
    return (s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//13U Announcements//EN", "CALSCALE:GREGORIAN",
         f"X-WR-CALNAME:{esc(payload['title'])}", "X-PUBLISHED-TTL:PT1H"]
for e in events:
    if not e.get("start"):
        continue
    loc = e.get("location") or {}
    where = ", ".join(x for x in [loc.get("name"), loc.get("address")] if x)
    lines += ["BEGIN:VEVENT", f"UID:groupme-{e['event_id']}@13u", f"DTSTAMP{ics_dt(e.get('updated') or e['start'], False)}",
              f"DTSTART{ics_dt(e['start'], e['all_day'])}"]
    if e.get("end"):
        lines.append(f"DTEND{ics_dt(e['end'], e['all_day'])}")
    lines.append(f"SUMMARY:{esc(e['name'])}")
    if e.get("description"):
        lines.append(f"DESCRIPTION:{esc(e['description'])}")
    if where:
        lines.append(f"LOCATION:{esc(where)}")
    lines.append("END:VEVENT")
lines.append("END:VCALENDAR")
(DOCS / "ics").mkdir(exist_ok=True)
(DOCS / "ics" / "13u.ics").write_text("\r\n".join(lines) + "\r\n")

print(f"wrote data.js: {len(messages)} messages, {len(events)} events, {len(links)} links")
