"""Every card is in the deck, every game, and no card ever leaves the table.

The deck is the whole game: 253 printed faces, 161 physical cards (92 of them
two-sided plus 69 single-sided ocean cards), and a game is meant to be dealt
from ALL of them. A card that is quietly left out can never be drawn, so its
strategy is dead and the copy counts the rulebook, the Encyclopedia and the
achievements all quote are wrong for that game.

test_deck_shuffle_fairness.py proves the shuffle is uniform. This suite proves
completeness, on two independent readings of the deck:

  A. The card files, parsed here by hand, agree with the engine's card_db about
     which faces exist and which pair into one two-sided card. Every face
     belongs to exactly one deck entry, so no face is unreachable.
  B. Every freshly built deck is the complete set: all 161 cards, once each.
  C. Real games at 2 through 8 players, censused at EVERY decision from the
     deal to the final turn: all 161 cards are always accounted for between
     the deck, the hands, the pool, the discard pile and the boards. Nothing
     vanishes, nothing is in two places at once.
  D. The engine's own runtime guard (validate_card_census, which every game
     runs at setup and at every turn) agrees, and actually catches a card
     taken off the table or dealt twice.
  E. Every card face has art the client can show, so "in the deck" also means
     playable on screen.

Run:  python3 test_deck_completeness.py
"""

import collections
import importlib.util
import os
import pathlib
import random
import sys

ROOT = pathlib.Path(__file__).resolve().parent

spec = importlib.util.spec_from_file_location("fish", str(ROOT / "fish_game_all_in_one.py"))
fish = importlib.util.module_from_spec(spec)
sys.modules["fish"] = fish
spec.loader.exec_module(fish)

CARD_DB = fish.load_card_db()
CARD_FILES = ("cards_vertical.txt", "cards_lr.txt", "cards_oceans.txt")

FAILURES = []
CHECKS = 0


def check(cond, label):
    global CHECKS
    CHECKS += 1
    if not cond:
        FAILURES.append(label)
    return bool(cond)


def faces_from_card_files():
    """uid -> (name, species), parsed without the engine so the test and the
    code under test are not reading the same parser."""
    faces = {}
    for name in CARD_FILES:
        path = ROOT / name
        for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not raw.strip():
                continue
            parts = raw.split("\t")
            if not check(len(parts) == 7, f"{name}:{lineno}: {len(parts)} fields, expected 7"):
                continue
            uid = int(parts[0])
            check(uid not in faces, f"{name}:{lineno}: uid {uid} is used twice")
            faces[uid] = (parts[1].strip(), parts[3].strip().lower())
    return faces


def expected_entries(faces):
    """One entry per physical card: an odd uid and the even uid after it are the
    two faces of one two-sided card (oceans are single-sided)."""
    entries, backs = set(), set()
    for uid in sorted(faces):
        if uid in backs:
            continue
        partner = uid + 1
        two_sided = (
            uid % 2 == 1
            and partner in faces
            and faces[uid][1] != "ocean"
            and faces[partner][1] != "ocean"
        )
        entries.add(uid)
        if two_sided:
            backs.add(partner)
    return entries, backs


FACES = faces_from_card_files()
ENTRIES, BACKS = expected_entries(FACES)
PAIRS, FACE_TO_PRIMARY = fish.build_non_ocean_pair_maps(CARD_DB)


def fresh_match_state():
    return fish.MatchState(pair_primary_to_faces=PAIRS, face_to_primary=FACE_TO_PRIMARY)


def census(gs, ms):
    """entry uid -> [zone, ...] for every card anywhere in the match."""
    return fish.card_census(gs, ms)


def test_A_the_card_files_and_the_engine_agree():
    check(
        set(CARD_DB) == set(FACES),
        f"card_db and the card files disagree: "
        f"only in the db {sorted(set(CARD_DB) - set(FACES))[:8]}, "
        f"only in the files {sorted(set(FACES) - set(CARD_DB))[:8]}",
    )
    engine_entries = {uid for uid in CARD_DB if FACE_TO_PRIMARY.get(uid, uid) == uid}
    check(
        engine_entries == ENTRIES,
        f"the engine's deck entries differ from the card files: "
        f"missing {sorted(ENTRIES - engine_entries)[:8]}, extra {sorted(engine_entries - ENTRIES)[:8]}",
    )
    check(len(FACES) == 253, f"{len(FACES)} card faces, expected 253")
    check(len(ENTRIES) == 161, f"{len(ENTRIES)} physical cards, expected 161")

    # Every face is reachable: it is either an entry or the back of exactly one.
    ms = fresh_match_state()
    owners = collections.Counter()
    for primary in ENTRIES:
        for face in fish.entry_faces(ms, primary):
            owners[face] += 1
    unreachable = sorted(set(FACES) - set(owners))
    check(not unreachable, f"card faces no entry ever deals: {unreachable[:8]}")
    shared = sorted(f for f, n in owners.items() if n != 1)
    check(not shared, f"card faces owned by more than one entry: {shared[:8]}")
    print(
        f"A PASS: {len(FACES)} faces ({len(BACKS)} of them backs) -> "
        f"{len(ENTRIES)} cards, every face dealt by exactly one of them"
    )


def test_B_every_built_deck_is_the_whole_deck():
    rounds = 5000
    bad = []
    for seed in range(rounds):
        deck, end_uid = fish.build_deck_with_late_end_game(
            CARD_DB, PAIRS, FACE_TO_PRIMARY, random.Random(seed)
        )
        counts = collections.Counter(deck)
        if set(counts) != ENTRIES or any(n != 1 for n in counts.values()):
            bad.append(
                (seed, sorted(ENTRIES - set(counts))[:4], [u for u, n in counts.items() if n != 1][:4])
            )
        if seed == 0:
            check(end_uid is not None, "the deck was built without an END GAME card")
    check(not bad, f"{len(bad)}/{rounds} built decks were not the full set, e.g. {bad[:2]}")
    print(f"B PASS: {rounds} freshly built decks, each one all {len(ENTRIES)} cards exactly once")


def test_C_real_games_never_lose_a_card():
    weights = fish.stabilize_weights(dict(fish.default_weights()))
    problems = []
    decisions = 0
    games = 0

    for count in range(2, 9):
        base = fish._train_make_policy({"weights": weights}, epsilon=0.02)

        def policy(gs, ms, player, _base=base, _count=count):
            nonlocal decisions
            decisions += 1
            seen = census(gs, ms)
            missing = sorted(ENTRIES - set(seen))
            dupes = sorted(u for u, z in seen.items() if len(z) > 1)
            unknown = sorted(set(seen) - ENTRIES)
            if missing or dupes or unknown:
                problems.append((f"{_count}p decision {decisions}", missing[:4], dupes[:4], unknown[:4]))
            return _base(gs, ms, player)

        gs, ms = fish.run_match(
            card_db=CARD_DB,
            player_names=[f"AI_{i + 1}" for i in range(count)],
            action_policies=[policy] * count,
            seed=4100 + count,
            max_turns=600,
            human_index=None,
            verbose=False,
            verbose_state=False,
        )
        games += 1

        seen = census(gs, ms)
        check(
            set(seen) == ENTRIES and all(len(z) == 1 for z in seen.values()),
            f"{count}p: the finished game held {len(seen)} cards, expected {len(ENTRIES)} once each",
        )
        check(
            not fish.validate_card_census(gs, ms, where=f"{count}p end"),
            f"{count}p: the engine's own census flagged the finished game",
        )
        # The log is the game's own record: a clean game must not have reported
        # a census problem at any turn.
        shouted = [line for line in gs.log if "CARD CENSUS" in line]
        check(not shouted, f"{count}p: the engine logged a census problem: {shouted[:2]}")

    check(not problems, f"{len(problems)} mid-game censuses did not add up, e.g. {problems[:2]}")
    print(
        f"C PASS: {games} games at 2-8 players, {decisions} censuses from deal to last turn, "
        f"all {len(ENTRIES)} cards present at every single one"
    )


def test_D_the_runtime_guard_catches_a_missing_card():
    """The guard is only worth having if it fires. Take a card off the table and
    deal another one twice, and it must say so."""
    gs, ms = _settled_game()
    check(not fish.validate_card_census(gs, ms), "the guard flagged a healthy game")

    taken = gs.deck.pop()
    problems = fish.validate_card_census(gs, ms, where="test")
    check(
        any("NO zone" in p for p in problems),
        f"a card removed from the game was not reported: {problems}",
    )
    gs.deck.append(taken)

    gs.players[0].hand.append(gs.deck[0])
    problems = fish.validate_card_census(gs, ms, where="test")
    check(
        any("places at once" in p for p in problems),
        f"a card dealt into two zones was not reported: {problems}",
    )
    gs.players[0].hand.pop()
    check(not fish.validate_card_census(gs, ms), "the guard stayed unhappy after the state was put back")
    print("D PASS: the runtime guard catches both a missing card and a duplicated one")


def _settled_game():
    """A dealt, mulliganed game, exactly as run_match sets one up."""
    rng = random.Random(90210)
    deck, end_uid = fish.build_deck_with_late_end_game(CARD_DB, PAIRS, FACE_TO_PRIMARY, rng)
    players = [fish.PlayerState(f"P{i + 1}") for i in range(4)]
    gs = fish.GameState(card_db=CARD_DB, players=players, deck=deck)
    ms = fish.MatchState(
        end_game_uid=end_uid, pair_primary_to_faces=PAIRS, face_to_primary=FACE_TO_PRIMARY
    )
    fish.start_game(gs, starting_hand=8, shuffle=False)
    fish.perform_mulligans(gs, ms)
    return gs, ms


def test_E_every_card_face_has_art_on_disk():
    """imagePathForUid() in preview-app.js maps a uid to one art file. A card
    with no art is in the deck but blank on screen, which is the same thing as
    not being in the game."""
    def art_paths(uid):
        if 1 <= uid <= 96:
            page = (uid + 1) // 2
            folder = "horizontal_cards"
        elif 101 <= uid <= 188:
            page = ((uid - 101) // 2) + 1
            folder = "vertical_cards"
        elif 201 <= uid <= 269:
            page = uid - 200
            folder = "oceans_cards"
        else:
            return None
        return [ROOT / folder / f"page_{page:02d}.png", ROOT / folder / f"page_{page:02d}.mini.jpg"]

    unmapped, missing = [], []
    files = set()
    for uid in sorted(FACES):
        paths = art_paths(uid)
        if paths is None:
            unmapped.append((uid, FACES[uid][0]))
            continue
        for path in paths:
            files.add(path)
            if not path.exists():
                missing.append((uid, FACES[uid][0], os.path.join(path.parent.name, path.name)))
    check(not unmapped, f"card faces the client cannot find art for: {unmapped[:6]}")
    check(not missing, f"card art files that are not on disk: {missing[:6]}")
    print(f"E PASS: all {len(FACES)} faces resolve to art, {len(files)} files, every one present")


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
    print("ALL DECK COMPLETENESS TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
