"""Currents and Critters: Clan System (server-authoritative core).

Wired additively into multiplayer_server (same pattern as tournament_server):
    import clan_server
    clan_server.init(...)                     # in main()
    if clan_server.handle_get(self, parsed):  # in do_GET
    if clan_server.handle_post(self, parsed, body):  # in do_POST
    clan_server.on_trade_completed(db, trade) # from _trade_confirm

Everything lives in Firestore (admin SDK, the browser can never touch these
collections directly, rules stay default-deny):
    clans/{clanId}          one doc per clan: identity, members+roles, season
                            counters, activity log, events, daily goal, XP.
    clan_names/{nameLower}  uniqueness reservation (like usernames/).
    clans/{id}/chat/{mid}   clan chat messages (server-moderated).
    clans/{id}/ledger/{lid} point-award dedup + audit (doc-id create() = the
                            atomic "only once" guarantee per game/trade/user).
    clan_meta/season_{sid}  finalized season results (standings, MVPs, rewards).
    clan_reports/{rid}      player reports (names / chat) for admin review.
    clan_flags/{fid}        suspicious-activity flags for admin review.
    users/{uid}             gains: clan_id, clan_cooldown_until, clan_invites,
                            clan_badges; coins land in stats.critter_coins.

Clan seasons are QUARTERLY, the exact same get_season_id() quarters the
competitive ladder uses (Tim: "each season is three months like how long each
competitive season is"). Seasonal counters are keyed by season id, so a new
quarter starts at zero automatically; the old quarter is finalized lazily
(coins, badges, MVP) the first time any clan endpoint runs inside the new one.

Points can only be claimed for games THIS server recorded (games_history /
competitive_games record files), and trades only via the real /api/trade
completion hook, the client can ask, but never self-report a score.
"""
from __future__ import annotations

import copy
import hashlib
import itertools
import json
import os
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

import warm_cache

# ── Tunables (spec'd values: see the Clan System design doc) ────────────────
CLAN_MAX_MEMBERS          = 25
CLAN_NAME_MIN             = 3
CLAN_NAME_MAX             = 30
CLAN_DESC_MAX             = 240
CLAN_CHAT_MAX             = 500        # chars per message
CLAN_CHAT_FETCH           = 80         # messages returned per poll
CLAN_ACTIVITY_MAX         = 120        # activity-log entries kept on the doc
CLAN_MAX_CUSTOM_ROLES     = 5
CLAN_MAX_EVENTS           = 10
CLAN_INVITE_TTL_SEC       = 7 * 24 * 3600
CLAN_INVITE_MAX           = 10         # pending invites per player

POINTS_COMP_WIN           = 3
POINTS_CASUAL_FIRST       = 2
POINTS_CASUAL_SECOND      = 1          # 0 in a 2-player game
POINTS_CASUAL_THIRD       = 1          # only in games with 4+ players
POINTS_TRADE_DAILY        = 1
# A game with no real opponent (everyone else is a bot or a guest) can never
# pay full placement points, that is the whole anti-farm rule. But winning one
# isn't worth nothing either: first place against bots pays a HALF point, at
# any player count. Second and third against bots pay nothing.
POINTS_CASUAL_FIRST_BOTS  = 0.5
WEEKLY_POINT_CAP          = 150
COMP_SAME_OPP_DAILY_CAP   = 3          # comp matches vs same opponent that can score / day
CASUAL_SAME_OPPS_DAILY_CAP = 5         # casual games vs same opponent set / day
JOIN_COOLDOWN_SEC         = 24 * 3600  # after leaving/being removed from a clan
TRADE_BOUNCE_WINDOW_SEC   = 7 * 24 * 3600   # same-items back-and-forth window
TRADE_PAIR_WEEK_FLAG      = 3          # >N completed trades same pair/week → flag

SEASON_REWARD_COINS       = [400, 300, 200]   # 1st / 2nd / 3rd place clans
SEASON_REWARD_MIN_POINTS  = 10         # member contribution needed for the coin reward
SEASON_BORDER_TOP_N       = 10         # top-10 clans get the seasonal border

# The season's real-world prize: the clan that finishes #1 picks a board game up
# to this much and it is bought and shipped to them. Unlike the coin rewards
# NOTHING here pays it out: no server can post a parcel, so the payout is a
# human one and _finalize_season_bonuses deliberately leaves it alone.
#
# It lives here anyway, next to the payouts it sits beside, because it is a
# PROMISE repeated in four places (the marketing banner, Player Home, the Clans
# tab and the published rules) and the coin rewards already taught us what
# happens when a payout figure is typed in more than one place: the podium
# advertised 400/300/200 for months while the server paid 150/100/50. Every
# payload that carries a season carries this too, so the banner cannot promise
# $150 while the rules page promises $100.
SEASON_GRAND_PRIZE_USD    = 100
# What the money is FOR, in the words the banner prints. "$100" alone reads as
# cash, and this prize is not cash: it is a game, chosen by the winners, shipped.
SEASON_GRAND_PRIZE_WHAT   = "a board game of their choice, shipped to them"
# Who has to do something for it to arrive. Stated with the prize so it is never
# advertised without the catch attached.
SEASON_GRAND_PRIZE_CLAIM  = (
    "The winning clan's owner is contacted after the season is finalized, picks "
    "the game with their clan, and gives one shipping address. Claim within 30 "
    "days. One prize per clan, per season, shipped to one address."
)
MVP_MIN_POINTS            = 25
MVP_BONUS_COINS           = 50
MVP_ICON_DAYS             = 14         # MVP chip shown for first 2 weeks of next season
SEASON_KEEP               = 8          # quarters of per-member history kept on a clan doc

DAILY_GOAL_XP             = 25         # clan XP for finishing the shared daily goal
CLAN_XP_PER_LEVEL_STEP    = 100        # level n→n+1 costs 100*n XP

# Season 1 = 2026-Q3 (launch quarter). Names rotate through this ocean roster.
CLAN_SEASON_EPOCH         = (2026, 3)  # (year, quarter)
# Clan seasons run this many days PAST the quarter they are named for, so there
# is time to actually finish a season's challenges. Everything reads the season
# through _season_bounds/_clan_sid, so this one number moves the countdown, the
# season-challenge deadlines and the moment the season rolls over, together.
CLAN_SEASON_EXTRA_DAYS    = 30
CLAN_SEASON_EXTRA_SEC     = CLAN_SEASON_EXTRA_DAYS * 86400
CLAN_SEASON_NAMES = [
    "Riptide", "Undertow", "High Tide", "Deep Current", "Gulf Stream",
    "Whirlpool", "Tidal Bloom", "Abyssal", "Coral Crest", "Moonlit Tide",
    "Kelp Forest", "Open Water",
]

# ── Clan challenges ──────────────────────────────────────────────────────────
# Two ladders, both server-verified from the counters below:
#   • WEEKLY: reset every Monday 00:00 UTC with the weekly point cap.
#   • SEASON: run the whole quarter, alongside the weekly ones.
# Each entry is {id, name, desc, metric, target, clan_points, member_xp}.
# `metric` names a counter in _challenge_metric / _season_metric; nothing here
# is ever reported by a client, every counter is fed by claim_game_points from
# a game record THIS server wrote, or by the real trade / event / join hooks.
#
# `target` is an int, or the string "members" for "everybody in the clan".
# `min_contribution` is the weekly point contribution a member must have made
# to share in the member XP when a weekly challenge completes.
CLAN_WEEKLY_CHALLENGES: List[Dict[str, Any]] = [
    {"id": "w_full_crew", "name": "Full Crew",
     "desc": "Have at least 5 different clan members play and complete a game.",
     "metric": "members_played", "target": 5, "clan_points": 10, "member_xp": 50},
    {"id": "w_all_hands", "name": "All Hands on Deck",
     "desc": "Have at least 10 different clan members complete a game.",
     "metric": "members_played", "target": 10, "clan_points": 20, "member_xp": 100},
    {"id": "w_daily_divers", "name": "Daily Divers",
     "desc": "Complete at least one clan game on 5 different days this week.",
     "metric": "days_played", "target": 5, "clan_points": 15, "member_xp": 75},
    {"id": "w_six_seven", "name": "Six/Seven",
     "desc": "Complete at least one game on 6 of the week's 7 days (they do not have to be in a row).",
     "metric": "days_played", "target": 6, "clan_points": 30, "member_xp": 150},
    {"id": "w_double_handed", "name": "Double Handed",
     "desc": "Complete 10 competitive 1v1 games.",
     "metric": "comp_games", "target": 10, "clan_points": 15, "member_xp": 75},
    {"id": "w_artificial_start", "name": "Artificial Start",
     "desc": "Play Artificial Reef as the first Ocean in 4 games.",
     "metric": "artificial_first", "target": 4, "clan_points": 6, "member_xp": 30},
    {"id": "w_casual_current", "name": "Casual Current",
     "desc": "Finish first in 4 casual games.",
     "metric": "casual_wins", "target": 4, "clan_points": 8, "member_xp": 40},
    {"id": "w_crowded_waters", "name": "Crowded Waters",
     "desc": "Complete 6 casual games with four or more players.",
     "metric": "casual_4p", "target": 6, "clan_points": 15, "member_xp": 75},
    {"id": "w_eight_at_sea", "name": "Eight at Sea",
     "desc": "Complete two 8-player games with all real people.",
     "metric": "eight_all_human", "target": 2, "clan_points": 15, "member_xp": 75},
    {"id": "w_friendly_competition", "name": "Friendly Competition",
     "desc": "Have 3 different clan members finish first in a casual game against another clan.",
     "metric": "vs_clan_winners", "target": 3, "clan_points": 15, "member_xp": 75},
    {"id": "w_comeback_current", "name": "Comeback Current",
     "desc": "Win two casual games you were NOT leading when the End Game card was revealed.",
     "metric": "comebacks", "target": 2, "clan_points": 20, "member_xp": 100},
    {"id": "w_competitive_current", "name": "Competitive Current",
     "desc": "Win 10 competitive matches.",
     "metric": "comp_wins", "target": 10, "clan_points": 25, "member_xp": 125},
    {"id": "w_winning_waters", "name": "Winning Waters",
     "desc": "Have 5 different clan members win a competitive match.",
     "metric": "comp_win_members", "target": 5, "clan_points": 25, "member_xp": 125},
    {"id": "w_winning_streak", "name": "Winning Streak",
     "desc": "Earn two three-game competitive winning streaks as a clan.",
     "metric": "comp_streak3", "target": 2, "clan_points": 15, "member_xp": 75},
    {"id": "w_humu_duo", "name": "Two of a Kind",
     "desc": "Have two members complete Humuhumunukuapua'a (play 5 Cephalopods in one turn).",
     "metric": "humu_members", "target": 2, "clan_points": 10, "member_xp": 50},
    {"id": "w_double_trouble", "name": "Double Trouble",
     "desc": "In a competitive game, have BOTH of your hands score double the opponent's highest hand.",
     "metric": "double_hands", "target": 1, "clan_points": 20, "member_xp": 100},
    {"id": "w_dominant_depths", "name": "Dominant Depths",
     "desc": "Win 5 competitive matches with both of your hands beating the opponent's highest hand.",
     "metric": "dominant_wins", "target": 5, "clan_points": 25, "member_xp": 125},
    {"id": "w_ocean_architects", "name": "Ocean Architects",
     "desc": "Play 75 Ocean cards.",
     "metric": "oceans_played", "target": 75, "clan_points": 15, "member_xp": 75},
    {"id": "w_critter_collection", "name": "Critter Collection",
     "desc": "Play 125 animal cards.",
     "metric": "animals_played", "target": 125, "clan_points": 15, "member_xp": 75},
    {"id": "w_moving_tide", "name": "Moving Tide",
     "desc": "Move animals between Oceans 20 times.",
     "metric": "moves", "target": 20, "clan_points": 15, "member_xp": 75},
    {"id": "w_star_power", "name": "Star Power",
     "desc": "Activate 75 ★ abilities.",
     "metric": "stars", "target": 75, "clan_points": 15, "member_xp": 75},
    {"id": "w_chain_reaction", "name": "Chain Reaction",
     "desc": "Complete 25 turns with two or more chained ★ abilities.",
     "metric": "star_chains", "target": 25, "clan_points": 20, "member_xp": 100},
    {"id": "w_pool_party", "name": "Pool Party",
     "desc": "Draw 150 cards from the Pool.",
     "metric": "pool_draws", "target": 150, "clan_points": 15, "member_xp": 75},
    {"id": "w_deep_draw", "name": "Deep Draw",
     "desc": "Draw 150 cards directly from the Deck.",
     "metric": "deck_draws", "target": 150, "clan_points": 15, "member_xp": 75},
    {"id": "w_clan_traders", "name": "Clan Traders",
     "desc": "Complete 15 eligible clan trades.",
     "metric": "trades", "target": 15, "clan_points": 20, "member_xp": 100},
]

CLAN_SEASON_CHALLENGES: List[Dict[str, Any]] = [
    {"id": "s_clan_kickoff", "name": "Clan Kickoff",
     "desc": "Complete 20 games with one or more clan members in the game.",
     "metric": "games_with_clanmate", "target": 20, "clan_points": 30, "member_xp": 150},
    {"id": "s_ocean_expedition", "name": "Ocean Expedition",
     "desc": "Complete 50 games with one or more clan members in the game.",
     "metric": "games_with_clanmate", "target": 50, "clan_points": 60, "member_xp": 300},
    {"id": "s_ranked_predators", "name": "Ranked Predators",
     "desc": "Win 30 competitive matches.",
     "metric": "comp_wins", "target": 30, "clan_points": 40, "member_xp": 200},
    {"id": "s_packed_ocean", "name": "Packed Ocean",
     "desc": "Complete 5 casual games with six or more players.",
     "metric": "casual_6p", "target": 5, "clan_points": 20, "member_xp": 100},
    {"id": "s_clan_voyage", "name": "Clan Voyage",
     "desc": "Complete 25 games during the season.",
     "metric": "games", "target": 25, "clan_points": 30, "member_xp": 150},
    {"id": "s_ocean_marathon", "name": "Ocean Marathon",
     "desc": "Complete 50 games during the season.",
     "metric": "games", "target": 50, "clan_points": 55, "member_xp": 275},
    {"id": "s_all_together", "name": "All Together",
     "desc": "Have at least 8 different clan members play and complete one game with each other.",
     "metric": "max_clanmates_in_game", "target": 8, "clan_points": 15, "member_xp": 75},
    {"id": "s_regular_tides", "name": "Regular Tides",
     "desc": "Complete at least 3 eligible games in 10 different weeks of the season.",
     "metric": "weeks_3plus", "target": 10, "clan_points": 50, "member_xp": 250},
    {"id": "s_rising_tide", "name": "Rising Tide",
     "desc": "Earn 75 Clan Points through gameplay.",
     "metric": "gameplay_points", "target": 75, "clan_points": 25, "member_xp": 125},
    {"id": "s_powerful_current", "name": "Powerful Current",
     "desc": "Earn 150 Clan Points through gameplay.",
     "metric": "gameplay_points", "target": 150, "clan_points": 50, "member_xp": 250},
    {"id": "s_balanced_waters", "name": "Balanced Waters",
     "desc": "Earn at least 25 Clan Points from competitive wins AND 25 from casual placements.",
     "metric": "balanced_points", "target": 25, "clan_points": 25, "member_xp": 125},
    {"id": "s_podium_masters", "name": "Podium Masters",
     "desc": "Earn 100 total podium finishes in casual games.",
     "metric": "podiums", "target": 100, "clan_points": 20, "member_xp": 100},
    {"id": "s_shoot_the_moon", "name": "Shoot the Moon",
     "desc": "Complete the Shoot the Moon bonus, all 4 Mandarin Gobies on one board.",
     "metric": "moon_games", "target": 1, "clan_points": 10, "member_xp": 50},
    {"id": "s_shooting_the_moon", "name": "Shooting the Moon",
     "desc": "Complete the Shoot the Moon bonus in five different games.",
     "metric": "moon_games", "target": 5, "clan_points": 25, "member_xp": 125},
    {"id": "s_new_members", "name": "Fresh Recruits",
     "desc": "Have your clan gain two new members this season.",
     "metric": "new_members", "target": 2, "clan_points": 10, "member_xp": 50},
    {"id": "s_competitive_fleet", "name": "Competitive Fleet",
     "desc": "Have at least 10 different clan members win a competitive match.",
     "metric": "comp_win_members", "target": 10, "clan_points": 15, "member_xp": 75},
    {"id": "s_rank_climbers", "name": "Rank Climbers",
     "desc": "Increase clan members' competitive rank divisions 15 times.",
     "metric": "rank_ups", "target": 15, "clan_points": 20, "member_xp": 100},
    {"id": "s_ecosystem_engineers", "name": "Ecosystem Engineers",
     "desc": "Play 555 animal cards and 555 Ocean cards.",
     "metric": "ecosystem", "target": 555, "clan_points": 55, "member_xp": 275},
    {"id": "s_invertebrates", "name": "Saving the Invertebrates",
     "desc": "Have every member of the clan complete the Saving the Invertebrates achievement.",
     "metric": "invert_members", "target": "members", "clan_points": 30, "member_xp": 150},
    {"id": "s_good_game", "name": "Good Sports",
     "desc": "Say “good game” in 25 different games.",
     "metric": "gg_games", "target": 25, "clan_points": 10, "member_xp": 50},
    {"id": "s_events", "name": "Event Organizers",
     "desc": "Organize three events with your clan.",
     "metric": "events_held", "target": 3, "clan_points": 5, "member_xp": 25},
    {"id": "s_team_mode", "name": "Team Tide",
     "desc": "Play 10 games in Team Mode.",
     "metric": "team_games", "target": 10, "clan_points": 15, "member_xp": 75},
    {"id": "s_rival", "name": "Choose Your Rival",
     "desc": "Create a rival clan for the season.",
     "metric": "rival_set", "target": 1, "clan_points": 10, "member_xp": 50},
    {"id": "s_beat_rival", "name": "Rival Reckoning",
     "desc": "Finish the season with more Clan Points than your rival clan "
             "(scored when the season ends).",
     "metric": "beat_rival", "target": 1, "clan_points": 10, "member_xp": 50},
    {"id": "s_clan_trades", "name": "Reef Merchants",
     "desc": "Trade 5 times with other clan members.",
     "metric": "clan_trades", "target": 5, "clan_points": 5, "member_xp": 25},
    {"id": "s_team_rival", "name": "Cross-Current",
     "desc": "Play a Team Mode game with 3 members from your clan and 3 from your rival clan "
             "both clans score it.",
     "metric": "team_rival_games", "target": 1, "clan_points": 15, "member_xp": 75},
]

# Both lists may be replaced wholesale from the environment (same JSON shape) so
# a live event can be run without a deploy. A bad blob is ignored, never fatal.
def _challenges_from_env(var: str, fallback: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    try:
        raw = os.environ.get(var, "").strip()
        if not raw:
            return fallback
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            picked = [c for c in parsed if isinstance(c, dict) and c.get("id")]
            if picked:
                return picked
    except Exception as exc:  # noqa: BLE001
        print(f"[clan] {var} parse failed: {exc}")
    return fallback


CLAN_WEEKLY_CHALLENGES = _challenges_from_env("FISH_CLAN_CHALLENGES", CLAN_WEEKLY_CHALLENGES)
CLAN_SEASON_CHALLENGES = _challenges_from_env("FISH_CLAN_SEASON_CHALLENGES", CLAN_SEASON_CHALLENGES)

# ── End-of-season competitive rank payout ────────────────────────────────────
# "The higher your competitive rank, the more you bring your squad." At season
# finalize every CURRENT member's competitive division is read from their
# profile (stats.rank_competitive, the same string the Competitive tab shows)
# and converted to a tier here. The Clan Points land on the season being
# finalized BEFORE the final standings are computed, so they can still change
# the placing; the Critter Coins go straight to the member.
# Coins climb by 50 a tier from Silver up; Diamond sits between Gold and
# Emerald in the live ladder and its Clan Points are interpolated to match.
COMP_RANK_TIER_ORDER = ("unranked", "bronze", "silver", "gold", "diamond", "emerald", "king")
COMP_RANK_SEASON_REWARDS: Dict[str, Dict[str, int]] = {
    "unranked": {"coins": 0,   "clan_points": 0},
    "bronze":   {"coins": 0,   "clan_points": 0},
    "silver":   {"coins": 20,  "clan_points": 5},
    "gold":     {"coins": 40,  "clan_points": 15},
    "diamond":  {"coins": 60,  "clan_points": 20},
    "emerald":  {"coins": 80,  "clan_points": 25},
    "king":     {"coins": 100, "clan_points": 50},
}
# Division name → tier. Matched on the distinctive word so "Golden Grouper II",
# "Emerald Emperor Penguin III" and "King of the Critters" all resolve.
_RANK_TIER_WORDS = (
    ("king", "king"), ("emerald", "emerald"), ("diamond", "diamond"),
    ("golden", "gold"), ("gold", "gold"), ("silver", "silver"), ("bronze", "bronze"),
)

# The shared daily goal roster; one is picked per clan per UTC day.
DAILY_GOALS = [
    {"id": "games",   "target": 5, "label": "Complete 5 games today"},
    {"id": "points",  "target": 10, "label": "Earn 10 Clan Points today"},
    {"id": "members", "target": 3, "label": "3 different members earn points today"},
    {"id": "trade",   "target": 1, "label": "Complete a clan trade today"},
]

PRIVACY_MODES = ("public", "request", "invite", "password")
CORE_ROLES    = ("owner", "captain", "recruiter", "member")

# ── Clan join passwords ──────────────────────────────────────────────────────
# "password" is the fourth privacy mode: anyone who knows the word joins
# INSTANTLY, exactly like a public clan, and anyone who doesn't cannot get in at
# all. It is the setting for a clan that wants to be open to its friends without
# the owner having to sit on a queue of join requests.
#
# The password is never stored, never returned by any endpoint, and never
# checked on the client: only a PBKDF2 hash goes in the clan doc, every public
# shaper builds its dict field-by-field (so the hash cannot ride along), and the
# comparison happens here, server-side, in constant time.
CLAN_PASSWORD_MIN         = 4
CLAN_PASSWORD_MAX         = 64
CLAN_PASSWORD_ITERS       = 120_000    # PBKDF2-HMAC-SHA256 rounds
CLAN_PASSWORD_TRIES       = 8          # wrong guesses allowed…
CLAN_PASSWORD_TRY_WINDOW  = 600        # …per player, per clan, per 10 minutes


def _clean_password(raw: Any) -> Tuple[str, Optional[str]]:
    """Validate a password a player typed. Returns (password, error)."""
    # Stripped, because a password that arrives with a trailing space from a
    # phone keyboard or a paste should still be the password they set.
    pw = str(raw or "").strip()
    if len(pw) < CLAN_PASSWORD_MIN:
        return "", "password_too_short"
    if len(pw) > CLAN_PASSWORD_MAX:
        return "", "password_too_long"
    return pw, None


def _hash_password(raw: str) -> Dict[str, Any]:
    """The only form of a clan password that ever reaches storage."""
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", raw.encode("utf-8"),
                             salt.encode("ascii"), CLAN_PASSWORD_ITERS)
    return {"algo": "pbkdf2_sha256", "iters": CLAN_PASSWORD_ITERS,
            "salt": salt, "hash": dk.hex()}


def _check_password(stored: Any, raw: Any) -> bool:
    """Constant-time check of a typed password against a stored hash. A clan
    with no usable hash always answers NO, a broken or missing record must
    never read as "any password works"."""
    if not isinstance(stored, dict):
        return False
    salt  = str(stored.get("salt") or "")
    want  = str(stored.get("hash") or "")
    try:
        iters = int(stored.get("iters") or 0)
    except (TypeError, ValueError):
        return False
    if not salt or not want or iters <= 0:
        return False
    if str(stored.get("algo") or "pbkdf2_sha256") != "pbkdf2_sha256":
        return False
    typed = str(raw or "").strip()
    if not typed:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", typed.encode("utf-8"),
                             salt.encode("ascii"), iters)
    return secrets.compare_digest(dk.hex(), want)


# Wrong-guess throttle, per (player, clan). In-process and best-effort: it makes
# guessing a password one player at a time pointless without adding a Firestore
# write to every attempt.
_PW_TRIES: Dict[Tuple[str, str], List[float]] = {}
_PW_TRIES_LOCK = threading.Lock()


def _password_attempt_ok(uid: str, clan_id: str) -> bool:
    """True if this player may try a password for this clan right now."""
    key = (str(uid), str(clan_id))
    cutoff = time.time() - CLAN_PASSWORD_TRY_WINDOW
    with _PW_TRIES_LOCK:
        tries = [t for t in _PW_TRIES.get(key, []) if t > cutoff]
        _PW_TRIES[key] = tries
        return len(tries) < CLAN_PASSWORD_TRIES


def _password_attempt_failed(uid: str, clan_id: str) -> None:
    key = (str(uid), str(clan_id))
    cutoff = time.time() - CLAN_PASSWORD_TRY_WINDOW
    with _PW_TRIES_LOCK:
        tries = [t for t in _PW_TRIES.get(key, []) if t > cutoff]
        tries.append(time.time())
        _PW_TRIES[key] = tries
        # Never let the throttle table grow without bound.
        if len(_PW_TRIES) > 5000:
            for k in [k for k, v in _PW_TRIES.items() if not [t for t in v if t > cutoff]]:
                _PW_TRIES.pop(k, None)


def _password_attempt_ok_reset(uid: str, clan_id: str) -> None:
    """A correct password clears the player's wrong-guess history."""
    with _PW_TRIES_LOCK:
        _PW_TRIES.pop((str(uid), str(clan_id)), None)

# ── Critter icons a clan is allowed to wear ──────────────────────────────────
# A clan's critter (its icon, the banner image on its profile/leaderboard/invite
# rows, and the season-vote favourite that becomes the Clans tab icon) may only
# be a critter SOMEBODY IN THE CLAN has unlocked. One member owning it is enough
# for the whole clan, the others don't have to have unlocked it themselves.
# Ownership lives in users/{uid}.unlocked_icons; STARTER_ICONS are the ones every
# account has without unlocking anything (mirrors the client's ANIMAL_AVATARS
# entries with unlock.type === "starter": Mullet is the universal default).
STARTER_ICONS = ("/avatars/mullet.png",)
_ICON_RE      = re.compile(r"^/avatars/[a-z0-9\-]+\.(png|webp)$")

# Permissions grantable to custom roles. Owner-only powers (delete clan,
# transfer ownership, change the owner's role, promote to owner, membership/
# security settings) are NOT in this list and can never be granted.
CUSTOM_PERMS = (
    "invite", "review_requests", "remove_members", "post_announcements",
    "pin_announcements", "moderate_chat", "create_events",
    "manage_challenges", "change_roles",
)

PRESENCE_FRESH_SEC = 5 * 60

# ── Injected from multiplayer_server.init ────────────────────────────────────
_get_firestore: Callable[[], Any] = lambda: None
_verify_token: Callable[[str], Optional[dict]] = lambda t: None
_find_uid_by_username: Callable[[Any, str], Optional[str]] = lambda db, n: None
_level_progress: Callable[[Any], Tuple[int, int, int]] = lambda xp: (1, 0, 50)
_get_season_id: Callable[..., str] = lambda ts=None: "2026-Q3"
_GAMES_HISTORY_DIR: str = "."
_COMPETITIVE_GAMES_DIR: str = "."
_PROF_STRONG_RE: Optional[Any] = None
_PROF_WORD_RE: Optional[Any] = None
_PROF_LEET: Dict[str, str] = {}
_PROF_STRONG: List[str] = []
_PROF_WORDS: List[str] = []

_FINALIZE_LOCK = threading.Lock()
_FINALIZED_SIDS: set = set()          # in-process cache so we don't re-check every call
# name.lower() → (is_a_real_account, checked_at). Every finished game asks about
# each opponent; without this that's a Firestore query per opponent per game.
_REG_CACHE: Dict[str, Tuple[Dict[str, Any], float]] = {}
_REG_TTL_SEC = 600.0


def init(*, get_firestore, verify_token, find_uid_by_username, level_progress,
         get_season_id, games_history_dir, competitive_games_dir,
         prof_strong_re=None, prof_word_re=None, prof_leet=None,
         prof_strong=None, prof_words=None) -> None:
    global _get_firestore, _verify_token, _find_uid_by_username, _level_progress
    global _get_season_id, _GAMES_HISTORY_DIR, _COMPETITIVE_GAMES_DIR
    global _PROF_STRONG_RE, _PROF_WORD_RE, _PROF_LEET, _PROF_STRONG, _PROF_WORDS
    _get_firestore = get_firestore
    _verify_token = verify_token
    _find_uid_by_username = find_uid_by_username
    _level_progress = level_progress
    _get_season_id = get_season_id
    _GAMES_HISTORY_DIR = games_history_dir
    _COMPETITIVE_GAMES_DIR = competitive_games_dir
    _PROF_STRONG_RE = prof_strong_re
    _PROF_WORD_RE = prof_word_re
    _PROF_LEET = dict(prof_leet or {})
    _PROF_STRONG = list(prof_strong or [])
    _PROF_WORDS = list(prof_words or [])


# ── Small time / season helpers ──────────────────────────────────────────────
def _now() -> int:
    return int(time.time())


def _date_key(ts: Optional[int] = None) -> str:
    return datetime.fromtimestamp(ts if ts is not None else _now(), tz=timezone.utc).strftime("%Y-%m-%d")


def _week_key(ts: Optional[int] = None) -> str:
    iso = datetime.fromtimestamp(ts if ts is not None else _now(), tz=timezone.utc).isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


# ── Clan Points are fractional ───────────────────────────────────────────────
# First place against bots pays half a point, so every point counter has to
# survive a .5. _num is the one normaliser: it keeps one decimal place, returns
# a plain int when the value is whole (so nothing renders "12.0"), and turns
# junk into 0 instead of raising inside a transaction.
def _num(value: Any) -> Any:
    try:
        f = round(float(value or 0), 1)
    except (TypeError, ValueError):
        return 0
    return int(f) if f == int(f) else f


def _rank_tier(division: Any) -> str:
    """Competitive division name → reward tier ('Golden Grouper II' → 'gold')."""
    text = str(division or "").strip().lower()
    if not text or text.startswith("unranked"):
        return "unranked"
    for word, tier in _RANK_TIER_WORDS:
        if word in text:
            return tier
    return "unranked"


def _rank_tier_index(division: Any) -> int:
    """Where a division sits in the ladder, the basis for 'ranked up'.

    Divisions climb I → II → III inside a tier, so the roman numeral is part of
    the ordering: Silver II really is above Silver I. Anything unrecognised
    sorts to 0, which can only ever read as "no climb"."""
    tier = _rank_tier(division)
    base = COMP_RANK_TIER_ORDER.index(tier) if tier in COMP_RANK_TIER_ORDER else 0
    if base <= 0:
        return 0
    text = str(division or "").strip().lower()
    step = 1
    for numeral, value in (("iii", 3), ("ii", 2), ("i", 1)):
        if re.search(r"\b" + numeral + r"\b", text):
            step = value
            break
    return base * 10 + step


def _sid_parse(sid: str) -> Tuple[int, int]:
    """'2026-Q3' → (2026, 3). Falls back to the epoch on garbage."""
    m = re.match(r"^(\d{4})-Q([1-4])$", str(sid or ""))
    if not m:
        return CLAN_SEASON_EPOCH
    return int(m.group(1)), int(m.group(2))


def _season_number(sid: str) -> int:
    y, q = _sid_parse(sid)
    ey, eq = CLAN_SEASON_EPOCH
    return max(1, (y - ey) * 4 + (q - eq) + 1)


def _season_name(sid: str) -> str:
    return CLAN_SEASON_NAMES[(_season_number(sid) - 1) % len(CLAN_SEASON_NAMES)]


def _season_bounds(sid: str) -> Tuple[int, int]:
    """(start_ts, end_ts) of the season, UTC.

    A clan season is the quarter it is named for PLUS CLAN_SEASON_EXTRA_DAYS,
    so the countdown everyone plays against runs a month longer than the bare
    calendar quarter. end_ts is still exactly the next season's start_ts: the
    extra days shift both ends, they never overlap or leave a gap. The epoch
    season keeps the quarter's own start, because there is no season before it
    to run long into it."""
    y, q = _sid_parse(sid)
    start = datetime(y, 3 * (q - 1) + 1, 1, tzinfo=timezone.utc)
    ny, nq = (y + 1, 1) if q == 4 else (y, q + 1)
    end = datetime(ny, 3 * (nq - 1) + 1, 1, tzinfo=timezone.utc)
    start_ts, end_ts = int(start.timestamp()), int(end.timestamp())
    if (y, q) != CLAN_SEASON_EPOCH:
        start_ts += CLAN_SEASON_EXTRA_SEC
    return start_ts, end_ts + CLAN_SEASON_EXTRA_SEC


def _clan_sid(ts: Optional[int] = None) -> str:
    """The clan season running at `ts`. Seasons run CLAN_SEASON_EXTRA_DAYS past
    the quarter they are named for, so for the first stretch of a new calendar
    quarter the PREVIOUS season is still the live one: every clan read and
    write has to agree about that, or points would land in a season the page
    isn't showing."""
    now = int(ts if ts is not None else _now())
    quarter_sid = _get_season_id(now)
    prev = _prev_sid(quarter_sid)
    # Only a season that actually EXISTS runs long: the epoch season starts on
    # its quarter, so the quarter before it never reaches into it. Without that
    # guard the launch quarter's first month would be filed under a season the
    # game has never had.
    if _sid_parse(prev) >= CLAN_SEASON_EPOCH:
        start, end = _season_bounds(prev)
        if start <= now < end:
            return prev
    return quarter_sid


def _prev_sid(sid: str) -> str:
    y, q = _sid_parse(sid)
    return f"{y - 1}-Q4" if q == 1 else f"{y}-Q{q - 1}"


def _season_public(sid: str) -> Dict[str, Any]:
    start, end = _season_bounds(sid)
    return {
        "id": sid,
        "number": _season_number(sid),
        "name": _season_name(sid),
        "starts_ts": start,
        "ends_ts": end,
        "now": _now(),
        # What finishing top three is actually worth. The Clans page used to
        # print these numbers by hand and they drifted: the podium promised
        # 400/300/200 while the payout paid 150/100/50. Every payload that
        # carries a season carries the real figures now, so there is one place
        # they can be wrong, and it is the place that pays them.
        "reward_coins": list(SEASON_REWARD_COINS),
        "reward_min_points": SEASON_REWARD_MIN_POINTS,
        "mvp_coins": MVP_BONUS_COINS,
        "mvp_min_points": MVP_MIN_POINTS,
        "border_top_n": SEASON_BORDER_TOP_N,
        "extra_days": CLAN_SEASON_EXTRA_DAYS,
        # The real-world prize for finishing #1, for the banner that advertises
        # it. Same reasoning as the coins directly above: one number, shipped
        # with the season, so no screen can invent its own.
        "grand_prize_usd": SEASON_GRAND_PRIZE_USD,
        "grand_prize_what": SEASON_GRAND_PRIZE_WHAT,
        "grand_prize_claim": SEASON_GRAND_PRIZE_CLAIM,
    }


# ── Clan name filter ─────────────────────────────────────────────────────────
# Reuses the server profanity roots but hardened for NAMES: leet-map digits and
# symbols back to letters, drop every separator (spaces / punctuation / repeats)
# and collapse repeated letters, then look for the roots in the collapsed form.
# This catches "S h 1 t  Sq_u-a-d", "b!tchz", "fuuuuck" etc. Word-roots (ass,
# hell…) are only matched as whole words of the ORIGINAL text so "Bass Reef" and
# "Shellfish Crew" stay legal (the Scunthorpe rule).
_LEET_TO_LETTER: Dict[str, str] = {}
def _leet_map() -> Dict[str, str]:
    global _LEET_TO_LETTER
    if not _LEET_TO_LETTER:
        m: Dict[str, str] = {}
        for letter, alts in (_PROF_LEET or {}).items():
            for ch in alts:
                m.setdefault(ch, letter)
        _LEET_TO_LETTER = m
    return _LEET_TO_LETTER


def _collapse_for_filter(text: str) -> str:
    low = str(text or "").lower()
    mapped = "".join(_leet_map().get(ch, ch) for ch in low)
    letters = re.sub(r"[^a-z]", "", mapped)          # drop spaces, digits-left, punctuation
    return re.sub(r"(.)\1{2,}", r"\1\1", letters)     # coooool → cool-ish (keep doubles)


def text_is_profane(text: str) -> bool:
    """Profanity core, shared by names / descriptions / role & event names.
    Any length, never gate this behind a length check."""
    raw = str(text or "")
    if not raw.strip():
        return False
    # 1) the shared strong/word regexes over the raw text (leet + separator aware)
    if _PROF_STRONG_RE is not None and _PROF_STRONG_RE.search(raw):
        return True
    if _PROF_WORD_RE is not None and _PROF_WORD_RE.search(raw):
        return True
    # 2) collapsed-form check: strips ALL separators/leet, then substring-scan
    #    the strong roots and repeat-collapsed variants of them.
    collapsed = _collapse_for_filter(raw)
    doubled_gone = re.sub(r"(.)\1+", r"\1", collapsed)
    for root in _PROF_STRONG:
        r = re.sub(r"(.)\1+", r"\1", root)
        if root in collapsed or r in doubled_gone:
            return True
    # 3) word roots as whole tokens of the leet-mapped text
    tokens = re.split(r"[^a-z]+", "".join(_leet_map().get(c, c) for c in raw.lower()))
    bad_words = set(_PROF_WORDS)
    for tok in tokens:
        t = re.sub(r"(.)\1+", r"\1", tok)
        if tok in bad_words or t in bad_words:
            return True
    return False


def censor_text(text: str) -> str:
    """Mask swears in free text (clan chat / announcements), same regexes as
    the room-chat filter in multiplayer_server."""
    out = str(text or "")
    if not out:
        return out
    if _PROF_STRONG_RE is not None:
        out = _PROF_STRONG_RE.sub(lambda m: "*" * len(m.group(0)), out)
    if _PROF_WORD_RE is not None:
        out = _PROF_WORD_RE.sub(lambda m: m.group(1) + "*" * len(m.group(2)), out)
    return out


def clan_name_check(name: str) -> Tuple[bool, str]:
    """(ok, reason). reason ∈ '', 'length', 'charset', 'inappropriate'."""
    raw = str(name or "").strip()
    if len(raw) < CLAN_NAME_MIN or len(raw) > CLAN_NAME_MAX:
        return False, "length"
    if not re.match(r"^[A-Za-z0-9 .,'&!\-]+$", raw):
        return False, "charset"
    if not re.search(r"[A-Za-z]", raw):
        return False, "charset"
    if text_is_profane(raw):
        return False, "inappropriate"
    return True, ""


# ── Firestore shorthands ─────────────────────────────────────────────────────
def _clans(db):
    return db.collection("clans")


def _users(db):
    return db.collection("users")


def _norm_icon(raw: Any) -> str:
    """"/avatars/x.png" if `raw` is a real critter icon path, else "". Strips the
    cache-buster query and lowercases, the way the client stores them."""
    s = str(raw or "").split("?")[0].strip().lower()
    return s if _ICON_RE.match(s) else ""


def _user_icons(udoc: Dict[str, Any]) -> set:
    """Every critter ONE player may use: the starter set plus their unlocks."""
    out = set(STARTER_ICONS)
    arr = (udoc or {}).get("unlocked_icons")
    if isinstance(arr, list):
        for x in arr:
            n = _norm_icon(x)
            if n:
                out.add(n)
    return out


# Member docs are wanted by three different things on one screen (the roster's
# presence dots, the clan's critter pool, the vote gate), and reading them one
# document at a time was what made the Clans tab feel slow: a 25-member clan
# paid 25 sequential Firestore round-trips per call, and opening the tab then
# opening the clan then voting made that bill three times over.
#
# So: ONE batched get_all() for the whole roster, plus a few seconds of cache so
# the calls that follow each other inside a single interaction reuse it. The TTL
# is far shorter than PRESENCE_FRESH_SEC, so an online dot can't go stale from
# this, and any write that changes what a member doc says (joining, leaving,
# unlocking) goes through a path that drops the cache.
_MEMBERS_CACHE: Dict[str, Any] = {}          # uid -> (fetched_at, doc dict)
_MEMBERS_TTL_SEC = 3.0


def _members_invalidate(uids: Optional[Any] = None) -> None:
    """Forget cached member docs, all of them, or just the ones named.

    The name→account cache goes too: it carries the player's CLAN, and that is
    what decides whether a game was "against another clan". A player who just
    joined must not still read as clanless for the next ten minutes."""
    _REG_CACHE.clear()
    if uids is None:
        _MEMBERS_CACHE.clear()
        return
    for u in uids:
        _MEMBERS_CACHE.pop(str(u), None)


def _member_docs(db, uids: Any, fresh: bool = False) -> Dict[str, Dict[str, Any]]:
    """{uid: user doc} for a whole roster in ONE round-trip.

    Falls back to per-document gets only if the client has no get_all (older
    google-cloud-firestore), so behaviour is identical either way, just slower.
    A uid that doesn't resolve maps to {}, never to a missing key, so callers
    never have to test for absence. `fresh=True` ignores the cache, for the one
    caller that must not refuse something on stale evidence."""
    want = [str(u) for u in (uids or []) if u]
    now = time.time()
    out: Dict[str, Dict[str, Any]] = {}
    misses: List[str] = []
    for u in want:
        hit = None if fresh else _MEMBERS_CACHE.get(u)
        if hit and now - hit[0] < _MEMBERS_TTL_SEC:
            out[u] = hit[1]
        else:
            misses.append(u)
    if misses:
        refs = [_users(db).document(u) for u in misses]
        docs = []
        try:
            get_all = getattr(db, "get_all", None)
            docs = list(get_all(refs)) if get_all else [r.get() for r in refs]
        except Exception as exc:  # noqa: BLE001
            print(f"[clan] member batch read failed: {exc}")
            docs = []
        seen = set()
        for snap in docs:
            try:
                u = str(getattr(snap, "id", "") or (snap.reference.id if snap.reference else ""))
                d = (snap.to_dict() or {}) if getattr(snap, "exists", False) else {}
            except Exception:  # noqa: BLE001
                continue
            if not u:
                continue
            seen.add(u)
            out[u] = d
            _MEMBERS_CACHE[u] = (now, d)
        for u in misses:
            if u not in seen:
                out[u] = {}                  # read failed or no such user
    return out


def _clan_icon_pool(db, clan: Dict[str, Any],
                    member_docs: Optional[Dict[str, Dict[str, Any]]] = None,
                    fresh: bool = False) -> set:
    """Every critter THIS clan may wear: the union of its current members'
    unlocked icons. One member having it unlocked is enough for the clan, and a
    member who leaves takes their exclusive critters with them (the pool is
    recomputed from the live membership every time).

    `member_docs` lets a caller that has already read the roster hand it over
    instead of paying for the same read twice."""
    pool = set(STARTER_ICONS)
    uids = list((clan.get("members") or {}).keys())
    docs = member_docs if member_docs is not None else _member_docs(db, uids, fresh=fresh)
    for m_uid in uids:
        pool |= _user_icons(docs.get(m_uid) or {})
    return pool


def _icon_pool_allowing(db, clan: Dict[str, Any], icon: str) -> Tuple[bool, set]:
    """(is the clan allowed to wear `icon`, the pool that says so).

    The pool is built from cached member docs, so a critter unlocked seconds ago
    could be missing from it, and refusing someone's brand-new critter is worse
    than one extra read. So a miss is re-checked against fresh reads before it
    becomes a "no"; a hit costs nothing extra."""
    pool = _clan_icon_pool(db, clan)
    if icon in pool:
        return True, pool
    pool = _clan_icon_pool(db, clan, fresh=True)
    return icon in pool, pool


def _favorite_from_votes(votes: Dict[str, Any], pool: set) -> Tuple[Optional[str], Dict[str, int]]:
    """Season favourite critter → (winner, tally). Most votes wins, and the
    winner is what the clan's members see on their Clans tab button. Votes for a
    critter nobody in the clan owns any more (its only owner left, or traded it
    away) drop out of the tally, so the badge can never show a critter the clan
    can't wear. Ties break alphabetically so every member sees the same winner."""
    tally: Dict[str, int] = {}
    for raw in (votes or {}).values():
        icon = _norm_icon(raw)
        if icon and icon in pool:
            tally[icon] = tally.get(icon, 0) + 1
    fav = min(tally.items(), key=lambda kv: (-kv[1], kv[0]))[0] if tally else None
    return fav, tally


def _txn_helpers():
    from firebase_admin import firestore
    transactional = getattr(firestore, "transactional", None)
    if transactional is None:
        from google.cloud.firestore_v1 import transactional  # type: ignore
    return transactional


def _new_clan_id() -> str:
    return "c" + secrets.token_hex(5)


def _field_delete() -> Any:
    """Firestore's DELETE_FIELD sentinel, or "" when unavailable.

    set(merge=True) MERGES nested maps: dropping a key from the dict you write
    does NOT remove it from the stored doc. Anything that must actually go away
    has to be written as this sentinel. Readers treat "" as absent."""
    try:
        from firebase_admin import firestore as _fs
        sentinel = getattr(_fs, "DELETE_FIELD", None)
        if sentinel is not None:
            return sentinel
    except Exception:
        pass
    return ""


def _clan_level(xp: int) -> Dict[str, int]:
    """Triangular curve: level n→n+1 costs 100*n XP."""
    xp = max(0, int(xp or 0))
    level, spent = 1, 0
    while xp - spent >= CLAN_XP_PER_LEVEL_STEP * level:
        spent += CLAN_XP_PER_LEVEL_STEP * level
        level += 1
    return {"level": level, "xp": xp, "into": xp - spent,
            "next": CLAN_XP_PER_LEVEL_STEP * level}


# Season counters that are plain running totals, every one of them is fed by
# _apply_game_counters from a game record this server wrote. Kept in one table
# so the slot initialiser, the metric reader and the tests can't drift apart.
_SEASON_COUNTERS = (
    "points", "comp_wins", "comp_losses", "casual_wins", "games",
    "trade_points", "challenge_points", "challenges_completed",
    "win_streak", "last_gain_ts",
    # Season-challenge counters (see CLAN_SEASON_CHALLENGES).
    "games_with_clanmate", "max_clanmates_in_game", "casual_6p",
    "gameplay_points", "comp_points", "casual_points", "podiums",
    "moon_games", "new_members", "rank_ups", "oceans_played", "animals_played",
    "gg_games", "events_held", "team_games", "rival_set", "beat_rival",
    "clan_trades", "team_rival_games", "rank_bonus_points",
    # Points handed out by an admin (scripts/grant_clan_points.py). They count
    # towards the clan's season and lifetime totals, and are kept separable
    # here so "earned by playing" can always be told apart from a gift.
    "bonus_points",
)
# Season counters whose value is "how many distinct things": stored as maps so
# the same member/week can never be counted twice.
_SEASON_SETS = ("comp_win_members", "invert_members", "week_games", "member_ranks")


def _season_slot(clan: Dict[str, Any], sid: str) -> Dict[str, Any]:
    seasons = clan.setdefault("seasons", {})
    slot = seasons.setdefault(sid, {})
    for key in _SEASON_COUNTERS:
        slot.setdefault(key, 0)
    for key in _SEASON_SETS:
        slot.setdefault(key, {})
    slot.setdefault("contrib", {})
    slot.setdefault("critter_votes", {})
    slot.setdefault("challenges_done", [])
    return slot


def _contrib_slot(slot: Dict[str, Any], uid: str, name: str) -> Dict[str, Any]:
    c = slot["contrib"].setdefault(uid, {})
    c.setdefault("name", name or c.get("name") or "Player")
    if name:
        c["name"] = name
    c.setdefault("points", 0)
    c.setdefault("game_points", 0)
    c.setdefault("trade_points", 0)
    c.setdefault("challenge_points", 0)
    c.setdefault("comp_wins", 0)
    c.setdefault("casual_wins", 0)
    c.setdefault("challenges_done", 0)
    c.setdefault("weekly", {})
    c.setdefault("days_active", 0)
    c.setdefault("last_active_date", "")
    c.setdefault("last_trade_date", "")
    c.setdefault("opp_date", "")
    c.setdefault("opp_counts", {})
    c.setdefault("games", 0)
    c.setdefault("gg_games", 0)
    return c


def _activity_push(clan: Dict[str, Any], type_: str, text: str) -> None:
    log = clan.setdefault("activity", [])
    log.insert(0, {"ts": _now(), "type": type_, "text": str(text)[:200]})
    del log[CLAN_ACTIVITY_MAX:]


# Chat message ids sort the way the messages read. `ts` is whole seconds, so
# two lines written in the same second have no order of their own; a per-process
# counter breaks the tie, and the random tail keeps two SERVERS from colliding.
# Zero-padded so plain string sorting matches time order.
_CHAT_SEQ = itertools.count(1)


def _chat_mid(ts: Optional[int] = None) -> str:
    return f"m{int(ts if ts is not None else _now()):011d}_{next(_CHAT_SEQ) % 100000:05d}_{secrets.token_hex(3)}"


def _chat_rows(db, clan_id: str, since: int = 0, cap: int = CLAN_CHAT_FETCH) -> List[Dict[str, Any]]:
    """Clan chat, oldest-last, from `since` (INCLUSIVE) onward.

    Newest-first server-side so a long-lived clan chat never streams its whole
    history on every poll; falls back to a plain capped scan where order_by
    isn't available (tests / old SDKs). Ordered by (ts, id): ids are minted so
    that string order matches write order inside one second."""
    rows: List[Dict[str, Any]] = []
    col = _clans(db).document(clan_id).collection("chat")
    try:
        try:
            cursor = col.order_by("ts", direction="DESCENDING").limit(max(1, cap))
        except Exception:
            cursor = col.limit(600)
        for doc in cursor.stream():
            d = doc.to_dict() or {}
            if int(d.get("ts") or 0) < since or d.get("deleted"):
                continue
            rows.append({"id": doc.id, **d})
    except Exception as exc:  # noqa: BLE001
        print(f"[clan] chat read failed: {exc}")
    rows.sort(key=lambda r: (int(r.get("ts") or 0), str(r.get("id") or "")))
    return rows[-max(1, cap):]


def _chat_system(db, clan_id: str, text: str) -> None:
    """Best-effort system line into the clan chat (never raises)."""
    try:
        _clans(db).document(clan_id).collection("chat").document(_chat_mid()).set({
            "ts": _now(), "uid": "", "name": "", "kind": "system",
            "text": str(text)[:CLAN_CHAT_MAX],
        })
    except Exception as exc:  # noqa: BLE001
        print(f"[clan] system chat write failed: {exc}")


def _daily_goal_for(clan_id: str, date_key: str) -> Dict[str, Any]:
    h = int(hashlib.md5(f"{clan_id}:{date_key}".encode()).hexdigest(), 16)
    return dict(DAILY_GOALS[h % len(DAILY_GOALS)])


def _daily_slot(clan: Dict[str, Any], clan_id: str) -> Dict[str, Any]:
    today = _date_key()
    d = clan.get("daily") or {}
    if d.get("date") != today:
        d = {"date": today, "goal": _daily_goal_for(clan_id, today),
             "games": 0, "points": 0, "trades": 0, "contributors": {}, "done": False}
    d.setdefault("goal", _daily_goal_for(clan_id, today))
    clan["daily"] = d
    return d


# Weekly counters, same split as the season ones.
_WEEKLY_COUNTERS = (
    "games", "points", "trades", "comp_wins", "comp_games", "casual_wins",
    "artificial_first", "casual_4p", "eight_all_human", "comebacks",
    "comp_streak", "comp_streak3", "double_hands", "dominant_wins",
    "oceans_played", "animals_played", "moves", "stars", "star_chains",
    "pool_draws", "deck_draws",
)
_WEEKLY_SETS = ("contributors", "players", "days", "vs_clan_winners",
                "comp_win_members", "humu_members")


def _weekly_slot(clan: Dict[str, Any]) -> Dict[str, Any]:
    wk = _week_key()
    w = clan.get("weekly") or {}
    if w.get("week") != wk:
        w = {"week": wk}
    for key in _WEEKLY_COUNTERS:
        w.setdefault(key, 0)
    for key in _WEEKLY_SETS:
        w.setdefault(key, {})
    w.setdefault("challenges_done", [])
    clan["weekly"] = w
    return w


def _daily_goal_progress(daily: Dict[str, Any]) -> Any:
    gid = (daily.get("goal") or {}).get("id")
    if gid == "games":
        return _num(daily.get("games"))
    if gid == "points":
        return _num(daily.get("points"))
    if gid == "members":
        return len(daily.get("contributors") or {})
    if gid == "trade":
        return _num(daily.get("trades"))
    return 0


def _challenge_metric(weekly: Dict[str, Any], metric: str) -> Any:
    """Progress of one WEEKLY challenge metric.

    Set-valued metrics ("how many DIFFERENT members…") are stored as maps and
    read as their size; everything else is a running total."""
    if metric == "members_active":
        return len(weekly.get("contributors") or {})
    if metric in _WEEKLY_SETS:
        return len(weekly.get(metric) or {})
    if metric == "days_played":
        return len(weekly.get("days") or {})
    if metric == "members_played":
        return len(weekly.get("players") or {})
    return _num(weekly.get(metric))


def _season_metric(slot: Dict[str, Any], metric: str) -> Any:
    """Progress of one SEASON challenge metric.

    Three of them are compound, they are only satisfied when TWO totals are
    both there, so each reports the smaller of its two halves. That makes the
    progress bar honest (it can never sit at 100% while half the work is
    missing) and lets one `progress >= target` test drive every challenge."""
    if metric == "balanced_points":
        return min(_num(slot.get("comp_points")), _num(slot.get("casual_points")))
    if metric == "ecosystem":
        return min(_num(slot.get("animals_played")), _num(slot.get("oceans_played")))
    if metric == "weeks_3plus":
        # "A regular week" is a week the clan actually turned up for. Weeks are
        # counted, never dates, nothing in this system is monthly.
        return sum(1 for n in (slot.get("week_games") or {}).values() if _num(n) >= 3)
    if metric in _SEASON_SETS:
        return len(slot.get(metric) or {})
    return _num(slot.get(metric))


def _challenge_target(ch: Dict[str, Any], clan: Optional[Dict[str, Any]] = None) -> int:
    """A challenge's target. "members" means the whole clan has to do it, so it
    scales with the roster, and a clan of one can't claim it by being alone."""
    target = ch.get("target")
    if target == "members":
        return max(2, len((clan or {}).get("members") or {}))
    try:
        return int(target or 0)
    except (TypeError, ValueError):
        return 0


# ── The one award engine ─────────────────────────────────────────────────────
# Every point ever granted flows through _apply_award inside a Firestore
# transaction: dedup ledger create + clan season/contrib counters + weekly cap
# + daily goal + weekly challenge sweep + activity, all-or-nothing.
def _apply_award(db, clan_id: str, uid: str, name: str, *, kind: str,
                 points: Any, dedup_id: str, activity_text: str,
                 counts_game: bool = False, is_comp_win: bool = False,
                 is_comp_loss: bool = False, is_casual_win: bool = False,
                 is_trade: bool = False, count_trade: bool = True,
                 meta: Optional[Dict[str, Any]] = None,
                 game: Optional[Dict[str, Any]] = None,
                 ) -> Dict[str, Any]:
    transactional = _txn_helpers()
    clan_ref = _clans(db).document(clan_id)
    ledger_ref = clan_ref.collection("ledger").document(dedup_id)
    txn = db.transaction()
    sid = _clan_sid()
    wk = _week_key()
    today = _date_key()
    out: Dict[str, Any] = {}

    @transactional
    def _run(t) -> Dict[str, Any]:
        led = ledger_ref.get(transaction=t)
        if led.exists:
            return {"ok": False, "error": "already_claimed"}
        snap = clan_ref.get(transaction=t)
        if not snap.exists:
            return {"ok": False, "error": "no_clan"}
        clan = snap.to_dict() or {}
        if uid not in (clan.get("members") or {}):
            return {"ok": False, "error": "not_member"}

        slot = _season_slot(clan, sid)
        contrib = _contrib_slot(slot, uid, name)
        weekly_used = _num(contrib["weekly"].get(wk))
        want = _num(points)
        granted = _num(max(0, min(want, WEEKLY_POINT_CAP - weekly_used)))
        capped = granted < want

        # Season + contributor counters (wins/losses/games record even when the
        # points themselves were capped or zero: "Clan wins and losses are
        # still recorded in clan statistics").
        if counts_game:
            slot["games"] = _num(slot.get("games")) + 1
            contrib["games"] = _num(contrib.get("games")) + 1
        if is_comp_win:
            slot["comp_wins"] = _num(slot.get("comp_wins")) + 1
            contrib["comp_wins"] = _num(contrib.get("comp_wins")) + 1
        if is_comp_loss:
            slot["comp_losses"] = _num(slot.get("comp_losses")) + 1
        if is_casual_win:
            slot["casual_wins"] = _num(slot.get("casual_wins")) + 1
            contrib["casual_wins"] = _num(contrib.get("casual_wins")) + 1
        if is_comp_win or is_casual_win:
            slot["win_streak"] = _num(slot.get("win_streak")) + 1
        elif counts_game:
            slot["win_streak"] = 0

        if granted > 0:
            slot["points"] = _num(_num(slot.get("points")) + granted)
            slot["last_gain_ts"] = _now()
            contrib["points"] = _num(_num(contrib.get("points")) + granted)
            contrib["weekly"][wk] = _num(weekly_used + granted)
            if is_trade:
                slot["trade_points"] = _num(_num(slot.get("trade_points")) + granted)
                contrib["trade_points"] = _num(_num(contrib.get("trade_points")) + granted)
            else:
                contrib["game_points"] = _num(_num(contrib.get("game_points")) + granted)
                # "Through gameplay": Rising Tide / Powerful Current / Balanced
                # Waters all mean points earned by PLAYING, so trade points and
                # challenge bonuses are deliberately not in here.
                slot["gameplay_points"] = _num(_num(slot.get("gameplay_points")) + granted)
                if kind == "comp":
                    slot["comp_points"] = _num(_num(slot.get("comp_points")) + granted)
                elif kind == "casual":
                    slot["casual_points"] = _num(_num(slot.get("casual_points")) + granted)
            life = clan.setdefault("lifetime", {})
            life["points"] = _num(_num(life.get("points")) + granted)
        if is_trade:
            contrib["last_trade_date"] = today

        # Days-active streak for the contributor (drives "most active member").
        if contrib.get("last_active_date") != today:
            contrib["last_active_date"] = today
            contrib["days_active"] = _num(contrib.get("days_active")) + 1

        # Shared daily goal progress.
        daily = _daily_slot(clan, clan_id)
        if counts_game:
            daily["games"] = _num(daily.get("games")) + 1
        if is_trade and count_trade:
            daily["trades"] = _num(daily.get("trades")) + 1
        if granted > 0:
            daily["points"] = _num(_num(daily.get("points")) + granted)
            daily.setdefault("contributors", {})[uid] = True
        goal_done_now = False
        if not daily.get("done") and _daily_goal_progress(daily) >= _num((daily.get("goal") or {}).get("target") or 9999):
            daily["done"] = True
            goal_done_now = True
            clan["xp"] = int(clan.get("xp") or 0) + DAILY_GOAL_XP
            _activity_push(clan, "daily_goal",
                           f"🌞 Daily goal complete: {(daily.get('goal') or {}).get('label')} (+{DAILY_GOAL_XP} Clan XP)")

        # Weekly counters.
        weekly = _weekly_slot(clan)
        if counts_game:
            weekly["games"] = _num(weekly.get("games")) + 1
        # A trade has TWO sides and pays both of them, but it is still ONE
        # trade, the clan-wide counters move for the first side only, or the
        # clan would read 8 real trades as 16.
        if is_trade and count_trade:
            weekly["trades"] = _num(weekly.get("trades")) + 1
            slot["clan_trades"] = _num(slot.get("clan_trades")) + 1
        if is_comp_win:
            weekly["comp_wins"] = _num(weekly.get("comp_wins")) + 1
            weekly.setdefault("comp_win_members", {})[uid] = 1
            slot.setdefault("comp_win_members", {})[uid] = 1
        if is_casual_win:
            weekly["casual_wins"] = _num(weekly.get("casual_wins")) + 1
        if granted > 0:
            weekly["points"] = _num(_num(weekly.get("points")) + granted)
            wc = weekly.setdefault("contributors", {})
            wc[uid] = _num(_num(wc.get(uid)) + granted)

        # Everything a finished GAME proves: see _apply_game_counters.
        if game:
            _apply_game_counters(clan, slot, contrib, weekly, uid, game)

        # Challenge sweeps. Weekly first, then season, so a game that finishes
        # both is recorded in the order a player would read it.
        challenges_done_now: List[Dict[str, Any]] = []
        _sweep_challenges(clan, slot, weekly, CLAN_WEEKLY_CHALLENGES,
                          weekly.setdefault("challenges_done", []),
                          lambda ch: _challenge_metric(weekly, str(ch.get("metric") or "")),
                          "Weekly", challenges_done_now)
        _sweep_challenges(clan, slot, weekly, CLAN_SEASON_CHALLENGES,
                          slot.setdefault("challenges_done", []),
                          lambda ch: _season_metric(slot, str(ch.get("metric") or "")),
                          "Season", challenges_done_now)

        if activity_text:
            suffix = " (weekly cap reached)" if (capped and granted == 0) else ""
            _activity_push(clan, kind, activity_text.replace("{pts}", str(granted)) + suffix)

        t.set(ledger_ref, {
            "ts": _now(), "uid": uid, "name": name, "kind": kind,
            "points": granted, "requested": want, "week": wk,
            "date": today, "season": sid, "meta": meta or {},
        })
        t.set(clan_ref, clan)
        return {"ok": True, "granted": granted, "capped": capped,
                "goal_done": goal_done_now, "challenges_done": challenges_done_now,
                "clan_name": clan.get("name"), "season_points": slot["points"]}

    try:
        out = _run(txn)
    except Exception as exc:  # noqa: BLE001
        print(f"[clan] award txn failed ({kind} {dedup_id}): {exc}")
        return {"ok": False, "error": "award_failed"}
    if out.get("ok"):
        _lb_invalidate()
        if out.get("goal_done"):
            _chat_system(db, clan_id, "🌞 Today's clan goal is complete! +25 Clan XP")
        for ch in out.get("challenges_done") or []:
            _chat_system(db, clan_id, f"🏁 {ch.get('scope', 'Weekly')} challenge complete: {ch.get('name')}!")
            _grant_challenge_xp(db, clan_id, ch)
    return out


def _sweep_challenges(clan: Dict[str, Any], slot: Dict[str, Any],
                      weekly: Dict[str, Any], table: List[Dict[str, Any]],
                      done_list: List[str], progress_of: Callable[[Dict[str, Any]], Any],
                      scope: str, out: List[Dict[str, Any]]) -> None:
    """Complete every challenge in one table whose counter has reached target.

    Runs INSIDE the award transaction against the very counters the same
    transaction just moved, so a challenge can never be paid twice (the id in
    done_list is written in the same commit as its points) and can never be
    missed (nothing else can move a counter between the bump and this check).

    A challenge with no target, or an unknown metric, can never complete:
    _challenge_target returning 0 would otherwise fire everything at once."""
    for ch in table:
        cid = str(ch.get("id") or "")
        if not cid or cid in done_list:
            continue
        target = _challenge_target(ch, clan)
        if target <= 0:
            continue
        if _num(progress_of(ch)) < target:
            continue
        done_list.append(cid)
        cp = _num(ch.get("clan_points"))
        slot["challenge_points"] = _num(_num(slot.get("challenge_points")) + cp)
        slot["challenges_completed"] = _num(slot.get("challenges_completed")) + 1
        if cp:
            slot["points"] = _num(_num(slot.get("points")) + cp)
            slot["last_gain_ts"] = _now()
        _activity_push(clan, "challenge",
                       f"🏁 {scope} challenge complete: {ch.get('name')} (+{cp} Clan Points)")
        entry = dict(ch)
        entry["scope"] = scope
        out.append(entry)


# ── What a finished game proves ──────────────────────────────────────────────
# `game` is the normalised, server-verified summary claim_game_points builds
# from THIS server's own game record (see _game_facts). Every clan-challenge
# counter that a game can move is moved here and nowhere else, so there is one
# place to read when asking "what does this challenge actually count?".
def _apply_game_counters(clan: Dict[str, Any], slot: Dict[str, Any],
                         contrib: Dict[str, Any], weekly: Dict[str, Any],
                         uid: str, game: Dict[str, Any]) -> None:
    today = _date_key()
    wk = _week_key()
    is_comp = bool(game.get("competitive"))

    # Participation: who played, and on which days.
    weekly.setdefault("players", {})[uid] = 1
    weekly.setdefault("days", {})[today] = 1
    slot.setdefault("week_games", {})[wk] = _num(slot["week_games"].get(wk)) + 1

    if is_comp:
        weekly["comp_games"] = _num(weekly.get("comp_games")) + 1
        if game.get("double_hands"):
            weekly["double_hands"] = _num(weekly.get("double_hands")) + 1
        if game.get("dominant_win"):
            weekly["dominant_wins"] = _num(weekly.get("dominant_wins")) + 1
        # Clan-wide competitive win streak. Every third win in a row is one
        # "three-game streak" and the count restarts, so 6 straight wins is
        # two streaks, not four overlapping ones.
        if game.get("won"):
            streak = _num(weekly.get("comp_streak")) + 1
            if streak >= 3:
                weekly["comp_streak3"] = _num(weekly.get("comp_streak3")) + 1
                streak = 0
            weekly["comp_streak"] = streak
        else:
            weekly["comp_streak"] = 0
    else:
        players = int(game.get("player_count") or 0)
        if players >= 4:
            weekly["casual_4p"] = _num(weekly.get("casual_4p")) + 1
        if players >= 6:
            slot["casual_6p"] = _num(slot.get("casual_6p")) + 1
        if players >= 8 and game.get("all_real_people"):
            weekly["eight_all_human"] = _num(weekly.get("eight_all_human")) + 1
        # A podium is a placement that actually PLACES, the same bar the
        # placement points use, so second in a two-player game (which is last)
        # is not a podium finish and third in a three-player game isn't either.
        place = int(game.get("place") or 0)
        if place == 1 or (place == 2 and players > 2) or (place == 3 and players >= 4):
            slot["podiums"] = _num(slot.get("podiums")) + 1
        if place == 1:
            if game.get("comeback"):
                weekly["comebacks"] = _num(weekly.get("comebacks")) + 1
            if game.get("vs_other_clan"):
                weekly.setdefault("vs_clan_winners", {})[uid] = 1

    # Clanmates in the game (both are "how many of US were in there", which is
    # why a solo game with bots can never move them).
    mates = int(game.get("clanmates") or 0)
    if mates >= 2:
        slot["games_with_clanmate"] = _num(slot.get("games_with_clanmate")) + 1
    slot["max_clanmates_in_game"] = max(_num(slot.get("max_clanmates_in_game")), mates)

    if game.get("team_mode"):
        slot["team_games"] = _num(slot.get("team_games")) + 1
        if game.get("team_rival"):
            slot["team_rival_games"] = _num(slot.get("team_rival_games")) + 1

    # Per-play telemetry, summed over every seat this player owned.
    for key in ("oceans_played", "animals_played", "moves", "stars",
                "star_chains", "pool_draws", "deck_draws"):
        add = _num(game.get(key))
        if add:
            weekly[key] = _num(weekly.get(key)) + add
    for key in ("oceans_played", "animals_played"):
        add = _num(game.get(key))
        if add:
            slot[key] = _num(slot.get(key)) + add

    if game.get("artificial_first"):
        weekly["artificial_first"] = _num(weekly.get("artificial_first")) + 1
    if game.get("humu"):
        weekly.setdefault("humu_members", {})[uid] = 1
    if game.get("moon"):
        slot["moon_games"] = _num(slot.get("moon_games")) + 1
    if game.get("said_gg"):
        slot["gg_games"] = _num(slot.get("gg_games")) + 1
        contrib["gg_games"] = _num(contrib.get("gg_games")) + 1
    if game.get("has_invertebrates"):
        slot.setdefault("invert_members", {})[uid] = 1


def _grant_challenge_xp(db, clan_id: str, ch: Dict[str, Any]) -> None:
    """Member XP for a finished weekly challenge, only members whose weekly
    contribution meets the challenge's min_contribution (individual
    participation required; inactive members get nothing)."""
    xp = int(ch.get("member_xp") or 0)
    need = int(ch.get("min_contribution") or 1)
    if xp <= 0:
        return
    try:
        snap = _clans(db).document(clan_id).get()
        clan = snap.to_dict() or {}
        weekly = clan.get("weekly") or {}
        for uid, pts in (weekly.get("contributors") or {}).items():
            if _num(pts) < need:
                continue
            uref = _users(db).document(uid)
            usnap = uref.get()
            stats = ((usnap.to_dict() or {}).get("stats") or {}) if usnap.exists else {}
            new_xp = int(stats.get("total_xp") or 0) + xp
            lvl, xp_cur, xp_goal = _level_progress(new_xp)
            uref.set({"stats": {
                "total_xp": new_xp, "level": lvl, "player_level": lvl,
                "xp_current": xp_cur, "level_xp_current": xp_cur,
                "xp_goal": xp_goal, "level_xp_goal": xp_goal,
            }}, merge=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[clan] challenge XP grant failed: {exc}")


# ── Membership / permission engine ───────────────────────────────────────────
def _member_of(clan: Dict[str, Any], uid: str) -> Optional[Dict[str, Any]]:
    return (clan.get("members") or {}).get(uid)


def _role_rank(clan: Dict[str, Any], mem: Optional[Dict[str, Any]]) -> int:
    if not mem:
        return 0
    return {"owner": 4, "captain": 3, "recruiter": 2, "member": 1}.get(mem.get("role"), 1)


def _custom_perms(clan: Dict[str, Any], mem: Optional[Dict[str, Any]]) -> Dict[str, bool]:
    if not mem or not mem.get("custom_role_id"):
        return {}
    for r in clan.get("custom_roles") or []:
        if r.get("id") == mem.get("custom_role_id"):
            return dict(r.get("perms") or {})
    return {}


def _has_perm(clan: Dict[str, Any], uid: str, perm: str) -> bool:
    """One permission gate for every moderated action. Owner: everything.
    Captain: invite/review/remove/announce/pin/moderate/events/challenges/roles.
    Recruiter: invite + review. Custom roles: exactly their granted perms."""
    mem = _member_of(clan, uid)
    if not mem:
        return False
    role = mem.get("role")
    if role == "owner":
        return True
    if role == "captain":
        if perm in ("invite", "review_requests", "remove_members",
                    "post_announcements", "pin_announcements", "moderate_chat",
                    "create_events", "manage_challenges", "change_roles"):
            return True
        if perm == "edit_custom_roles":
            return bool(clan.get("captains_can_edit_roles"))
        return False
    if role == "recruiter" and perm in ("invite", "review_requests"):
        return True
    return bool(_custom_perms(clan, mem).get(perm))


# ── Public shapers ───────────────────────────────────────────────────────────
def _clan_card(clan_id: str, clan: Dict[str, Any], sid: str) -> Dict[str, Any]:
    slot = (clan.get("seasons") or {}).get(sid) or {}
    return {
        "id": clan_id,
        "name": clan.get("name"),
        "icon": clan.get("icon"),
        "icon_name": clan.get("icon_name"),
        "description": clan.get("description") or "",
        "privacy": clan.get("privacy"),
        # WHETHER there is a password, never the password or its hash. The owner
        # screen needs it to know if "switch to Password mode" already has one
        # to fall back on; nobody else can do anything with a boolean.
        "has_password": isinstance(clan.get("join_password"), dict),
        "member_count": len(clan.get("members") or {}),
        "max_members": CLAN_MAX_MEMBERS,
        "points": _num(slot.get("points")),
        "comp_wins": _num(slot.get("comp_wins")),
        "casual_wins": _num(slot.get("casual_wins")),
        "challenge_points": _num(slot.get("challenge_points")),
        "challenges_completed": _num(slot.get("challenges_completed")),
        "trade_points": _num(slot.get("trade_points")),
        "games": _num(slot.get("games")),
        "comp_losses": _num(slot.get("comp_losses")),
        "level": _clan_level(int(clan.get("xp") or 0))["level"],
        "season_border": clan.get("season_border"),
    }


def _lb_sort_key(card: Dict[str, Any]) -> Tuple:
    """Season ranking with the spec'd tiebreakers: points, comp wins,
    challenges completed, casual wins, distinct contributors, first-to-score."""
    return (-card["points"], -card["comp_wins"], -card["challenges_completed"],
            -card["casual_wins"], -card.get("_contributors", 0), card.get("_last_gain_ts", 1 << 60))


_LB_TTL_SEC = 20.0
# The standings are a whole-collection read, they cost seconds from Render, and
# opening the Clans tab needs them before it can draw anything: /home, /browse,
# /leaderboard and every clan profile all ask. Cached briefly, and served
# instantly even when that brief window has passed, with the refresh running
# behind the reader instead of in front of them. hard_ttl is an hour: a podium
# that is minutes old for the moment it takes to refresh beats a blank tab.
_LB_WARM = warm_cache.WarmCache("clan-standings", ttl=_LB_TTL_SEC,
                                hard_ttl=3600.0)


def _lb_invalidate() -> None:
    """Drop the standings cache. Called after every write that can change what
    the leaderboard shows, so a brand-new clan (or a just-earned point) is
    never missing from the board the player looks at one second later."""
    _LB_WARM.invalidate()


def _leaderboard_rows(db, sid: str, cap: int = 500, fresh: bool = False) -> List[Dict[str, Any]]:
    """Season standings for every clan, from the warm cache.

    Anything that must observe its own write passes fresh=True, which really
    does re-read (the season payout does; ordinary points land well inside the
    TTL). Callers mutate the rows they get back (browse stamps `joinable`, home
    stamps `rank`), so every caller gets its own deep copy."""
    rows = _LB_WARM.get(f"{sid}|{cap}",
                        lambda: _scan_leaderboard_rows(db, sid, cap),
                        fresh=fresh)
    return copy.deepcopy(rows or [])


def prewarm_standings() -> None:
    """Fill the current season's standings off the request path, at boot."""
    db = _get_firestore()
    if db is None:
        return
    sid = _clan_sid()
    _LB_WARM.warm(f"{sid}|500", lambda: _scan_leaderboard_rows(db, sid, 500))


def _scan_leaderboard_rows(db, sid: str, cap: int = 500) -> Optional[List[Dict[str, Any]]]:
    """The real whole-collection scan. None means "the scan failed", which the
    cache reads as "keep the standings you already have"."""
    rows: List[Dict[str, Any]] = []
    try:
        for doc in _clans(db).limit(cap).stream():
            clan = doc.to_dict() or {}
            card = _clan_card(doc.id, clan, sid)
            slot = (clan.get("seasons") or {}).get(sid) or {}
            contrib = slot.get("contrib") or {}
            card["_contributors"] = sum(1 for c in contrib.values() if _num(c.get("points")) > 0)
            card["_last_gain_ts"] = int(slot.get("last_gain_ts") or 0) or (1 << 60)
            rows.append(card)
    except Exception as exc:  # noqa: BLE001
        print(f"[clan] leaderboard scan failed: {exc}")
        return None
    rows.sort(key=_lb_sort_key)
    wl_record = lambda c: f"{c['comp_wins'] + c['casual_wins']}-{max(0, c['games'] - c['comp_wins'] - c['casual_wins'])}"
    for i, c in enumerate(rows):
        c["rank"] = i + 1
        c["record"] = wl_record(c)
        c.pop("_contributors", None)
        c.pop("_last_gain_ts", None)
    return rows


# ── Season finalize (lazy, idempotent) ───────────────────────────────────────
def _finalize_season_bonuses(db, sid: str) -> None:
    """The two Clan Point bonuses that can only be settled once a season ends.

      • Rival Reckoning: did we finish ahead of the clan we called out? Judged
        on the points BOTH clans had earned during the season, snapshotted
        before any of these bonuses land, so two rivals can't leapfrog each
        other off each other's bonuses.
      • Competitive rank, every current member's division is worth Critter
        Coins to them and Clan Points to the squad (COMP_RANK_SEASON_REWARDS).

    Both write into the season being finalized, before it is ranked. The whole
    pass runs exactly once: the caller holds the clan_meta create() lock."""
    docs: Dict[str, Dict[str, Any]] = {}
    try:
        for doc in _clans(db).limit(500).stream():
            docs[doc.id] = doc.to_dict() or {}
    except Exception as exc:  # noqa: BLE001
        print(f"[clan] bonus pass scan failed: {exc}")
        return
    earned = {cid: _num(((c.get("seasons") or {}).get(sid) or {}).get("points"))
              for cid, c in docs.items()}

    for clan_id, clan in docs.items():
        slot = (clan.get("seasons") or {}).get(sid) or {}
        members = list((clan.get("members") or {}).keys())
        add: Dict[str, Any] = {}
        set_to: Dict[str, Any] = {}
        rival_id = str((clan.get("rivals") or {}).get(sid) or "")
        if rival_id and rival_id in earned and earned.get(clan_id, 0) > earned[rival_id]:
            set_to["beat_rival"] = 1

        # Competitive rank payout, member by member.
        rank_points = 0
        member_docs = _member_docs(db, members, fresh=True)
        for m_uid in members:
            udoc = member_docs.get(m_uid) or {}
            stats = udoc.get("stats") or {}
            tier = _rank_tier(stats.get("rank_competitive"))
            reward = COMP_RANK_SEASON_REWARDS.get(tier) or {}
            coins = int(reward.get("coins") or 0)
            rank_points += int(reward.get("clan_points") or 0)
            if coins <= 0:
                continue
            try:
                uref = _users(db).document(m_uid)
                uref.set({"stats": {"critter_coins": int(stats.get("critter_coins") or 0) + coins}},
                         merge=True)
            except Exception as exc:  # noqa: BLE001
                print(f"[clan] rank coin payout for {m_uid} failed: {exc}")
        if rank_points:
            add["points"] = rank_points
            add["rank_bonus_points"] = rank_points
        if not add and not set_to:
            continue
        note = ""
        if rank_points:
            note = f"🏅 Competitive rank bonus: +{rank_points} Clan Points from the squad's ranks"
        _bump_season(db, clan_id, sid=sid, add=add, set_to=set_to, activity=note)
        if set_to.get("beat_rival"):
            _chat_system(db, clan_id, "⚔️ We finished the season ahead of our rival!")


def ensure_season_finalized(db) -> None:
    """First clan call inside a new quarter finalizes the previous one:
    snapshot standings, pay coin rewards to eligible members of the top 3,
    stamp badges/borders, pick each clan's MVP, write clan_meta/season_<sid>.
    The meta doc's create() is the cross-process idempotency lock."""
    sid_now = _clan_sid()
    prev = _prev_sid(sid_now)
    if prev in _FINALIZED_SIDS:
        return
    with _FINALIZE_LOCK:
        if prev in _FINALIZED_SIDS:
            return
        meta_ref = db.collection("clan_meta").document(f"season_{prev}")
        try:
            if meta_ref.get().exists:
                _FINALIZED_SIDS.add(prev)
                return
        except Exception:
            return
        try:
            meta_ref.create({"finalizing": True, "ts": _now()})
        except Exception:
            _FINALIZED_SIDS.add(prev)   # someone else won the race
            return
        # End-of-season Clan Point bonuses (rival result + every member's
        # competitive rank) are added to the season BEFORE it is ranked, so
        # they can still change the placings, that is the point of "the higher
        # your rank, the more you bring your squad". Only the process that won
        # the meta_ref.create() race above ever runs this.
        try:
            _finalize_season_bonuses(db, prev)
        except Exception as exc:  # noqa: BLE001
            print(f"[clan] season bonus pass failed: {exc}")
        # Anything to finalize at all? (Fresh install: prev quarter has no data.)
        rows = _leaderboard_rows(db, prev, fresh=True)   # paying out, never off a cache
        rows = [r for r in rows if r["points"] > 0 or r["games"] > 0]
        results: List[Dict[str, Any]] = []
        try:
            start, end = _season_bounds(prev)
            for r in rows:
                clan_ref = _clans(db).document(r["id"])
                snap = clan_ref.get()
                if not snap.exists:
                    continue
                clan = snap.to_dict() or {}
                slot = (clan.get("seasons") or {}).get(prev) or {}
                contrib: Dict[str, Any] = slot.get("contrib") or {}
                members: Dict[str, Any] = clan.get("members") or {}
                place = r["rank"]
                coins_per = SEASON_REWARD_COINS[place - 1] if place <= 3 else 0
                rewarded: Dict[str, int] = {}
                badges: List[str] = []
                # Coin + badge rewards: current members only, ≥10 season points.
                for uid, c in contrib.items():
                    if uid not in members:
                        continue      # left the clan → no seasonal rewards
                    if _num(c.get("points")) < SEASON_REWARD_MIN_POINTS:
                        continue
                    updates: Dict[str, Any] = {}
                    if coins_per:
                        rewarded[uid] = coins_per
                    if place <= 3:
                        badges.append(uid)
                    try:
                        uref = _users(db).document(uid)
                        usnap = uref.get()
                        udoc = usnap.to_dict() or {}
                        stats = udoc.get("stats") or {}
                        if coins_per:
                            updates["stats"] = {"critter_coins": int(stats.get("critter_coins") or 0) + coins_per}
                        if place <= 3:
                            blist = [b for b in (udoc.get("clan_badges") or []) if isinstance(b, dict)]
                            blist.append({"type": "season", "place": place,
                                          "season": _season_number(prev), "sid": prev,
                                          "clan": clan.get("name"), "ts": _now()})
                            updates["clan_badges"] = blist
                        if updates:
                            uref.set(updates, merge=True)
                    except Exception as exc:  # noqa: BLE001
                        print(f"[clan] finalize reward for {uid} failed: {exc}")
                # MVP: highest-points CURRENT member, ≥25 pts, not mainly trades,
                # no confirmed violations. Tiebreak: game points → comp wins →
                # challenges → days active.
                mvp = None
                cands = []
                for uid, c in contrib.items():
                    if uid not in members:
                        continue
                    pts = _num(c.get("points"))
                    if pts < MVP_MIN_POINTS:
                        continue
                    if _num(c.get("trade_points")) * 2 > pts:
                        continue      # majority-trades can't take MVP
                    if c.get("violations_confirmed"):
                        continue
                    cands.append((uid, c))
                cands.sort(key=lambda uc: (
                    -_num(uc[1].get("points")),
                    -_num(uc[1].get("game_points")),
                    -_num(uc[1].get("comp_wins")),
                    -_num(uc[1].get("challenges_done")),
                    -_num(uc[1].get("days_active"))))
                if cands:
                    uid, c = cands[0]
                    mvp = {"uid": uid, "name": c.get("name") or "Player",
                           "points": _num(c.get("points"))}
                    try:
                        uref = _users(db).document(uid)
                        usnap = uref.get()
                        udoc = usnap.to_dict() or {}
                        stats = udoc.get("stats") or {}
                        blist = [b for b in (udoc.get("clan_badges") or []) if isinstance(b, dict)]
                        blist.append({"type": "mvp", "season": _season_number(prev),
                                      "sid": prev, "clan": clan.get("name"),
                                      "title": f"Season {_season_number(prev)} Clan MVP",
                                      "ts": _now()})
                        uref.set({"clan_badges": blist,
                                  "stats": {"critter_coins": int(stats.get("critter_coins") or 0) + MVP_BONUS_COINS}},
                                 merge=True)
                        rewarded[uid] = int(rewarded.get(uid) or 0) + MVP_BONUS_COINS
                    except Exception as exc:  # noqa: BLE001
                        print(f"[clan] finalize MVP reward failed: {exc}")
                # Most-active + best-comp member for the results screen.
                most_active = max(contrib.items(),
                                  key=lambda uc: int(uc[1].get("days_active") or 0),
                                  default=(None, None))
                top_comp = max(contrib.items(),
                               key=lambda uc: int(uc[1].get("comp_wins") or 0),
                               default=(None, None))
                entry = {
                    "clan_id": r["id"], "name": clan.get("name"), "icon": clan.get("icon"),
                    "rank": place, "points": r["points"], "comp_wins": r["comp_wins"],
                    "casual_wins": r["casual_wins"], "challenge_points": r["challenge_points"],
                    "challenges_completed": r["challenges_completed"],
                    "trade_points": r["trade_points"], "games": r["games"],
                    "record": r["record"], "member_count": r["member_count"],
                    "mvp": mvp, "coins_per_member": coins_per,
                    "rewarded": rewarded,
                    "most_active": ({"uid": most_active[0], "name": (most_active[1] or {}).get("name"),
                                     "days": int((most_active[1] or {}).get("days_active") or 0)}
                                    if most_active[0] else None),
                    "top_comp": ({"uid": top_comp[0], "name": (top_comp[1] or {}).get("name"),
                                  "wins": int((top_comp[1] or {}).get("comp_wins") or 0)}
                                 if top_comp[0] else None),
                }
                results.append(entry)
                # Stamp the clan doc: previous-season snapshot, MVP chip window,
                # top-10 border, activity note.
                stamp: Dict[str, Any] = {}
                prev_results = clan.get("prev_results") or {}
                prev_results[prev] = {k: entry[k] for k in
                                      ("rank", "points", "mvp", "coins_per_member", "record")}
                stamp["prev_results"] = prev_results
                if place <= SEASON_BORDER_TOP_N:
                    stamp["season_border"] = {"sid": prev, "rank": place,
                                              "season": _season_number(prev)}
                if mvp:
                    stamp["mvp_chip"] = {"uid": mvp["uid"], "name": mvp["name"],
                                         "sid": prev, "season": _season_number(prev),
                                         "until": end + MVP_ICON_DAYS * 24 * 3600}
                _activity_push(clan, "season",
                               f"🏆 Season {_season_number(prev)} final: #{place}"
                               + (f": rewards paid to {len(rewarded)} member(s)" if rewarded else ""))
                stamp["activity"] = clan.get("activity")
                # Keep the doc from growing forever: only the last SEASON_KEEP
                # quarters of per-member breakdowns and placements stay on the
                # clan. Older finals live on in clan_meta/season_<sid>.
                # merge=True MERGES nested maps (it never drops keys), so the
                # retired quarters have to be explicit field deletes.
                delete_sentinel = _field_delete()
                for field, data in (("seasons", clan.get("seasons") or {}),
                                    ("prev_results", prev_results)):
                    if len(data) <= SEASON_KEEP:
                        continue
                    drop = sorted(data.keys(), reverse=True)[SEASON_KEEP:]
                    merged = dict(stamp.get(field) or {})
                    for old_sid in drop:
                        merged[old_sid] = delete_sentinel
                    stamp[field] = merged
                clan_ref.set(stamp, merge=True)
                _chat_system(db, r["id"],
                             f"🏆 Season {_season_number(prev)} ({_season_name(prev)}) finished: "
                             f"{clan.get('name')} placed #{place}!"
                             + (f" MVP: {mvp['name']} 🎖" if mvp else ""))
            meta_ref.set({
                "sid": prev, "season": _season_number(prev), "name": _season_name(prev),
                "finalized": True, "finalizing": False, "ts": _now(),
                "standings": results[:100],
                "next_season": _season_public(sid_now),
            })
            _FINALIZED_SIDS.add(prev)
        except Exception as exc:  # noqa: BLE001
            print(f"[clan] season finalize failed: {exc}")
            try:
                meta_ref.set({"finalizing": False, "error": str(exc)[:200]}, merge=True)
            except Exception:
                pass


# ── Finding a player to invite ───────────────────────────────────────────────
# Clan invites are addressed by FRIEND CODE (the 4-digit number on Player Home),
# not by username, a username is easy to mistype and easy to impersonate, and
# most people already trade friend codes to add each other.
_FC_ONLY_RE  = re.compile(r"^#?(\d{3,6})$")            # "2809" / "#2809"
_FC_NAMED_RE = re.compile(r"^(.+?)\s*[#\s]\s*(\d{3,6})$")  # "Twin Midi 9113"


def _uid_by_friend_code(db, code: str) -> Tuple[str, str]:
    """Bare friend code → (uid, error). Codes are random 4-digit numbers and are
    NOT unique on their own, so a collision asks for the name as well rather
    than guessing and inviting a stranger."""
    # Signup writes the code as a string. Query the number too: an == filter
    # for "2809" does not match a doc holding 2809, and one legacy account
    # stored that way would look like "no such player" forever.
    try:
        rows = list(_users(db).where("friend_code", "==", str(code)).limit(5).get())
        if not rows and str(code).isdigit():
            rows = list(_users(db).where("friend_code", "==", int(code)).limit(5).get())
    except Exception as exc:  # noqa: BLE001
        print(f"[clan] friend-code lookup failed: {exc}")
        return "", "no_user"
    if not rows:
        return "", "no_user"
    if len(rows) > 1:
        return "", "ambiguous_code"
    return rows[0].id, ""


def _uid_by_name_and_code(db, name: str, code: str) -> str:
    """friend_lookup/{nicknameLower}_{code}, the exact doc the Friends tab
    writes at signup and rewrites on every nickname change."""
    key = f"{str(name or '').strip().lower()}_{code}"
    try:
        snap = db.collection("friend_lookup").document(key).get()
        if snap.exists:
            return str((snap.to_dict() or {}).get("uid") or "")
    except Exception as exc:  # noqa: BLE001
        print(f"[clan] friend_lookup failed: {exc}")
    return ""


def _resolve_invitee(db, raw: str) -> Tuple[str, str]:
    """Whatever was typed into the invite box → (uid, error).

    Accepts "2809", "#2809", "Twin Midi 9113" and "Twin Midi#9113". A plain
    username still resolves too, because the profile and Messages invite
    buttons pass a name, and because a nickname can itself contain digits
    ("Player123"), the name+code split is only trusted when friend_lookup
    actually has that pair."""
    txt = str(raw or "").strip()
    if not txt:
        return "", "no_user"
    m = _FC_ONLY_RE.match(txt)
    if m:
        return _uid_by_friend_code(db, m.group(1))
    code = ""
    m = _FC_NAMED_RE.match(txt)
    if m:
        uid = _uid_by_name_and_code(db, m.group(1), m.group(2))
        if uid:
            return uid, ""
        code = m.group(2)      # the name may be stale; the code usually isn't
    uid = _find_uid_by_username(db, txt.lower()) or ""
    if uid:
        return uid, ""
    if code:
        return _uid_by_friend_code(db, code)
    return "", "no_user"


# ── Game claims ──────────────────────────────────────────────────────────────
def _latest_record(dir_path: str, room_id: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Newest game_<room>_<ts>.json record for a room → (record_id, record)."""
    rid = str(room_id or "").strip().upper()
    if not rid or not os.path.isdir(dir_path):
        return None
    best: Optional[Tuple[int, str]] = None
    try:
        for fname in os.listdir(dir_path):
            if not (fname.startswith(f"game_{rid}_") and fname.endswith(".json")):
                continue
            try:
                ts = int(fname[len(f"game_{rid}_"):-len(".json")])
            except ValueError:
                continue
            if best is None or ts > best[0]:
                best = (ts, fname)
    except OSError:
        return None
    if not best:
        return None
    try:
        with open(os.path.join(dir_path, best[1]), "r", encoding="utf-8") as f:
            return best[1][:-len(".json")], json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _names_match(a: str, b: str) -> bool:
    return str(a or "").strip().lower() == str(b or "").strip().lower()


def _lookup_player(db, name: str) -> Dict[str, Any]:
    """An in-game name → {"found", "uid", "clan_id"} for the account behind it.

    Players are shown by NICKNAME in a game, and plenty of accounts have a
    nickname that differs from their unique username: matching on username
    alone would quietly refuse Clan Points for perfectly normal games. So we
    accept either. A nickname isn't unique, so this is only ever used as
    "a real account played here" and "which clan were they in", never to decide
    WHO gets credited: the worst case is a guest who copied a real player's
    nickname. Cached for _REG_TTL_SEC, a whole 8-player lobby is otherwise
    re-queried on every single claim."""
    nm = str(name or "").strip()
    if not nm:
        return {"found": False, "uid": "", "clan_id": ""}
    key = nm.lower()
    hit = _REG_CACHE.get(key)
    if hit and time.time() - hit[1] < _REG_TTL_SEC:
        return dict(hit[0])
    out: Dict[str, Any] = {"found": False, "uid": "", "clan_id": ""}
    try:
        uid = _find_uid_by_username(db, key)
        if uid:
            out.update({"found": True, "uid": str(uid)})
        else:
            for field in ("nickname", "usernameLower", "username"):
                value = key if field == "usernameLower" else nm
                try:
                    from google.cloud.firestore_v1 import FieldFilter
                    q = _users(db).where(filter=FieldFilter(field, "==", value)).limit(1)
                except Exception:
                    q = _users(db).where(field, "==", value).limit(1)
                for snap in q.stream():
                    out.update({"found": True, "uid": str(getattr(snap, "id", "") or "")})
                    doc = snap.to_dict() or {}
                    out["clan_id"] = str(doc.get("clan_id") or "")
                    break
                if out["found"]:
                    break
        if out["found"] and out["uid"] and not out["clan_id"]:
            doc = (_member_docs(db, [out["uid"]]) or {}).get(out["uid"]) or {}
            out["clan_id"] = str(doc.get("clan_id") or "")
    except Exception as exc:  # noqa: BLE001
        print(f"[clan] player lookup failed: {exc}")
        return {"found": False, "uid": "", "clan_id": ""}
    _REG_CACHE[key] = (dict(out), time.time())
    return out


def _is_registered(db, name: str) -> bool:
    """Is this in-game name a real (non-guest) account?"""
    return bool(_lookup_player(db, name).get("found"))


def _cooldown_active(udoc: Dict[str, Any]) -> int:
    until = int(udoc.get("clan_cooldown_until") or 0)
    return until if until > _now() else 0


_TELEMETRY_KEYS = ("oceans_played", "animals_played", "moves", "stars",
                   "star_chains", "pool_draws", "deck_draws")


def _my_clan_stats(rec: Dict[str, Any], my_names: set,
                   seats: Optional[set] = None) -> Dict[str, Any]:
    """Sum the per-seat clan telemetry the game server stamped on the record.

    Casual: one seat, matched by name. Competitive: a player owns TWO seats
    ({0,1} or {2,3}), and both of their hands are theirs, so the telemetry is
    summed over the pair, matched by SEAT not by name (a rename mid-match must
    never move someone's cards onto their opponent's ledger).

    A record written before this telemetry existed simply has no clan_stats,
    and every counter reads 0, old games stop short of the new challenges
    instead of blocking the claim."""
    out: Dict[str, Any] = {k: 0 for k in _TELEMETRY_KEYS}
    out.update({"artificial_first": False, "humu": False, "moon": False,
                "said_gg": False, "behind_at_endgame": False})
    gobies = 0
    for p in (rec.get("players") or []):
        cs = p.get("clan_stats") or {}
        if seats is not None:
            try:
                if int(p.get("seat_index", cs.get("seat_index", -1))) not in seats:
                    continue
            except (TypeError, ValueError):
                continue
        elif str(p.get("name") or "").strip().lower() not in my_names:
            continue
        for k in _TELEMETRY_KEYS:
            out[k] = _num(out[k]) + _num(cs.get(k))
        if str(cs.get("first_ocean") or "").strip().lower() == "artificial reef":
            out["artificial_first"] = True
        if int(cs.get("max_ceph_turn") or 0) >= 5:
            out["humu"] = True          # Humuhumunukuapua'a: 5 Cephalopods in a turn
        gobies += int(cs.get("gobies") or 0)
        if cs.get("said_gg"):
            out["said_gg"] = True
        if cs.get("behind_at_endgame"):
            out["behind_at_endgame"] = True
    out["moon"] = gobies >= 4           # Shoot the Moon: all 4 Mandarin Gobies
    return out


def _clan_names_in(clan: Dict[str, Any]) -> set:
    """Lowercased display names of a clan's current roster, the cheap way to
    ask "how many of us were in that game" without a lookup per player."""
    out = set()
    for mem in (clan.get("members") or {}).values():
        nm = str((mem or {}).get("name") or "").strip().lower()
        if nm:
            out.add(nm)
    return out


def claim_game_points(uid: str, room_id: str) -> Dict[str, Any]:
    """Award Clan Points for a finished game THIS server recorded. Called by
    the client at the end screen; every rule (placement points, real-player
    minimums, same-opponent daily caps, weekly cap, join cooldown, one claim
    per game) is enforced here against the server's own record."""
    db = _get_firestore()
    if db is None:
        return {"ok": False, "error": "firestore_unavailable"}
    ensure_season_finalized(db)
    usnap = _users(db).document(uid).get()
    udoc = usnap.to_dict() or {} if usnap.exists else {}
    clan_id = str(udoc.get("clan_id") or "")
    if not clan_id:
        return {"ok": False, "error": "no_clan"}
    cd = _cooldown_active(udoc)
    if cd:
        return {"ok": False, "error": "cooldown", "until": cd}
    my_names = {str(udoc.get("nickname") or "").strip().lower(),
                str(udoc.get("username") or "").strip().lower(),
                str(udoc.get("usernameLower") or "").strip().lower()}
    my_names.discard("")
    if not my_names:
        return {"ok": False, "error": "no_username"}

    hist = _latest_record(_GAMES_HISTORY_DIR, room_id)
    if hist is None:
        return {"ok": False, "error": "no_record"}
    rec_id, rec = hist
    mode = str(rec.get("mode") or "")
    if mode == "truncated":
        return {"ok": False, "error": "not_finished"}   # must finish the game

    display_name = udoc.get("nickname") or udoc.get("username") or "Player"
    dedup = f"g_{rec_id}_{uid}"
    # Fast pre-check so a duplicate claim can't tick the same-opponent daily
    # counters; the transaction's ledger create() is the real guarantee.
    try:
        if _clans(db).document(clan_id).collection("ledger").document(dedup).get().exists:
            return {"ok": False, "error": "already_claimed"}
    except Exception:
        pass

    # The clan doc is needed for the "who else in the game is one of ours"
    # questions. A failed read is not fatal, those counters just stay 0.
    clan_doc: Dict[str, Any] = {}
    try:
        csnap = _clans(db).document(clan_id).get()
        clan_doc = csnap.to_dict() or {} if csnap.exists else {}
    except Exception as exc:  # noqa: BLE001
        print(f"[clan] clan read for claim failed: {exc}")
    my_clan_names = _clan_names_in(clan_doc)
    # Achievements this player already holds, read from the profile document
    # this claim opened anyway (no extra round-trip).
    achievements = udoc.get("achievements") or {}
    has_invert = bool((achievements.get("saving_the_invertebrates") or {}).get("completed"))

    if mode == "competitive":
        comp = _latest_record(_COMPETITIVE_GAMES_DIR, room_id)
        if comp is None:
            return {"ok": False, "error": "no_record"}
        _, crec = comp
        p1, p2 = str(crec.get("p1_name") or ""), str(crec.get("p2_name") or "")
        if p1.strip().lower() in my_names:
            me, opp, my_seats = p1, p2, {0, 1}
            my_best = int(crec.get("p1_best_score") or 0)
            my_second = int(crec.get("p1_second_score") or 0)
            opp_best = int(crec.get("p2_best_score") or 0)
        elif p2.strip().lower() in my_names:
            me, opp, my_seats = p2, p1, {2, 3}
            my_best = int(crec.get("p2_best_score") or 0)
            my_second = int(crec.get("p2_second_score") or 0)
            opp_best = int(crec.get("p1_best_score") or 0)
        else:
            return {"ok": False, "error": "not_in_game"}
        # Both players must be real, registered (non-guest) accounts. Unlike
        # casual, where bots fill a table real people are sitting at, so the
        # game still counts for challenge progress and only the points are
        # withheld, a 1v1 against a bot or a guest is not a competitive match
        # for the clan at all. Every competitive challenge is about beating
        # real people, and counting these would make all of them farmable.
        if not _is_registered(db, opp):
            return {"ok": False, "error": "opponent_not_registered"}
        won = bool(crec.get("winner")) and _names_match(crec.get("winner"), me)
        # First-3-vs-same-opponent-per-day cap (only the WIN needs it, losses
        # score 0 anyway but still tick the counter so W-L-W-W can't dodge it).
        allowed, over = _bump_opponent_counter(db, clan_id, uid, display_name,
                                               key="comp:" + opp.strip().lower(),
                                               cap=COMP_SAME_OPP_DAILY_CAP)
        pts = POINTS_COMP_WIN if (won and allowed) else 0
        facts = _my_clan_stats(rec, my_names, seats=my_seats)
        facts.update({
            "competitive": True,
            "won": won,
            "player_count": 2,
            # Both hands beat their best hand / doubled it. p*_second is the
            # LOWER of the two hands, so testing it covers both of them.
            "dominant_win": bool(won and my_best > opp_best and my_second > opp_best),
            "double_hands": bool(opp_best > 0 and my_second >= 2 * opp_best
                                 and my_best >= 2 * opp_best),
            # One opponent, so "with a clan member" is simply: are they one of ours.
            "clanmates": 1 + (1 if opp.strip().lower() in my_clan_names else 0),
            "team_mode": False,
            "has_invertebrates": has_invert,
        })
        res = _apply_award(
            db, clan_id, uid, display_name, kind="comp",
            points=pts, dedup_id=dedup,
            activity_text=(f"⚔️ {display_name} won a competitive match (+{{pts}} pts)" if won
                           else f"⚔️ {display_name} finished a competitive match"),
            counts_game=True, is_comp_win=won, is_comp_loss=(not won and not crec.get("is_draw")),
            meta={"room": room_id, "opp": opp, "won": won,
                  "opp_capped": bool(won and not allowed)},
            game=facts)
        if res.get("ok"):
            res.update({"points": res.get("granted", 0), "mode": "competitive", "won": won,
                        "opp_capped": bool(won and not allowed)})
            _sweep_member_derived(db, clan_id, uid, udoc)
        return res

    # Casual (standard / team): placement points from the server standings.
    standings = rec.get("standings") or []
    players = rec.get("players") or []
    me_row = None
    for row in standings:
        if str(row.get("name") or "").strip().lower() in my_names:
            me_row = row
            break
    if me_row is None:
        return {"ok": False, "error": "not_in_game"}
    humans = [p for p in players if p.get("is_human")]
    other_names = [str(p.get("name") or "") for p in humans
                   if str(p.get("name") or "").strip().lower() not in my_names]
    # A game with no registered opponent is a game against bots and/or guests.
    # It cannot pay placement points, that is the anti-farm rule, but first
    # place in one is still worth a HALF point at any player count.
    real_opponents = [n for n in other_names if _is_registered(db, n)]
    has_real_opponent = bool(real_opponents)
    my_score = int(me_row.get("score") or 0)
    place = 1 + sum(1 for row in standings if int(row.get("score") or 0) > my_score)
    n_players = int(rec.get("player_count") or len(standings))
    if has_real_opponent:
        pts: Any = 0
        if place == 1:
            pts = POINTS_CASUAL_FIRST
        elif place == 2 and n_players > 2:
            pts = POINTS_CASUAL_SECOND
        elif place == 3 and n_players >= 4:
            pts = POINTS_CASUAL_THIRD
    else:
        pts = POINTS_CASUAL_FIRST_BOTS if place == 1 else 0
    # Repeat-lobby limiter: same exact opponent set only scores a few times/day.
    opp_key = "cas:" + "|".join(sorted(n.strip().lower() for n in other_names))
    allowed, over = _bump_opponent_counter(db, clan_id, uid, display_name,
                                           key=opp_key, cap=CASUAL_SAME_OPPS_DAILY_CAP)
    if not allowed:
        pts = 0
    ordinal = {1: "1st", 2: "2nd", 3: "3rd"}.get(place, f"{place}th")
    facts = _my_clan_stats(rec, my_names)
    # "All real people" means every seat was a human AND every one of them a
    # registered account, the same bar the Full Boat achievement uses.
    all_real = (len(humans) == n_players and n_players > 0
                and all(_is_registered(db, n) for n in other_names))
    clanmates = 1 + sum(1 for n in other_names if n.strip().lower() in my_clan_names)
    facts.update({
        "competitive": False,
        "place": place,
        "player_count": n_players,
        "all_real_people": all_real,
        "clanmates": clanmates,
        "team_mode": bool(rec.get("team_mode")),
        "has_invertebrates": has_invert,
        # A comeback is a win you were NOT leading at the moment the End Game
        # card was revealed.
        "comeback": bool(place == 1 and facts.get("behind_at_endgame")),
        # "Against another clan": at least one registered opponent who is in a
        # clan, and not this one.
        "vs_other_clan": _played_another_clan(db, clan_id, real_opponents),
    })
    if facts["team_mode"]:
        facts["team_rival"] = _team_rival_match(db, clan_doc, clanmates, other_names)
    res = _apply_award(
        db, clan_id, uid, display_name, kind="casual",
        points=pts, dedup_id=dedup,
        activity_text=f"🌊 {display_name} finished {ordinal} in a {n_players}-player game (+{{pts}} pts)",
        counts_game=True, is_casual_win=(place == 1),
        meta={"room": room_id, "place": place, "players": n_players,
              "bots_only": not has_real_opponent, "opp_capped": not allowed},
        game=facts)
    if res.get("ok"):
        res.update({"points": res.get("granted", 0), "mode": "casual",
                    "place": place, "opp_capped": not allowed,
                    "bots_only": not has_real_opponent})
        _sweep_member_derived(db, clan_id, uid, udoc)
    return res


def _bump_season(db, clan_id: str, *, add: Optional[Dict[str, Any]] = None,
                 set_to: Optional[Dict[str, Any]] = None,
                 activity: str = "", sid: str = "") -> Dict[str, Any]:
    """Move season counters that are NOT driven by a game record: declaring a
    rival, scheduling an event, a new member joining, the end-of-season rank
    bonus, and re-sweep the season challenges in the same transaction so the
    reward lands with the counter.

    `sid` defaults to the live season; the finalize pass passes the season it
    is closing. Returns {"ok", "challenges_done"}; never raises (the caller's
    own action has already succeeded and must not be undone by bookkeeping)."""
    transactional = _txn_helpers()
    clan_ref = _clans(db).document(clan_id)
    sid = sid or _clan_sid()
    out: Dict[str, Any] = {"ok": False, "challenges_done": []}

    @transactional
    def _run(t) -> Dict[str, Any]:
        snap = clan_ref.get(transaction=t)
        if not snap.exists:
            return {"ok": False, "challenges_done": []}
        clan = snap.to_dict() or {}
        slot = _season_slot(clan, sid)
        for key, delta in (add or {}).items():
            slot[key] = _num(_num(slot.get(key)) + _num(delta))
        for key, value in (set_to or {}).items():
            slot[key] = value
        if activity:
            _activity_push(clan, "challenge", activity)
        weekly = _weekly_slot(clan)
        done: List[Dict[str, Any]] = []
        _sweep_challenges(clan, slot, weekly, CLAN_SEASON_CHALLENGES,
                          slot.setdefault("challenges_done", []),
                          lambda ch: _season_metric(slot, str(ch.get("metric") or "")),
                          "Season", done)
        t.set(clan_ref, clan)
        return {"ok": True, "challenges_done": done}

    try:
        out = _run(db.transaction())
    except Exception as exc:  # noqa: BLE001
        print(f"[clan] season counter bump failed ({clan_id}): {exc}")
        return {"ok": False, "challenges_done": []}
    if out.get("ok"):
        _lb_invalidate()
        for ch in out.get("challenges_done") or []:
            _chat_system(db, clan_id, f"🏁 Season challenge complete: {ch.get('name')}!")
            _grant_challenge_xp(db, clan_id, ch)
    return out


def _played_another_clan(db, my_clan_id: str, opponent_names: List[str]) -> bool:
    """Was there a player from a DIFFERENT clan in this game?"""
    for nm in opponent_names:
        info = _lookup_player(db, nm)
        other = str(info.get("clan_id") or "")
        if other and other != my_clan_id:
            return True
    return False


def _team_rival_match(db, clan: Dict[str, Any], my_count: int,
                      other_names: List[str]) -> bool:
    """Cross-Current: a Team Mode game with 3+ of us and 3+ of our rival.

    The rival is whoever THIS clan declared for the season. If they declared us
    back, their own members' claims score it for them the same way, which is
    what "you both get 15 points" means."""
    if my_count < 3:
        return False
    rival_id = str((clan.get("rivals") or {}).get(_clan_sid()) or "")
    if not rival_id:
        return False
    try:
        rsnap = _clans(db).document(rival_id).get()
        if not rsnap.exists:
            return False
        rival_names = _clan_names_in(rsnap.to_dict() or {})
    except Exception as exc:  # noqa: BLE001
        print(f"[clan] rival roster read failed: {exc}")
        return False
    return sum(1 for n in other_names if n.strip().lower() in rival_names) >= 3


def _sweep_member_derived(db, clan_id: str, uid: str, udoc: Dict[str, Any]) -> None:
    """Counters that live on the PLAYER's profile, not in a game record.

    Two of them:
      • Rank Climbers, every time a member's competitive DIVISION goes up.
        The rank the client writes after a match may land either side of this
        claim, so a climb is credited on the first claim that observes it; the
        count is exact, it can just be one game late.
      • Saving the Invertebrates, a hidden achievement earned outside a game,
        so it is picked up whenever its owner next plays.
    Never raises: a failure here must not undo an award that already committed.
    """
    stats = (udoc.get("stats") or {})
    rank_idx = _rank_tier_index(stats.get("rank_competitive"))
    achievements = udoc.get("achievements") or {}
    has_invert = bool((achievements.get("saving_the_invertebrates") or {}).get("completed"))
    if rank_idx <= 0 and not has_invert:
        return
    transactional = _txn_helpers()
    clan_ref = _clans(db).document(clan_id)
    sid = _clan_sid()

    @transactional
    def _run(t) -> None:
        snap = clan_ref.get(transaction=t)
        if not snap.exists:
            return
        clan = snap.to_dict() or {}
        if uid not in (clan.get("members") or {}):
            return
        slot = _season_slot(clan, sid)
        changed = False
        if rank_idx > 0:
            seen = slot.setdefault("member_ranks", {})
            prev = int(seen.get(uid) or 0)
            if prev and rank_idx > prev:
                # One point of progress per DIVISION climbed, so a jump of two
                # divisions counts twice, and a demotion never counts at all.
                slot["rank_ups"] = _num(slot.get("rank_ups")) + (rank_idx % 10 - prev % 10
                                                                 if rank_idx // 10 == prev // 10
                                                                 else 1)
                changed = True
            if prev != rank_idx:
                seen[uid] = rank_idx
                changed = True
        if has_invert and uid not in (slot.get("invert_members") or {}):
            slot.setdefault("invert_members", {})[uid] = 1
            changed = True
        if not changed:
            return
        weekly = _weekly_slot(clan)
        done: List[Dict[str, Any]] = []
        _sweep_challenges(clan, slot, weekly, CLAN_SEASON_CHALLENGES,
                          slot.setdefault("challenges_done", []),
                          lambda ch: _season_metric(slot, str(ch.get("metric") or "")),
                          "Season", done)
        t.set(clan_ref, clan)

    try:
        _run(db.transaction())
        _lb_invalidate()
    except Exception as exc:  # noqa: BLE001
        print(f"[clan] member sweep failed: {exc}")


def _bump_opponent_counter(db, clan_id: str, uid: str, name: str, *, key: str,
                           cap: int) -> Tuple[bool, int]:
    """Per-player per-day counter for 'same opponent(s)' limits, kept on the
    player's contrib slot. Returns (this_game_still_scores, count_after)."""
    transactional = _txn_helpers()
    clan_ref = _clans(db).document(clan_id)
    txn = db.transaction()
    sid = _clan_sid()
    today = _date_key()
    khash = "o" + hashlib.md5(key.encode()).hexdigest()[:10]

    @transactional
    def _run(t) -> Tuple[bool, int]:
        snap = clan_ref.get(transaction=t)
        if not snap.exists:
            return True, 0
        clan = snap.to_dict() or {}
        slot = _season_slot(clan, sid)
        c = _contrib_slot(slot, uid, name)
        if c.get("opp_date") != today:
            c["opp_date"] = today
            c["opp_counts"] = {}
        n = int((c.get("opp_counts") or {}).get(khash) or 0) + 1
        c["opp_counts"][khash] = n
        t.set(clan_ref, clan)
        return n <= cap, n

    try:
        ok, n = _run(txn)
        if not ok and n == cap + 1:
            _flag(db, "repeat_opponents", uid=uid, clan_id=clan_id,
                  detail=f"{key} exceeded {cap}/day")
        return ok, n
    except Exception as exc:  # noqa: BLE001
        print(f"[clan] opponent counter failed: {exc}")
        return True, 0


def _flag(db, kind: str, **fields) -> None:
    """Suspicious-activity marker for admin review (never raises)."""
    try:
        db.collection("clan_flags").document(f"f{_now()}_{secrets.token_hex(3)}").set(
            {"kind": kind, "ts": _now(), **fields})
    except Exception as exc:  # noqa: BLE001
        print(f"[clan] flag write failed: {exc}")


# ── Trade hook (called from _trade_confirm on completion) ────────────────────
def _trade_signature(trade: Dict[str, Any]) -> str:
    """Order-independent fingerprint of WHAT changed hands: used to refuse
    points for the same items bouncing back and forth between two players."""
    offers = trade.get("offers") or {}
    sides = []
    for uid in sorted(trade.get("participants") or []):
        o = offers.get(uid) or {}
        sides.append(json.dumps({
            "c": int(o.get("coins") or 0),
            "a": sorted(o.get("avatars") or []),
            "b": sorted(o.get("backgrounds") or []),
        }, sort_keys=True))
    blob = "||".join(sorted(sides))    # sorted → A→B same as B→A
    return hashlib.md5(blob.encode()).hexdigest()[:16]


def on_trade_completed(db, trade: Dict[str, Any]) -> Dict[str, Any]:
    """Daily clan trading point. Both players get +1 when: same clan, the trade
    actually moved something, neither point was already earned today, no
    same-items bounce inside 7 days. Never raises: trading itself must never
    break because of clan bookkeeping."""
    out: Dict[str, Any] = {"awarded": {}}
    try:
        parts = [str(p) for p in (trade.get("participants") or []) if p]
        if len(parts) != 2:
            return out
        offers = trade.get("offers") or {}
        moved = any(
            int((offers.get(p) or {}).get("coins") or 0) > 0
            or (offers.get(p) or {}).get("avatars")
            or (offers.get(p) or {}).get("backgrounds")
            for p in parts)
        if not moved:
            return out                      # nothing actually transferred
        udocs: Dict[str, Dict[str, Any]] = {}
        for p in parts:
            snap = _users(db).document(p).get()
            udocs[p] = snap.to_dict() or {} if snap.exists else {}
        clan_a = str(udocs[parts[0]].get("clan_id") or "")
        clan_b = str(udocs[parts[1]].get("clan_id") or "")
        if not clan_a or clan_a != clan_b:
            return out                      # must be the SAME clan
        ensure_season_finalized(db)
        today = _date_key()
        pair_key = "_".join(sorted(parts))
        sig = _trade_signature(trade)
        # Bounce check + pair-volume flag, tracked on a tiny pair doc.
        pair_ref = db.collection("clan_meta").document(f"tradepair_{pair_key}")
        psnap = pair_ref.get()
        pdoc = psnap.to_dict() or {} if psnap.exists else {}
        sigs = {k: int(v) for k, v in (pdoc.get("sigs") or {}).items()}
        bounced = int(sigs.get(sig) or 0) >= _now() - TRADE_BOUNCE_WINDOW_SEC and sig in sigs
        wk = _week_key()
        wk_n = (int(pdoc.get("week_n") or 0) + 1) if pdoc.get("week") == wk else 1
        sigs[sig] = _now()
        cutoff = _now() - TRADE_BOUNCE_WINDOW_SEC
        sigs = {k: v for k, v in sorted(sigs.items(), key=lambda kv: -kv[1])[:20] if v >= cutoff or k == sig}
        pair_ref.set({"sigs": sigs, "week": wk, "week_n": wk_n, "ts": _now(),
                      "clan": clan_a}, merge=True)
        if wk_n == TRADE_PAIR_WEEK_FLAG + 1:
            _flag(db, "trade_pair_volume", clan_id=clan_a, pair=pair_key,
                  detail=f"{wk_n} completed trades this week")
        if bounced:
            _flag(db, "trade_bounce", clan_id=clan_a, pair=pair_key, sig=sig,
                  detail="same items traded back within 7 days")
            out["bounced"] = True
            return out
        # Both sides are paid, but the TRADE is counted once. Whichever side's
        # award commits first carries the count; if the first side had already
        # taken their daily point, the second side carries it instead, so a
        # real trade is never lost from the clan's tally.
        counted = False
        for p in parts:
            cd = _cooldown_active(udocs[p])
            if cd:
                continue                    # inside the 24h clan-switch cooldown
            nm = udocs[p].get("nickname") or udocs[p].get("username") or "Player"
            res = _apply_award(
                db, clan_a, p, nm, kind="trade",
                points=POINTS_TRADE_DAILY,
                dedup_id=f"t_{today}_{p}",   # ONE trade point per player per day
                activity_text=f"🤝 {nm} completed a clan trade (+{{pts}} pt)",
                is_trade=True, count_trade=not counted,
                meta={"pair": pair_key})
            if res.get("ok"):
                counted = True
            if res.get("ok") and _num(res.get("granted")) > 0:
                out["awarded"][p] = res.get("granted")
    except Exception as exc:  # noqa: BLE001
        print(f"[clan] trade hook failed: {exc}")
    return out


# ── Clan lifecycle ops ───────────────────────────────────────────────────────
def _create_clan(uid: str, body: Dict[str, Any]) -> Dict[str, Any]:
    db = _get_firestore()
    if db is None:
        return {"ok": False, "error": "firestore_unavailable"}
    ensure_season_finalized(db)
    name = str(body.get("name") or "").strip()
    ok, why = clan_name_check(name)
    if not ok:
        return {"ok": False, "error": "bad_name", "reason": why}
    icon = _norm_icon(body.get("icon"))
    if not icon:
        return {"ok": False, "error": "bad_icon"}
    icon_name = str(body.get("icon_name") or "").strip()[:40]
    desc = str(body.get("description") or "").strip()[:CLAN_DESC_MAX]
    if desc and text_is_profane(desc):
        return {"ok": False, "error": "bad_description"}
    privacy = str(body.get("privacy") or "public")
    if privacy not in PRIVACY_MODES:
        return {"ok": False, "error": "bad_privacy"}
    # A password clan with no password would be a clan nobody could ever join.
    pw_record: Optional[Dict[str, Any]] = None
    if privacy == "password":
        pw, why = _clean_password(body.get("password"))
        if why:
            return {"ok": False, "error": why}
        pw_record = _hash_password(pw)

    transactional = _txn_helpers()
    name_lower = name.lower()
    name_ref = db.collection("clan_names").document(name_lower)
    user_ref = _users(db).document(uid)
    clan_id = _new_clan_id()
    clan_ref = _clans(db).document(clan_id)
    txn = db.transaction()

    @transactional
    def _run(t) -> Dict[str, Any]:
        nsnap = name_ref.get(transaction=t)
        if nsnap.exists:
            return {"ok": False, "error": "name_taken"}
        usnap = user_ref.get(transaction=t)
        udoc = usnap.to_dict() or {} if usnap.exists else {}
        if udoc.get("clan_id"):
            return {"ok": False, "error": "already_in_clan"}
        # The founder is the whole clan at this point, so the clan's critter has
        # to be one THEY have unlocked.
        if icon not in _user_icons(udoc):
            return {"ok": False, "error": "icon_not_unlocked"}
        nick = udoc.get("nickname") or udoc.get("username") or "Player"
        clan = {
            "name": name, "nameLower": name_lower,
            "icon": icon, "icon_name": icon_name,
            "description": desc, "privacy": privacy,
            "owner_uid": uid, "created_ts": _now(),
            "members": {uid: {"name": nick, "role": "owner",
                              "custom_role_id": None, "joined_ts": _now(),
                              "avatar": str(udoc.get("avatar_url") or "")}},
            "custom_roles": [], "captains_can_edit_roles": False,
            "pinned_announcement": None,
            "join_requests": [], "muted": {}, "events": [],
            "xp": 0, "lifetime": {"points": 0}, "seasons": {}, "activity": [],
        }
        if pw_record:
            clan["join_password"] = pw_record
        _activity_push(clan, "create", f"🛡️ {nick} founded the clan")
        t.set(clan_ref, clan)
        t.set(name_ref, {"clan_id": clan_id, "ts": _now()})
        t.set(user_ref, {"clan_id": clan_id, "clan_joined_ts": _now()}, merge=True)
        return {"ok": True, "clan_id": clan_id}

    try:
        res = _run(txn)
    except Exception as exc:  # noqa: BLE001
        print(f"[clan] create failed: {exc}")
        return {"ok": False, "error": "create_failed"}
    if res.get("ok"):
        _chat_system(db, clan_id, f"🛡️ Welcome to {name}! This is your private clan chat.")
    return res


def _join_clan(uid: str, body: Dict[str, Any]) -> Dict[str, Any]:
    db = _get_firestore()
    if db is None:
        return {"ok": False, "error": "firestore_unavailable"}
    ensure_season_finalized(db)
    clan_id = str(body.get("clan_id") or "").strip()
    if not clan_id:
        return {"ok": False, "error": "bad_clan"}
    transactional = _txn_helpers()
    clan_ref = _clans(db).document(clan_id)
    user_ref = _users(db).document(uid)
    txn = db.transaction()

    @transactional
    def _run(t) -> Dict[str, Any]:
        csnap = clan_ref.get(transaction=t)
        if not csnap.exists:
            return {"ok": False, "error": "no_clan"}
        clan = csnap.to_dict() or {}
        usnap = user_ref.get(transaction=t)
        udoc = usnap.to_dict() or {} if usnap.exists else {}
        if udoc.get("clan_id"):
            return {"ok": False, "error": "already_in_clan"}
        members = clan.get("members") or {}
        if uid in members:
            return {"ok": False, "error": "already_member"}
        if len(members) >= CLAN_MAX_MEMBERS:
            return {"ok": False, "error": "clan_full"}
        invites = [i for i in (udoc.get("clan_invites") or [])
                   if isinstance(i, dict) and int(i.get("ts") or 0) > _now() - CLAN_INVITE_TTL_SEC]
        has_invite = any(str(i.get("clan_id")) == clan_id for i in invites)
        privacy = clan.get("privacy")
        # A password clan lets in anyone who types the word, and nobody else,
        # the same instant join a public clan gives, behind one question. An
        # invite still bypasses it: being invited IS the owner letting you in.
        if privacy == "password" and not has_invite:
            if not _password_attempt_ok(uid, clan_id):
                return {"ok": False, "error": "too_many_tries"}
            if not _check_password(clan.get("join_password"), body.get("password")):
                _password_attempt_failed(uid, clan_id)
                return {"ok": False, "error": "bad_password"}
            _password_attempt_ok_reset(uid, clan_id)
        elif privacy != "public" and not has_invite:
            return {"ok": False, "error": "invite_required" if privacy == "invite" else "request_required"}
        nick = udoc.get("nickname") or udoc.get("username") or "Player"
        members[uid] = {"name": nick, "role": "member", "custom_role_id": None,
                        "joined_ts": _now(), "avatar": str(udoc.get("avatar_url") or "")}
        clan["members"] = members
        clan["join_requests"] = [r for r in (clan.get("join_requests") or [])
                                 if str(r.get("uid")) != uid]
        # Fresh Recruits: see _join_clan_direct.
        slot = _season_slot(clan, _clan_sid())
        slot["new_members"] = _num(slot.get("new_members")) + 1
        _activity_push(clan, "join", f"🌊 {nick} joined the clan")
        t.set(clan_ref, clan)
        t.set(user_ref, {"clan_id": clan_id, "clan_joined_ts": _now(),
                         "clan_invites": [i for i in invites if str(i.get("clan_id")) != clan_id]},
              merge=True)
        return {"ok": True, "clan_id": clan_id, "name": clan.get("name"), "nick": nick}

    try:
        res = _run(txn)
    except Exception as exc:  # noqa: BLE001
        print(f"[clan] join failed: {exc}")
        return {"ok": False, "error": "join_failed"}
    if res.get("ok"):
        _members_invalidate([uid])
        _chat_system(db, clan_id, f"🌊 {res.pop('nick', 'A new member')} joined the clan: say hi!")
        _bump_season(db, clan_id)      # re-sweep: the new arrival may finish one
    return res


def _leave_clan(uid: str, *, kicked_by: Optional[str] = None,
                clan_id_hint: str = "") -> Dict[str, Any]:
    """Voluntary leave OR kick (kicked_by set). Earned points stay with the
    clan (contrib is never deleted); the leaver gets the 24h cooldown."""
    db = _get_firestore()
    if db is None:
        return {"ok": False, "error": "firestore_unavailable"}
    transactional = _txn_helpers()
    user_ref = _users(db).document(uid)
    usnap = user_ref.get()
    udoc = usnap.to_dict() or {} if usnap.exists else {}
    clan_id = clan_id_hint or str(udoc.get("clan_id") or "")
    if not clan_id:
        return {"ok": False, "error": "no_clan"}
    clan_ref = _clans(db).document(clan_id)
    txn = db.transaction()

    @transactional
    def _run(t) -> Dict[str, Any]:
        csnap = clan_ref.get(transaction=t)
        if not csnap.exists:
            t.set(user_ref, {"clan_id": ""}, merge=True)
            return {"ok": True, "gone": True}
        clan = csnap.to_dict() or {}
        members = clan.get("members") or {}
        mem = members.get(uid)
        if not mem:
            t.set(user_ref, {"clan_id": ""}, merge=True)
            return {"ok": True, "gone": True}
        if mem.get("role") == "owner" and len(members) > 1:
            return {"ok": False, "error": "transfer_first"}
        nick = mem.get("name") or "A member"
        del members[uid]
        clan["members"] = members
        verb = "was removed from" if kicked_by else "left"
        _activity_push(clan, "leave", f"👋 {nick} {verb} the clan")
        if not members:
            # Last member out → the clan dissolves; free the name.
            t.delete(clan_ref)
            t.delete(db.collection("clan_names").document(str(clan.get("nameLower") or "")))
        else:
            t.set(clan_ref, clan)
        t.set(user_ref, {"clan_id": "",
                         "clan_cooldown_until": _now() + JOIN_COOLDOWN_SEC}, merge=True)
        return {"ok": True, "nick": nick, "dissolved": not members}

    try:
        res = _run(txn)
    except Exception as exc:  # noqa: BLE001
        print(f"[clan] leave failed: {exc}")
        return {"ok": False, "error": "leave_failed"}
    if res.get("ok"):
        _members_invalidate([uid])     # they are clanless again from this moment
    if res.get("ok") and not res.get("gone") and not res.get("dissolved"):
        _chat_system(db, clan_id,
                     f"👋 {res.pop('nick', 'A member')} "
                     + ("was removed from the clan." if kicked_by else "left the clan."))
    return res


# ── One-shot roster moves ────────────────────────────────────────────────────
# Players who were placed straight into a clan by hand, with no invite round
# trip. Each entry runs at most ONCE ever: the marker doc written to
# clan_moves/{key} is what stops a restart (or the second Render instance) from
# repeating it, so the key must never be reused. Safe to delete an entry after
# its marker exists, it just stops being checked.
PENDING_MEMBER_MOVES: Tuple[Dict[str, str], ...] = (
    {"key": "2026-08-01-belmont-lemmeseethemtoes",
     "name": "LemmeSeeThemToes", "code": "2809", "clan": "Belmont Board Game Club"},
    {"key": "2026-08-01-belmont-twinmidi",
     "name": "Twin Midi", "code": "9113", "clan": "Belmont Board Game Club"},
)
MOVE_RETRY_SEC = 900          # re-check unresolved moves every 15 min
_MOVES_NEXT_CHECK = 0.0


def _clan_id_by_name(db, name: str) -> str:
    """Clan name → id via the clan_names/{nameLower} uniqueness reservation."""
    nl = str(name or "").strip().lower()
    if not nl:
        return ""
    try:
        snap = db.collection("clan_names").document(nl).get()
        if snap.exists:
            return str((snap.to_dict() or {}).get("clan_id") or "")
    except Exception as exc:  # noqa: BLE001
        print(f"[clan] clan-name lookup failed: {exc}")
    return ""


def _force_move_member(db, mv: Dict[str, str]) -> str:
    """Put one player into one clan, no invite needed.

    Returns "moved"/"already_member" when it's done, or "" for "not yet",
    and "not yet" is deliberately what EVERY failure returns, including ones
    that look permanent. The clan may not be founded yet, the player may not
    have signed up yet, the clan may be full this minute: none of those mean
    the move should be quietly abandoned, so no marker is written and the next
    pass tries again. The reason is logged each time."""
    who = f"{mv.get('name', '')}#{mv.get('code', '')}"
    uid, _why = _resolve_invitee(db, who)
    if not uid:
        print(f"[clan] move {mv.get('key')}: no player matches {who}")
        return ""
    clan_id = _clan_id_by_name(db, str(mv.get("clan") or ""))
    csnap = _clans(db).document(clan_id).get() if clan_id else None
    if not clan_id or csnap is None or not csnap.exists:
        print(f"[clan] move {mv.get('key')}: no clan named {mv.get('clan')!r} yet")
        return ""
    clan = csnap.to_dict() or {}
    usnap = _users(db).document(uid).get()
    udoc = usnap.to_dict() or {} if usnap.exists else {}
    if str(udoc.get("clan_id") or "") == clan_id or uid in (clan.get("members") or {}):
        return "already_member"
    if len(clan.get("members") or {}) >= CLAN_MAX_MEMBERS:
        print(f"[clan] move {mv.get('key')}: {mv.get('clan')!r} is full right now")
        return ""
    if udoc.get("clan_id"):
        old_id = str(udoc["clan_id"])
        old = (_clans(db).document(old_id).get().to_dict() or {})
        if (old.get("members") or {}).get(uid, {}).get("role") == "owner":
            # Never move a clan's owner out from under it. With clanmates that
            # needs a transfer; alone it would DISSOLVE their clan. Either way
            # that's a person's decision, not a roster edit's.
            print(f"[clan] move {mv.get('key')}: player owns {old.get('name')!r}: "
                  f"transfer or leave it first")
            return ""
        res = _leave_clan(uid, clan_id_hint=old_id)
        if not res.get("ok"):
            print(f"[clan] move {mv.get('key')}: can't leave current clan "
                  f"({res.get('error')})")
            return ""
        udoc = (_users(db).document(uid).get().to_dict() or {})
    # _join_clan is the one code path that builds a correct member record, so
    # reuse it, an invite is appended (not replaced) purely to satisfy its
    # privacy gate for a request-only or invite-only clan.
    invites = [i for i in (udoc.get("clan_invites") or []) if isinstance(i, dict)]
    if not any(str(i.get("clan_id")) == clan_id for i in invites):
        invites.append({"clan_id": clan_id, "name": clan.get("name"),
                        "icon": clan.get("icon"), "by": "Currents and Critters",
                        "ts": _now()})
        _users(db).document(uid).set({"clan_invites": invites}, merge=True)
    res = _join_clan(uid, {"clan_id": clan_id})
    if not res.get("ok"):
        print(f"[clan] move {mv.get('key')}: join refused ({res.get('error')})")
        return ""
    # Leaving stamps the 24h Clan-Point cooldown. That's a penalty for quitting
    # a clan, and this player didn't quit anything: clear it.
    _users(db).document(uid).set({"clan_cooldown_until": 0}, merge=True)
    return "moved"


def ensure_pending_moves(db) -> None:
    """Apply PENDING_MEMBER_MOVES. Costs one marker read per unapplied entry,
    at most every MOVE_RETRY_SEC, and nothing at all once they're all done.
    Never raises: a bad entry must not take the Clans tab down with it."""
    global _MOVES_NEXT_CHECK
    if not PENDING_MEMBER_MOVES or time.time() < _MOVES_NEXT_CHECK:
        return
    _MOVES_NEXT_CHECK = time.time() + MOVE_RETRY_SEC
    pending = 0
    for mv in PENDING_MEMBER_MOVES:
        try:
            ref = db.collection("clan_moves").document(str(mv["key"]))
            if ref.get().exists:
                continue
            outcome = _force_move_member(db, mv)
            if not outcome:
                # Not applied (it logged why): leave the marker unwritten so
                # the next pass retries instead of losing the move for good.
                pending += 1
                continue
            ref.set({**mv, "ts": _now(), "result": outcome})
            print(f"[clan] roster move {mv['key']}: {outcome}")
        except Exception as exc:  # noqa: BLE001
            pending += 1
            print(f"[clan] roster move {mv.get('key')} failed: {exc}")
    if not pending:
        _MOVES_NEXT_CHECK = float("inf")   # all applied; never look again


# ── Route helpers ────────────────────────────────────────────────────────────
def _auth_uid(body: Dict[str, Any]) -> Optional[str]:
    tok = body.get("idToken") if isinstance(body.get("idToken"), str) else ""
    claims = _verify_token(tok) if tok else None
    return claims.get("uid") if claims and claims.get("uid") else None


# The previous season's finalized standings never change again once they are
# written, but /home wanted them on every single call, a Firestore read per
# tab open, per account, forever. Read once per season per process.
_PREV_META_CACHE: Dict[str, Dict[str, Any]] = {}


def _prev_season_meta(db, sid: str) -> Dict[str, Any]:
    prev = _prev_sid(sid)
    hit = _PREV_META_CACHE.get(prev)
    if hit is not None:
        return hit
    meta: Dict[str, Any] = {}
    try:
        snap = db.collection("clan_meta").document(f"season_{prev}").get()
        meta = snap.to_dict() or {} if snap.exists else {}
    except Exception as exc:  # noqa: BLE001
        print(f"[clan] previous-season meta read failed: {exc}")
        return {}            # not cached: a failed read must be retried
    # Only a FINALIZED season is immutable enough to keep; an unfinished one is
    # still being written and has to be re-read.
    if meta.get("finalized"):
        _PREV_META_CACHE[prev] = meta
    return meta


def _with_clan(uid: str) -> Tuple[Optional[Any], Optional[str], Optional[Dict[str, Any]], Dict[str, Any]]:
    """(db, clan_id, clan, udoc) for the caller: clan fields None if clanless."""
    db = _get_firestore()
    if db is None:
        return None, None, None, {}
    usnap = _users(db).document(uid).get()
    udoc = usnap.to_dict() or {} if usnap.exists else {}
    clan_id = str(udoc.get("clan_id") or "")
    if not clan_id:
        return db, None, None, udoc
    csnap = _clans(db).document(clan_id).get()
    if not csnap.exists:
        return db, None, None, udoc
    return db, clan_id, csnap.to_dict() or {}, udoc


def _clan_profile(db, clan_id: str, clan: Dict[str, Any], sid: str,
                  viewer_uid: str = "") -> Dict[str, Any]:
    card = _clan_card(clan_id, clan, sid)
    slot = (clan.get("seasons") or {}).get(sid) or {}
    contrib = slot.get("contrib") or {}
    members_out = []
    mvp_chip = clan.get("mvp_chip") or {}
    mvp_uid = mvp_chip.get("uid") if int(mvp_chip.get("until") or 0) > _now() else None
    wk = _week_key()
    today = _date_key()
    # The clan's critter pool, built from the very same member reads the
    # presence dots need, every icon at least one member has unlocked. The
    # whole roster is fetched in ONE batched read; doing it a document at a
    # time is what made this page slow for a full clan.
    pool = set(STARTER_ICONS)
    member_docs = _member_docs(db, list((clan.get("members") or {}).keys()))
    for m_uid, mem in (clan.get("members") or {}).items():
        c = contrib.get(m_uid) or {}
        online, last_seen = False, 0
        try:
            u = member_docs.get(m_uid) or {}
            la = u.get("last_active")
            la_sec = int(la.timestamp()) if hasattr(la, "timestamp") else int(la or 0)
            online = bool(u.get("online")) and la_sec >= _now() - PRESENCE_FRESH_SEC
            last_seen = la_sec
            pool |= _user_icons(u)
        except Exception:
            pass
        members_out.append({
            "uid": m_uid, "name": mem.get("name"), "avatar": mem.get("avatar") or "",
            "role": mem.get("role"), "custom_role_id": mem.get("custom_role_id"),
            "joined_ts": int(mem.get("joined_ts") or 0),
            "online": online, "last_seen": last_seen,
            "points": _num(c.get("points")),
            "weekly_points": _num((c.get("weekly") or {}).get(wk)),
            "comp_wins": _num(c.get("comp_wins")),
            "casual_wins": _num(c.get("casual_wins")),
            "games": _num(c.get("games")),
            "challenges_done": _num(c.get("challenges_done")),
            "trade_point_today": c.get("last_trade_date") == today,
            "is_mvp_chip": m_uid == mvp_uid,
        })
    # Season-history contributors who already left still show in contrib.
    former = [{"uid": u, "name": (c or {}).get("name"), "points": _num((c or {}).get("points"))}
              for u, c in contrib.items() if u not in (clan.get("members") or {})]
    daily = _daily_slot(dict(clan), clan_id)   # copy → never mutates the stored doc here
    weekly = _weekly_slot(dict(clan))
    # Season favourite critter: most votes wins, and the winner is what the
    # clan's members see on their Clans tab button. Votes for a critter nobody
    # in the clan owns any more (its only owner left, or traded it away) drop
    # out of the tally, so the badge can never show a critter the clan can't
    # wear. Ties break alphabetically so every member sees the same winner.
    votes = slot.get("critter_votes") or {}
    fav, tally = _favorite_from_votes(votes, pool)
    # Friendly rival: the head-to-head stat card. Taken from the standings the
    # caller has already paid for rather than a fresh document read, a rival's
    # leaderboard row IS its clan card, plus the rank, and it costs nothing.
    rival = None
    rival_id = str((clan.get("rivals") or {}).get(sid) or "")
    if rival_id:
        try:
            rival = next((r for r in _leaderboard_rows(db, sid) if r["id"] == rival_id), None)
        except Exception as exc:  # noqa: BLE001
            print(f"[clan] rival lookup failed: {exc}")
    out = {
        **card,
        "owner_uid": clan.get("owner_uid"),
        "created_ts": clan.get("created_ts"),
        "captains_can_edit_roles": bool(clan.get("captains_can_edit_roles")),
        "custom_roles": clan.get("custom_roles") or [],
        "pinned_announcement": clan.get("pinned_announcement"),
        "members": sorted(members_out, key=lambda m: (-{"owner": 3, "captain": 2, "recruiter": 1}.get(m["role"], 0), -m["points"])),
        "former_contributors": former,
        "activity": (clan.get("activity") or [])[:60],
        "events": [e for e in (clan.get("events") or []) if int(e.get("ts") or 0) > _now() - 3600],
        "win_streak": _num(slot.get("win_streak")),
        "prev_results": clan.get("prev_results") or {},
        "mvp_chip": clan.get("mvp_chip"),
        "level_info": _clan_level(int(clan.get("xp") or 0)),
        "daily_goal": {"goal": daily.get("goal"), "progress": _daily_goal_progress(daily),
                       "done": bool(daily.get("done")), "date": daily.get("date")},
        "weekly": {"week": weekly.get("week"), "games": weekly.get("games"),
                   "points": weekly.get("points"), "trades": weekly.get("trades"),
                   "comp_wins": weekly.get("comp_wins"),
                   "challenges_done": weekly.get("challenges_done") or []},
        "challenges": _challenges_view(weekly, slot, clan),
        "season_challenges": _season_challenges_view(slot, clan, sid),
        "week_ends_ts": _week_end_ts(),
        "favorite_critter": fav,
        "favorite_votes": tally,
        "my_vote": votes.get(viewer_uid) if viewer_uid else None,
        "rival": rival,
        "rival_ahead": (None if not rival else
                        _num(slot.get("points")) > _num(rival.get("points"))),
        "season": _season_public(sid),
    }
    if viewer_uid and _member_of(clan, viewer_uid):
        # Which critters this clan may wear (icon + season vote). Members only:
        # it is the clan's own business what its people have unlocked.
        out["icon_pool"] = sorted(pool)
        out["my"] = {
            "role": (_member_of(clan, viewer_uid) or {}).get("role"),
            "custom_role_id": (_member_of(clan, viewer_uid) or {}).get("custom_role_id"),
            "perms": {p: _has_perm(clan, viewer_uid, p) for p in
                      CUSTOM_PERMS + ("edit_custom_roles",)},
            "is_owner": clan.get("owner_uid") == viewer_uid,
            "contribution": contrib.get(viewer_uid) or {},
        }
        out["join_requests"] = (clan.get("join_requests") or []) if _has_perm(clan, viewer_uid, "review_requests") else []
    return out


def _week_end_ts() -> int:
    """Unix time when the current ISO week rolls over (next Monday 00:00 UTC),
    the weekly challenge + weekly-cap reset moment."""
    now = datetime.now(tz=timezone.utc)
    midnight = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    return int(midnight.timestamp()) + (7 - now.isoweekday() + 1) * 86400


def _challenges_view(weekly: Dict[str, Any], slot: Dict[str, Any],
                     clan: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """This week's challenge board, with who has pulled their weight."""
    contribs = weekly.get("contributors") or {}
    members = (clan or {}).get("members") or {}
    who = [{"uid": u, "name": (members.get(u) or {}).get("name") or "Player",
            "points": _num(p)}
           for u, p in sorted(contribs.items(), key=lambda kv: -_num(kv[1]))]
    out = _challenge_rows(CLAN_WEEKLY_CHALLENGES, set(weekly.get("challenges_done") or []),
                          lambda ch: _challenge_metric(weekly, str(ch.get("metric") or "")),
                          clan, "weekly", _week_end_ts())
    for row in out:
        need = row["min_contribution"]
        row["contributors"] = [dict(w, qualifies=w["points"] >= need) for w in who[:20]]
    return out


def _season_challenges_view(slot: Dict[str, Any],
                            clan: Optional[Dict[str, Any]] = None,
                            sid: str = "") -> List[Dict[str, Any]]:
    """The season-long challenge board, same shape as the weekly one, so one
    renderer draws both (in the Clans tab AND inside a live game)."""
    ends = _season_bounds(sid or _clan_sid())[1]
    return _challenge_rows(CLAN_SEASON_CHALLENGES, set(slot.get("challenges_done") or []),
                           lambda ch: _season_metric(slot, str(ch.get("metric") or "")),
                           clan, "season", ends)


def _challenge_rows(table: List[Dict[str, Any]], done: set,
                    progress_of: Callable[[Dict[str, Any]], Any],
                    clan: Optional[Dict[str, Any]], scope: str,
                    ends_ts: int) -> List[Dict[str, Any]]:
    out = []
    for ch in table:
        target = _challenge_target(ch, clan)
        progress = _num(progress_of(ch))
        out.append({
            "id": ch.get("id"), "name": ch.get("name"), "desc": ch.get("desc"),
            "metric": ch.get("metric"), "target": target,
            # A finished challenge always reads as full, even if the counter it
            # rode on has since been reset (a new week) or its target grew (a
            # new member joined an "everybody" challenge).
            "progress": target if ch.get("id") in done else min(progress, target),
            "raw_progress": progress,
            "clan_points": _num(ch.get("clan_points")),
            "member_xp": int(ch.get("member_xp") or 0),
            "min_contribution": int(ch.get("min_contribution") or 1),
            "done": ch.get("id") in done,
            "scope": scope,
            "ends_ts": int(ends_ts or 0),
            "contributors": [],
        })
    return out


# ── HTTP surface ─────────────────────────────────────────────────────────────
def clan_rules() -> Dict[str, Any]:
    """The whole rulebook, generated FROM the constants that enforce it.

    The Clans tab and the in-game challenge sheet both render this, so the
    published rules can never drift away from what the server actually does:
    change POINTS_CASUAL_FIRST or a challenge target and the rules change with
    it. Nothing in here is per-clan, so it is a public, cacheable read."""
    return {
        "max_members": CLAN_MAX_MEMBERS,
        "season": {
            "length": "Clan seasons are quarterly, the same three-month season "
                      "the Competitive ladder uses.",
            "weekly_reset": "Weekly challenges and the weekly point cap reset "
                            "every Monday at 00:00 UTC.",
        },
        "scoring": [
            {"what": "Win a competitive match", "points": POINTS_COMP_WIN},
            {"what": "1st place in a casual game", "points": POINTS_CASUAL_FIRST},
            {"what": "2nd place in a casual game (3+ players)", "points": POINTS_CASUAL_SECOND},
            {"what": "3rd place in a casual game (4+ players)", "points": POINTS_CASUAL_THIRD},
            {"what": "1st place against bots only, any player count",
             "points": POINTS_CASUAL_FIRST_BOTS},
            {"what": "Complete a clan trade (once per day)", "points": POINTS_TRADE_DAILY},
        ],
        "core_rules": [
            "Clan Points need a REAL opponent: everyone involved must be a "
            "registered (non-guest) account. A game against bots or guests "
            f"scores 0: except first place, which is worth {POINTS_CASUAL_FIRST_BOTS} "
            "of a Clan Point at any player count.",
            "Only games this server recorded from start to finish can be "
            "claimed. Quitting early scores nothing.",
            "Each game pays a player once, ever. Re-opening the end screen "
            "cannot claim it twice.",
            f"A member can earn at most {WEEKLY_POINT_CAP} Clan Points per week. "
            "Wins, losses and games are still recorded in the clan's stats "
            "after the cap is reached.",
            f"Beating the SAME competitive opponent only scores the first "
            f"{COMP_SAME_OPP_DAILY_CAP} times a day; the same casual lobby only "
            f"scores the first {CASUAL_SAME_OPPS_DAILY_CAP} times a day.",
            "Challenge progress counts every finished CASUAL game, bots "
            "included, only the Clan POINTS there need a real opponent. "
            "Competitive is different: a match against a bot or a guest is not "
            "a competitive match for your clan at all, and counts for nothing.",
            "Leaving or being removed from a clan starts a 24-hour cooldown "
            "before you can join another. Points you earned stay with the clan.",
            f"A clan holds up to {CLAN_MAX_MEMBERS} members.",
            "The owner chooses how people get in: 🌊 Public (anyone joins "
            "instantly), 🔑 Password (anyone who knows the clan's password "
            f"joins instantly: {CLAN_PASSWORD_MIN}–{CLAN_PASSWORD_MAX} "
            "characters, changeable any time), ✉️ Request to Join (the owner "
            "or a recruiter approves each one) or 🔒 Invite Only. An invite "
            "always gets you in, whichever setting is on.",
        ],
        "weekly_challenges": [
            {"id": c.get("id"), "name": c.get("name"), "desc": c.get("desc"),
             "target": c.get("target"), "clan_points": _num(c.get("clan_points")),
             "member_xp": int(c.get("member_xp") or 0)}
            for c in CLAN_WEEKLY_CHALLENGES
        ],
        "season_challenges": [
            {"id": c.get("id"), "name": c.get("name"), "desc": c.get("desc"),
             "target": c.get("target"), "clan_points": _num(c.get("clan_points")),
             "member_xp": int(c.get("member_xp") or 0)}
            for c in CLAN_SEASON_CHALLENGES
        ],
        "rank_rewards": {
            "note": "When the season ends, every member's Competitive rank pays "
                    "out: Critter Coins to them, Clan Points to the squad. The "
                    "Clan Points land before the final standings are worked out, "
                    "so a highly ranked roster can still move the clan up the "
                    "leaderboard on the last day.",
            "tiers": [
                {"tier": "Bronze Barracuda", "coins": COMP_RANK_SEASON_REWARDS["bronze"]["coins"],
                 "clan_points": COMP_RANK_SEASON_REWARDS["bronze"]["clan_points"]},
                {"tier": "Silver Spiny Lobster", "coins": COMP_RANK_SEASON_REWARDS["silver"]["coins"],
                 "clan_points": COMP_RANK_SEASON_REWARDS["silver"]["clan_points"]},
                {"tier": "Golden Grouper", "coins": COMP_RANK_SEASON_REWARDS["gold"]["coins"],
                 "clan_points": COMP_RANK_SEASON_REWARDS["gold"]["clan_points"]},
                {"tier": "Diamond Dolphin", "coins": COMP_RANK_SEASON_REWARDS["diamond"]["coins"],
                 "clan_points": COMP_RANK_SEASON_REWARDS["diamond"]["clan_points"]},
                {"tier": "Emerald Emperor Penguin", "coins": COMP_RANK_SEASON_REWARDS["emerald"]["coins"],
                 "clan_points": COMP_RANK_SEASON_REWARDS["emerald"]["clan_points"]},
                {"tier": "King of the Critters", "coins": COMP_RANK_SEASON_REWARDS["king"]["coins"],
                 "clan_points": COMP_RANK_SEASON_REWARDS["king"]["clan_points"]},
            ],
        },
        "season_rewards": [
            # First, because it is the biggest thing on offer and the only one
            # that leaves the game. The claim terms ride with it: a prize with
            # no stated way to collect it is the same as no prize.
            f"1st place clan: ${SEASON_GRAND_PRIZE_USD} towards "
            f"{SEASON_GRAND_PRIZE_WHAT}. {SEASON_GRAND_PRIZE_CLAIM}",
            f"1st place clan: {SEASON_REWARD_COINS[0]} Critter Coins each",
            f"2nd place clan: {SEASON_REWARD_COINS[1]} Critter Coins each",
            f"3rd place clan: {SEASON_REWARD_COINS[2]} Critter Coins each",
            f"Rewards need {SEASON_REWARD_MIN_POINTS}+ Clan Points contributed, "
            "and you must still be in the clan when the season ends.",
            f"Each clan's MVP ({MVP_MIN_POINTS}+ points, not mostly from trades) "
            f"gets {MVP_BONUS_COINS} Critter Coins and an MVP chip.",
            f"The top {SEASON_BORDER_TOP_N} clans wear a seasonal border.",
        ],
    }


def handle_get(handler, parsed) -> bool:
    """GET /api/clan/leaderboard and /api/clan/rules: public reads."""
    if parsed.path == "/api/clan/rules":
        handler._send_json({"ok": True, "rules": clan_rules()})
        return True
    if parsed.path != "/api/clan/leaderboard":
        return False
    db = _get_firestore()
    if db is None:
        handler._send_json({"ok": False, "error": "firestore_unavailable"})
        return True
    ensure_season_finalized(db)
    ensure_pending_moves(db)
    sid = _clan_sid()
    handler._send_json({"ok": True, "season": _season_public(sid),
                        "rows": _leaderboard_rows(db, sid)})
    return True


def handle_post(handler, parsed, body: Dict[str, Any]) -> bool:  # noqa: C901
    path = parsed.path
    if not path.startswith("/api/clan/") and not path.startswith("/api/admin/clan"):
        return False

    # ── Admin review endpoints (name/chat reports) ───────────────────────────
    if path.startswith("/api/admin/clan"):
        admin_key = body.get("admin_key") if isinstance(body.get("admin_key"), str) else ""
        env_key = os.environ.get("ADMIN_RECOVERY_KEY", "").strip()
        if not env_key or not secrets.compare_digest(admin_key, env_key):
            handler._send_json({"ok": False, "error": "unauthorized"}, status=403)
            return True
        db = _get_firestore()
        if db is None:
            handler._send_json({"ok": False, "error": "firestore_unavailable"})
            return True
        if path == "/api/admin/clan-reports":
            rows = []
            try:
                for doc in db.collection("clan_reports").limit(200).stream():
                    rows.append({"id": doc.id, **(doc.to_dict() or {})})
            except Exception as exc:  # noqa: BLE001
                print(f"[clan] report list failed: {exc}")
            rows.sort(key=lambda r: -int(r.get("ts") or 0))
            handler._send_json({"ok": True, "reports": rows})
            return True
        if path == "/api/admin/clan-report-act":
            rid = str(body.get("id") or "")
            action = str(body.get("action") or "dismiss")
            try:
                rref = db.collection("clan_reports").document(rid)
                rep = rref.get().to_dict() or {}
                if action == "force_rename" and rep.get("clan_id"):
                    cref = _clans(db).document(str(rep["clan_id"]))
                    csnap = cref.get()
                    if csnap.exists:
                        clan = csnap.to_dict() or {}
                        old_lower = str(clan.get("nameLower") or "")
                        new_name = f"Clan {str(rep['clan_id'])[-6:].upper()}"
                        cref.set({"name": new_name, "nameLower": new_name.lower(),
                                  "description": ""}, merge=True)
                        db.collection("clan_names").document(new_name.lower()).set(
                            {"clan_id": rep["clan_id"], "ts": _now()})
                        if old_lower:
                            db.collection("clan_names").document(old_lower).delete()
                rref.set({"status": "resolved", "action": action,
                          "resolved_ts": _now()}, merge=True)
                handler._send_json({"ok": True})
            except Exception as exc:  # noqa: BLE001
                handler._send_json({"ok": False, "error": str(exc)[:120]})
            return True
        handler._send_json({"ok": False, "error": "unknown_action"}, status=404)
        return True

    action = path[len("/api/clan/"):]
    uid = _auth_uid(body)
    if not uid:
        handler._send_json({"ok": False, "error": "unauthorized"}, status=401)
        return True
    db = _get_firestore()
    if db is None:
        handler._send_json({"ok": False, "error": "firestore_unavailable"})
        return True
    ensure_season_finalized(db)
    ensure_pending_moves(db)
    sid = _clan_sid()

    try:
        handler._send_json(_route_post(db, uid, action, body, sid))
    except Exception as exc:  # noqa: BLE001
        import traceback
        print(f"[clan] {action} failed: {exc}\n{traceback.format_exc(limit=4)}")
        handler._send_json({"ok": False, "error": "server_error"})
    return True


# Actions that cannot move the standings. Everything else can, so the one route
# funnel below drops the leaderboard cache after it succeeds, a clan created
# this second must be on the board the next second. Pure reads are here for the
# obvious reason; chatting and voting for a critter are here because they write,
# but write nothing the leaderboard shows, and throwing the whole-collection
# scan away on every chat line or vote click is a big cost for no change.
_NO_STANDINGS_CHANGE = frozenset({
    "home", "leaderboard", "browse", "get", "season-results", "check-name",
    "challenges", "chat-get", "chat-peek", "chat-send", "vote-critter",
})


def _route_post(db, uid: str, action: str, body: Dict[str, Any], sid: str) -> Dict[str, Any]:
    result = _route_action(db, uid, action, body, sid)
    if action not in _NO_STANDINGS_CHANGE and isinstance(result, dict) and result.get("ok"):
        _lb_invalidate()
    return result


def _route_action(db, uid: str, action: str, body: Dict[str, Any], sid: str  # noqa: C901
                  ) -> Dict[str, Any]:
    # ---- reads -------------------------------------------------------------
    if action == "home":
        _, clan_id, clan, udoc = _with_clan(uid)
        rows = _leaderboard_rows(db, sid)
        mine = None
        if clan_id:
            mine = next((r for r in rows if r["id"] == clan_id), None)
        contrib = {}
        if clan is not None and clan_id:
            slot = (clan.get("seasons") or {}).get(sid) or {}
            contrib = (slot.get("contrib") or {}).get(uid) or {}
        prev_meta = _prev_season_meta(db, sid)
        invites = [i for i in (udoc.get("clan_invites") or [])
                   if isinstance(i, dict) and int(i.get("ts") or 0) > _now() - CLAN_INVITE_TTL_SEC]
        return {"ok": True, "season": _season_public(sid),
                "top3": rows[:3], "total_clans": len(rows),
                "my_clan": mine,
                "my_clan_full": (_clan_profile(db, clan_id, clan, sid, uid) if clan is not None and clan_id else None),
                "my_contribution": {"points": _num(contrib.get("points")),
                                    "game_points": _num(contrib.get("game_points")),
                                    "trade_points": _num(contrib.get("trade_points")),
                                    "weekly": _num((contrib.get("weekly") or {}).get(_week_key())),
                                    "weekly_cap": WEEKLY_POINT_CAP},
                "cooldown_until": _cooldown_active(udoc),
                # The critters I personally own, the icon choices offered when
                # I found a clan, where I am the only member there is.
                "my_unlocked": sorted(_user_icons(udoc)),
                "invites": invites,
                "badges": udoc.get("clan_badges") or [],
                "prev_season": {"sid": _prev_sid(sid),
                                "number": _season_number(_prev_sid(sid)),
                                "name": _season_name(_prev_sid(sid)),
                                "standings": (prev_meta.get("standings") or [])[:10]}}

    if action == "leaderboard":
        return {"ok": True, "season": _season_public(sid), "rows": _leaderboard_rows(db, sid)}

    if action == "browse":
        # EVERY clan is listed, invite-only ones included. Hiding them is what
        # made a newly created clan look like it had never been created: the
        # founder set it to Invite Only, went to Browse to check on it, and the
        # game showed them a world with one clan in it. A clan you cannot press
        # Join on is still a clan that exists, so it is on the list, marked,
        # with the reason you can't join in place of the button.
        q = str(body.get("query") or "").strip().lower()
        rows = _leaderboard_rows(db, sid)
        if q:
            rows = [r for r in rows if q in str(r.get("name") or "").lower()]
        for r in rows:
            r["full"] = r["member_count"] >= CLAN_MAX_MEMBERS
            r["joinable"] = (not r["full"]
                             and r.get("privacy") in ("public", "request", "password"))
        # The clans you can act on first, then the rest; each block by rank.
        open_first = sorted(rows, key=lambda r: (not r["joinable"], r["rank"]))
        # `recommended` is the home screen's ONE-TAP list, so it stays strictly
        # to clans that really are one tap: public and not full. A password
        # clan is a tap plus a word you have to go and ask for, which is not a
        # shortcut. It is still listed in `rows` like everything else.
        recommended = [r for r in open_first
                       if r.get("privacy") == "public" and not r["full"]][:5]
        return {"ok": True, "season": _season_public(sid),
                "rows": open_first[:100], "recommended": recommended,
                "total_clans": len(rows)}

    if action == "get":
        # No clan_id means "mine", the in-game Clan Challenges sheet asks that
        # way, because inside a game the only clan a player cares about is
        # their own and it shouldn't have to know its id to ask.
        clan_id = str(body.get("clan_id") or "")
        if not clan_id:
            usnap = _users(db).document(uid).get()
            clan_id = str(((usnap.to_dict() or {}) if usnap.exists else {}).get("clan_id") or "")
        if not clan_id:
            return {"ok": False, "error": "no_clan"}
        csnap = _clans(db).document(clan_id).get()
        if not csnap.exists:
            return {"ok": False, "error": "no_clan"}
        clan = csnap.to_dict() or {}
        prof = _clan_profile(db, clan_id, clan, sid, uid)
        rows = _leaderboard_rows(db, sid)
        mine = next((r for r in rows if r["id"] == clan_id), None)
        prof["rank"] = mine["rank"] if mine else None
        prof["record"] = mine["record"] if mine else "0-0"
        return {"ok": True, "clan": prof}

    if action == "season-results":
        want = str(body.get("sid") or _prev_sid(sid))
        snap = db.collection("clan_meta").document(f"season_{want}").get()
        meta = snap.to_dict() or {} if snap.exists else {}
        # the caller's own breakdown for that season, if they were in a clan
        _, clan_id, clan, udoc = _with_clan(uid)
        my = None
        if clan is not None and clan_id:
            slot = (clan.get("seasons") or {}).get(want) or {}
            my = (slot.get("contrib") or {}).get(uid)
        # What I personally walked away with: coins from the standings ledger
        # plus every badge/cosmetic stamped for that season.
        my_coins = 0
        for row in meta.get("standings") or []:
            if row.get("clan_id") == clan_id:
                my_coins = int((row.get("rewarded") or {}).get(uid) or 0)
                break
        my_badges = [b for b in (udoc.get("clan_badges") or [])
                     if isinstance(b, dict) and b.get("sid") == want]
        return {"ok": True, "sid": want, "number": _season_number(want),
                "name": _season_name(want), "meta": meta, "my_contribution": my,
                "my_coins": my_coins, "my_badges": my_badges,
                "my_clan_id": clan_id,
                "next_season": _season_public(sid)}

    if action == "check-name":
        name = str(body.get("name") or "")
        ok, why = clan_name_check(name)
        taken = False
        if ok:
            try:
                taken = db.collection("clan_names").document(name.strip().lower()).get().exists
            except Exception:
                taken = False
        return {"ok": True, "clean": ok, "reason": why, "available": ok and not taken}

    if action == "challenges":
        _, clan_id, clan, _udoc = _with_clan(uid)
        if clan is None or not clan_id:
            return {"ok": False, "error": "no_clan"}
        weekly = _weekly_slot(dict(clan))
        slot = (clan.get("seasons") or {}).get(sid) or {}
        return {"ok": True, "week": weekly.get("week"),
                "week_ends_ts": _week_end_ts(),
                "challenges": _challenges_view(weekly, slot, clan)}

    # ---- lifecycle ----------------------------------------------------------
    if action == "create":
        return _create_clan(uid, body)
    if action == "join":
        return _join_clan(uid, body)
    if action == "leave":
        return _leave_clan(uid)
    if action == "disband":
        _, clan_id, clan, _udoc = _with_clan(uid)
        if clan is None or not clan_id:
            return {"ok": False, "error": "no_clan"}
        if clan.get("owner_uid") != uid:
            return {"ok": False, "error": "owner_only"}
        try:
            for m_uid in list((clan.get("members") or {}).keys()):
                _users(db).document(m_uid).set({"clan_id": ""}, merge=True)
            _clans(db).document(clan_id).delete()
            nl = str(clan.get("nameLower") or "")
            if nl:
                db.collection("clan_names").document(nl).delete()
            return {"ok": True}
        except Exception as exc:  # noqa: BLE001
            print(f"[clan] disband failed: {exc}")
            return {"ok": False, "error": "disband_failed"}

    if action == "request":
        clan_id = str(body.get("clan_id") or "")
        cref = _clans(db).document(clan_id)
        csnap = cref.get()
        if not csnap.exists:
            return {"ok": False, "error": "no_clan"}
        clan = csnap.to_dict() or {}
        if clan.get("privacy") != "request":
            return {"ok": False, "error": "not_requestable"}
        usnap = _users(db).document(uid).get()
        udoc = usnap.to_dict() or {} if usnap.exists else {}
        if udoc.get("clan_id"):
            return {"ok": False, "error": "already_in_clan"}
        if len(clan.get("members") or {}) >= CLAN_MAX_MEMBERS:
            return {"ok": False, "error": "clan_full"}
        reqs = [r for r in (clan.get("join_requests") or []) if str(r.get("uid")) != uid][:19]
        reqs.append({"uid": uid, "name": udoc.get("nickname") or udoc.get("username") or "Player",
                     "ts": _now()})
        cref.set({"join_requests": reqs}, merge=True)
        return {"ok": True}

    if action == "request-cancel":    # withdraw my own pending join request
        clan_id = str(body.get("clan_id") or "")
        cref = _clans(db).document(clan_id)
        csnap = cref.get()
        if csnap.exists:
            clan = csnap.to_dict() or {}
            cref.set({"join_requests": [r for r in (clan.get("join_requests") or [])
                                        if str(r.get("uid")) != uid]}, merge=True)
        return {"ok": True}

    if action == "request-act":       # accept / reject a join request
        _, clan_id, clan, _udoc = _with_clan(uid)
        if clan is None or not clan_id:
            return {"ok": False, "error": "no_clan"}
        if not _has_perm(clan, uid, "review_requests"):
            return {"ok": False, "error": "no_permission"}
        target = str(body.get("uid") or "")
        accept = bool(body.get("accept"))
        reqs = clan.get("join_requests") or []
        row = next((r for r in reqs if str(r.get("uid")) == target), None)
        if not row:
            return {"ok": False, "error": "no_request"}
        if accept:
            res = _join_clan_direct(db, target, clan_id)
            if not res.get("ok"):
                # still clear the request if the player became unjoinable
                if res.get("error") in ("already_in_clan", "no_user"):
                    _clans(db).document(clan_id).set(
                        {"join_requests": [r for r in reqs if str(r.get("uid")) != target]}, merge=True)
                return res
            return {"ok": True, "joined": True}
        _clans(db).document(clan_id).set(
            {"join_requests": [r for r in reqs if str(r.get("uid")) != target]}, merge=True)
        return {"ok": True, "joined": False}

    if action == "invite":
        _, clan_id, clan, udoc = _with_clan(uid)
        if clan is None or not clan_id:
            return {"ok": False, "error": "no_clan"}
        if not _has_perm(clan, uid, "invite"):
            return {"ok": False, "error": "no_permission"}
        if len(clan.get("members") or {}) >= CLAN_MAX_MEMBERS:
            return {"ok": False, "error": "clan_full"}
        to_uid = str(body.get("to_uid") or "").strip()
        if not to_uid:
            # to_code is what the Clans invite box sends; to_name is what the
            # profile / Messages buttons send. Both go through one resolver.
            raw = body.get("to_code") or body.get("to_name") or ""
            to_uid, why = _resolve_invitee(db, str(raw))
            if why:
                return {"ok": False, "error": why}
        if to_uid == uid:
            return {"ok": False, "error": "self_invite"}
        if not to_uid:
            return {"ok": False, "error": "no_user"}
        tsnap = _users(db).document(to_uid).get()
        if not tsnap.exists:
            return {"ok": False, "error": "no_user"}
        tdoc = tsnap.to_dict() or {}
        if str(tdoc.get("clan_id") or "") == clan_id:
            return {"ok": False, "error": "already_member"}
        inviter = udoc.get("nickname") or udoc.get("username") or "A player"
        invites = [i for i in (tdoc.get("clan_invites") or [])
                   if isinstance(i, dict) and int(i.get("ts") or 0) > _now() - CLAN_INVITE_TTL_SEC
                   and str(i.get("clan_id")) != clan_id][-(CLAN_INVITE_MAX - 1):]
        invites.append({"clan_id": clan_id, "name": clan.get("name"),
                        "icon": clan.get("icon"), "by": inviter, "ts": _now()})
        _users(db).document(to_uid).set({"clan_invites": invites}, merge=True)
        # Drop a DM-style note in their Messages inbox (same subcollection the
        # trade system mirrors into: shows up with their existing rules). The
        # clan critter rides along so the invite shows its icon everywhere.
        try:
            _users(db).document(to_uid).collection("messages").document(
                f"claninvite_{clan_id}_{_now()}").set({
                    "from": uid, "fromName": inviter, "kind": "clan_invite",
                    "text": f"🛡️ {inviter} invited you to join the clan "
                            f"“{clan.get('name')}”: open the Clans tab to accept!",
                    "clan_id": clan_id, "clan_name": clan.get("name"),
                    "clan_icon": clan.get("icon"), "ts": _now(), "read": False,
                })
        except Exception as exc:  # noqa: BLE001
            print(f"[clan] invite message failed: {exc}")
        # Echo the name back: the invite box only ever knew a friend code, so
        # this is the only way its toast can say WHO got invited.
        return {"ok": True, "name": tdoc.get("nickname") or tdoc.get("username") or "player"}

    if action == "report":
        # Deliberately ABOVE the members-only gate: anyone signed in can report
        # a clan NAME that slipped past the filter. Message reports still
        # require being in the clan whose chat the message lives in.
        kind = str(body.get("kind") or "name")
        _, my_clan_id, _my_clan, _u = _with_clan(uid)
        target_clan = str(body.get("clan_id") or my_clan_id or "")
        if not target_clan:
            return {"ok": False, "error": "bad_clan"}
        if kind == "message" and target_clan != my_clan_id:
            return {"ok": False, "error": "no_permission"}
        rep = {"kind": kind, "clan_id": target_clan, "by": uid, "ts": _now(),
               "status": "pending", "reason": str(body.get("reason") or "")[:200]}
        if kind == "message" and body.get("msg_id"):
            rep["msg_id"] = str(body.get("msg_id"))[:40]
            rep["msg_text"] = str(body.get("msg_text") or "")[:200]
        if kind == "name":
            try:
                csnap = _clans(db).document(target_clan).get()
                rep["clan_name"] = ((csnap.to_dict() or {}).get("name") if csnap.exists else "") or ""
            except Exception:
                pass
        db.collection("clan_reports").document(f"r{_now()}_{secrets.token_hex(3)}").set(rep)
        return {"ok": True}

    if action == "invite-decline":
        clan_id = str(body.get("clan_id") or "")
        usnap = _users(db).document(uid).get()
        udoc = usnap.to_dict() or {} if usnap.exists else {}
        invites = [i for i in (udoc.get("clan_invites") or [])
                   if isinstance(i, dict) and str(i.get("clan_id")) != clan_id]
        _users(db).document(uid).set({"clan_invites": invites}, merge=True)
        return {"ok": True}

    # ---- everything below needs an existing membership ---------------------
    _, clan_id, clan, udoc = _with_clan(uid)
    if clan is None or not clan_id:
        return {"ok": False, "error": "no_clan"}

    if action == "kick":
        target = str(body.get("uid") or "")
        tmem = _member_of(clan, target)
        if not tmem:
            return {"ok": False, "error": "not_member"}
        if not _has_perm(clan, uid, "remove_members"):
            return {"ok": False, "error": "no_permission"}
        my_rank = _role_rank(clan, _member_of(clan, uid))
        if _role_rank(clan, tmem) >= my_rank and clan.get("owner_uid") != uid:
            return {"ok": False, "error": "outranked"}   # captains can't kick captains/owner
        if target == clan.get("owner_uid"):
            return {"ok": False, "error": "cannot_remove_owner"}
        return _leave_clan(target, kicked_by=uid, clan_id_hint=clan_id)

    if action == "role":
        target = str(body.get("uid") or "")
        new_role = str(body.get("role") or "member")
        custom_id = str(body.get("custom_role_id") or "") or None
        tmem = _member_of(clan, target)
        if not tmem:
            return {"ok": False, "error": "not_member"}
        if target == clan.get("owner_uid"):
            return {"ok": False, "error": "cannot_change_owner"}
        if new_role == "owner":
            return {"ok": False, "error": "use_transfer"}
        if new_role not in ("captain", "recruiter", "member"):
            return {"ok": False, "error": "bad_role"}
        is_owner = clan.get("owner_uid") == uid
        if new_role == "captain" and not is_owner:
            return {"ok": False, "error": "owner_only"}   # only the owner promotes captains
        if not is_owner:
            if not _has_perm(clan, uid, "change_roles"):
                return {"ok": False, "error": "no_permission"}
            if _role_rank(clan, tmem) >= _role_rank(clan, _member_of(clan, uid)):
                return {"ok": False, "error": "outranked"}
        if custom_id and not any(r.get("id") == custom_id for r in clan.get("custom_roles") or []):
            return {"ok": False, "error": "no_such_role"}
        members = clan.get("members") or {}
        members[target] = {**tmem, "role": new_role, "custom_role_id": custom_id}
        label = new_role + (f" · {custom_id}" if custom_id else "")
        _activity_push(clan, "role", f"🎖 {tmem.get('name')} is now {label}")
        _clans(db).document(clan_id).set({"members": members, "activity": clan.get("activity")}, merge=True)
        return {"ok": True}

    if action == "transfer":
        if clan.get("owner_uid") != uid:
            return {"ok": False, "error": "owner_only"}
        target = str(body.get("uid") or "")
        tmem = _member_of(clan, target)
        if not tmem or target == uid:
            return {"ok": False, "error": "not_member"}
        members = clan.get("members") or {}
        members[uid] = {**members[uid], "role": "captain"}
        members[target] = {**tmem, "role": "owner", "custom_role_id": None}
        _activity_push(clan, "transfer",
                       f"👑 {tmem.get('name')} is the new clan owner")
        _clans(db).document(clan_id).set({"members": members, "owner_uid": target,
                                          "activity": clan.get("activity")}, merge=True)
        _chat_system(db, clan_id, f"👑 {tmem.get('name')} is the new clan owner!")
        return {"ok": True}

    if action == "settings":
        if clan.get("owner_uid") != uid:
            return {"ok": False, "error": "owner_only"}   # icon/privacy/desc = owner (major settings)
        updates: Dict[str, Any] = {}
        if isinstance(body.get("icon"), str) and body.get("icon"):
            icon = _norm_icon(body["icon"])
            if not icon:
                return {"ok": False, "error": "bad_icon"}
            # The clan's critter (its icon everywhere, and the banner image on
            # its own page) must be unlocked by at least one current member.
            if not _icon_pool_allowing(db, clan, icon)[0]:
                return {"ok": False, "error": "icon_not_unlocked"}
            updates["icon"] = icon
            updates["icon_name"] = str(body.get("icon_name") or "").strip()[:40]
        if isinstance(body.get("description"), str):
            desc = str(body["description"]).strip()[:CLAN_DESC_MAX]
            if desc and text_is_profane(desc):
                return {"ok": False, "error": "bad_description"}
            updates["description"] = desc
        if isinstance(body.get("privacy"), str) and body.get("privacy"):
            if body["privacy"] not in PRIVACY_MODES:
                return {"ok": False, "error": "bad_privacy"}
            updates["privacy"] = body["privacy"]
        # The password: settable on its own (change the word, same mode), or
        # alongside the switch INTO password mode. Switching to password mode
        # without one is only allowed if the clan already has a password to
        # fall back on, otherwise the clan would lock everybody out forever.
        new_privacy = updates.get("privacy", clan.get("privacy"))
        has_password = isinstance(clan.get("join_password"), dict)
        if body.get("password") not in (None, ""):
            pw, why = _clean_password(body.get("password"))
            if why:
                return {"ok": False, "error": why}
            if new_privacy != "password":
                # Setting a password IS asking for password mode; saying so
                # beats silently storing one that nothing would ever check.
                return {"ok": False, "error": "password_needs_password_mode"}
            updates["join_password"] = _hash_password(pw)
        elif new_privacy == "password" and not has_password:
            return {"ok": False, "error": "password_required"}
        if body.get("captains_can_edit_roles") is not None:
            updates["captains_can_edit_roles"] = bool(body.get("captains_can_edit_roles"))
        if not updates:
            return {"ok": False, "error": "nothing_to_update"}
        _activity_push(clan, "settings", "⚙️ Clan settings updated")
        updates["activity"] = clan.get("activity")
        _clans(db).document(clan_id).set(updates, merge=True)
        return {"ok": True}

    if action == "custom-role":
        is_owner = clan.get("owner_uid") == uid
        if not (is_owner or _has_perm(clan, uid, "edit_custom_roles")):
            return {"ok": False, "error": "no_permission"}
        op = str(body.get("op") or "create")
        roles = list(clan.get("custom_roles") or [])
        if op == "delete":
            rid = str(body.get("id") or "")
            roles = [r for r in roles if r.get("id") != rid]
            members = clan.get("members") or {}
            for m_uid, mem in members.items():
                if mem.get("custom_role_id") == rid:
                    members[m_uid] = {**mem, "custom_role_id": None}
            _clans(db).document(clan_id).set({"custom_roles": roles, "members": members}, merge=True)
            return {"ok": True}
        raw = body.get("role") if isinstance(body.get("role"), dict) else {}
        rname = str(raw.get("name") or "").strip()[:24]
        if len(rname) < 2 or text_is_profane(rname):
            return {"ok": False, "error": "bad_role_name"}
        # Only the whitelisted, never-owner-level permissions can be granted,
        # and a captain can never hand out more than a captain has.
        perms = {p: bool((raw.get("perms") or {}).get(p)) for p in CUSTOM_PERMS}
        if op == "create":
            if len(roles) >= CLAN_MAX_CUSTOM_ROLES:
                return {"ok": False, "error": "too_many_roles"}
            roles.append({"id": "r" + secrets.token_hex(3), "name": rname, "perms": perms})
        else:  # edit
            rid = str(raw.get("id") or body.get("id") or "")
            hit = next((r for r in roles if r.get("id") == rid), None)
            if not hit:
                return {"ok": False, "error": "no_such_role"}
            hit["name"], hit["perms"] = rname, perms
        _activity_push(clan, "roles", f"🧩 Custom role “{rname}” {'created' if op == 'create' else 'updated'}")
        _clans(db).document(clan_id).set({"custom_roles": roles, "activity": clan.get("activity")}, merge=True)
        return {"ok": True, "roles": roles}

    if action == "announce":
        if not _has_perm(clan, uid, "post_announcements"):
            return {"ok": False, "error": "no_permission"}
        text = censor_text(str(body.get("text") or "").strip()[:CLAN_CHAT_MAX])
        if not text:
            return {"ok": False, "error": "empty"}
        pin = bool(body.get("pin"))
        me = _member_of(clan, uid) or {}
        if pin and not _has_perm(clan, uid, "pin_announcements"):
            return {"ok": False, "error": "no_permission"}
        updates: Dict[str, Any] = {}
        if pin:
            updates["pinned_announcement"] = {"text": text, "by": me.get("name"), "ts": _now()}
        _activity_push(clan, "announce", f"📣 {me.get('name')}: {text[:80]}")
        updates["activity"] = clan.get("activity")
        _clans(db).document(clan_id).set(updates, merge=True)
        try:
            _clans(db).document(clan_id).collection("chat").document(_chat_mid()).set({
                "ts": _now(), "uid": uid, "name": me.get("name"),
                "kind": "announce", "text": text})
        except Exception as exc:  # noqa: BLE001
            print(f"[clan] announce chat write failed: {exc}")
        return {"ok": True}

    if action == "unpin":
        if not _has_perm(clan, uid, "pin_announcements"):
            return {"ok": False, "error": "no_permission"}
        _clans(db).document(clan_id).set({"pinned_announcement": None}, merge=True)
        return {"ok": True}

    if action == "chat-send":
        me = _member_of(clan, uid) or {}
        muted_until = int((clan.get("muted") or {}).get(uid) or 0)
        if muted_until > _now():
            return {"ok": False, "error": "muted", "until": muted_until}
        kind = str(body.get("kind") or "msg")
        if kind not in ("msg", "game_invite", "tourney_invite"):
            kind = "msg"
        text = censor_text(str(body.get("text") or "").strip()[:CLAN_CHAT_MAX])
        if not text:
            return {"ok": False, "error": "empty"}
        doc = {"ts": _now(), "uid": uid, "name": me.get("name"), "kind": kind,
               "text": text}
        if kind == "game_invite" and body.get("room_id"):
            doc["room_id"] = str(body.get("room_id"))[:12].upper()
        if kind == "tourney_invite" and body.get("tid"):
            doc["tid"] = str(body.get("tid"))[:24]
        mid = _chat_mid(doc["ts"])
        _clans(db).document(clan_id).collection("chat").document(mid).set(doc)
        # Occasional prune so the unordered limit() scan below can never fill
        # up with ancient messages and starve the recent ones.
        try:
            if secrets.randbelow(20) == 0:
                cutoff = _now() - 14 * 24 * 3600
                col = _clans(db).document(clan_id).collection("chat")
                for old in col.limit(400).stream():
                    if int(((old.to_dict() or {}).get("ts")) or 0) < cutoff:
                        col.document(old.id).delete()
        except Exception as exc:  # noqa: BLE001
            print(f"[clan] chat prune failed: {exc}")
        # Hand the whole message back so the sender's own line can appear the
        # instant they press Send, without waiting for the next poll.
        return {"ok": True, "id": mid, "message": {"id": mid, **doc}}

    if action == "chat-get":
        # `since` is INCLUSIVE, and that is the whole point. `ts` is only
        # accurate to the second, so an exclusive cursor silently ate every
        # message written in the same second as the newest one the caller
        # already had: two clanmates replying at once, or a reply landing in
        # the same second as your own line, and it was gone for good, because
        # the cursor never moves back. That is what "the clan chat doesn't
        # work" was. The caller re-reads that one second and drops the ids it
        # already holds, which costs a handful of duplicate rows per poll and
        # cannot lose a message.
        since = int(body.get("since") or 0)
        rows = _chat_rows(db, clan_id, since)
        muted_until = int((clan.get("muted") or {}).get(uid) or 0)
        return {"ok": True, "messages": rows[-CLAN_CHAT_FETCH:],
                "muted_until": muted_until if muted_until > _now() else 0,
                "server_ts": _now(),
                "pinned": clan.get("pinned_announcement")}

    if action == "chat-peek":
        # The cheapest possible "has anyone said anything?": ONE message, for
        # the background poller that raises the clan-chat notification from
        # any page. Never returns history, so it stays affordable to call on a
        # timer from every signed-in member.
        rows = _chat_rows(db, clan_id, 0, cap=1)
        last = rows[-1] if rows else None
        return {"ok": True, "server_ts": _now(),
                "last": ({"id": last.get("id"), "ts": last.get("ts"),
                          "uid": last.get("uid"), "name": last.get("name"),
                          "kind": last.get("kind"),
                          "text": str(last.get("text") or "")[:120]}
                         if last else None),
                "clan_id": clan_id, "clan_name": clan.get("name"),
                "clan_icon": clan.get("icon")}

    if action == "chat-mod":
        if not _has_perm(clan, uid, "moderate_chat"):
            return {"ok": False, "error": "no_permission"}
        op = str(body.get("op") or "")
        if op == "delete" and body.get("id"):
            _clans(db).document(clan_id).collection("chat").document(str(body["id"])).set(
                {"deleted": True}, merge=True)
            return {"ok": True}
        if op == "mute" and body.get("uid"):
            target = str(body["uid"])
            if target == clan.get("owner_uid"):
                return {"ok": False, "error": "cannot_mute_owner"}
            mins = max(5, min(1440, int(body.get("minutes") or 30)))
            muted = clan.get("muted") or {}
            muted[target] = _now() + mins * 60
            _clans(db).document(clan_id).set({"muted": muted}, merge=True)
            return {"ok": True, "until": muted[target]}
        if op == "unmute" and body.get("uid"):
            # merge=True merges nested maps, so popping the key locally would
            # leave the stored mute in place: delete the field explicitly.
            _clans(db).document(clan_id).set(
                {"muted": {str(body["uid"]): _field_delete()}}, merge=True)
            return {"ok": True}
        return {"ok": False, "error": "bad_op"}

    if action == "events":
        op = str(body.get("op") or "list")
        events = [e for e in (clan.get("events") or []) if isinstance(e, dict)]
        me = _member_of(clan, uid) or {}
        if op == "list":
            return {"ok": True, "events": events}
        if op == "create":
            if not _has_perm(clan, uid, "create_events"):
                return {"ok": False, "error": "no_permission"}
            events = [e for e in events if int(e.get("ts") or 0) > _now() - 3600][:CLAN_MAX_EVENTS - 1]
            name = str(body.get("name") or "").strip()[:60]
            if not name or text_is_profane(name):
                return {"ok": False, "error": "bad_name"}
            ev = {"id": "e" + secrets.token_hex(3), "name": name,
                  "ts": max(_now(), int(body.get("ts") or 0)),
                  "desc": str(body.get("desc") or "").strip()[:200],
                  "host_uid": uid, "host_name": me.get("name"),
                  "attending": [uid], "reminders": []}
            events.append(ev)
            _activity_push(clan, "event", f"📅 Event scheduled: {name}")
            _clans(db).document(clan_id).set({"events": events, "activity": clan.get("activity")}, merge=True)
            _chat_system(db, clan_id, f"📅 New clan event: {name}: check the Events tab!")
            # "Organize three events with your clan" counts events SCHEDULED,
            # so deleting one afterwards can't un-count it (and re-creating it
            # can't double-count, because each create is one new event).
            _bump_season(db, clan_id, add={"events_held": 1})
            return {"ok": True, "events": events}
        eid = str(body.get("id") or "")
        ev = next((e for e in events if e.get("id") == eid), None)
        if not ev:
            return {"ok": False, "error": "no_event"}
        if op == "delete":
            if ev.get("host_uid") != uid and not _has_perm(clan, uid, "create_events"):
                return {"ok": False, "error": "no_permission"}
            events = [e for e in events if e.get("id") != eid]
        elif op == "join":
            if uid not in ev.get("attending", []):
                ev.setdefault("attending", []).append(uid)
        elif op == "leave":
            ev["attending"] = [u for u in ev.get("attending", []) if u != uid]
            ev["reminders"] = [u for u in ev.get("reminders", []) if u != uid]
        elif op == "remind":
            if uid not in ev.get("reminders", []):
                ev.setdefault("reminders", []).append(uid)
        else:
            return {"ok": False, "error": "bad_op"}
        _clans(db).document(clan_id).set({"events": events}, merge=True)
        return {"ok": True, "events": events}

    if action == "rival":
        # Friendly season rivalry: ONE rival clan per season. Declaring one is
        # itself a season challenge, and finishing ahead of them is another,
        # but the rivalry never pays per GAME, because two clans farming each
        # other for points is exactly what that would become (see spec).
        if not (clan.get("owner_uid") == uid or _has_perm(clan, uid, "post_announcements")):
            return {"ok": False, "error": "no_permission"}
        op = str(body.get("op") or "set")
        rivals = dict(clan.get("rivals") or {})
        if op == "clear":
            # merge=True never drops a nested key: clear it explicitly.
            _clans(db).document(clan_id).set({"rivals": {sid: _field_delete()}}, merge=True)
            return {"ok": True, "rival": None}
        target = str(body.get("clan_id") or "").strip()
        if not target or target == clan_id:
            return {"ok": False, "error": "bad_clan"}
        tsnap = _clans(db).document(target).get()
        if not tsnap.exists:
            return {"ok": False, "error": "no_clan"}
        rivals[sid] = target
        _activity_push(clan, "rival",
                       f"⚔️ Friendly rivalry declared with {(tsnap.to_dict() or {}).get('name')}")
        _clans(db).document(clan_id).set({"rivals": rivals,
                                          "activity": clan.get("activity")}, merge=True)
        _chat_system(db, clan_id,
                     f"⚔️ {(tsnap.to_dict() or {}).get('name')} is our friendly rival this season!")
        # Choose Your Rival is a one-shot flag, not a counter: swapping rivals
        # a dozen times can't score it a dozen times.
        _bump_season(db, clan_id, set_to={"rival_set": 1})
        return {"ok": True, "rival": target}

    if action == "vote-critter":
        # The winner of this vote becomes the clan's icon on the Clans tab, so
        # it is gated exactly like the clan icon itself: somebody in the clan
        # has to have unlocked it.
        icon = _norm_icon(body.get("icon"))
        if not icon:
            return {"ok": False, "error": "bad_icon"}
        allowed, pool = _icon_pool_allowing(db, clan, icon)
        if not allowed:
            return {"ok": False, "error": "icon_not_unlocked"}
        # One ballot is one key in one map, so this needs no transaction and no
        # read: a nested merge writes seasons.<sid>.critter_votes.<uid> and
        # touches nothing else. It used to re-read the clan and write the WHOLE
        # document back, which meant two clanmates voting at the same moment
        # fought over every field in the clan, and a full-doc round trip per
        # click is most of why voting felt like it hung.
        _clans(db).document(clan_id).set(
            {"seasons": {sid: {"critter_votes": {uid: icon}}}}, merge=True)
        # Hand back the new standings so the client can repaint the vote list
        # and the tab critter from this response alone. Without it the client
        # had to fetch /home AND /get again before the tally moved.
        votes = dict(((clan.get("seasons") or {}).get(sid) or {}).get("critter_votes") or {})
        votes[uid] = icon
        fav, tally = _favorite_from_votes(votes, pool)
        return {"ok": True, "my_vote": icon,
                "favorite_critter": fav, "favorite_votes": tally}

    if action == "claim-game":
        return claim_game_points(uid, str(body.get("room_id") or ""))

    return {"ok": False, "error": "unknown_action"}


def _join_clan_direct(db, uid: str, clan_id: str) -> Dict[str, Any]:
    """Accept-request path: joins without privacy checks (a reviewer already
    said yes). Same one-clan / capacity / cooldown-preserving rules."""
    transactional = _txn_helpers()
    clan_ref = _clans(db).document(clan_id)
    user_ref = _users(db).document(uid)
    txn = db.transaction()

    @transactional
    def _run(t) -> Dict[str, Any]:
        csnap = clan_ref.get(transaction=t)
        if not csnap.exists:
            return {"ok": False, "error": "no_clan"}
        clan = csnap.to_dict() or {}
        usnap = user_ref.get(transaction=t)
        if not usnap.exists:
            return {"ok": False, "error": "no_user"}
        udoc = usnap.to_dict() or {}
        if udoc.get("clan_id"):
            return {"ok": False, "error": "already_in_clan"}
        members = clan.get("members") or {}
        if len(members) >= CLAN_MAX_MEMBERS:
            return {"ok": False, "error": "clan_full"}
        nick = udoc.get("nickname") or udoc.get("username") or "Player"
        members[uid] = {"name": nick, "role": "member", "custom_role_id": None,
                        "joined_ts": _now(), "avatar": str(udoc.get("avatar_url") or "")}
        clan["members"] = members
        clan["join_requests"] = [r for r in (clan.get("join_requests") or [])
                                 if str(r.get("uid")) != uid]
        # Fresh Recruits counts arrivals this season, inside the same
        # transaction as the arrival itself.
        slot = _season_slot(clan, _clan_sid())
        slot["new_members"] = _num(slot.get("new_members")) + 1
        _activity_push(clan, "join", f"🌊 {nick} joined the clan")
        t.set(clan_ref, clan)
        t.set(user_ref, {"clan_id": clan_id, "clan_joined_ts": _now()}, merge=True)
        return {"ok": True, "nick": nick}

    try:
        res = _run(txn)
    except Exception as exc:  # noqa: BLE001
        print(f"[clan] request-accept join failed: {exc}")
        return {"ok": False, "error": "join_failed"}
    if res.get("ok"):
        _members_invalidate([uid])
        _chat_system(db, clan_id, f"🌊 {res.pop('nick', 'A new member')} joined the clan: say hi!")
        _bump_season(db, clan_id)      # re-sweep: the new arrival may finish one
    return res
