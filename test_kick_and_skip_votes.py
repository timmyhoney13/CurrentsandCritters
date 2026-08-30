#!/usr/bin/env python3
"""Vote Kick, Skip Turn, and the waiting room's Table Setup.

Run:  python3 test_kick_and_skip_votes.py

Three things used to be impossible or hidden in chat:

  • Removing a player who was ruining the game. There was no way at all: the
    only exit was for everyone else to leave.
  • Passing the turn of a player who had wandered off. You had to know to type
    "P3 is AFK" into chat, which nothing in the UI ever told you.
  • Changing how many people a room holds. The count was fixed when the room
    was created, so playing with one more friend meant closing the room and
    re-inviting everybody.

The rules the buttons enforce are deliberately different from each other, and
that difference is most of what these tests pin:

  Skip Turn  costs one turn, and passes on HALF the other players.
  Vote Kick  is permanent, and passes only when EVERY other person agrees.

The rest is the ways a vote must refuse (yourself, a bot, your own other hand
in competitive, the host in their own lobby), and the two things a kick must
never do: park the table on the empty chair it just made, or leave the door
open for the person who was just voted out.
"""
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))

# multiplayer_server guards its own startup behind __main__, so a plain import
# runs nothing.
spec = importlib.util.spec_from_file_location(
    "mp_server_under_test", os.path.join(ROOT, "multiplayer_server.py"))
mp = importlib.util.module_from_spec(spec)
sys.modules["mp_server_under_test"] = mp
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


def room(humans=3, bots=1, **kw):
    """A room with `humans` human seats, all but seat 0 unclaimed."""
    r = Room(room_id="ABCD", host_name="Tim",
             total_players=humans + bots, human_players=humans,
             ai_players=bots, **kw)
    return r


def seated(humans=3, bots=1, names=("Bo", "Cy", "Di", "Ed", "Fi"), **kw):
    """A room with every human seat claimed. Returns (room, {name: token})."""
    r = room(humans, bots, **kw)
    for i in range(1, humans):
        r.claim_seat(names[i - 1], i, None)
    return r, {s.claimed_name: s.token for s in r.seats if s.token}


# ══ 1. Vote Kick needs EVERY other player ═════════════════════════════════════
print("vote kick, unanimity:")

r, tok = seated(humans=3, bots=1)
r.phase = "running"
out = r.player_kick_vote({"seat_token": tok["Tim"], "target_seat_index": 2})
check(out["ok"] and not out["kicked"], "first vote is accepted")
check((out["votes"], out["needed"]) == (1, 2), "one of the two other players has voted")
check(not r.seats[2].kicked, "one vote short is not a kick")

out = r.player_kick_vote({"seat_token": tok["Tim"], "target_seat_index": 2})
check(out.get("duplicate") and out["votes"] == 1, "voting twice does not count twice")

out = r.player_kick_vote({"seat_token": tok["Bo"], "target_seat_index": 2})
check(out["kicked"], "the last outstanding vote carries it")
check(r.seats[2].kicked, "the seat is flagged kicked")
check(r.seats[2].token is None, "the kicked player's token is cleared")
check(r.seats[2].claimed_name == "Cy", "the seat keeps its name, a bot plays it out")

# Bots are not people and never hold up a vote: the room above was 3 humans and
# a bot, and 2 votes settled it.
r2, tok2 = seated(humans=2, bots=6)
r2.phase = "running"
out = r2.player_kick_vote({"seat_token": tok2["Tim"], "target_seat_index": 1})
check(out["needed"] == 1, "six bots add nothing to the quorum")
check(out["kicked"], "with one other human, everyone else is that one person")

# Taking a vote back.
r3, tok3 = seated(humans=3, bots=0)
r3.phase = "running"
r3.player_kick_vote({"seat_token": tok3["Tim"], "target_seat_index": 2})
out = r3.player_kick_vote({"seat_token": tok3["Tim"], "target_seat_index": 2, "undo": True})
check(out["votes"] == 0, "a vote can be taken back")
check(not r3.seats[2].kicked, "taking the last vote back leaves the player in")


# ══ 2. The votes a kick has to refuse ═════════════════════════════════════════
print("vote kick, refusals:")

r, tok = seated(humans=3, bots=1)
r.phase = "running"
check(not r.player_kick_vote({"seat_token": tok["Tim"], "target_seat_index": 0})["ok"],
      "you cannot vote to kick yourself")
check(not r.player_kick_vote({"seat_token": tok["Tim"], "target_seat_index": 3})["ok"],
      "a bot seat is not a player")
check(not r.player_kick_vote({"seat_token": "nope", "target_seat_index": 1})["ok"],
      "a bad seat token cannot vote")
check(not r.player_kick_vote({"seat_token": tok["Tim"], "target_seat_index": 99})["ok"],
      "an out-of-range seat is rejected")
r.phase = "ended"
check(not r.player_kick_vote({"seat_token": tok["Tim"], "target_seat_index": 1})["ok"],
      "no kicking once the game is over")

# The host runs the lobby: removing them would leave a room nobody can start.
# Mid-game they are just another player.
r, tok = seated(humans=2, bots=2)
check(not r.player_kick_vote({"seat_token": tok["Bo"], "target_seat_index": 0})["ok"],
      "the host cannot be kicked from their own lobby")
r.phase = "running"
check(r.player_kick_vote({"seat_token": tok["Bo"], "target_seat_index": 0})["kicked"],
      "mid-game the host is kickable like anyone else")
check(any(s.is_host for s in r.seats if s.token), "the host badge moves to somebody still here")

# Competitive: four seats, two people. One person is one ballot, and their own
# other hand is not a voter against them.
rc = Room(room_id="EFGH", host_name="Tim", total_players=4, human_players=4,
          ai_players=0, competitive=True)
rc.claim_seat("Tim 2", 1, None)
rc.claim_seat("Bo", 2, None)
rc.claim_seat("Bo 2", 3, None)
ct = {s.index: s.token for s in rc.seats if s.token}
rc.phase = "running"
check(not rc.player_kick_vote({"seat_token": ct[0], "target_seat_index": 1})["ok"],
      "competitive: you cannot kick your own other hand")
out = rc.player_kick_vote({"seat_token": ct[0], "target_seat_index": 2})
check(out["needed"] == 1, "competitive: the opponent's two hands are one person")
check(out["kicked"] and rc.seats[2].kicked and rc.seats[3].kicked,
      "competitive: kicking a player removes BOTH of their hands")


# ══ 3. A kicked player does not come back ═════════════════════════════════════
print("kicked players stay out:")

r, tok = seated(humans=3, bots=1)
r.phase = "running"
r.player_kick_vote({"seat_token": tok["Tim"], "target_seat_index": 2})
r.player_kick_vote({"seat_token": tok["Bo"], "target_seat_index": 2})

check(r.kicked_token_notice(tok["Cy"]) is not None,
      "the removed player's old token gets a reason, not a bare rejection")
check(r.kicked_token_notice("someone-elses-token") is None,
      "an unrelated stale token gets no kicked notice")

# Their seat is gone even after the game ends and the lobby reopens.
r.phase = "lobby"
out = r.claim_seat("Cy", None, None)
check(not out["ok"] and out.get("kicked"), "a kicked name cannot claim a seat again")
check(r.claim_seat("Zed", None, None)["ok"], "somebody else can still take a seat")

# The state payload tells the removed client why.
payload = r.state_view(tok["Cy"], "localhost")
check(payload.get("kicked_notice") is not None, "the state payload carries the kicked notice")
payload_other = r.state_view(tok["Tim"], "localhost")
check(payload_other.get("kicked_notice") is None, "nobody else sees a kicked notice")


# ══ 4. A kicked seat never parks the table ════════════════════════════════════
print("a kicked seat keeps playing:")

r, tok = seated(humans=3, bots=1)
r.phase = "running"
r.player_kick_vote({"seat_token": tok["Tim"], "target_seat_index": 2})
r.player_kick_vote({"seat_token": tok["Bo"], "target_seat_index": 2})

# The policy is bound at launch, so a human seat with nobody behind it waits
# out a 30-minute window every single turn. _wait_for_action has to hand back
# at once instead, so the stand-in bot can move.
# _wait_for_action is only ever reached on the match thread, so it expects the
# per-game scratch state that _launch_game_locked sets up. Stand it in here
# rather than run a whole match to exercise one wake-up.
r._admin_mod_queue = []
cmd = r._wait_for_action(2, timeout_sec=5.0)
check(cmd is not None and cmd.get("kind") == "__kicked__",
      "the parked turn wakes immediately instead of waiting out the window")
cmd_live = r._wait_for_action(1, timeout_sec=0.05)
check(cmd_live is None, "a seat whose player is still here keeps waiting normally")

# The seat is reported as kicked so the pill can say a bot has the chair.
snap = {s["index"]: s for s in r.seat_snapshot_locked()}
check(snap[2]["kicked"] is True, "the seat snapshot reports the kick")
check(snap[1]["kicked"] is False, "other seats are not marked kicked")

# Survives a restart: rebuilt as an ordinary empty human seat, it would park.
saved = r._serialize_checkpoint_locked()
back = Room.from_checkpoint(saved)
check(back is not None and back.seats[2].kicked, "the kick survives a checkpoint round trip")
check("cy" in back.kicked_names, "the closed door survives a checkpoint round trip")


# ══ 5. Skip Turn is the other rule: half, and only on their turn ══════════════
print("skip turn:")

r, tok = seated(humans=4, bots=0)
r.phase = "running"
r.active_action_seat = 2                      # Cy is up

check(not r.skip_turn_vote({"seat_token": tok["Tim"], "target_seat_index": 1})["ok"],
      "you can only skip the turn of whoever is actually playing")
check(not r.skip_turn_vote({"seat_token": tok["Cy"], "target_seat_index": 2})["ok"],
      "you cannot vote to skip your own turn")

out = r.skip_turn_vote({"seat_token": tok["Tim"], "target_seat_index": 2})
check(out["ok"] and (out["votes"], out["needed"]) == (1, 2),
      "three others means two votes, not three: half, not everyone")
check(not out["challenge_started"], "one of two does not start the countdown yet")
out = r.skip_turn_vote({"seat_token": tok["Bo"], "target_seat_index": 2})
check(out["challenge_started"], "half the table starts the 20-second check")
check(r.afk_challenge_seat == 2, "the challenge is aimed at the right seat")

# Surf's Up beats it: the table waits for a player who said they stepped away.
r2, tok2 = seated(humans=3, bots=0)
r2.phase = "running"
r2.active_action_seat = 2
r2.set_away({"seat_token": tok2["Cy"], "away": True})
out = r2.skip_turn_vote({"seat_token": tok2["Tim"], "target_seat_index": 2})
check(not out["ok"], "a player on Surf's Up cannot have their turn skipped")

# A kicked seat is a bot's now; there is no turn of theirs to skip.
r3, tok3 = seated(humans=3, bots=0)
r3.phase = "running"
r3.player_kick_vote({"seat_token": tok3["Tim"], "target_seat_index": 2})
r3.player_kick_vote({"seat_token": tok3["Bo"], "target_seat_index": 2})
r3.active_action_seat = 2
check(not r3.skip_turn_vote({"seat_token": tok3["Tim"], "target_seat_index": 2})["ok"],
      "a removed player's seat cannot be voted on")


# ══ 6. Typing it in chat still works exactly as before ════════════════════════
print("the chat form is unchanged:")

r, tok = seated(humans=3, bots=0)
r.phase = "running"
r.active_action_seat = 2
r.submit_chat({"seat_token": tok["Tim"], "message": "P3 is AFK"})
check(r.afk_votes.get(2) == {0}, "\"P3 is AFK\" still registers a vote")
notes = [m["message"] for m in r.chat_messages if m.get("system")]
check(any("want Cy to draw 2 cards" in n for n in notes),
      "the room is still told the tally in chat")

r2, tok2 = seated(humans=3, bots=0)
r2.phase = "running"
r2.active_action_seat = 2
r2.submit_chat({"seat_token": tok2["Tim"], "message": "P2 is AFK"})
notes = [m["message"] for m in r2.chat_messages if m.get("system")]
check(any("only report the current player" in n for n in notes),
      "the chat form still explains itself when the target is wrong")
check(not r2.afk_votes, "and files no vote")

# An ordinary chat line is still an ordinary chat line.
r3, tok3 = seated(humans=3, bots=0)
r3.phase = "running"
r3.active_action_seat = 2
r3.submit_chat({"seat_token": tok3["Tim"], "message": "nice play"})
check(not r3.afk_votes, "ordinary chat casts no vote")


# ══ 7. What the buttons are told ══════════════════════════════════════════════
print("the vote panel the client renders:")

r, tok = seated(humans=3, bots=1)
r.phase = "running"
r.active_action_seat = 2
with r.cond:
    vp = r._vote_payload_locked(r.seats[0])
kick_seats = sorted(k["seat"] for k in vp["kick"])
check(kick_seats == [1, 2], "a kick option per other player, and none for bots or me")
check(all(k["needed"] == 2 for k in vp["kick"]), "each carries the unanimous bar")
check(vp["skip"] and vp["skip"]["seat"] == 2, "a skip option only for the player who is up")
check(vp["ballot_seat"] == 0, "the viewer's own ballot seat comes back")

r.player_kick_vote({"seat_token": tok["Tim"], "target_seat_index": 1})
with r.cond:
    vp = r._vote_payload_locked(r.seats[0])
    vp_bo = r._vote_payload_locked(r.seats[1])
mine = next(k for k in vp["kick"] if k["seat"] == 1)
check(mine["mine"] and mine["votes"] == 1, "my own vote comes back marked as mine")
theirs = next(k for k in vp_bo["kick"] if k["seat"] == 2)
check(not theirs["mine"], "somebody else's vote is not marked as mine")

# The host row is marked blocked in a lobby, so the button can say why.
r2, tok2 = seated(humans=3, bots=0)
with r2.cond:
    vp2 = r2._vote_payload_locked(r2.seats[1])
host_row = next((k for k in vp2["kick"] if k["seat"] == 0), None)
check(host_row is not None and host_row["blocked"], "the host row says it is blocked in the lobby")

# A player alone with bots has nobody to vote with, and gets no menu at all.
r3 = room(humans=1, bots=3)
r3.phase = "running"
with r3.cond:
    vp3 = r3._vote_payload_locked(r3.seats[0])
check(vp3["kick"] == [] and vp3["skip"] is None, "solo against bots: no votes offered")


# ══ 8. Table Setup: add or subtract players in the waiting room ═══════════════
print("table setup:")

r = room(humans=2, bots=2)
r.claim_seat("Bo", 1, None)
ht = r.host_control_token

out = r.configure_lobby_seats(ht, None, 4, 2)
check(out["ok"] and out["total_players"] == 6, "the host can add players and keep the bots")
check([s.kind for s in r.seats] == ["human"] * 4 + ["ai"] * 2,
      "humans first, bots behind them")
check(all(s.index == i for i, s in enumerate(r.seats)), "seats are renumbered in order")

out = r.configure_lobby_seats(ht, None, None, 0)
check(out["ok"] and out["ai_players"] == 0, "bots can be taken to zero on their own")
check(out["human_players"] == 4, "leaving a count out leaves it alone")

# The two people already sitting keep their seats, tokens and names.
check(r.seats[0].claimed_name == "Tim" and r.seats[0].token,
      "a seated player keeps their seat through a resize")
check(r.seats[1].claimed_name == "Bo" and r.seats[1].token,
      "and so does everyone else already here")
check(any(s.is_host and s.token for s in r.seats), "the room still has a seated host")

check(not r.configure_lobby_seats(ht, None, 1, 1)["ok"],
      "a spot somebody is sitting in cannot be taken away")
check(not r.configure_lobby_seats(ht, None, 8, 4)["ok"], "eight is the ceiling")
check(r.configure_lobby_seats(ht, None, 1, 0)["ok"] is False, "two is the floor")
check(not r.configure_lobby_seats("", r.seats[1].token, 3, 1)["ok"],
      "only the host can change the table")

check(r.configure_lobby_seats(ht, None, 2, 1)["ok"], "shrinking back down works")
check(len(r.seats) == 3, "and the seat list really shrinks")
check(r.configure_lobby_seats(ht, None, 2, 1).get("unchanged"),
      "asking for what is already set changes nothing")

r.phase = "running"
check(not r.configure_lobby_seats(ht, None, 3, 1)["ok"],
      "the table cannot be reshaped mid-game")
r.phase = "lobby"

rc = Room(room_id="EFGH", host_name="Tim", total_players=4, human_players=4,
          ai_players=0, competitive=True)
check(not rc.configure_lobby_seats(rc.host_control_token, None, 3, 1)["ok"],
      "competitive is always two players with two hands each")

# Team Mode: every seat still has a team that exists.
rt = Room(room_id="IJKL", host_name="Tim", total_players=4, human_players=2,
          ai_players=2, team_mode=True, team_count=2)
out = rt.configure_lobby_seats(rt.host_control_token, None, 3, 3)
check(out["ok"], "a team lobby can be resized")
check(all(s.team is not None and 0 <= s.team < rt.team_count for s in rt.seats),
      "every seat comes out of a resize on a real team")

# Quick Play keeps its own fixed chooser, and it still works.
rq = Room(room_id="MNOP", host_name="Tim", total_players=4, human_players=2,
          ai_players=2, quick_play=True)
check(rq.configure_quick_play_seats(rq.host_control_token, None, 3)["ok"],
      "the Quick Play chooser is untouched")


print()
if failures:
    print(f"FAILED {failures} of {checks} checks")
    sys.exit(1)
print(f"All {checks} checks passed.")
