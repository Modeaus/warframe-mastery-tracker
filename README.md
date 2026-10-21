# Warframe Mastery Tracker

![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)
![Python 3](https://img.shields.io/badge/python-3-blue.svg)
![Flask](https://img.shields.io/badge/backend-Flask-black.svg)
![Self-hosted](https://img.shields.io/badge/deployment-self--hosted-informational.svg)

A small self-hosted dashboard that shows which masterable Warframe items
(Warframes, weapons, companions, archwing gear) you're still missing, where
to farm them, and which relics are the most efficient targets — i.e. drop
the most of your missing items at once.

## How it works

- **Player data** comes from Warframe's public profile-viewer endpoint
  (`api.warframe.com/cdn/getProfileViewingData.php?playerId=<accountId>`) —
  the same data shown when you view another player's profile in-game. No
  login, no game client, no memory-scanning required; just a 24-character
  account ID.
- **Item catalog** comes from
  [WFCD/warframe-items](https://github.com/WFCD/warframe-items), a
  community-maintained dataset of every masterable item and its drop
  locations, refreshed daily by `catalog_sync.py`.
- The backend diffs the two to compute what's missing, groups drop sources
  by relic (collapsing Intact/Exceptional/Flawless/Radiant into one row),
  and ranks relics by how many missing items they can drop.
- Relic rarity (Common/Uncommon/Rare) is derived from the Intact-tier drop
  chance rather than trusting WFCD's raw per-tier `rarity` field, which
  drifts with refinement (see comments in `catalog_sync.py`).

## What it tracks

- **Missing items**, with every drop location, and **Targeted Farming**:
  the sources that drop the most of your missing items at once.
- **Parts found** for missing items. Tick parts off in an item's popup, or
  let the screen reader do it. Recipes that need two of a part (Kestrel
  Prime's Blades, akimbo Barrels) show `Blade 1/2` until both are found.
  Found parts drop out of Targeted Farming.
- **Ready to craft**: items whose every part is found get a highlighted row,
  and their category an "N ready" pill. (Crafting resources aren't tracked.)
- **In Progress**: owned items not yet at max rank. This includes gear
  that's built but never leveled, which the profile API can't see (no XP
  yet), recorded from inventory scans.
- **Arcanes**: copies held per rank against what's needed to max (rank 5 =
  21 copies; Exodia, Pax, Virtuos and Residual arcanes max at rank 3 = 10), with where to get
  each one: best drop locations, vendors and Loid's collections.

Parts, built-but-unleveled items and arcanes aren't in the public profile
data. They come from ticking boxes or from the optional
[screen reader](reader/README.md), which OCRs in-game inventory screens and
posts what it sees.

## Multi-player support

Anyone can add a player by account ID (Settings → Players panel). A
bookmarklet is included that reads your own `user_id` from
`warframe.com/api/user-data` while you're logged in there, then hands it
back to the tracker pre-filled — no manual lookup needed.

Note: this is public, non-authenticated data (the same thing in-game "View
Profile" shows), so there's intentionally no login/auth gate on adding a
player. See "Security notes" below.

## Setup

```
pip install --user flask gunicorn
```

1. Find your Warframe account ID: log into warframe.com, visit
   `https://www.warframe.com/api/user-data`, copy the `user_id` field.
2. `echo -n '<your account id>' > account_id.txt` — this becomes the
   default player shown at `/`.
3. `python3 catalog_sync.py` — fetches and caches the item catalog
   (`catalog_cache.json`, gitignored, regenerate any time).
4. `python3 app.py` for local testing (http://127.0.0.1:8420), or run it
   under gunicorn/systemd for real deployment — see `deploy/`.

`users.json` (gitignored) stores the account IDs of everyone you've added
via the Players panel; it's created automatically on first use. Likewise
gitignored and created on first use: `checked_parts.json` (parts found,
`{component: count}` per item), `owned_items.json` (built-but-unleveled
gear) and `arcanes.json` (arcane stacks per rank). `catalog_sync.py` also
writes `arcanes_cache.json`, the arcane catalog.

## Deployment reference

`deploy/` has the exact systemd units and nginx config this runs under in
production:

- `warframe-tracker.service` — gunicorn, **single worker only**. The
  in-memory rate limiter and profile cache aren't shared across processes,
  so multiple workers would silently break both (see Security notes).
- `warframe-catalog-sync.service` / `.timer` — refreshes the item catalog
  daily.
- `nginx-warframe-tracker.conf` — reference vhost (static frontend +
  `/api/` reverse-proxied to gunicorn on `127.0.0.1:8420`); update
  `server_name` and the cert paths for your own domain.

## Security notes

- `/api/mastery` and `/api/users` are rate-limited (30 req/60s per IP) and
  cache upstream profile fetches for 5 minutes, capped at 200 entries, to
  keep the server from being used as an unthrottled relay against
  `api.warframe.com`.
- No auth on adding a player is intentional, not an oversight — the data
  exposed is the same public profile-viewer data anyone can already see
  in-game. Don't add anything more sensitive (e.g. the private inventory
  API) behind this app without adding real authentication first.
- Content-Security-Policy is locked to same-origin; third-party data
  (item wiki links) is validated as `https://` and HTML-escaped before
  rendering.
- Keep this on a **single gunicorn worker** — see the note under
  Deployment reference above.
- The write endpoints (`POST /api/checked-parts`, `/api/owned-items`,
  `/api/arcanes`) are unauthenticated and rate-limited like the rest. Anyone
  who can reach the site can edit any tracked player's farming notes. That's
  fine for a personal or friends instance, since they only affect this
  tracker's own bookkeeping and never the game or account. Put it behind
  auth (e.g. nginx basic auth on `/api/` POSTs) if that matters for yours.
  Inputs are validated against the catalog and bounded.

## Credits

Item data and images from [WFCD](https://github.com/WFCD) (Warframe
Community Developers), used under their MIT license. Not affiliated with
or endorsed by Digital Extremes.
