#!/usr/bin/env python3
"""Stream Deck button icon for the reader: `button.py idle|busy|found`.

Optional. Written for streamdeck-ui (its `streamdeckc` CLI and
~/.streamdeck_ui.json). Without a Stream Deck, bind scan.sh to a keyboard
shortcut instead (see README) -- these icon updates then quietly do nothing.

The button is found by its command (anything running scan.sh) in
~/.streamdeck_ui.json instead of a hardcoded index, so dragging it to
another slot in streamdeck-ui doesn't break the icon updates.
"""
import json
import subprocess
import sys
from pathlib import Path

ICONS = Path.home() / "Pictures" / "StreamDeck"
UI_STATE = Path.home() / ".streamdeck_ui.json"


def locate():
    """(deck serial, page, button) of the reader button, or None."""
    state = json.loads(UI_STATE.read_text())["state"]
    for serial, deck in state.items():
        for page, buttons in deck["buttons"].items():
            for index, button in buttons.items():
                for s in button.get("states", {}).values():
                    if "warframe-reader/scan.sh" in s.get("command", ""):
                        return serial, page, index
    return None


def set_icon(name: str):
    try:
        where = locate()
    except (OSError, ValueError, KeyError):
        return  # no streamdeck-ui config: not using a Stream Deck
    if where is None:
        return
    serial, page, index = where
    decks = list(json.loads(UI_STATE.read_text())["state"])
    try:
        subprocess.run(["streamdeckc", "-a", "set_icon", "-d", str(decks.index(serial)),
                        "-p", page, "-b", index, "--icon", str(ICONS / f"warframe-reader-{name}.png")],
                       capture_output=True)
    except OSError:
        pass  # streamdeckc not installed


if __name__ == "__main__":
    set_icon(sys.argv[1])
