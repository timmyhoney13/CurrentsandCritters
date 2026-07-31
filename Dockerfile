FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# firebase-admin lets the server read the exact registered-player and live
# online-player counts from Firestore, served to the website via /api/stats.
# (Snap & Score card recognition runs in the player's browser against the
# prebuilt multiplayer/client/snap-card-library.json — no vision API, no
# anthropic SDK, no server-side image processing.)
RUN pip install --no-cache-dir firebase-admin==6.5.0

# Copy only runtime files needed by the live multiplayer server.
COPY multiplayer_server.py /app/multiplayer_server.py
COPY snap_score.py /app/snap_score.py
COPY fish_game_all_in_one.py /app/fish_game_all_in_one.py
COPY tournament_engine.py /app/tournament_engine.py
COPY tournament_server.py /app/tournament_server.py
COPY clan_server.py /app/clan_server.py
COPY fish_ai_brain.json /app/fish_ai_brain.json
COPY cards_vertical.txt /app/cards_vertical.txt
COPY cards_lr.txt /app/cards_lr.txt
COPY cards_oceans.txt /app/cards_oceans.txt
COPY multiplayer/client /app/multiplayer/client
COPY multiplayer/human_game_dataset.jsonl /app/multiplayer/human_game_dataset.jsonl
COPY horizontal_cards /app/horizontal_cards
COPY vertical_cards /app/vertical_cards
COPY oceans_cards /app/oceans_cards

# Default room state path; in Render this is overridden to mounted disk.
RUN mkdir -p /app/multiplayer/state /var/data/fish-room-state

EXPOSE 10000

CMD ["sh", "-c", "cd /app && python3 multiplayer_server.py --host 0.0.0.0 --port ${PORT:-10000} ${PUBLIC_BASE_URL:+--public-base-url ${PUBLIC_BASE_URL}} ${FISH_CREATE_KEY:+--create-key ${FISH_CREATE_KEY}} ${FISH_CORS_ALLOW_ORIGIN:+--cors-allow-origin ${FISH_CORS_ALLOW_ORIGIN}}"]
