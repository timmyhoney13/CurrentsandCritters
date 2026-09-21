#!/usr/bin/env python3
"""Train every strategy, at every table size, until it stops getting better.

    python3 bot_training_rotation.py            # runs until stopped
    pkill -f bot_training_rotation              # stop it

WHAT IT TRAINS
--------------
The ten strategies a bot can actually commit to, and the Reef Planner's own
knobs. Every strategy is trained
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

# THE COMBOS ARE NOT TRAINED, and that is not an oversight.
#
# B-Lob, B-Coral and Coral / Cephalopods are names for a finished board, not
# plans a bot can commit to. Every live path that assigns a bot its plan
# excludes them: reef_planner.STRATEGY_FAMILIES has the ten mains and nothing
# else, so neither choose_family nor reconsider_family can reach one, and
# fish.strategies_allowed_for_skill leaves them out at all four skill levels, so
# neither can the opening-hand assignment. The only thing that can set a combo
# label is _force_strategy_family -- which is set by nothing but the trainer.
#
# So a combo champion is a weight vector no bot can ever look up. The rotation
# this replaced spent about fifteen hours a cycle producing them:
# birds_crustaceans alone took eleven and a half. That time goes to the ten real
# strategies and to the planner instead.
#
# fish_game_all_in_one keeps their profiles, and should: they are what lets a
# recap call a board "B-Lob" instead of guessing. Naming a board is not the same
# as choosing a plan.
COMBOS: List[str] = []

# WHICH BRAIN EACH RUNG ACTUALLY PLAYS WITH. This decides how the night is
# spent, and it is not what it was when the Reef Planner landed:
#
#   F E D C   the shared weight vector, under the grade ladder's handicaps
#   B         its strategy's own trained weights   <- champion_<strategy>.json
#   A S S+ S++ GS   the Reef Planner's knobs       <- champion_planner.json
#
# Per-strategy weights therefore improve exactly ONE rung. Not the default grade
# a player meets (C), and none of the five at the top. Everything from Eugenie
# Clark up goes through reef_planner.choose_action, which never reads a weight
# vector at all -- it plans the board and scores it with the real scorer.
#
# So the old rationale here ("weights trained at B are the ones S++ plays with")
# was true before the planner existed and is false now, and following it spent
# thirteen cells in sixteen on a single rung. The planner takes a turn every
# other strategy instead, and every so often that turn is taken at S++ itself
# rather than at the cheapest planner grade, because S++ is the rung that is
# meant to be the best thing in the game.
TRAIN_GRADE = "william_beebe"
PLANNER_GRADE = "eugenie_clark"      # A: cheapest planner grade, for volume
TOP_PLANNER_GRADE = "charles_darwin" # S++: the rung this is all for

COUNTS = "2,3,4,5,6"
JOBS = max(2, (os.cpu_count() or 4) - 1)

# mutants, screen games, confirming batch, cap on confirming games
WEIGHT_TIERS = [(8, 40, 150, 450), (8, 60, 250, 900), (10, 80, 400, 1600)]
# The same ladder as the weights, and for a plain reason: this is the brain five
# of the ten rungs play with, including every rung at the top, so it has earned
# at least the evidence a single rung gets.
#
# It was sized smaller on the assumption that a planner game costs many times a
# weighted chooser's. Measured on the real run, it does not -- at Eugenie
# Clark's LITE search a planner cell finished in 13 and 18 minutes against 33 to
# 42 for a strategy cell. So the smaller budget was not buying time, it was just
# capping the evidence at 240 confirming games where the strategies get 450, and
# an edge too small to prove in 240 games is exactly the size of edge that a
# well-trodden knob set still has left in it.
PLANNER_TIERS = [(8, 40, 150, 450), (8, 60, 250, 900), (10, 80, 400, 1600)]
# S++ looks at two worlds a move and confirms its leading six in eight more, so
# its games really do cost several times an A game's. It is here to check and
# refine what the cheap grade found rather than to explore, so its ladder is
# shorter -- but not as short as it was.
#
# The first S++ cell ran at 12 screening deals and 120 confirming games. Against
# the paired spread the run has actually shown (about 13.8 points a deal), 120
# games can only prove an edge of 2.5 points. No planner knob is worth two and a
# half points a deal, so that cell could not have crowned anything whatever it
# found: it was not a cheap measurement, it was no measurement.
TOP_PLANNER_TIERS = [(6, 24, 80, 240), (6, 36, 140, 420), (8, 48, 200, 600)]
SETTLED_TIER = len(WEIGHT_TIERS)          # one past the top = nothing left to find
SETTLED_RECHECK_CYCLES = 4
GENERATIONS = 2
# A run that produces no result at all is a broken trainer, not a finding about
# the bots. Wait before trying the next one, and give up rather than spin.
FAILURE_BACKOFF_S = 60
MAX_CONSECUTIVE_FAILURES = 6

# The longest one cell may run before it is stopped and the cycle moves on.
#
# Without this, one slow cell can eat a whole night on its own and every other
# strategy waits behind it. An S++ planner game looks at two worlds a move and
# confirms its leading six in eight more, so it costs many times a weighted
# chooser's game, and its cell is the one most likely to run long.
#
# Stopping a cell early costs nothing that was earned: a champion is written to
# disk the moment it is crowned, atomically, so whatever the cell had already
# proved is kept. Only the generation in progress is lost, and a generation in
# progress has proved nothing yet by definition.
CELL_TIME_LIMIT_S = 3 * 3600
TOP_PLANNER_TIME_LIMIT_S = 5 * 3600

_stop = False
# The training run in progress, so a stop can take its whole process group with
# it. Without this, `pkill -f bot_training_rotation` kills this script and
# leaves bot_evolve and its eleven workers running, orphaned and invisible,
# still using every core on the machine -- which is exactly what happened to the
# rotation this replaced, and it had to be hunted down by hand.
_child = None


def _on_signal(signum, _frame):
    global _stop
    _stop = True
    log(f"signal {signum}: stopping, and taking the run in progress with me.")
    _kill_child()


def _kill_child() -> None:
    """Stop the training subprocess and every worker it spawned."""
    proc = _child
    if proc is None or proc.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.terminate()
        except Exception:
            pass
    try:
        proc.wait(timeout=20)
    except Exception:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            pass


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


def planner_grade_for(name: str) -> str:
    """The planner grade a cell plays at, or "" when it is not a planner cell."""
    if name == "planner":
        return PLANNER_GRADE
    if name == "planner_top":
        return TOP_PLANNER_GRADE
    return ""


def run_cell(name: str, tier: int, planner: bool) -> Optional[int]:
    """One visit. Returns how many champions it crowned, or None if it failed."""
    if name == "planner_top":
        tiers = TOP_PLANNER_TIERS
    elif planner:
        tiers = PLANNER_TIERS
    else:
        tiers = WEIGHT_TIERS
    mutants, screen, confirm, cap = tiers[tier]
    cmd = [sys.executable, "bot_evolve.py", "--counts", COUNTS,
           "--generations", str(GENERATIONS), "--mutants", str(mutants),
           "--screen-games", str(screen), "--confirm-games", str(confirm),
           "--max-confirm-games", str(cap), "--jobs", str(JOBS),
           "--out-dir", OUT_DIR]
    if planner:
        cmd += ["--planner", planner_grade_for(name)]
    else:
        cmd += ["--strategy", name, "--chooser", "live", "--grade", TRAIN_GRADE]
    console = os.path.join(OUT_DIR, f"console_{name}.log")
    log(f"{name}: tier {tier} · {mutants} mutants · {screen} screen · {confirm}/{cap} confirming")
    global _child
    started = time.time()
    out = ""
    timed_out = False
    try:
        with open(console, "a", encoding="utf-8") as fh:
            fh.write(f"\n===== {time.strftime('%F %T')} · tier {tier} =====\n")
            fh.flush()
            # Its own session, so a stop can signal the whole group at once.
            _child = subprocess.Popen(cmd, cwd=HERE, stdout=subprocess.PIPE,
                                      stderr=subprocess.STDOUT, text=True,
                                      start_new_session=True)
            limit = (TOP_PLANNER_TIME_LIMIT_S if name == "planner_top"
                     else CELL_TIME_LIMIT_S)
            try:
                out, _ = _child.communicate(timeout=limit)
            except subprocess.TimeoutExpired:
                timed_out = True
                log(f"{name}: over its {limit // 3600}h budget — stopping it here "
                    f"and moving on. Anything it crowned is already saved.")
                _kill_child()
                try:
                    out, _ = _child.communicate(timeout=30)
                except Exception:
                    out = ""
            fh.write(out or "")
    except Exception as exc:
        log(f"{name}: run failed to start ({exc})")
        _child = None
        return None
    rc = _child.returncode if _child is not None else -1
    _child = None
    mins = (time.time() - started) / 60.0
    if _stop:
        log(f"{name}: stopped after {mins:.0f} min")
        return None
    if timed_out:
        # Not a failure: the trainer worked, it just ran out of budget. Report
        # whatever it crowned before it was stopped, so a cell that promoted in
        # its first generation still counts as having found something.
        crowned = promotions_from(out or "")
        log(f"{name}: {crowned} new champion(s) in {mins:.0f} min before the budget ran out")
        return crowned
    if rc != 0:
        log(f"{name}: exited {rc} after {mins:.0f} min — see {console}")
        return None
    crowned = promotions_from(out or "")
    log(f"{name}: {crowned} new champion(s) in {mins:.0f} min")
    return crowned


# How many strategy cells run between two visits to the planner's own knobs.
#
# The planner used to be last in the cycle, which meant one visit per cycle --
# about ten hours. That is the wrong way round. Its six per-rival knobs have
# never been measured at all, so it is the cell with the most unclaimed ground
# on it, and it is the brain every grade from Eugenie Clark up actually plays
# with: the top half of the ladder, which is what a strong player meets. Its
# games cost roughly four times a weighted chooser's, so it earns a turn every
# few strategies rather than every one.
PLANNER_EVERY = 2
# ...and every this-many planner turns is taken at S++ instead of at A. The
# knobs are shared by every planner grade, so a turn at A is the cheap way to
# explore them -- but a knob that wins with A's shallow search does not have to
# win with S++'s deep one, and S++ is the rung being optimised. Its games cost
# several times an A game's, hence "every third".
TOP_PLANNER_EVERY = 3


def interleave_planner(strategies: List[str]) -> List[str]:
    """The cycle's running order, with the planner given regular turns."""
    out: List[str] = []
    planner_turns = 0

    def planner_cell() -> str:
        nonlocal planner_turns
        planner_turns += 1
        return "planner_top" if planner_turns % TOP_PLANNER_EVERY == 0 else "planner"

    for i, name in enumerate(strategies):
        out.append(name)
        if (i + 1) % PLANNER_EVERY == 0:
            out.append(planner_cell())
    if not out[-1].startswith("planner"):
        out.append(planner_cell())
    return out


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
    failures = 0
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
        order = interleave_planner(fresh + MAINS + COMBOS)

        # Pick up where the last run stopped, rather than at the top of the
        # cycle. A restart used to begin the order again, so every restart gave
        # the cells at the front another visit and the ones at the back none --
        # after five restarts in a day, mammals had been trained three times and
        # goby_moon_shot and invertebrates not once. The position is saved with
        # everything else, and clamped in case the order has since changed
        # length (it did, when the combos came out of it).
        start_at = int(state.get("next_index", 0) or 0)
        if start_at >= len(order):
            start_at = 0
        if start_at:
            log(f"resuming at cell {start_at + 1}/{len(order)} ({order[start_at]})")

        for idx in range(start_at, len(order)):
            name = order[idx]
            state["next_index"] = idx
            if _stop:
                break
            planner = name.startswith("planner")
            c = cell(state, name)
            if c["settled"] and (cycle - int(c.get("settled_cycle", 0))) % SETTLED_RECHECK_CYCLES:
                continue
            tier = min(int(c["tier"]), SETTLED_TIER - 1)
            crowned = run_cell(name, tier, planner)
            if _stop:
                # Asked to stop. Not a failure, and nothing to back off from:
                # leave now rather than sleeping out the backoff first.
                save_state(state)
                break
            if crowned is None:
                # A cell that cannot run must not be retried at full speed. With
                # no backoff, a trainer broken in any way -- a bad import, a
                # missing file -- turns this into a spin that fills the disk with
                # logs and never stops, unattended, for as long as it takes
                # somebody to notice.
                failures += 1
                if failures >= MAX_CONSECUTIVE_FAILURES:
                    log(f"{failures} runs in a row produced no result. That is the "
                        f"trainer being broken, not the bots being finished; "
                        f"stopping rather than spinning. See the console logs.")
                    save_state(state)
                    return
                log(f"  backing off {FAILURE_BACKOFF_S}s after a failed run "
                    f"({failures} in a row)")
                time.sleep(FAILURE_BACKOFF_S)
                save_state(state)
                continue
            failures = 0
            c["visits"] = int(c["visits"]) + 1
            c["last_cycle"] = cycle
            if crowned > 0:
                # There is more here, so look somewhere cheaper -- but only one
                # step cheaper, not all the way back to the bottom.
                #
                # How much evidence a cell needs is a property of the cell, not
                # of whether it has just improved. Measured on the first
                # champion this run crowned: the paired spread on mammals is
                # about 13.8 points a deal, so 450 confirming games can only
                # prove an edge of 1.27 points, 900 can prove 0.90, and 1600 can
                # prove 0.68. The improvement it found was 0.99 -- real, and
                # invisible at tier 0. Dropping straight back to tier 0 after
                # every crowning would spend a barren visit rediscovering that
                # this cell needs more games, every single time.
                c["promotions"] = int(c["promotions"]) + crowned
                c["tier"] = max(0, int(c["tier"]) - 1)
                c["barren_visits"] = 0
                if c["settled"]:
                    log(f"{name}: settled no longer — the field moved and it found "
                        f"{crowned} more")
                c["settled"] = False
            else:
                c["barren_visits"] = int(c["barren_visits"]) + 1
                c["tier"] = min(int(c["tier"]) + 1, SETTLED_TIER)
                if c["tier"] >= SETTLED_TIER and not c["settled"]:
                    c["settled"] = True
                    c["settled_cycle"] = cycle
                    _top = (TOP_PLANNER_TIERS if name == "planner_top"
                            else (PLANNER_TIERS if planner else WEIGHT_TIERS))[-1][3]
                    log(f"{name}: SETTLED — nothing beat it at the top bar "
                        f"({_top} confirming games). Re-checked every "
                        f"{SETTLED_RECHECK_CYCLES} cycles.")
            state["next_index"] = idx + 1
            save_state(state)

        # A cycle that ran to the end starts the next one at the top.
        if not _stop:
            state["next_index"] = 0

        # Put the cycle's champions where the game reads them. Without this the
        # whole night is a set of JSON files nobody plays against: from grade B
        # up a bot reads brain["by_strategy"], and only this writes it.
        promote()

        settled = sorted(n for n, v in state["cells"].items() if v.get("settled"))
        log(f"cycle {cycle} done · settled: {', '.join(settled) if settled else 'none'}")

    log("stopped.")


if __name__ == "__main__":
    main()
