#!/usr/bin/env python3
"""What is each PLAN worth? A forced-plan paired A/B, one plan at a time.

    python3 ab_plan.py --games 200 --counts 2,4,6 --plans all
    python3 ab_plan.py --games 400 --counts 4 --plans king_salmon,crustaceans

WHY THIS AND NOT A CENSUS. Counting what the planner chose and what it then
scored cannot measure a plan, because the planner chooses the plan FROM the
hand: a plan that only gets picked when the hand is already good will look
good, and one picked as a last resort will look bad. That is selection bias,
and it is the reason reef_planner.FAMILY_PRIOR exists at all -- the projection
cannot see a plan's ceiling on its own.

What this does instead: one seat is FORCED onto the plan under test, every
other seat plays a normal planner, and the same deal is replayed with that seat
free to choose for itself. The paired difference is therefore

    "what committing to this plan is worth, against what this planner would
     have done with the same cards"

which is exactly the quantity FAMILY_PRIOR is supposed to hold. Reported per
table size, because a plan's worth is not the same at 2 players as at 6, and
in 4P-equivalent points, the units every training log here uses.

Deterministic by default (temperature 0). Grade A samples its move at
temperature 6.0, a deliberate handicap measured at about 18 points, and every
earlier plan measurement was taken through it.

A forced plan is never re-chosen and never gated (reef_planner ~1911), so this
measures gated plans like Game Fish as readily as the rest.
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
from typing import Dict, List, Optional, Tuple

os.environ["PYTHONHASHSEED"] = "0"
os.environ["FISH_PLAN_BY_COUNT"] = "1"

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.chdir(HERE)
import fish_game_all_in_one as fish
import reef_planner as _reef

MARGIN_SCALE = {2: 0.384, 3: 0.647, 4: 1.0, 5: 1.344, 6: 1.778, 7: 2.1, 8: 2.4}

_DB = None
_PARAMS: Optional[Dict[str, float]] = None
_GRADE = ""


def _init(params, grade):
    global _DB, _PARAMS, _GRADE
    _DB = fish.load_card_db()
    fish.register_all_card_abilities(_DB)
    _PARAMS, _GRADE = params, grade


def _pol(gs, ms, p):
    return _reef.choose_action(gs, ms, p, params=dict(_PARAMS))


def _play(task):
    """(arm, seed, seat, count, plan) -> result row.

    arm 1 forces `plan` on `seat`; arm 0 is the identical deal with that seat
    choosing for itself. Every other seat is a free planner in both arms.
    """
    arm, seed, seat, count, plan = task
    random.seed(seed)
    forced = [plan if (arm == 1 and i == seat) else None for i in range(count)]
    try:
        gs, _ms = fish.run_match(
            card_db=_DB, player_names=[f"P{i}" for i in range(count)],
            action_policies=[_pol] * count, seed=seed, max_turns=500,
            human_index=None, verbose=False, verbose_state=False,
            ai_difficulties=[_GRADE] * count,
            online_weights=None, online_state=None, online_state_path=None,
            force_strategies=forced)
        finals = [float(fish.final_points(gs, p)) for p in gs.players]
        actually = str(gs.players[seat].flags.get("_strategy_family", "") or "")
    except Exception as e:
        return (arm, seed, seat, count, plan, None, None, "", repr(e)[:160])
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
    return (arm, seed, seat, count, plan, win, margin, actually, None)


def mean_ci(xs: List[float]) -> Tuple[float, float, float]:
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
    ap.add_argument("--games", type=int, default=200, help="deals per plan PER table size")
    ap.add_argument("--counts", default="2,4,6")
    ap.add_argument("--plans", default="all",
                    help="comma-separated, or 'all' for every plan a bot can commit to")
    ap.add_argument("--grade", default="eugenie_clark")
    ap.add_argument("--temperature", type=float, default=0.0,
                    help="0.0 measures the bot taking the move it ranked best")
    ap.add_argument("--seed", type=int, default=31415)
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 4) - 2))
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    counts = [int(c) for c in a.counts.split(",") if c.strip()]
    if a.plans.strip().lower() == "all":
        plans = list(_reef.STRATEGY_FAMILIES)
    else:
        plans = [p.strip().lower() for p in a.plans.split(",") if p.strip()]
    unknown = [p for p in plans if p not in _reef.STRATEGY_FAMILIES]
    if unknown:
        print(f"not plans: {unknown}\nknown: {list(_reef.STRATEGY_FAMILIES)}")
        return 2

    params = _reef.params_for_grade(a.grade) or dict(_reef.PARAMS)
    params["temperature"] = float(a.temperature)
    # Every plan is offered in the free arm, so the choice the forced arm is
    # being compared against is the planner's real one.
    for knob in _reef.GATED_FAMILIES.values():
        params[knob] = 1.0

    rng = random.Random(a.seed)
    tasks = []
    for plan in plans:
        for count in counts:
            if plan == "invertebrates" and count < fish.INVERTEBRATE_MIN_PLAYERS:
                continue
            for i in range(a.games):
                seed = rng.randrange(1, 2 ** 31 - 1)
                tasks.append((1, seed, i % count, count, plan))
                tasks.append((0, seed, i % count, count, plan))
    rng.shuffle(tasks)
    print(f"{len(tasks)} games: {len(plans)} plan(s) x {counts} x {a.games} deals x 2 arms")
    print(f"grade {a.grade}, temperature {a.temperature}, {a.jobs} jobs", flush=True)

    t0 = time.time()
    res: Dict[Tuple, Dict[int, Tuple[float, float]]] = {}
    kept = {}
    errs: List[str] = []
    with mp.Pool(a.jobs, initializer=_init, initargs=(params, a.grade)) as pool:
        done = 0
        for out in pool.imap_unordered(_play, tasks, chunksize=1):
            arm, seed, seat, count, plan, win, margin, actually, err = out
            done += 1
            if err:
                errs.append(err)
                continue
            res.setdefault((plan, seed, seat, count), {})[arm] = (win, margin)
            if arm == 1:
                k = (plan, count)
                kept.setdefault(k, [0, 0])
                kept[k][1] += 1
                if actually == plan:
                    kept[k][0] += 1
            if done % 500 == 0:
                el = time.time() - t0
                print(f"  {done}/{len(tasks)}  {el:.0f}s  {done/max(el,1e-9):.1f}/s "
                      f"(eta {(len(tasks)-done)/max(done/max(el,1e-9),1e-9)/60:.0f} min)",
                      flush=True)

    per: Dict[str, Dict[int, List[float]]] = {}
    wins: Dict[str, Dict[int, List[float]]] = {}
    for (plan, seed, seat, count), arms in res.items():
        if 0 not in arms or 1 not in arms:
            continue
        fw, fm = arms[1]
        bw, bm = arms[0]
        per.setdefault(plan, {}).setdefault(count, []).append(fm - bm)
        wins.setdefault(plan, {}).setdefault(count, []).append(fw - bw)

    print(f"\n=== what each plan is worth, against the planner's own choice ===")
    print(f"grade {a.grade} temperature {a.temperature} · {time.time()-t0:.0f}s · "
          f"errors {len(errs)}\n")
    header = f"{'plan':22s}" + "".join(f"{str(c)+'P':>18s}" for c in counts) + f"{'ALL':>20s}"
    print(header)
    print("-" * len(header))
    summary: Dict[str, Dict[str, object]] = {}
    for plan in plans:
        if plan not in per:
            continue
        row = f"{plan:22s}"
        alld: List[float] = []
        cells = {}
        for c in counts:
            d = per[plan].get(c, [])
            alld += d
            if not d:
                row += f"{'-':>18s}"
                continue
            m, lo, hi = mean_ci(d)
            cells[str(c)] = {"mean": m, "ci": [lo, hi], "n": len(d)}
            mark = "+" if lo > 0 else ("-" if hi < 0 else " ")
            row += f"{m:+8.2f}+-{(hi-lo)/2:5.2f}{mark}"
        m, lo, hi = mean_ci(alld)
        verdict = "BETTER" if lo > 0 else ("WORSE" if hi < 0 else "no proof")
        row += f"{m:+9.2f} [{lo:+6.2f},{hi:+6.2f}]"
        print(row)
        wd = [x for c in counts for x in wins[plan].get(c, [])]
        wm, wlo, whi = mean_ci(wd)
        summary[plan] = {"by_count": cells, "all": {"mean": m, "ci": [lo, hi],
                                                    "n": len(alld)},
                         "win_delta": {"mean": wm, "ci": [wlo, whi]},
                         "verdict": verdict,
                         "stuck": {f"{c}P": kept.get((plan, c), [0, 0]) for c in counts}}
    print("\n'+' means the interval is above zero, '-' below. Both are a real finding;")
    print("a blank is 'no proof', which at these sample sizes is the common answer.")
    print("\nwin-rate change from committing to the plan (same pairing):")
    for plan in plans:
        if plan not in summary:
            continue
        w = summary[plan]["win_delta"]
        lo, hi = w["ci"]
        print(f"   {plan:22s} {w['mean']:+7.4f}  [{lo:+7.4f},{hi:+7.4f}]")
    print("\nhow often the forced seat really ended on the plan (forced/total):")
    for plan in plans:
        if plan not in summary:
            continue
        st = summary[plan]["stuck"]
        print(f"   {plan:22s} " + "  ".join(f"{k} {v[0]}/{v[1]}" for k, v in st.items()))
    if errs:
        print(f"\nfirst errors: {errs[:3]}")
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump({"grade": a.grade, "temperature": a.temperature,
                       "counts": counts, "games": a.games, "plans": plans,
                       "when": time.strftime("%F %T"), "summary": summary,
                       "errors": len(errs)}, f, indent=1)
        print(f"\nwrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
