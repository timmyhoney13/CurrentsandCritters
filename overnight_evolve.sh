#!/bin/bash
# Evolve every Casual table size, round-robin, until stopped.
#
# One count at a time so each gets the whole machine, and each count keeps its
# own champion on disk, so a cycle resumes where the last one left off rather
# than starting over. Bigger tables get fewer generations per visit because
# their games cost more: an 8-player game is ~2.5x a 4-player one.
#
# Budgets are deliberately tight. A generation that lets three challengers each
# run to 1200 games costs ~4800 games, and as champions improve they build
# bigger boards and the games get SLOWER -- measured at 1.6 -> 1.14 -> 0.69
# games/s across three generations. One such generation took four hours, which
# would mean never reaching the other table sizes at all. Visiting every count
# matters more than certifying every borderline challenger, and one rejected
# for want of games simply gets re-proposed on the next cycle.
#
# Stop with:  pkill -f overnight_evolve
cd "$(dirname "$0")"
mkdir -p fish_training/evolve
LOG=fish_training/evolve/overnight.log
echo "[$(date '+%F %T')] overnight rotation started" >> "$LOG"

CYCLE=0
while true; do
  CYCLE=$((CYCLE+1))
  echo "[$(date '+%F %T')] ===== cycle $CYCLE =====" >> "$LOG"
  for spec in "4:3" "2:3" "3:3" "5:2" "6:2" "7:2" "8:2"; do
    COUNT="${spec%%:*}"
    GENS="${spec##*:}"
    echo "[$(date '+%F %T')] cycle $CYCLE -> ${COUNT}P, $GENS generations" >> "$LOG"
    python3 bot_evolve.py --count "$COUNT" --generations "$GENS" \
      --mutants 8 --screen-games 40 --confirm-games 200 \
      --max-confirm-games 600 --jobs 12 \
      >> fish_training/evolve/console_${COUNT}p.log 2>&1
    echo "[$(date '+%F %T')] cycle $CYCLE -> ${COUNT}P finished (exit $?)" >> "$LOG"
  done
done
