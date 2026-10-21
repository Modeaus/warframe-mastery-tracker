# Screen reader

Optional companion to the tracker. One trigger takes a screenshot of
Warframe, works out which inventory screen is open, OCRs its tiles, and
updates the tracker with what the public profile API can't see:

| Screen | What it records |
|---|---|
| Inventory → **Prime Parts**, **Miscellaneous** | How many of each part/blueprint of a still-missing item you hold, including the ×2 count badges (Kestrel Prime needs 2 Blades). Misc holds the non-prime ones. |
| Inventory → **Warframes, Weapons, Companions, Vehicles, …** | Items the tracker lists as missing that are in your inventory: built but never leveled, so the profile has no XP for them yet. |
| Arsenal → **Arcanes** (manager, one category tab per screen; the *Incomplete* tab covers everything unfinished) | Copies held at each rank, and which arcanes are uncollected. Preferred over the inventory's Arcanes tab, which also works. |

Only what's visible is updated. Nothing off-screen is ever unchecked or
un-owned. Scroll and trigger again for the next page.

## Requirements

Written and tested on **Linux, KDE Plasma (Wayland)**, with Warframe running
under Proton, borderless, at 2560×1440 (pixel thresholds scale with height):

- `spectacle` for the screenshot. The game renders through DX12/Vulkan, so
  plain X11 grabs come back black; X11 (via XWayland) is used only to find the
  game window's position.
- `notify-send` for results.
- Python 3 with `pip install -r requirements.txt` (RapidOCR runs locally on
  CPU, capped at 4 threads so it doesn't take frames from the game).

Other desktops need a different `capture.grab()`; everything after the
screenshot is platform-independent.

## Setup

```
cd reader
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
export WARFRAME_TRACKER_URL=https://your-domain.example   # default http://127.0.0.1:8420
```

Set `WARFRAME_TRACKER_URL` wherever the trigger runs it (e.g. in `scan.sh`).
It posts as the tracker's default player (`account_id.txt`).

## Triggering a scan

> **Note:** this was built around a **Stream Deck** button. If you don't have
> one, bind `scan.sh` to a **keyboard shortcut** instead. In KDE: System
> Settings → Shortcuts → Add New → Command, with command
> `bash /path/to/reader/scan.sh`. Results arrive as a desktop notification
> either way. The Stream Deck icon updates in `button.py` quietly do nothing
> without one.

With a Stream Deck (via [streamdeck-ui](https://github.com/streamdeck-linux-gui/streamdeck-linux-gui)),
set a button's command to `bash /path/to/reader/scan.sh` and run
`.venv/bin/python make_icons.py` for its idle/busy/found icons (written to
`~/Pictures/StreamDeck/`). The button is found by its command, so it can be
moved around the deck.

Keep the mouse off the grid while scanning; hover tooltips are filtered
out, but the tile under the cursor may misread. Each scan is logged to
`~/.cache/warframe-reader.log`, including tiles that matched nothing (handy
for spotting rows a scroll skipped).

## How it reads the screen

- `capture.py`: screenshot cropped to the Warframe window.
- `reader.py`: finds the screen from its section label (or the Arcanes
  manager's page title), regroups OCR lines into tiles in reading order, and
  reads per-tile extras: count badges from an upscaled corner crop (inventory)
  or `xN` text (Arcanes manager); arcane rank = filled diamonds, found by
  locating the diamond row and testing each centre dot; uncollected arcanes
  by their dimmed icons. An unfamiliar tab whose tiles read `Name [rank]` is
  treated as equipment.
- `tracker.py`: API client and matching. Exact names first; fuzzy only for
  small OCR slips, and the item name must match on its own and agree on
  Prime vs non-Prime, so "Baruuk Prime Chassis" can't match "Xaku Prime
  Chassis" and "Akarius Blueprint" can't match "Akarius Prime Blueprint".
- `scan.py`: one handler per screen type. To support a new screen, add its
  label in `reader.py` and a `scan_*` handler here.
