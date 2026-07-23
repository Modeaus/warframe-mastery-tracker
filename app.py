#!/usr/bin/env python3
import json
import re
import time
import urllib.request
from pathlib import Path

from flask import Flask, abort, jsonify, request

APP_DIR = Path(__file__).parent
CATALOG_PATH = APP_DIR / "catalog_cache.json"
USERS_PATH = APP_DIR / "users.json"
CHECKED_PARTS_PATH = APP_DIR / "checked_parts.json"
DEFAULT_ACCOUNT_ID = (APP_DIR / "account_id.txt").read_text().strip()

PROFILE_CACHE_SECONDS = 300
MAX_PROFILE_CACHE_ENTRIES = 200
MAX_TRACKED_USERS = 50
ACCOUNT_ID_RE = re.compile(r"^[0-9a-fA-F]{24}\Z")
MAX_FARMING_TARGETS = 25
RARITY_SORT_ORDER = {"Common": 0, "Uncommon": 1, "Rare": 2}
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_MAX_REQUESTS = 30
RATE_LIMIT_MAX_TRACKED_IPS = 1000
_PRIVATE_USE_RANGES = [(0xE000, 0xF8FF), (0xF0000, 0xFFFFD), (0x100000, 0x10FFFD)]
_PRIVATE_USE_PATTERN = "[" + "".join(
    chr(lo) + "-" + chr(hi) for lo, hi in _PRIVATE_USE_RANGES
) + "]"
PRIVATE_USE_RE = re.compile(_PRIVATE_USE_PATTERN)

app = Flask(__name__, static_folder="static", static_url_path="")

_rate_limit_hits = {}  # ip -> list[timestamp], pruned/bounded on each check


def client_ip() -> str:
    # nginx sets X-Real-IP; fall back to the direct peer for local testing.
    return request.headers.get("X-Real-IP", request.remote_addr) or "unknown"


def rate_limited() -> bool:
    ip = client_ip()
    now = time.time()

    hits = _rate_limit_hits.setdefault(ip, [])
    while hits and now - hits[0] > RATE_LIMIT_WINDOW_SECONDS:
        hits.pop(0)

    if len(hits) >= RATE_LIMIT_MAX_REQUESTS:
        return True

    hits.append(now)

    if not hits:
        del _rate_limit_hits[ip]
    elif len(_rate_limit_hits) > RATE_LIMIT_MAX_TRACKED_IPS:
        # Defensive cap so a flood of spoofed/distinct source IPs can't grow
        # this dict without bound; drop an arbitrary entry to stay bounded.
        _rate_limit_hits.pop(next(iter(_rate_limit_hits)))

    return False


def clean_name(name):
    """Strip private-use-area glyph codepoints Warframe embeds in some names."""
    if not name:
        return name
    return PRIVATE_USE_RE.sub("", name).strip()


_RELIC_TIER_RE = re.compile(r" \((Exceptional|Flawless|Radiant)\)$")


def base_relic_location(location: str) -> str:
    """Collapse relic refinement tiers (Exceptional/Flawless/Radiant) into
    their base (Intact) relic name for the farming-targets aggregation."""
    return _RELIC_TIER_RE.sub("", location)


# Per-item mastery XP cap and max rank are computed in catalog_sync.py
# (catalog_cache.json's "masteryXpCap"/"maxRank" fields) since they vary
# per item, not just per category — Kuva/Tenet weapons cap at rank 40
# instead of 30, and the "Misc" category mixes K-Drive (frame-type) and
# Kitgun (weapon-type) components with different affinity divisors.


def estimate_rank(xp: int, cap: int, max_rank: int) -> int:
    """Invert the affinity formula (cap = divisor * max_rank^2) to estimate
    the current rank from total XP earned so far."""
    if cap <= 0 or max_rank <= 0:
        return max_rank
    divisor = cap / (max_rank ** 2)
    return min(max_rank, int((xp / divisor) ** 0.5))


def points_per_rank(item: dict) -> int:
    """100 mastery points/rank for weapons, 200 for Warframes/companions/
    archwing suits/K-Drives/Plexus (verified against wiki.warframe.com/w/
    Mastery_Rank) — constant regardless of an item's max rank, which is why
    Kuva/Tenet weapons (rank 40) and Necramechs (rank 40) total more points
    than standard gear without needing a different per-rank rate."""
    cap = item.get("masteryXpCap")
    max_rank = item.get("maxRank", 30)
    if not cap or not max_rank:
        return 100
    divisor = cap / (max_rank ** 2)
    return 200 if divisor > 750 else 100


def mastery_rank_xp_threshold(rank: int) -> int:
    """Cumulative mastery XP needed to reach a given Mastery Rank. Ranks 1-30
    follow 2500 * rank^2; Legendary ranks (31+) follow 2,250,000 + 147,500 *
    (rank - 30). Verified against wiki.warframe.com/w/Mastery_Rank."""
    if rank <= 30:
        return 2500 * rank ** 2
    return 2_250_000 + 147_500 * (rank - 30)


def resolve_owned_xp(item: dict, unique_name: str, owned_xp: dict):
    """Check the item's canonical uniqueName plus any known aliases (e.g.
    Sirius & Orion's two internal forms — see catalog_sync.py's
    DUPLICATE_ITEM_ALIASES) and return the highest XP found, or None if the
    item isn't owned under any of them."""
    candidates = [unique_name] + item.get("aliasUniqueNames", [])
    xps = [owned_xp[n] for n in candidates if n in owned_xp]
    return max(xps) if xps else None

_catalog = json.loads(CATALOG_PATH.read_text())
_profile_cache = {}  # account_id -> {"data": ..., "fetched_at": ...}


def load_users() -> dict:
    if USERS_PATH.exists():
        return json.loads(USERS_PATH.read_text())
    return {}


def save_users(users: dict):
    USERS_PATH.write_text(json.dumps(users, indent=2))


def load_checked_parts() -> dict:
    """{account_id: {item_unique_name: [component, ...]}} — components a
    player has manually marked as already found for a still-missing item.
    Not derived from Warframe's API at all (private inventory/component
    ownership isn't exposed there); purely a local farming-progress aid."""
    if CHECKED_PARTS_PATH.exists():
        return json.loads(CHECKED_PARTS_PATH.read_text())
    return {}


def save_checked_parts(data: dict):
    CHECKED_PARTS_PATH.write_text(json.dumps(data, indent=2))


def fetch_profile(account_id: str) -> dict:
    now = time.time()
    cached = _profile_cache.get(account_id)
    if cached and (now - cached["fetched_at"] < PROFILE_CACHE_SECONDS):
        return cached["data"]

    url = f"https://api.warframe.com/cdn/getProfileViewingData.php?playerId={account_id}"
    req = urllib.request.Request(url, headers={"User-Agent": "warframe-tracker/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())

    _profile_cache[account_id] = {"data": data, "fetched_at": now}
    _evict_stale_profile_cache_entries(now)
    return data


def _evict_stale_profile_cache_entries(now: float):
    """Bound _profile_cache's memory: drop expired entries first, then the
    oldest ones if it's still over the cap (e.g. many distinct real account
    IDs queried within one cache window)."""
    if len(_profile_cache) <= MAX_PROFILE_CACHE_ENTRIES:
        return

    stale = [k for k, v in _profile_cache.items() if now - v["fetched_at"] >= PROFILE_CACHE_SECONDS]
    for k in stale:
        del _profile_cache[k]

    if len(_profile_cache) > MAX_PROFILE_CACHE_ENTRIES:
        oldest_first = sorted(_profile_cache.items(), key=lambda kv: kv[1]["fetched_at"])
        for k, _ in oldest_first[: len(_profile_cache) - MAX_PROFILE_CACHE_ENTRIES]:
            del _profile_cache[k]


def build_mastery_payload(account_id: str) -> dict:
    profile = fetch_profile(account_id)["Results"][0]
    owned_xp = {x["ItemType"]: x["XP"] for x in profile["LoadOutInventory"]["XPInfo"]}
    checked_parts = load_checked_parts().get(account_id, {})

    missing_by_category = {}
    in_progress_by_category = {}
    category_totals = {}  # cat -> {"owned": N, "total": M}
    owned_count = 0
    total_mastery_points = 0
    # location -> { item_name: {chance, rarity, component} } keeping the best chance seen
    location_map = {}

    for unique_name, item in _catalog.items():
        # Some items (e.g. Kitgun Chambers, buildable as either a Primary or
        # Secondary weapon) display under more than one category with a
        # single shared mastery credit — counted once, listed everywhere.
        display_categories = [item["category"]] + item.get("alsoInCategories", [])
        for dc in display_categories:
            category_totals.setdefault(dc, {"owned": 0, "total": 0})["total"] += 1

        xp = resolve_owned_xp(item, unique_name, owned_xp)
        if xp is not None:
            owned_count += 1
            cap = item.get("masteryXpCap")
            max_rank = item.get("maxRank", 30)
            rank = estimate_rank(xp, cap, max_rank) if cap else max_rank
            total_mastery_points += points_per_rank(item) * rank
            for dc in display_categories:
                category_totals[dc]["owned"] += 1
                if cap and xp < cap:
                    in_progress_by_category.setdefault(dc, []).append({
                        "name": item["name"],
                        "imageName": item["imageName"],
                        "isPrime": item["isPrime"],
                        "xp": xp,
                        "maxXp": cap,
                        "rank": estimate_rank(xp, cap, max_rank),
                        "maxRank": max_rank,
                        "progressPct": round(100 * xp / cap, 1),
                    })
            continue

        sources = sorted(
            item["sources"],
            key=lambda s: (s["chance"] is None, -(s["chance"] or 0)),
        )

        item_checked_components = checked_parts.get(unique_name, [])

        for dc in display_categories:
            missing_by_category.setdefault(dc, []).append({
                "uniqueName": unique_name,
                "name": item["name"],
                "imageName": item["imageName"],
                "masteryReq": item["masteryReq"],
                "isPrime": item["isPrime"],
                "wikiaUrl": item["wikiaUrl"],
                "buildable": item["buildable"],
                "sources": sources,
                "acquisitionNote": item.get("acquisitionNote"),
                "checkedComponents": item_checked_components,
            })

        for s in sources:
            loc = s["location"]
            if not loc:
                continue
            if s["component"] in item_checked_components:
                continue  # already found — exclude from targeted farming
            norm_loc = base_relic_location(loc)
            is_intact_tier = loc == norm_loc

            bucket = location_map.setdefault(norm_loc, {})
            entry = bucket.get(item["name"])
            chance = s["chance"] or 0

            if entry is None:
                entry = {
                    "chance": s["chance"],
                    "rarity": s["rarity"],
                    "component": s["component"],
                    "imageName": item["imageName"],
                }
                bucket[item["name"]] = entry
            elif chance > (entry["chance"] or 0):
                entry["chance"] = s["chance"]
                entry["component"] = s["component"]

            # Refinement tiers can shift a reward's percentage-bucket rarity
            # label (e.g. Rare at Intact can read as Uncommon at Radiant).
            # The Intact-tier label is the canonical/stable rarity.
            if is_intact_tier:
                entry["rarity"] = s["rarity"]

    for cat in missing_by_category:
        missing_by_category[cat].sort(key=lambda i: i["name"])

    for cat in in_progress_by_category:
        in_progress_by_category[cat].sort(key=lambda i: -i["progressPct"])

    farming_targets = []
    for location, items in location_map.items():
        farming_targets.append({
            "location": location,
            "itemCount": len(items),
            "items": [
                {"name": name, **info}
                for name, info in sorted(
                    items.items(),
                    key=lambda kv: (RARITY_SORT_ORDER.get(kv[1]["rarity"], 1), kv[0]),
                )
            ],
        })
    farming_targets.sort(key=lambda t: t["itemCount"], reverse=True)

    total = len(_catalog)
    current_rank = profile.get("PlayerLevel", 0)
    rank_floor = mastery_rank_xp_threshold(current_rank)
    rank_ceiling = mastery_rank_xp_threshold(current_rank + 1)
    # Our catalog is a few items short of the true game total (see README),
    # so total_mastery_points can undercount. The profile API's PlayerLevel
    # is ground truth for the current rank, so never show XP below what
    # that rank actually requires — clamp up to the floor if we're short.
    total_mastery_points = max(total_mastery_points, rank_floor)
    mastery_progress_pct = (
        round(min(100, 100 * (total_mastery_points - rank_floor) / (rank_ceiling - rank_floor)), 1)
        if rank_ceiling > rank_floor else 0
    )

    return {
        "accountId": account_id,
        "displayName": clean_name(profile.get("DisplayName")),
        "masteryRank": current_rank,
        "masteryXp": total_mastery_points,
        "masteryXpForCurrentRank": rank_floor,
        "masteryXpForNextRank": rank_ceiling,
        "masteryProgressPct": mastery_progress_pct,
        "founder": profile.get("Founder", 0),
        "guildName": clean_name(profile.get("GuildName")),
        "guildTier": profile.get("GuildTier"),
        "totalMasterable": total,
        "totalOwned": owned_count,
        "completionPct": round(100 * owned_count / total, 1) if total else 0,
        "categoryCompletion": category_totals,
        "missingByCategory": missing_by_category,
        "inProgressByCategory": in_progress_by_category,
        "farmingTargets": farming_targets[:MAX_FARMING_TARGETS],
    }


@app.route("/api/mastery")
def mastery():
    if rate_limited():
        abort(429, "too many requests, slow down")
    account_id = request.args.get("player", DEFAULT_ACCOUNT_ID)
    if not ACCOUNT_ID_RE.match(account_id):
        abort(400, "invalid account id")
    try:
        return jsonify(build_mastery_payload(account_id))
    except Exception:
        abort(502, "could not fetch profile for that account id")


def build_category_payload(account_id: str, category_name: str) -> dict:
    profile = fetch_profile(account_id)["Results"][0]
    owned_xp = {x["ItemType"]: x["XP"] for x in profile["LoadOutInventory"]["XPInfo"]}

    items = []
    for unique_name, item in _catalog.items():
        display_categories = [item["category"]] + item.get("alsoInCategories", [])
        if category_name not in display_categories:
            continue

        max_rank = item.get("maxRank", 30)
        cap = item.get("masteryXpCap")
        xp = resolve_owned_xp(item, unique_name, owned_xp)
        owned = xp is not None
        rank = estimate_rank(xp, cap, max_rank) if (owned and cap) else (max_rank if owned else 0)

        items.append({
            "name": item["name"],
            "imageName": item["imageName"],
            "isPrime": item["isPrime"],
            "owned": owned,
            "rank": rank,
            "maxRank": max_rank,
            "progressPct": round(100 * rank / max_rank, 1) if max_rank else 0,
            "crossListed": len(display_categories) > 1,
        })

    items.sort(key=lambda i: i["name"])
    return {"category": category_name, "items": items}


@app.route("/api/category/<category_name>")
def category_detail(category_name):
    if rate_limited():
        abort(429, "too many requests, slow down")
    account_id = request.args.get("player", DEFAULT_ACCOUNT_ID)
    if not ACCOUNT_ID_RE.match(account_id):
        abort(400, "invalid account id")
    try:
        return jsonify(build_category_payload(account_id, category_name))
    except Exception:
        abort(502, "could not fetch profile for that account id")


@app.route("/api/checked-parts", methods=["POST"])
def toggle_checked_part():
    if rate_limited():
        abort(429, "too many requests, slow down")
    body = request.get_json(silent=True) or {}
    account_id = (body.get("accountId") or "").strip()
    unique_name = (body.get("uniqueName") or "").strip()
    component = (body.get("component") or "").strip()
    checked = bool(body.get("checked"))

    if not ACCOUNT_ID_RE.match(account_id):
        abort(400, "invalid account id")

    item = _catalog.get(unique_name)
    if item is None:
        abort(400, "unknown item")
    valid_components = {s["component"] for s in item.get("sources", [])}
    if component not in valid_components:
        abort(400, "unknown component for this item")

    all_checked = load_checked_parts()
    account_checked = all_checked.setdefault(account_id, {})
    components = set(account_checked.get(unique_name, []))
    if checked:
        components.add(component)
    else:
        components.discard(component)

    if components:
        account_checked[unique_name] = sorted(components)
    else:
        account_checked.pop(unique_name, None)
    if not account_checked:
        all_checked.pop(account_id, None)

    save_checked_parts(all_checked)
    return jsonify({"uniqueName": unique_name, "checkedComponents": sorted(components)})


@app.route("/api/users", methods=["GET"])
def list_users():
    users = load_users()
    return jsonify([{"accountId": k, "displayName": v} for k, v in users.items()])


@app.route("/api/users", methods=["POST"])
def add_user():
    if rate_limited():
        abort(429, "too many requests, slow down")
    body = request.get_json(silent=True) or {}
    account_id = (body.get("accountId") or "").strip()
    if not ACCOUNT_ID_RE.match(account_id):
        abort(400, "invalid account id format")

    users = load_users()
    if account_id not in users and len(users) >= MAX_TRACKED_USERS:
        abort(400, "too many tracked users")

    try:
        profile = fetch_profile(account_id)["Results"][0]
    except Exception:
        abort(502, "could not find a profile for that account id")

    display_name = clean_name(profile.get("DisplayName")) or account_id
    users[account_id] = display_name
    save_users(users)
    return jsonify({"accountId": account_id, "displayName": display_name})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8420)
