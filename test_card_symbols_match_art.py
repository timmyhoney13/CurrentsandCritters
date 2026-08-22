"""Every card's coded symbol must be the symbol PRINTED on its art.

A card's symbol is how you pay for it: you discard a card whose symbol matches
the one you are playing. The player reads that symbol off the badge in the
corner of the card art, the engine reads it out of cards_vertical.txt /
cards_lr.txt / cards_oceans.txt. Nothing ties the two together, so a card art
rebuild can silently leave the text file describing the OLD symbol, and then
the game refuses a discard that visibly matches (or accepts one that visibly
does not). That is exactly what happened to Arctic Ocean 252, which prints a
Triangle and was coded Circle, along with eight of its neighbours.

This suite closes the loop by reading the symbol back OUT of the art:

  A. The five reference badges (Pier 201-205, one per symbol) are mutually
     distinct, so the classifier below can actually tell them apart.
  B. Every playable face on every sheet is classified from its own badge, and
     the answer must equal the symbol in the card database.
  C. Cards whose art file is byte-identical must carry the same symbol. This is
     the cheap invariant that surfaced the Arctic bug: the sheet renderer emits
     one image per (card, symbol) pair, so two cards sharing a file that are
     coded differently means one of them is wrong.
  D. The eight Arctic Ocean symbols are pinned by uid, so the regression this
     suite was written for cannot come back unnoticed.
  E. snap-card-library.json, the symbol table shipped to the Snap & Score
     scanner, agrees with the card database (rebuild it when cards change:
     python3 build_snap_card_library.py).

How the classifier works: crop the badge, keep the pale-yellow mascot body,
take its largest connected blob (so sand or a yellow sun in the background
cannot join in), normalize that blob to its bounding box, and score it against
the five references by intersection-over-union. The mascot is the same drawing
on every card, so a correct match scores above 0.9 and the runner-up sits far
below.
"""
import collections
import hashlib
import json
import os

from PIL import Image

import fish_game_all_in_one as fish

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Badge crop per sheet kind and face, as fractions of the full 720x1008 page.
# Deliberately a little loose: the largest-blob step below trims the slack, and
# a tight box would clip a badge that the renderer nudged by a pixel.
BADGE_REGIONS = {
    "h": {"up":    (0.780, 0.010, 0.980, 0.155),
          "down":  (0.780, 0.510, 0.980, 0.655)},
    "v": {"left":  (0.315, 0.010, 0.515, 0.155),
          "right": (0.815, 0.010, 1.000, 0.155)},
    "o": {"ocean": (0.780, 0.010, 0.980, 0.155)},
}

# uid -> (kind, sheet page path, face). Mirrors cardImageUrl() in preview-app.js
# and page_faces() in build_snap_card_library.py: odd uids take the first face.
def art_face(uid):
    if 1 <= uid <= 96:
        return "h", f"horizontal_cards/page_{(uid + 1) // 2:02d}.png", ("up" if uid % 2 else "down")
    if 101 <= uid <= 188:
        return "v", f"vertical_cards/page_{(uid - 101) // 2 + 1:02d}.png", ("left" if uid % 2 else "right")
    if 201 <= uid <= 269:
        return "o", f"oceans_cards/page_{uid - 200:02d}.png", "ocean"
    return None


GLYPH_N = 96          # side of the normalized glyph mask
MIN_BLOB = 60         # a badge mascot is far bigger than this; less means a bad crop
MIN_MATCH = 0.85      # a correct match scores ~0.93-1.00
MIN_MARGIN = 0.10     # winner must clear the runner-up by this much


def _largest_blob(mask, w, h):
    """4-connected largest component of a flat bool list, as a new flat list."""
    seen = bytearray(w * h)
    best = []
    for start in range(w * h):
        if not mask[start] or seen[start]:
            continue
        seen[start] = 1
        stack = [start]
        blob = []
        while stack:
            i = stack.pop()
            blob.append(i)
            y, x = divmod(i, w)
            if x > 0 and mask[i - 1] and not seen[i - 1]:
                seen[i - 1] = 1
                stack.append(i - 1)
            if x < w - 1 and mask[i + 1] and not seen[i + 1]:
                seen[i + 1] = 1
                stack.append(i + 1)
            if y > 0 and mask[i - w] and not seen[i - w]:
                seen[i - w] = 1
                stack.append(i - w)
            if y < h - 1 and mask[i + w] and not seen[i + w]:
                seen[i + w] = 1
                stack.append(i + w)
        if len(blob) > len(best):
            best = blob
    out = bytearray(w * h)
    for i in best:
        out[i] = 1
    return out, len(best)


_glyph_cache = {}


def glyph_mask(uid):
    """Normalized badge silhouette for one card face, as a flat bool bytearray."""
    if uid in _glyph_cache:
        return _glyph_cache[uid]
    kind, rel, face = art_face(uid)
    path = os.path.join(BASE_DIR, rel)
    with Image.open(path) as sheet:
        img = sheet.convert("RGB")
    pw, ph = img.size
    x0, y0, x1, y1 = BADGE_REGIONS[kind][face]
    crop = img.crop((int(x0 * pw), int(y0 * ph), int(x1 * pw), int(y1 * ph)))
    w, h = crop.size
    raw = crop.tobytes()
    # The mascot body is the one pale-yellow mass in the badge; the water,
    # the kelp and the badge frame are all blue, green or near black.
    mask = bytearray(
        1 if (raw[i] > 195 and raw[i + 1] > 175 and raw[i + 2] < 180
              and raw[i] > raw[i + 2] + 45) else 0
        for i in range(0, len(raw), 3)
    )
    blob, size = _largest_blob(mask, w, h)
    assert size >= MIN_BLOB, f"uid {uid}: found no symbol badge in {rel} ({face})"
    xs = [i % w for i in range(w * h) if blob[i]]
    ys = [i // w for i in range(w * h) if blob[i]]
    box = Image.frombytes("L", (w, h), bytes(255 if v else 0 for v in blob))
    box = box.crop((min(xs), min(ys), max(xs) + 1, max(ys) + 1))
    box = box.resize((GLYPH_N, GLYPH_N), Image.BILINEAR)
    out = bytearray(1 if v > 127 else 0 for v in box.tobytes())
    _glyph_cache[uid] = out
    return out


def iou(a, b):
    inter = union = 0
    for i in range(GLYPH_N * GLYPH_N):
        if a[i] or b[i]:
            union += 1
            if a[i] and b[i]:
                inter += 1
    return inter / union if union else 0.0


# One Pier of each symbol, the deck's plainest ocean art.
REFERENCE_UIDS = {"Diamond": 201, "Square": 202, "Triangle": 203, "Heart": 204, "Circle": 205}


def classify(uid, refs):
    scored = sorted(((iou(glyph_mask(uid), m), sym) for sym, m in refs.items()), reverse=True)
    return scored[0][1], scored[0][0], scored[1][0]


def coded_symbols():
    db = fish.load_card_db()
    out = {}
    for uid, card in db.items():
        sym = (card.symbol or "").strip()
        if not sym or sym == "N/A" or art_face(uid) is None:
            continue
        out[uid] = (card.name, sym)
    return out


def test_A_reference_badges_are_distinct():
    refs = {sym: glyph_mask(uid) for sym, uid in REFERENCE_UIDS.items()}
    syms = sorted(refs)
    worst = 1.0
    for i, a in enumerate(syms):
        for b in syms[i + 1:]:
            score = iou(refs[a], refs[b])
            assert score < 0.85, f"{a} and {b} badges are not distinguishable ({score:.3f})"
            worst = min(worst, 1 - score)
    print(f"A PASS: 5 reference badges, closest pair still {worst:.2f} apart")


def test_B_every_card_symbol_matches_its_art():
    refs = {sym: glyph_mask(uid) for sym, uid in REFERENCE_UIDS.items()}
    coded = coded_symbols()
    assert len(coded) == 252, f"expected 252 playable faces, found {len(coded)}"
    wrong = []
    weakest = (1.0, None)
    for uid in sorted(coded):
        name, sym = coded[uid]
        seen, top, runner = classify(uid, refs)
        if seen != sym:
            wrong.append(f"uid {uid} {name}: art shows {seen}, coded {sym}")
            continue
        assert top >= MIN_MATCH, f"uid {uid} {name}: {sym} badge only scored {top:.3f}"
        if top - runner < weakest[0]:
            weakest = (top - runner, uid)
        assert top - runner >= MIN_MARGIN, (
            f"uid {uid} {name}: {sym} beat the runner-up by only {top - runner:.3f}"
        )
    assert not wrong, "card data disagrees with the printed art:\n  " + "\n  ".join(wrong)
    print(f"B PASS: all 252 faces match their printed badge "
          f"(narrowest call uid {weakest[1]}, margin {weakest[0]:.2f})")


def test_C_identical_art_carries_identical_symbol():
    groups = collections.defaultdict(list)
    for uid, (_name, sym) in coded_symbols().items():
        _kind, rel, face = art_face(uid)
        with open(os.path.join(BASE_DIR, rel), "rb") as f:
            digest = hashlib.md5(f.read()).hexdigest()
        groups[(digest, face)].append((uid, sym))
    clashes = []
    shared = 0
    for members in groups.values():
        if len(members) < 2:
            continue
        shared += 1
        syms = {sym for _uid, sym in members}
        if len(syms) > 1:
            clashes.append(f"{[u for u, _ in members]} share one image but are coded {sorted(syms)}")
    assert not clashes, "same art, different symbol:\n  " + "\n  ".join(clashes)
    print(f"C PASS: {shared} groups of cards share art, every group agrees on its symbol")


def test_D_arctic_ocean_symbols_are_pinned():
    # Read off the printed badges of oceans_cards/page_45..52. uid 252 is the
    # one that shipped as Circle while showing a Triangle.
    expected = {
        245: "Heart", 246: "Triangle", 247: "Diamond", 248: "Circle",
        249: "Triangle", 250: "Square", 251: "Circle", 252: "Triangle",
    }
    db = fish.load_card_db()
    for uid, sym in expected.items():
        assert db[uid].name == "Arctic Ocean", f"uid {uid} is no longer an Arctic Ocean"
        assert db[uid].symbol == sym, f"Arctic Ocean {uid}: expected {sym}, got {db[uid].symbol}"
    print("D PASS: all 8 Arctic Ocean symbols pinned to their art")


def test_E_snap_library_symbols_match_the_card_database():
    path = os.path.join(BASE_DIR, "multiplayer", "client", "snap-card-library.json")
    with open(path, "r", encoding="utf-8") as f:
        library = json.load(f)
    coded = coded_symbols()
    drift = []
    checked = 0
    for card in library.get("cards", []):
        for face in (card.get("halves") or {}).values():
            uid = face.get("u")
            if uid not in coded:
                continue
            checked += 1
            if face.get("sym") != coded[uid][1]:
                drift.append(f"uid {uid} {coded[uid][0]}: library {face.get('sym')}, data {coded[uid][1]}")
    assert checked == 252, f"snap library covers {checked} faces, expected 252"
    assert not drift, ("snap-card-library.json is stale, rerun "
                       "`python3 build_snap_card_library.py`:\n  " + "\n  ".join(drift))
    print(f"E PASS: snap scanner library agrees with the card data on all {checked} faces")


if __name__ == "__main__":
    test_A_reference_badges_are_distinct()
    test_B_every_card_symbol_matches_its_art()
    test_C_identical_art_carries_identical_symbol()
    test_D_arctic_ocean_symbols_are_pinned()
    test_E_snap_library_symbols_match_the_card_database()
    print("\nALL CARD SYMBOL / ART TESTS PASSED ✓")
