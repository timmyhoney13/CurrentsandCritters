#!/bin/bash
# Evolve every Casual table size, round-robin, until stopped.
#
# One count at a time so each gets the whole machine, and each count keeps its
# own champion on disk, so a cycle resumes where the last one left off rather
# than starting over. Bigger tables get fewer generations per visit because
# their games cost more: an 8-player game is ~2.5x a 4-player one.
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
  for spec in "4:5" "2:5" "3:5" "5:4" "6:4" "7:3" "8:3"; do
    COUNT="${spec%%:*}"
    GENS="${spec##*:}"
    echo "[$(date '+%F %T')] cycle $CYCLE -> ${COUNT}P, $GENS generations" >> "$LOG"
    python3 bot_evolve.py --count "$COUNT" --generations "$GENS" \
      --mutants 10 --screen-games 60 --confirm-games 200 \
      --max-confirm-games 1200 --jobs 12 \
      >> fish_training/evolve/console_${COUNT}p.log 2>&1
    echo "[$(date '+%F %T')] cycle $CYCLE -> ${COUNT}P finished (exit $?)" >> "$LOG"
  done
done
