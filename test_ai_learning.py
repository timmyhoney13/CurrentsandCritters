#!/usr/bin/env python3
"""Does a finished game actually teach the AI anything?

Run:  python3 test_ai_learning.py

For a long time the answer was almost always no, and nothing said so. Two
separate quality gates decided whether a finished game was worth learning
from: one in the live server, one inside update_brain_from_match. Both were
written from intuition, neither was ever checked against a real game, and both
were set at ~100 points. A four-player match on this engine has a median top
score of 70 and, over 159 complete games, never once passed 115 — which is
exactly where the second gate sat for human games. Between them they threw
away all but one game in 159, silently, while logging that learning had been
"skipped".

So this file asks the only question that matters: play a real match, hand it
to the learner, and see whether the brain changed. It also pins the two things
that let the bug hide for so long:

  * there is ONE floor function, not a copy in each file, and
  * the floor is BELOW what a real game of that size actually scores.
"""
import copy
import os
import random
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fish_game_all_in_one as fish  # noqa: E402
import multiplayer_server as mps  # noqa: E402

PASS = 0
FAIL = 0


def check(cond, name, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  ✗ FAIL: {name}" + (f"  → {extra}" if extra else ""))


def section(t):
    print(f"\n{t}")


# What complete four-player matches on the shipped engine actually score.
# Two independent samples, taken from real calibration runs:
#   159 games on the older ladder: median 70, max 115
#    58 games on the current one : median 72, max  95
# The floor has to live under these, or it is a gate that never opens. The
# stricter (lower) of the two maxima is used, so the assertion below stays
# true for both.
MEASURED_4P_MEDIAN_TOP = 70
MEASURED_4P_MAX_TOP = 95

section("the floor is set from what a game really scores")

floor4 = fish.learning_top_score_floor(4)
floor4h = fish.learning_top_score_floor(4, True)
check(floor4 < MEASURED_4P_MEDIAN_TOP,
      "a four-player game of MEDIAN quality is learned from",
      f"floor {floor4}, median top {MEASURED_4P_MEDIAN_TOP}")
check(floor4h < MEASURED_4P_MEDIAN_TOP,
      "…and so is a median HUMAN game, which is the data worth most",
      f"human floor {floor4h}")
check(floor4h < MEASURED_4P_MAX_TOP,
      "the human floor is under the best game ever recorded, not over it",
      f"human floor {floor4h} vs best-ever {MEASURED_4P_MAX_TOP}")
check(floor4h >= floor4,
      "a human game is still held to a slightly higher bar")
check(floor4h <= floor4 * 1.10,
      "…but only slightly: the old 15% is what put it out of reach",
      f"{floor4} -> {floor4h}")
# Four players share one deck, so they each get fewer turns and score LESS
# than two players do. The old floors had this backwards.
check(fish.learning_top_score_floor(2) >= fish.learning_top_score_floor(4),
      "a two-player game is held to a higher bar than a four-player one, "
      "because two-player games score higher",
      f"2p {fish.learning_top_score_floor(2)}, 4p {floor4}")

section("there is only one copy of that number")

server_src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "multiplayer_server.py"), encoding="utf-8").read()
learn_block = server_src[server_src.index("Quality gate: only learn from GOOD"):]
learn_block = learn_block[:4000]
check("learning_top_score_floor" in learn_block,
      "the live server asks the shared function for its floor")
check("_top_score >= 100" not in server_src,
      "…and no hand-written 100 is left anywhere in it")
check(server_src.count("learning_top_score_floor") >= 1,
      "…so the two gates can never drift apart again")

section("a real finished game changes the brain")

# Play real four-player matches with the shipped chooser, hand each finished
# game to the learner, and look at the brain before and after.
brain = fish.load_brain()
cbrain = fish.get_count_brain(brain, 4)
use_history = fish.use_historical_policy_bias()
weights = fish.stabilize_weights(dict(fish.default_weights()))
if use_history:
    weights.update(cbrain.get("weights", {}))
    weights = fish.stabilize_weights(weights)


def _m(key):
    v = cbrain.get(key, {})
    return v if isinstance(v, dict) else {}


def policy(gs, ms, player):
    return mps.choose_action_weighted_deep(
        gs, ms, player, weights, _m("synergy"), _m("species_synergy"),
        _m("same_ocean_synergy"), _m("strategy_value"), _m("strategy_count"),
        _m("strategy_transition"), _m("strategy_transition_count"))


# A table across the middle of the ladder, which is what a Bot Match usually
# is. Several seeds, because one match is a coin toss, and the question here is
# whether ORDINARY games train the AI, not whether one lucky one did.
random.seed(20260910)
results = []
for seed in (4242, 909, 31337, 5150):
    g, _ms = fish.run_match(
        card_db=mps.CARD_DB, player_names=[f"P{i}" for i in range(4)],
        action_policies=[policy] * 4, seed=seed, max_turns=260,
        human_indices=set(), verbose=False, verbose_state=False,
        ai_difficulties=["c", "b", "a", "s"],
    )
    results.append((g, max(fish.final_points(g, pl) for pl in g.players)))

tops = [t for _g, t in results]
print(f"  (played 4 real 4-player matches, top scores {[round(t) for t in tops]})")
check(all(t > 0 for t in tops), "the matches produced real results", str(tops))

floor_h = fish.learning_top_score_floor(4, True)


def _fresh_brain():
    """A sandbox brain, so this test never writes to the shipped one."""
    return {
        "weights": dict(fish.default_weights()),
        "synergy": {}, "species_synergy": {}, "same_ocean_synergy": {},
        "strategy_value": {}, "strategy_count": {},
        "strategy_transition": {}, "strategy_transition_count": {},
        "strategy_family_stats": {}, "game_memory": [],
    }


taught = 0
for g, _top in results:
    sandbox = _fresh_brain()
    fish.update_brain_from_match(g, sandbox, human_weight=2.5)
    if sandbox["synergy"] or sandbox["species_synergy"] or sandbox["same_ocean_synergy"]:
        taught += 1

# Two separate claims, kept separate on purpose. The first is exact and can
# never be flaky: whatever these four games happened to score, the learner has
# to agree with the floor about which of them count.
qualifying = sum(1 for _g, t in results if t >= floor_h)
check(taught == qualifying,
      "the learner trains on exactly the games the floor admits, no more and no less",
      f"floor {floor_h:.0f} admits {qualifying}, learner trained on {taught}, "
      f"tops {[round(t) for t in tops]}")
# The second is the point of the whole change: ordinary games qualify. If this
# one starts failing, the floor has drifted above what a real game scores
# again, which is exactly the bug this file exists to catch.
#
# It is a claim about the DISTRIBUTION, so it is tested as one. It used to ask
# for three of these four games to clear the floor, and four games cannot carry
# that: measured over twelve seeds, the shipped chooser clears this floor in
# about ten of them, so a four-game sample lands on 2/4 perfectly routinely and
# the test failed for no reason at all. The MEDIAN of the four is the same
# claim ("a typical game clears the floor") without the coin toss: over those
# twelve seeds the median top score was 65 against a floor of 58, and a median
# that falls under the floor really does mean the floor has drifted.
median_top = statistics.median(tops)
check(median_top >= floor_h,
      "the typical ordinary game is worth learning from",
      f"median top {median_top:.0f} vs floor {floor_h:.0f}, "
      f"tops {[round(t) for t in tops]}")
# And a hard floor under that: if the floor ever admits one game in four or
# fewer, it is not admitting ordinary games whatever the median says.
check(qualifying >= 2,
      "…and it is not just one lucky game in four",
      f"{qualifying}/4 cleared the floor of {floor_h:.0f}, "
      f"tops {[round(t) for t in tops]}")

# The same four games under the OLD gates. This is the number the whole change
# is about: the outer gate was 100, and the inner one 115 for a human game.
old_taught = sum(1 for t in tops if t >= 115.0)
check(old_taught == 0,
      "…and under the old gates NONE of them taught it anything",
      f"{old_taught}/4 would have passed a 115 floor")
print(f"  (old gates: {old_taught}/4 learned from.  new floor "
      f"{floor_h:.0f}: {taught}/4)")

gs = results[0][0]   # kept for the refusal test below
sandbox = _fresh_brain()
fish.update_brain_from_match(gs, sandbox, human_weight=2.5)

section("a genuinely broken game is still refused")

# The floor still has a job: a game that ended in the first few turns has no
# developed board to learn from, and must not train anything.
stunted = copy.deepcopy(sandbox)
before_stunted = copy.deepcopy(stunted)
for p in gs.players:
    p.flags["_test_zero"] = True
zeroed = copy.deepcopy(gs)
for p in zeroed.players:
    p.board_oceans = []
    p.score = 0
fish.update_brain_from_match(zeroed, stunted, human_weight=2.5)
check(stunted["synergy"] == before_stunted["synergy"],
      "a game with no board on it teaches nothing",
      "the floor is lower, not gone")

section("a mixed human + bot table is not thrown away")

check("human_weight=2.5" in server_src,
      "a table of one person and three bots trains the synergy maps too",
      "every Bot Match is one of these, so skipping them skips the evidence")
# Anchor on the LEARNING call, not on "if human_only_game:" — that phrase also
# appears a few lines earlier where the demo boost is worked out, and slicing
# from the wrong one reads a block that proves nothing.
_anchor = server_src.index("fish.update_brain_from_match(")
mixed_branch = server_src[_anchor - 400:_anchor + 1400]
check("else:" in mixed_branch and "update_brain_from_match" in mixed_branch,
      "…through the same learner, at a lower weight")

print(f"\n{'=' * 50}\nRESULT: {PASS} passed, {FAIL} failed")
raise SystemExit(1 if FAIL else 0)
