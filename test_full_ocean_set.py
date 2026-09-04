"""Every Last Drop: hold every printed copy of one Ocean type in one game.

The achievement's whole meaning is "nobody else can have this Ocean", so it is
only correct while its copy table matches the deck. Three places have to agree:

  * the real CARD_DB (the deck as printed),
  * OCEAN_FULL_SET in multiplayer/client/js/preview-app.js (the online game),
  * OCEAN_FULL_SET in snap_score.py (Snap & Score, which scores a physical
    board from a photo).

If a card file ever adds or drops a copy and only one table follows, the
achievement either fires early (a player is credited for a set they do not
hold) or becomes unwinnable. These tests pin all three to each other, and then
exercise the Snap & Score check on real board shapes.

Run:  python3 test_full_ocean_set.py
"""

import collections
import importlib.util
import pathlib
import random
import re
import sys

spec = importlib.util.spec_from_file_location("fish", "fish_game_all_in_one.py")
fish = importlib.util.module_from_spec(spec)
sys.modules["fish"] = fish
spec.loader.exec_module(fish)

import snap_score

ROOT = pathlib.Path(__file__).resolve().parent
PREVIEW_APP = ROOT / "multiplayer" / "client" / "js" / "preview-app.js"

CARD_DB = fish.load_card_db()

FAILURES = []
CHECKS = 0


def check(cond, label):
    global CHECKS
    CHECKS += 1
    if not cond:
        FAILURES.append(label)
    return bool(cond)


def deck_ocean_counts():
    """Copies of each Ocean, counted off the deck the game actually builds."""
    pair_primary, face_to_primary = fish.build_non_ocean_pair_maps(CARD_DB)
    deck, _end = fish.build_deck_with_late_end_game(
        CARD_DB, pair_primary, face_to_primary, random.Random(20260904)
    )
    ms = fish.MatchState(
        pair_primary_to_faces=pair_primary, face_to_primary=face_to_primary
    )
    gs = fish.GameState(card_db=CARD_DB, players=[], deck=list(deck))
    counts = collections.Counter()
    for uid in deck:
        if not fish.entry_is_ocean(ms, gs, uid):
            continue
        names = {
            str(CARD_DB[f].name).strip().lower()
            for f in pair_primary.get(uid, [uid])
            if f in CARD_DB
        }
        # The END GAME card reads as an Ocean entry but is not a playable Ocean.
        names.discard("end game")
        for nm in names:
            counts[nm] += 1
    return counts


def client_table():
    """OCEAN_FULL_SET as written in preview-app.js."""
    src = PREVIEW_APP.read_text(encoding="utf-8")
    m = re.search(r"const OCEAN_FULL_SET = \{(.*?)\n  \};", src, re.S)
    if not m:
        return None
    out = {}
    for name, num in re.findall(r'"([^"]+)":\s*(\d+)', m.group(1)):
        out[name] = int(num)
    return out


def test_A_deck_counts_are_the_source_of_truth():
    deck = deck_ocean_counts()
    check(len(deck) == 8, f"expected 8 Ocean types in the deck, found {len(deck)}")
    for table, where in ((client_table(), "preview-app.js"), (snap_score.OCEAN_FULL_SET, "snap_score.py")):
        if not check(table is not None, f"could not read OCEAN_FULL_SET from {where}"):
            continue
        for name, want in deck.items():
            got = table.get(name)
            check(got == want,
                  f"{where}: {name} is {want} in the deck but {got} in OCEAN_FULL_SET")
        # Aliases (Mangrove/Mangroves) are allowed, unknown Oceans are not.
        for name in table:
            check(name in deck or name.rstrip("s") in deck,
                  f"{where}: OCEAN_FULL_SET has '{name}', which is not an Ocean in the deck")
    print(f"A PASS: deck Oceans {dict(sorted(deck.items()))}")


def test_B_both_tables_agree_with_each_other():
    a, b = client_table(), snap_score.OCEAN_FULL_SET
    if not check(a is not None, "no client table to compare"):
        return
    check(a == b, f"the two OCEAN_FULL_SET tables differ: {a} vs {b}")
    print("B PASS: preview-app.js and snap_score.py hold identical tables")


def test_C_achievement_is_defined_at_2500_xp():
    src = PREVIEW_APP.read_text(encoding="utf-8")
    m = re.search(r'\{ id:"every_last_drop",.*?\},\n', src, re.S)
    if not check(m is not None, "every_last_drop is not in ACHIEVEMENT_DEFS"):
        return
    line = m.group(0)
    check("xp:2500" in line.replace(" ", ""), f"every_last_drop is not worth 2500 XP: {line.strip()}")
    check('name:"Every Last Drop"' in line, "every_last_drop lost its name")
    # It must be granted in-game AND at game end, and reset between games.
    check(src.count('"every_last_drop"') >= 3,
          "every_last_drop is defined but not granted/reset in all three places")
    check("fullOceanSet: null" in src, "the per-game tracker field is missing")
    check("_gameAchTracker.fullOceanSet      = null;" in src,
          "the tracker field is never reset, so one set would credit every later game")
    print("C PASS: every_last_drop defined at 2500 XP, tracked, granted and reset")


def _board(pairs):
    """A board of (ocean name, copies) with nothing attached."""
    by_name = {}
    for uid, cd in CARD_DB.items():
        by_name.setdefault(str(cd.name).strip().lower(), []).append(uid)
    oceans = []
    for name, n in pairs:
        uids = by_name[name]
        check(len(uids) >= n, f"deck has only {len(uids)} '{name}', test wants {n}")
        for u in uids[:n]:
            oceans.append({"u": u, "up": [], "down": [], "left": [], "right": []})
    return {"oceans": oceans}


def _fires(pairs):
    facts = snap_score._board_facts(_board(pairs), CARD_DB)
    return facts["has_full_ocean_set"]


def test_D_snap_score_fires_only_on_a_complete_set():
    check(_fires([("tide pool", 6)]), "all 6 Tide Pools did not fire the achievement")
    check(_fires([("artificial reef", 6)]), "all 6 Artificial Reefs did not fire")
    check(_fires([("deep ocean", 8)]), "all 8 Deep Oceans did not fire")
    check(_fires([("coral reef", 13)]), "all 13 Coral Reefs did not fire")
    check(not _fires([("tide pool", 5)]), "5 of 6 Tide Pools fired it early")
    check(not _fires([("coral reef", 8), ("kelp forest", 9)]),
          "a big board with no complete set fired it")
    check(not _fires([]), "an empty board fired it")
    # A complete set buried in a wide board still counts.
    check(_fires([("tide pool", 6), ("pier", 3), ("mangrove", 2)]),
          "a complete Tide Pool set went unseen next to other Oceans")
    print("D PASS: Snap & Score fires on a complete set and never short of one")


def test_E_it_is_in_the_snap_score_achievement_list():
    ids = [d["id"] for d in snap_score._ach_defs()]
    check("every_last_drop" in ids, "Snap & Score does not offer every_last_drop")
    for d in snap_score._ach_defs():
        if d["id"] == "every_last_drop":
            check(d["xp"] == 2500, f"Snap & Score pays {d['xp']} XP, the client pays 2500")
    print("E PASS: Snap & Score grants it at the same 2500 XP")


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
    print(f"\nchecks: {CHECKS}")
    if FAILURES:
        print(f"{len(FAILURES)} FAILED:")
        for f in FAILURES:
            print("  ✗ " + f)
        return 1
    print("Every Last Drop OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
