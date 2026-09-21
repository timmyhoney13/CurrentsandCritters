#!/usr/bin/env python3
"""Tournament selection for the Casual bot weights: keep only what wins.

The self-play trainer learns by regression -- it nudges the weights toward
predicting a return. That can move backwards, because the thing it optimises
(predicting a return) is not the thing we measure (winning games).

This optimises the measured thing directly, and can only move forwards:

    champion = the weights now live for this table size
    repeat:
        make N mutants of the champion
        sit each ONE mutant against champions and play
        the champion is replaced ONLY by a mutant that beat it with confidence

A mutant that fails to beat the champion is thrown away, so the champion's
strength is monotone by construction. That is the whole point of it.

Fairness, because a rigged measurement is worse than none:
  * one mutant per game against (count - 1) champions, so an evenly matched
    mutant wins 1/count of its games -- that, not 0.5, is the bar it must clear
  * every candidate in a generation plays THE SAME seeds (common random
    numbers), so they are compared on the same deals rather than on luck
  * the candidate's seat rotates, so a seat advantage cannot be mistaken for a
    better policy
  * Wilson lower bound, so a lucky run of games cannot promote a worse bot
  * a cheap screening round first, then STAGED confirmation for the survivors:
    a challenger that still might clear the bar keeps playing, one that
    provably cannot is dropped. A fixed number of games rejects real
    improvements for want of evidence -- one measured at 0.297 against a 0.250
    bar was thrown away on a lower bound of 0.248.

Nothing here touches the deal, the shuffle, or hidden information: every game
goes through the same fish.run_match the real Casual game uses.

    python3 bot_evolve.py --count 4 --generations 20
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import multiprocessing as mp
import os
import random
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

# Pin string hashing for every worker this process spawns. The engine's
# decisions depend on the order it walks sets of card names and tags, and Python
# randomises that order per process, so one deal played in two workers could
# come out as two different games: measured, the same seed and seat with
# identical weights diverged in 4 of 6 deals once the hash seed changed. Every
# "paired" comparison was therefore mostly comparing different games, and the
# luck it was meant to cancel was still in there. Spawned workers read this at
# interpreter start, so it has to be set before the first Pool exists.
os.environ["PYTHONHASHSEED"] = "0"
# Rollout counts, not the clock, decide how far a live-chooser bot looks. See
# multiplayer_server._PLAN_BY_COUNT: without it a strong grade measures weaker
# on a busy machine, and a paired comparison stops being the same game.
os.environ["FISH_PLAN_BY_COUNT"] = "1"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fish_game_all_in_one as fish
import reef_planner as _reef

_W_CARD_DB = None
# One set of learned maps PER TABLE SIZE. Each count keeps its own brain
# (fish.get_count_brain), and a deal is now played at whichever size the deal
# says, so the worker holds them all and looks up the one it needs.
_W_MAPS_BY_COUNT: Optional[Dict[int, Dict[str, Any]]] = None
_W_MAPS: Optional[Dict[str, Any]] = None        # the seed count's maps
_W_CHAMPION: Optional[Dict[str, float]] = None
_W_CANDIDATES: Optional[List[Dict[str, float]]] = None
_W_COUNT: int = 4
# Strategy mode: the label being trained, and the champion vector for EVERY
# strategy. The seat under test is forced onto the target strategy so a
# thousand games of Coral can be played on purpose; the other seats play their
# own plans with their own champion weights, because a strategy has to be good
# against the field, not against copies of itself.
_W_STRATEGY: Optional[str] = None
_W_STRAT_MAP: Optional[Dict[str, Dict[str, float]]] = None
# Planner mode: the grade whose Reef Planner settings are being tuned. When it
# is set, a "champion" is a dict of planner knobs rather than a weight vector,
# and every seat plays the planner instead of the weighted chooser.
_W_PLANNER_GRADE: str = ""


# Which decision-maker plays, and at what grade. "engine" is the light one-pass
# chooser in fish_game_all_in_one; "live" is the deep chooser the real game uses,
# the only one that reads the grade ladder's search knobs. Weights tuned on the
# engine chooser were tuned for a bot nobody plays against, so the ranks train
# on "live". Passed by environment because spawned workers inherit it.
_W_CHOOSER = os.environ.get("FISH_TRAIN_CHOOSER", "engine")
_W_GRADE = os.environ.get("FISH_TRAIN_GRADE", "")
_MPS = None


def _init(maps_by_count, champion, candidates, count, strategy=None, strat_map=None,
          planner_grade=""):
    global _W_CARD_DB, _W_MAPS, _W_MAPS_BY_COUNT, _W_CHAMPION, _W_CANDIDATES, _W_COUNT
    global _W_STRATEGY, _W_STRAT_MAP, _W_CHOOSER, _W_GRADE, _MPS, _W_PLANNER_GRADE
    _W_CARD_DB = fish.load_card_db()
    _W_MAPS_BY_COUNT = maps_by_count
    _W_MAPS = maps_by_count[count]
    _W_CHAMPION, _W_CANDIDATES, _W_COUNT = champion, candidates, count
    _W_STRATEGY, _W_STRAT_MAP = strategy, strat_map
    _W_PLANNER_GRADE = planner_grade or ""
    _W_CHOOSER = os.environ.get("FISH_TRAIN_CHOOSER", "engine")
    _W_GRADE = os.environ.get("FISH_TRAIN_GRADE", "") or fish.DEFAULT_BOT_GRADE
    if _W_CHOOSER == "live" or _W_PLANNER_GRADE:
        import multiplayer_server as mps
        _MPS = mps


def _strategy_policy(strategy_weights: Dict[str, Dict[str, float]], count: int):
    """A policy that scores with the weights of whatever strategy the bot is
    committed to, read at decision time, on the configured chooser.

    The maps are this table size's own (each count learns into its own brain);
    the strategy's weight vector is shared across sizes, because what the table
    size changes is carried by the count-aware features themselves, not by a
    vector per size. See fish_game_all_in_one.table_clock."""
    maps = _W_MAPS_BY_COUNT[count]
    if _W_CHOOSER != "live":
        return fish._train_make_strategy_policy(maps, strategy_weights, epsilon=0.0)
    m, fb, mps = maps, maps["weights"], _MPS

    def _pol(gs, ms, p):
        lab = str(p.flags.get("_strategy_family", "")).strip().lower()
        return mps.choose_action_weighted_deep(
            gs, ms, p, strategy_weights.get(lab) or fb,
            m.get("synergy", {}), m.get("species_synergy", {}), m.get("same_ocean_synergy", {}),
            m.get("strategy_value", {}), m.get("strategy_count", {}),
            m.get("strategy_transition", {}), m.get("strategy_transition_count", {}))
    return _pol


def _policies_for(cand: Optional[Dict[str, float]], seat: int, count: int):
    """Build one policy per seat, and say which strategy each seat is forced to."""
    if _W_PLANNER_GRADE:
        # Every seat is the planner. The seat under test plays the candidate
        # knobs, the rest play the champion's. No strategy is forced: which plan
        # to commit to is one of the things the planner decides for itself, and
        # at a six-player table it is most of what it decides.
        champ_pol = _planner_policy(_W_CHAMPION or {}, _W_PLANNER_GRADE)
        seat_pol = _planner_policy(cand, _W_PLANNER_GRADE) if cand is not None else champ_pol
        return [seat_pol if i == seat else champ_pol for i in range(count)], None
    if _W_STRATEGY is None:
        maps = _W_MAPS_BY_COUNT[count]
        pol = [fish._train_make_policy(
                   maps, weights=dict(cand if (cand is not None and i == seat) else _W_CHAMPION),
                   epsilon=0.0)
               for i in range(count)]
        return pol, None
    base = dict(_W_STRAT_MAP or {})
    seat_map = dict(base)
    if cand is not None:
        seat_map[_W_STRATEGY] = cand
    champ_pol = _strategy_policy(base, count)
    seat_pol = _strategy_policy(seat_map, count)
    pol = [seat_pol if i == seat else champ_pol for i in range(count)]
    forced = [_W_STRATEGY if i == seat else None for i in range(count)]
    return pol, forced


# A two-player game finishes around 229 points and a six-player game around 50,
# off the same deck, so the same improvement is worth four and a half times as
# many raw points at 2P as at 6P. Averaging raw margins across table sizes would
# let the 2P deals decide every generation and leave the crowded tables -- where
# the bots are weakest and the margins are smallest -- effectively unmeasured.
# Every margin is reported in 4P-equivalent points instead, so a deal counts the
# same wherever it was played and the numbers stay in the units the logs have
# always used.
#
# MEASURED, over 12 complete games at each size. Median top score: 229 at 2P,
# 136 at 3P, 88 at 4P, 66 at 5P, 50 at 6P. Median winning margin, which is the
# quantity actually being scaled: 38, 14, 11, 8, 6 -- the same shape, and the
# two agree on every size within a few percent except 3P.
#
# These are not taken from fish.TRAIN_TARGET_TOP, which this used to divide by.
# That table is the offline --train pipeline's idea of an "excellent" score and
# it has gone stale: it calls 135 a good four-player game when twelve of them
# ran 76 to 99. Reading it here made a 2P deal count about one and a half times
# what it should and a 6P deal about seven tenths.
#
# Scaled by the median TOP rather than by the spread of the margins, which would
# be the textbook choice: at twelve games a size the spread estimates are pure
# noise -- they came out claiming a 3P deal should count for a quarter of a 4P
# one and a 5P deal for half, which no property of the game supports.
_MARGIN_SCALE: Dict[int, float] = {2: 0.384, 3: 0.647, 4: 1.0, 5: 1.344, 6: 1.778,
                                   7: 2.1, 8: 2.4}


def _play(task: Tuple[int, int, int, int]) -> Tuple[int, float, float]:
    """One game. `ci` >= 0 puts that candidate in `seat` against champions;
    ci == -1 plays the all-champion version of the same deal, which is the
    baseline every candidate on this deal is measured against."""
    ci, seed, seat, count = task
    random.seed(seed)
    cand = _W_CANDIDATES[ci] if ci >= 0 else None
    policies, forced = _policies_for(cand, seat, count)
    try:
        gs, _ms = fish.run_match(
            card_db=_W_CARD_DB,
            player_names=[f"P{i}" for i in range(count)],
            action_policies=policies,
            seed=seed, max_turns=500, human_index=None,
            verbose=False, verbose_state=False,
            ai_difficulties=[_W_GRADE or fish.DEFAULT_BOT_GRADE] * count,
            online_weights=None, online_state=None, online_state_path=None,
            force_strategies=forced,
        )
        finals = [float(fish.final_points(gs, p)) for p in gs.players]
    except Exception:
        return ci, 0.0, 0.0
    mine = finals[seat]
    top = max(finals)
    if mine >= top - 1e-9 and finals.count(top) == 1:
        win = 1.0
    elif abs(mine - top) < 1e-9:
        win = 0.5
    else:
        win = 0.0
    others = [s for j, s in enumerate(finals) if j != seat]
    margin = mine - (max(others) if others else 0.0)
    return ci, win, margin * _MARGIN_SCALE.get(count, 1.0)


def _wilson_low(wins: float, n: int) -> float:
    if n <= 0:
        return 0.0
    z = 1.96
    p = wins / n
    d = 1.0 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(max(0.0, p * (1 - p) / n + z * z / (4 * n * n)))
    return (c - m) / d


# The weights each strategy actually lives or dies by. A Coral bot is decided by
# where its coral lands and whether it respects the reef chart; a Cephalopod bot
# by whether it banks cards for the Reef Trigger Fish dump. Mutating at random
# across all 26 weights mostly tests things a given plan does not care about, so
# most mutants are null and the search crawls. Aim it: most mutations touch the
# plan's own weights, a few still roam so nothing is ruled out by assumption.
STRATEGY_FOCUS: Dict[str, Tuple[str, ...]] = {
    "coral":              ("placement_fit", "ocean_threshold", "stack_bonus",
                           "strategy_bonus", "plan_fit_bonus"),
    "cephalopods":        ("combo_timing", "stack_bonus", "future_value",
                           "strategy_bonus", "immediate_delta"),
    "yellowfin_tuna":     ("combo_timing", "placement_fit", "stack_bonus",
                           "strategy_bonus", "future_value"),
    "crustaceans":        ("placement_fit", "stack_bonus", "synergy_bonus",
                           "strategy_bonus", "species_bonus"),
    "king_salmon":        ("ocean_completion", "fills_empty_ocean", "target_occupancy",
                           "plan_fit_bonus", "combo_timing"),
    "baitfish_barrage":   ("combo_timing", "species_bonus", "stack_bonus",
                           "strategy_bonus", "synergy_bonus"),
    "mammals":            ("combo_timing", "species_bonus", "stack_bonus",
                           "strategy_bonus", "synergy_bonus"),
    "birds_of_a_feather": ("species_bonus", "stack_bonus", "synergy_bonus",
                           "strategy_bonus", "plan_fit_bonus"),
    "goby_moon_shot":     ("synergy_bonus", "plan_fit_bonus", "stack_bonus",
                           "future_value", "strategy_bonus"),
    "invertebrates":      ("species_bonus", "synergy_bonus", "fills_empty_ocean",
                           "stack_bonus", "combo_timing"),
}
_FOCUS_SHARE = 0.75

# A combo is two strategies played together, so it is trained AFTER both halves
# and starts from what they already learned rather than from nothing: its first
# champion is the average of its parents' champions. That is why the main
# strategies have to be solid first -- a combo seeded from two untrained halves
# inherits nothing worth having.
COMBO_PARENTS: Dict[str, Tuple[str, str]] = {
    "birds_crustaceans": ("birds_of_a_feather", "crustaceans"),   # B-Lob
    "coral_cephalopods": ("coral", "cephalopods"),                # Coral / Cephalopods
    "birds_coral":       ("birds_of_a_feather", "coral"),          # B-Coral
}
for _combo, (_a, _b) in COMBO_PARENTS.items():
    STRATEGY_FOCUS[_combo] = tuple(dict.fromkeys(
        STRATEGY_FOCUS.get(_a, ()) + STRATEGY_FOCUS.get(_b, ())))

# The four weights that say what the TABLE SIZE is worth are aimed at as well,
# for every strategy, because every strategy has to answer the question and none
# of them has ever been asked it.
#
# This is not a preference, it is what the numbers demanded. Left out of the
# focus lists they were reachable only through the quarter of mutations that
# roam, one key at a time out of forty-four: measured over 100 generations of 8
# mutants, a given one of them was tried in 13 generations out of 100. At the
# ~45 minutes a strategy's two generations take, that is one attempt every few
# hours, before it has to survive screening and confirmation as well. A
# dimension explored that slowly is not being explored.
#
# They belong to each strategy separately rather than to a shared vector,
# because the right answer really is different per plan: a Coral board is a long
# build that a six-player game never gives time for, and a Baitfish board is
# already fast and barely cares.
COUNT_FOCUS: Tuple[str, ...] = ("future_urgency", "tempo_urgency",
                                "crowd_cost", "pool_greed")
for _lab in list(STRATEGY_FOCUS):
    STRATEGY_FOCUS[_lab] = tuple(dict.fromkeys(STRATEGY_FOCUS[_lab] + COUNT_FOCUS))


# ── The Reef Planner's knobs, and what each is allowed to be ───────────────
# Only the knobs that say what a position is WORTH. The search widths
# (top_width, worlds, node_budget and the rest) are deliberately left out: they
# are what separates one planner grade from the next, and a "better" planner
# found by letting it look at twice as many moves has not learned anything, it
# has just been given more machine. Every grade from Eugenie Clark up shares
# the knobs below, so they are trained once, at the cheapest planner grade, and
# every grade above it plays with what that found.
# The knobs and their ranges are the planner's own (reef_planner.TUNABLE_BOUNDS),
# not a second copy here: a tuner that disagreed with the module it is tuning
# about what a knob may be would write files the planner then refuses to read.
PLANNER_BOUNDS: Dict[str, Tuple[float, float]] = dict(_reef.TUNABLE_BOUNDS)

# What a planner run aims mutations at.
#
# Chosen by measurement, not by taste. Each knob was moved half its legal range
# at fifteen real planner decisions, five each at 2P, 4P and 6P, and the move it
# then chose was compared with the move it chose before:
#
#   adaptive_turn_value 100%   rival_weight 80%   denial 53%   final_sweep 53%
#   loyalty 53%   plan_discount 53%   turn_value_per_rival 53%
#   plan_discount_per_rival 40%   turn_value 33%   rival_weight_per_rival 27%
#   denial_per_rival 20%   crowding 13%
#
# WHAT IS SWITCHED OFF IS WHERE THE GROUND IS. adaptive_turn_value,
# rival_weight, denial and final_sweep are finished, working machinery sitting
# at 0.0, and turning them on changes between half and all of the planner's
# moves. Nobody has ever measured whether those are better moves. That is a
# question a tournament can answer and an opinion cannot.
#
# The per-rival terms show the signature they were designed to have:
# turn_value_per_rival changes the move 100% of the time at 2P, 0% at 4P and 60%
# at 6P. Zero at 4P is correct -- that is the size the planner was tuned at, so
# the correction is the identity there by construction.
#
# Left to the quarter of mutations that roam: the knobs that changed nothing at
# any size (deck_rate_prior, draw_frac, survival), and the two that cannot do
# anything until the knob they depend on is on -- turn_value_floor needs
# adaptive_turn_value, denial_threshold needs denial. If training turns those on,
# their dependants become worth aiming at, and this list should be measured again.
PLANNER_FOCUS: Tuple[str, ...] = (
    # finished machinery that ships switched off
    "adaptive_turn_value", "rival_weight", "denial", "final_sweep",
    # what the table size is worth
    "turn_value_per_rival", "plan_discount_per_rival",
    "rival_weight_per_rival", "denial_per_rival",
    # what its own plan is worth, beyond the points on the cards
    "loyalty", "crowding", "switch_margin",
    # the price of a turn, and how far a plan is trusted
    "turn_value", "plan_discount",
)


def planner_defaults() -> Dict[str, float]:
    """The planner knobs as they stand now: the champion a run starts from."""
    return {k: float(_reef.PARAMS.get(k, 0.0)) for k in PLANNER_BOUNDS}


def _clamp_params(p: Dict[str, float]) -> Dict[str, float]:
    out = dict(p)
    for k, (lo, hi) in PLANNER_BOUNDS.items():
        v = float(out.get(k, 0.0))
        out[k] = lo if v < lo else (hi if v > hi else v)
    return out


def _mutate_params(p: Dict[str, float], rng: random.Random, sigma: float,
                   focus: Tuple[str, ...] = PLANNER_FOCUS) -> Dict[str, float]:
    """One to three knobs moved, on the same reasoning as _mutate: a mutant that
    moves eight of them at once is a shuffle, not a hypothesis."""
    out = dict(p)
    all_keys = list(PLANNER_BOUNDS)
    focus_keys = [k for k in focus if k in PLANNER_BOUNDS]
    chosen: List[str] = []
    for _ in range(rng.randint(1, 3)):
        pool = focus_keys if (focus_keys and rng.random() < _FOCUS_SHARE) else all_keys
        k = rng.choice(pool)
        if k not in chosen:
            chosen.append(k)
    for k in chosen:
        lo, hi = PLANNER_BOUNDS[k]
        # Step from the knob's own size, with a floor from its range, so a knob
        # sitting at exactly zero -- which every per-rival term does on day one
        # -- can still be moved off it.
        scale = abs(float(out.get(k, 0.0))) + 0.15 * (hi - lo)
        out[k] = float(out.get(k, 0.0)) + rng.gauss(0.0, sigma) * scale
    return _clamp_params(out)


def _planner_policy(knobs: Dict[str, float], grade: str):
    """A seat played by the Reef Planner at `grade`, with `knobs` in place of the
    planner's defaults. The grade's own search settings are untouched."""
    params = _reef.params_for_grade(grade) or dict(_reef.PARAMS)
    params.update({k: float(v) for k, v in knobs.items()})

    def _pol(gs, ms, p, _params=params):
        return _reef.choose_action(gs, ms, p, params=dict(_params))
    return _pol


# Table sizes a strategy may be trained at. Forcing a seat onto a strategy
# bypasses the live allowlist, so without this the trainer would happily spend
# half its games teaching Invertebrates to play 2P and 3P tables it is never
# offered at (fish.INVERTEBRATE_MIN_PLAYERS), and grade those lessons into the
# one vector it plays its real 5P and 6P games with.
STRATEGY_COUNTS: Dict[str, Tuple[int, ...]] = {
    "invertebrates": (5, 6),
}
DEFAULT_COUNTS: Tuple[int, ...] = (2, 3, 4, 5, 6)


def counts_for_strategy(strategy: Optional[str], counts: List[int]) -> List[int]:
    """`counts`, minus the table sizes this strategy is never played at."""
    allowed = STRATEGY_COUNTS.get(str(strategy or "").strip().lower())
    if not allowed:
        return list(counts)
    out = [c for c in counts if c in allowed]
    return out or list(allowed)


def _mutate(w: Dict[str, float], rng: random.Random, sigma: float,
            focus: Tuple[str, ...] = ()) -> Dict[str, float]:
    """Change one to three weights, not a third of them.

    A mutant that moves eight weights at once is not a hypothesis, it is a
    shuffle: whatever it gains on one it can lose on another, so the result
    lands near zero and says nothing about any of them. Two generations of
    Mammals screened at +0.11 and confirmed at +0.0008 doing exactly that.

    Narrow mutants also make the screening round honest. Picking the best of
    eight on 40 games is a maximum, not a measurement, and the wider each
    mutant is the more of that apparent edge is luck waiting to evaporate."""
    out = dict(w)
    all_keys = [k for k in out if k in fish.default_weights()]
    focus_keys = [k for k in focus if k in out]
    chosen: List[str] = []
    for _ in range(rng.randint(1, 3)):
        pool = focus_keys if (focus_keys and rng.random() < _FOCUS_SHARE) else all_keys
        k = rng.choice(pool)
        if k not in chosen:
            chosen.append(k)
    for k in chosen:
        out[k] = float(out[k]) + rng.gauss(0.0, sigma) * (abs(float(out[k])) + 0.35)
    return fish.stabilize_weights(out)


def _wilson_high(wins: float, n: int) -> float:
    if n <= 0:
        return 1.0
    z = 1.96
    p = wins / n
    d = 1.0 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(max(0.0, p * (1 - p) / n + z * z / (4 * n * n)))
    return (c + m) / d


def _baseline(pool, deals) -> Dict[Tuple[int, int, int], Tuple[float, float]]:
    """What the CHAMPION achieves from each (seed, seat) a candidate will use,
    as (win, margin).

    The seat is forced onto the strategy under test, exactly as the candidate's
    will be. That is the whole of the pairing: the only thing that may differ
    between a candidate game and its baseline is the weights.

    This used to play ONE unforced all-champion game per deal and read every
    seat off it. In strategy mode that compared a candidate FORCED onto, say,
    King Salmon against a champion free to play whatever suited its hand -- so
    every edge was part weights and part "was made to play a plan the hand did
    not fit", a handicap only the candidate ever paid. Fourteen generations
    overnight confirmed at a mean edge of -0.014 and crowned nothing.

    Every candidate on a deal uses the same seat (seat = deal index mod count),
    so one baseline game serves all of them, and they are compared to each
    other on identical positions as well as to the champion.

    A deal now carries its TABLE SIZE as well as its seed, and the baseline is
    played at that size too. That is what keeps the pairing exact once a
    generation spans 2P through 6P: the candidate game and the game it is
    measured against differ in the weights and in nothing else -- not the deal,
    not the seat, and not how many players are sitting at the table."""
    tasks = [(-1, sd, gi % ct, ct) for gi, (sd, ct) in enumerate(deals)]
    out: Dict[Tuple[int, int, int], Tuple[float, float]] = {}
    for (_ci, sd, k, ct), (_c, win, margin) in zip(tasks, pool.map(_play, tasks, chunksize=4)):
        out[(sd, k, ct)] = (win, margin)
    return out


def _paired(pool, n_cands, deals, base) -> Tuple[List[List[float]], List[List[float]]]:
    """Each candidate's result MINUS the champion's from the same deal and the
    same forced seat, as (margin differences, win differences).

    Selection is decided on MARGIN -- the bot's score minus the best opponent's.
    A win is almost all information thrown away: over 16 paired games against a
    very different candidate, the margin changed in 16 and the winner in 1.
    Decisions and scores move every game; who finishes first rarely flips over a
    short sample, so a win-only test needs many times the games to see anything.

    Win is still collected, because it is what actually matters: a challenger
    that raises its margin by losing by less, without winning any more, is not
    promoted (see the guard in evolve)."""
    plan = [(ci, sd, gi % ct, ct)
            for ci in range(n_cands) for gi, (sd, ct) in enumerate(deals)]
    margins: List[List[float]] = [[] for _ in range(n_cands)]
    wins: List[List[float]] = [[] for _ in range(n_cands)]
    for (ci, sd, k, ct), (_c, win, margin) in zip(plan, pool.map(_play, plan, chunksize=4)):
        bw, bm = base[(sd, k, ct)]
        margins[ci].append(margin - bm)
        wins[ci].append(win - bw)
    return margins, wins


def _lower_bound(d: List[float]) -> Tuple[float, float]:
    """(mean, 95% lower bound) of the paired difference."""
    n = len(d)
    if n < 2:
        return 0.0, -1.0
    m = sum(d) / n
    var = sum((x - m) ** 2 for x in d) / (n - 1)
    return m, m - 1.96 * math.sqrt(var / n)


def _write_json(path: str, payload: Dict[str, Any]) -> None:
    """Write a champion file so that no reader can ever see half of one.

    These files are read back while training is still running: the next
    strategy reads every OTHER strategy's champion to build the field it plays
    against, and reef_planner reads champion_planner.json at import, which
    happens in every worker process this script spawns. A plain
    json.dump(..., open(path, "w")) truncates the file first, so a reader
    arriving in that window gets a broken file -- and on a run meant to last
    days, a window that small still comes up. Writing to a temporary file and
    renaming it makes the swap atomic: a reader sees the old champion or the
    new one, never neither.
    """
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def evolve(counts: List[int], generations: int, mutants: int, screen: int, confirm: int,
           jobs: int, sigma: float, seed: int, out_dir: str, promote: bool,
           max_confirm: int = 1200, strategy: Optional[str] = None,
           planner_grade: str = "") -> None:
    os.makedirs(out_dir, exist_ok=True)
    counts = counts_for_strategy(strategy, counts)
    # The table size whose brain seeds a strategy's starting vector, and the
    # size a single-count run promotes into. With several sizes in play that is
    # the middle of them, which is the 4P the ladder was always tuned at.
    seed_count = 4 if 4 in counts else counts[len(counts) // 2]
    tag = "x".join(str(c) for c in counts) if len(counts) > 1 else str(counts[0])
    log_path = os.path.join(out_dir, f"evolve_{tag}p.log")
    fh = open(log_path, "a", encoding="utf-8")

    def log(m: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {m}"
        print(line, flush=True)
        fh.write(line + "\n")
        fh.flush()

    brain = fish.load_brain(fish.BRAIN_PATH)
    # Every size in play brings its own learned maps; the strategy vector under
    # test is one vector that has to be right at all of them.
    maps_by_count = {c: fish._train_policy_maps_from_cbrain(fish.get_count_brain(brain, c))
                     for c in counts}
    maps = maps_by_count[seed_count]

    def deals_for(n: int, rng_: random.Random) -> List[Tuple[int, int]]:
        """`n` deals, the table sizes dealt round-robin so every size carries
        the same weight in the average and a generation can never be decided by
        whichever size happened to come up most."""
        return [(rng_.randrange(1 << 30), counts[i % len(counts)]) for i in range(n)]

    strat_map: Optional[Dict[str, Dict[str, float]]] = None
    if planner_grade:
        # Tuning the Reef Planner itself. There is one set of knobs for every
        # planner grade, so it is trained once at the cheapest of them and
        # Charles Darwin and Giant Squid play with what this finds.
        champion = planner_defaults()
        ck_path = os.path.join(out_dir, "champion_planner.json")
    elif strategy:
        # Every strategy gets its own vector; the one being trained is the
        # champion, the rest are the field it has to beat.
        strat_map = {}
        for prof in fish.strategy_family_profiles():
            lab = str(prof.get("label", "")).strip().lower()
            # The field is every OTHER strategy's best-yet, not its starting
            # weights. Champions are saved to disk as they are crowned, and a
            # strategy that trains against untrained opponents is learning to
            # beat a table nobody will ever sit at: when Coral trains it must
            # face the Crustaceans that already improved, not the ones that
            # existed before they did.
            ck = os.path.join(out_dir, f"champion_{lab}.json")
            if os.path.exists(ck):
                try:
                    strat_map[lab] = fish.stabilize_weights(dict(json.load(open(ck))["weights"]))
                    continue
                except Exception:
                    pass
            strat_map[lab] = fish.stabilize_weights(
                dict(fish.get_strategy_weights(brain, lab, maps["weights"])))
        if strategy not in strat_map:
            raise SystemExit(f"unknown strategy {strategy!r}; "
                             f"known: {sorted(strat_map)}")
        champion = dict(strat_map[strategy])
        parents = COMBO_PARENTS.get(strategy)
        if parents and not os.path.exists(os.path.join(out_dir, f"champion_{strategy}.json")):
            a, b = strat_map.get(parents[0]), strat_map.get(parents[1])
            if a and b:
                champion = fish.stabilize_weights(
                    {k: (float(a.get(k, 0.0)) + float(b.get(k, 0.0))) / 2.0 for k in set(a) | set(b)})
                strat_map[strategy] = dict(champion)
                log(f"Combo {strategy}: seeded from {parents[0]} + {parents[1]} champions.")
        ck_path = os.path.join(out_dir, f"champion_{strategy}.json")
    else:
        if len(counts) != 1:
            raise SystemExit("--counts with more than one size needs --strategy: "
                             "the per-count weight vectors are trained one size at a time.")
        champion = dict(maps["weights"])
        ck_path = os.path.join(out_dir, f"champion_{counts[0]}p.json")
    if os.path.exists(ck_path):
        saved = json.load(open(ck_path))
        champion = (_clamp_params(dict(saved["weights"])) if planner_grade
                    else fish.stabilize_weights(dict(saved["weights"])))
        if strat_map is not None and strategy:
            strat_map[strategy] = dict(champion)
        log(f"Resuming from saved champion (generation {saved.get('generation', 0)}).")

    rng = random.Random(seed)
    log("=" * 68)
    who = ("/".join(f"{c}P" for c in counts))
    if planner_grade:
        who += f" · Reef Planner knobs at {planner_grade}"
    else:
        who += f" · strategy {strategy}" if strategy else " · all strategies"
    log(f"TOURNAMENT SELECTION · {who} · a mutant must beat the champion on the same deals")
    log(f"{generations} generations · {mutants} mutants · {screen} screen + {confirm} confirm games · jobs={jobs}")
    log("=" * 68)

    promotions = 0
    for gen in range(1, generations + 1):
        if planner_grade:
            cands = [_mutate_params(champion, rng, sigma) for _ in range(mutants)]
        else:
            focus = STRATEGY_FOCUS.get(strategy or "", ())
            cands = [_mutate(champion, rng, sigma, focus) for _ in range(mutants)]
        screen_deals = deals_for(screen, rng)
        t0 = time.time()
        with mp.Pool(jobs, initializer=_init,
                     initargs=(maps_by_count, champion, cands, seed_count, strategy, strat_map,
                               planner_grade)) as pool:
            base = _baseline(pool, screen_deals)
            sdiff, _swin = _paired(pool, len(cands), screen_deals, base)
        order = sorted(range(len(cands)), key=lambda i: -(sum(sdiff[i]) / len(sdiff[i])))
        keep = order[:3]
        log(f"gen {gen:>3} screen: best margin edge {sum(sdiff[keep[0]])/len(sdiff[keep[0]]):+.3f} pts "
            f"vs the same deals · {time.time()-t0:.0f}s")

        # Staged confirmation. A mutant only has to be PROVED better, and how
        # many games that takes depends on how much better it is. Playing a
        # fixed number throws away real improvements that merely needed more
        # evidence: a mutant measured at 0.297 against a 0.250 bar was
        # discarded on a lower bound of 0.248, two thousandths short. So keep
        # playing the ones that still could prove it, and stop early on the
        # ones that provably cannot.
        finals = [cands[i] for i in keep]
        acc: List[List[float]] = [[] for _ in finals]
        accw: List[List[float]] = [[] for _ in finals]
        played = 0
        alive = list(range(len(finals)))
        lo = rate = 0.0
        best_i = 0
        while alive and played < max_confirm:
            batch = min(confirm, max_confirm - played)
            conf_deals = deals_for(batch, rng)
            sub = [finals[i] for i in alive]
            with mp.Pool(jobs, initializer=_init,
                         initargs=(maps_by_count, champion, sub, seed_count, strategy, strat_map,
                                   planner_grade)) as pool:
                cbase = _baseline(pool, conf_deals)
                bd, bw = _paired(pool, len(sub), conf_deals, cbase)
            for k, i in enumerate(alive):
                acc[i].extend(bd[k])
                accw[i].extend(bw[k])
            played += batch
            stats = {i: _lower_bound(acc[i]) for i in alive}
            best_i = max(alive, key=lambda i: stats[i][1])
            rate, lo = stats[best_i]
            win_edge = sum(accw[best_i]) / max(1, len(accw[best_i]))
            if lo > 0.0 and win_edge >= 0.0:
                break
            # drop anyone whose edge is now provably negative
            alive = [i for i in alive if stats[i][0] + 1.96 * (
                (sum((x - stats[i][0]) ** 2 for x in acc[i]) / max(1, len(acc[i]) - 1))
                / max(1, len(acc[i]))) ** 0.5 > 0.0]
            if alive:
                log(f"gen {gen:>3}   +{played} games: best margin edge {rate:+.3f} pts "
                    f"(low {lo:+.3f}), wins {win_edge:+.4f} · {len(alive)} still alive")

        win_edge = sum(accw[best_i]) / max(1, len(accw[best_i])) if accw and accw[best_i] else 0.0
        # Promoted only on a margin that is provably better AND wins that are not
        # worse. Margin is where the signal is; winning is what the bot is for.
        if lo > 0.0 and win_edge >= 0.0:
            champion = finals[best_i]
            if strat_map is not None and strategy:
                strat_map[strategy] = dict(champion)
            promotions += 1
            _ref = planner_defaults() if planner_grade else maps["weights"]
            changed = {k: round(champion[k], 3) for k in champion
                       if abs(champion[k] - _ref.get(k, 0.0)) > 0.01}
            log(f"gen {gen:>3} NEW CHAMPION · margin {rate:+.3f} pts (95% low {lo:+.3f} > 0), "
                f"wins {win_edge:+.4f} over {played} paired games")
            log(f"          drifted: {changed}")
            _write_json(ck_path, {
                "count": seed_count, "counts": counts,
                "strategy": strategy, "planner_grade": planner_grade,
                "generation": gen, "weights": champion,
                "margin_edge": rate, "margin_edge_low": lo, "win_edge": win_edge,
                "games": played, "chooser": _W_CHOOSER,
                "grade": os.environ.get("FISH_TRAIN_GRADE", "")})
        else:
            log(f"gen {gen:>3} champion holds · best margin {rate:+.3f} pts (95% low {lo:+.3f}), "
                f"wins {win_edge:+.4f} over {played} paired games")

    log(f"Done. {promotions}/{generations} generations produced a new champion.")
    if planner_grade and promotions:
        log(f"Champion planner knobs saved to {ck_path}. The planner reads that file "
            f"at import (reef_planner.load_tuned_params), so every planner grade "
            f"plays with them from the next process on.")
    elif promote and promotions:
        backup = f"{fish.BRAIN_PATH}.evolve_backup_{time.strftime('%Y%m%d_%H%M%S')}.json"
        json.dump(fish.load_brain(fish.BRAIN_PATH), open(backup, "w"))
        brain = fish.load_brain(fish.BRAIN_PATH)
        if strategy:
            fish.get_strategy_weights(brain, strategy, maps["weights"]).update(champion)
            fish.save_brain(brain, fish.BRAIN_PATH)
            log(f"Promoted champion into the live brain for strategy {strategy} "
                f"(backup {backup}).")
        else:
            fish.get_count_brain(brain, counts[0])["weights"] = champion
            fish.save_brain(brain, fish.BRAIN_PATH)
            log(f"Promoted champion into the live brain for {counts[0]}P (backup {backup}).")
    elif promotions:
        log(f"Champion saved to {ck_path}; live brain untouched (--promote to apply).")
    fh.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--count", type=int, default=4, help="table size to evolve (2-8)")
    ap.add_argument("--counts", type=str, default="",
                    help="train across SEVERAL table sizes at once, e.g. 2,3,4,5,6. "
                         "The deals are shared out between them and every margin is "
                         "reported in 4P-equivalent points, so one vector is measured "
                         "on every size it will be played at. Overrides --count.")
    ap.add_argument("--generations", type=int, default=20)
    ap.add_argument("--mutants", type=int, default=10)
    ap.add_argument("--screen-games", type=int, default=60,
                    help="games per mutant in the screening round")
    ap.add_argument("--confirm-games", type=int, default=300,
                    help="games per survivor in each confirming batch")
    ap.add_argument("--max-confirm-games", type=int, default=1200,
                    help="cap on confirming games for a challenger that keeps looking real")
    ap.add_argument("--jobs", type=int, default=0, help="0 = every core")
    ap.add_argument("--sigma", type=float, default=0.25, help="mutation size")
    ap.add_argument("--seed", type=int, default=0, help="0 = random")
    ap.add_argument("--out-dir", type=str, default="fish_training/evolve")
    ap.add_argument("--strategy", type=str, default="",
                    help="train ONE strategy's weights (e.g. coral, king_salmon). "
                         "Omit to train the shared per-count vector.")
    ap.add_argument("--chooser", choices=("engine", "live"), default="engine",
                    help="'live' plays through the deep chooser the real game uses, "
                         "which is what the grade ladder actually runs on")
    ap.add_argument("--grade", type=str, default="",
                    help="grade whose search settings every bot plays at "
                         "(e.g. william_beebe for B). Only matters with --chooser live.")
    ap.add_argument("--planner", type=str, default="",
                    help="tune the REEF PLANNER's knobs instead of a weight vector, "
                         "playing every seat at this planner grade (e.g. eugenie_clark). "
                         "The knobs are shared by every planner grade, so training the "
                         "cheapest one improves all of them.")
    ap.add_argument("--promote", action="store_true",
                    help="write the final champion into the live brain")
    a = ap.parse_args()
    os.environ["FISH_TRAIN_CHOOSER"] = a.chooser
    if a.grade:
        os.environ["FISH_TRAIN_GRADE"] = fish.normalize_bot_grade(a.grade)
    elif a.planner.strip():
        # Every seat is a planner seat at that grade, so the table is graded as
        # one too: anything else keyed off the grade sees the game it is in.
        os.environ["FISH_TRAIN_GRADE"] = fish.normalize_bot_grade(a.planner)
    if a.counts.strip():
        counts = sorted({max(2, min(8, int(x))) for x in a.counts.replace(" ", "").split(",") if x})
    else:
        counts = [max(2, min(8, a.count))]
    evolve(counts=counts, generations=a.generations, mutants=a.mutants,
           screen=a.screen_games, confirm=a.confirm_games,
           jobs=a.jobs or (os.cpu_count() or 4), sigma=a.sigma,
           seed=a.seed or random.randrange(1 << 30),
           out_dir=a.out_dir, promote=a.promote, max_confirm=a.max_confirm_games,
           strategy=(a.strategy.strip().lower() or None),
           planner_grade=(fish.normalize_bot_grade(a.planner) if a.planner.strip() else ""))


if __name__ == "__main__":
    main()
