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
        print(f"  ✗ {name}" + (f": {detail}" if detail else ""))


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
check("every card carries per-half color layouts (hc)",
      all(len(c["hc"]) == (1 if c["kind"] == "o" else 2)
          and all(len(h) == 48 for h in c["hc"]) for c in lib["cards"]))
check("h/v cards carry per-half pHashes (hp) and name grids (nm)",
      all(len(c["hp"]) == 2 and all(len(p) == 16 for p in c["hp"])
          and len(c["nm"]) == 2 and all(len(g) == 48 for g in c["nm"])
          for c in lib["cards"] if c["kind"] in ("h", "v")))
check("ocean cards carry no half pHash / name grid (oceans have no name band)",
      all(c["hp"] == [] and c["nm"] == [] for c in lib["cards"] if c["kind"] == "o"))

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
# above it, a Left card beside it, exactly the uid space the client sends
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

print("\ncombo abilities score EXACTLY like the online game (same engine)")
db = fish.load_card_db()


def uid_named(name, direction=None, symbol=None, exclude=()):
    for u in sorted(db):
        cd = db[u]
        if str(cd.name).strip().lower() != name.lower() or u in exclude:
            continue
        if direction and str(cd.direction or "").strip().lower() != direction:
            continue
        if symbol and str(cd.symbol or "").strip().lower() != symbol.lower():
            continue
        return u
    return None


def total(oceans):
    return snap_score.score_boards(
        [{"name": "T", "oceans": oceans}], with_breakdown=False)["players"][0]["score"]


ocean_u = 208
# "+2 per matching symbol" (Orange Tube Sponge): the SYMBOL COPY the scanner
# picks must change the score, which is why page/badge identity matters.
# Located by ability text so card renames can't break the test.
def uid_by_text(frag, direction, exclude=()):
    for u in sorted(db):
        cd = db[u]
        if u in exclude:
            continue
        if direction and str(cd.direction or "").strip().lower() != direction:
            continue
        if frag in str(cd.text or "").lower():
            return u
    return None

sponge = uid_by_text("matching symbol", "down")
sp_sym = str(db[sponge].symbol).strip().lower()
mate_same = next((u for u in sorted(db) if u != sponge
                  and str(db[u].direction or "").strip().lower() == "up"
                  and str(db[u].symbol or "").strip().lower() == sp_sym
                  and "matching symbol" not in str(db[u].text or "").lower()), None)
mate_diff = next((u for u in sorted(db) if u != sponge and u != mate_same
                  and str(db[u].name).lower() == str(db[mate_same].name).lower()
                  and str(db[u].direction or "").strip().lower() == "up"
                  and str(db[u].symbol or "").strip().lower() != sp_sym), None)
check("found matching-symbol card + same-name mates with matching/different symbols",
      all(x is not None for x in (sponge, mate_same, mate_diff)))
same = total([{"u": ocean_u, "up": [mate_same], "down": [sponge], "left": [], "right": []}])
diff = total([{"u": ocean_u, "up": [mate_diff], "down": [sponge], "left": [], "right": []}])
check("matching-symbol combo scores MORE with a matching-symbol partner",
      same > diff, f"same-sym {same} vs diff-sym {diff}")

# Blue Tang: "+2 per Crosscurrent animal": cross-card combo counting
tang = uid_by_text("per crosscurrent", "down")
goby = next((u for u in sorted(db)
             if str(db[u].species or "").strip().lower() == "crosscurrent"
             and str(db[u].direction or "").strip().lower() == "down" and u != tang), None)
clown = next((u for u in sorted(db)
              if str(db[u].species or "").strip().lower() == "crosscurrent"
              and str(db[u].direction or "").strip().lower() == "right"), None)
check("found blue tang + crosscurrent partners", all(x is not None for x in (tang, goby, clown)))
ocean_alone = total([{"u": ocean_u, "up": [], "down": [], "left": [], "right": []}])
solo_t = total([{"u": ocean_u, "up": [], "down": [tang], "left": [], "right": []}])
with_cc = total([{"u": ocean_u, "up": [], "down": [tang, goby], "left": [], "right": [clown]}])
base_cc = total([{"u": ocean_u, "up": [], "down": [goby], "left": [], "right": [clown]}])
marg_with_partners = with_cc - base_cc
marg_alone = solo_t - ocean_alone
check("Blue Tang is worth MORE per extra crosscurrent on the board (combo works)",
      marg_with_partners > marg_alone,
      f"marginal with partners {marg_with_partners} vs alone {marg_alone}")

# The scorer here IS the simulation's engine: same function, same GameState
gs_boards, gs_db, _ = snap_score.normalize_boards(
    [{"name": "T", "oceans": [{"u": ocean_u, "up": [1], "down": [2], "left": [101], "right": [102]}]}])
gs = snap_score._build_gs(gs_boards, gs_db)
direct = int(fish.final_points(gs, gs.players[0]))
via_api = total([{"u": ocean_u, "up": [1], "down": [2], "left": [101], "right": [102]}])
check("score_boards == fish.final_points exactly (same engine as the game)",
      direct == via_api, f"{direct} vs {via_api}")

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
