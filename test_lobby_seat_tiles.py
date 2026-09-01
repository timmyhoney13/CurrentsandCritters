#!/usr/bin/env python3
"""The waiting room's seat tiles: the host's removal, and the look on a seat.

Run:  python3 test_lobby_seat_tiles.py

The lobby used to be a list of dots and names, and the only way to change the
size of the table was a pair of steppers. Two things changed:

  • Every seat is now a tile showing the player the way the rest of the game
    already shows them: their critter, the background they equipped behind it,
    and their Level. So that look has to travel WITH the seat list, not only
    inside a running game's public state, which is what the first half here
    pins.

  • The host can remove somebody from their own lobby. That is deliberately
    NOT the in-game kick: the in-game one is a unanimous vote (see
    test_kick_and_skip_votes.py) because taking a seat off somebody mid-match
    should need the table's agreement. Before the game starts the room is the
    host's, so it is theirs alone, and the second half pins every way it must
    refuse.

The rule that ties the whole screen together and is easy to get wrong: an
unclaimed human seat does NOT quietly become a bot at kickoff. start_game
refuses outright while any one of them is open. So the ONLY ways on are
somebody sitting down, or the host turning that seat into a bot. Anything the
lobby says about it has to match that, and the last section pins it.
"""
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))

spec = importlib.util.spec_from_file_location(
    "mp_server_lobby_tiles", os.path.join(ROOT, "multiplayer_server.py"))
mp = importlib.util.module_from_spec(spec)
sys.modules["mp_server_lobby_tiles"] = mp
spec.loader.exec_module(mp)

Room = mp.GameRoom

failures = 0
checks = 0


def check(cond, label):
    global failures, checks
    checks += 1
    if cond:
        return
    failures += 1
    print("  ✗ " + label)


def seated(humans=3, bots=1, names=("Bo", "Cy", "Di", "Ed", "Fi"), **kw):
    """A lobby with every human seat claimed. Returns (room, {name: token})."""
    r = Room(room_id="ABCD", host_name="Tim",
             total_players=humans + bots, human_players=humans,
             ai_players=bots, **kw)
    for i in range(1, humans):
        r.claim_seat(names[i - 1], i, None)
    return r, {s.claimed_name: s.token for s in r.seats if s.token}


def seat_by_name(r, name):
    for s in r.seats:
        if s.claimed_name == name:
            return s
    return None


# ══ 1. A seat carries the look the tile draws ═════════════════════════════════
print("the look travels with the seat:")

r, tok = seated(humans=2, bots=1)
snap = r.seat_snapshot_locked()
for field in ("avatar", "background", "level"):
    check(field in snap[0], f"the seat list carries {field}")
check(snap[0]["avatar"] == "" and snap[0]["background"] == "" and snap[0]["level"] == 0,
      "a seat that has pushed nothing reports empties, never null")

for field in ("xp", "xp_goal", "best", "games", "title"):
    check(field in snap[0], f"the seat list carries {field}")

out = r.set_avatar({"seat_token": tok["Tim"], "avatar": "/avatars/clownfish.png", "level": 47,
                    "xp": 3120, "xp_goal": 4600, "best": 412, "games": 96,
                    "title": "Reef Wanderer"})
check(out["ok"], "a seat can push its whole name plate with its avatar")
snap = r.seat_snapshot_locked()
check(snap[0]["avatar"] == "/avatars/clownfish.png", "the pushed avatar is on the seat list")
check(snap[0]["level"] == 47, "so is the level")
check((snap[0]["xp"], snap[0]["xp_goal"]) == (3120, 4600), "so is the XP progress")
check((snap[0]["best"], snap[0]["games"]) == (412, 96), "so is the record")
check(snap[0]["title"] == "Reef Wanderer", "so is the level title")

r.set_background({"seat_token": tok["Tim"], "background": "/backgrounds/bg-kelp.png"})
check(r.seat_snapshot_locked()[0]["background"] == "/backgrounds/bg-kelp.png",
      "the equipped background is on the seat list too")

# An old client sends an avatar and no level at all. That must leave the level
# alone, not blank the number the player already had showing.
r.set_avatar({"seat_token": tok["Tim"], "avatar": "/avatars/mullet.png"})
check(r.seat_snapshot_locked()[0]["level"] == 47,
      "an avatar push with no level leaves the level where it was")

# Level is decoration, exactly like the avatar, so it is clamped rather than
# trusted: nothing downstream may be handed a level of 9999 or -3.
r.set_avatar({"seat_token": tok["Tim"], "avatar": "/avatars/mullet.png", "level": 9999})
check(r.seat_snapshot_locked()[0]["level"] == 100, "a level over the cap is clamped to 100")
r.set_avatar({"seat_token": tok["Tim"], "avatar": "/avatars/clownfish.png", "level": -5})
check(r.seat_snapshot_locked()[0]["level"] == 0, "a negative level is clamped to 0")

# The rest of the plate is decoration on the same terms, so it is clamped too:
# nothing downstream may be handed a best score of a billion or a novel.
r.set_avatar({"seat_token": tok["Tim"], "avatar": "/avatars/clownfish.png",
              "best": 10 ** 9, "games": -4, "xp": -1, "title": "x" * 200})
snap = r.seat_snapshot_locked()[0]
check(snap["best"] == 100000, "an absurd best score is clamped")
check(snap["games"] == 0 and snap["xp"] == 0, "negative counts are clamped to zero")
check(len(snap["title"]) <= 32, "a long title is cut to something a card can hold")

# An avatar push that mentions none of them leaves them all alone.
r.set_avatar({"seat_token": tok["Tim"], "avatar": "/avatars/mullet.png"})
check(r.seat_snapshot_locked()[0]["best"] == 100000,
      "an avatar push with no plate leaves the plate where it was")

# The look must not leak between seats: one push, one seat.
check(r.seat_snapshot_locked()[1]["avatar"] == "",
      "pushing my look does not dress anybody else's seat")


# ══ 2. The host's own removal ═════════════════════════════════════════════════
print("the host removes somebody from the lobby:")

r, tok = seated(humans=3, bots=1)
target = seat_by_name(r, "Cy")
out = r.lobby_remove_player(r.host_control_token, tok["Tim"], target.index)
check(out["ok"] and out["removed"], "the host can remove a player before the game starts")
check(out["name"] == "Cy", "the reply names who went")
check(target.token is None and target.claimed_name is None, "the seat opens back up")
check(target.kind == "human", "it is still a human seat, not silently a bot")
check(not target.kicked, "and it is not left flagged as a played-out seat")
check(sum(1 for s in r.seats if s.kind == "human") == 3,
      "removing a player does not shrink the table, it frees a seat")
check(target.avatar is None and target.background is None and target.level == 0,
      "the freed seat drops their look, so the spot is not left wearing their face")
check(target.xp == 0 and target.best == 0 and target.games == 0 and target.title == "",
      "…and none of their numbers either")

# A removal the player can undo by pressing Join again is not a removal.
check("cy" in r.kicked_names, "the removed name is remembered")
back = r.claim_seat("Cy", target.index, None)
check(not back.get("ok"), "the removed player cannot simply rejoin with the code")

# A seat recycled by a resize must not carry the old look either.
r_recycle, tok_recycle = seated(humans=3, bots=0)
r_recycle.set_avatar({"seat_token": tok_recycle["Bo"], "avatar": "/avatars/clownfish.png", "level": 31})
r_recycle.lobby_remove_player(r_recycle.host_control_token, tok_recycle["Tim"],
                              seat_by_name(r_recycle, "Bo").index)
r_recycle.configure_lobby_seats(r_recycle.host_control_token, tok_recycle["Tim"], 2, 1)
check(all((s.level == 0 and not s.avatar) for s in r_recycle.seats if not s.token),
      "a seat turned into a bot carries no leftover Level or critter")

# Somebody else may take the freed seat straight away.
again = r.claim_seat("Zed", target.index, None)
check(again.get("ok"), "the freed seat is open to anybody else")

# The host's own seat token works as authorisation, not just the room token.
r2, tok2 = seated(humans=3, bots=0)
out = r2.lobby_remove_player("", tok2["Tim"], seat_by_name(r2, "Bo").index)
check(out["ok"], "the host's seat token authorises the removal on its own")


# ══ 3. Every way it has to refuse ═════════════════════════════════════════════
print("removal refusals:")

r, tok = seated(humans=3, bots=1)
out = r.lobby_remove_player("", tok["Bo"], seat_by_name(r, "Cy").index)
check(not out["ok"] and out["error"] == "host authorization required",
      "a player who is not the host cannot remove anybody")
check(seat_by_name(r, "Cy") is not None, "…and the target is still sitting there")

out = r.lobby_remove_player("nonsense-token", None, seat_by_name(r, "Cy").index)
check(not out["ok"], "a made-up host token is refused")

out = r.lobby_remove_player(r.host_control_token, tok["Tim"], 0)
check(not out["ok"] and "host" in out["error"],
      "the host cannot remove themselves and orphan the room")

bot_idx = next(s.index for s in r.seats if s.kind == "ai")
out = r.lobby_remove_player(r.host_control_token, tok["Tim"], bot_idx)
check(not out["ok"] and out["error"] == "that seat is not a player",
      "a bot is removed by resizing the table, not by this")

open_idx = seat_by_name(r, "Cy").index
r.lobby_remove_player(r.host_control_token, tok["Tim"], open_idx)
out = r.lobby_remove_player(r.host_control_token, tok["Tim"], open_idx)
check(not out["ok"] and out["error"] == "that seat is not a player",
      "an already-empty seat has nobody to remove")

for bad in (-1, 99, "two", None):
    out = r.lobby_remove_player(r.host_control_token, tok["Tim"], bad)
    check(not out["ok"], f"seat index {bad!r} is refused")

# Once the game is running the vote is the only way, and this must not become a
# back door around it.
r3, tok3 = seated(humans=3, bots=0)
r3.phase = "running"
out = r3.lobby_remove_player(r3.host_control_token, tok3["Tim"], seat_by_name(r3, "Bo").index)
check(not out["ok"] and "before the game starts" in out["error"],
      "a running game is the vote's business, not the host's")
check(seat_by_name(r3, "Bo") is not None, "…and nobody was removed")


# ══ 4. Adding is a bot; an open seat blocks the start ═════════════════════════
print("an open human seat holds the game up:")

r, tok = seated(humans=3, bots=0)
freed = seat_by_name(r, "Cy")
r.lobby_remove_player(r.host_control_token, tok["Tim"], freed.index)

out = r.start_game(r.host_control_token, tok["Tim"], {})
check(not out["ok"] and out["error"] == "all human seats must be claimed before start",
      "the game will NOT start with an open human seat")
check(not any(s.kind == "ai" for s in r.seats),
      "…and the open seat did not quietly become a bot")

# The way on: the host turns that seat into a bot. Which is exactly what the
# tile's "Make it a bot" button asks for, one fewer human, one more bot.
out = r.configure_lobby_seats(r.host_control_token, tok["Tim"], 2, 1)
check(out["ok"], "the host can turn the open seat into a bot")
check(sum(1 for s in r.seats if s.kind == "ai") == 1, "there is now one bot")
check(sum(1 for s in r.seats if s.kind == "human") == 2, "and two human seats")
check(all(s.token for s in r.seats if s.kind == "human"),
      "both human seats are the two people still here")
# The exact precondition start_game enforces, asserted without launching a
# game thread inside a unit test.
check(r._all_humans_claimed_locked(), "and now every human seat is claimed, so the table can start")

# Adding is always a bot: the host can grow the table to the ceiling with them,
# and never past it.
r4, tok4 = seated(humans=2, bots=0)
for want in range(1, 7):
    out = r4.configure_lobby_seats(r4.host_control_token, tok4["Tim"], 2, want)
    check(out["ok"], f"a bot can be added up to a table of {2 + want}")
out = r4.configure_lobby_seats(r4.host_control_token, tok4["Tim"], 2, 7)
check(not out["ok"], "the ninth seat is refused: a game holds at most 8")

# And a seat somebody is sitting in is never taken away underneath them.
out = r4.configure_lobby_seats(r4.host_control_token, tok4["Tim"], 1, 6)
check(not out["ok"] and "already sitting" in out["error"],
      "shrinking the human spots below the people in them is refused")

# A competitive (ranked) room is people only: the "Make it a bot" way out is
# closed there, and the server is the one that says so.
r5, tok5 = seated(humans=3, bots=0, ranked=True)
out = r5.configure_lobby_seats(r5.host_control_token, tok5["Tim"], 2, 1)
check(not out["ok"] and "people only" in out["error"],
      "a competitive game never takes a bot")


print()
if failures:
    print(f"FAILED: {failures} of {checks} checks")
    sys.exit(1)
print(f"All {checks} checks passed.")
