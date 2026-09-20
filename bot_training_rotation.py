#!/usr/bin/env python3
"""Train every strategy, at every table size, until it stops getting better.

    python3 bot_training_rotation.py            # runs until stopped
    pkill -f bot_training_rotation              # stop it

WHAT IT TRAINS
--------------
Thirteen strategies and the Reef Planner's own knobs. Every strategy is trained
across 2P through 6P in one run (bot_evolve --counts), because the bots now have
weights that read the table size -- see fish_game_all_in_one.table_clock -- and a
weight that reads the table size can only be measured by playing several.
Invertebrates trains at 5P and 6P alone; it is not offered at smaller tables.

WHAT "AS GOOD AS IT GETS" MEANS HERE
------------------------------------
There is no point at which a bot is provably perfect, so this uses the only
honest version of the claim: a strategy is SETTLED when no mutant can be shown
to beat it even when we spend a great deal of evidence looking. That has to be
earned at a rising bar, or "settled" just means "we did not look hard enough":

    tier 0   8 mutants ·  40 screen ·  150/450 confirming games
    tier 1   8 mutants ·  60 screen ·  250/900
    tier 2  10 mutants ·  80 screen ·  400/1600

A visit that crowns a champion drops that strategy back to tier 0: there is
clearly more to find, and it is cheaper to find it there. A visit that crowns
nothing moves it up a tier, so the next look is a harder test on more games.
Failing at tier 2 marks it settled, and a settled strategy is only re-checked
every fourth cycle -- not never, because the field it is measured against keeps
improving, and a plan that could not beat last week's table may be beatable now.

Nothing here decides anything by itself. Every champion is crowned by
bot_evolve's tournament selection: paired deals, the same seats, the same table
sizes, a 95% lower bound above zero on the margin, and wins that are not worse.
A champion can only ever be replaced by something measured to be better than it,
so a strategy cannot go backwards across a night of this.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "fish_training", "evolve")
STATE_PATH = os.path.join(OUT_DIR, "rotation_state.json")
LOG_PATH = os.path.join(OUT_DIR, "rotation.log")

# The strategies a bot can actually be asked to commit to. ocean_all_blue is
# left out on purpose: the planner drops it from its own list too, because it
# is a description of a board, not a plan a player commits to at the start.
MAINS = ["mammals", "yellowfin_tuna", "birds_of_a_feather", "crustaceans",
         "baitfish_barrage", "cephalopods", "coral", "king_salmon",
         "goby_moon_shot", "invertebrates"]
COMBOS = ["birds_crustaceans", "coral_cephalopods", "birds_coral"]

# The grade the weights are trained at. B is the lowest grade that plays with a
# strategy's trained weights, and every grade above it reuses those same weights
# with more search on top, so this is the cheapest place to train what S++ plays
# with. The planner knobs are trained at the cheapest planner grade for exactly
# the same reason.
TRAIN_GRADE = "william_beebe"
PLANNER_GRADE = "eugenie_clark"

COUNTS = "2,3,4,5,6"
JOBS = max(2, (os.cpu_count() or 4) - 1)

# mutants, screen games, confirming batch, cap on confirming games
WEIGHT_TIERS = [(8, 40, 150, 450), (8, 60, 250, 900), (10, 80, 400, 1600)]
# The planner searches for every move, so its games cost many times a weighted
# chooser's. Same ladder, sized for what it can actually play in a night.
PLANNER_TIERS = [(6, 24, 80, 240), (6, 36, 140, 420), (8, 48, 200, 600)]
SETTLED_TIER = len(WEIGHT_TIERS)          # one past the top = nothing left to find
SETTLED_RECHECK_CYCLES = 4
GENERATIONS = 2

_stop = False


def _on_signal(signum, _frame):
    global _stop
    _stop = True
    log(f"signal {signum}: finishing the run in progress, then stopping.")


def log(msg: str) -> None:
    line = f"[{time.strftime('%F %T')}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def load_state() -> Dict[str, Any]:
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as fh:
            got = json.load(fh)
        if isinstance(got, dict) and isinstance(got.get("cells"), dict):
            return got
    except Exception:
        pass
    return {"cycle": 0, "cells": {}}


def save_state(state: Dict[str, Any]) -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, sort_keys=True)
    os.replace(tmp, STATE_PATH)        # never leave a half-written state file


def cell(state: Dict[str, Any], name: str) -> Dict[str, Any]:
    got = state["cells"].get(name)
    if not isinstance(got, dict):
        got = {"tier": 0, "visits": 0, "promotions": 0, "barren_visits": 0,
               "settled": False, "last_cycle": -1}
        state["cells"][name] = got
    return got


def promotions_from(output: str) -> int:
    """How many champions that run crowned, off bot_evolve's closing line."""
    for line in reversed(output.strip().splitlines()):
        if line.strip().startswith("Done.") or "generations produced a new champion" in line:
            for token in line.split():
                if "/" in token:
                    head = token.split("/")[0].rsplit(" ", 1)[-1]
                    try:
                        return int(head)
                    except ValueError:
                        continue
    return 0


def run_cell(name: str, tier: int, planner: bool) -> Optional[int]:
    """One visit. Returns how many champions it crowned, or None if it failed."""
    mutants, screen, confirm, cap = (PLANNER_TIERS if planner else WEIGHT_TIERS)[tier]
    cmd = [sys.executable, "bot_evolve.py", "--counts", COUNTS,
           "--generations", str(GENERATIONS), "--mutants", str(mutants),
           "--screen-games", str(screen), "--confirm-games", str(confirm),
           "--max-confirm-games", str(cap), "--jobs", str(JOBS),
           "--out-dir", OUT_DIR]
    if planner:
        cmd += ["--planner", PLANNER_GRADE]
    else:
        cmd += ["--strategy", name, "--chooser", "live", "--grade", TRAIN_GRADE]
    console = os.path.join(OUT_DIR, f"console_{name}.log")
    log(f"{name}: tier {tier} · {mutants} mutants · {screen} screen · {confirm}/{cap} confirming")
    started = time.time()
    try:
        with open(console, "a", encoding="utf-8") as fh:
            fh.write(f"\n===== {time.strftime('%F %T')} · tier {tier} =====\n")
            fh.flush()
            proc = subprocess.run(cmd, cwd=HERE, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True)
            fh.write(proc.stdout or "")
    except Exception as exc:
        log(f"{name}: run failed to start ({exc})")
        return None
    mins = (time.time() - started) / 60.0
    if proc.returncode != 0:
        log(f"{name}: exited {proc.returncode} after {mins:.0f} min — see {console}")
        return None
    crowned = promotions_from(proc.stdout or "")
    log(f"{name}: {crowned} new champion(s) in {mins:.0f} min")
    return crowned


def promote() -> None:
    """Copy this cycle's champions into the brain the live game reads."""
    try:
        proc = subprocess.run([sys.executable, "promote_champions.py", "--write"],
                              cwd=HERE, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True)
    except Exception as exc:
        log(f"promote: failed to run ({exc})")
        return
    tail = (proc.stdout or "").strip().splitlines()
    log("promote: " + (tail[-1].strip() if tail else f"exit {proc.returncode}"))


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)
    state = load_state()
    log("=" * 70)
    log(f"ladder training · strategies at {COUNTS} players · {JOBS} jobs · "
        f"weights at {TRAIN_GRADE}, planner at {PLANNER_GRADE}")
    log("=" * 70)

    while not _stop:
        state["cycle"] = int(state.get("cycle", 0)) + 1
        cycle = state["cycle"]
        log(f"===== cycle {cycle} =====")
        # Strategies with no champion yet first: they have the most to gain, and
        # a combo seeded from an untrained half inherits nothing worth having.
        fresh = [s for s in MAINS + COMBOS
                 if not os.path.exists(os.path.join(OUT_DIR, f"champion_{s}.json"))]
        order = fresh + MAINS + COMBOS + ["planner"]

        for name in order:
            if _stop:
                break
            planner = (name == "planner")
            c = cell(state, name)
            if c["settled"] and (cycle - int(c.get("settled_cycle", 0))) % SETTLED_RECHECK_CYCLES:
                continue
            tier = min(int(c["tier"]), SETTLED_TIER - 1)
            crowned = run_cell(name, tier, planner)
            if crowned is None:
                save_state(state)
                continue
            c["visits"] = int(c["visits"]) + 1
            c["last_cycle"] = cycle
            if crowned > 0:
                # There is more here, and tier 0 is the cheapest place to find it.
                c["promotions"] = int(c["promotions"]) + crowned
                c["tier"] = 0
                c["barren_visits"] = 0
                if c["settled"]:
                    log(f"{name}: settled no longer — the field moved and it found "
                        f"{crowned} more")
                c["settled"] = False
            else:
                c["barren_visits"] = int(c["barren_visits"]) + 1
                c["tier"] = int(c["tier"]) + 1
                if c["tier"] >= SETTLED_TIER and not c["settled"]:
                    c["settled"] = True
                    c["settled_cycle"] = cycle
                    log(f"{name}: SETTLED — nothing beat it at the top bar "
                        f"({PLANNER_TIERS[-1][3] if planner else WEIGHT_TIERS[-1][3]} "
                        f"confirming games). Re-checked every "
                        f"{SETTLED_RECHECK_CYCLES} cycles.")
            save_state(state)

        # Put the cycle's champions where the game reads them. Without this the
        # whole night is a set of JSON files nobody plays against: from grade B
        # up a bot reads brain["by_strategy"], and only this writes it.
        promote()

        settled = sorted(n for n, v in state["cells"].items() if v.get("settled"))
        log(f"cycle {cycle} done · settled: {', '.join(settled) if settled else 'none'}")

    log("stopped.")


if __name__ == "__main__":
    main()
