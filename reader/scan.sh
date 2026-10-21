#!/bin/bash
# One scan of whatever Warframe inventory screen is open -> mastery tracker.
# Trigger it from a Stream Deck button (streamdeck-ui) or, without one, a
# global keyboard shortcut (e.g. KDE System Settings > Shortcuts > Add
# Command: bash /path/to/reader/scan.sh). Results show as a notification.
cd "$(dirname "$0")" && exec nice -n 10 .venv/bin/python scan.py >> "$HOME/.cache/warframe-reader.log" 2>&1
