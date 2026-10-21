#!/usr/bin/env python3
"""Stream Deck press: screenshot Warframe once, work out which inventory
screen is showing, and update the mastery tracker from its visible tiles:

  Prime Parts, Miscellaneous -- how many of each part/blueprint of a
                       still-missing item you hold (reads the x2 count
                       badges); Misc holds the non-prime ones
  Arcanes           -- copies held at each rank (filled diamonds + badge)
  Warframes, Primary, Secondary, Melee, ... -- items the tracker lists as
                       missing but that are in your inventory (built, never
                       leveled, so the profile API can't see them yet)

Only visible tiles are touched -- something out of view or used in
crafting isn't evidence it was never found, so nothing off-screen is ever
unchecked or un-owned.
"""
import fcntl
import subprocess
import sys
import time
from pathlib import Path

import button
import capture
import reader
from tracker import Tracker

LOCK_PATH = Path(f"/run/user/{__import__('os').getuid()}/warframe-reader.lock")


def notify(title: str, body: str = ""):
    subprocess.run(["notify-send", "-a", "Warframe Reader", "-i", "warframe", title, body],
                   capture_output=True)


def scan_prime_parts(img, header, tracker: Tracker):
    tiles = reader.read_tiles(img, header)
    changed = []
    for name, count in tiles:
        m = tracker.match(name)
        if m is None:
            print(f"  unmatched: {name!r}", flush=True)
            continue
        unique_name, component, label, _ = m
        key = (unique_name, component)
        required = tracker.required.get(key, 1)
        count = min(count, required)
        if tracker.found.get(key, 0) == count:
            continue
        tracker.set_found(unique_name, component, count)
        if required > 1:
            label = f"{label} {count}/{required}"
        changed.append(label)
        print(f"set: {label}  (read as {name!r} x{count})", flush=True)
    return changed, len(tiles)


def scan_arcanes(img, header, tracker: Tracker, manager: bool = False):
    # Inventory (Arcanes tab) and the Arcanes manager both give
    # (name, rank, count); the manager also lists uncollected arcanes as
    # count 0, which clears anything stale recorded for them.
    tiles = (reader.read_arcane_manager if manager else reader.read_arcanes)(img, header)
    tracker.load_arcanes()
    stacks = {}  # uniqueName -> {rank: count}
    order = []   # uniqueName per matched tile, in reading order
    for name, rank, count in tiles:
        a = tracker.match_arcane(name)
        if a is None:
            continue
        rank = min(rank, a["maxRank"])
        ranks = stacks.setdefault(a["uniqueName"], {})
        if count:
            ranks[str(rank)] = ranks.get(str(rank), 0) + count
        order.append(a["uniqueName"])
    if not order:
        return [], len(tiles)
    # An arcane at either end of the visible grid may have more tiles on
    # the neighbouring page: merge those instead of replacing.
    edge = {order[0], order[-1]}
    by_name = {a["uniqueName"]: a for a in tracker.arcanes.values()}
    updates, changed = [], []
    for unique_name, ranks in stacks.items():
        old = {r: n for r, n in by_name[unique_name]["ranks"].items() if n}
        merge = unique_name in edge
        if (dict(old, **ranks) if merge else ranks) == old:
            continue
        updates.append({"uniqueName": unique_name, "ranks": ranks, "merge": merge})
    if updates:
        result = tracker.set_arcanes(updates)
        for u in updates:
            a = by_name[u["uniqueName"]]
            copies = result[u["uniqueName"]]["copies"]
            ranks = result[u["uniqueName"]]["ranks"]
            best = max((int(r) for r in ranks), default=None)
            state = ("not collected" if best is None else
                     "maxed" if best == a["maxRank"] else f"{copies}/{a['copiesToMax']} copies")
            changed.append(f"{a['name']}: {state}")
            print(f"arcane: {a['name']} {u['ranks']}{' (merged)' if u['merge'] else ''} -> {state}", flush=True)
    return changed, len(tiles)


def scan_equipment(img, header, tiles, tracker: Tracker):
    changed = []
    for name, rank in tiles:
        m = tracker.match_item(name)
        if m is None:
            continue
        unique_name, label = m
        tracker.set_owned(unique_name)
        changed.append(f"{label}: owned, rank {rank if rank is not None else '?'}")
        print(f"owned: {label}  (read as {name!r} [{rank}])", flush=True)
    return changed, len(tiles)


def scan() -> int:
    img = capture.grab()
    if img is None:
        notify("Warframe isn't running")
        return 1
    screen = reader.find_screen(img)
    if screen is None:
        print(f"{time.strftime('%F %T')} no recognized inventory screen", flush=True)
        notify("Not on an inventory screen",
               "Open Inventory (Prime Parts, Warframes, weapons...), then press again.")
        return 1

    name, header = screen
    tracker = Tracker()
    tracker.refresh()
    if name in reader.PARTS_SCREENS:
        changed, read = scan_prime_parts(img, header, tracker)
    elif name == reader.ARCANES:
        changed, read = scan_arcanes(img, header, tracker)
    elif name == reader.ARCANE_MANAGER:
        changed, read = scan_arcanes(img, header, tracker, manager=True)
    elif name in reader.EQUIPMENT_SCREENS:
        changed, read = scan_equipment(img, header, reader.read_equipment(img, header), tracker)
    else:
        tiles = reader.read_equipment(img, header)
        if not reader.looks_like_equipment(tiles):
            print(f"{time.strftime('%F %T')} unhandled screen {header.text!r} "
                  f"({len(tiles)} tiles, e.g. {[t for t, _ in tiles[:3]]})", flush=True)
            notify(f"Don't know the {header.text.title()} screen yet",
                   "Logged it in ~/.cache/warframe-reader.log so support can be added.")
            return 1
        print(f"  {header.text!r}: unfamiliar tab with [rank] tiles, treating as equipment", flush=True)
        changed, read = scan_equipment(img, header, tiles, tracker)

    print(f"{time.strftime('%F %T')} {name}: read {read} tiles, updated {len(changed)}", flush=True)
    if changed:
        notify(f"Updated {len(changed)} item{'s' * (len(changed) > 1)}", "\n".join(changed))
    else:
        notify("Nothing new", f"Read {read} tiles on {header.text.title()}; no changes for missing items.")
    return 0 if changed else 2


def main():
    with open(LOCK_PATH, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0  # a scan is already running; ignore double-presses
        button.set_icon("busy")
        result = 1
        try:
            result = scan()
        except OSError as e:
            notify("Couldn't reach the tracker", str(e))
        finally:
            if result == 0:
                button.set_icon("found")
                time.sleep(2)
            button.set_icon("idle")
    return result


if __name__ == "__main__":
    sys.exit(main())
