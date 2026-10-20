#!/usr/bin/env python3
"""Surf's Up!! cannot be used to flood the room, and chat survives it.

Run:  python3 test_surf_spam.py

THE BUG THIS EXISTS FOR
A player held down Surf's Up!! and the table's chat stopped working for
everybody in the room, for the rest of the game.

Two separate faults lined up:

  1. set_away announced "<name> is on Surf's Up, Away" / "<name> is back."
     into chat on EVERY call, including calls that changed nothing. The sibling
     set_inactive_eligible immediately below it already short-circuits an
     unchanged value; set_away never did. So one player mashing one button
     appended one System line per press, as fast as the network allowed.

  2. chat_messages is capped (the live payload sends the last 80). Roughly
     eighty presses is therefore enough to push every real message out of the
     window other players can see, and the client's repaint guard froze at the
     cap on top of that (see test_surf_spam.js for that half).

So the conversation was buried, and then frozen.

THE RULES PINNED HERE
  • Toggling to the value you are already on is a no-op: no chat line, no
    status note, no version bump. It still answers ok with the real state.
  • Going Away is allowed at most once every SURF_TOGGLE_MIN_INTERVAL
    seconds per person. Faster than that is refused with a message the player
    can act on, and the refusal does NOT write to chat either.
  • Coming BACK is never refused. An Away player cannot make a move and the
    turn loop waits on them, so throttling "I'm Back" would strand the player
    and stall the table. Throttling one direction is enough: a toggle loop has
    to pass through it.
  • A player's own Surf's Up line is rewritten in place while it is still
    the newest line in the room, so stepping out and back is one entry in the
    log rather than a running commentary. Only the newest line is ever edited,
    so nothing sitting above other people's messages changes after the fact.
  • Immunity and the Away flag itself are untouched by the throttle: a refused
    press must not quietly clear protection the player already earned.
  • Therefore N presses in a burst cost the room at most 1 chat line, and the
    conversation that was there before it is still there after.
"""
import importlib.util
import os
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))

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
        print("  ✓ " + label)
        return
    failures += 1
    print("  ✗ FAIL: " + label)


def seated(humans=3, bots=1):
    """A room with every human seat claimed. Returns (room, {name: token})."""
    r = Room(room_id="SURF", host_name="Tim",
             total_players=humans + bots, human_players=humans,
             ai_players=bots)
    for i, nm in zip(range(1, humans), ("Bo", "Cy", "Di", "Ed")):
        r.claim_seat(nm, i, None)
    return r, {s.claimed_name: s.token for s in r.seats if s.token}


def system_lines(r):
    return [m for m in r.chat_messages if m.get("sender") == "System"]


def said(r):
    """Just the human conversation, in order."""
    return [m.get("message") for m in r.chat_messages if m.get("sender") != "System"]


print("1. An unchanged toggle is a no-op, not an announcement")
{}
r, toks = seated()
tok = toks["Bo"]
r.set_away({"seat_token": tok, "away": True})
base_lines = len(r.chat_messages)
base_ver = r.state_version

# Already Away. Saying "go Away" again changes nothing about the world.
res = r.set_away({"seat_token": tok, "away": True})
check(res.get("ok") is True, "an unchanged toggle still answers ok")
check(res.get("is_away") is True, "...and reports the real state back")
check(len(r.chat_messages) == base_lines,
      "an unchanged toggle writes no chat line")
check(r.state_version == base_ver,
      "an unchanged toggle does not bump the version (no needless push)")

print("2. A burst on the button costs the room at most one chat line")
r, toks = seated()
tok = toks["Bo"]
for _ in range(120):
    r.set_away({"seat_token": tok})          # no explicit value: a real toggle
lines = len(system_lines(r))
check(lines <= 1,
      "120 presses in a burst leave at most 1 System line (got %d)" % lines)

print("3. The conversation that was there before a burst is still there after")
r, toks = seated()
for i in range(1, 6):
    r.submit_chat({"seat_token": toks["Cy"], "message": "hello %d" % i})
before = said(r)
for _ in range(200):
    r.set_away({"seat_token": toks["Bo"]})
after = said(r)
check(after == before,
      "200 presses did not push one word of the conversation out of the buffer")
check(len(r.chat_messages) < 80,
      "the room is nowhere near the 80-message window the client is sent "
      "(got %d)" % len(r.chat_messages))

print("4. The throttle refuses, and says something the player can act on")
r, toks = seated()
tok = toks["Bo"]
first = r.set_away({"seat_token": tok, "away": True})
back = r.set_away({"seat_token": tok, "away": False})
again = r.set_away({"seat_token": tok, "away": True})
check(first.get("ok") is True, "the first press goes through")
check(back.get("ok") is True,
      "coming back is never refused, however fast: Away cannot play at all")
check(again.get("ok") is False, "going Away again right behind it is refused")
msg = str(again.get("error", ""))
check(bool(msg), "the refusal carries a message")
check("—" not in msg and "–" not in msg,
      "the refusal has no em dash or en dash in it (player-facing string)")
check("surf" in msg.lower(),
      "the refusal names the button, so the player knows what was refused")

print("5. A refused press changes nothing it should not")
r, toks = seated()
tok = toks["Bo"]
r.set_away({"seat_token": tok, "away": True})
r.set_away({"seat_token": tok, "away": False})        # allowed: coming back
seat = next(s for s in r.seats if s.token == tok)
immune_at = r.afk_immune_until.get(seat.index)
lines_at = len(r.chat_messages)
ver_at = r.state_version
r.set_away({"seat_token": tok, "away": True})         # refused: too soon
check(seat.is_away is False, "a refused press does not flip the Away flag")
check(r.afk_immune_until.get(seat.index) == immune_at,
      "a refused press does not shorten the AFK immunity already earned")
check(len(r.chat_messages) == lines_at, "a refused press writes no chat line")
check(r.state_version == ver_at, "a refused press does not bump the version")

print("6. The throttle is per person, not per room")
r, toks = seated()
a = r.set_away({"seat_token": toks["Bo"]})
b = r.set_away({"seat_token": toks["Cy"]})
check(a.get("ok") is True and b.get("ok") is True,
      "two different people may step away at the same moment")

print("7. Once the interval passes, the button works again")
r, toks = seated()
tok = toks["Bo"]
r.set_away({"seat_token": tok, "away": True})
r.set_away({"seat_token": tok, "away": False})
seat = next(s for s in r.seats if s.token == tok)
# Wind the clock back rather than sleeping for real.
r.last_away_toggle[seat.index] -= (mp.GameRoom.SURF_TOGGLE_MIN_INTERVAL + 1)
res = r.set_away({"seat_token": tok, "away": True})
check(res.get("ok") is True, "after the interval they may step away again")
check(res.get("is_away") is True, "...and really are Away")
check(r.chat_messages[-1]["message"].endswith("is on Surf's Up, Away"),
      "the room is told their true current state")

print("7b. The log keeps one line per player, and never edits an older one")
r, toks = seated()
r.set_away({"seat_token": toks["Bo"], "away": True})
r.submit_chat({"seat_token": toks["Cy"], "message": "where did Bo go"})
r.set_away({"seat_token": toks["Bo"], "away": False})
msgs = [m.get("message") for m in r.chat_messages]
check(msgs == ["Bo is on Surf's Up, Away", "where did Bo go", "Bo is back."],
      "once someone else has spoken, the old line stands and a new one is "
      "added (got %r)" % (msgs,))

print("8. Competitive: one person, two hands, still one throttle")
r = Room(room_id="SURF", host_name="Tim", total_players=4, human_players=4,
         ai_players=0, competitive=True)
for i, nm in zip(range(1, 4), ("Bo", "Cy", "Di")):
    r.claim_seat(nm, i, None)
tok = next(s.token for s in r.seats if s.claimed_name == "Bo")
r.set_away({"seat_token": tok})
n = len(system_lines(r))
for _ in range(50):
    r.set_away({"seat_token": tok})
check(len(system_lines(r)) == n,
      "mashing the button in competitive adds nothing after the first press")
seat0 = next(s for s in r.seats if s.claimed_name == "Bo")
check(all(s.is_away == seat0.is_away for s in r._owned_seats_locked(seat0)),
      "both of the player's hands still agree about whether they are here")

print()
if failures:
    print("%d of %d checks FAILED" % (failures, checks))
    sys.exit(1)
print("all %d checks passed" % checks)
