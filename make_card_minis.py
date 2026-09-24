#!/usr/bin/env python3
"""Build the small card art the table actually draws.

Every card on the table, in a hand, in the pool or on an opponent's board is
drawn from a full page scan: 720 x 1008 pixels holding two cards. A board card
is 84 x 59 CSS pixels. Measured on a real late-game table (4 players, the
median end-state of 6 oceans / 15 animals each), that is 84 <img> elements over
65 distinct page scans, and the browser holds every one of them decoded:

    180 MB of bitmap, to paint about 1 MB of pixels.

Phones evict and re-decode under that, which is what the lag was. This writes a
half-size twin of every page next to it:

    horizontal_cards/page_01.png  ->  horizontal_cards/page_01.mini.jpg
                                      horizontal_cards/page_01.mini.webp

360 x 504 is the smallest size that is still sharp where people actually look:
the widest a mini is ever drawn is the hand at 98 CSS px, and 98 x 3 (a phone's
device pixel ratio) is 294. The three places a card is shown BIG, the zoom
modal, the tutorial zoom and the end-game cinematic, keep the full page scan.

The .jpg is the fallback; multiplayer_server.py serves the .webp to any browser
that advertises it, exactly as it already does for the full-size art.

Run after changing any card art:  python3 make_card_minis.py
"""
import os
import sys

from PIL import Image

MINI_W, MINI_H = 360, 504
DIRS = ("horizontal_cards", "vertical_cards", "oceans_cards")
BASE = os.path.dirname(os.path.abspath(__file__))


def build(force: bool = False) -> int:
    made = 0
    for d in DIRS:
        src_dir = os.path.join(BASE, d)
        if not os.path.isdir(src_dir):
            print(f"  skip {d}: not here")
            continue
        for name in sorted(os.listdir(src_dir)):
            if not name.endswith(".png") or ".mini." in name:
                continue
            src = os.path.join(src_dir, name)
            stem = os.path.splitext(src)[0]
            jpg, webp = stem + ".mini.jpg", stem + ".mini.webp"
            if not force and os.path.exists(jpg) and os.path.exists(webp) \
               and os.path.getmtime(jpg) >= os.path.getmtime(src) \
               and os.path.getmtime(webp) >= os.path.getmtime(src):
                continue
            with Image.open(src) as im:
                im = im.convert("RGB").resize((MINI_W, MINI_H), Image.LANCZOS)
                im.save(jpg, "JPEG", quality=86, optimize=True, progressive=True)
                im.save(webp, "WEBP", quality=84, method=6)
            made += 1
    return made


if __name__ == "__main__":
    n = build(force="--force" in sys.argv)
    print(f"built {n} mini pages at {MINI_W}x{MINI_H}")
    for d in DIRS:
        p = os.path.join(BASE, d)
        if not os.path.isdir(p):
            continue
        full = sum(os.path.getsize(os.path.join(p, f)) for f in os.listdir(p) if f.endswith(".png") and ".mini." not in f)
        mini = sum(os.path.getsize(os.path.join(p, f)) for f in os.listdir(p) if f.endswith(".mini.webp"))
        print(f"  {d:18s} full {full/1e6:6.1f} MB   mini(webp) {mini/1e6:5.1f} MB")
