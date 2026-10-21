"""Generate the Stream Deck icons: idle (gold), reading (grey), found (cyan)."""
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path.home() / "Pictures" / "StreamDeck"
SIZE = 288
STYLES = {
    "warframe-reader-idle": ((214, 178, 92), "SCAN"),
    "warframe-reader-busy": ((90, 90, 96), "..."),
    "warframe-reader-found": ((80, 220, 230), "FOUND"),
}


def font(size):
    for name in ("DejaVuSans-Bold.ttf", "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default(size)


def draw(color, label):
    img = Image.new("RGB", (SIZE, SIZE), (12, 12, 16))
    d = ImageDraw.Draw(img)
    c = SIZE // 2
    eye_y = 118
    # Stylized eye (the "reader"): two arcs around an iris, Lotus-gold when on.
    d.chord((40, eye_y - 62, SIZE - 40, eye_y + 62), 200, 340, outline=color, width=10)
    d.chord((40, eye_y - 62, SIZE - 40, eye_y + 62), 20, 160, outline=color, width=10)
    d.ellipse((c - 34, eye_y - 34, c + 34, eye_y + 34), outline=color, width=10)
    d.ellipse((c - 12, eye_y - 12, c + 12, eye_y + 12), fill=color)
    for i in range(4):  # little scan ticks
        a = math.radians(225 + i * 30)
        d.line((c + 92 * math.cos(a), eye_y + 92 * math.sin(a),
                c + 108 * math.cos(a), eye_y + 108 * math.sin(a)), fill=color, width=6)
    f = font(46)
    d.text((c, 222), "WF", font=font(40), fill=color, anchor="mm")
    d.text((c, 264), label, font=f, fill=color, anchor="mm")
    return img


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for name, (color, label) in STYLES.items():
        draw(color, label).save(OUT / f"{name}.png")
        print(OUT / f"{name}.png")
