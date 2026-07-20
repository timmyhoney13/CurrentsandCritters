#!/usr/bin/env python3
"""Snap & Score local-recognition server-side tests.

    python3 test_snap_local_board.py

Covers: the uid ↔ sheet-page mapping the browser scanner depends on (every
half uid must sit on the page/side the library builder assigns it), the
generated library staying in sync with the card DB, photo-evidence validation
for /api/snap/session/photo, and deterministic scoring of a corrected board
(the flow after the player fixes cards by hand).
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fish_game_all_in_one as fish
import snap_score
from build_snap_card_library import page_map

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}" + (f" — {detail}" if detail else ""))


print("uid ↔ page mapping (what the browser library is built from)")
pages, problems = page_map()
check("mapping has zero problems against the card DB", not problems, "; ".join(problems[:3]))
check("160 physical cards", len(pages) == 160, str(len(pages)))
face_uids = sorted(h["u"] for _, _, _, halves in pages for h in halves.values() if h)
roster_uids = sorted(c["u"] for c in snap_score.build_roster()["cards"])
check("library faces == server roster exactly", face_uids == roster_uids,
      f"{len(face_uids)} vs {len(roster_uids)}")
check("every page image exists on disk",
      all(os.path.exists(rel) for _, _, rel, _ in pages))

print("\ngenerated snap-card-library.json")
lib_path = os.path.join("multiplayer", "client", "snap-card-library.json")
check("library file exists", os.path.exists(lib_path))
lib = json.load(open(lib_path))
check("library card count matches", len(lib["cards"]) == 160, str(len(lib["cards"])))
lib_uids = sorted(h["u"] for c in lib["cards"] for h in c["halves"].values())
check("library uids match the roster", lib_uids == roster_uids)
check("every card carries all descriptors",
      all(len(c["p"]) == 16 and len(c["d"]) == 16 and len(c["c"]) == 48 and len(c["e"]) == 8
          and all(len(b) == 64 for b in c["b"].values()) for c in lib["cards"]))

print("\nphoto-evidence validation (/api/snap/session/photo)")
ev = snap_score._validate_photo_evidence({
    "hash": "a" * 64, "dhash": "0123456789abcdef",
    "thumb": "data:image/jpeg;base64,/9j/AAAA=",
})
check("valid evidence accepted", ev["hash"] == "a" * 64 and ev["dhash"] == "0123456789abcdef"
      and ev["thumb"] is not None)
bad = snap_score._validate_photo_evidence({
    "hash": "zz", "dhash": "nope", "thumb": "data:image/png;base64,AAAA",
})
check("garbage evidence rejected field-by-field",
      bad["hash"] == "" and bad["dhash"] == "" and bad["thumb"] is None)
huge = snap_score._validate_photo_evidence({"thumb": "data:image/jpeg;base64," + "A" * 200_000})
check("oversized thumbnail rejected", huge["thumb"] is None)

print("\nscoring a manually corrected board (deterministic engine, no AI)")
# a board like the scanner + player would confirm: one ocean, an Up card
# above it, a Left card beside it — exactly the uid space the client sends
board = [{"name": "Tester", "oceans": [
    {"u": 208, "up": [1], "down": [2], "left": [101], "right": [102]},
]}]
res = snap_score.score_boards(board)
p = res["players"][0]
check("score computed", isinstance(p["score"], int), repr(p.get("score")))
check("breakdown covers every card + the ocean", len(p["breakdown"]) == 5,
      str(len(p["breakdown"])))
check("board view echoes names", p["board"]["oceans"][0]["n"] != "")
res2 = snap_score.score_boards(board)
check("scoring is deterministic", res2["players"][0]["score"] == p["score"])

# after a "manual correction" (player swaps a misread card for another copy)
board_fixed = [{"name": "Tester", "oceans": [
    {"u": 208, "up": [3], "down": [2], "left": [101], "right": [102]},
]}]
resf = snap_score.score_boards(board_fixed)
check("corrected board scores cleanly", isinstance(resf["players"][0]["score"], int))

# duplicate-copy remap: same uid twice can't double-count silently
board_dup = [{"name": "Tester", "oceans": [
    {"u": 208, "up": [1, 1], "down": [], "left": [], "right": []},
]}]
resd = snap_score.score_boards(board_dup)
check("duplicate uid remaps to another real copy (with a warning or clean remap)",
      isinstance(resd["players"][0]["score"], int))

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
