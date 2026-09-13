#!/usr/bin/env python3
"""
notify.py — push a phone notification for each whiskey new to the catalog.

    python3 notify.py diff                    # docs/data.json vs live site -> new.json
    python3 notify.py diff --previous old.json
    python3 notify.py send new.json           # post each new bottle to ntfy
    python3 notify.py send new.json --dry-run # print instead of posting
    python3 notify.py test                    # one message, to check subscriptions

"New" means two things at once:

  1. the id is not in the data.json currently deployed on the site, and
  2. the product was created in the last NOTIFY_DAYS days.

The second test stops relisted old stock — or the recovery after a partial
build that dropped half the catalog — from re-announcing bottles people have
already seen.

The ntfy topic comes from NTFY_TOPIC. Anyone who knows a topic can read it and
post to it, so it lives in a GitHub secret rather than in this public repo.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh")
NOTIFY_DAYS = 7
# Above this many bottles in one run, send a single summary instead of a
# message each, so a big restock doesn't bury everyone's phone.
MAX_EACH = 10
CURRENT = Path("docs/data.json")


def site_url() -> str:
    """The deployed Pages site, derived from the repo Actions is running in."""
    if os.environ.get("SITE_URL"):
        return os.environ["SITE_URL"].rstrip("/") + "/"
    owner, name = os.environ.get("GITHUB_REPOSITORY", "jungyg/hp").split("/", 1)
    return f"https://{owner.lower()}.github.io/{name}/"


# --------------------------------------------------------------------------- #
# diff
# --------------------------------------------------------------------------- #

def load_previous(src: str) -> dict:
    if not src.startswith("http"):
        return json.loads(Path(src).read_text())
    # Pages sits behind a CDN that caches for ~10 minutes; a unique query
    # string makes sure we compare against what is really deployed.
    url = f"{src}?t={int(time.time())}"
    req = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def diff(args) -> int:
    current = json.loads(CURRENT.read_text())["items"]
    src = args.previous or site_url() + "data.json"
    try:
        previous = load_previous(src)["items"]
    except Exception as e:                 # noqa: BLE001
        # First deploy, or the site is down. Without a baseline every bottle
        # would look new, so announce nothing this run.
        print(f"No previous catalog at {src} ({e}); nothing to compare, skipping")
        previous = None

    new = []
    if previous:
        seen = {item["id"] for item in previous}
        cutoff = (datetime.now(timezone.utc)
                  - timedelta(days=NOTIFY_DAYS)).strftime("%Y-%m-%d")
        new = [item for item in current
               if item["whiskey"] and item["id"] not in seen
               and (item["created"] or "") >= cutoff]

    Path(args.out).write_text(json.dumps(new, indent=1))
    print(f"{len(new)} new whiskey -> {args.out}")
    for item in new:
        print(f"  {item['created']}  {item['title']}")
    return 0


# --------------------------------------------------------------------------- #
# send
# --------------------------------------------------------------------------- #

def money(x: float) -> str:
    return f"${x:,.2f}"


def describe(item: dict) -> str:
    """One line of facts: price, proof, age, category, vendor."""
    bits = []
    if item["price"] is not None:
        price = money(item["price"])
        if item["retail"]:
            price += f" (was {money(item['retail'])}, {item['discount']}% off)"
        bits.append(price)
    if item["proof"]:
        bits.append(f"{item['proof']:g} proof")
    if item["age"]:
        bits.append(f"{item['age']} yr")
    bits.append(item["category"])
    line = " · ".join(bits)
    if item["vendor"]:
        line += f"\n{item['vendor']}"
    if not item["available"]:
        line += "\nListed, but currently sold out"
    return line


def messages(items: list[dict]) -> list[dict]:
    if len(items) > MAX_EACH:
        lines = [f"• {i['title']}"
                 + (f" — {money(i['price'])}" if i["price"] is not None else "")
                 for i in items[:15]]
        if len(items) > 15:
            lines.append(f"…and {len(items) - 15} more")
        return [{
            "title": f"{len(items)} new whiskeys at Hi Proof",
            "message": "\n".join(lines),
            "click": site_url(),
            "tags": ["tumbler_glass"],
        }]

    out = []
    for item in items:
        msg = {
            "title": f"New: {item['title']}",
            "message": describe(item),
            "click": item["url"],
            "tags": ["tumbler_glass"],
        }
        if item["image"]:
            # Shopify's CDN resizes on request; full-size photos are several MB.
            msg["attach"] = item["image"] + "&width=600"
        out.append(msg)
    return out


def post(topic: str, msg: dict) -> None:
    body = json.dumps({"topic": topic, **msg}).encode()
    req = urllib.request.Request(NTFY_SERVER, data=body,
                                 headers={"Content-Type": "application/json"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                resp.read()
            return
        except Exception:                  # noqa: BLE001
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)


def topic_or_exit() -> str:
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        sys.exit("NTFY_TOPIC is not set. Add it under Settings -> Secrets and"
                 " variables -> Actions.")
    return topic


def send(args) -> int:
    items = json.loads(Path(args.file).read_text())
    if not items:
        print("Nothing new; no notifications sent")
        return 0

    msgs = messages(items)
    if args.dry_run:
        for msg in msgs:
            print(json.dumps(msg, indent=1, ensure_ascii=False))
        return 0

    topic = topic_or_exit()
    for msg in msgs:
        post(topic, msg)
        time.sleep(1)
    print(f"Sent {len(msgs)} notification(s) for {len(items)} new whiskey")
    return 0


def test(args) -> int:
    post(topic_or_exit(), {
        "title": "Hi Proof alerts are working",
        "message": "You'll get a notification here when new whiskey lands.",
        "click": site_url(),
        "tags": ["tumbler_glass"],
    })
    print("Test notification sent")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("diff", help="find whiskey new since the last deploy")
    d.add_argument("--previous", help="URL or path of the old data.json"
                   " (default: the live site)")
    d.add_argument("--out", default="new.json")
    d.set_defaults(func=diff)

    s = sub.add_parser("send", help="post new whiskey to ntfy")
    s.add_argument("file", nargs="?", default="new.json")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(func=send)

    t = sub.add_parser("test", help="send one test notification")
    t.set_defaults(func=test)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
