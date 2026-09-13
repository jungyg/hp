"""
whiskey_filter.py — decide whether a Hi Proof product is whiskey.

Every list here was derived from a real 250-product page of
/collections/new-arrivals/products.json, not guessed. See notes inline.

Classification is tiered:

  0. title says it is not a bottle (event, glass) -> NO
  1. product_type is a known whiskey label      -> YES
  2. product_type is a known non-whiskey label  -> NO
  3. product_type empty or unrecognised         -> keyword tiers:
       3a. a negative keyword fires             -> NO
       3b. a positive keyword fires             -> YES
       3c. the vendor appears on a whiskey-typed product elsewhere
           in the same fetch                    -> YES
       3d. nothing fires                        -> REVIEW

REVIEW is deliberate. In the sample, one genuine whiskey ("Stagg 26A 129.3
Proof 750ml") has an empty product_type and no whiskey word in its title.
Silently dropping it would be the worst outcome for an alerting tool, so
unclassifiable items with no product_type are surfaced separately instead.
"""

from __future__ import annotations

import re

# --------------------------------------------------------------------------- #
# Tier 1/2 — product_type vocabulary, verbatim from the live feed
# --------------------------------------------------------------------------- #
# Observed counts in the 250-product sample:
#   WHISKY 72, Whiskey 31, (empty) 29, WINE 28, Tequila 20, Bourbon 17,
#   Champagne 9, Scotch 9, Rum 8, Mezcal 5, Spirits 5, Vodka 3, Liqueur 2,
#   Cognac 2, "this" 2, RTD 2, Brandy 1, Barware 1, whiskey 1,
#   Non-Alcoholic 1, Gin 1, tequila 1
#
# Note the casing is inconsistent (WHISKY / Whiskey / whiskey), so matching is
# always done lowercased. Filtering on "WHISKY" alone would have caught 72 of
# the 130 type-labelled whiskeys — it misses 45% of them.

WHISKEY_TYPES = {
    "whisky", "whiskey", "bourbon", "scotch", "rye",
    # not present in the sample but consistent with the store's nav taxonomy
    "single malt", "irish whiskey", "japanese whisky", "canadian whisky",
    "american single malt", "world whisky", "blended scotch", "tennessee whiskey",
}

NON_WHISKEY_TYPES = {
    "wine", "tequila", "champagne", "rum", "mezcal", "vodka", "liqueur",
    "cognac", "brandy", "barware", "non-alcoholic", "gin", "rtd", "spirits",
    "sake", "soju", "beer", "cider", "vermouth", "sherry", "port",
}
# "Spirits" covers Bittermens bitters and Dalho soju in the sample — no whiskey.
# "this" (2 products) is a data-entry slip; both are whiskey and both are caught
# by the keyword tier, so it is left out of both sets on purpose.

# --------------------------------------------------------------------------- #
# Tier 3a — negative keywords, checked FIRST
# --------------------------------------------------------------------------- #
# These exist because whiskey words genuinely appear in non-whiskey titles:
#   "An Evening with Woodinville Master Distiller | Whiskey Tasting"  (event)
#   "Valdespino Vermouth 'Origen' aged in macallan virgin oak casks"  (vermouth)
#   "Velvet Cask Original Cream 750ml"                                (cream)
#   "1000 St Ories California Bourbon Barrel Aged Chardonay 2020"     (wine)

NEGATIVE = [
    # events, merch, packaging
    "tasting", "ticket", "evening with", "gift set", "gift combo",
    "glass", "glassware", "decanter", "barware", "t-shirt", "hoodie",
    # other categories that borrow whiskey vocabulary
    "vermouth", "liqueur", "cream", "amaro", "bitters",
    "mezcal", "tequila", "sotol", "raicilla", "soju", "sake", "spritz",
    "cognac", "armagnac", "calvados", "grappa", "pisco",
    "v.s.o.p", "x.o",      # cognac age statements: "Hennessy V.S.O.P", tag "x.o"
    # wine varietals and appellations seen in the sample
    "barolo", "chateauneuf", "châteauneuf", "shiraz", "chardonnay",
    "cabernet", "sauvignon", "pinot", "sangiovese", "merlot", "zinfandel",
    "riesling", "prosecco", "champagne", "brunello", "rioja", "malbec",
    "napa valley", "igt", "docg",
]

# --------------------------------------------------------------------------- #
# Tier 3b — positive keywords
# --------------------------------------------------------------------------- #
# Chosen from an exclusivity scan over the sample: token counts among the 130
# type-labelled whiskeys vs the 89 type-labelled non-whiskeys. Only terms that
# are both domain-meaningful AND scored zero on the non-whiskey side are kept.
#
# Deliberately REJECTED despite scoring zero, because n=250 is too small to
# trust them and they are generic: "old" (21), "year old" (13), "finish" (12),
# "rare" (3), "gift" (3), "release" (4), "pick" (11), "barrel" (17 alone).
# "proof" and "cask" are excluded outright — both appear on non-whiskey
# products in the sample ("80 Proof" mezcal, "High Proof Glass", "Velvet Cask").

POSITIVE = [
    "whisky", "whiskey",
    "whisey",              # real typo in the feed: "Doc Swinson ... Rye Whisey"
    "bourbon", "scotch", "rye",
    "single malt", "single barrel", "straight",
    "bottled in bond", "kentucky", "cask strength", "barrel proof",
    "small batch", "blended malt", "peated", "islay", "speyside",
    "highland park", "campbeltown", "glenlivet", "distillery",
]
# "single barrel" and "straight" are what recover the Four Roses and Buffalo
# Trace single-barrel picks, which carry no explicit whiskey word at all.


# --------------------------------------------------------------------------- #
# Tier 0 — things that are not a bottle of spirits at all
# --------------------------------------------------------------------------- #
# Applied BEFORE product_type, so it must contain only unambiguous multi-word
# phrases. Do NOT put category words here.
#
# Tested consequence of getting this wrong: letting the full NEGATIVE list
# override product_type drops 9 real whiskeys from the 250-product sample,
# because cask-finish whiskeys are named after the barrel's previous contents:
#   Doc Swinson's Dos Padres Anejo TEQUILA Cask Finish Straight Bourbon
#   Basil Hayden COGNAC Cask Reserve Bourbon Whisky
#   Hooten Young ZINFANDEL Cask Finish American Whiskey
#   Virginia Distillery Co. CABERNET Cask Select American Single Malt
#   CREAM of Kentucky Cask Strength Kentucky Straight Bourbon  (brand name)
#   Heaven Hill Grain to GLASS Wheated Bourbon                 (product name)
# The store's own product_type is more reliable than any title heuristic, so
# it always wins over category keywords.

FORMAT_EXCLUDE = [
    "whiskey tasting", "whisky tasting", "tasting event", "evening with",
    "ticket", "high proof glass", "glencairn", "decanter",
    "t-shirt", "hoodie", "tote bag",
]


def _words(text: str) -> str:
    """Lowercase and pad so word-boundary matching is cheap and safe."""
    return " " + re.sub(r"[^a-z0-9]+", " ", text.lower()).strip() + " "


def _hit(padded: str, phrase: str) -> bool:
    """Word-boundary match. Substring matching is wrong here: 'doc' (DOCG)
    would match 'Doc Swinson', and 'port' would match 'Portland'."""
    return f" {re.sub(r'[^a-z0-9]+', ' ', phrase.lower()).strip()} " in padded


def whiskey_vendors(products) -> set:
    """Vendors that appear on at least one whiskey-typed product.

    Used as tier 3c. 'Hi Proof' is dropped because it is the store's house
    vendor and is attached to both the Eagle Rare bottling and the Woodinville
    tasting event, so it carries no signal.
    """
    out = set()
    for p in products:
        if (p.get("product_type") or "").strip().lower() in WHISKEY_TYPES:
            v = (p.get("vendor") or "").strip()
            if v and v.lower() not in {"hi proof", "hiproof", ""}:
                out.add(v)
    return out


def classify(product: dict, vendor_hints: set = frozenset()) -> tuple[str, str]:
    """Return (verdict, reason) where verdict is 'yes', 'no' or 'review'."""
    ptype = (product.get("product_type") or "").strip().lower()

    tags = product.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]
    padded = _words(" ".join([
        product.get("title") or "",
        " ".join(str(t) for t in tags),
    ]))

    for kw in FORMAT_EXCLUDE:
        if _hit(padded, kw):
            return "no", f"not a bottle: '{kw}'"

    if ptype in WHISKEY_TYPES:
        return "yes", f"product_type={product.get('product_type')}"
    if ptype in NON_WHISKEY_TYPES:
        return "no", f"product_type={product.get('product_type')}"

    for kw in NEGATIVE:
        if _hit(padded, kw):
            return "no", f"negative keyword '{kw}'"

    for kw in POSITIVE:
        if _hit(padded, kw):
            return "yes", f"keyword '{kw}'"

    vendor = (product.get("vendor") or "").strip()
    if vendor and vendor in vendor_hints:
        return "yes", f"vendor '{vendor}' seen on whiskey elsewhere"

    if not ptype:
        return "review", "no product_type, no keyword match"
    return "no", f"unrecognised product_type={product.get('product_type')}"
