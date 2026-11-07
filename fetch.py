#!/usr/bin/env python3
"""
Poll the club's GroupMe group(s): keep the announcements that apply to 13U, and pull the
group calendar's events too.  Output goes to data/ for build_site.py.

Needs:    GROUPME_TOKEN   (env; your access token from https://dev.groupme.com)
Optional: GROUPME_BOT_ID  (env; a bot in a private group - kept messages are reposted there)
Config:   config.json     (sources, keywords, exclude patterns, senders, mode)

  python fetch.py --list-groups     show your groups and their IDs
  python fetch.py --dry-run         show what would be kept, change nothing
  python fetch.py --backfill 200    on a group's first run, scan the last N messages
  python fetch.py --rescan          forget cursors and re-apply the rules to the last --backfill messages
"""

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

import ocr

API = "https://api.groupme.com/v3"
ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "config.json"
DATA = ROOT / "data"
MESSAGES = DATA / "messages.json"   # kept messages, newest first
SKIPPED = DATA / "skipped.json"     # last 100 messages that were NOT kept (for tuning the rules)
EVENTS = DATA / "events.json"       # every calendar event seen, with a "kept" flag
STATE = DATA / "state.json"         # last message id seen per group


def jload(p, default):
    try:
        return json.loads(p.read_text())
    except FileNotFoundError:
        return default


def jsave(p, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=1, ensure_ascii=False) + "\n")


def api(token, path, **params):
    params["token"] = token
    r = requests.get(f"{API}{path}", params=params, timeout=30)
    if r.status_code == 304:
        return None
    r.raise_for_status()
    body = r.json()
    return body.get("response", body)


def list_groups(token):
    for g in sorted(api(token, "/groups", per_page=100), key=lambda g: g["name"].lower()):
        print(f"{g['id']:>10}  {g['name']}")


def topics(token, gid):
    """id -> topic name for a group's Topics (subgroups); {} if the endpoint fails."""
    try:
        subs = api(token, f"/groups/{gid}/subgroups", per_page=100) or []
        return {str(t["id"]): t.get("topic") or t.get("name") or str(t["id"]) for t in subs}
    except requests.HTTPError as e:
        print(f"subgroups list failed for {gid}: {e}", file=sys.stderr)
        return {}


# ---------------------------------------------------------------- messages

def new_messages(token, gid, after_id, backfill):
    if after_id is None:
        out, before = [], None
        while len(out) < backfill:
            resp = api(token, f"/groups/{gid}/messages", limit=100, **({"before_id": before} if before else {}))
            if not resp or not resp["messages"]:
                break
            out.extend(resp["messages"])
            before = resp["messages"][-1]["id"]
            if len(resp["messages"]) < 100:
                break
        return list(reversed(out[:backfill]))      # oldest first
    out, cursor = [], after_id
    while True:
        resp = api(token, f"/groups/{gid}/messages", after_id=cursor, limit=100)
        if not resp or not resp["messages"]:
            break
        out.extend(resp["messages"])
        cursor = resp["messages"][-1]["id"]
        if len(resp["messages"]) < 100:
            break
    return out


def rules(cfg):
    """Returns (keep_patterns, exclude_patterns, senders)."""
    keep = [re.compile(r"\b" + re.escape(k) + r"\b", re.I) for k in cfg.get("keywords", [])]
    keep += [re.compile(p, re.I) for p in cfg.get("regex", [])]
    excl = [re.compile(p, re.I) for p in cfg.get("exclude_regex", [])]
    return keep, excl, {s.lower() for s in cfg.get("senders", [])}


def why(msg, keep, excl, senders, mode="exclude"):
    """Return the reason this message is kept, or None.

    mode "exclude" (default): keep everything unless an exclude pattern hits and no keep pattern does.
    mode "include": keep only if a keep pattern or whitelisted sender hits.
    """
    name = msg.get("name") or ""
    text = msg.get("text") or ""
    if name.lower() in senders:
        return f"from {name}"
    hit = next((m for p in keep if (m := p.search(text))), None)
    if hit:
        return f"mentions \"{hit.group(0)}\""
    if mode == "include":
        return None
    ex = next((m for p in excl if (m := p.search(text))), None)
    if ex:
        return None
    return "no other age group named"


def attachments(msg):
    """Flatten GroupMe attachments into something the page can render."""
    out = []
    for a in msg.get("attachments", []):
        t = a.get("type")
        if t in ("image", "video", "linked_image") and a.get("url"):
            out.append({"type": "image" if t == "linked_image" else t, "url": a["url"]})
        elif t == "file" and a.get("file_id"):
            out.append({"type": "file", "file_id": a["file_id"], "name": a.get("file_name") or a.get("name") or ""})
        elif t == "event" and a.get("event_id"):
            out.append({"type": "event", "event_id": a["event_id"], "view": a.get("view")})
        elif t == "poll" and a.get("poll_id"):
            out.append({"type": "poll", "poll_id": a["poll_id"]})
    return out


def slim(msg, group_name, reason):
    return {
        "id": msg["id"],
        "group": group_name,
        "name": msg.get("name") or "?",
        "avatar": msg.get("avatar_url"),
        "ts": msg["created_at"],
        "text": msg.get("text") or "",
        "attachments": attachments(msg),
        "likes": len(msg.get("favorited_by", [])),
        "reason": reason,
    }


FILES = ROOT / "docs" / "files"


def _ext(name, ctype):
    return Path(name or "").suffix.lower() or {"application/pdf": ".pdf", "image/png": ".png", "image/jpeg": ".jpg",
                                                "image/gif": ".gif", "image/webp": ".webp"}.get((ctype or "").split(";")[0], ".bin")


def download_file(token, gid, att):
    """GroupMe files need the token; fetch once into docs/files so the page can link them."""
    FILES.mkdir(parents=True, exist_ok=True)
    existing = next(FILES.glob(att["file_id"] + ".*"), None)
    if existing:
        att["url"] = f"files/{existing.name}"
        return existing
    r = requests.get(f"https://file.groupme.com/v1/{gid}/files/{att['file_id']}", params={"token": token}, timeout=60)
    if r.status_code != 200:
        print(f"file {att['file_id']} download failed: {r.status_code}", file=sys.stderr)
        return None
    name = att.get("name") or ""
    m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)', r.headers.get("content-disposition", ""))
    if m and not name:
        name = m.group(1)
    ext = _ext(name, r.headers.get("content-type"))
    att["name"] = att.get("name") or name or (att["file_id"] + ext)
    dest = FILES / (att["file_id"] + ext)
    dest.write_bytes(r.content)
    att["url"] = f"files/{dest.name}"
    return dest


def download_image(att):
    """Images are public, but a local copy lets us OCR them and serve them from the page."""
    FILES.mkdir(parents=True, exist_ok=True)
    key = re.sub(r"[^a-f0-9]", "", att["url"].split("/uploads/")[-1].split("/")[0]) or re.sub(r"\W", "", att["url"])[-32:]
    existing = next(FILES.glob(key + ".*"), None)
    if existing:
        att["local"] = f"files/{existing.name}"
        return existing
    try:
        r = requests.get(att["url"], timeout=60)
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"image download failed {att['url']}: {e}", file=sys.stderr)
        return None
    dest = FILES / (key + _ext("", r.headers.get("content-type")))
    dest.write_bytes(r.content)
    att["local"] = f"files/{dest.name}"
    return dest


def judge_pages(att, path, keep, excl, senders, mode):
    """OCR an image or each PDF page and record which pages 13U cares about."""
    pages = []
    if path.suffix.lower() == ".pdf":
        for n, text, png in ocr.pdf_pages(path, FILES, path.stem):
            pages.append({"n": n, "url": f"files/{png.name}", "text": text[:400]})
    else:
        text = ocr.ocr_image(path)
        pages.append({"n": 1, "url": att.get("local") or att["url"], "text": (text or "")[:400], "unread": text is None})
    for pg in pages:
        r = why({"name": "", "text": pg["text"]}, keep, excl, senders, mode)
        pg["relevant"] = bool(r) or pg.get("unread", False)
        pg["reason"] = r or ("couldn't read it" if pg.get("unread") else "names another age group")
    att["pages"] = pages
    att["relevant"] = any(p["relevant"] for p in pages)


def post_bot(bot_id, text):
    for i in range(0, len(text), 950):
        requests.post(f"{API}/bots/post", json={"bot_id": bot_id, "text": text[i:i + 950]}, timeout=30).raise_for_status()


# ---------------------------------------------------------------- calendar events

def fetch_events(token, gid, gname, keep, excl, senders, mode):
    """All events from 60 days ago onward, with description + location, and whether 13U cares."""
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        listing = api(token, f"/conversations/{gid}/events/list", end_at=since, limit=100) or {}
    except requests.HTTPError as e:
        print(f"events list failed for {gname}: {e}", file=sys.stderr)
        return []
    out = []
    for ev in listing.get("events", []):
        full = ev
        try:
            shown = api(token, f"/conversations/{gid}/events/show", event_id=ev["event_id"]) or {}
            full = shown.get("event", shown) or ev
        except requests.HTTPError:
            pass
        text = f"{full.get('name', '')}\n{full.get('description', '') or ''}"
        loc = full.get("location") or {}
        item = {
            "event_id": full["event_id"],
            "group": gname,
            "name": full.get("name", ""),
            "description": full.get("description") or "",
            "start": full.get("start_at"),
            "end": full.get("end_at"),
            "all_day": bool(full.get("is_all_day")),
            "timezone": full.get("timezone"),
            "location": {"name": loc.get("name"), "address": loc.get("address"), "lat": loc.get("lat"), "lng": loc.get("lng")} if loc else None,
            "image": full.get("image_url"),
            "going": len(full.get("going") or []),
            "updated": full.get("updated_at"),
            "deleted": bool(full.get("deleted_at")),
        }
        item["reason"] = f"from the {gname} chat" if mode == "all" else why({"name": "", "text": text}, keep, excl, senders, mode)
        item["kept"] = item["reason"] is not None
        out.append(item)
    return out


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list-groups", action="store_true")
    ap.add_argument("--list-topics", metavar="GROUP_ID", help="show a group's Topics (sub-chats) and their IDs")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--backfill", type=int, default=50, help="messages to scan on a group's first run")
    ap.add_argument("--rescan", action="store_true", help="forget cursors and re-scan --backfill messages")
    args = ap.parse_args()

    token = os.environ.get("GROUPME_TOKEN")
    if not token:
        sys.exit("GROUPME_TOKEN is not set")
    cfg = jload(CONFIG, None) or sys.exit("config.json missing")

    if args.list_groups:
        list_groups(token)
        return
    if args.list_topics:
        for tid, name in topics(token, args.list_topics).items():
            print(f"{tid:>10}  {name}")
        return

    keep, excl, senders = rules(cfg)
    mode = cfg.get("mode", "exclude")
    state = {} if args.rescan else jload(STATE, {})
    kept = jload(MESSAGES, [])
    seen = {m["id"] for m in kept}
    skipped = [] if args.rescan else [x for x in jload(SKIPPED, []) if x["id"] not in seen]
    bot = os.environ.get("GROUPME_BOT_ID")
    added = 0
    events = []

    topic_names = {}
    for src in cfg["sources"]:
        if src.get("topic_of"):
            topic_names.update(topics(token, src["topic_of"]))
    for src in cfg["sources"]:
        gid = str(src["group_id"])
        gname = src.get("name") or topic_names.get(gid) or gid
        print(f"== {gname} ({gid})")
        src_mode = "all" if src.get("filter") is False else mode
        msgs = new_messages(token, gid, state.get(gid), args.backfill)
        for m in msgs:
            if m.get("system") or m.get("sender_type") == "bot" or m["id"] in seen:
                continue
            r = f"from the {gname} chat" if src_mode == "all" else why(m, keep, excl, senders, mode)
            if r:
                item = slim(m, gname, r)
                if not args.dry_run:
                    for a in item["attachments"]:
                        path = download_file(token, gid, a) if a["type"] == "file" else (download_image(a) if a["type"] == "image" else None)
                        if path and path.suffix.lower() in (".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp"):
                            judge_pages(a, path, keep, excl, senders, mode)
                kept.append(item)
                seen.add(m["id"])
                added += 1
                print("KEEP ", r, "|", item["name"], "|", item["text"][:90].replace("\n", " "))
                if bot and not args.dry_run:
                    when = time.strftime("%a %m/%d %I:%M %p", time.localtime(m["created_at"]))
                    links = "".join("\n" + a["url"] for a in item["attachments"] if a.get("url"))
                    post_bot(bot, f"[{gname}] {item['name']} @ {when}\n{item['text']}{links}")
            elif not any(x["id"] == m["id"] for x in skipped):
                skipped.append(slim(m, gname, None))
        if msgs:
            state[gid] = msgs[-1]["id"]
        events.extend(fetch_events(token, gid, gname, keep, excl, senders, src_mode if src_mode == "all" else mode))

    kept.sort(key=lambda m: m["ts"], reverse=True)
    skipped = sorted(skipped, key=lambda m: m["ts"], reverse=True)[:100]
    events.sort(key=lambda e: e.get("start") or "")
    print(f"{added} new message(s) kept; {len(kept)} total; {sum(e['kept'] for e in events)}/{len(events)} events kept")
    if not args.dry_run:
        jsave(MESSAGES, kept)
        jsave(SKIPPED, skipped)
        jsave(EVENTS, events)
        jsave(STATE, state)


if __name__ == "__main__":
    main()
