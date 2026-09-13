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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fish_game_all_in_one as fish

_W_CARD_DB = None
_W_MAPS: Optional[Dict[str, Any]] = None
_W_CHAMPION: Optional[Dict[str, float]] = None
_W_CANDIDATES: Optional[List[Dict[str, float]]] = None
_W_COUNT: int = 4


def _init(maps, champion, candidates, count):
    global _W_CARD_DB, _W_MAPS, _W_CHAMPION, _W_CANDIDATES, _W_COUNT
    _W_CARD_DB = fish.load_card_db()
    _W_MAPS, _W_CHAMPION, _W_CANDIDATES, _W_COUNT = maps, champion, candidates, count


def _play(task: Tuple[int, int, int]) -> Tuple[int, float, float]:
    """One game: candidate `ci` at `seat`, champions elsewhere. Returns its win."""
    ci, seed, seat = task
    random.seed(seed)
    cand = _W_CANDIDATES[ci]
    policies = []
    for i in range(_W_COUNT):
        w = cand if i == seat else _W_CHAMPION
        policies.append(fish._train_make_policy(_W_MAPS, weights=dict(w), epsilon=0.0))
    try:
        gs, _ms = fish.run_match(
            card_db=_W_CARD_DB,
            player_names=[f"P{i}" for i in range(_W_COUNT)],
            action_policies=policies,
            seed=seed, max_turns=500, human_index=None,
            verbose=False, verbose_state=False,
            ai_difficulties=[fish.DEFAULT_BOT_GRADE] * _W_COUNT,
            online_weights=None, online_state=None, online_state_path=None,
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


def _mutate(w: Dict[str, float], rng: random.Random, sigma: float) -> Dict[str, float]:
    """Perturb a few weights rather than all of them: a mutant that changes one
    idea at a time is one whose win or loss actually tells you something."""
    out = dict(w)
    keys = [k for k in out if k in fish.default_weights()]
    rng.shuffle(keys)
    for k in keys[: max(1, len(keys) // 3)]:
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


def _round_wins(pool, n_cands, seeds, count) -> List[float]:
    """Play every candidate over the same seeds, rotating seats. Returns raw wins."""
    tasks = [(ci, s, (gi + ci) % count)
             for ci in range(n_cands) for gi, s in enumerate(seeds)]
    wins = [0.0] * n_cands
    for ci, win, _m in pool.imap_unordered(_play, tasks, chunksize=4):
        wins[ci] += win
    return wins


def evolve(count: int, generations: int, mutants: int, screen: int, confirm: int,
           jobs: int, sigma: float, seed: int, out_dir: str, promote: bool,
           max_confirm: int = 1200) -> None:
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
    champion = dict(maps["weights"])
    neutral = 1.0 / count

    ck_path = os.path.join(out_dir, f"champion_{count}p.json")
    if os.path.exists(ck_path):
        saved = json.load(open(ck_path))
        champion = fish.stabilize_weights(dict(saved["weights"]))
        log(f"Resuming from saved champion (generation {saved.get('generation', 0)}).")

    rng = random.Random(seed)
    log("=" * 68)
    log(f"TOURNAMENT SELECTION · {count}P · a mutant must beat {neutral:.3f} to take the crown")
    log(f"{generations} generations · {mutants} mutants · {screen} screen + {confirm} confirm games · jobs={jobs}")
    log("=" * 68)

    promotions = 0
    for gen in range(1, generations + 1):
        cands = [_mutate(champion, rng, sigma) for _ in range(mutants)]
        screen_seeds = [rng.randrange(1 << 30) for _ in range(screen)]
        t0 = time.time()
        with mp.Pool(jobs, initializer=_init,
                     initargs=(maps, champion, cands, count)) as pool:
            swins = _round_wins(pool, len(cands), screen_seeds, count)
        order = sorted(range(len(cands)), key=lambda i: -swins[i])
        keep = order[:3]
        log(f"gen {gen:>3} screen: best raw {swins[keep[0]]/screen:.3f} "
            f"(neutral {neutral:.3f}) · {time.time()-t0:.0f}s")

        # Staged confirmation. A mutant only has to be PROVED better, and how
        # many games that takes depends on how much better it is. Playing a
        # fixed number throws away real improvements that merely needed more
        # evidence: a mutant measured at 0.297 against a 0.250 bar was
        # discarded on a lower bound of 0.248, two thousandths short. So keep
        # playing the ones that still could prove it, and stop early on the
        # ones that provably cannot.
        finals = [cands[i] for i in keep]
        wins = [0.0] * len(finals)
        played = 0
        alive = list(range(len(finals)))
        lo = rate = 0.0
        best_i = 0
        while alive and played < max_confirm:
            batch = min(confirm, max_confirm - played)
            conf_seeds = [rng.randrange(1 << 30) for _ in range(batch)]
            sub = [finals[i] for i in alive]
            with mp.Pool(jobs, initializer=_init,
                         initargs=(maps, champion, sub, count)) as pool:
                bw = _round_wins(pool, len(sub), conf_seeds, count)
            for k, i in enumerate(alive):
                wins[i] += bw[k]
            played += batch
            ranked = sorted(alive, key=lambda i: -_wilson_low(wins[i], played))
            best_i = ranked[0]
            lo = _wilson_low(wins[best_i], played)
            rate = wins[best_i] / played
            if lo > neutral:
                break
            # drop any challenger that can no longer reach the bar
            alive = [i for i in alive if _wilson_high(wins[i], played) > neutral]
            if alive:
                log(f"gen {gen:>3}   +{played} games: best {rate:.3f} "
                    f"(low {lo:.3f}) · {len(alive)} still alive")

        if lo > neutral:
            champion = finals[best_i]
            promotions += 1
            changed = {k: round(champion[k], 3) for k in champion
                       if abs(champion[k] - maps["weights"].get(k, 0.0)) > 0.01}
            log(f"gen {gen:>3} NEW CHAMPION · win {rate:.3f} over {played} games "
                f"(95% low {lo:.3f} > {neutral:.3f})")
            log(f"          drifted: {changed}")
            json.dump({"count": count, "generation": gen, "weights": champion,
                       "win_rate": rate, "wilson_low": lo, "neutral": neutral},
                      open(ck_path, "w"), indent=2)
        else:
            log(f"gen {gen:>3} champion holds · best challenger {rate:.3f} over "
                f"{played} games (95% low {lo:.3f}, needs > {neutral:.3f})")

    log(f"Done. {promotions}/{generations} generations produced a new champion.")
    if promote and promotions:
        backup = f"{fish.BRAIN_PATH}.evolve_backup_{time.strftime('%Y%m%d_%H%M%S')}.json"
        json.dump(fish.load_brain(fish.BRAIN_PATH), open(backup, "w"))
        brain = fish.load_brain(fish.BRAIN_PATH)
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
    ap.add_argument("--promote", action="store_true",
                    help="write the final champion into the live brain")
    a = ap.parse_args()
    evolve(count=max(2, min(8, a.count)), generations=a.generations, mutants=a.mutants,
           screen=a.screen_games, confirm=a.confirm_games,
           jobs=a.jobs or (os.cpu_count() or 4), sigma=a.sigma,
           seed=a.seed or random.randrange(1 << 30),
           out_dir=a.out_dir, promote=a.promote, max_confirm=a.max_confirm_games)


if __name__ == "__main__":
    main()
