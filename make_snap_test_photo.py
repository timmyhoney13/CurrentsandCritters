#!/usr/bin/env python3
"""Generate realistic Snap & Score test photos from the REAL card sheets.

Composites actual page PNGs (real art, real text, real borders — everything a
phone camera sees) onto synthetic tabletops with rotation, soft blur, a warm
color cast, and a JPEG round-trip, then dumps raw RGBA + a truth manifest for
test_snap_vision.js. This is the closest thing to a phone photo the automated
suite can produce, and it exists because purely synthetic art hid a real-world
detection failure (real card interiors are full of edges).

    python3 make_snap_test_photo.py        # writes test_snap_real_photo_*.bin/json

Outputs are large and machine-generated — they stay untracked (the default
`*` gitignore already excludes them); tests skip gracefully when absent.
"""

from __future__ import annotations

import io
import json
import math
import os
import random

from PIL import Image, ImageDraw, ImageFilter

BASE = os.path.dirname(os.path.abspath(__file__))


def load_card(rel, height):
    img = Image.open(os.path.join(BASE, rel)).convert("RGBA")
    w = round(height * img.width / img.height)
    return img.resize((w, height), Image.LANCZOS)


def table(w, h, base, noise, seed):
    rnd = random.Random(seed)
    img = Image.new("RGB", (w, h), base)
    d = ImageDraw.Draw(img)
    # wood-ish streaks + speckle so the surface is textured like a real table
    for _ in range(140):
        x0 = rnd.randint(-50, w)
        y0 = rnd.randint(0, h)
        ln = rnd.randint(60, 400)
        shade = rnd.randint(-noise, noise)
        col = tuple(max(0, min(255, c + shade)) for c in base)
        d.line([(x0, y0), (x0 + ln, y0 + rnd.randint(-6, 6))], fill=col, width=rnd.randint(1, 4))
    px = img.load()
    for _ in range(w * h // 18):
        x, y = rnd.randint(0, w - 1), rnd.randint(0, h - 1)
        r, g, b = px[x, y]
        s = rnd.randint(-noise, noise)
        px[x, y] = (max(0, min(255, r + s)), max(0, min(255, g + s)), max(0, min(255, b + s)))
    return img


def place(photo, card, cx, cy, rot_deg):
    rot = card.rotate(rot_deg, expand=True, resample=Image.BICUBIC)
    photo.paste(rot, (round(cx - rot.width / 2), round(cy - rot.height / 2)), rot)


def finish(img, warm, blur, jpeg_q):
    if warm:
        r, g, b = img.split()
        r = r.point(lambda v: min(255, int(v * 1.07)))
        b = b.point(lambda v: int(v * 0.90))
        img = Image.merge("RGB", (r, g, b))
    img = img.filter(ImageFilter.GaussianBlur(blur))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=jpeg_q)
    return Image.open(io.BytesIO(buf.getvalue())).convert("RGBA")


def dump(img, cards, name):
    raw = img.tobytes()
    open(os.path.join(BASE, f"{name}.bin"), "wb").write(raw)
    json.dump({"width": img.width, "height": img.height, "cards": cards},
              open(os.path.join(BASE, f"{name}.json"), "w"))
    print(f"wrote {name}.bin ({len(raw)//1024}KB) + manifest ({len(cards)} cards)")


def scene_light():
    """Bright wood table: ocean center, h-card above (landscape), v-card right,
    h-card below, v-card left — every attachment side exercised."""
    w, h = 1600, 1200
    img = table(w, h, (196, 172, 140), 9, seed=11)
    H = 300  # card height on the table
    cards = []

    def put(page_rel, cid, cx, cy, rot, side):
        place(img, load_card(page_rel, H), cx, cy, rot)
        cards.append({"id": cid, "cx": cx, "cy": cy, "side": side})

    put("oceans_cards/page_08.png", "o08", 800, 620, 2, "ocean")
    put("horizontal_cards/page_01.png", "h01", 800, 300, 88, "up")      # landscape above
    put("horizontal_cards/page_05.png", "h05", 800, 952, -91, "down")   # landscape below
    put("vertical_cards/page_01.png", "v01", 1140, 620, -3, "right")
    put("vertical_cards/page_07.png", "v07", 462, 622, 4, "left")
    return finish(img, warm=True, blur=0.6, jpeg_q=82), cards


def scene_dark():
    """Darker tabletop + slightly farther camera + a non-card distractor."""
    w, h = 1500, 1100
    img = table(w, h, (88, 84, 78), 7, seed=23)
    H = 250
    cards = []

    def put(page_rel, cid, cx, cy, rot, side):
        place(img, load_card(page_rel, H), cx, cy, rot)
        cards.append({"id": cid, "cx": cx, "cy": cy, "side": side})

    put("oceans_cards/page_20.png", "o20", 700, 560, -2, "ocean")
    put("horizontal_cards/page_11.png", "h11", 700, 290, 90, "up")
    put("vertical_cards/page_16.png", "v16", 986, 560, 2, "right")
    # distractor: a bright coaster-ish disc that must not become a card
    d = ImageDraw.Draw(img)
    d.ellipse([1180, 820, 1360, 1000], fill=(226, 224, 214))
    return finish(img, warm=False, blur=0.7, jpeg_q=78), cards


def main():
    img, cards = scene_light()
    dump(img, cards, "test_snap_real_photo_light")
    img, cards = scene_dark()
    dump(img, cards, "test_snap_real_photo_dark")


if __name__ == "__main__":
    main()
