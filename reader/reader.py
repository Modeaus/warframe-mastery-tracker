"""OCR a Warframe screenshot: work out which inventory screen it shows, and
pull the tiles off its grid (Prime Parts names + counts, or equipment
names + ranks).

Each grid tile prints its name in 1-3 short centered lines under the icon
("Banshee Prime" / "Neuroptics" / "Blueprint"), so OCR lines are regrouped
into tiles by x-center and vertical adjacency. All pixel thresholds are
written for 1440p and scaled to the actual window height.
"""
import re
from dataclasses import dataclass

import numpy as np
from PIL import Image
from rapidocr import RapidOCR
from rapidocr.inference_engine.onnxruntime.main import OrtInferSession

# onnxruntime's thread pool busy-waits between runs by default, which kept
# ~2 cores pegged even while the watcher slept. RapidOCR doesn't expose the
# setting, so wrap its session-options builder to turn spinning off.
_orig_sess_opts = OrtInferSession._init_sess_opts


def _no_spin_sess_opts(cfg):
    opts = _orig_sess_opts(cfg)
    opts.add_session_config_entry("session.intra_op.allow_spinning", "0")
    opts.add_session_config_entry("session.inter_op.allow_spinning", "0")
    return opts


OrtInferSession._init_sess_opts = staticmethod(_no_spin_sess_opts)

_engine = None

PRIME_PARTS = "PRIMEPARTS"
# Same tile layout and count badges as Prime Parts, mixed with resources;
# holds non-prime blueprints and parts (Arbucep Blueprint, Aeolak Barrel...).
PARTS_SCREENS = {PRIME_PARTS, "MISCELLANEOUS"}
# Inventory tabs whose tiles read "Name [rank]". Squashed (letters only);
# anything here is handled as an equipment screen.
EQUIPMENT_SCREENS = {
    "WARFRAMES", "WEAPONS", "PRIMARY", "SECONDARY", "MELEE", "COMPANIONS", "SENTINELS",
    "SENTINELWEAPONS", "ROBOTICWEAPONS", "ARCHWING", "ARCHWINGS", "ARCHGUN",
    "ARCHGUNS", "ARCHMELEE", "ARCHWINGWEAPONS", "AMPS", "KDRIVES", "NECRAMECHS", "VEHICLES",
}
ARCANES = "ARCANES"
# The Arcanes manager (Arsenal -> Arcanes): page *title* "ARCANES", one
# category per screen ("COLLECTED 61/64"), uncollected arcanes dimmed, and
# counts printed as "x5" text rather than the inventory's badge glyph.
ARCANE_MANAGER = "ARCANEMANAGER"
MANAGER_COUNT_RE = re.compile(r"^[xX×]\s?(\d{1,4})$")
# Uncollected arcanes are dimmed: their icon's brightest pixels (95th
# percentile) read <= 55, collected icons >= 92 even when the art is dark.
# (Mean brightness overlapped: dim 18-25 vs collected from 24.)
UNCOLLECTED_ICON_P95 = 75
# OCR sometimes reads an arcane's rank diamonds as text ("DOOOO", "DODDD").
PIP_TEXT_RE = re.compile(r"^[DOQ0CUo◇◆]{1,6}$")


def _is_tooltip(line) -> bool:
    """Hover tooltips print the arcane's name in caps ("ARCANE FURY");
    grid names are title case. Short caps fragments are misread diamonds."""
    return line.text.isupper() and sum(c.isalpha() for c in line.text) >= 4
PIP_PITCH = 13.75   # px between rank diamonds at 1440p
PIP_FILLED = 60     # filled diamond centres read 70-200 (Theorem's are dim), empty ones <= 30
# "Sirius & Orion [0]", "Cyte-09 [30]"; OCR sometimes turns [ into ( or |.
RANK_RE = re.compile(r"^(.*\S)\s*[\[\(|{]\s*(\d{1,2})\s*[\]\)|}]?\s*$")
OCR_THREADS = 4
BADGE_RE = re.compile(r"^\W*\d+$")  # "2", "©2" ownership-count badges
BADGE_MIN_SCORE = 0.6


@dataclass
class Line:
    text: str
    cx: float
    top: float
    bottom: float


def _get_engine() -> RapidOCR:
    global _engine
    if _engine is None:
        # Capped threads so OCR doesn't steal frames from the game.
        _engine = RapidOCR(params={
            "Global.log_level": "error",
            "EngineConfig.onnxruntime.intra_op_num_threads": OCR_THREADS,
        })
    return _engine


def _ocr(img: Image.Image, offset=(0, 0)) -> list[Line]:
    # Explicit flags: RapidOCR remembers use_det=False from badge reads.
    res = _get_engine()(np.array(img), use_det=True, use_cls=True, use_rec=True)
    if res.txts is None:
        return []
    lines = []
    for box, text in zip(res.boxes, res.txts):
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        lines.append(Line(text.strip(), offset[0] + sum(xs) / 4,
                          offset[1] + min(ys), offset[1] + max(ys)))
    return lines


def _squash(s: str) -> str:
    return re.sub(r"[^A-Z]", "", s.upper())


def find_screen(img: Image.Image):
    """(screen key, header Line) for an inventory grid, else None. The key
    is the squashed section label: PRIME_PARTS, one of EQUIPMENT_SCREENS,
    or an unfamiliar label (e.g. a tab not seen yet) -- the caller decides
    what to do with those. Only the top-left quarter is OCR'd (at half
    size). Known labels win; otherwise the label is found by position: an
    all-caps line at the far left, ~30% down (434px at 1440p on every
    inventory tab seen so far)."""
    w, h = img.size
    quarter = img.crop((0, 0, w // 2, h // 2)).resize((w // 4, h // 4))
    lines = [Line(l.text, l.cx * 2, l.top * 2, l.bottom * 2) for l in _ocr(quarter)]
    for line in sorted(lines, key=lambda l: l.top):
        key = _squash(line.text)
        if key == ARCANES and line.top < h * 0.1:
            return ARCANE_MANAGER, line
        if key in PARTS_SCREENS or key in EQUIPMENT_SCREENS or key == ARCANES:
            return key, line
    for line in sorted(lines, key=lambda l: l.top):
        key = _squash(line.text)
        if (len(key) >= 3 and line.text.upper() == line.text and line.cx < w * 0.1
                and h * 0.25 < line.top < h * 0.36):
            return key, line
    return None


def looks_like_equipment(tiles: list[tuple[str, int | None]]) -> bool:
    """An unfamiliar tab is treated as equipment if most tiles carry a
    "[rank]" -- that's the format of every gear grid."""
    return len(tiles) >= 4 and sum(r is not None for _, r in tiles) >= 0.6 * len(tiles)


def grid_region(img: Image.Image, header: Line):
    """Crop box for the tile grid: below the header, left of the sell panel."""
    w, h = img.size
    return (0, int(header.bottom + 15 * h / 1440), int(w * 0.85), int(h * 0.93))


def _tile_lines(img: Image.Image, header: Line, box=None, extras=None) -> list[list[Line]]:
    """OCR the grid and regroup its text lines into one list per tile.
    Lines matching a count format are left out of the names; pass a list
    as `extras` to collect them."""
    h = img.size[1]
    scale = h / 1440
    box = box or grid_region(img, header)
    lines = [l for l in _ocr(img.crop(box), offset=box[:2]) if l.text]
    counts = [l for l in lines if BADGE_RE.match(l.text) or MANAGER_COUNT_RE.match(l.text)]
    if extras is not None:
        extras.extend(counts)
    names = [l for l in lines if l not in counts]

    tiles: list[list[Line]] = []
    for line in sorted(names, key=lambda l: l.top):
        for tile in tiles:
            last = tile[-1]
            if abs(last.cx - line.cx) < 55 * scale and -15 * scale < line.top - last.bottom < 12 * scale:
                tile.append(line)
                break
        else:
            tiles.append([line])
    # Reading order: rows (tiles in a row share a text bottom to within a
    # few px; rows are ~150px apart), then left to right.
    tiles.sort(key=lambda t: max(l.bottom for l in t))
    rows: list[list[list[Line]]] = []
    for tile in tiles:
        if rows and max(l.bottom for l in tile) - max(l.bottom for l in rows[-1][0]) < 40 * scale:
            rows[-1].append(tile)
        else:
            rows.append([tile])
    return [t for row in rows for t in sorted(row, key=lambda t: t[0].cx)]


def read_tiles(img: Image.Image, header: Line) -> list[tuple[str, int]]:
    """Prime Parts: [(tile name, owned count)] for the visible grid. The
    count comes from the badge in the tile's top-left corner, which the game
    only draws when you own more than one; no badge means 1."""
    scale = img.size[1] / 1440
    return [(" ".join(l.text for l in tile), _badge_count(img, tile, scale))
            for tile in _tile_lines(img, header)]


def read_arcanes(img: Image.Image, header: Line) -> list[tuple[str, int, int]]:
    """Arcanes: [(name, rank, count)] in reading order. Each rank of an
    arcane is its own tile; rank = filled diamonds under the name (empty
    ones are unranked slots), count = the top-left badge (copies at that
    rank). 3-rank arcanes use the middle three of the same five slots."""
    scale = img.size[1] / 1440
    gray = np.asarray(img.convert("L"), dtype=np.int16)
    out = []
    for tile in _tile_lines(img, header):
        names = [l for l in tile if not PIP_TEXT_RE.match(l.text.replace(" ", ""))]
        # All-caps text is the hover tooltip ("ARCANE FURY" + its own rank
        # diamonds), not a grid tile; grid names are title case.
        if not names or any(_is_tooltip(l) for l in names):
            continue
        cx = sum(l.cx for l in names) / len(names)
        bottom = int(max(l.bottom for l in names))
        rank = _filled_diamonds(gray, cx, bottom, scale)
        out.append((" ".join(l.text for l in names), rank, _badge_count(img, names, scale)))
    return out


def _filled_diamonds(gray: np.ndarray, cx: float, bottom: int, scale: float) -> int:
    """Count filled rank diamonds under a tile name. The diamond row's centre
    is found from the diamonds' left/right points (bright on filled and
    empty ones alike), searched 1-10px below the text; then only the centre
    pixel is tested, since empty diamonds' top/bottom points are bright
    enough to fool a looser test."""
    xs = [int(round(cx + k * PIP_PITCH * scale)) for k in (-2, -1, 0, 1, 2)]
    d = [int(round(5 * scale)), int(round(6 * scale))]
    rows = range(bottom + max(1, int(scale)), min(gray.shape[0] - 2, bottom + int(11 * scale)))
    if not rows or xs[0] - d[1] < 0 or xs[-1] + d[1] >= gray.shape[1]:
        return 0
    y = max(rows, key=lambda r: sum(int(gray[r, x - o]) + int(gray[r, x + o]) for x in xs for o in d))
    return sum(gray[y - 1:y + 2, x - 1:x + 2].mean() > PIP_FILLED for x in xs)


def read_arcane_manager(img: Image.Image, header: Line) -> list[tuple[str, int, int]]:
    """Arcanes manager: [(name, rank, count)] in reading order, count 0 for
    uncollected (dimmed) arcanes. Same tile size and rank diamonds as the
    inventory; the "xN" count sits ~80px above the name, left of centre."""
    w, h = img.size
    scale = h / 1440
    gray = np.asarray(img.convert("L"), dtype=np.int16)
    box = (0, int(h * 0.085), int(w * 0.79), int(h * 0.93))  # grid, left of the detail panel
    count_lines: list[Line] = []
    tiles = _tile_lines(img, header, box=box, extras=count_lines)
    out = []
    for tile in tiles:
        names = [l for l in tile if not PIP_TEXT_RE.match(l.text.replace(" ", ""))]
        if not names or any(_is_tooltip(l) for l in names):
            continue
        cx = sum(l.cx for l in names) / len(names)
        top = int(min(l.top for l in names))
        bottom = int(max(l.bottom for l in names))
        icon = gray[max(0, top - int(110 * scale)):top - int(25 * scale),
                    int(cx - 40 * scale):int(cx + 40 * scale)]
        if icon.size and np.percentile(icon, 95) < UNCOLLECTED_ICON_P95:
            out.append((" ".join(l.text for l in names), 0, 0))
            continue
        rank = _filled_diamonds(gray, cx, bottom, scale)
        count = 1
        for c in count_lines:
            if 40 * scale < top - c.bottom < 110 * scale and 10 * scale < cx - c.cx < 90 * scale:
                count = int(MANAGER_COUNT_RE.match(c.text).group(1))
                break
        out.append((" ".join(l.text for l in names), rank, count))
    return out


def read_equipment(img: Image.Image, header: Line) -> list[tuple[str, int | None]]:
    """Equipment screens: [(item name, current rank or None)]. The rank is
    the copy's current level (it resets with Forma), not mastery earned."""
    tiles = []
    for tile in _tile_lines(img, header):
        text = " ".join(l.text for l in tile)
        m = RANK_RE.match(text)
        tiles.append((m.group(1), int(m.group(2))) if m else (text, None))
    return tiles


def _badge_count(img: Image.Image, tile: list[Line], scale: float) -> int:
    """Read the tile's top-left count badge. The grid-wide OCR pass misses
    most of these tiny glyphs, so each tile's badge corner is cropped,
    upscaled and run through recognition alone. The corner is located from
    the bottom of the tile's name text, which sits at the tile's bottom."""
    cx = sum(l.cx for l in tile) / len(tile)
    bottom = max(l.bottom for l in tile)
    # Badge sits 107-121px above the name's bottom on Prime Parts and
    # 88-112px on Arcanes; one crop covers both.
    box = (int(cx - 68 * scale), int(bottom - 134 * scale),
           int(cx - 12 * scale), int(bottom - 84 * scale))
    if box[1] < 0:
        return 1
    corner = img.crop(box)
    corner = corner.resize((corner.width * 3, corner.height * 3), Image.LANCZOS)
    res = _get_engine()(np.array(corner), use_det=False, use_cls=False)
    # Real badges recognize at 0.66+; icon art misread as digits stays
    # under ~0.4. A missed badge reads as 1, which errs toward "keep
    # farming" rather than claiming a copy you don't have.
    if not res.txts or res.scores[0] < BADGE_MIN_SCORE:
        return 1
    digits = re.sub(r"\D", "", res.txts[0])
    return int(digits) if digits and 1 < int(digits) < 100 else 1


if __name__ == "__main__":
    import sys
    img = Image.open(sys.argv[1]).convert("RGB")
    if len(sys.argv) > 2:  # optional "left,top,width,height" crop for test images
        l, t, w, h = map(int, sys.argv[2].split(","))
        img = img.crop((l, t, l + w, t + h))
    found = find_screen(img)
    print("screen:", found and found[0])
    if found and found[0] in PARTS_SCREENS:
        for name, count in read_tiles(img, found[1]):
            print(f"  {count}x {name}")
    elif found and found[0] in (ARCANES, ARCANE_MANAGER):
        read = read_arcanes if found[0] == ARCANES else read_arcane_manager
        for name, rank, count in read(img, found[1]):
            print(f"  {count}x R{rank} {name}")
    elif found:
        for name, rank in read_equipment(img, found[1]):
            print(f"  [{rank}] {name}")
