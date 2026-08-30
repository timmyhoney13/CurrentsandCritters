#!/usr/bin/env python3
"""Every client in a room hears the theme song from the same clock.

Run:  python3 test_music_sync.py

The browser used to start the track at the top the moment it walked into a
room, so four people in one game were four different distances into the same
song, and it began at whatever second each of them happened to arrive. Nothing
in the payload told them otherwise.

The server half is two fields on every state payload:

    room.music_epoch_ms   the moment the room's loop is measured from
    server_now_ms         this reply's send time

Between them a browser can work out (server now - epoch) modulo the track
length without trusting its own clock, and every browser in the room gets the
same number. These tests pin that both views carry them, that the epoch is the
same for everyone and steady while the room is, and that it is the kickoff
once there is one so the theme opens the match together.

The client half is test_music_sync.js.
"""
import importlib.util
import os
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))

# multiplayer_server guards its own startup behind __main__, so a plain import
# runs nothing.
spec = importlib.util.spec_from_file_location(
    "mp_server_music_test", os.path.join(ROOT, "multiplayer_server.py"))
mp = importlib.util.module_from_spec(spec)
sys.modules["mp_server_music_test"] = mp
spec.loader.exec_module(mp)

HOST = "127.0.0.1:8777"

failures = 0
checks = 0


def check(cond, label):
    global failures, checks
    checks += 1
    if cond:
        return
    failures += 1
    print("  ✗ " + label)


def eq(actual, expected, label):
    check(actual == expected,
          "%s  (got %r, want %r)" % (label, actual, expected))


def room():
    return mp.GameRoom("MUSIC1", "Otter", total_players=2, human_players=2,
                       ai_players=0)


# ── Both views carry the timeline ───────────────────────────────────────────
print("state payloads carry the shared music clock")
r = room()
seated = r.state_view(None, HOST)
spectator = r.spectator_state_view(HOST)

check("music_epoch_ms" in seated["room"], "seated payload carries room.music_epoch_ms")
check("server_now_ms" in seated, "seated payload carries server_now_ms")
check("music_epoch_ms" in spectator["room"], "spectator payload carries room.music_epoch_ms")
check("server_now_ms" in spectator, "spectator payload carries server_now_ms")

# A spectator is in the same room, listening to the same song.
eq(spectator["room"]["music_epoch_ms"], seated["room"]["music_epoch_ms"],
   "a spectator is on the same timeline as the table")

# Milliseconds, not seconds: the client divides by 1000 to get seconds into
# the track, and a seconds-valued epoch would put it 50 years off.
eq(seated["room"]["music_epoch_ms"], r.created_unix * 1000,
   "epoch is the room's creation, in milliseconds")
check(abs(seated["server_now_ms"] / 1000.0 - time.time()) < 5,
      "server_now_ms is the wall clock in milliseconds")


# ── One number for everybody, steady while the room is ──────────────────────
print("the epoch is the same for every viewer and does not wander")
r = room()
host_token = r.seats[0].token if r.seats and r.seats[0].token else None
epochs = {
    r.state_view(None, HOST)["room"]["music_epoch_ms"],
    r.state_view(host_token, HOST)["room"]["music_epoch_ms"],
    r.state_view(None, HOST, "", r.host_control_token)["room"]["music_epoch_ms"],
    r.spectator_state_view(HOST)["room"]["music_epoch_ms"],
}
eq(len(epochs), 1, "every viewer of one room is handed one epoch")

# Two payloads a moment apart: the epoch holds still (it is what the loop is
# measured FROM), while server_now_ms moves (it is the measurement).
first = r.state_view(None, HOST)
time.sleep(0.02)
second = r.state_view(None, HOST)
eq(second["room"]["music_epoch_ms"], first["room"]["music_epoch_ms"],
   "the epoch does not move between polls")
check(second["server_now_ms"] > first["server_now_ms"],
      "server_now_ms is read per request, never cached")


# ── Kickoff owns the epoch once there is one ────────────────────────────────
print("the theme opens the match, together")
r = room()
lobby_epoch = r.state_view(None, HOST)["room"]["music_epoch_ms"]
eq(lobby_epoch, r.created_unix * 1000,
   "before the game starts, the lobby shares room creation")

r.started_unix = r.created_unix + 137
eq(r.state_view(None, HOST)["room"]["music_epoch_ms"], r.started_unix * 1000,
   "once the match starts, the track starts with it")
eq(r.spectator_state_view(HOST)["room"]["music_epoch_ms"], r.started_unix * 1000,
   "spectators move to kickoff too")

# Play Again re-launches the same room and sets started_unix again, so the
# theme opens the next game the same way instead of running on mid-verse.
r.started_unix = r.created_unix + 900
eq(r.state_view(None, HOST)["room"]["music_epoch_ms"], r.started_unix * 1000,
   "a re-launch moves the epoch to the new kickoff")

# Two different rooms are two different songs-in-progress, never assumed equal.
a = mp.GameRoom("MUSICA", "Otter", total_players=2, human_players=2, ai_players=0)
b = mp.GameRoom("MUSICB", "Otter", total_players=2, human_players=2, ai_players=0)
a.started_unix = 1_700_000_000
b.started_unix = 1_700_000_042
check(a.state_view(None, HOST)["room"]["music_epoch_ms"]
      != b.state_view(None, HOST)["room"]["music_epoch_ms"],
      "each room keeps its own epoch")


print()
if failures:
    print("FAILED  %d of %d checks" % (failures, checks))
    sys.exit(1)
print("PASSED  %d checks" % checks)
