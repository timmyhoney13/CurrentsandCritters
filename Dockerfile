FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# firebase-admin lets the server read the exact registered-player and live
# online-player counts from Firestore, served to the website via /api/stats.
# (Snap & Score card recognition runs in the player's browser against the
# prebuilt multiplayer/client/snap-card-library.json, no vision API, no
# anthropic SDK, no server-side image processing.)
# nh3 is the HTML sanitiser for admin-authored newsletter content (Rust
# `ammonia` bindings: the maintained successor to bleach). newsletter_email.py
# has a deny-by-default fallback parser if this import ever fails, so a wheel
# problem degrades safely instead of shipping unsanitised HTML; nh3 is the path
# that should actually run in production.
RUN pip install --no-cache-dir firebase-admin==6.5.0 nh3==0.2.18

# Copy only runtime files needed by the live multiplayer server.
COPY multiplayer_server.py /app/multiplayer_server.py
COPY snap_score.py /app/snap_score.py
COPY fish_game_all_in_one.py /app/fish_game_all_in_one.py
COPY tournament_engine.py /app/tournament_engine.py
COPY tournament_server.py /app/tournament_server.py
COPY clan_server.py /app/clan_server.py
# Shared warm cache, imported at module scope by the two servers above,
# so a missing COPY here is a server that will not start at all.
COPY warm_cache.py /app/warm_cache.py
COPY prestige_server.py /app/prestige_server.py
COPY analytics_server.py /app/analytics_server.py
# Both halves of the newsletter, or neither: multiplayer_server.py imports
# newsletter_server at module scope and newsletter_server imports newsletter_email,
# so a missing COPY here is not a missing mailing list, it is the whole server
# failing to boot on ImportError.
COPY newsletter_server.py /app/newsletter_server.py
COPY newsletter_email.py /app/newsletter_email.py
# Discord join reward, imported at module scope too, so a missing COPY here is
# a server that will not boot, not a reward that quietly goes missing.
COPY discord_server.py /app/discord_server.py
# Level Pass and the friend-code referral reward, module-scope imports as well,
# so a missing COPY here is a server that will not boot, not a feature that
# quietly goes missing.
COPY level_pass_server.py /app/level_pass_server.py
COPY referral_server.py /app/referral_server.py
# Purchase redemption codes: how a website donation reaches an account.
# Module-scope import too, so a missing COPY here is a server that will not boot.
COPY redeem_codes.py /app/redeem_codes.py
# The welcome bonus + the dev friends roster. Module-scope import as well.
COPY welcome_server.py /app/welcome_server.py
# The email on an account (link, confirm, reset). Module-scope import too.
COPY account_email.py /app/account_email.py
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
