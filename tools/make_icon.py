"""Draw src/mr/ui/icon.ico from the same shapes as icon.svg (PIL cannot read SVG).

  python tools/make_icon.py
"""
from pathlib import Path
from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parents[1] / 'src' / 'mr' / 'ui' / 'icon.ico'
SIZE = 256
S = SIZE * 4 / 64          # draw 4x larger than the icon, in icon.svg's 64-unit grid, then shrink for smooth edges

def box(x, y, w, h):
    return [x * S, y * S, (x + w) * S, (y + h) * S]

def draw():
    img = Image.new('RGBA', (SIZE * 4, SIZE * 4), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(box(4, 6, 56, 52), radius=12 * S, fill='#2563eb')
    d.rounded_rectangle(box(4, 6, 56, 14), radius=7 * S, fill='#1d4ed8')
    d.rectangle(box(4, 14, 56, 6), fill='#1d4ed8')     # the header's lower corners are square under the body
    for cx in (18, 46):
        d.ellipse(box(cx - 3, 10, 6, 6), fill='#ffffff')
    bars = Image.new('RGBA', img.size, (0, 0, 0, 0))
    b = ImageDraw.Draw(bars)
    for x, y, h, alpha in ((14, 28, 20, 140), (28, 34, 14, 204), (42, 25, 23, 255)):
        b.rounded_rectangle(box(x, y, 8, h), radius=2 * S, fill=(255, 255, 255, alpha))
    img.alpha_composite(bars)
    return img.resize((SIZE, SIZE), Image.LANCZOS)

if __name__ == '__main__':
    draw().save(OUT, sizes=[(16, 16), (20, 20), (24, 24), (32, 32), (40, 40), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(OUT)
