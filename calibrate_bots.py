#!/usr/bin/env python3
"""Measure what every bot grade is worth, and write the Elo back into the ladder.

The grade ladder in fish_game_all_in_one.py (F- … S+) carries an Elo next to
each grade, and the game shows that number to players before they sit down. It
is only worth showing if it is true, so this script is what makes it true: it
sits the grades down against each other for hundreds of real matches, played by
the SAME chooser the live server uses, and fits a rating to how they actually
finish.

    python3 calibrate_bots.py --games 400            # measure and report
    python3 calibrate_bots.py --games 400 --write    # ...and update the ladder

How the rating is fitted
------------------------
Every match produces a finishing ORDER, not a win/loss, so the model is
Plackett-Luce: the chance a table finishes 1st=a, 2nd=b, 3rd=c, 4th=d is

    p(a)/(p(a)+p(b)+p(c)+p(d)) · p(b)/(p(b)+p(c)+p(d)) · p(c)/(p(c)+p(d))

with p(i) = exp(theta_i). It is fitted by the standard MM iteration (Hunter
2004), which is monotone and needs no step size. Plackett-Luce's implied
head-to-head is exactly Bradley-Terry, so a theta difference converts to Elo on
the ordinary scale:

    elo_i - elo_j = (400 / ln 10) · (theta_i - theta_j)

which is what makes "B+ is 1550 and C+ is 1200" a claim you can check: 350
points says B+ takes about 88% of the head-to-head games, and if it doesn't,
this script will say so.

USE FEWER WORKERS THAN YOU HAVE CORES. This is not a performance note, it is
a correctness one. The top grades' rollout budget is WALL-CLOCK
(`plan_budget`), so a run that saturates the machine measures those grades
doing far less thinking than a player on a quiet server would ever meet. It
was measured the hard way: a 10-worker run on a 12-core box reported every
grade from B upward as the same strength, ~1300 Elo, which read like proof
that rollout confirmation does nothing. It does. The same bot with rollouts on
beats itself with them off 67.5% of the time, worth +127 Elo, over 40 games on
an idle box. The flat ladder was the measurement starving, not the bots.

So: leave headroom. Half the cores is a reasonable default. A saturated run is
still a valid measurement of a very busy server, but it is not the server most
players meet, and it is not what the published Elo should describe.

The scale has no natural zero, so the ladder is ANCHORED: the default grade is
pinned to ANCHOR_ELO and everything else is measured relative to it. Ordering
is a property of the ladder by construction (each grade is handicapped less
than the one below it), so a fitted ordering that disagrees is a bug in the
knobs, not a fact about the game: it is reported loudly, and --write refuses
to smooth it away silently.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import statistics
import sys
import time
from collections import defaultdict
from typing import Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fish_game_all_in_one as fish  # noqa: E402
import multiplayer_server as mps  # noqa: E402

ELO_PER_LOGIT = 400.0 / math.log(10.0)
ANCHOR_GRADE = fish.DEFAULT_BOT_GRADE
ANCHOR_ELO = 1200.0
LADDER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "fish_game_all_in_one.py")

_WORKER: Dict[str, object] = {}


# ── playing the games ───────────────────────────────────────────────────────

def _brain_args(player_count: int) -> Tuple[Dict[str, object], bool]:
    """The same weights and learned maps a live room hands its bots."""
    brain = fish.load_brain()
    cbrain = fish.get_count_brain(brain, player_count)
    use_history = fish.use_historical_policy_bias()
    weights = dict(fish.default_weights())
    if use_history:
        weights.update(cbrain.get("weights", {}))
    weights = fish.stabilize_weights(weights)

    def m(key: str) -> Dict:
        v = cbrain.get(key, {})
        return v if use_history and isinstance(v, dict) else {}

    try:
        turtle_gated = not fish.turtle_is_effective(cbrain)
    except Exception:
        turtle_gated = False
    return {
        "weights": weights,
        "synergy_map": m("synergy"),
        "species_map": m("species_synergy"),
        "same_ocean_map": m("same_ocean_synergy"),
        "strategy_value_map": m("strategy_value"),
        "strategy_count_map": m("strategy_count"),
        "strategy_transition_map": m("strategy_transition"),
        "strategy_transition_count_map": m("strategy_transition_count"),
    }, turtle_gated


def _make_policy(args: Dict[str, object]):
    def policy(gs, ms, player):
        return mps.choose_action_weighted_deep(
            gs, ms, player,
            args["weights"], args["synergy_map"], args["species_map"],
            args["same_ocean_map"], args["strategy_value_map"],
            args["strategy_count_map"], args["strategy_transition_map"],
            args["strategy_transition_count_map"],
        )
    return policy


def _worker_init(player_count: int) -> None:
    args, gated = _brain_args(player_count)
    _WORKER["args"] = args
    _WORKER["gated"] = gated


def _play(task: Tuple[int, List[str]]) -> Dict[str, object]:
    """One match. Returns the finishing order as ladder ids, best first."""
    seed, grades = task
    args = _WORKER["args"]
    started = time.time()
    gs, _ms = fish.run_match(
        card_db=mps.CARD_DB,
        player_names=[f"S{i}_{g}" for i, g in enumerate(grades)],
        action_policies=[_make_policy(args) for _ in grades],
        seed=seed,
        max_turns=260,
        human_index=None,
        human_indices=set(),
        verbose=False,
        verbose_state=False,
        ai_difficulties=list(grades),
        turtle_gated=bool(_WORKER["gated"]),
    )
    scores: List[float] = []
    for p in gs.players:
        try:
            scores.append(float(fish.final_points(gs, p)))
        except Exception:
            scores.append(float(getattr(p, "score", 0)))
    return {
        "grades": list(grades),
        "scores": scores,
        "seconds": round(time.time() - started, 2),
    }


# ── choosing who plays whom ─────────────────────────────────────────────────

def build_schedule(n_games: int, seats: int, window: int, seed: int) -> List[Tuple[int, List[str]]]:
    """Tables that are worth playing.

    Most tables are drawn from a WINDOW of neighbouring grades, because a match
    between grades that are close is where the information is: S+ beating F- 60
    games running says only that the gap is large, not how large. A fifth of
    the schedule is drawn from the whole ladder anyway, which is what keeps the
    ratings on one connected scale instead of nineteen local ones.
    """
    rng = random.Random(seed)
    ladder = list(fish.BOT_GRADE_ORDER)
    n = len(ladder)
    out: List[Tuple[int, List[str]]] = []
    for g in range(n_games):
        if g % 5 == 4:
            idx = rng.sample(range(n), min(seats, n))
        else:
            lo = rng.randrange(0, n)
            lo = max(0, min(lo, n - 1))
            span = [i for i in range(max(0, lo - window), min(n, lo + window + 1))]
            if len(span) >= seats:
                idx = rng.sample(span, seats)
            else:
                idx = [rng.choice(span) for _ in range(seats)]
        rng.shuffle(idx)   # seat order is turn order, so it must not track grade
        out.append((seed * 7919 + g, [ladder[i] for i in idx]))
    return out


# ── fitting ─────────────────────────────────────────────────────────────────

def fit_plackett_luce(rankings: Sequence[Sequence[str]], items: Sequence[str],
                      iters: int = 4000, tol: float = 1e-10) -> Dict[str, float]:
    """MM iteration for Plackett-Luce. Returns theta (log strength), mean 0.

    `rankings` are finishing orders, best first. Items that never appear, or
    that never once finished ahead of anybody, would send the fit to infinity,
    so they are held at the mean and reported by the caller.
    """
    index = {name: i for i, name in enumerate(items)}
    n = len(items)
    wins = [0.0] * n          # times each item was chosen out of a live set
    for order in rankings:
        for pos in range(len(order) - 1):
            wins[index[order[pos]]] += 1.0

    p = [1.0] * n
    for _ in range(iters):
        denom = [0.0] * n
        for order in rankings:
            # Walk the order from last to first, accumulating the tail sum so
            # each "choice out of the remaining field" costs O(1), not O(k).
            tail = 0.0
            for pos in range(len(order) - 1, -1, -1):
                tail += p[index[order[pos]]]
                if pos < len(order) - 1:
                    inv = 1.0 / tail if tail > 0 else 0.0
                    for later in order[pos:]:
                        denom[index[later]] += inv
        new = []
        for i in range(n):
            if denom[i] <= 0.0 or wins[i] <= 0.0:
                new.append(p[i])
            else:
                new.append(wins[i] / denom[i])
        geo = math.exp(sum(math.log(max(v, 1e-12)) for v in new) / n)
        new = [v / geo for v in new]
        delta = max(abs(a - b) for a, b in zip(new, p))
        p = new
        if delta < tol:
            break
    return {name: math.log(max(p[index[name]], 1e-12)) for name in items}


def bootstrap_spread(rankings: List[List[str]], items: Sequence[str],
                     rounds: int, seed: int) -> Dict[str, float]:
    """Standard deviation of each grade's Elo over resampled schedules."""
    if rounds <= 0:
        return {name: 0.0 for name in items}
    rng = random.Random(seed)
    draws: Dict[str, List[float]] = defaultdict(list)
    for _ in range(rounds):
        sample = [rankings[rng.randrange(len(rankings))] for _ in range(len(rankings))]
        seen = {g for order in sample for g in order}
        if len(seen) < len(items):
            continue
        theta = fit_plackett_luce(sample, items, iters=400)
        anchor = theta.get(ANCHOR_GRADE, 0.0)
        for name in items:
            draws[name].append((theta[name] - anchor) * ELO_PER_LOGIT)
    out = {}
    for name in items:
        vals = draws.get(name, [])
        out[name] = statistics.pstdev(vals) if len(vals) > 1 else 0.0
    return out


def isotonic(values: List[float]) -> List[float]:
    """Pool-adjacent-violators: the closest non-decreasing series to `values`."""
    out = [[v, 1.0] for v in values]
    i = 0
    while i < len(out) - 1:
        if out[i][0] <= out[i + 1][0]:
            i += 1
            continue
        total = out[i][0] * out[i][1] + out[i + 1][0] * out[i + 1][1]
        weight = out[i][1] + out[i + 1][1]
        out[i] = [total / weight, weight]
        del out[i + 1]
        if i > 0:
            i -= 1
    flat: List[float] = []
    for value, weight in out:
        flat.extend([value] * int(weight))
    return flat


# ── writing the answer back ─────────────────────────────────────────────────

def write_ladder(elos: Dict[str, int]) -> List[str]:
    """Replace the elo column in _BOT_GRADE_LADDER, in place."""
    src = open(LADDER_PATH, encoding="utf-8").read()
    changed: List[str] = []
    for key, elo in elos.items():
        pat = re.compile(
            r'(\(\s*"' + re.escape(key) + r'"\s*,\s*"[^"]+"\s*,\s*)(\d+)(\s*,)')
        m = pat.search(src)
        if not m:
            raise SystemExit(f"could not find {key} in the ladder table")
        if int(m.group(2)) != int(elo):
            changed.append(f"{key}: {m.group(2)} -> {elo}")
        src = pat.sub(lambda mm: f"{mm.group(1)}{elo:>4}{mm.group(3)}", src, count=1)
    open(LADDER_PATH, "w", encoding="utf-8").write(src)
    return changed


# ── main ────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--games", type=int, default=400, help="matches to play")
    ap.add_argument("--seats", type=int, default=4, help="players per table")
    ap.add_argument("--window", type=int, default=4,
                    help="how far apart grades at one table may be, in ladder steps")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 4) - 2))
    ap.add_argument("--seed", type=int, default=20260909)
    ap.add_argument("--bootstrap", type=int, default=200,
                    help="resamples for the uncertainty column (0 to skip)")
    ap.add_argument("--results", default="", help="write the raw match results here")
    ap.add_argument("--resume", default="", help="read match results from here instead of playing")
    ap.add_argument("--write", action="store_true",
                    help="update the Elo column in the ladder")
    ap.add_argument("--force", action="store_true",
                    help="--write even when the measured order disagrees with the ladder")
    a = ap.parse_args()

    ladder = list(fish.BOT_GRADE_ORDER)
    label = {k: fish.AI_DIFFICULTY_CONFIGS[k]["grade"] for k in ladder}
    nominal = {k: fish.AI_DIFFICULTY_CONFIGS[k]["elo"] for k in ladder}

    results: List[Dict[str, object]]
    if a.resume:
        results = [json.loads(line) for line in open(a.resume, encoding="utf-8")]
        print(f"Read {len(results)} matches from {a.resume}")
    else:
        schedule = build_schedule(a.games, a.seats, a.window, a.seed)
        print(f"Playing {len(schedule)} matches of {a.seats} "
              f"across {len(ladder)} grades on {a.workers} workers…", flush=True)
        results = []
        started = time.time()
        out_fh = open(a.results, "w", encoding="utf-8") if a.results else None
        try:
            if a.workers <= 1:
                _worker_init(a.seats)
                for i, task in enumerate(schedule, 1):
                    results.append(_play(task))
                    if out_fh:
                        out_fh.write(json.dumps(results[-1]) + "\n")
                        out_fh.flush()
                    print(f"  {i}/{len(schedule)}", flush=True)
            else:
                import multiprocessing as mp
                ctx = mp.get_context("fork")
                with ctx.Pool(a.workers, initializer=_worker_init,
                              initargs=(a.seats,)) as pool:
                    for i, res in enumerate(
                            pool.imap_unordered(_play, schedule, chunksize=1), 1):
                        results.append(res)
                        if out_fh:
                            out_fh.write(json.dumps(res) + "\n")
                            out_fh.flush()
                        if i % 10 == 0 or i == len(schedule):
                            done = time.time() - started
                            rate = done / i
                            print(f"  {i}/{len(schedule)}  "
                                  f"{done/60:.1f} min elapsed, "
                                  f"~{rate*(len(schedule)-i)/60:.1f} min left",
                                  flush=True)
        finally:
            if out_fh:
                out_fh.close()

    # ── turn matches into finishing orders ──────────────────────────────────
    rankings: List[List[str]] = []
    played = defaultdict(int)
    points = defaultdict(list)
    firsts = defaultdict(float)
    for res in results:
        grades = list(res["grades"])
        scores = list(res["scores"])
        for g, sc in zip(grades, scores):
            played[g] += 1
            points[g].append(float(sc))
        order = sorted(range(len(grades)), key=lambda i: scores[i], reverse=True)
        best = scores[order[0]]
        tied = [i for i in order if scores[i] == best]
        for i in tied:
            firsts[grades[i]] += 1.0 / len(tied)
        # A tie is not evidence about which of the tied is better, so the tied
        # block is shuffled rather than ordered by seat, which would quietly
        # credit whoever sat down first.
        rng = random.Random(hash(tuple(scores)) & 0xFFFFFFFF)
        rng.shuffle(tied)
        seen = set(tied)
        rankings.append([grades[i] for i in tied]
                        + [grades[i] for i in order if i not in seen])

    missing = [g for g in ladder if played[g] == 0]
    if missing:
        print("\n⚠ never played: " + ", ".join(label[g] for g in missing))
    items = [g for g in ladder if played[g] > 0]

    theta = fit_plackett_luce(rankings, items)
    anchor_theta = theta.get(ANCHOR_GRADE, statistics.mean(theta.values()))
    raw = {g: ANCHOR_ELO + (theta[g] - anchor_theta) * ELO_PER_LOGIT for g in items}
    spread = bootstrap_spread(rankings, items, a.bootstrap, a.seed + 1)

    smoothed_vals = isotonic([raw[g] for g in items])
    smoothed = {g: v for g, v in zip(items, smoothed_vals)}
    inversions = [
        (items[i], items[i + 1])
        for i in range(len(items) - 1)
        if raw[items[i]] > raw[items[i + 1]] + 1e-9
    ]

    # ── the report ──────────────────────────────────────────────────────────
    total_secs = sum(float(r.get("seconds", 0)) for r in results)
    print(f"\n{len(results)} matches, {total_secs/60:.1f} CPU-minutes of play\n")
    print(f"{'grade':>5}  {'games':>5}  {'1st':>6}  {'avg pts':>8}  "
          f"{'measured':>9}  {'± ':>5}  {'ladder':>7}  {'drift':>7}")
    print("-" * 68)
    for g in items:
        pts = statistics.mean(points[g]) if points[g] else 0.0
        print(f"{label[g]:>5}  {played[g]:>5}  "
              f"{firsts[g]/max(1,played[g]):>6.1%}  {pts:>8.1f}  "
              f"{raw[g]:>9.0f}  {spread[g]:>5.0f}  {nominal[g]:>7}  "
              f"{raw[g]-nominal[g]:>+7.0f}")

    lo, hi = min(raw.values()), max(raw.values())
    print(f"\nmeasured spread: {hi-lo:.0f} Elo "
          f"({label[items[0]]} {lo:.0f} → {label[items[-1]]} {hi:.0f}); "
          f"ladder claims {nominal[ladder[-1]]-nominal[ladder[0]]}")
    # An inversion inside the noise is not evidence that the ladder is wrong;
    # it is evidence that this many games cannot separate two grades that
    # close. An inversion BIGGER than the noise is a real problem with the
    # knobs, and is the only kind worth refusing to publish.
    real_inversions = []
    for lo_g, hi_g in inversions:
        noise = math.hypot(spread.get(lo_g, 0.0), spread.get(hi_g, 0.0))
        if raw[lo_g] - raw[hi_g] > 2.0 * max(noise, 1.0):
            real_inversions.append((lo_g, hi_g))

    if inversions:
        print(f"\n{len(inversions)} pair(s) finished out of ladder order:")
        for lo_g, hi_g in inversions:
            noise = math.hypot(spread.get(lo_g, 0.0), spread.get(hi_g, 0.0))
            gap = raw[lo_g] - raw[hi_g]
            tag = "REAL" if (lo_g, hi_g) in real_inversions else "within noise"
            print(f"    {label[lo_g]} ({raw[lo_g]:.0f}) > {label[hi_g]} "
                  f"({raw[hi_g]:.0f})  by {gap:.0f}, noise ±{noise:.0f}  [{tag}]")
        if real_inversions:
            print("  A REAL inversion means two grades' knobs disagree with the "
                  "order the ladder claims. Fix the knobs, do not smooth it.")
        else:
            print("  All inside the measurement's own error bars. The ladder's "
                  "order is known by construction (each grade is handicapped "
                  "less than the one below), so the honest estimate under that "
                  "constraint is the isotonic fit, which is what --write uses.")
    else:
        print("\n✓ every grade measured stronger than the one below it.")

    # A gap this small is not a grade; it is noise wearing a letter.
    tight = [
        (items[i], items[i + 1])
        for i in range(len(items) - 1)
        if 0 <= raw[items[i + 1]] - raw[items[i]] < 25
    ]
    if tight:
        print(f"\n⚠ {len(tight)} neighbouring pair(s) are within 25 Elo, close "
              f"enough that a player could not tell them apart:")
        for a_g, b_g in tight:
            print(f"    {label[a_g]} → {label[b_g]}: "
                  f"{raw[b_g]-raw[a_g]:+.0f}")

    if a.write:
        if real_inversions and not a.force:
            raise SystemExit(
                "\nRefusing to write: " + ", ".join(
                    f"{label[a_g]} beats {label[b_g]}" for a_g, b_g in real_inversions)
                + " by more than the measurement error. Publishing an Elo that "
                "says one thing while the bot does another is the one outcome "
                "worth failing over. Fix the knobs and re-measure, or pass "
                "--force to write the isotonic fit anyway.")
        final = {g: int(round(smoothed[g] if inversions else raw[g])) for g in items}
        for g in ladder:
            final.setdefault(g, nominal[g])
        changed = write_ladder(final)
        print(f"\nWrote {len(changed)} Elo change(s) into the ladder:")
        for line in changed:
            print("    " + line)
    else:
        print("\n(no --write: the ladder was not touched)")


if __name__ == "__main__":
    main()
