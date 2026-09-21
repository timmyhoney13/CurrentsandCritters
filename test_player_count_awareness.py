#!/usr/bin/env python3
"""What the bots know about the size of the table they are sitting at.

Run:  python3 test_player_count_awareness.py

A player gets about 67 turns of their own in a 2P game and 19 in a 6P game, off
the same deck. Until the code these tests cover, no bot knew that. The weighted
chooser scored a board the same way at every table size, and the Reef Planner
answered "is it worth feeding the Pool to play this?" the same way against one
opponent as against five.

Five things have to hold:

 1. THE CLOCK IS REAL. table_clock has to say a 6P table is nearly out of time
    while a 2P table on the same deck still has plenty, and it has to read
    exactly 1.0 rivals at the 4P the bots were tuned at, so 4P is uncorrected.

 2. THE FEATURES CARRY IT. The four count-aware features are products of the
    clock and something that differs between moves. A feature that is the same
    for every move in a decision cannot change the move chosen, so each one is
    checked for actually varying, and for growing with the table size.

 3. SWITCHED OFF, NOTHING MOVES. All four weights and all six planner per-rival
    knobs start at zero, and at zero the bots must play the move they played
    before any of this existed. This is what makes the whole thing safe to ship
    untrained: it cannot lose a game until a measurement says it wins one.

 4. THE PLANNER'S KNOBS BEND WITH THE TABLE, AND ONLY WITHIN THEIR RANGE.

 5. A DAMAGED TUNING FILE CHANGES NOTHING. load_tuned_params is the one path by
    which a training run reaches the live bots, so it has to refuse everything
    it does not recognise rather than trust the file.
"""
from __future__ import annotations

import json
import os
import re
import random
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fish_game_all_in_one as fish
import reef_planner as rp
import bot_evolve

PASS = 0
FAIL = 0

NEW_WEIGHTS = ("future_urgency", "tempo_urgency", "crowd_cost", "pool_greed")
PER_RIVAL = ("denial_per_rival", "rival_weight_per_rival", "plan_discount_per_rival",
             "turn_value_per_rival", "survival_per_rival", "crowding_per_rival")


def check(cond, name, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  ✗ FAIL: {name}" + (f"  → {extra}" if extra else ""))


def section(title):
    print(f"\n{title}")


class FakeGS:
    """Only what table_clock reads."""
    def __init__(self, players, deck):
        self.players = [None] * players
        self.deck = [0] * deck


# Deck sizes at each table's first turn, measured from real games.
FRESH_DECK = {2: 145, 3: 137, 4: 129, 5: 121, 6: 113}

# ── 1. the clock ────────────────────────────────────────────────────────────
section("the clock knows how many turns this player has left")

h4, r4 = fish.table_clock(FakeGS(4, FRESH_DECK[4]))
check(abs(r4 - 1.0) < 1e-9, "a 4P table reads as exactly one rival's worth, so it is uncorrected",
      f"rivals={r4}")

rivals = [fish.table_clock(FakeGS(n, FRESH_DECK[n]))[1] for n in (2, 3, 4, 5, 6)]
check(rivals == sorted(rivals) and rivals[0] < rivals[-1],
      "rivals rises with the table size", f"{rivals}")

fresh = [fish.table_clock(FakeGS(n, FRESH_DECK[n]))[0] for n in (2, 3, 4, 5, 6)]
check(fresh == sorted(fresh, reverse=True), "a fuller table has less of a game in front of it",
      f"{[round(x, 2) for x in fresh]}")
check(fresh[0] >= 0.999, "a 2P game opens with all the time in the world", f"{fresh[0]}")
check(fresh[-1] < 0.75, "a 6P game is already short of time on its first move", f"{fresh[-1]}")

halves = [fish.table_clock(FakeGS(n, FRESH_DECK[n] // 2))[0] for n in (2, 3, 4, 5, 6)]
check(halves[0] > halves[-1] + 0.4, "halfway through the deck, 2P still has far more left than 6P",
      f"2P={halves[0]:.2f} 6P={halves[-1]:.2f}")

check(fish.table_clock(FakeGS(6, 0))[0] == 0.0, "an empty deck is no time at all")
check(fish.table_clock(FakeGS(0, 50))[0] >= 0.0, "no players does not divide by zero")

# ── 2. the features ─────────────────────────────────────────────────────────
section("the features carry the table size into the decision")


def game_feature_totals(count, seed=3):
    """Every candidate move of a whole game, summed."""
    random.seed(seed)
    db = fish.load_card_db()
    brain = fish.load_brain(fish.BRAIN_PATH)
    maps = fish._train_policy_maps_from_cbrain(fish.get_count_brain(brain, count))
    pol = fish._train_make_policy(maps, epsilon=0.0)
    tot = {k: 0.0 for k in NEW_WEIGHTS}
    seen = {"moves": 0, "varies": set()}

    def spy(gs, ms, p):
        rows = [fish.action_features(gs, ms, p, a)
                for a in fish.legal_actions(gs, ms, p, include_draw=True)]
        for k in NEW_WEIGHTS:
            vals = [r[k] for r in rows]
            for v in vals:
                tot[k] += abs(v)
            if len(set(round(v, 9) for v in vals)) > 1:
                seen["varies"].add(k)
        seen["moves"] += len(rows)
        return pol(gs, ms, p)

    fish.run_match(card_db=db, player_names=[f"P{i}" for i in range(count)],
                   action_policies=[spy] + [pol] * (count - 1), seed=seed, max_turns=500,
                   human_index=None, verbose=False, verbose_state=False,
                   ai_difficulties=[fish.DEFAULT_BOT_GRADE] * count,
                   online_weights=None, online_state=None, online_state_path=None)
    moves = max(1, seen["moves"])
    return {k: tot[k] / moves for k in NEW_WEIGHTS}, seen["varies"]

small, varies_small = game_feature_totals(2)
big, varies_big = game_feature_totals(6)

for k in NEW_WEIGHTS:
    check(k in varies_small or k in varies_big,
          f"{k} differs between the moves on offer, so it can change which one is chosen")
for k in ("crowd_cost", "pool_greed"):
    check(big[k] > small[k], f"{k} weighs more at a six-player table than a two-player one",
          f"2P={small[k]:.3f} 6P={big[k]:.3f}")
check(big["future_urgency"] > small["future_urgency"],
      "a card that only pays later is discounted harder at 6P",
      f"2P={small['future_urgency']:.3f} 6P={big['future_urgency']:.3f}")

check(all(k in fish.default_weights() for k in NEW_WEIGHTS),
      "every count-aware feature has a weight the trainer can find")

# ── 3. switched off, nothing moves ──────────────────────────────────────────
section("at zero they change nothing at all")

check(all(fish.default_weights()[k] == 0.0 for k in NEW_WEIGHTS),
      "the four weights ship at zero")
# Against the literals the module SHIPS with, not the live values: a tuning
# file legitimately changes those at import, and "ships at zero" is a claim
# about the source. Asserting it against rp.PARAMS made this fail the moment
# training first crowned a planner champion, which is the test being wrong
# about its own premise rather than the planner being wrong.
_src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "reef_planner.py"), encoding="utf-8").read()
_block = _src[_src.index("PARAMS: Dict[str, float] = {"):_src.index("# knob -> the per-rival term")]
SHIPPED = {m.group(1): float(m.group(2))
           for m in re.finditer(r'"([a-z_]+)":\s*(-?[\d.]+)', _block)}

check(all(SHIPPED.get(k, 0.0) == 0.0 for k in PER_RIVAL),
      "the six planner per-rival knobs ship at zero",
      f"{ {k: SHIPPED.get(k) for k in PER_RIVAL if SHIPPED.get(k, 0.0) != 0.0} }")

old_champ = {k: 0.5 for k in ("bias", "is_ocean", "uses_star")}
filled = fish.stabilize_weights(dict(old_champ))
check(all(filled.get(k) == 0.0 for k in NEW_WEIGHTS),
      "a champion trained before these existed gains them at zero, not at a guess")

p = dict(rp.PARAMS)
check(rp.params_for_table(p, 4) is p, "a 4P table gets the planner's params untouched")
_pristine = dict(rp.PARAMS)
_pristine.update({k: 0.0 for k in PER_RIVAL})
check(rp.params_for_table(_pristine, 6) is _pristine,
      "so does every other size while the per-rival knobs are zero")

# ── 4. the planner bends with the table ─────────────────────────────────────
section("the planner's knobs bend with the table, within their range")

tuned = dict(rp.PARAMS)
tuned["denial_per_rival"] = 0.6
by_count = {n: rp.params_for_table(tuned, n)["denial"] for n in (2, 3, 4, 5, 6)}
check(by_count[4] == float(rp.PARAMS["denial"]), "4P is still the untouched value", f"{by_count}")
check(by_count[6] > by_count[5] > by_count[4],
      "feeding the Pool costs more the more players there are to pick over it", f"{by_count}")

lo, hi = rp.TUNABLE_BOUNDS["denial"]
wild = dict(rp.PARAMS)
wild["denial_per_rival"] = 99.0
check(rp.params_for_table(wild, 6)["denial"] <= hi,
      "a runaway knob is clamped to the range, not applied", f"{rp.params_for_table(wild, 6)['denial']}")
wild["denial_per_rival"] = -99.0
check(rp.params_for_table(wild, 6)["denial"] >= lo, "and clamped at the bottom too")

check(set(PER_RIVAL) <= set(rp.TUNABLE_BOUNDS),
      "every per-rival knob is one a training run is allowed to move")
check(not any(k in rp.TUNABLE_BOUNDS for k in
              ("top_width", "worlds", "node_budget", "chain_beam", "confirm_worlds")),
      "the search widths are NOT tunable: buying strength with more search is not learning")
check(bot_evolve.PLANNER_BOUNDS == dict(rp.TUNABLE_BOUNDS),
      "the tuner and the planner agree on what every knob may be")

# COUNT_SHAPED clamps the knob AFTER the table-size correction; TUNABLE_BOUNDS
# clamps the value stored in the file. They are two different moments, so they
# are two tables -- and if they ever disagreed, a knob could be tuned to a value
# the correction then refuses to reach.
for _knob, (_per, _lo, _hi) in rp.COUNT_SHAPED.items():
    check((_lo, _hi) == rp.TUNABLE_BOUNDS[_knob],
          f"{_knob} is held to the same range before and after the table-size correction",
          f"COUNT_SHAPED={(_lo, _hi)} TUNABLE_BOUNDS={rp.TUNABLE_BOUNDS[_knob]}")
    check(_per in rp.TUNABLE_BOUNDS, f"{_per} is a knob a training run may move")

# ── 5. a damaged tuning file changes nothing ────────────────────────────────
section("a damaged tuning file cannot make the bots worse")


def tuned_into(payload):
    """Run load_tuned_params over `payload` and report what it accepted."""
    target = dict(rp.PARAMS)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        if payload is not None:
            fh.write(payload)
        path = fh.name
    try:
        changed = rp.load_tuned_params(path, into=target)
    finally:
        os.unlink(path)
    return changed, target

check(rp.load_tuned_params(os.path.join(tempfile.gettempdir(), "no_such_planner_file.json"),
                           into=dict(rp.PARAMS)) == [],
      "a missing file changes nothing")
check(tuned_into("{not json at all")[0] == [], "a half-written file changes nothing")
check(tuned_into('{"weights": "nonsense"}')[0] == [], "weights that are not a table change nothing")
check(tuned_into('{"weights": {"top_width": 999}}')[0] == [],
      "a knob the tuner may not move is ignored even when the file asks for it")
check(tuned_into('{"weights": {"denial": "lots"}}')[0] == [],
      "a knob that is not a number is ignored")

changed, target = tuned_into('{"weights": {"denial": 9999.0}}')
check(changed == ["denial"] and target["denial"] == rp.TUNABLE_BOUNDS["denial"][1],
      "a knob out of range is clamped in, not taken at its word", f"{target['denial']}")

changed, target = tuned_into('{"weights": {"denial_per_rival": 0.4}}')
check(changed == ["denial_per_rival"] and abs(target["denial_per_rival"] - 0.4) < 1e-9,
      "a knob in range is taken")

section("a tuned file reaches a fresh planner, and only with what it may carry")

# The path the whole tuning run exists for: bot_evolve writes the file,
# reef_planner reads it at IMPORT, and every process that plays a bot from
# grade A up imports reef_planner. Tested in a real subprocess, because import
# happens once and this module has already done it.
_probe = os.path.join(tempfile.gettempdir(), "cc_tuned_probe.json")
with open(_probe, "w", encoding="utf-8") as _fh:
    json.dump({"weights": {"denial_per_rival": 0.75, "denial": 0.2, "top_width": 999}}, _fh)
import subprocess
_env = dict(os.environ, FISH_PLANNER_TUNED=_probe, PYTHONHASHSEED="0")
_code = ("import reef_planner as rp, json;"
         "print(json.dumps({'applied': sorted(rp.TUNED_PARAMS_APPLIED),"
         "'denial': rp.PARAMS['denial'], 'per_rival': rp.PARAMS['denial_per_rival'],"
         "'top_width': rp.PARAMS['top_width'],"
         "'at2': rp.params_for_table(rp.PARAMS, 2)['denial'],"
         "'at6': rp.params_for_table(rp.PARAMS, 6)['denial']}))")
_out = subprocess.run([sys.executable, "-c", _code], cwd=os.path.dirname(os.path.abspath(__file__)),
                      env=_env, capture_output=True, text=True)
os.unlink(_probe)
try:
    _got = json.loads(_out.stdout.strip().splitlines()[-1])
except Exception:
    _got = {}
    check(False, "a fresh planner process reads the tuned file", _out.stderr[-300:])
if _got:
    check(_got["applied"] == ["denial", "denial_per_rival"],
          "a fresh planner picks up exactly the knobs the file names", f"{_got['applied']}")
    check(_got["top_width"] == 12,
          "and refuses the search width, however loudly the file asks for it",
          f"top_width={_got['top_width']}")
    check(_got["at2"] < _got["denial"] < _got["at6"],
          "the tuned knob then bends the right way with the table size",
          f"2P={_got['at2']} 4P={_got['denial']} 6P={_got['at6']}")

# ── who else is at the table ────────────────────────────────────────────────
section("the planner sees every opponent's plan, not just other planners'")


class FakeP:
    def __init__(self, **flags):
        self.flags = dict(flags)


def crowd(*players, me=0):
    gs = FakeGS(len(players), 60)
    gs.players = list(players)
    return rp.crowd_by_family(gs, players[me])


me = FakeP(_strategy_family="coral", _planner="reef")
planner_rival = FakeP(_strategy_family="coral", _planner="reef")
graded_rival = FakeP(_strategy_family="coral")          # a bot below grade A
other_plan = FakeP(_strategy_family="mammals")
person = FakeP()                                        # never assigned a family

check(crowd(me, planner_rival) == {"coral": 1}, "another planner on my plan is counted")
check(crowd(me, graded_rival) == {"coral": 1},
      "a lower-graded bot on my plan is counted too — it eats the same cards",
      f"{crowd(me, graded_rival)}")
check(crowd(me, graded_rival, planner_rival) == {"coral": 2},
      "two opponents on my plan count as two")
check(crowd(me, other_plan) == {"mammals": 1}, "an opponent on another plan is counted under that plan")
check(crowd(me, person) == {}, "a person is never counted: nothing here may see their plan")
check(crowd(me) == {}, "with nobody else at the table, nothing is crowded")
check("coral" not in crowd(me, other_plan), "I am not crowding myself")

# ── the trainer's own rules ─────────────────────────────────────────────────
section("the trainer trains each strategy where it is actually played")

check(bot_evolve.counts_for_strategy("invertebrates", [2, 3, 4, 5, 6]) == [5, 6],
      "Invertebrates is trained only at the tables it is offered at",
      f"{bot_evolve.counts_for_strategy('invertebrates', [2, 3, 4, 5, 6])}")
check(bot_evolve.counts_for_strategy("coral", [2, 3, 4, 5, 6]) == [2, 3, 4, 5, 6],
      "every other strategy is trained at all of them")
check(bot_evolve.counts_for_strategy("invertebrates", [2, 3]) == [5, 6],
      "asking for Invertebrates at a table it is never offered at gives its own sizes back")

scale = bot_evolve._MARGIN_SCALE
check(scale[4] == 1.0, "a 4P margin is reported as itself")
check(scale[2] < 1.0 < scale[6],
      "a 2P margin is scaled down and a 6P margin up, so no size can outvote the others",
      f"2P={scale[2]:.2f} 6P={scale[6]:.2f}")
_sizes = sorted(scale)
check([scale[c] for c in _sizes] == sorted(scale[c] for c in _sizes),
      "the scale rises with every seat added, with no kinks from a noisy sample",
      f"{ {c: scale[c] for c in _sizes} }")
# It must not be read back off the stale offline-training table it used to
# divide by, or the correction quietly goes wrong again when that table drifts.
_stale = {c: fish.TRAIN_TARGET_TOP[4] / fish.TRAIN_TARGET_TOP[c] for c in (2, 3, 4, 5, 6)}
check(abs(scale[2] - _stale[2]) > 0.1,
      "the scale comes from measured games, not from TRAIN_TARGET_TOP",
      f"measured {scale[2]} vs that table's {_stale[2]:.3f}")

# ── how the overnight run spends itself ─────────────────────────────────────
section("the rotation spends the night where it reaches a real game")

import bot_training_rotation as rot

_order = rot.interleave_planner(rot.MAINS + rot.COMBOS)
_combos = {"birds_crustaceans", "coral_cephalopods", "birds_coral"}

# A combo is trained only because a bot can now commit to one. The rule the
# rotation has to keep is the general one: train exactly the plans that can be
# chosen, and nothing else -- a plan no bot can pick is a set of weights nothing
# ever looks up.
check(set(rot.MAINS + rot.COMBOS) == set(rp.STRATEGY_FAMILIES),
      "the rotation trains exactly the plans a bot can commit to",
      f"difference: {sorted(set(rot.MAINS + rot.COMBOS) ^ set(rp.STRATEGY_FAMILIES))}")
check(_combos <= set(_order), "each combo gets a cell of its own",
      f"missing {sorted(_combos - set(_order))}")
check(_combos <= set(rp.STRATEGY_FAMILIES),
      "the planner may choose a combo")
check(_combos <= fish.strategies_allowed_for_skill("advanced", 6),
      "so may an advanced bot from its opening hand")
check(not (_combos & fish.strategies_allowed_for_skill("intermediate", 6)),
      "but not a weaker one: a two-part plan needs the cards for both halves")

# Every combo's cards are its parents' cards, or committing to one means
# collecting a card that does not exist.
for _c, _parents in rp.COMBO_PARENTS.items():
    check(rp.family_accepts(_c) == _parents,
          f"{_c} counts its parents' cards as its own", f"{rp.family_accepts(_c)}")
    check(all(p in rot.MAINS for p in _parents),
          f"{_c} is built on plans that are themselves trained")
for _m in rot.MAINS:
    check(rp.family_accepts(_m) == (_m,), f"{_m} counts only its own cards")

_last_main = max(_order.index(m) for m in rot.MAINS)
check(all(_order.index(c) > _last_main for c in rot.COMBOS),
      "every combo is trained after every single plan, because its champion is "
      "seeded from its parents'",
      f"mains end at {_last_main}, combos at {[ _order.index(c) for c in rot.COMBOS ]}")

check("planner" in _order, "the planner gets turns — it is what A to S++ play with")
check("planner_top" in _order, "…and some of those turns are taken at S++ itself")
check(rot.planner_grade_for("planner_top") == "charles_darwin",
      "S++ is Charles Darwin", rot.planner_grade_for("planner_top"))
check(rp.params_for_grade("charles_darwin") is not None,
      "…and S++ really is a planner grade, so tuning the knobs reaches it")

# The evidence ladder: barren visits raise the bar, a crowning lowers it by one
# step rather than dropping to the bottom.
def _walk(outcomes):
    tier = 0
    for o in outcomes:
        tier = max(0, tier - 1) if o else min(tier + 1, rot.SETTLED_TIER)
    return tier

check(_walk([False]) == 1, "a barren visit raises the bar")
check(_walk([False, False, False]) == rot.SETTLED_TIER,
      "three barren visits in a row is what settled means")
check(_walk([False, True]) == 0, "a crowning steps the bar down")
check(_walk([False, False, True]) == 1,
      "…by one step, not back to the bottom: how much evidence a cell needs is "
      "a property of the cell, not of whether it just improved")
check(_walk([True, True, True]) == 0, "the bar never goes below the cheapest tier")
check(len(rot.WEIGHT_TIERS) == rot.SETTLED_TIER,
      "every tier below settled is a real budget")

print(f"\n{'=' * 50}\nRESULT: {PASS} passed, {FAIL} failed")
raise SystemExit(1 if FAIL else 0)
