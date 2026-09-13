#!/usr/bin/env python3
"""
notify.py — announce whiskey that is new to the catalog, via ntfy and Discord.

    python3 notify.py diff                    # docs/data.json vs live site -> new.json
    python3 notify.py diff --previous old.json
    python3 notify.py send new.json           # post each new bottle
    python3 notify.py send new.json --dry-run # print instead of posting
    python3 notify.py test                    # one message per channel

"New" means two things at once:

  1. the id is not in the data.json currently deployed on the site, and
  2. the product was created in the last NOTIFY_DAYS days.

The second test stops relisted old stock — or the recovery after a partial
build that dropped half the catalog — from re-announcing bottles people have
already seen.

Channels are configured by environment, and any that are unset are skipped:

  NTFY_TOPIC            ntfy.sh topic name
  DISCORD_WEBHOOK_URL   Discord channel webhook

Anyone holding either can post to it, so both live in GitHub secrets rather
than in this public repo.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh")
NOTIFY_DAYS = 7
# Above this many bottles in one run, send a single summary instead of a
# message each, so a big restock doesn't bury everyone's phone.
MAX_EACH = 10
CURRENT = Path("docs/data.json")

# Discord's CDN rejects urllib's default User-Agent outright.
USER_AGENT = "DiscordBot (https://github.com/jungyg/hp, 1.0)"
GREEN, COPPER = 0x1E4232, 0xA65E2E     # the site's bottle green and markdown copper


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
# Message formatting
# --------------------------------------------------------------------------- #

def money(x: float) -> str:
    return f"${x:,.2f}"


def image(item: dict) -> str | None:
    # Shopify's CDN resizes on request; full-size photos are several MB.
    return item["image"] + "&width=600" if item["image"] else None


def summary_lines(items: list[dict], markdown: bool) -> list[str]:
    lines = []
    for i in items[:15]:
        name = f"[{i['title']}]({i['url']})" if markdown else i["title"]
        price = f" — {money(i['price'])}" if i["price"] is not None else ""
        lines.append(f"• {name}{price}")
    if len(items) > 15:
        lines.append(f"…and {len(items) - 15} more")
    return lines


def describe(item: dict) -> str:
    """One line of facts for ntfy: price, proof, age, category, vendor."""
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


def ntfy_messages(items: list[dict]) -> list[dict]:
    if len(items) > MAX_EACH:
        return [{
            "title": f"{len(items)} new whiskeys at Hi Proof",
            "message": "\n".join(summary_lines(items, markdown=False)),
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
        if image(item):
            msg["attach"] = image(item)
        out.append(msg)
    return out


def discord_embed(item: dict) -> dict:
    fields = []
    if item["price"] is not None:
        price = f"**{money(item['price'])}**"
        if item["retail"]:
            price += f"  ~~{money(item['retail'])}~~  {item['discount']}% off"
        fields.append({"name": "Price", "value": price, "inline": True})
    if item["proof"]:
        fields.append({"name": "Proof", "value": f"{item['proof']:g}", "inline": True})
    if item["age"]:
        fields.append({"name": "Age", "value": f"{item['age']} yr", "inline": True})
    fields.append({"name": "Category", "value": item["category"], "inline": True})

    desc = item["vendor"]
    if not item["available"]:
        desc += "\nListed, but currently sold out"

    embed = {
        "title": item["title"][:256],       # Discord's limit
        "url": item["url"],
        "color": COPPER if item["retail"] else GREEN,
        "fields": fields,
    }
    if desc.strip():
        embed["description"] = desc.strip()
    if image(item):
        embed["thumbnail"] = {"url": image(item)}
    return embed


def discord_messages(items: list[dict]) -> list[dict]:
    if len(items) > MAX_EACH:
        return [{"embeds": [{
            "title": f"{len(items)} new whiskeys at Hi Proof",
            "url": site_url(),
            "description": "\n".join(summary_lines(items, markdown=True))[:4096],
            "color": GREEN,
        }]}]
    return [{"embeds": [discord_embed(item)]} for item in items]


# --------------------------------------------------------------------------- #
# Delivery
# --------------------------------------------------------------------------- #

def post_json(url: str, payload: dict) -> None:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": USER_AGENT})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                resp.read()
            return
        except urllib.error.HTTPError as e:
            # A bad URL or payload won't fix itself; only retry rate limits
            # and server errors.
            if e.code != 429 and e.code < 500 or attempt == 2:
                raise
            time.sleep(float(e.headers.get("Retry-After") or 2 ** attempt))
        except urllib.error.URLError:
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)


def channels() -> dict:
    """name -> (url, payloads-from-items, payload-for-test), for each one set."""
    out = {}
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if topic:
        out["ntfy"] = (
            NTFY_SERVER,
            lambda items: [{"topic": topic, **m} for m in ntfy_messages(items)],
            {"topic": topic, "title": "Hi Proof alerts are working",
             "message": "You'll get a notification here when new whiskey lands.",
             "click": site_url(), "tags": ["tumbler_glass"]},
        )
    hook = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if hook:
        out["discord"] = (
            hook,
            discord_messages,
            {"embeds": [{"title": "Hi Proof alerts are working",
                         "url": site_url(), "color": GREEN,
                         "description": "New whiskey will be posted in this channel."}]},
        )
    return out


def configured() -> dict:
    chans = channels()
    if not chans:
        sys.exit("No channel configured. Set NTFY_TOPIC and/or DISCORD_WEBHOOK_URL"
                 " under Settings -> Secrets and variables -> Actions.")
    return chans


def deliver(sends: dict[str, list[dict]], urls: dict[str, str]) -> int:
    """Post every payload; one channel failing doesn't stop the others."""
    failed = []
    for name, payloads in sends.items():
        try:
            for payload in payloads:
                post_json(urls[name], payload)
                time.sleep(1)
            print(f"{name}: sent {len(payloads)} message(s)")
        except Exception as e:             # noqa: BLE001
            print(f"{name}: FAILED ({e})", file=sys.stderr)
            failed.append(name)
    return 1 if failed else 0


def send(args) -> int:
    items = json.loads(Path(args.file).read_text())
    if not items:
        print("Nothing new; no notifications sent")
        return 0

    if args.dry_run:
        print("== ntfy")
        for m in ntfy_messages(items):
            print(json.dumps(m, indent=1, ensure_ascii=False))
        print("== discord")
        for m in discord_messages(items):
            print(json.dumps(m, indent=1, ensure_ascii=False))
        return 0

    chans = configured()
    print(f"{len(items)} new whiskey")
    return deliver({name: build(items) for name, (_, build, _) in chans.items()},
                   {name: url for name, (url, _, _) in chans.items()})


def test(args) -> int:
    chans = configured()
    return deliver({name: [msg] for name, (_, _, msg) in chans.items()},
                   {name: url for name, (url, _, _) in chans.items()})


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("diff", help="find whiskey new since the last deploy")
    d.add_argument("--previous", help="URL or path of the old data.json"
                   " (default: the live site)")
    d.add_argument("--out", default="new.json")
    d.set_defaults(func=diff)

    s = sub.add_parser("send", help="post new whiskey to each channel")
    s.add_argument("file", nargs="?", default="new.json")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(func=send)

    t = sub.add_parser("test", help="send one test message per channel")
    t.set_defaults(func=test)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
