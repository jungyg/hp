"""
hiproof_watch.py — shared fetch helpers for Hi Proof's Shopify feeds.

build_site.py imports SHOP, MAX_PAGES, REQUEST_DELAY, _get and fetch_atom
from here. Stdlib only.
"""

from __future__ import annotations

import re
import time
import urllib.request
import xml.etree.ElementTree as ET

SHOP = "https://www.hiproof.com"

# 250 products per page; ~5,000 products in the whole store, so 40 is headroom.
MAX_PAGES = 40
REQUEST_DELAY = 1.0          # seconds between pages, to be polite
TIMEOUT = 30
RETRIES = 3

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; hiproof-catalog/1.0)",
    "Accept": "application/json, application/atom+xml, */*",
}

ATOM = "{http://www.w3.org/2005/Atom}"
SHOPIFY = "{http://jadedpixel.com/-/spec/shopify}"


def _get(url: str) -> str:
    """GET url and return the body as text, retrying transient failures."""
    last = None
    for attempt in range(RETRIES):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return resp.read().decode("utf-8")
        except Exception as e:           # noqa: BLE001 — retry anything
            last = e
            time.sleep(2 ** attempt)
    raise last


def fetch_atom(collection: str) -> list[dict]:
    """Fallback when products.json is unavailable.

    Returns products in the same shape as products.json, as far as the Atom
    feed allows. It has no stock status, compare-at price or images, so every
    variant is assumed available and markdowns are not detected.
    """
    try:
        root = ET.fromstring(_get(f"{SHOP}/collections/{collection}.atom"))
    except Exception as e:               # noqa: BLE001
        print(f"  Atom feed failed too ({e})")
        return []

    products = []
    for entry in root.iter(f"{ATOM}entry"):
        link = entry.find(f"{ATOM}link[@rel='alternate']")
        href = link.get("href", "") if link is not None else ""
        summary = entry.findtext(f"{ATOM}summary") or ""
        price = re.search(r"Price:\s*</strong>\s*([\d.,]+)", summary)
        published = entry.findtext(f"{ATOM}published") or ""
        products.append({
            "id": (entry.findtext(f"{ATOM}id") or "").rsplit("/", 1)[-1],
            "handle": href.rsplit("/products/", 1)[-1],
            "title": entry.findtext(f"{ATOM}title") or "",
            "vendor": entry.findtext(f"{SHOPIFY}vendor") or "",
            "product_type": entry.findtext(f"{SHOPIFY}type") or "",
            "created_at": published,
            "published_at": published,
            "updated_at": entry.findtext(f"{ATOM}updated") or "",
            "images": [],
            "variants": [{
                "price": price.group(1).replace(",", "") if price else None,
                "available": True,
            }],
        })
    return products
