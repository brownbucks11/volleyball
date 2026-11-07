#!/usr/bin/env python3
"""
Poll the club's GroupMe announcement group(s), keep the messages that matter to 13U,
and write them to data/messages.json for build_site.py.

Needs:   GROUPME_TOKEN   (env; your personal access token from https://dev.groupme.com)
Optional: GROUPME_BOT_ID (env; a bot in a private group - matches get reposted there too)
Config:  config.json     (source groups, keywords, senders)

  python fetch.py --list-groups     show your groups and their IDs
  python fetch.py --dry-run         show what would be kept, change nothing
  python fetch.py --backfill 200    on first run, also scan the last N messages
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

API = "https://api.groupme.com/v3"
ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "config.json"
DATA = ROOT / "data"
MESSAGES = DATA / "messages.json"   # kept messages, newest first
SKIPPED = DATA / "skipped.json"     # last 100 messages that were NOT kept (for tuning keywords)
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
    return r.json()["response"]


def list_groups(token):
    for g in sorted(api(token, "/groups", per_page=100), key=lambda g: g["name"].lower()):
        print(f"{g['id']:>10}  {g['name']}")


def new_messages(token, gid, after_id, backfill):
    if after_id is None:
        out = []
        before = None
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


def slim(msg, group_name, reason):
    return {
        "id": msg["id"],
        "group": group_name,
        "name": msg.get("name") or "?",
        "avatar": msg.get("avatar_url"),
        "ts": msg["created_at"],
        "text": msg.get("text") or "",
        "attachments": [a["url"] for a in msg.get("attachments", []) if a.get("url")],
        "likes": len(msg.get("favorited_by", [])),
        "reason": reason,
    }


def post_bot(bot_id, text):
    for i in range(0, len(text), 950):
        requests.post(f"{API}/bots/post", json={"bot_id": bot_id, "text": text[i:i + 950]}, timeout=30).raise_for_status()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list-groups", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--backfill", type=int, default=50, help="messages to scan on a group's first run")
    ap.add_argument("--rescan", action="store_true", help="forget cursors and re-scan --backfill messages (re-applies rules to skipped ones)")
    args = ap.parse_args()

    token = os.environ.get("GROUPME_TOKEN")
    if not token:
        sys.exit("GROUPME_TOKEN is not set")
    cfg = jload(CONFIG, None) or sys.exit("config.json missing")

    if args.list_groups:
        list_groups(token)
        return

    keep, excl, senders = rules(cfg)
    mode = cfg.get("mode", "exclude")
    state = {} if args.rescan else jload(STATE, {})
    kept = jload(MESSAGES, [])
    seen = {m["id"] for m in kept}
    skipped = [] if args.rescan else [x for x in jload(SKIPPED, []) if x["id"] not in seen]
    bot = os.environ.get("GROUPME_BOT_ID")
    added = 0

    for src in cfg["sources"]:
        gid, gname = str(src["group_id"]), src.get("name", str(src["group_id"]))
        msgs = new_messages(token, gid, state.get(gid), args.backfill)
        for m in msgs:
            if m.get("system") or m.get("sender_type") == "bot" or m["id"] in seen:
                continue
            r = why(m, keep, excl, senders, mode)
            if r:
                item = slim(m, gname, r)
                kept.append(item)
                seen.add(m["id"])
                added += 1
                print("KEEP ", r, "|", item["name"], "|", item["text"][:90].replace("\n", " "))
                if bot and not args.dry_run:
                    when = time.strftime("%a %m/%d %I:%M %p", time.localtime(m["created_at"]))
                    post_bot(bot, f"[{gname}] {item['name']} @ {when}\n{item['text']}" +
                             ("".join("\n" + u for u in item["attachments"])))
            else:
                if not any(x["id"] == m["id"] for x in skipped):
                    skipped.append(slim(m, gname, None))
        if msgs:
            state[gid] = msgs[-1]["id"]

    kept.sort(key=lambda m: m["ts"], reverse=True)
    skipped = sorted(skipped, key=lambda m: m["ts"], reverse=True)[:100]
    print(f"{added} new message(s) kept; {len(kept)} total")
    if not args.dry_run:
        jsave(MESSAGES, kept)
        jsave(SKIPPED, skipped)
        jsave(STATE, state)


if __name__ == "__main__":
    main()
