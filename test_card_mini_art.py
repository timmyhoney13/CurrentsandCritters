#!/usr/bin/env python3
"""The table must draw from the small card art, not the full page scans.

A page scan is 720x1008 and holds two cards. A board card is drawn at 84x59, a
hand card at 98x138. Serving the full scan to those meant a real late-game
table (4 players, 6 oceans and 15 animals each) held 84 <img> elements over 65
distinct scans: 180 MB of decoded bitmap to paint about 1 MB of pixels, which
is what made the game stutter. The half-size twins bring that to 45 MB.

Nothing about that is visible in a screenshot, so nothing would notice it
breaking. These are the ways it can break:

  A. new card art added, make_card_minis.py never re-run, so the mini is
     missing and the server quietly falls back to the full scan
  B. a mini regenerated at a different aspect ratio, which silently moves every
     object-position crop and shows the wrong half of the page
  C. imagePathForUid losing its mini default, or a big-card view losing its
     explicit "full"
  D. the server route no longer recognising .mini.jpg

Run:  python3 test_card_mini_art.py
"""
import os
import re
import sys

from PIL import Image

BASE = os.path.dirname(os.path.abspath(__file__))
CLIENT = os.path.join(BASE, "multiplayer/client")
DIRS = ("horizontal_cards", "vertical_cards", "oceans_cards")
FULL_W, FULL_H = 720, 1008
MINI_W, MINI_H = 360, 504

# The widest a mini is ever drawn, in CSS px: the hand card. On a 3x phone that
# is 294 device px, so a 360-wide mini is still sharper than the screen.
WIDEST_MINI_CSS = 98
MAX_DPR = 3

fails = []
checks = 0


def ok(cond, msg):
    global checks
    checks += 1
    if cond:
        print(f"  ✓ {msg}")
    else:
        print(f"  ✗ {msg}")
        fails.append(msg)


print("A. every page scan has a mini twin")
for d in DIRS:
    p = os.path.join(BASE, d)
    pages = sorted(f for f in os.listdir(p) if f.endswith(".png") and ".mini." not in f)
    missing = [f for f in pages
               if not os.path.exists(os.path.join(p, f[:-4] + ".mini.jpg"))
               or not os.path.exists(os.path.join(p, f[:-4] + ".mini.webp"))]
    ok(not missing, f"{d}: all {len(pages)} pages have .mini.jpg + .mini.webp"
       + (f" (missing: {missing[:4]})" if missing else ""))

print("\nB. the minis crop exactly like the full scans")
for d in DIRS:
    p = os.path.join(BASE, d)
    bad = []
    for f in sorted(f for f in os.listdir(p) if f.endswith(".mini.jpg")):
        with Image.open(os.path.join(p, f)) as im:
            if im.size != (MINI_W, MINI_H):
                bad.append((f, im.size))
    ok(not bad, f"{d}: every mini is exactly {MINI_W}x{MINI_H}" + (f" (bad: {bad[:3]})" if bad else ""))
ok(FULL_W / FULL_H == MINI_W / MINI_H,
   f"mini aspect ratio matches the full page, so object-position crops the same half")
ok(MINI_W >= WIDEST_MINI_CSS * MAX_DPR,
   f"a {MINI_W}px mini still covers the widest mini view ({WIDEST_MINI_CSS} CSS px at {MAX_DPR}x = {WIDEST_MINI_CSS*MAX_DPR}px)")

print("\nC. the client asks for the mini by default")
app = open(os.path.join(CLIENT, "js/preview-app.js"), encoding="utf-8").read()
m = re.search(r"function imagePathForUid\(uid, size\) \{(.*?)\n  \}", app, re.S)
ok(m is not None, "imagePathForUid takes a size argument")
if m:
    body = m.group(1)
    ok('(size === "full") ? ".png" : ".mini.jpg"' in body,
       "it returns .mini.jpg unless the caller asks for \"full\"")
    ok(body.count("${ext}") == 3,
       "all three art folders go through that choice")

calls = re.findall(r"imagePathForUid\(([^)]*)\)", app)
full_calls = [c for c in calls if '"full"' in c]
ok(len(full_calls) == 3,
   f"exactly three views still ask for the full page scan (found {len(full_calls)})")
for big in ("pv-zoom-img", "tut-zoom-img"):
    ok(re.search(re.escape(big) + r'"\)\.src\s*=\s*imagePathForUid\(uid, "full"\)', app) is not None,
       f"{big} (a card shown big) keeps the full scan")
ok('img.src = imagePathForUid(cardUid, "full");' in app,
   "the end-game cinematic (a card shown big) keeps the full scan")

print("\nD. the server serves the mini, and falls back when one is missing")
srv = open(os.path.join(BASE, "multiplayer_server.py"), encoding="utf-8").read()
rx = re.search(r'_card_art = re\.fullmatch\(\s*r"([^"]+)"', srv)
ok(rx is not None, "the card-art route is still a single regex")
if rx:
    pat = re.compile(rx.group(1))
    ok(pat.fullmatch("/horizontal_cards/page_01.mini.jpg") is not None, "it matches a .mini.jpg request")
    ok(pat.fullmatch("/oceans_cards/page_69.png") is not None, "it still matches a full .png request")
    ok(pat.fullmatch("/horizontal_cards/../secret.png") is None, "it does not match a path escaping the folder")
ok('card_path = os.path.join(BASE_DIR, card_dir, card_page + ".png")' in srv,
   "a missing mini falls back to the full page scan rather than 404ing")
ok(re.search(r'card_ext == "\.mini\.jpg" and not os\.path\.exists', srv) is not None,
   "the fallback is guarded on the mini actually being absent")
ok("allow_webp=True," in srv, "the route still hands WebP to browsers that take it")

print("\n" + "-" * 58)
if fails:
    print(f"{checks - len(fails)} passed, {len(fails)} FAILED")
    for f in fails:
        print(f"   FAILED: {f}")
    sys.exit(1)
print(f"{checks} passed, 0 failed")
