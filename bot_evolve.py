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

_W_CARD_DB = None
_W_MAPS: Optional[Dict[str, Any]] = None
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


# Which decision-maker plays, and at what grade. "engine" is the light one-pass
# chooser in fish_game_all_in_one; "live" is the deep chooser the real game uses,
# the only one that reads the grade ladder's search knobs. Weights tuned on the
# engine chooser were tuned for a bot nobody plays against, so the ranks train
# on "live". Passed by environment because spawned workers inherit it.
_W_CHOOSER = os.environ.get("FISH_TRAIN_CHOOSER", "engine")
_W_GRADE = os.environ.get("FISH_TRAIN_GRADE", "")
_MPS = None


def _init(maps, champion, candidates, count, strategy=None, strat_map=None):
    global _W_CARD_DB, _W_MAPS, _W_CHAMPION, _W_CANDIDATES, _W_COUNT
    global _W_STRATEGY, _W_STRAT_MAP, _W_CHOOSER, _W_GRADE, _MPS
    _W_CARD_DB = fish.load_card_db()
    _W_MAPS, _W_CHAMPION, _W_CANDIDATES, _W_COUNT = maps, champion, candidates, count
    _W_STRATEGY, _W_STRAT_MAP = strategy, strat_map
    _W_CHOOSER = os.environ.get("FISH_TRAIN_CHOOSER", "engine")
    _W_GRADE = os.environ.get("FISH_TRAIN_GRADE", "") or fish.DEFAULT_BOT_GRADE
    if _W_CHOOSER == "live":
        import multiplayer_server as mps
        _MPS = mps


def _strategy_policy(strategy_weights: Dict[str, Dict[str, float]]):
    """A policy that scores with the weights of whatever strategy the bot is
    committed to, read at decision time, on the configured chooser."""
    if _W_CHOOSER != "live":
        return fish._train_make_strategy_policy(_W_MAPS, strategy_weights, epsilon=0.0)
    m, fb, mps = _W_MAPS, _W_MAPS["weights"], _MPS

    def _pol(gs, ms, p):
        lab = str(p.flags.get("_strategy_family", "")).strip().lower()
        return mps.choose_action_weighted_deep(
            gs, ms, p, strategy_weights.get(lab) or fb,
            m.get("synergy", {}), m.get("species_synergy", {}), m.get("same_ocean_synergy", {}),
            m.get("strategy_value", {}), m.get("strategy_count", {}),
            m.get("strategy_transition", {}), m.get("strategy_transition_count", {}))
    return _pol


def _policies_for(cand: Optional[Dict[str, float]], seat: int):
    """Build one policy per seat, and say which strategy each seat is forced to."""
    if _W_STRATEGY is None:
        pol = [fish._train_make_policy(
                   _W_MAPS, weights=dict(cand if (cand is not None and i == seat) else _W_CHAMPION),
                   epsilon=0.0)
               for i in range(_W_COUNT)]
        return pol, None
    base = dict(_W_STRAT_MAP or {})
    seat_map = dict(base)
    if cand is not None:
        seat_map[_W_STRATEGY] = cand
    champ_pol = _strategy_policy(base)
    seat_pol = _strategy_policy(seat_map)
    pol = [seat_pol if i == seat else champ_pol for i in range(_W_COUNT)]
    forced = [_W_STRATEGY if i == seat else None for i in range(_W_COUNT)]
    return pol, forced


def _play(task: Tuple[int, int, int]) -> Tuple[int, float, float]:
    """One game. `ci` >= 0 puts that candidate in `seat` against champions;
    ci == -1 plays the all-champion version of the same deal, which is the
    baseline every candidate on this seed is measured against."""
    ci, seed, seat = task
    random.seed(seed)
    cand = _W_CANDIDATES[ci] if ci >= 0 else None
    policies, forced = _policies_for(cand, seat)
    try:
        gs, _ms = fish.run_match(
            card_db=_W_CARD_DB,
            player_names=[f"P{i}" for i in range(_W_COUNT)],
            action_policies=policies,
            seed=seed, max_turns=500, human_index=None,
            verbose=False, verbose_state=False,
            ai_difficulties=[_W_GRADE or fish.DEFAULT_BOT_GRADE] * _W_COUNT,
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
    return ci, win, mine - (max(others) if others else 0.0)


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


def _baseline(pool, seeds, count) -> Dict[Tuple[int, int], Tuple[float, float]]:
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
    other on identical positions as well as to the champion."""
    tasks = [(-1, s, gi % count) for gi, s in enumerate(seeds)]
    out: Dict[Tuple[int, int], Tuple[float, float]] = {}
    for (_ci, s, k), (_c, win, margin) in zip(tasks, pool.map(_play, tasks, chunksize=4)):
        out[(s, k)] = (win, margin)
    return out


def _paired(pool, n_cands, seeds, count, base) -> Tuple[List[List[float]], List[List[float]]]:
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
    plan = [(ci, s, gi % count)
            for ci in range(n_cands) for gi, s in enumerate(seeds)]
    margins: List[List[float]] = [[] for _ in range(n_cands)]
    wins: List[List[float]] = [[] for _ in range(n_cands)]
    for (ci, s, k), (_c, win, margin) in zip(plan, pool.map(_play, plan, chunksize=4)):
        bw, bm = base[(s, k)]
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


def evolve(count: int, generations: int, mutants: int, screen: int, confirm: int,
           jobs: int, sigma: float, seed: int, out_dir: str, promote: bool,
           max_confirm: int = 1200, strategy: Optional[str] = None) -> None:
    os.makedirs(out_dir, exist_ok=True)
    log_path = os.path.join(out_dir, f"evolve_{count}p.log")
    fh = open(log_path, "a", encoding="utf-8")

    def log(m: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {m}"
        print(line, flush=True)
        fh.write(line + "\n")
        fh.flush()

    brain = fish.load_brain(fish.BRAIN_PATH)
    cbrain = fish.get_count_brain(brain, count)
    maps = fish._train_policy_maps_from_cbrain(cbrain)
    neutral = 1.0 / count

    strat_map: Optional[Dict[str, Dict[str, float]]] = None
    if strategy:
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
        champion = dict(maps["weights"])
        ck_path = os.path.join(out_dir, f"champion_{count}p.json")
    if os.path.exists(ck_path):
        saved = json.load(open(ck_path))
        champion = fish.stabilize_weights(dict(saved["weights"]))
        if strat_map is not None and strategy:
            strat_map[strategy] = dict(champion)
        log(f"Resuming from saved champion (generation {saved.get('generation', 0)}).")

    rng = random.Random(seed)
    log("=" * 68)
    who = f"{count}P" + (f" · strategy {strategy}" if strategy else " · all strategies")
    log(f"TOURNAMENT SELECTION · {who} · a mutant must beat {neutral:.3f} to take the crown")
    log(f"{generations} generations · {mutants} mutants · {screen} screen + {confirm} confirm games · jobs={jobs}")
    log("=" * 68)

    promotions = 0
    for gen in range(1, generations + 1):
        focus = STRATEGY_FOCUS.get(strategy or "", ())
        cands = [_mutate(champion, rng, sigma, focus) for _ in range(mutants)]
        screen_seeds = [rng.randrange(1 << 30) for _ in range(screen)]
        t0 = time.time()
        with mp.Pool(jobs, initializer=_init,
                     initargs=(maps, champion, cands, count, strategy, strat_map)) as pool:
            base = _baseline(pool, screen_seeds, count)
            sdiff, _swin = _paired(pool, len(cands), screen_seeds, count, base)
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
            conf_seeds = [rng.randrange(1 << 30) for _ in range(batch)]
            sub = [finals[i] for i in alive]
            with mp.Pool(jobs, initializer=_init,
                         initargs=(maps, champion, sub, count, strategy, strat_map)) as pool:
                cbase = _baseline(pool, conf_seeds, count)
                bd, bw = _paired(pool, len(sub), conf_seeds, count, cbase)
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
            changed = {k: round(champion[k], 3) for k in champion
                       if abs(champion[k] - maps["weights"].get(k, 0.0)) > 0.01}
            log(f"gen {gen:>3} NEW CHAMPION · margin {rate:+.3f} pts (95% low {lo:+.3f} > 0), "
                f"wins {win_edge:+.4f} over {played} paired games")
            log(f"          drifted: {changed}")
            json.dump({"count": count, "strategy": strategy, "generation": gen, "weights": champion,
                       "margin_edge": rate, "margin_edge_low": lo, "win_edge": win_edge,
                       "games": played, "chooser": _W_CHOOSER,
                       "grade": os.environ.get("FISH_TRAIN_GRADE", "")},
                      open(ck_path, "w"), indent=2)
        else:
            log(f"gen {gen:>3} champion holds · best margin {rate:+.3f} pts (95% low {lo:+.3f}), "
                f"wins {win_edge:+.4f} over {played} paired games")

    log(f"Done. {promotions}/{generations} generations produced a new champion.")
    if promote and promotions:
        backup = f"{fish.BRAIN_PATH}.evolve_backup_{time.strftime('%Y%m%d_%H%M%S')}.json"
        json.dump(fish.load_brain(fish.BRAIN_PATH), open(backup, "w"))
        brain = fish.load_brain(fish.BRAIN_PATH)
        if strategy:
            fish.get_strategy_weights(brain, strategy, maps["weights"]).update(champion)
            fish.save_brain(brain, fish.BRAIN_PATH)
            log(f"Promoted champion into the live brain for strategy {strategy} "
                f"(backup {backup}).")
        else:
            fish.get_count_brain(brain, count)["weights"] = champion
            fish.save_brain(brain, fish.BRAIN_PATH)
            log(f"Promoted champion into the live brain for {count}P (backup {backup}).")
    elif promotions:
        log(f"Champion saved to {ck_path}; live brain untouched (--promote to apply).")
    fh.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--count", type=int, default=4, help="table size to evolve (2-8)")
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
    ap.add_argument("--promote", action="store_true",
                    help="write the final champion into the live brain")
    a = ap.parse_args()
    os.environ["FISH_TRAIN_CHOOSER"] = a.chooser
    if a.grade:
        os.environ["FISH_TRAIN_GRADE"] = fish.normalize_bot_grade(a.grade)
    evolve(count=max(2, min(8, a.count)), generations=a.generations, mutants=a.mutants,
           screen=a.screen_games, confirm=a.confirm_games,
           jobs=a.jobs or (os.cpu_count() or 4), sigma=a.sigma,
           seed=a.seed or random.randrange(1 << 30),
           out_dir=a.out_dir, promote=a.promote, max_confirm=a.max_confirm_games,
           strategy=(a.strategy.strip().lower() or None))


if __name__ == "__main__":
    main()
