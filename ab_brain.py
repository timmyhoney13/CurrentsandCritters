#!/usr/bin/env python3
"""Paired-deal A/B for a BRAIN, the way ab.py does it for the planner's knobs.

ab.py answers "is this planner setting better", and every seat in it is a
planner. It cannot answer "did tonight's learning make the bots better",
because learning does not live in the planner's knobs -- it lives in the
brain's learned maps and its per-strategy weight vectors, and those are read by
the grades BELOW the planner (william_beebe and down, via
choose_action_weighted_deep). So this is the other half of the measurement.

One seat plays with brain A, every other seat with brain B. The same deal is
then replayed with that seat on brain B too, so the two arms differ in exactly
one thing: which brain is deciding one seat's moves. The per-deal difference of
margins is the measurement, which removes the deal, the seat and the table size.

    python3 ab_brain.py --games 300 --counts 2,4,6 \
        --new fish_ai_brain.json \
        --old fish_training/baseline_20261014/fish_ai_brain.json \
        --label tonight-vs-baseline

Margins are reported in 4P-equivalent points, the units every other training
log here uses (bot_evolve._MARGIN_SCALE), so a number out of this is comparable
with a number out of ab.py.

A note on what this can see: a paired deal has a standard deviation of about 35
points, because one changed decision cascades into a wholly different game.
Pairing removes the deal, not the chaos. At 300 deals per table size the 95%
interval is roughly +-4 points, so this cannot resolve anything subtler than
that and must not be read as though it could.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import time
import multiprocessing as mp
from typing import Any, Dict, List, Optional, Tuple

# The engine's decisions depend on the order it walks sets of card names, and
# Python randomises that per process, so the same deal played in two workers
# could come out as two different games -- which would put the chaos back into
# a comparison whose whole point is that both arms see the same game. Set
# before any Pool exists, because spawned workers read it at interpreter start.
os.environ["PYTHONHASHSEED"] = "0"
# Rollout counts, not the clock, decide how far a live-chooser bot looks, so a
# busy machine cannot make one arm think less than the other.
os.environ["FISH_PLAN_BY_COUNT"] = "1"

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.chdir(HERE)
import fish_game_all_in_one as fish

# Same scale bot_evolve reports in: median top score by table size, so a deal
# counts the same wherever it was played.
MARGIN_SCALE = {2: 0.384, 3: 0.647, 4: 1.0, 5: 1.344, 6: 1.778, 7: 2.1, 8: 2.4}

_CARD_DB = None
_A: Optional[Dict[int, Dict[str, Any]]] = None     # count -> maps, arm 1
_B: Optional[Dict[int, Dict[str, Any]]] = None     # count -> maps, arm 0
_A_STRAT: Optional[Dict[str, Dict[str, float]]] = None
_B_STRAT: Optional[Dict[str, Dict[str, float]]] = None
_GRADE = "william_beebe"
_MPS = None


def _load_old(path: str, mode: str) -> Dict[str, Any]:
    """A brain file loaded with `mode` worth of anchor flooring.

    fish.load_brain does three things on the way in: stabilise the weights,
    floor two STAR weights, and apply ensure_priority_anchor_brain_rules. The
    first two are reproduced here whatever the mode; the third is what the mode
    selects. Without this the harness cannot see an anchor change at all,
    because load_brain would give both arms the same treatment.
    """
    if mode == "full":
        return fish.load_brain(path)
    with open(path, encoding="utf-8") as fh:
        brain = json.load(fh)
    w = fish.stabilize_weights(dict(brain.get("weights", {})))
    w["uses_star"] = max(0.95, float(w.get("uses_star", 0.0)))
    w["pool_pick_value"] = max(0.9, float(w.get("pool_pick_value", 0.0)))
    brain["weights"] = fish.stabilize_weights(dict(w))
    if mode == "top":
        was = fish.ANCHOR_FLOOR_COUNTS
        fish.ANCHOR_FLOOR_COUNTS = False
        try:
            fish.ensure_priority_anchor_brain_rules(brain)
        finally:
            fish.ANCHOR_FLOOR_COUNTS = was
    return brain


def _maps_by_count(brain: Dict[str, Any], counts: List[int]) -> Dict[int, Dict[str, Any]]:
    """The learned maps each table size plays with, one snapshot per count."""
    return {c: fish._train_policy_maps_from_cbrain(fish.get_count_brain(brain, c))
            for c in counts}


def _strategy_vectors(brain: Dict[str, Any]) -> Dict[str, Dict[str, float]]:
    """Every strategy's own weight vector, as the live server reads them.

    brain["by_strategy"][label] IS the vector -- not a record holding one under
    a "weights" key, which is what the champion files on disk look like and
    what this read wrongly for its first draft. It reported "0 strategy
    vectors" against a brain holding thirteen, which would have measured both
    arms on the shared fallback vector and called the difference zero.
    """
    out: Dict[str, Dict[str, float]] = {}
    by_strat = brain.get("by_strategy")
    if isinstance(by_strat, dict):
        for label, slot in by_strat.items():
            if isinstance(slot, dict):
                out[str(label).strip().lower()] = {k: float(v) for k, v in slot.items()
                                                  if isinstance(v, (int, float))}
    return out


def _init(a_maps, b_maps, a_strat, b_strat, grade):
    global _CARD_DB, _A, _B, _A_STRAT, _B_STRAT, _GRADE, _MPS
    _CARD_DB = fish.load_card_db()
    fish.register_all_card_abilities(_CARD_DB)
    _A, _B, _A_STRAT, _B_STRAT, _GRADE = a_maps, b_maps, a_strat, b_strat, grade
    import multiplayer_server as mps
    _MPS = mps


def _policy(maps: Dict[str, Any], strat: Dict[str, Dict[str, float]]):
    """The deep chooser the real game runs, reading this brain's maps and the
    weight vector of whatever plan the seat is committed to."""
    fb = maps["weights"]
    mps = _MPS

    def _pol(gs, ms, p):
        lab = str(p.flags.get("_strategy_family", "")).strip().lower()
        return mps.choose_action_weighted_deep(
            gs, ms, p, strat.get(lab) or fb,
            maps.get("synergy", {}), maps.get("species_synergy", {}),
            maps.get("same_ocean_synergy", {}),
            maps.get("strategy_value", {}), maps.get("strategy_count", {}),
            maps.get("strategy_transition", {}), maps.get("strategy_transition_count", {}))
    return _pol


def _play(task):
    """(arm, seed, seat, count) -> (arm, seed, seat, count, win, margin, err)"""
    arm, seed, seat, count = task
    random.seed(seed)
    b_pol = _policy(_B[count], _B_STRAT)
    seat_pol = _policy(_A[count], _A_STRAT) if arm == 1 else _policy(_B[count], _B_STRAT)
    policies = [seat_pol if i == seat else b_pol for i in range(count)]
    try:
        gs, _ms = fish.run_match(
            card_db=_CARD_DB,
            player_names=[f"P{i}" for i in range(count)],
            action_policies=policies,
            seed=seed, max_turns=500, human_index=None,
            verbose=False, verbose_state=False,
            ai_difficulties=[_GRADE] * count,
            online_weights=None, online_state=None, online_state_path=None,
        )
        finals = [float(fish.final_points(gs, p)) for p in gs.players]
    except Exception as e:
        return (arm, seed, seat, count, None, None, repr(e)[:200])
    mine = finals[seat]
    top = max(finals)
    if mine >= top - 1e-9 and finals.count(top) == 1:
        win = 1.0
    elif abs(mine - top) < 1e-9:
        win = 0.5
    else:
        win = 0.0
    others = [s for j, s in enumerate(finals) if j != seat]
    margin = (mine - (max(others) if others else 0.0)) * MARGIN_SCALE.get(count, 1.0)
    return (arm, seed, seat, count, win, margin, None)


def mean_ci(xs):
    n = len(xs)
    if n == 0:
        return 0.0, 0.0, 0.0
    m = sum(xs) / n
    if n < 2:
        return m, 0.0, 0.0
    var = sum((x - m) ** 2 for x in xs) / (n - 1)
    se = math.sqrt(var / n)
    return m, m - 1.96 * se, m + 1.96 * se


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=300, help="deals PER table size")
    ap.add_argument("--counts", default="2,4,6")
    ap.add_argument("--new", default="fish_ai_brain.json",
                    help="the brain under test (plays ONE seat)")
    ap.add_argument("--old", required=True,
                    help="the brain it is measured against (plays every other seat)")
    ap.add_argument("--old-anchors", choices=("full", "top", "none"), default="full",
                    help="how much anchor flooring the OLD arm gets. 'full' is "
                         "what the engine does now (top-level maps AND each "
                         "count's own copy). 'top' is the behaviour before "
                         "2026-10-15, flooring the shared maps only, which is "
                         "what every count that has its own cell was NOT getting. "
                         "'none' skips the floors entirely, for measuring a change "
                         "to the anchor lists themselves. Needed because "
                         "fish.load_brain floors at every load, so without this "
                         "both arms get the same treatment and an anchor change "
                         "is invisible to the harness meant to measure it.")
    ap.add_argument("--raw-old", action="store_true",
                    help="deprecated alias for --old-anchors none")
    ap.add_argument("--grade", default="william_beebe",
                    help="B is the lowest grade that plays with a strategy's trained "
                         "weights, and every grade above it reuses the same weights "
                         "with more search on top")
    ap.add_argument("--label", default="new-vs-old")
    ap.add_argument("--seed", type=int, default=20261014)
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 4) - 2))
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    counts = [int(c) for c in a.counts.split(",") if c.strip()]
    mode = "none" if a.raw_old else a.old_anchors
    new_brain = fish.load_brain(a.new)
    old_brain = _load_old(a.old, mode)
    a_maps, b_maps = _maps_by_count(new_brain, counts), _maps_by_count(old_brain, counts)
    a_strat, b_strat = _strategy_vectors(new_brain), _strategy_vectors(old_brain)

    print(f"new {a.new}: {new_brain.get('games_played', 0)} games learned, "
          f"{len(a_strat)} strategy vectors")
    print(f"old {a.old}: {old_brain.get('games_played', 0)} games learned, "
          f"{len(b_strat)} strategy vectors", flush=True)

    rng = random.Random(a.seed)
    deals = []
    for count in counts:
        for i in range(a.games):
            deals.append((rng.randrange(1, 2 ** 31 - 1), i % count, count))
    tasks = [(1, s, t, c) for (s, t, c) in deals] + [(0, s, t, c) for (s, t, c) in deals]
    rng.shuffle(tasks)

    t0 = time.time()
    res: Dict[Tuple[int, int, int], Dict[int, Tuple[float, float]]] = {}
    errs: List[str] = []
    with mp.Pool(a.jobs, initializer=_init,
                 initargs=(a_maps, b_maps, a_strat, b_strat, a.grade)) as pool:
        done = 0
        for out in pool.imap_unordered(_play, tasks, chunksize=1):
            arm, seed, seat, count, win, margin, err = out
            done += 1
            if err:
                errs.append(err)
                continue
            res.setdefault((seed, seat, count), {})[arm] = (win, margin)
            if done % 100 == 0:
                el = time.time() - t0
                print(f"  {done}/{len(tasks)} games  {el:.0f}s  "
                      f"{done/max(el,1e-9):.1f}/s", flush=True)

    paired = {c: [] for c in counts}
    wins = {c: [0.0, 0.0, 0] for c in counts}
    allpaired: List[float] = []
    for (seed, seat, count), arms in res.items():
        if 0 not in arms or 1 not in arms:
            continue
        nw, nm = arms[1]
        ow, om = arms[0]
        paired[count].append(nm - om)
        allpaired.append(nm - om)
        wins[count][0] += nw
        wins[count][1] += ow
        wins[count][2] += 1

    print(f"\n=== {a.label}  grade={a.grade}  {time.time()-t0:.0f}s errors={len(errs)} ===")
    for c in counts:
        d = paired[c]
        if not d:
            continue
        m, lo, hi = mean_ci(d)
        nw, ow, n = wins[c]
        print(f"  {c}P  n={n:4d}  paired margin {m:+7.3f}  95% CI [{lo:+7.3f},{hi:+7.3f}]  "
              f"winrate {nw/max(1,n):.3f} vs old {ow/max(1,n):.3f} (fair {1.0/c:.3f})")
    m, lo, hi = mean_ci(allpaired)
    tnw = sum(wins[c][0] for c in counts)
    tow = sum(wins[c][1] for c in counts)
    tn = sum(wins[c][2] for c in counts)
    verdict = "BETTER" if lo > 0 else ("WORSE" if hi < 0 else "no proof")
    print(f"  ALL n={tn}  paired margin {m:+7.3f}  95% CI [{lo:+7.3f},{hi:+7.3f}]  "
          f"winrate {tnw/max(1,tn):.3f} vs {tow/max(1,tn):.3f}")
    print(f"  VERDICT: {verdict}")
    if errs:
        print(f"  first errors: {errs[:3]}")
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump({"label": a.label, "new": a.new, "old": a.old, "grade": a.grade,
                       "counts": counts, "games": a.games, "n": tn,
                       "mean": m, "ci": [lo, hi], "verdict": verdict,
                       "winrate_new": tnw / max(1, tn), "winrate_old": tow / max(1, tn),
                       "paired_by_count": {str(k): v for k, v in paired.items()},
                       "errors": len(errs)}, f, indent=1)
        print(f"  wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
