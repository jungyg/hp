# Hi Proof whiskey new-arrival watcher

Emails you when new whiskey appears at hiproof.com. Python 3.9+, stdlib only.

## Endpoints

Hi Proof runs on Shopify, so every collection publishes machine-readable feeds:

    https://www.hiproof.com/collections/<handle>/products.json?limit=250&page=1
    https://www.hiproof.com/collections/<handle>.atom

`limit` caps at 250; use `page` to walk the rest. The Atom feed needs no
parameters and is always available, but omits tags and stock status.

Check they respond before doing anything else:

    python3 hiproof_watch.py --verify

## Why this doesn't watch "New Arrivals"

`/collections/new-arrivals` holds ~3,486 products. The whole store is ~4,848.
Roughly 72% of the catalogue carries the "New arrival" badge, so it's a
merchandising label rather than a recency signal — and it mixes in wine,
tequila, rum, Glencairn glassware and tasting-event tickets.

## How whiskey is isolated

By collection membership, not keyword matching. The site's own menu structure
gives three whiskey parent collections:

| Handle | Covers |
|---|---|
| `bourbon-rye-american-whiskey` | bourbon, rye, wheated, Tennessee, American single malt |
| `scotch-single-malt` | blended, Islay, Island, Campbeltown, Speyside, Highland, Lowland, grain |
| `japanese-world-whisky` | Japanese, Taiwanese, Indian, Irish, Canadian, French, world |

A product appearing in one of these for the first time is new whiskey by
construction. Wine, agave, rum, gin and glassware are structurally excluded —
nothing depends on a title containing the word "bourbon".

Recency comes from `published_at` in the JSON, capped by `MAX_AGE_DAYS` (30 by
default). That's what stops old stock being re-added to a collection from
flooding your inbox.

To narrow further, replace `COLLECTIONS` with leaf handles:

    COLLECTIONS = ["islay", "japanese-whisky-1", "rye"]

Any handle from a `/collections/<handle>` URL on the site works.

## The catalog site

`build_site.py` fetches the feed, classifies it, and writes `docs/data.json`.
`docs/index.html` is a static page that reads it. No server, no build step.

    python3 build_site.py --from-file products.json   # build from a saved page
    python3 build_site.py                             # fetch every page live
    python3 build_site.py --audit                     # coverage report only
    cd docs && python3 -m http.server                 # preview at :8000

Deploy: put `hiproof-pages.yml` in `.github/workflows/`, set Pages to "GitHub
Actions" under Settings → Pages. It rebuilds daily and refuses to publish if
the feed returns nothing, so a store-side outage can't blank the catalog.

### Sorting

Sort is the primary control, so it sits in the header as buttons rather than a
dropdown. Default is newest arrival.

| Sort | Field |
|---|---|
| Newest arrival | `created_at`, then `updated_at` |
| Recently updated | `updated_at`, then `created_at` |
| Biggest markdown | `compare_at_price` vs `price` |
| Lowest / Highest price | cheapest available variant |
| Highest proof | parsed from the title |
| Oldest age | parsed from the title |
| A–Z | title |

**`updated_at` carries no signal on this store.** In the 250-product sample all
250 products shared one identical timestamp — `2026-09-13T09:19:48-07:00` — so
a bulk sync touches the entire catalogue at once. `created_at` had 35 distinct
days over the same set and is the real arrival date. The secondary key is wired
as requested and will work if that ever changes; today it never breaks a tie.
`published_at` is a third date, and it differs from `created_at` for about half
the catalogue, so it is kept in the data but not sorted on.

Three details that matter:

- **Missing values sink.** A bottle with no proof statement sorts to the bottom
  of "Highest proof", never to the top as a zero.
- **Every sort has a tiebreak** — created date, then title — so equal values
  keep a stable order instead of shuffling between renders.
- **Price means the cheapest available variant**, falling back to the cheapest
  overall when everything is sold out, so a sold-out 50ml can't misrepresent a
  bottle's price.

Proof, age and size are parsed from titles with guards: proof only accepts
40–200 (and converts `54.5%` ABV), age only 1–80 years so vintages like `2019`
don't register. In the sample that yields proof for 49 bottles, age for 59,
size for 233, and a markdown for 37.

### Paging and dates

Page size is 30, 50 or 100. The pager shows first and last with a window around
the current page — `1 … 29 30 31 … 60` — so it stays one line at any catalogue
size. Changing page size keeps your position; changing a sort or filter resets
to page 1, since the ordering underneath changed.

Each row shows `added YYMMDD` and `upd YYMMDD`, full ISO date in the tooltip.

### Two tabs

**Just landed** — whiskey whose `created_at` falls inside the last
`RECENT_DAYS` (30, set in `build_site.py`). Opens on newest-first, because
that is the point of the view. The cutoff date is computed at build time and
stored in `data.json`, so the page shows the same window the fetch saw rather
than drifting as the file ages.

**All whiskey** — every whiskey in the feed, with the full sort set including
`updated_at` and markdown percentage.

Both tabs are one dataset with a `created_at` filter, not two fetches, so
switching is instant and search, category, stock and page size carry across.
The tab lives in the URL hash (`#all`), so a view can be linked or bookmarked.

Filters are category, stock, and free-text search across title and distillery.

## How whiskey is identified

Validated against a real 250-product page of
`/collections/new-arrivals/products.json`. Result: **149 whiskey, 100 not,
1 needs review, 0 false positives.**

### What the live data actually looks like

`product_type` distribution in the sample:

    WHISKY 72 | Whiskey 31 | (empty) 29 | WINE 28 | Tequila 20 | Bourbon 17
    Champagne 9 | Scotch 9 | Rum 8 | Mezcal 5 | Spirits 5 | Vodka 3
    Liqueur 2 | Cognac 2 | "this" 2 | RTD 2 | Brandy 1 | Barware 1
    whiskey 1 | Non-Alcoholic 1 | Gin 1 | tequila 1

Two consequences:

- **`WHISKY` alone is not enough.** Whiskey is spread across `WHISKY`,
  `Whiskey`, `whiskey`, `Bourbon` and `Scotch` — 130 products. Matching only
  `WHISKY` catches 72 and misses 45% of them. Casing is inconsistent, so all
  matching is lowercased.
- **Tags are useless here.** All 250 products carry exactly one tag, `New`.
  Only 19 distinct tags exist in total and just 4 are whiskey-related. Tags
  cannot serve as the fallback tier.

### The tiers

| Tier | Rule | Caught |
|---|---|---|
| 0 | `FORMAT_EXCLUDE` — not a bottle (tasting event, glassware) | 2 |
| 1 | `product_type` in `WHISKEY_TYPES` | 130 |
| 2 | `product_type` in `NON_WHISKEY_TYPES` | 87 |
| 3a | negative keyword (wine varietal, agave, cognac…) | 11 |
| 3b | positive keyword | 19 |
| 3c | vendor seen on a whiskey-typed product elsewhere | 0 in sample |
| 3d | nothing matched, no `product_type` → **review** | 1 |

### Keywords were derived, not guessed

Token counts across the 130 type-labelled whiskeys vs the 89 type-labelled
non-whiskeys. Only terms scoring zero on the non-whiskey side were kept, then
hand-filtered for generic ones: `old` (21), `year old` (13), `finish` (12),
`pick` (11), `rare` (3) and `gift` (3) all scored zero but were **rejected** —
n=250 is too small to trust low-count generic tokens.

`proof` and `cask` were rejected outright: both appear on non-whiskey products
(`400 Conejos Mezcal ... 80 Proof`, `Velvet Cask Original Cream`).

`single barrel` and `straight` earn their place by recovering four bottles with
no whiskey word anywhere in the title — the Four Roses and Buffalo Trace
single-barrel picks.

The feed contains a typo, `Doc Swinson ... Rye Whisey`, so `whisey` is in the
positive list.

Matching is word-boundary, not substring. `DOCG` (wine) would otherwise match
`Doc Swinson`, and `port` would match `Portland`.

### Why product_type always beats keywords

Cask-finish whiskeys are named after the barrel's previous contents. Letting
the negative list override `product_type` drops nine real whiskeys:

    Doc Swinson's Dos Padres Anejo TEQUILA Cask Finish Straight Bourbon
    Basil Hayden COGNAC Cask Reserve Bourbon Whisky
    Hooten Young ZINFANDEL Cask Finish American Whiskey
    Virginia Distillery Co. CABERNET Cask Select American Single Malt
    CREAM of Kentucky Cask Strength Kentucky Straight Bourbon   (brand name)
    Heaven Hill Grain to GLASS Wheated Bourbon                  (product name)

So category keywords only ever fill gaps; they never override the store's own
label. `FORMAT_EXCLUDE` is the one pre-`product_type` tier and holds only
unambiguous multi-word phrases for things that are not bottles.

### The review bucket

`Stagg 26A 129.3 Proof 750ml` is whiskey, has no `product_type`, and contains
no whiskey word. Rather than drop it silently, unclassifiable items with no
`product_type` go to a separate "no category set" block in the email. For an
alerting tool, one occasional false alarm beats missing an allocated bottle.
Set `REPORT_UNCLASSIFIED = False` to suppress it.

### Tuning

Everything lives in `whiskey_filter.py`: `WHISKEY_TYPES`, `NON_WHISKEY_TYPES`,
`FORMAT_EXCLUDE`, `NEGATIVE`, `POSITIVE`. Re-run the vocabulary check on a
fresh page whenever you want to confirm they still hold:

    python3 hiproof_watch.py --inspect new-arrivals

Two items the store itself types as `WHISKY` that you may not want —
`BHAKTA ... Whiskey & Spirits Gift Set` and `Tullamore D.E.W. Honey Irish
Whiskey Liqueur` — are kept, because the store called them whiskey. Add exact
phrases to `FORMAT_EXCLUDE` if you disagree.

## Setup

Gmail needs an **app password** (2-step verification on, then Google Account →
Security → App passwords), not your account password.

    export HIPROOF_SMTP_USER="you@gmail.com"
    export HIPROOF_SMTP_PASS="abcdefghijklmnop"
    export HIPROOF_MAIL_TO="you@gmail.com"

Other providers: set `HIPROOF_SMTP_HOST` / `HIPROOF_SMTP_PORT`. Outlook is
`smtp-mail.outlook.com` / 587 (STARTTLS); Fastmail `smtp.fastmail.com` / 465.

    python3 hiproof_watch.py --verify       # endpoints reachable?
    python3 hiproof_watch.py --test-email   # SMTP works?
    python3 hiproof_watch.py --init         # baseline — REQUIRED, or run 1 emails everything
    python3 hiproof_watch.py --dry-run      # print matches, send nothing
    python3 hiproof_watch.py                # normal run

State lives in `~/.hiproof_seen.json` (override with `HIPROOF_STATE`), written
atomically so an interrupted run can't corrupt it.

## Scheduling

**cron** — twice daily:

    0 8,14 * * * cd /path/to/watcher && HIPROOF_SMTP_USER=... HIPROOF_SMTP_PASS=... HIPROOF_MAIL_TO=... /usr/bin/python3 hiproof_watch.py >> watch.log 2>&1

Cron gets a bare environment, so set the vars inline or `source` a file. Only
fires while the machine is awake.

**GitHub Actions** — put `hiproof-watch.yml` in `.github/workflows/`, add the
three env vars as repository secrets under Settings → Secrets and variables →
Actions. Takes its own baseline on the first run and caches state between runs.
Note GitHub disables scheduled workflows after 60 days of repo inactivity, and
scheduled runs can be delayed under load.

## Tuning

| Constant | Effect |
|---|---|
| `COLLECTIONS` | Which collections to watch |
| `MAX_AGE_DAYS` | Ignore listings published longer ago than this. `None` disables |
| `REQUIRE_NEW_ARRIVAL_BADGE` | Also require the "New arrival" badge |
| `EXCLUDE` | Drop words — glassware, tasting tickets, merch |
| `MIN_PRICE` / `MAX_PRICE` | Price window in USD |
| `IN_STOCK_ONLY` | Suppress sold-out listings |

## Notes

- `REQUEST_DELAY` spaces requests a second apart. Leave it — hammering a small
  shop's storefront is how you get IP-blocked.
- For allocated releases (Weller, Blanton's, Eagle Rare), add
  `sazerac-allocation` to `COLLECTIONS` and run hourly. Those don't last.
- Hi Proof's own newsletter signup is in the site footer. Less targeted, but it
  catches announcements that never reach a collection. Worth having both.
# hp
