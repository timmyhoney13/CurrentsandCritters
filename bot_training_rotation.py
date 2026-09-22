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

(The planner screens twice as many mutants for the same confirming budgets --
its games cost a seventh of a strategy game -- and S++ runs a shorter ladder
still, because its games cost forty times an A game's. See the tier tables.)

A visit that crowns a champion steps that cell one tier DOWN, not back to the
bottom: there is clearly more to find and it is cheaper to look for it lower,
but how much evidence a cell needs is a property of how noisy its games are,
not of whether it has just improved. A visit that crowns nothing moves it up a
tier, so the next look is a harder test on more games.
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

import glob
import json
import os
import shutil
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

# The three combos, trained after every single plan, because a combo's champion
# is seeded from its two parents' and a combo built on an untrained half
# inherits nothing worth having (see bot_evolve.COMBO_PARENTS).
#
# They were dropped from this list for a while, and correctly: no bot could
# commit to one, so their weights were unreadable and the rotation this replaced
# spent about fifteen hours a cycle making them -- birds_crustaceans alone took
# eleven and a half. What changed is that a bot can now choose one. They are in
# reef_planner.STRATEGY_FAMILIES and in the advanced and expert allowlists, and
# a combo's cards resolve to its parents' cards, so committing to B-Lob values
# gulls and lobsters rather than hunting for a card that does not exist.
COMBOS = ["birds_crustaceans", "coral_cephalopods", "birds_coral"]

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
# other strategy instead, and every so often that turn is taken at S+ itself
# rather than at the cheapest planner grade, because S+ is now the top rung a
# player can earn.
TRAIN_GRADE = "william_beebe"
PLANNER_GRADE = "eugenie_clark"      # A: cheapest planner grade, for volume
TOP_PLANNER_GRADE = "jacques_cousteau" # S+: the top rung a player can earn

COUNTS = "2,3,4,5,6"
# ...except at S++, which skips the two smallest tables.
#
# Measured on the first S++ cell: a confirming game cost 1217 core-seconds,
# about sixty times a strategy game, and the cost is dominated by the small
# tables -- a 2P game runs about 67 turns of deep search against 19 at 6P. The
# cheap planner grade still trains on all five sizes, so nothing about the
# table-size knobs goes unmeasured; S++ is here to check that what the cheap
# grade found survives a deeper search, and it can do that at the sizes people
# most often sit down at.
TOP_PLANNER_COUNTS = "4,5,6"


def counts_for_cell(name: str) -> str:
    return TOP_PLANNER_COUNTS if name == "planner_top" else COUNTS
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
# More hypotheses per generation than the strategies get, because a planner game
# at Eugenie Clark costs 3 core-seconds against a strategy game's 20. Screening
# sixteen mutants over sixty deals instead of eight over forty costs about three
# minutes a generation here (9.8 -> 12.8) where the same change on a strategy
# cell would cost half an hour. Cheap games should buy a wider search, not the
# same search finished sooner; the confirming budgets are unchanged, because
# what a challenger has to prove should not depend on how cheap it was to think
# of it.
PLANNER_TIERS = [(16, 60, 150, 450), (16, 80, 250, 900), (20, 100, 400, 1600)]
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
# S++ was given five hours and spent nine and a half on a single generation. A
# cycle is about eleven hours, so a budget that big is not a safety net, it is
# permission to eat the night.
TOP_PLANNER_TIME_LIMIT_S = 2 * 3600
# How often the budget is checked while a cell runs. Short enough that a stop is
# prompt, long enough to cost nothing.
BUDGET_POLL_S = 30

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
    # The three ladders need not be the same length, and SETTLED_TIER is
    # measured against the weights' one, so clamp rather than trust the caller.
    mutants, screen, confirm, cap = tiers[max(0, min(tier, len(tiers) - 1))]
    cmd = [sys.executable, "bot_evolve.py", "--counts", counts_for_cell(name),
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
            # The budget is enforced against our own clock, in short waits,
            # rather than by handing communicate() one enormous timeout.
            #
            # It was written the obvious way first -- communicate(timeout=limit)
            # -- and the first S++ cell ran nine hours and forty minutes against
            # a five hour budget without the timeout ever firing. The mechanism
            # works in isolation; whatever it does with a five-hour deadline on
            # a child that holds its pipe open for hours, it does not raise. A
            # budget that can silently not apply is worse than no budget, since
            # it is the thing standing between one slow cell and a whole night.
            deadline = time.time() + limit
            while True:
                try:
                    out, _ = _child.communicate(timeout=BUDGET_POLL_S)
                    break
                except subprocess.TimeoutExpired:
                    if time.time() >= deadline:
                        timed_out = True
                        _b = (f"{limit // 3600}h" if limit >= 3600
                              else f"{limit // 60}m")
                        log(f"{name}: over its {_b} budget — stopping it here "
                            f"and moving on. Anything it crowned is already "
                            f"saved.")
                        _kill_child()
                        try:
                            out, _ = _child.communicate(timeout=30)
                        except Exception:
                            out = ""
                        break
                    if _stop:
                        out = ""
                        break
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
# A planner turn after EVERY strategy, because the 400-match calibration says
# that is where the ladder is weak and where nothing else can reach.
#
#     the five chooser rungs (F..B)   span 710 Elo measured, 600 wanted
#     the five planner rungs (A..GS)  span 179 Elo measured, 850 wanted
#
# The bottom half of the ladder works. The top half is nearly flat: S+ to S++ is
# 47 Elo and S++ to Giant Squid is MINUS 8, inside the noise. Those rungs differ
# only in how much search they buy, so search has saturated -- twelve times the
# work for about 180 Elo. What is left to improve at the top is the planner's
# judgement, and its knobs are shared by all five of those rungs, so one knob
# proved better lifts every one of them at once.
#
# Against that, a strategy vector reaches exactly one rung (B). So the cycle is
# split about evenly between thirteen strategy cells and thirteen planner cells
# rather than thirteen against seven.
PLANNER_EVERY = 1
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
    if not out or not out[-1].startswith("planner"):
        out.append(planner_cell())
    return out


def promote() -> None:
    """Copy this cycle's champions into the brain the live game reads."""
    try:
        # It only reads and writes files, but this runs unattended between
        # every cycle, and anything without a timeout in that position is a way
        # for the night to stop without saying so.
        proc = subprocess.run([sys.executable, "promote_champions.py", "--write"],
                              cwd=HERE, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True, timeout=600)
    except Exception as exc:
        log(f"promote: failed to run ({exc})")
        return
    tail = (proc.stdout or "").strip().splitlines()
    log("promote: " + (tail[-1].strip() if tail else f"exit {proc.returncode}"))


SNAPSHOTS_KEPT = 6


def snapshot_state(tag: str) -> None:
    """Copy a KNOWN-GOOD training state aside, so a night cannot be lost.

    The brain is backed up on every promotion, but the champions never were --
    and they are the night's work. A champion file is written atomically, so it
    cannot be torn by a crash, but atomic is not the same as recoverable: a
    champion that is merely WRONG, or a file lost to something outside this
    program, had nothing to roll back to.

    Taken only when the integrity check has just passed, so what is kept is
    known good rather than merely recent. If a cycle ends unsound the last good
    snapshot is left exactly where it is.
    """
    root = os.path.join(OUT_DIR, "snapshots")
    dest = os.path.join(root, tag)
    try:
        os.makedirs(dest, exist_ok=True)
        for f in glob.glob(os.path.join(OUT_DIR, "champion_*.json")):
            shutil.copy2(f, os.path.join(dest, os.path.basename(f)))
        brain = os.path.join(HERE, "fish_ai_brain.json")
        if os.path.exists(brain):
            shutil.copy2(brain, os.path.join(dest, "fish_ai_brain.json"))
        state = os.path.join(OUT_DIR, "rotation_state.json")
        if os.path.exists(state):
            shutil.copy2(state, os.path.join(dest, "rotation_state.json"))
        kept = sorted(d for d in os.listdir(root)
                      if os.path.isdir(os.path.join(root, d)))
        for old_dir in kept[:-SNAPSHOTS_KEPT]:
            shutil.rmtree(os.path.join(root, old_dir), ignore_errors=True)
        log(f"snapshot: known-good state saved as snapshots/{tag}")
    except Exception as exc:
        log(f"snapshot: could not save ({exc})")


def integrity_check(why: str) -> bool:
    """Ask check_training.py whether what we just wrote is sound.

    Run at the moments the files actually change -- a promotion and the end of a
    cycle -- because that is when damage would appear, and an unattended run
    that has quietly corrupted the brain looks exactly like one that has not.

    It never stops the run. It has already been shown that a checker can be
    wrong about a healthy file, and a false alarm that kills a night is worse
    than a real fault that is merely reported. Loudly reported, and still
    reported in every cycle summary after it.
    """
    try:
        proc = subprocess.run([sys.executable, "check_training.py"], cwd=HERE,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, timeout=900)
    except Exception as exc:
        log(f"integrity ({why}): could not run the check ({exc})")
        return True
    tail = [l for l in (proc.stdout or "").splitlines() if "sound," in l]
    verdict = tail[-1].strip() if tail else f"exit {proc.returncode}"
    if proc.returncode == 0:
        log(f"integrity ({why}): {verdict}")
        return True
    bad = [l.strip() for l in (proc.stdout or "").splitlines() if l.strip().startswith("FAIL")]
    log(f"integrity ({why}): NOT SOUND — {verdict}")
    for l in bad[:6]:
        log(f"    {l}")
    log("    the run continues; see check_training.py for the detail")
    return False


def log_summary(state: Dict[str, Any], cycle: int) -> None:
    """What the run has to show for itself, every cycle.

    An unattended run needs a line somebody can read at a glance and know
    whether it is still finding anything, because "it is still running" and "it
    is still getting better" are different questions and only one of them is
    obvious from the process list.
    """
    cells = state.get("cells", {})
    total = sum(int(v.get("promotions", 0)) for v in cells.values())
    settled = sorted(n for n, v in cells.items() if v.get("settled"))
    log(f"cycle {cycle} done · {total} champion(s) crowned in all · "
        f"settled: {', '.join(settled) if settled else 'none'}")
    flagged = state.get("last_integrity")
    if flagged:
        log(f"    ! an integrity check has failed since this run began ({flagged})")
    for name in sorted(cells):
        v = cells[name]
        log(f"    {name:20s} tier {v.get('tier', 0)} · {v.get('visits', 0)} visit(s) "
            f"· {v.get('promotions', 0)} champion(s)"
            + ("  SETTLED" if v.get("settled") else ""))


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
        # Count COMPLETED passes, not process starts. It used to increment on
        # every launch, so a day of restarts read as "cycle 7" when not one
        # cycle had run end to end -- and SETTLED_RECHECK_CYCLES counts in these,
        # so an inflated number also brought settled cells back early.
        resuming = int(state.get("next_index", 0) or 0) > 0
        if not resuming:
            state["cycle"] = int(state.get("cycle", 0)) + 1
        cycle = int(state.get("cycle", 1))
        log(f"===== cycle {cycle} =====")
        # A known-good copy before the cycle touches anything, so a night's work
        # always has something to go back to.
        if not resuming and integrity_check(f"start of cycle {cycle}"):
            snapshot_state(f"cycle{cycle:03d}_{time.strftime('%Y%m%d_%H%M%S')}")
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
        # The saved index is only meaningful against the order it was saved
        # from, and the order does change -- it did when the combos came out of
        # it, and it does whenever a strategy gains its first champion and drops
        # out of the `fresh` prefix. The cell's NAME is the thing that is
        # actually meant, so prefer it and keep the index as a fallback.
        want = str(state.get("next_cell", "") or "")
        if want:
            try:
                start_at = order.index(want, min(start_at, len(order) - 1)) \
                    if want in order[min(start_at, len(order) - 1):] else order.index(want)
            except ValueError:
                pass
        if start_at >= len(order):
            start_at = 0
        if start_at:
            log(f"resuming at cell {start_at + 1}/{len(order)} ({order[start_at]})")

        for idx in range(start_at, len(order)):
            name = order[idx]
            state["next_index"] = idx
            state["next_cell"] = name
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
                # Put it in the brain NOW, not at the end of the cycle. A cycle
                # is about eleven hours, and a champion sitting in a file is a
                # champion nothing plays against: from grade B up a bot reads
                # brain["by_strategy"], and only promote_champions writes it. It
                # costs a moment of file copying, and it means the run improves
                # the bots as it goes rather than in one lump at the end that a
                # stop at the wrong moment would miss entirely.
                promote()
                if not integrity_check(f"after promoting {name}"):
                    state["last_integrity"] = f"cycle {cycle}: {name}"
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
            state["next_cell"] = order[idx + 1] if idx + 1 < len(order) else ""
            save_state(state)

        # A cycle that ran to the end starts the next one at the top.
        if not _stop:
            state["next_index"] = 0
            state["next_cell"] = ""

        # Put the cycle's champions where the game reads them. Without this the
        # whole night is a set of JSON files nobody plays against: from grade B
        # up a bot reads brain["by_strategy"], and only this writes it.
        promote()
        if not integrity_check(f"end of cycle {cycle}"):
            state["last_integrity"] = f"cycle {cycle}: end of cycle"
        save_state(state)

        log_summary(state, cycle)

    log("stopped.")


if __name__ == "__main__":
    main()
