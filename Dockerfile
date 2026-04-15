FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Copy only runtime files needed by the live multiplayer server.
COPY multiplayer_server.py /app/multiplayer_server.py
COPY fish_game_all_in_one.py /app/fish_game_all_in_one.py
COPY fish_ai_brain.json /app/fish_ai_brain.json
COPY cards_vertical.txt /app/cards_vertical.txt
COPY cards_lr.txt /app/cards_lr.txt
COPY cards_oceans.txt /app/cards_oceans.txt
COPY multiplayer/client /app/multiplayer/client
COPY multiplayer/human_game_dataset.jsonl /app/multiplayer/human_game_dataset.jsonl

# Default room state path; in Render this is overridden to mounted disk.
RUN mkdir -p /app/multiplayer/state /var/data/fish-room-state

EXPOSE 10000

CMD ["sh", "-lc", "python3 multiplayer_server.py --host 0.0.0.0 --port ${PORT:-10000} ${PUBLIC_BASE_URL:+--public-base-url ${PUBLIC_BASE_URL}} ${FISH_CREATE_KEY:+--create-key ${FISH_CREATE_KEY}} ${FISH_CORS_ALLOW_ORIGIN:+--cors-allow-origin ${FISH_CORS_ALLOW_ORIGIN}}"]
