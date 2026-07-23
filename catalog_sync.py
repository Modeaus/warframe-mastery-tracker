#!/usr/bin/env python3
"""Fetch WFCD's warframe-items data and build a local cache of masterable
items (Warframes, weapons, companions, archwing) with acquisition info."""

import json
import re
import urllib.request
from pathlib import Path

CATEGORIES = [
    "Warframes", "Primary", "Secondary", "Melee",
    "Sentinels", "SentinelWeapons", "Pets",
    "Archwing", "Arch-Gun", "Arch-Melee",
    "Misc",  # K-Drive and Kitgun components live here (verified: only 9
             # masterable items total in this file, nothing else leaks in)
]

RAW_BASE = "https://raw.githubusercontent.com/WFCD/warframe-items/master/data/json"

# WFCD publishes a dedicated image CDN specifically for third-party tools
# (see their README) — using that instead of hotlinking wiki.warframe.com's
# own asset server directly. Downloaded once per image and cached in
# static/icons/; re-synced only for images not already present locally.
ICON_CDN_BASE = "https://cdn.warframestat.us/img"
ICONS_DIR = Path(__file__).parent / "static" / "icons"
CACHE_PATH = Path(__file__).parent / "catalog_cache.json"

_RELIC_TIER_RE = re.compile(r" \((Exceptional|Flawless|Radiant)\)$")

# Affinity to reach rank R is `divisor * R^2` (see wiki.warframe.com/w/Affinity).
# Warframes/companions/archwing suits/K-Drives use 1000; weapons (including
# Kitgun components) use 500. "Misc" mixes both K-Drive and Kitgun
# components, so it's resolved per-item via its `type` field instead of
# per-category like everything else.
FRAME_TYPE_CATEGORIES = {"Warframes", "Archwing", "Sentinels", "Pets"}
FRAME_TYPE_MISC_ITEM_TYPES = {"K-Drive Component"}

# Items WFCD flags masterable=False (or omits entirely) despite granting
# mastery in-game, confirmed against wiki.warframe.com/w/Hidden_Mastery and
# the dedicated Amp wiki page. WFCD doesn't model syndicate rank-up rewards
# or quest rewards as "drops", so these get a manual acquisition note
# instead of farmable sources.
#
# Only the Prism (Barrel) component of each Amp grants mastery — Scaffold
# (Chassis) and Brace (Grip) parts don't, despite following the same
# component structure in WFCD's data. Includes the Mote (starter) tier,
# which follows the identical Barrel/Chassis/Grip pattern as the other 7.
AMP_PART_UNIQUE_NAMES = {
    "/Lotus/Weapons/Sentients/OperatorAmplifiers/Set1/Barrel/SentAmpSet1BarrelPartA",  # Raplak Prism
    "/Lotus/Weapons/Sentients/OperatorAmplifiers/Set1/Barrel/SentAmpSet1BarrelPartB",  # Shwaak Prism
    "/Lotus/Weapons/Sentients/OperatorAmplifiers/Set1/Barrel/SentAmpSet1BarrelPartC",  # Granmu Prism
    "/Lotus/Weapons/Sentients/OperatorAmplifiers/Set2/Barrel/SentAmpSet2BarrelPartA",  # Rahn Prism
    "/Lotus/Weapons/Corpus/OperatorAmplifiers/Set1/Barrel/CorpAmpSet1BarrelPartA",  # Cantic Prism
    "/Lotus/Weapons/Corpus/OperatorAmplifiers/Set1/Barrel/CorpAmpSet1BarrelPartB",  # Lega Prism
    "/Lotus/Weapons/Corpus/OperatorAmplifiers/Set1/Barrel/CorpAmpSet1BarrelPartC",  # Klamora Prism
    "/Lotus/Weapons/Sentients/OperatorAmplifiers/SentTrainingAmplifier/SentAmpTrainingBarrel",  # Mote Prism
}
VENARI_UNIQUE_NAMES = {
    "/Lotus/Powersuits/Khora/Kavat/KhoraKavatPowerSuit",
    "/Lotus/Powersuits/Khora/Kavat/KhoraPrimeKavatPowerSuit",
}
# Kitgun Chambers: the "business end" component that actually grants
# mastery (same rule as Amp Prisms — the Grip/Handle doesn't count). Two
# separate lines exist in WFCD's data: SolarisUnited (Catchmoon/Gaze/
# Rattleguts/Tombfinger, type "Kitgun Component") and Infested (Vermisplicer/
# Sporelacer, type "Pistol" — easy to miss since it doesn't match the other
# line's type label). Every chamber can be built into either a Primary or
# Secondary weapon depending on the grip used, and the game's own profile
# cross-lists each one under both categories with the SAME shared XP/mastery
# credit (confirmed by inspecting a real MR6 profile) — not two separately
# tracked items. Counted once toward totals; displayed under both.
KITGUN_CHAMBER_UNIQUE_NAMES = {
    "/Lotus/Weapons/SolarisUnited/Secondary/SUModularSecondarySet1/Barrel/SUModularSecondaryBarrelAPart",  # Catchmoon
    "/Lotus/Weapons/SolarisUnited/Secondary/SUModularSecondarySet1/Barrel/SUModularSecondaryBarrelDPart",  # Gaze
    "/Lotus/Weapons/SolarisUnited/Secondary/SUModularSecondarySet1/Barrel/SUModularSecondaryBarrelCPart",  # Rattleguts
    "/Lotus/Weapons/SolarisUnited/Secondary/SUModularSecondarySet1/Barrel/SUModularSecondaryBarrelBPart",  # Tombfinger
    "/Lotus/Weapons/Infested/Pistols/InfKitGun/Barrels/InfBarrelBeam/InfModularBarrelBeamPart",  # Vermisplicer
    "/Lotus/Weapons/Infested/Pistols/InfKitGun/Barrels/InfBarrelEgg/InfModularBarrelEggPart",  # Sporelacer
}
INFESTED_KITGUN_CHAMBER_NAMES = {
    "/Lotus/Weapons/Infested/Pistols/InfKitGun/Barrels/InfBarrelBeam/InfModularBarrelBeamPart",
    "/Lotus/Weapons/Infested/Pistols/InfKitGun/Barrels/InfBarrelEgg/InfModularBarrelEggPart",
}

MANUAL_INCLUDE_UNIQUE_NAMES = AMP_PART_UNIQUE_NAMES | VENARI_UNIQUE_NAMES | INFESTED_KITGUN_CHAMBER_NAMES

# WFCD flags this masterable=True, but it has no build recipe/components and
# different stats/slot than the real Grimoire — it's the enemy doppelganger
# NPC's weapon variant from the "Whispers in the Walls" quest fight, not a
# player-obtainable item. The wiki's own checklist doesn't track it either.
EXCLUDED_UNIQUE_NAMES = {
    "/Lotus/Weapons/Tenno/Grimoire/TnDoppelgangerGrimoire",
}

AMP_ACQUISITION_NOTE = "Quills/Vox Solaris syndicate rank-up reward, or Toroid trade with Business (Vox Solaris)."
ACQUISITION_NOTES = {
    **{n: AMP_ACQUISITION_NOTE for n in AMP_PART_UNIQUE_NAMES},
    "/Lotus/Powersuits/Khora/Kavat/KhoraKavatPowerSuit": "Automatically unlocked alongside owning Khora.",
    "/Lotus/Powersuits/Khora/Kavat/KhoraPrimeKavatPowerSuit": "Automatically unlocked alongside owning Khora Prime.",
}

# Plexus doesn't exist anywhere in WFCD's dataset (checked Railjack.json,
# which has zero masterable entries and no "Plexus" item at all), so it's
# hand-built rather than sourced from a category file. Its uniqueName is a
# best-effort match (/Lotus/Types/Game/CrewShip/RailJack/DefaultHarness,
# found via a real player's XPInfo) — not independently confirmed by any
# wiki/community source. Flag for review if it doesn't behave as expected.
PLEXUS_UNIQUE_NAME = "/Lotus/Types/Game/CrewShip/RailJack/DefaultHarness"

# Sirius & Orion is presented as two Warframes in-game but occupies a single
# Warframe slot (wiki.warframe.com/w/Sirius_%26_Orion: "Though presented as
# two separate Warframes, Sirius & Orion occupy a single Warframe slot").
# WFCD catalogs each form as its own masterable item, double-counting it.
# Merged into one entry keyed on the canonical uniqueName below; ownership
# checks also look at the alias, since it's unconfirmed whether the game
# tracks affinity under one form or both.
DUPLICATE_ITEM_ALIASES = {
    "/Lotus/Powersuits/SiriusOrion/OrionSuit": ["/Lotus/Powersuits/SiriusOrion/SiriusSuit"],
}


def affinity_divisor(category: str, item: dict) -> int:
    if category == "Misc":
        if item.get("type") == "Amp":
            return 500  # Amps are Operator weapons, weapon-type affinity
        return 1000 if item.get("type") in FRAME_TYPE_MISC_ITEM_TYPES else 500
    return 1000 if category in FRAME_TYPE_CATEGORIES else 500


def is_necramech(item: dict) -> bool:
    return item.get("productCategory") == "MechSuits"


def is_kdrive_component(item: dict) -> bool:
    return item.get("type") in FRAME_TYPE_MISC_ITEM_TYPES


def is_amp_component(item: dict) -> bool:
    return item.get("type") == "Amp"


def resolve_max_rank(item: dict) -> int:
    # WFCD's maxLevelCap field covers Kuva/Tenet weapons but doesn't capture
    # Necramech overleveling (verified against wiki.warframe.com/w/Necramech:
    # Necramechs reach rank 40 via 5 Forma, same mechanic as Kuva/Tenet).
    if item.get("maxLevelCap"):
        return item["maxLevelCap"]
    if is_necramech(item):
        return 40
    return 30


def is_moa_pet(item: dict) -> bool:
    return "MoaPets/MoaPetParts" in item.get("uniqueName", "")


def is_hound_pet(item: dict) -> bool:
    return "ZanukaPets/ZanukaPetParts" in item.get("uniqueName", "")


SIROCCO_UNIQUE_NAME = "/Lotus/Weapons/Operator/Pistols/DrifterPistol/DrifterPistolPlayerWeapon"


def display_category(cat: str, item: dict) -> str:
    """Mirrors the in-game Player Profile's own equipment grouping
    (wiki.warframe.com/w/Player_Profile) and cross-checked directly against
    the raw wikitext of wiki.warframe.com/w/Mastery_Rank_Checklist: Robotic =
    "robotic weapons, Sentinels, MOA (Companion), and Hound (Companion)";
    Companions = Beast companions, including Venari/Venari Prime (the
    checklist counts them here despite the live profile hiding them — see
    Hidden_Mastery); Vehicles = "K-Drives, Archwings, Necramechs, and
    Plexus"; Amps includes Sirocco (the Drifter's pistol — the checklist's
    own Amp section lists it alongside the Amp Prisms, not under Primary).
    Archguns/Archmelees stay their own categories."""
    if cat in ("Sentinels", "SentinelWeapons"):
        return "Robotic"

    if cat == "Pets":
        if is_moa_pet(item) or is_hound_pet(item):
            return "Robotic"
        return "Companions"

    if cat == "Archwing":
        return "Vehicles"

    # Necramechs (in Warframes.json) and K-Drive components (in Misc.json)
    # read as separate equipment types to players, not "Warframes"/"Misc" —
    # group them under one label regardless of which WFCD file they live in.
    if is_necramech(item) or is_kdrive_component(item):
        return "Vehicles"
    if is_amp_component(item) or item.get("uniqueName") == SIROCCO_UNIQUE_NAME:
        return "Amps"
    if item.get("uniqueName") in KITGUN_CHAMBER_UNIQUE_NAMES:
        return "Secondary"  # home category; also cross-listed into Primary
    return cat


def cross_list_categories(item: dict) -> list:
    """Additional categories an item should also be displayed under, beyond
    its home "category", without being counted twice toward totals."""
    if item.get("uniqueName") in KITGUN_CHAMBER_UNIQUE_NAMES:
        return ["Primary"]
    return []


def fetch_category(name: str) -> list:
    url = f"{RAW_BASE}/{name}.json"
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.loads(resp.read())


def classify_relic_rarity(chance):
    """WFCD's rarity label drifts per refinement tier and never uses
    'Common' for relics (verified against the raw data: Intact-tier chances
    cluster at exactly 2% / 11% / 25.33%, but only 'Uncommon' and 'Rare'
    labels are used, with 25.33% wrongly bucketed as 'Uncommon'). Derive the
    canonical three-tier classification from the Intact-tier chance instead.
    """
    if chance is None:
        return None
    if chance >= 20:
        return "Common"
    if chance >= 5:
        return "Uncommon"
    return "Rare"


def extract_acquisition(item: dict) -> list:
    """Collect drop locations from an item's components (or itself)."""
    sources = []
    for comp in item.get("components", []) or [item]:
        for drop in comp.get("drops", []) or []:
            sources.append({
                "component": comp.get("name", item.get("name")),
                "location": drop.get("location"),
                "chance": drop.get("chance"),
                "rarity": drop.get("rarity"),
            })

    # Determine canonical rarity per (component, base relic) from the
    # Intact-tier entry, then apply it across all refinement tiers of that
    # same reward slot so rarity no longer drifts with refinement.
    canonical_rarity = {}
    for s in sources:
        loc = s["location"] or ""
        base = _RELIC_TIER_RE.sub("", loc)
        if loc == base and base.endswith("Relic"):
            canonical_rarity[(s["component"], base)] = classify_relic_rarity(s["chance"])

    for s in sources:
        loc = s["location"] or ""
        base = _RELIC_TIER_RE.sub("", loc)
        key = (s["component"], base)
        if key in canonical_rarity:
            s["rarity"] = canonical_rarity[key]

    return sources


def build_catalog() -> dict:
    catalog = {}
    for cat in CATEGORIES:
        for item in fetch_category(cat):
            unique_name = item["uniqueName"]
            if unique_name in EXCLUDED_UNIQUE_NAMES:
                continue
            if not (item.get("masterable") or unique_name in MANUAL_INCLUDE_UNIQUE_NAMES):
                continue
            sources = extract_acquisition(item)
            max_rank = resolve_max_rank(item)
            divisor = affinity_divisor(cat, item)
            catalog[unique_name] = {
                "name": item.get("name"),
                "category": display_category(cat, item),
                "imageName": item.get("imageName"),
                "masteryReq": item.get("masteryReq", 0),
                "isPrime": item.get("isPrime", False),
                "wikiaUrl": item.get("wikiaUrl"),
                "buildable": bool(item.get("buildPrice") or item.get("marketCost")),
                "sources": sources,
                "maxRank": max_rank,
                "masteryXpCap": divisor * max_rank ** 2,
                "acquisitionNote": ACQUISITION_NOTES.get(unique_name),
                "aliasUniqueNames": [],
                "alsoInCategories": cross_list_categories(item),
            }

    # Plexus doesn't exist in any WFCD category file — see comment above
    # PLEXUS_UNIQUE_NAME for how its uniqueName was determined.
    catalog[PLEXUS_UNIQUE_NAME] = {
        "name": "Plexus",
        "category": "Vehicles",
        "imageName": None,
        "masteryReq": 0,
        "isPrime": False,
        "wikiaUrl": "https://wiki.warframe.com/w/Plexus",
        "buildable": False,
        "sources": [],
        "maxRank": 30,
        "masteryXpCap": 1000 * 30 ** 2,
        "acquisitionNote": "Reward for completing \"The Archwing\" quest.",
        "aliasUniqueNames": [],
        "alsoInCategories": [],
    }

    # Merge known duplicate items (e.g. Sirius & Orion) into their canonical
    # entry so they're only counted once.
    for canonical, aliases in DUPLICATE_ITEM_ALIASES.items():
        if canonical not in catalog:
            continue
        catalog[canonical]["aliasUniqueNames"] = aliases
        catalog[canonical]["name"] = "Sirius & Orion"
        for alias in aliases:
            catalog.pop(alias, None)

    return catalog


def sync_icons(catalog: dict):
    """Download each item's icon once and cache it locally so the frontend
    never hits an external host on page load. Skips anything already
    downloaded, so daily re-syncs only fetch new items."""
    ICONS_DIR.mkdir(parents=True, exist_ok=True)
    downloaded = 0
    failed = 0
    for item in catalog.values():
        image_name = item.get("imageName")
        if not image_name or (ICONS_DIR / image_name).exists():
            continue
        try:
            url = f"{ICON_CDN_BASE}/{image_name}"
            req = urllib.request.Request(url, headers={"User-Agent": "warframe-tracker/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                (ICONS_DIR / image_name).write_bytes(resp.read())
            downloaded += 1
        except Exception as e:
            failed += 1
            print(f"warning: failed to download icon {image_name}: {e}")
    print(f"icons: {downloaded} downloaded, {failed} failed, cached in {ICONS_DIR}")


def main():
    catalog = build_catalog()
    CACHE_PATH.write_text(json.dumps(catalog, indent=2))
    print(f"cached {len(catalog)} masterable items -> {CACHE_PATH}")
    sync_icons(catalog)


if __name__ == "__main__":
    main()
