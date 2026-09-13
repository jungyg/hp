#!/usr/bin/env python3
"""
build_site.py — turn Hi Proof's product feed into a static, sortable catalogue.

    python3 build_site.py --from-file products.json   # test with a saved page
    python3 build_site.py                             # fetch every page live
    python3 build_site.py --audit                     # coverage report, no build

Writes docs/data.json. Serve docs/ anywhere static (GitHub Pages).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hiproof_watch import SHOP, MAX_PAGES, REQUEST_DELAY, _get, fetch_atom
from whiskey_filter import (classify, whiskey_vendors, WHISKEY_TYPES,
                            NON_WHISKEY_TYPES)

COLLECTION = "new-arrivals"

# Window for the "Just landed" tab, measured on created_at.
RECENT_DAYS = 30
OUT = Path("docs")

# --------------------------------------------------------------------------- #
# Category — coarser than product_type, for the filter control
# --------------------------------------------------------------------------- #
# Order matters: the first rule that matches wins.
CATEGORY_RULES = [
    ("Irish", ["irish", "tullamore", "redbreast", "jameson", "bushmills",
               "green spot", "powers", "teeling"]),
    ("Japanese & world", ["japanese", "japan", "suntory", "nikka", "chichibu",
                          "mars whisky", "shibui", "hibiki", "yamazaki",
                          "hakushu", "kavalan", "taiwan", "amrut", "india",
                          "paul john", "three societies", "world whisky"]),
    ("Scotch & single malt", ["scotch", "islay", "speyside", "campbeltown",
                              "highland", "lowland", "single malt", "peated",
                              "glen", "bruichladdich", "ardbeg", "laphroaig",
                              "macallan", "bowmore", "talisker", "cadenhead"]),
    ("Rye", ["rye"]),
    ("Bourbon", ["bourbon"]),
]


def category(product: dict) -> str:
    ptype = (product.get("product_type") or "").strip().lower()
    text = f"{product.get('title','')} {ptype}".lower()
    if ptype == "bourbon":
        return "Bourbon"
    if ptype == "scotch":
        return "Scotch & single malt"
    for name, words in CATEGORY_RULES:
        if any(w in text for w in words):
            return name
    return "Other whiskey"


# --------------------------------------------------------------------------- #
# Field extraction — these power the sort options
# --------------------------------------------------------------------------- #

def proof_of(title: str):
    """'129.3 Proof' -> 129.3. Also accepts '54.5% ABV' and converts."""
    m = re.search(r"(\d{2,3}(?:\.\d+)?)\s*proof\b", title, re.I)
    if m:
        val = float(m.group(1))
        return val if 40 <= val <= 200 else None
    m = re.search(r"(\d{2}(?:\.\d+)?)\s*%(?:\s*abv)?", title, re.I)
    if m:
        val = float(m.group(1)) * 2
        return val if 40 <= val <= 200 else None
    return None


def age_of(title: str):
    """'14 Year Old' -> 14. Rejects vintages and bottle counts."""
    for m in re.finditer(r"(\d{1,2})\s*(?:year|yr)s?\b", title, re.I):
        val = int(m.group(1))
        if 1 <= val <= 80:
            return val
    return None


def size_ml(title: str):
    m = re.search(r"(\d+(?:\.\d+)?)\s*(ml|l)\b", title, re.I)
    if not m:
        return None
    val = float(m.group(1))
    return val * 1000 if m.group(2).lower() == "l" else val


def money(raw):
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def best_variant(product: dict):
    """Cheapest available variant, else cheapest overall."""
    variants = product.get("variants") or []
    priced = [v for v in variants if money(v.get("price")) is not None]
    if not priced:
        return None
    live = [v for v in priced if v.get("available")]
    return min(live or priced, key=lambda v: money(v.get("price")))


def record(product: dict, reason: str, verdict: str) -> dict:
    v = best_variant(product) or {}
    price = money(v.get("price"))
    retail = money(v.get("compare_at_price"))
    if retail is not None and price is not None and retail <= price:
        retail = None                      # not a real markdown
    discount = round((1 - price / retail) * 100) if retail and price else None

    images = product.get("images") or []
    title = product.get("title") or ""

    return {
        "id": str(product.get("id") or product.get("handle")),
        "title": title,
        "vendor": (product.get("vendor") or "").strip(),
        "type": (product.get("product_type") or "").strip(),
        "category": category(product),
        "url": f"{SHOP}/products/{product.get('handle','')}",
        "image": images[0].get("src") if images else None,
        "price": price,
        "retail": retail,
        "discount": discount,
        "available": any(x.get("available") for x in (product.get("variants") or [])),
        "published": (product.get("published_at") or "")[:10],
        "proof": proof_of(title),
        "age": age_of(title),
        "size": size_ml(title),
        "created": (product.get("created_at") or "")[:10],
        "updated": (product.get("updated_at") or "")[:10],
        "whiskey": verdict == "yes",
        "review": verdict == "review",
        "reason": reason,
    }


# --------------------------------------------------------------------------- #
# Fetch
# --------------------------------------------------------------------------- #

def fetch_all(collection: str) -> list[dict]:
    """Walk /products.json?page=1,2,3… until a short or empty page comes back.

    limit=250 is Shopify's hard maximum. The loop stops on a page with fewer
    than 250 products, which is how you know you've reached the end — there is
    no total count in the response.
    """
    products, page = [], 1
    while page <= MAX_PAGES:
        url = f"{SHOP}/collections/{collection}/products.json?limit=250&page={page}"
        print(f"  page {page:>2} …", end="", flush=True)
        try:
            batch = json.loads(_get(url)).get("products", [])
        except Exception as e:
            print(f" failed ({e})")
            break
        print(f" {len(batch):>3}  (running total {len(products) + len(batch)})")
        if not batch:
            break
        products.extend(batch)
        if len(batch) < 250:
            break
        page += 1
        time.sleep(REQUEST_DELAY)
    else:
        print(f"  stopped at MAX_PAGES={MAX_PAGES}; raise it in hiproof_watch.py"
              " if the store has grown")
    if not products:
        print("  products.json unavailable; falling back to Atom")
        products = fetch_atom(collection)
    return products


def load(args) -> list[dict]:
    if args.from_file:
        raw = json.loads(Path(args.from_file).read_text())
        return raw["products"] if isinstance(raw, dict) else raw
    print(f"Fetching /collections/{COLLECTION} …")
    return fetch_all(COLLECTION)


# --------------------------------------------------------------------------- #
# Audit — is the classifier's vocabulary still complete?
# --------------------------------------------------------------------------- #

def audit(products: list[dict]) -> int:
    from collections import Counter
    known = WHISKEY_TYPES | NON_WHISKEY_TYPES
    types = Counter((p.get("product_type") or "(empty)").strip() for p in products)
    unknown = {t: c for t, c in types.items()
               if t != "(empty)" and t.lower() not in known}

    print(f"\n{len(products)} products, {len(types)} distinct product_type values")
    print(f"empty product_type: {types.get('(empty)', 0)}")

    if unknown:
        print("\nproduct_type values the classifier does NOT recognise:")
        for t, c in sorted(unknown.items(), key=lambda x: -x[1]):
            print(f"  {c:>5}  {t}")
        print("\nAdd each to WHISKEY_TYPES or NON_WHISKEY_TYPES in whiskey_filter.py.")
    else:
        print("\nEvery product_type value is recognised.")

    hints = whiskey_vendors(products)
    review = [p for p in products if classify(p, hints)[0] == "review"]
    print(f"\n{len(review)} product(s) unclassifiable (no product_type, no keyword):")
    for p in review[:40]:
        print(f"  {p.get('title','')[:74]}")
    if len(review) > 40:
        print(f"  … and {len(review) - 40} more")
    return 0


# --------------------------------------------------------------------------- #

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-file", metavar="PATH",
                    help="build from a saved products.json instead of fetching")
    ap.add_argument("--audit", action="store_true",
                    help="report vocabulary coverage and exit")
    args = ap.parse_args()

    products = load(args)
    if not products:
        print("No products fetched.", file=sys.stderr)
        return 1

    if args.audit:
        return audit(products)

    # Only whiskey reaches the site. Unclassifiable items (no product_type and
    # no keyword) come along flagged, so nothing is lost silently.
    hints = whiskey_vendors(products)
    rows = []
    for p in products:
        verdict, reason = classify(p, hints)
        if verdict in ("yes", "review"):
            rows.append(record(p, reason, verdict))

    # Primary key created_at, secondary updated_at — newest arrivals first.
    # In the sample updated_at held one identical value across every product
    # (a bulk sync), so the secondary key rarely breaks a tie in practice.
    rows.sort(key=lambda r: (r["created"] or "", r["updated"] or "", r["title"]),
              reverse=True)

    cutoff = (datetime.now(timezone.utc) - timedelta(days=RECENT_DAYS)).strftime("%Y-%m-%d")

    OUT.mkdir(exist_ok=True)
    payload = {
        "built": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "recentDays": RECENT_DAYS,
        "cutoff": cutoff,
        "source": f"{SHOP}/collections/{COLLECTION}",
        "scanned": len(products),
        "items": rows,
    }
    (OUT / "data.json").write_text(json.dumps(payload, separators=(",", ":")))

    review = sum(1 for r in rows if r["review"])
    recent = sum(1 for r in rows if r["created"] >= cutoff)
    print(f"\n{len(products)} scanned -> {len(rows) - review} whiskey"
          f" + {review} unclassified"
          f" ({recent} created in the last {RECENT_DAYS} days)"
          f" -> {OUT/'data.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
