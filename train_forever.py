#!/usr/bin/env python3
"""Train the Casual bots, on every front there is, until somebody stops it.

    python3 train_forever.py                 # run it
    python3 train_forever.py --status         # what it has done so far
    pkill -f train_forever                    # stop it

WHY A SUPERVISOR AND NOT ANOTHER SHELL LOOP. There are three separate things
that make these bots stronger and they live in three different files, so a loop
over any one of them leaves the other two untouched:

  LEARN     the brain's learned maps -- synergy, same-ocean synergy, species
            synergy, strategy value and the strategy transitions. These are
            what "learning from the games" means here: games are played, ranked
            by how developed they got, and learned from in proportion. Read by
            every grade. The live brain is only written where a count's new
            maps BEAT its old ones on a benchmark, so this cannot go backwards.
            (fish_game_all_in_one.py --train-all-player-counts)

  STRATEGY  the per-plan weight vectors, one per plan, which decide what a bot
            committed to Coral does differently from one committed to Mammals.
            Read from grade B up. Tournament selection: a mutant replaces the
            champion only by beating it on paired deals with the margin's 95%
            lower bound above zero, so this cannot go backwards either.
            (bot_evolve.py --strategy, then promote_champions.py --write)

  PLANNER   the Reef Planner's shared judgement knobs, which are what grades A
            through GS actually play with. Tuned DETERMINISTICALLY: grade A
            samples its move at temperature 6.0, which is a deliberate handicap
            measured at 18 points, and tuning through 18 points of injected
            noise is how a knob with a real effect comes back "no proof".
            (bot_evolve.py --planner --deterministic)

Every phase gets a time budget and then yields to the next one, because
visiting all three matters more than finishing any one of them -- the same
reason overnight_evolve.sh rotations every table size. State is on disk, so a
cycle resumes where the last one left off instead of starting over.

MEASUREMENT, which is the point of all of it. Every --measure-every cycles it
plays the current brain against the brain this run started from, on paired
deals, and writes the verdict. That number is the only honest answer to "are
they better than they were", and it is allowed to come back "no proof".

Casual only. Nothing here touches Competitive or ranked paths.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import signal
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "fish_training", "forever")
STATE = os.path.join(OUT, "state.json")
PROGRESS = os.path.join(OUT, "progress.log")
PIDFILE = os.path.join(OUT, "train_forever.pid")

# The plans, and the table size each is trained at. Invertebrates only unlocks
# at five players, so training it at four would train a plan the bot is never
# allowed to pick.
MAINS: List[str] = [
    "mammals", "yellowfin_tuna", "birds_of_a_feather", "crustaceans",
    "baitfish_barrage", "cephalopods", "coral", "king_salmon", "goby_moon_shot",
    "game_fish",
]
COMBOS: List[str] = ["birds_crustaceans", "coral_cephalopods", "birds_coral"]
AT_SIX: set = {"invertebrates"}
ALL_PLANS: List[str] = MAINS + ["invertebrates"] + COMBOS

PHASES = ("learn", "strategy", "planner", "priors")

# THREE PLAYERS AND UP, EVERYWHERE. Two-player games are excluded from every
# phase and every measurement. They were also where the one proven harm from
# map learning lived (-5.2 points, replicated three times), so dropping them
# removes that cell as well as honouring how the game is actually played.
COUNTS = "3,4,6"
# bot_evolve shares a generation's deals out between the sizes given, so a
# vector is measured at every size it will be played at rather than at one.
EVOLVE_COUNTS = "3,4,5,6"
EVOLVE_COUNTS_BIG = "5,6"          # Invertebrates only unlocks at five

# How long each phase gets, in minutes of work actually done. Not equal,
# because the phases do not buy equal amounts:
#   planner  the Reef Planner's shared knobs, which grades A, S, S+ and GS all
#            play with -- four rungs, and the top of the ladder is the flat
#            part. Highest leverage per minute.
#   priors   which plan to commit to, also read by every planner grade. The
#            census found the planner choosing Crustaceans (67 points) seven
#            times as often as Shooting the Moon.
#   strategy per-plan weight vectors, which reach exactly ONE rung (B), and a
#            single generation costs an hour. Lowest leverage per minute, so
#            it gets its hour but not a quarter of the night.
#   learn    regression learning, measured at about a point in a direction
#            nobody can establish. Every fourth cycle.
PHASE_MINUTES = {"learn": 35.0, "strategy": 62.0, "planner": 38.0, "priors": 38.0}

# How many of the learning pipeline's own 5.8MB brain backups to keep. It
# writes one per promotion attempt and prunes none of them, which over a night
# of cycles is gigabytes of files whose whole purpose is "the last few states".
KEEP_TRAIN_BACKUPS = 8


def log(msg: str) -> None:
    line = f"[{time.strftime('%F %T')}] {msg}"
    print(line, flush=True)
    try:
        with open(PROGRESS, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def load_state() -> Dict[str, Any]:
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(st: Dict[str, Any]) -> None:
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, indent=2, sort_keys=True)
    os.replace(tmp, STATE)


def fresh_state() -> Dict[str, Any]:
    return {
        "started": time.strftime("%F %T"),
        "cycle": 0,
        "phase_index": 0,
        "plan_index": 0,
        "games_played": {"learn": 0, "strategy": 0, "planner": 0},
        "seconds": {"learn": 0.0, "strategy": 0.0, "planner": 0.0},
        "promotions": {"learn": 0, "strategy": 0, "planner": 0},
        "learn_games_per_count": 60,
        "prior_games": 60,
        "measurements": [],
        "phase_runs": 0,
    }


# Set by the SIGTERM/SIGINT handler. `run` watches it as well as the clock, and
# the phases watch it between pieces of work.
STOPPING = {"now": False}


def _end(proc: "subprocess.Popen", why: str, t0: float) -> Dict[str, Any]:
    """Ask a child to stop, then insist. SIGTERM first, because the tools that
    write a champion or a brain get to finish doing it."""
    try:
        proc.terminate()
        proc.wait(timeout=120)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    return {"ok": True, "rc": None, "secs": time.monotonic() - t0, "note": why}


def run(cmd: List[str], logfile: str, minutes: float) -> Dict[str, Any]:
    """Run one training command with a wall-clock budget.

    Returns when the child finishes, when its budget runs out, or when this
    supervisor has been asked to stop -- and in that last case the CHILD is
    stopped too. Without that, `pkill -f train_forever` set a flag and then sat
    waiting for a 20-minute generation to run its course, so "stop" meant
    "stop in up to twenty minutes" and a second plan got started on the way
    out. Measured: a stop took 7 minutes and began training a plan nobody
    asked for.
    """
    # MONOTONIC, not wall clock. A Mac that goes to sleep mid-phase comes back
    # with time.time() hours further on and the budget already blown, which is
    # how a 40-minute strategy phase came to report "budget reached ... in
    # 3854s". time.monotonic() does not advance while the machine is asleep on
    # Darwin, so a budget measures the time the work actually got.
    t0 = time.monotonic()
    with open(logfile, "a", encoding="utf-8") as fh:
        fh.write(f"\n===== {time.strftime('%F %T')}  {' '.join(cmd)} =====\n")
        fh.flush()
        try:
            proc = subprocess.Popen(cmd, cwd=HERE, stdout=fh, stderr=subprocess.STDOUT,
                                    start_new_session=True)
        except OSError as exc:
            return {"ok": False, "secs": 0.0, "note": f"could not start: {exc}"}
        deadline = t0 + minutes * 60.0
        while True:
            rc = proc.poll()
            if rc is not None:
                return {"ok": rc == 0, "rc": rc, "secs": time.monotonic() - t0,
                        "note": "finished" if rc == 0 else f"exit {rc}"}
            if STOPPING["now"]:
                return _end(proc, "stopped on request", t0)
            if time.monotonic() > deadline:
                return _end(proc, "budget reached, asked to stop", t0)
            time.sleep(2.0)


def prune_train_backups() -> int:
    found = sorted(glob.glob(os.path.join(HERE, "fish_ai_brain.backup_2*.json")))
    removed = 0
    for old in found[:-KEEP_TRAIN_BACKUPS] if len(found) > KEEP_TRAIN_BACKUPS else []:
        try:
            os.unlink(old)
            removed += 1
        except OSError:
            pass
    return removed


def brain_facts() -> Dict[str, Any]:
    """What the live brain knows, in the few numbers worth watching."""
    try:
        with open(os.path.join(HERE, "fish_ai_brain.json"), encoding="utf-8") as f:
            b = json.load(f)
    except Exception as exc:
        return {"error": str(exc)}
    return {
        "games_learned": b.get("games_played", 0),
        "move_updates": b.get("move_updates", 0),
        "synergy_pairs": len(b.get("synergy", {}) or {}),
        "same_ocean_pairs": len(b.get("same_ocean_synergy", {}) or {}),
        "species_pairs": len(b.get("species_synergy", {}) or {}),
        "strategy_values": len(b.get("strategy_value", {}) or {}),
        "strategy_vectors": len(b.get("by_strategy", {}) or {}),
        "counts_trained": sorted((b.get("by_count", {}) or {}).keys()),
    }


def champion_facts() -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for path in sorted(glob.glob(os.path.join(HERE, "fish_training", "evolve",
                                              "champion_*.json"))):
        label = os.path.basename(path)[len("champion_"):-len(".json")]
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            continue
        out[label] = {"generation": d.get("generation", 0),
                      "margin": round(float(d.get("margin_edge", 0.0)), 3),
                      "margin_low": round(float(d.get("margin_edge_low", 0.0)), 3),
                      "games": d.get("games", 0)}
    return out


def phase_learn(st: Dict[str, Any], a: argparse.Namespace) -> None:
    """Play games, rank them, learn from the good ones, promote what benchmarks
    better. This is the phase that learns FROM GAMES rather than from a
    tournament between weight vectors."""
    n = int(st.get("learn_games_per_count", 60))
    bench = max(40, n // 2)
    # RESUME a run the budget cut short rather than starting it over. The
    # pipeline only learns after it has generated every game it set out to, so
    # a phase that is always cut off before that point learns NOTHING, over and
    # over, and --fresh is what guarantees it: it deletes the games.jsonl the
    # next visit would have continued from. Start fresh only once the previous
    # run actually finished, which is also when a new seed is wanted.
    resume = bool(st.get("learn_unfinished"))
    cmd = [sys.executable, "fish_game_all_in_one.py", "--train-all-player-counts",
           "--min-players", "3", "--max-players", "8",
           "--games-per-player-count", str(n),
           "--benchmark-games", str(bench),
           "--jobs", str(a.jobs),
           "--train-output-dir", os.path.join("fish_training", "forever", "run")]
    if not resume:
        cmd += ["--fresh", "--seed", str(int(time.time()) & 0x7FFFFFFF)]
    log(f"  learn: {'resuming' if resume else 'new run'}, {n} games/count over "
        f"3..8 players, {bench} bench/count")
    res = run(cmd, os.path.join(OUT, "learn.log"), PHASE_MINUTES["learn"])
    st["seconds"]["learn"] += res["secs"]
    st["learn_unfinished"] = res.get("rc") != 0
    if res.get("rc") == 0:
        st["games_played"]["learn"] += n * 6
    log(f"  learn: {res['note']} in {res['secs']:.0f}s")

    # Size the next visit from what this one actually cost, so the phase fills
    # its budget instead of a number somebody guessed. Held between 20 and 400:
    # below 20 a count has too few games to learn anything from, and above 400
    # one visit crowds out the other two phases.
    if res.get("rc") == 0 and res["secs"] > 1.0:
        target = PHASE_MINUTES["learn"] * 60.0 * 0.85
        scaled = n * (target / res["secs"])
        st["learn_games_per_count"] = int(max(20, min(400, scaled)))
    elif res["note"].startswith("budget"):
        st["learn_games_per_count"] = int(max(20, n * 0.6))

    # ONLY IF THIS RUN FINISHED. train_summary.json is left behind by whichever
    # learn run last completed, and --fresh does not delete it, so a phase the
    # budget cut short would read the previous run's verdict and log it as its
    # own. Caught in a smoke test: a 126-second phase that could not have
    # benchmarked anything reported "promoted counts [4]" off a summary written
    # hours earlier.
    summary = os.path.join(HERE, "fish_training", "forever", "run", "train_summary.json")
    if res.get("rc") != 0:
        log("  learn: cut short, so no verdict of its own this visit "
            "(the summary on disk belongs to an earlier run)")
        prune_train_backups()
        return
    try:
        with open(summary, encoding="utf-8") as f:
            s = json.load(f)
        promo = s.get("promotion", {}) or {}
        got = promo.get("promoted", []) or []
        st["promotions"]["learn"] += len(got)
        agg = (s.get("benchmark", {}) or {}).get("aggregate", {}) or {}
        log(f"  learn: promoted counts {got} · overall new-vs-old win rate "
            f"{agg.get('old_win_rate')} (CI {agg.get('old_ci_low')}-{agg.get('old_ci_high')}, "
            f"n={agg.get('games')})")
    except Exception:
        log("  learn: no summary to read (the phase was cut short before it reported)")
    removed = prune_train_backups()
    if removed:
        log(f"  learn: pruned {removed} old brain backup(s)")


def phase_strategy(st: Dict[str, Any], a: argparse.Namespace) -> None:
    """One plan per visit, round-robin, so every plan keeps improving and none
    starves. A plan trains against the CURRENT champion of all the others, so
    the table gets harder as the night goes on."""
    budget = PHASE_MINUTES["strategy"]
    budget_end = time.monotonic() + budget * 60.0
    trained: List[str] = []
    while time.monotonic() < budget_end and not STOPPING["now"]:
        idx = int(st.get("plan_index", 0)) % len(ALL_PLANS)
        plan = ALL_PLANS[idx]
        counts = EVOLVE_COUNTS_BIG if plan in AT_SIX else EVOLVE_COUNTS
        left = (budget_end - time.monotonic()) / 60.0
        if left < 8.0:
            break
        cmd = [sys.executable, "bot_evolve.py", "--counts", counts,
               "--strategy", plan, "--generations", "1",
               "--chooser", "live", "--grade", "william_beebe",
               "--mutants", "8", "--screen-games", "40",
               "--confirm-games", "150", "--max-confirm-games", "450",
               "--jobs", str(a.jobs)]
        log(f"  strategy: {plan} at {counts}P (budget {left:.0f} min left)")
        res = run(cmd, os.path.join(OUT, f"strategy_{plan}.log"), left)
        st["seconds"]["strategy"] += res["secs"]
        trained.append(plan)
        log(f"  strategy: {plan} {res['note']} in {res['secs']:.0f}s")
        # ALWAYS ADVANCE. bot_evolve writes its champion at the END of a
        # generation, so a run the budget cut short produced nothing, and the
        # first version of this put the plan back at the front of the queue so
        # it would get another go. That was a trap: if a plan's generation
        # never fits the budget it is retried for ever. Measured -- fourteen
        # cycles and 11.2 hours of this phase, every one of them spent on
        # goby_moon_shot, and the other thirteen plans never trained once.
        # One retry, then the queue moves on and the plan comes round again
        # next cycle.
        retries = int(st.get("plan_retries", 0))
        if res.get("rc") != 0 and retries < 1:
            st["plan_retries"] = retries + 1
            log(f"  strategy: {plan} produced nothing; one more go at it")
        else:
            st["plan_retries"] = 0
            st["plan_index"] = (idx + 1) % len(ALL_PLANS)
        save_state(st)

    # A champion is dead on disk until this writes it into the brain the game
    # reads. The rotation that trained for five days without doing this taught
    # nothing to a single live game.
    out = subprocess.run([sys.executable, "promote_champions.py", "--write"],
                         cwd=HERE, capture_output=True, text=True)
    tail = (out.stdout or out.stderr or "").strip().splitlines()
    for line in tail[-4:]:
        log(f"  promote: {line.strip()}")
    if any("wrote" in ln for ln in tail):
        st["promotions"]["strategy"] += 1
    log(f"  strategy: visited {trained or 'nothing (budget too small)'}")


def phase_planner(st: Dict[str, Any], a: argparse.Namespace) -> None:
    """The Reef Planner's shared knobs, which is what grades A..GS play with.

    Deterministic on purpose. Grade A samples its move at temperature 6.0 -- a
    deliberate difficulty handicap worth about 18 points -- and every shared
    knob the planner ships with was selected through that noise. A knob whose
    job is to make the top-ranked move better is nearly invisible to a selector
    that then picks loosely.
    """
    cmd = [sys.executable, "bot_evolve.py", "--planner", "eugenie_clark",
           "--deterministic", "--counts", COUNTS, "--generations", "1",
           "--mutants", "8", "--screen-games", "40",
           "--confirm-games", "200", "--max-confirm-games", "600",
           "--jobs", str(a.jobs)]
    log("  planner: tuning the shared knobs at a deterministic grade A")
    res = run(cmd, os.path.join(OUT, "planner.log"), PHASE_MINUTES["planner"])
    st["seconds"]["planner"] += res["secs"]
    log(f"  planner: {res['note']} in {res['secs']:.0f}s")


def phase_priors(st: Dict[str, Any], a: argparse.Namespace) -> None:
    """What each plan is worth, and feed the answer back into plan choice.

    This is the one phase that learns something the other three cannot. The
    other three make a bot better at PLAYING the plan it is on; this one makes
    it better at picking which plan to be on, which the census says is where
    the bots are furthest from right -- they commit to Crustaceans seven times
    as often as to Shooting the Moon.

    Measured by forcing a seat onto the plan and pairing against the identical
    deal with that seat free, so the number is "what committing to this is
    worth against what this planner would have done with the same cards" --
    the quantity FAMILY_PRIOR is supposed to hold, rather than the census of
    chosen-and-then-scored that it currently holds.

    Only a plan whose interval clears zero is written, and only half of what
    was measured. A night of this cannot therefore run the prior away on noise.
    """
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out_json = os.path.join(OUT, f"plans_{stamp}.json")
    games = int(st.get("prior_games", 60))
    # A FEW PLANS PER VISIT, round-robin. ab_plan is not resumable, so a visit
    # the budget cuts short writes nothing at all -- and all fourteen plans at
    # three table sizes and two arms is 84 games per deal, which never fitted.
    # Measured: 8.95 hours in this phase across fourteen cycles, and it
    # reported "no measurement written" every time but three. Four plans a
    # visit finishes, and every plan still comes round.
    per_visit = max(1, int(a.priors_per_visit))
    pi = int(st.get("prior_plan_index", 0)) % len(ALL_PLANS)
    chosen = [ALL_PLANS[(pi + k) % len(ALL_PLANS)] for k in range(per_visit)]
    st["prior_plan_index"] = (pi + per_visit) % len(ALL_PLANS)
    cmd = [sys.executable, "ab_plan.py", "--games", str(games),
           "--counts", COUNTS, "--plans", ",".join(chosen),
           "--grade", "eugenie_clark",
           "--temperature", "0.0", "--seed", str(31415 + st["cycle"]),
           "--jobs", str(a.jobs), "--out", out_json]
    # 60 deals at each of three table sizes is 180 paired deals a plan, where
    # the 95% interval is about +-5 points. Fourteen plans measured at once
    # means roughly one spurious "significant" plan per cycle by chance; what
    # keeps that from accumulating is apply_family_prior's shrink and blend,
    # which between them move a prior only a quarter of the way to a single
    # measurement, so a fluke is undone by the next two cycles that disagree.
    log(f"  priors: {games} forced-plan deals per table size for "
        f"{', '.join(chosen)}")
    res = run(cmd, os.path.join(OUT, "priors.log"), PHASE_MINUTES["priors"])
    st["seconds"].setdefault("priors", 0.0)
    st["seconds"]["priors"] += res["secs"]
    log(f"  priors: {res['note']} in {res['secs']:.0f}s")

    # Size the next visit from what this one cost, with a floor of 120 deals a
    # plan: below that the interval is wider than the effects being looked for
    # and apply_family_prior's own --min-n would throw the measurement away
    # anyway, so a smaller visit is not a cheaper measurement, it is none.
    if res.get("rc") == 0 and res["secs"] > 1.0:
        target = PHASE_MINUTES["priors"] * 60.0 * 0.85
        st["prior_games"] = int(max(40, min(400, games * (target / res["secs"]))))
    elif res["note"].startswith("budget"):
        st["prior_games"] = int(max(40, games * 0.6))

    if not os.path.exists(out_json):
        log("  priors: no measurement written (cut short before it reported)")
        return
    out = subprocess.run([sys.executable, "apply_family_prior.py", out_json,
                          "--min-n", "150", "--write"],
                         cwd=HERE, capture_output=True, text=True)
    for line in (out.stdout or out.stderr or "").strip().splitlines():
        if line.strip():
            log(f"  prior: {line.rstrip()}")
    if "wrote family_prior.json" in (out.stdout or ""):
        st["promotions"].setdefault("priors", 0)
        st["promotions"]["priors"] += 1


def phase_measure(st: Dict[str, Any], a: argparse.Namespace) -> None:
    """Play the brain as it stands now against the brain this run started from.

    This is allowed to come back "no proof", and it often will: a paired deal
    has a standard deviation of about 35 points, so a few hundred of them can
    only resolve effects of a few points. Reporting "no proof" as "no change"
    would be a lie in one direction and reporting it as a gain would be a lie
    in the other.
    """
    base = os.path.join("fish_training", a.baseline, "fish_ai_brain.json")
    if not os.path.exists(os.path.join(HERE, base)):
        log(f"  measure: no baseline brain at {base}; skipped")
        return
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out_json = os.path.join(OUT, f"measure_{stamp}.json")
    cmd = [sys.executable, "ab_brain.py", "--games", str(a.measure_games),
           "--counts", COUNTS, "--old", base, "--new", "fish_ai_brain.json",
           "--grade", "william_beebe",
           "--label", f"cycle{st['cycle']}-vs-baseline",
           "--seed", str(90000 + st["cycle"]),
           "--jobs", str(a.jobs), "--out", out_json]
    log(f"  measure: {a.measure_games} paired deals per size, current brain vs {a.baseline}")
    res = run(cmd, os.path.join(OUT, "measure.log"), a.measure_minutes)
    try:
        with open(out_json, encoding="utf-8") as f:
            m = json.load(f)
        lo, hi = m["ci"]
        log(f"  MEASURED cycle {st['cycle']}: {m['mean']:+.2f} points "
            f"95% CI [{lo:+.2f},{hi:+.2f}]  n={m['n']}  "
            f"win rate {m['winrate_new']:.3f} vs {m['winrate_old']:.3f}  "
            f"=> {m['verdict']}")
        st["measurements"].append({
            "cycle": st["cycle"], "when": time.strftime("%F %T"),
            "mean": m["mean"], "ci": m["ci"], "n": m["n"],
            "verdict": m["verdict"], "winrate_new": m["winrate_new"],
            "winrate_old": m["winrate_old"],
        })
    except Exception as exc:
        log(f"  measure: no verdict ({exc}); {res['note']}")

    # AND AT A PLANNER GRADE. ab_brain plays grade B, which is where the
    # brain's learned weights are read -- and the plan priors this run keeps
    # writing are read by the PLANNER, grades A through GS, which grade B
    # never touches. So the one part of the night with four measured results
    # to its name was invisible to the only progress check there was.
    #
    # This turns the measured plan values off in one seat and on in every
    # other, on the same deals, at a deterministic grade A. It answers the
    # question the per-plan measurements do not: whether the planner PLAYS
    # better with them, which is not the same as whether each number is right.
    if not os.path.exists(os.path.join(HERE, "family_prior.json")):
        return
    pj = os.path.join(OUT, f"priors_live_{stamp}.json")
    cmd = [sys.executable, os.path.join("fish_training", "ab", "ab.py"),
           "--games", str(max(120, a.measure_games // 2)), "--counts", COUNTS,
           "--grade", "eugenie_clark",
           "--base", '{"temperature": 0.0}',
           "--variant", '{"temperature": 0.0, "use_measured_priors": 0.0}',
           "--label", f"cycle{st['cycle']}-priors-OFF-vs-ON",
           "--seed", str(70000 + st["cycle"]),
           "--jobs", str(a.jobs), "--out", pj]
    log("  measure: the learned plan values, off vs on, at a deterministic grade A")
    run(cmd, os.path.join(OUT, "measure_priors.log"), a.measure_minutes)
    try:
        with open(pj, encoding="utf-8") as f:
            m = json.load(f)
        lo, hi = m["ci"]
        # The VARIANT is priors OFF, so a negative mean means turning them off
        # is worse, i.e. the learned values are helping.
        verdict = ("the learned values HELP" if hi < 0 else
                   "the learned values HURT" if lo > 0 else "no proof either way")
        log(f"  MEASURED plan values: turning them off scores {m['mean']:+.2f} "
            f"95% CI [{lo:+.2f},{hi:+.2f}] n={m['n']} => {verdict}")
        st.setdefault("prior_checks", []).append({
            "cycle": st["cycle"], "when": time.strftime("%F %T"),
            "off_minus_on": m["mean"], "ci": m["ci"], "n": m["n"],
            "verdict": verdict})
    except Exception as exc:
        log(f"  measure: no plan-value verdict ({exc})")


def status() -> int:
    st = load_state()
    if not st:
        print("No run on record yet.")
        return 0
    print(f"started   {st.get('started')}")
    print(f"cycle     {st.get('cycle')}  (phase {PHASES[st.get('phase_index', 0) % len(PHASES)]}, "
          f"next plan {ALL_PLANS[st.get('plan_index', 0) % len(ALL_PLANS)]})")
    secs = st.get("seconds", {})
    tot = sum(float(v) for v in secs.values())
    print(f"time      {tot/3600.0:.2f} h total  " +
          "  ".join(f"{k} {float(v)/3600.0:.2f}h" for k, v in sorted(secs.items())))
    print(f"games     learn ~{st.get('games_played', {}).get('learn', 0)}")
    print(f"promoted  {st.get('promotions')}")
    print("\nbrain now:")
    for k, v in brain_facts().items():
        print(f"   {k:20s} {v}")
    print("\nchampions:")
    for label, d in sorted(champion_facts().items()):
        print(f"   {label:22s} gen {d['generation']:3d}  margin {d['margin']:+7.3f} "
              f"(low {d['margin_low']:+7.3f})  {d['games']} games")
    ms = st.get("measurements", [])
    print(f"\nmeasurements ({len(ms)}): current brain vs the brain this run started from")
    for m in ms[-12:]:
        lo, hi = m["ci"]
        print(f"   cycle {m['cycle']:3d}  {m['when']}  {m['mean']:+7.2f} "
              f"[{lo:+7.2f},{hi:+7.2f}]  n={m['n']:5d}  {m['verdict']}")
    if not ms:
        print("   none yet")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--budget-scale", type=float, default=1.0,
                    help="multiply every phase budget, for smoke-testing the "
                         "rotation without waiting an hour for one generation")
    ap.add_argument("--priors-per-visit", type=int, default=4,
                    help="how many plans the plan-value phase measures per visit")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 4) - 2))
    ap.add_argument("--learn-every", type=int, default=4,
                    help="run the regression-learning phase every Nth cycle "
                         "(1 = every cycle, 0 = never). Its effect measures at "
                         "about a point in an unestablished direction, and the "
                         "other three phases can prove their gains, so it does "
                         "not get a quarter of every cycle by default.")
    ap.add_argument("--measure-every", type=int, default=3,
                    help="measure current-vs-baseline every N cycles (0 = never)")
    ap.add_argument("--measure-games", type=int, default=300,
                    help="paired deals PER table size in a measurement")
    ap.add_argument("--measure-minutes", type=float, default=60.0)
    ap.add_argument("--baseline", default="baseline_20261014",
                    help="directory under fish_training/ holding the brain to measure against")
    ap.add_argument("--fresh", action="store_true", help="forget the previous run's state")
    ap.add_argument("--status", action="store_true", help="print progress and exit")
    ap.add_argument("--max-cycles", type=int, default=0, help="0 = until stopped")
    a = ap.parse_args()

    os.makedirs(OUT, exist_ok=True)
    if a.status:
        return status()
    if a.budget_scale != 1.0:
        for k in list(PHASE_MINUTES):
            PHASE_MINUTES[k] = max(0.5, PHASE_MINUTES[k] * a.budget_scale)

    # One at a time. Two supervisors would write the same brain from two
    # phases at once, and the loser's learning would be silently thrown away.
    if os.path.exists(PIDFILE):
        try:
            with open(PIDFILE) as f:
                other = int(f.read().strip())
            os.kill(other, 0)
            print(f"train_forever is already running as pid {other}. "
                  f"Stop it first (pkill -f train_forever) or read "
                  f"{os.path.relpath(PROGRESS, HERE)}.")
            return 1
        except (OSError, ValueError):
            pass
    with open(PIDFILE, "w") as f:
        f.write(str(os.getpid()))

    st = fresh_state() if a.fresh else (load_state() or fresh_state())
    st.setdefault("measurements", [])
    st.setdefault("prior_games", 60)
    for k in ("games_played", "seconds", "promotions"):
        st.setdefault(k, {})
        for ph in PHASES:
            st[k].setdefault(ph, 0 if k != "seconds" else 0.0)

    def _stop(_sig, _frm):
        STOPPING["now"] = True
        log("stop requested; stopping the work in flight and exiting")

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    log("=" * 72)
    log(f"train_forever started: {a.jobs} jobs, {COUNTS}-player tables, budgets "
        + " ".join(f"{k} {v:.0f}m" for k, v in sorted(PHASE_MINUTES.items()))
        + f", learn every {a.learn_every}, measuring every {a.measure_every}")
    log(f"brain at start: {brain_facts()}")
    log("stop with:  pkill -f train_forever")
    log("=" * 72)

    try:
        while not STOPPING["now"]:
            if a.max_cycles and st["cycle"] >= a.max_cycles:
                log(f"reached --max-cycles {a.max_cycles}; stopping")
                break
            st["cycle"] += 1
            log(f"===== cycle {st['cycle']} =====")
            for i, phase in enumerate(PHASES):
                if STOPPING["now"]:
                    break
                st["phase_index"] = i
                save_state(st)
                try:
                    if phase == "learn":
                        # WHY THIS PHASE IS NOT RUN EVERY CYCLE. Measured, by
                        # splitting one learning pass into its halves and
                        # measuring each against the live brain on paired deals:
                        #
                        #   weights only  -0.34  95% CI [-1.03,+0.35]  no proof
                        #   maps only     -1.21  95% CI [-3.13,+0.72]  no proof
                        #
                        # So a pass is worth somewhere around a point, in a
                        # direction nobody can establish. A paired deal in this
                        # game has a standard deviation of about 35 points, so
                        # resolving an effect that size to +-1 point would take
                        # roughly 4,700 deals PER TABLE SIZE, and the pipeline's
                        # own gate is unpaired at 40 games a count, where the
                        # interval is +-11 points. That gate cannot see a one
                        # point effect, which is why it has never promoted
                        # anything and why its alarming per-count margins (-7 to
                        # -33) are mostly noise.
                        #
                        # The other three phases do not have this problem: they
                        # select with common random numbers and staged
                        # confirmation, and only promote on a lower bound above
                        # zero, so what they do is monotone by construction.
                        # Spending a quarter of every cycle on a pass whose
                        # effect cannot be measured, against three that can
                        # prove their gains, is the wrong split. It still runs
                        # periodically: the machinery stays exercised, the games
                        # keep accumulating, and the gate still protects the
                        # brain if a pass ever does prove itself.
                        if a.learn_every and st["cycle"] % a.learn_every != 0:
                            log(f"  learn: skipped this cycle (runs every "
                                f"{a.learn_every}; see the note in the source)")
                            st["phase_runs"] = int(st.get("phase_runs", 0)) + 1
                            save_state(st)
                            continue
                        phase_learn(st, a)
                    elif phase == "strategy":
                        phase_strategy(st, a)
                    elif phase == "planner":
                        phase_planner(st, a)
                    else:
                        phase_priors(st, a)
                except Exception as exc:
                    # A phase that breaks must not take the night down with it.
                    log(f"  !! {phase} phase failed: {exc!r}")
                st["phase_runs"] = int(st.get("phase_runs", 0)) + 1
                save_state(st)
            if (not STOPPING["now"] and a.measure_every
                    and st["cycle"] % a.measure_every == 0):
                try:
                    phase_measure(st, a)
                except Exception as exc:
                    log(f"  !! measure failed: {exc!r}")
                save_state(st)
            # ARE THE GAMES STILL GAMES? Asked before anything else, because a
            # run that keeps selecting bots on broken games is worse than a run
            # that stopped: the champions it crowns are real champions of
            # something that is not this game, and every log line on the way
            # there looks healthy. If this fails the night ends here.
            #
            # The check that matters most is that reef_planner.fast_points
            # still agrees exactly with final_points. It is a hand-written copy
            # of the scorer and the planner picks every move by it, so if the
            # two ever drift apart then grades A and up are optimising a game
            # nobody is playing -- and they would still look like they were
            # winning, because they would be winning the game they think they
            # are in.
            try:
                out = subprocess.run([sys.executable, "check_games_valid.py",
                                      "--games", "6", "--counts", COUNTS, "--quiet"],
                                     cwd=HERE, capture_output=True, text=True,
                                     timeout=1800)
                for line in (out.stdout or out.stderr or "").strip().splitlines():
                    if line.strip():
                        log(f"  valid: {line.rstrip()}")
                if out.returncode != 0:
                    STOPPING["now"] = True
                    log("  !! THE GAMES ARE NOT VALID. Stopping rather than "
                        "training on them. Nothing has been promoted this cycle "
                        "that was not already proved; the brain and champions on "
                        "disk are whatever the last good cycle left.")
            except Exception as exc:
                # A check that cannot run is not a pass. It does not stop the
                # night on its own, because a timeout here is far more likely
                # than a broken deck, but it is said plainly.
                log(f"  !! validity check could not run: {exc!r}")

            # DID ANYTHING ACTUALLY CHANGE? Asked by value, not by counting map
            # entries, and printed with the evidence each change came with.
            # This is the check the supervisor cannot do from the inside: a
            # phase retrying one unit of work that never fits its budget logs
            # a healthy-looking line every time round.
            try:
                out = subprocess.run([sys.executable, "check_updating.py",
                                      "--snapshot", "--quiet-if-moving"],
                                     cwd=HERE, capture_output=True, text=True,
                                     timeout=600)
                for line in (out.stdout or out.stderr or "").strip().splitlines():
                    if line.strip():
                        log(f"  check: {line.rstrip()}")
            except Exception as exc:
                log(f"  !! update check failed: {exc!r}")
            log(f"cycle {st['cycle']} done")
    finally:
        save_state(st)
        try:
            os.unlink(PIDFILE)
        except OSError:
            pass
        log(f"train_forever stopped after {st['cycle']} cycle(s). "
            f"Read {os.path.relpath(PROGRESS, HERE)} or run --status.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
