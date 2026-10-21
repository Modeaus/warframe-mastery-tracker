"""Screenshot just the Warframe window.

The game renders through DX12/Vulkan, so plain X11 grabs of its XWayland
window come back black; the pixels have to come from KWin via spectacle.
X11 is still used for the window's geometry, to crop the multi-monitor
screenshot down to the game.
"""
import subprocess
import tempfile
from pathlib import Path

from PIL import Image
from Xlib import X, display

WINDOW_TITLE = "Warframe"


def warframe_geometry():
    """(left, top, width, height) of the game window, or None if not running."""
    d = display.Display()
    try:
        root = d.screen().root
        clients = root.get_full_property(d.intern_atom("_NET_CLIENT_LIST"), X.AnyPropertyType)
        for wid in clients.value if clients else []:
            w = d.create_resource_object("window", wid)
            name = w.get_full_property(d.intern_atom("_NET_WM_NAME"), 0)
            title = name.value.decode(errors="ignore") if name else (w.get_wm_name() or "")
            if title == WINDOW_TITLE:
                g = w.get_geometry()
                pos = w.translate_coords(root, 0, 0)
                return (-pos.x, -pos.y, g.width, g.height)
        return None
    finally:
        d.close()


def grab() -> Image.Image | None:
    geo = warframe_geometry()
    if geo is None:
        return None
    left, top, width, height = geo
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "shot.png"
        subprocess.run(["spectacle", "-b", "-n", "-f", "-o", str(out)],
                       check=True, capture_output=True, timeout=15)
        with Image.open(out) as full:
            return full.crop((left, top, left + width, top + height)).convert("RGB")


if __name__ == "__main__":
    import sys
    img = grab()
    print("geometry:", warframe_geometry())
    if img:
        img.save(sys.argv[1] if len(sys.argv) > 1 else "test.png")
        print("saved", img.size)
