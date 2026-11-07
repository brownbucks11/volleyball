#!/usr/bin/env python3
"""Turn data/messages.json into docs/data.js for the static page."""

import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
cfg = json.loads((ROOT / "config.json").read_text())


def load(name):
    try:
        return json.loads((ROOT / "data" / name).read_text())
    except FileNotFoundError:
        return []


payload = {
    "title": cfg.get("site_title", "13U Announcements"),
    "subtitle": cfg.get("site_subtitle", ""),
    "built": int(time.time()),
    "keywords": cfg.get("keywords", []),
    "senders": cfg.get("senders", []),
    "messages": load("messages.json"),
    "skipped": load("skipped.json"),
}
out = ROOT / "docs" / "data.js"
out.parent.mkdir(exist_ok=True)
out.write_text("window.DATA = " + json.dumps(payload, ensure_ascii=False) + ";\n")
print(f"wrote {out} ({len(payload['messages'])} messages)")
