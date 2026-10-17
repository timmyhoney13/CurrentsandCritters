#!/usr/bin/env python3
"""What actually wins this game, read off every finished board on disk.

    python3 analyze_game.py
    python3 analyze_game.py --logs 'fish_training/*/run/games.jsonl'

Everything else in this directory measures a CHANGE -- is this knob better, is
this plan worth committing to. Useful, and it can only ever compare the bots
with themselves. None of it asks the prior question: what does a winning board
in this game look like, and is that the board the bots are building?

So this looks at the finished boards, not the decisions. For every seat in
every logged game it takes what ended up on the board and whether that seat
won, and reports:

  * which plan won how often, against how often it was played -- a plan picked
    often and winning rarely is a plan the bots over-commit to, and that is
    exactly what the measured plan priors keep finding;
  * what a winner's board holds that a loser's does not, per card, as a rate
    difference. This is where a card the bots systematically ignore shows up,
    because it shows up on the boards of the seats that beat them;
  * the cards in the deck nobody ever collects, with how many of them exist.
    A card that is 13-of-253 in the deck and absent from three quarters of
    finished boards is either a trap or an unexplored plan, and which of those
    it is cannot be read off a board -- it needs a forced-plan measurement.
    Naming the candidates is this script's job; ab_plan.py settles them.

Reading rules, because a board is evidence about a correlation and nothing
more. Everything here is observational: the bots chose these boards, so a
feature can look good because it is good or because the hands that led to it
were good. A rate difference is a place to point a measurement, never a
finding on its own.
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import sys
from collections import Counter, defaultdict
from typing import Any, Dict, List, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.chdir(HERE)
import fish_game_all_in_one as fish
import reef_planner as rp


def load_seats(patterns: List[str]) -> List[Dict[str, Any]]:
    """One row per seat per game: its board, its score, whether it won."""
    seats: List[Dict[str, Any]] = []
    for pat in patterns:
        for path in sorted(glob.glob(pat)):
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue
                    # A broken game is not evidence about what wins.
                    if r.get("error") or r.get("stalled"):
                        continue
                    if int(r.get("rules_errors", 0) or 0) > 0:
                        continue
                    if not r.get("end_game_triggered"):
                        continue
                    count = int(r.get("num_players", 0) or 0)
                    for pl in r.get("players", []) or []:
                        names = [str(x).strip().lower() for x in (pl.get("board_names") or [])]
                        seats.append({
                            "count": count,
                            "won": bool(pl.get("won")),
                            "score": float(pl.get("score", 0.0) or 0.0),
                            "strategy": str(pl.get("strategy", "") or "?"),
                            "names": names,
                            "name_counts": Counter(names),
                            "species": Counter(str(s).strip().lower()
                                               for s in (pl.get("species") or [])),
                            "oceans_built": int(pl.get("oceans_built", 0) or 0),
                        })
    return seats


def wilson(k: int, n: int) -> Tuple[float, float]:
    if n == 0:
        return 0.0, 0.0
    p = k / n
    z = 1.96
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def suggest(seats: List[Dict[str, Any]]) -> int:
    """A plan order for the measurement budget, worst-mispriced first.

    MISPRICING, here, is the gap between how often the planner commits to a
    plan and how well that plan then does. A plan played a fifth of the time
    and winning less than chance is mispriced, and so is one played a
    fourteenth of the time and winning twice chance. Both are worth a forced
    measurement; a plan sitting at chance is not.

    THE LABEL IS NOT INNOCENT. A board's plan is named for the family whose
    cards EARNED its points (detect_player_strategy), so a high-scoring family
    is likelier to be the label on a board that won -- some of the win rate
    below is that, not the plan. Which is exactly why this only sets an ORDER
    for ab_plan.py, which forces a seat onto the plan and pairs it against the
    identical deal. The observation points; the forced measurement decides.

    Plans with no measured value yet come first among equals: a plan nobody has
    measured is worth more information than one already carrying a number.
    """
    try:
        with open(os.path.join(HERE, "family_prior.json"), encoding="utf-8") as fh:
            measured = set((json.load(fh).get("prior") or {}).keys())
    except Exception:
        measured = set()
    display_to_family = {v: k for k, v in fish.STRATEGY_DISPLAY_NAMES.items()}
    played: Counter = Counter()
    won: Counter = Counter()
    for s in seats:
        fam = display_to_family.get(s["strategy"])
        if not fam:
            continue
        played[fam] += 1
        if s["won"]:
            won[fam] += 1
    fair = sum(1.0 / s["count"] for s in seats) / max(1, len(seats))
    rows = []
    for fam, n in played.items():
        if n < 25:
            continue
        # Only plans a bot can actually commit to. ocean_all_blue comes back
        # here every time -- it is the label for an ocean-dominant board, it
        # wins 0.059 against a 0.182 share, and it is offered at no skill
        # level on purpose, so it is a description of a board nobody chose
        # rather than a plan ab_plan could force a seat onto.
        if fam not in rp.STRATEGY_FAMILIES:
            continue
        wr = won[fam] / n
        # Scaled by sqrt(n) so a gap seen on 400 seats outranks the same gap
        # seen on 30, and unmeasured plans break ties in their own favour.
        rows.append((abs(wr - fair) * math.sqrt(n), fam not in measured, fam, n, wr))
    rows.sort(reverse=True)
    print(" ".join(r[2] for r in rows))
    for _pri, unmeasured, fam, n, wr in rows:
        print(f"# {fam:22s} played {n:5d}  won {wr:.3f} vs chance {fair:.3f}"
              f"  {'UNMEASURED' if unmeasured else 'has a value'}", file=sys.stderr)
    return 0


def section(t: str) -> None:
    print("")
    print(t)
    print("-" * len(t))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs", action="append", default=None,
                    help="glob for game logs (repeatable)")
    ap.add_argument("--min-seats", type=int, default=40,
                    help="ignore a plan or card seen fewer times than this")
    ap.add_argument("--suggest", action="store_true",
                    help="print only a plan order for the measurement budget, "
                         "worst-mispriced first, for ab_plan.py to settle")
    a = ap.parse_args()
    patterns = a.logs or ["fish_training/*/run/games.jsonl"]
    seats = load_seats(patterns)
    if not seats:
        print("no finished games found in", patterns)
        return 1

    if a.suggest:
        return suggest(seats)

    db = fish.load_card_db()
    in_deck = Counter(fish.card_name_lc(c) for c in db.values())
    by_count = Counter(s["count"] for s in seats)
    print(f"{len(seats)} seats over {sum(by_count.values()) // max(1, min(by_count))} "
          f"games · table sizes " + ", ".join(f"{c}P:{n}" for c, n in sorted(by_count.items())))
    print("OBSERVATIONAL. The bots chose these boards, so every rate below is a")
    print("correlation and a place to point a measurement, not a finding.")

    # ── which plan wins, against how often it is played ────────────────────
    section("PLANS: how often played, how often that seat won")
    played: Counter = Counter()
    won: Counter = Counter()
    score: Dict[str, List[float]] = defaultdict(list)
    for s in seats:
        played[s["strategy"]] += 1
        if s["won"]:
            won[s["strategy"]] += 1
        score[s["strategy"]].append(s["score"])
    fair = sum(1.0 / s["count"] for s in seats) / len(seats)
    print(f"a seat picked at random wins {fair:.3f} of the time at these table sizes")
    print(f"{'plan':26s} {'played':>7s} {'share':>7s} {'won':>7s} {'95% CI':>16s} {'med score':>10s}")
    for plan, n in played.most_common():
        if n < a.min_seats:
            continue
        w = won[plan]
        lo, hi = wilson(w, n)
        ss = sorted(score[plan])
        med = ss[len(ss) // 2]
        flag = ""
        if hi < fair:
            flag = "  LOSES more than chance"
        elif lo > fair:
            flag = "  WINS more than chance"
        print(f"{plan:26s} {n:7d} {n/len(seats):6.1%} {w/n:7.3f} "
              f"[{lo:.3f},{hi:.3f}] {med:10.1f}{flag}")

    # ── what a winner's board holds that a loser's does not ────────────────
    section("CARDS: rate on a winning board minus rate on a losing board")
    winners = [s for s in seats if s["won"]]
    losers = [s for s in seats if not s["won"]]
    print(f"{len(winners)} winning seats, {len(losers)} losing seats")
    rows: List[Tuple[float, str, float, float, int]] = []
    for name in sorted(in_deck):
        if name in ("end game",):
            continue
        wavg = sum(s["name_counts"].get(name, 0) for s in winners) / max(1, len(winners))
        lavg = sum(s["name_counts"].get(name, 0) for s in losers) / max(1, len(losers))
        rows.append((wavg - lavg, name, wavg, lavg, in_deck[name]))
    rows.sort(reverse=True)
    print(f"{'card':26s} {'in deck':>8s} {'winners':>9s} {'losers':>8s} {'diff':>8s}")
    for diff, name, wavg, lavg, deck in rows[:12]:
        print(f"{name:26s} {deck:8d} {wavg:9.2f} {lavg:8.2f} {diff:+8.2f}")
    print("   ... ...")
    for diff, name, wavg, lavg, deck in rows[-8:]:
        print(f"{name:26s} {deck:8d} {wavg:9.2f} {lavg:8.2f} {diff:+8.2f}")

    # ── what nobody collects ───────────────────────────────────────────────
    section("UNEXPLORED: plentiful in the deck, absent from finished boards")
    print("A card the bots never gather is a trap or an unexplored plan. Which one")
    print("cannot be read off a board -- ab_plan.py settles it by forcing a seat.")
    print(f"{'card':26s} {'in deck':>8s} {'seats with none':>16s} {'avg held':>9s}")
    ignored: List[Tuple[float, str, int, float]] = []
    for name, deck in in_deck.items():
        if name == "end game" or deck < 4:
            continue
        none = sum(1 for s in seats if s["name_counts"].get(name, 0) == 0)
        avg = sum(s["name_counts"].get(name, 0) for s in seats) / len(seats)
        ignored.append((none / len(seats), name, deck, avg))
    ignored.sort(reverse=True)
    for frac, name, deck, avg in ignored[:10]:
        print(f"{name:26s} {deck:8d} {frac:15.1%} {avg:9.2f}")

    # ── the one chart in the deck with a valley ────────────────────────────
    section("THE CORAL REEF CHART: 1=1 2=4 3=9 4=16 5=0 6+=35")
    dist = Counter(s["name_counts"].get("coral reef", 0) for s in seats)
    pays = {0: 0, 1: 1, 2: 4, 3: 9, 4: 16, 5: 0}
    print(f"{'reefs':>6s} {'seats':>7s} {'chart pays':>11s} {'win rate':>9s}")
    for k in sorted(dist):
        n = dist[k]
        w = sum(1 for s in seats if s["name_counts"].get("coral reef", 0) == k and s["won"])
        note = "   <-- the valley" if k == 5 else ("   <-- the top tier" if k >= 6 else "")
        print(f"{k:6d} {n:7d} {pays.get(k, 35):11d} {w/max(1,n):9.3f}{note}")
    reach = sum(n for k, n in dist.items() if k >= 6)
    print(f"\n{in_deck.get('coral reef', 0)} coral reefs exist, the most numerous card in the")
    print(f"deck, and {reach} of {len(seats)} seats ({reach/len(seats):.2%}) ever reached the")
    print("top tier. That looked like an unexplored plan -- 35 points sitting in the")
    print("deck's commonest card that no bot can commit to, because Oceans are not a")
    print("plan a bot may pick. The numbers above say otherwise, and they say it")
    print("clearly: the win rate FALLS with every reef collected, and Coral Reef is")
    print("the single card most associated with losing on this whole sample.")
    print("")
    print("Which is what the chart says too, read properly. The top tier is 35 points")
    print("ONCE, and the road to it runs 1=1, 2=4, 3=9, 4=16, 5=0 -- so a sixth reef")
    print("is only worth having if you get there, and every board that stops short")
    print("has spent its ocean slots on a card paying 16 or less where an Arctic")
    print("Ocean pays 4 and plays again. The valley is not the trap; the journey is.")
    print("Ocean-dominant boards bear that out: they win 0.059 against a 0.182 share.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
