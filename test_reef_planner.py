#!/usr/bin/env python3
"""The Reef Planner: the bot brain from Eugenie Clark (A) up.

Run:  python3 test_reef_planner.py

Six things have to hold, and every one of them breaks quietly:

 1. ITS SCORER IS THE GAME'S SCORER. The planner scores boards thousands of
    times a move with fast_points, a copy of final_points that caches what does
    not change mid-move. One point of drift and it plans for a game that is not
    the one being played. Checked on every board of real games at 2, 4 and 6
    players, and on those boards with random cards added.

 2. THINKING LEAVES NO MARK. It tries every move on the live state and puts it
    back. Hands, boards, the Pool, the deck, the log and every flag must be
    exactly as they were after it decides.

 3. IT DOES NOT PEEK. Its worlds are built from which cards are hidden, never
    from where they are: reorder the real deck, or trade cards between the deck
    and another player's hand, and the move it picks must not change.

 4. IT PLAYS WHOLE GAMES. Two, four and six planners finish games with no
    exception and no move the engine rejects.

 5. THE LADDER USES IT WHERE IT SAYS. A and up plan; below A they do not. And
    the server image carries it: .gitignore, .dockerignore and the Dockerfile
    are all allowlists, and missing any one of them breaks the deploy.

 6. A BOARD IS NAMED FOR WHAT SCORED. The recap names every player's strategy,
    and it used to call nearly every board "King Salmon". A board is King
    Salmon only with a King Salmon on it, and a Bird/Lobster board is B-Lob.
"""
import os
import random
import sys

os.environ["PYTHONHASHSEED"] = os.environ.get("PYTHONHASHSEED", "0")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fish_game_all_in_one as fish  # noqa: E402
import reef_planner as rp  # noqa: E402

PASS = 0
FAIL = 0


def check(cond, name, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  ✗ FAIL: {name}" + (f"  → {extra}" if extra else ""))


def section(title):
    print(f"\n{title}")


DB = fish.load_card_db()
FAST = dict(rp.lite_params(rp.PARAMS))


# ── 1. fast_points is final_points ──────────────────────────────────────────
section("the planner's scorer agrees with the game's, board for board")

rng = random.Random(7)
checked = 0
mismatches = []


def compare(gs, p):
    global checked
    others = rp.others_summary(gs, p)
    a, b = fish.final_points(gs, p), rp.fast_points(gs, p, others)
    checked += 1
    if a != b and len(mismatches) < 3:
        mismatches.append((a, b, [DB[o].name for o in p.board_oceans]))


def random_policy(gs, ms, p):
    for pl in gs.players:
        compare(gs, pl)
        for _ in range(2):
            uid = rng.choice(list(DB))
            card = DB[uid]
            if fish.card_name_lc(card) == "end game":
                continue
            if fish.is_ocean(card):
                if uid in pl.ocean_slots:
                    continue
                rp._place(gs, pl, uid, None)
                try:
                    compare(gs, pl)
                finally:
                    rp._unplace(gs, pl, uid, None)
            elif pl.board_oceans:
                ocean = rng.choice(pl.board_oceans)
                rp._place(gs, pl, uid, ocean)
                try:
                    compare(gs, pl)
                finally:
                    rp._unplace(gs, pl, uid, ocean)
    acts = fish.legal_actions(gs, ms, p, include_draw=True)
    plays = [a for a in acts if a.kind != "draw"]
    if plays and rng.random() < 0.75:
        return rng.choice(plays)
    return rng.choice(acts) if acts else None


for n in (2, 4, 6):
    fish.run_match(card_db=DB, player_names=[f"P{i}" for i in range(n)],
                   action_policies=[random_policy] * n, seed=900 + n, max_turns=500,
                   ai_difficulties=["charles_darwin"] * n)
check(checked > 5000, "thousands of boards were compared", str(checked))
check(not mismatches, "fast_points == final_points on every one of them", str(mismatches))


# ── 2 & 3. thinking leaves no mark, and never peeks ─────────────────────────
section("a decision changes nothing, and does not depend on the hidden order")


def fingerprint(gs, ms):
    return (
        [(list(p.hand), list(p.board_oceans),
          {o: (list(s.up), list(s.down), list(s.left), list(s.right)) for o, s in p.ocean_slots.items()},
          sorted((k, repr(v)) for k, v in p.flags.items()
                 if not k.startswith("_planner") and not k.startswith("_strategy_family")))
         for p in gs.players],
        list(gs.deck), len(gs.log), gs.turn_index, gs.round_count,
        list(ms.pool), list(ms.discard_pile), ms.end_game_triggered, ms.final_turns_remaining,
    )


def describe(a):
    if a is None:
        return None
    return (a.kind, a.card_uid, a.face_uid, a.ocean_uid, a.draw_from_pool,
            tuple(a.pool_pick_uids), a.use_star, tuple(a.payment_uids))


mark_failures = []
peek_failures = []
decisions = [0]


def probing_policy(gs, ms, p):
    before = fingerprint(gs, ms)
    flags_before = dict(p.flags)
    seed = gs.round_count * 1000 + gs.turn_index * 10 + len(p.hand)
    chosen = rp.choose_action(gs, ms, p, params=FAST, rng=random.Random(seed))
    decisions[0] += 1
    after = fingerprint(gs, ms)
    if after != before and len(mark_failures) < 2:
        diff = [i for i, (x, y) in enumerate(zip(before, after)) if x != y]
        detail = ""
        if 0 in diff:
            for pb, pa in zip(before[0], after[0]):
                if pb != pa:
                    detail = str([(x, y) for x, y in zip(pb, pa) if x != y])[:400]
                    break
        mark_failures.append((gs.round_count, describe(chosen), diff, detail))
    flags_after = dict(p.flags)
    # Same hidden cards, different order, some swapped with another hand.
    if decisions[0] % 3 == 0 and len(gs.deck) > 4:
        saved_deck = list(gs.deck)
        other = next((o for o in gs.players if o is not p and o.hand), None)
        saved_hand = list(other.hand) if other else None
        movable = [u for u in gs.deck if u != ms.end_game_uid]
        random.Random(seed + 1).shuffle(movable)
        it = iter(movable)
        gs.deck[:] = [u if u == ms.end_game_uid else next(it) for u in gs.deck]
        if other is not None:
            i = next(i for i, u in enumerate(gs.deck) if u != ms.end_game_uid)
            gs.deck[i], other.hand[0] = other.hand[0], gs.deck[i]
        # Replay from the same bookkeeping the first call started from.
        p.flags.clear()
        p.flags.update(flags_before)
        again = rp.choose_action(gs, ms, p, params=FAST, rng=random.Random(seed))
        p.flags.clear()
        p.flags.update(flags_after)
        gs.deck[:] = saved_deck
        if other is not None:
            other.hand[:] = saved_hand
        if describe(again) != describe(chosen) and len(peek_failures) < 2:
            peek_failures.append((describe(chosen), describe(again)))
    return chosen


for n in (3, 5):
    fish.run_match(card_db=DB, player_names=[f"P{i}" for i in range(n)],
                   action_policies=[probing_policy] * n, seed=4200 + n, max_turns=500,
                   ai_difficulties=["charles_darwin"] * n)
check(decisions[0] > 100, "the planner made real decisions", str(decisions[0]))
check(not mark_failures, "every decision left the game exactly as it found it", str(mark_failures))
check(not peek_failures, "reordering the hidden cards never changed a decision", str(peek_failures))


# ── 4. whole games ──────────────────────────────────────────────────────────
section("planners finish games at 2, 4 and 6 players")

for n in (2, 4, 6):
    errors = []

    def pol(gs, ms, p, _e=errors):
        try:
            return rp.choose_action(gs, ms, p, params=FAST, rng=random.Random(len(gs.log)))
        except Exception as exc:  # pragma: no cover - reported below
            _e.append(repr(exc))
            return None

    out = {}
    gs, ms = fish.run_match(card_db=DB, player_names=[f"P{i}" for i in range(n)],
                            action_policies=[pol] * n, seed=77 + n, max_turns=800,
                            ai_difficulties=["charles_darwin"] * n, training_out=out)
    finals = [fish.final_points(gs, p) for p in gs.players]
    check(not errors, f"{n}P: no exception inside the planner", str(errors[:2]))
    check(ms.end_game_triggered, f"{n}P: the game ran to END GAME")
    rejected = int((out.get("rules_errors") if isinstance(out, dict) else 0) or 0)
    check(rejected == 0, f"{n}P: the engine rejected no planner move", str(out.get("rules_errors")))
    check(all(len(p.hand) <= fish.HAND_LIMIT for p in gs.players), f"{n}P: nobody ended over the hand limit")
    check(max(finals) > 20, f"{n}P: somebody actually built a board", str(finals))
    families = [p.flags.get("_strategy_family") for p in gs.players]
    check(all(f in rp.STRATEGY_FAMILIES for f in families), f"{n}P: every planner committed to a strategy",
          str(families))


# ── 5. the ladder ───────────────────────────────────────────────────────────
section("A and up plan; below A they do not")

for grade in fish.BOT_GRADE_ORDER:
    rank = fish.bot_grade_rank(grade)
    uses = rp.params_for_grade(grade) is not None
    want = rank >= fish.bot_grade_rank("eugenie_clark")
    check(uses == want, f"{fish.bot_grade_label(grade)} {'plans' if want else 'does not plan'}")
check(rp.params_for_grade("ss+") is not None, "an old saved id (ss+) still reaches the planner")


# ── 5b. it ships ────────────────────────────────────────────────────────────
section("the server image carries the planner")

HERE = os.path.dirname(os.path.abspath(__file__))
# multiplayer_server.py imports reef_planner at startup, and all three of these
# are allowlists: miss one and Render builds an image that cannot boot, or
# cannot build at all.
check("!reef_planner.py" in open(os.path.join(HERE, ".gitignore")).read(), ".gitignore tracks reef_planner.py")
check("!reef_planner.py" in open(os.path.join(HERE, ".dockerignore")).read(),
      ".dockerignore lets reef_planner.py into the build")
check("COPY reef_planner.py /app/reef_planner.py" in open(os.path.join(HERE, "Dockerfile")).read(),
      "the Dockerfile copies reef_planner.py into the image")


# ── 6. naming a board ───────────────────────────────────────────────────────
section("a board is named for the strategy that scored it")

by_name = {}
for uid, c in DB.items():
    by_name.setdefault(c.name, []).append(uid)


def board(*oceans):
    """Oceans with their animals, each animal "Name" or "Name@direction". Every
    placement is checked with the engine's own attach rule."""
    p = fish.PlayerState("B")
    used = set()

    def take(name, ocean, direction=None):
        for u in by_name[name]:
            if u in used or fish.is_ocean(DB[u]) != ocean:
                continue
            if direction and fish.card_direction_lc(DB[u]) != direction:
                continue
            used.add(u)
            return u
        raise KeyError(name)

    for ocean_name, animals in oceans:
        o = take(ocean_name, True)
        p.board_oceans.append(o)
        p.ocean_slots[o] = fish.OceanSlots()
        for a in animals:
            name, _, direction = a.partition("@")
            u = take(name, False, direction or None)
            assert fish.can_attach_to_ocean(fish.GameState(card_db=DB, players=[p], deck=[]), p, u, o), a
            p.ocean_slots[o].slot(fish.card_direction_lc(DB[u])).append(u)
    gs = fish.GameState(card_db=DB, players=[p], deck=[])
    return fish.detect_player_strategy(gs, p)


check(board(("Arctic Ocean", ["Horned Puffin", "Mantis Shrimp", "Sailfish"]),
            ("Mangrove", ["Great Albatross", "Lobster"]),
            ("Pier", ["Peruvian Pelican"])) != "King Salmon",
      "Arctic Oceans, Mangroves and play-again birds without a salmon are not King Salmon")
check(board(("Artificial Reef", ["California Gull", "Lobster", "Lobster", "Lobster"]),
            ("Coral Reef", ["Emperor Penguin", "Mantis Shrimp"]),
            ("Deep Ocean", ["Horned Puffin"])) == "B-Lob",
      "gulls on stacked lobsters with a flock beside them are B-Lob")
check(board(("Coral Reef", ["Staghorn Coral"]),
            ("Coral Reef", ["Elk Horn Coral"]),
            ("Coral Reef", ["Elk Horn Coral"]),
            ("Deep Ocean", ["Deep Sea Coral"])) == "Coral",
      "a coral board is Coral")
check(board(("Deep Ocean", ["King Salmon@left", "Lobster", "Horned Puffin", "Sailfish@right"]),
            ("Tide Pool", ["King Salmon@right", "Mantis Shrimp", "Osprey", "Yellowfin Tuna@left"]),
            ("Pier", ["Sailfish@left"])) == "King Salmon",
      "two King Salmon on two full oceans are King Salmon")
check(board(("Kelp Forest", ["Spinner Dolphin@right", "Narwhal@left"]),
            ("Deep Ocean", ["Bottlenose Dolphin@left", "Great White Shark@right"])) == "Mammals",
      "dolphins, a narwhal and a shark are Mammals")
check(board() == "Best Guess", "an empty board is a Best Guess")

# ── the knobs that decide whether it plays a PLAN or just counts points ────
# A knob nothing can reach is a knob that does not exist. switch_margin was
# read as params.get("switch_margin", 4.0) and was in no table at all, so no
# grade override and no tuning run could ever move it -- and it decides one of
# the few things that is purely about understanding a plan rather than counting
# points: how far ahead another plan has to be scoring before this bot admits
# its pieces went somewhere else.
section("the knobs that make it play a plan, not just points")

import bot_evolve as _be

check(rp.PARAMS.get("switch_margin") == 4.0,
      "switch_margin is a real parameter, at exactly the value it was hard-coded to",
      f"{rp.PARAMS.get('switch_margin')}")
check("switch_margin" in rp.TUNABLE_BOUNDS,
      "…and a training run is allowed to move it")

_understanding = ("loyalty", "crowding", "switch_margin")
for _k in _understanding:
    check(_k in rp.TUNABLE_BOUNDS, f"{_k} is tunable")
    check(_k in _be.PLANNER_FOCUS,
          f"{_k} is aimed at, not left to the quarter of mutations that roam",
          "at one key in twenty-four it would be tried about once in a hundred "
          "generations")

# The four pieces of finished machinery that ship switched off are where the
# unmeasured ground is: turning them on changes between half and all of the
# planner's moves, and nobody has ever measured whether those moves are better.
for _k in ("adaptive_turn_value", "rival_weight", "denial", "final_sweep"):
    check(float(rp.PARAMS[_k]) == 0.0, f"{_k} still ships switched off")
    check(_k in _be.PLANNER_FOCUS,
          f"{_k} is aimed at, because switched-off machinery is unclaimed ground")

check(_be.PLANNER_BOUNDS == dict(rp.TUNABLE_BOUNDS),
      "the tuner and the planner agree on every knob and its range")

# Every mutant has to stay inside the planner's own bounds, or it writes a file
# the planner then refuses to read back.
import random as _random
_rng = _random.Random(5)
_champ = _be.planner_defaults()
_bad = [(k, v) for _ in range(200)
        for k, v in _be._mutate_params(_champ, _rng, 0.6).items()
        if not (rp.TUNABLE_BOUNDS[k][0] <= v <= rp.TUNABLE_BOUNDS[k][1])]
check(not _bad, "no mutant ever leaves the range its knob is allowed", f"{_bad[:3]}")

print(f"\n{'=' * 50}\nRESULT: {PASS} passed, {FAIL} failed")
raise SystemExit(1 if FAIL else 0)
