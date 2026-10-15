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
    return {"ok": True, "rc": None, "secs": time.time() - t0, "note": why}


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
    t0 = time.time()
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
                return {"ok": rc == 0, "rc": rc, "secs": time.time() - t0,
                        "note": "finished" if rc == 0 else f"exit {rc}"}
            if STOPPING["now"]:
                return _end(proc, "stopped on request", t0)
            if time.time() > deadline:
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
           "--min-players", "2", "--max-players", "8",
           "--games-per-player-count", str(n),
           "--benchmark-games", str(bench),
           "--jobs", str(a.jobs),
           "--train-output-dir", os.path.join("fish_training", "forever", "run")]
    if not resume:
        cmd += ["--fresh", "--seed", str(int(time.time()) & 0x7FFFFFFF)]
    log(f"  learn: {'resuming' if resume else 'new run'}, {n} games/count over "
        f"2..8 players, {bench} bench/count")
    res = run(cmd, os.path.join(OUT, "learn.log"), a.phase_minutes)
    st["seconds"]["learn"] += res["secs"]
    st["learn_unfinished"] = res.get("rc") != 0
    if res.get("rc") == 0:
        st["games_played"]["learn"] += n * 7
    log(f"  learn: {res['note']} in {res['secs']:.0f}s")

    # Size the next visit from what this one actually cost, so the phase fills
    # its budget instead of a number somebody guessed. Held between 20 and 400:
    # below 20 a count has too few games to learn anything from, and above 400
    # one visit crowds out the other two phases.
    if res.get("rc") == 0 and res["secs"] > 1.0:
        target = a.phase_minutes * 60.0 * 0.85
        scaled = n * (target / res["secs"])
        st["learn_games_per_count"] = int(max(20, min(400, scaled)))
    elif res["note"].startswith("budget"):
        st["learn_games_per_count"] = int(max(20, n * 0.6))

    summary = os.path.join(HERE, "fish_training", "forever", "run", "train_summary.json")
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
    budget_end = time.time() + a.phase_minutes * 60.0
    trained: List[str] = []
    while time.time() < budget_end and not STOPPING["now"]:
        idx = int(st.get("plan_index", 0)) % len(ALL_PLANS)
        plan = ALL_PLANS[idx]
        st["plan_index"] = (idx + 1) % len(ALL_PLANS)
        count = 6 if plan in AT_SIX else 4
        left = (budget_end - time.time()) / 60.0
        if left < 3.0:
            break
        cmd = [sys.executable, "bot_evolve.py", "--count", str(count),
               "--strategy", plan, "--generations", "1",
               "--chooser", "live", "--grade", "william_beebe",
               "--mutants", "8", "--screen-games", "40",
               "--confirm-games", "150", "--max-confirm-games", "450",
               "--jobs", str(a.jobs)]
        log(f"  strategy: {plan} at {count}P (budget {left:.0f} min left)")
        res = run(cmd, os.path.join(OUT, f"strategy_{plan}.log"), left)
        st["seconds"]["strategy"] += res["secs"]
        trained.append(plan)
        log(f"  strategy: {plan} {res['note']} in {res['secs']:.0f}s")
        if res.get("rc") != 0:
            # bot_evolve writes its champion at the END of a generation, so a
            # run that was cut short produced nothing. Put the plan back at the
            # front of the queue rather than moving on to the next one: with 14
            # plans and one visit each per cycle, silently skipping a plan whose
            # generation was interrupted means it waits a full cycle for the
            # generation it never got.
            st["plan_index"] = idx
            save_state(st)
            break
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
           "--deterministic", "--counts", "2,4,6", "--generations", "1",
           "--mutants", "8", "--screen-games", "40",
           "--confirm-games", "200", "--max-confirm-games", "600",
           "--jobs", str(a.jobs)]
    log("  planner: tuning the shared knobs at a deterministic grade A")
    res = run(cmd, os.path.join(OUT, "planner.log"), a.phase_minutes)
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
    cmd = [sys.executable, "ab_plan.py", "--games", str(games),
           "--counts", "2,4,6", "--plans", "all", "--grade", "eugenie_clark",
           "--temperature", "0.0", "--seed", str(31415 + st["cycle"]),
           "--jobs", str(a.jobs), "--out", out_json]
    # 60 deals at each of three table sizes is 180 paired deals a plan, where
    # the 95% interval is about +-5 points. Fourteen plans measured at once
    # means roughly one spurious "significant" plan per cycle by chance; what
    # keeps that from accumulating is apply_family_prior's shrink and blend,
    # which between them move a prior only a quarter of the way to a single
    # measurement, so a fluke is undone by the next two cycles that disagree.
    log(f"  priors: {games} forced-plan deals per plan per table size")
    res = run(cmd, os.path.join(OUT, "priors.log"), a.phase_minutes)
    st["seconds"].setdefault("priors", 0.0)
    st["seconds"]["priors"] += res["secs"]
    log(f"  priors: {res['note']} in {res['secs']:.0f}s")

    # Size the next visit from what this one cost. 14 plans x 3 sizes x 2 arms
    # is 84 games per deal-count, so this phase is the most expensive of the
    # four and the sizing matters most here.
    if res.get("rc") == 0 and res["secs"] > 1.0:
        target = a.phase_minutes * 60.0 * 0.85
        st["prior_games"] = int(max(20, min(400, games * (target / res["secs"]))))
    elif res["note"].startswith("budget"):
        st["prior_games"] = int(max(20, games * 0.6))

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
           "--counts", "2,4,6", "--old", base, "--new", "fish_ai_brain.json",
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
    ap.add_argument("--phase-minutes", type=float, default=40.0,
                    help="wall-clock budget per phase before it yields to the next")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 4) - 2))
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
    log(f"train_forever started: {a.phase_minutes:.0f} min per phase, {a.jobs} jobs, "
        f"measuring every {a.measure_every} cycle(s) against {a.baseline}")
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
            log(f"cycle {st['cycle']} done · brain: {brain_facts()}")
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
