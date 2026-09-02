#!/usr/bin/env python3
"""Who is on a computer and who is on a phone, relayed by the room.

Run:  python3 test_player_device.py

Every screen that lists the people in a room now says what they are playing
ON. The client works its own answer out (client/js/device-select.js reads the
pointer hardware, then lets the first real touch or click correct it) and
pushes it here; the server keeps it on the seat and hands it to everybody with
the seat list, so the waiting room, the in-game seat cluster and the watcher
list all read one value instead of three.

That makes it decoration, on exactly the terms `avatar` and `background` are
already decoration: self-reported, drawn and nothing else. Which is fine, and
is why the interesting cases here are not "is it stored" but:

  1. IT IS ONE OF TWO WORDS. Anything else is not stored at all. It is printed
     next to a player's name on six screens, so it may never be free text.

  2. IT TRAVELS WITH THE SEAT LIST. That list is in every state payload, in the
     lobby AND in a running game, which is what lets one value serve both.

  3. ONE PERSON, ONE DEVICE. A competitive player owns two hands and pushes
     with one token. The same bug that once left their second hand wearing a
     stranger's avatar would leave it on the wrong machine here.

  4. IT IS ITS OWN ENDPOINT. Not a field on /avatar: that push is skipped
     whenever the avatar has not changed, and skipped entirely for a player
     who never picked a critter, so a device riding on it would often never be
     sent at all. And detection can flip AFTER boot (a laptop that guessed
     computer and then got touched), which changes this and nothing else.

  5. A SEAT NOBODY SPOKE FOR SAYS "". Never null, never a guess: a bot has no
     device and an old client reports none, and both must draw as nothing.
"""
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))

spec = importlib.util.spec_from_file_location(
    "mp_server_player_device", os.path.join(ROOT, "multiplayer_server.py"))
mp = importlib.util.module_from_spec(spec)
sys.modules["mp_server_player_device"] = mp
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


def dev(r, index):
    return r.seat_snapshot_locked()[index]["device"]


# ══ 1. Two words, and nothing else ═══════════════════════════════════════════
print("only computer or mobile is ever stored:")

check(mp._clean_device("computer") == "computer", "computer is kept")
check(mp._clean_device("mobile") == "mobile", "mobile is kept")
check(mp._clean_device("  MOBILE  ") == "mobile", "case and padding are normalised away")
check(mp._clean_device("Computer") == "computer", "…both ways")
check(mp._clean_device("mobile\n") == "mobile", "…including a trailing newline")
for junk in ("playstation", "phone", "tablet", "desktop", "", "  ",
             "<script>alert(1)</script>", "mobile computer", "mobile_"):
    check(mp._clean_device(junk) == "", f"{junk!r} is not a device")
for junk in (None, 1, True, [], {}, object()):
    check(mp._clean_device(junk) == "", f"{type(junk).__name__} is not a device")

# It is printed beside a name on six screens. A value that reached one of them
# as free text would be a way to write on somebody else's screen.
r, tok = seated(humans=2, bots=1)
r.set_device({"seat_token": tok["Tim"], "device": "<b>hi</b>"})
check(dev(r, 0) == "", "markup pushed as a device is dropped, not stored")


# ══ 2. It travels with the seat list ═════════════════════════════════════════
print("\nthe seat list carries it, in the lobby and in a game:")

r, tok = seated(humans=2, bots=1)
snap = r.seat_snapshot_locked()
check(all("device" in s for s in snap), "every seat in the list has the field")
check(all(s["device"] == "" for s in snap),
      "a seat that has said nothing reports \"\", never null")
check(isinstance(snap[2]["device"], str) and snap[2]["kind"] == "ai",
      "a bot seat is in the list with an empty device, not a missing one")

out = r.set_device({"seat_token": tok["Tim"], "device": "mobile"})
check(out.get("ok") and out.get("device") == "mobile", "a seat can report its device")
check(dev(r, 0) == "mobile", "…and the seat list says so")
check(dev(r, 1) == "" and dev(r, 2) == "",
      "reporting one seat's device says nothing about anybody else's")

# The whole point of putting it on the seat list: that list is in EVERY state
# payload, so a running game gets it from the same place the lobby does. Pin
# the one line that could quietly stop being true.
src = open(os.path.join(ROOT, "multiplayer_server.py"), encoding="utf-8").read()
check(src.count('"seats": self.seat_snapshot_locked()') >= 2,
      "the seat snapshot is what the state payload sends, in more than one place")
check('"device": str(getattr(seat, "device", "") or "")' in src,
      "the snapshot builds the device off the seat")

# Switching machines mid-lobby is the ordinary case, not an edge one.
r.set_device({"seat_token": tok["Tim"], "device": "computer"})
check(dev(r, 0) == "computer", "a player who moves to another machine is re-reported")
r.set_device({"seat_token": tok["Tim"], "device": "nonsense"})
check(dev(r, 0) == "", "and an unreadable report clears it rather than keeping a stale one")


# ══ 3. One person, one device ════════════════════════════════════════════════
print("\ncompetitive: two hands, one machine:")

r = Room(room_id="COMP", host_name="Tim", total_players=4, human_players=4,
         ai_players=0, competitive=True)
r.claim_seat("Bo", 2, None)
ctok = {s.claimed_name: s.token for s in r.seats if s.token}
r.set_device({"seat_token": ctok["Tim"], "device": "mobile"})
check(dev(r, 0) == "mobile" and dev(r, 1) == "mobile",
      "one push puts the device on BOTH of that player's hands")
check(dev(r, 2) == "" and dev(r, 3) == "",
      "…and on neither of the opponent's")
r.set_device({"seat_token": ctok["Bo"], "device": "computer"})
check(dev(r, 2) == "computer" and dev(r, 3) == "computer",
      "the opponent's pair is set by their own push")
check(dev(r, 0) == "mobile" and dev(r, 1) == "mobile",
      "…without disturbing the first player's")


# ══ 4. Its own endpoint, and who may use it ══════════════════════════════════
print("\nthe endpoint:")

check("def set_device" in src, "the room has a set_device")
check('parts[3] == "device"' in src, "…reachable on /api/rooms/<id>/device")
# Not a field smuggled onto the avatar push: see the file header for why.
avatar_fn = src[src.index("def set_avatar"):src.index("def set_background")]
check("device" not in avatar_fn, "the avatar push does not also carry a device")

r, tok = seated(humans=2, bots=1)
check(r.set_device({"seat_token": "not-a-token", "device": "mobile"}).get("ok") is False,
      "a made-up seat token is refused")
check(r.set_device({"device": "mobile"}).get("ok") is False,
      "so is no token at all")
check(r.set_device({"seat_token": None, "device": "mobile"}).get("ok") is False,
      "so is a null one")
check(all(s["device"] == "" for s in r.seat_snapshot_locked()),
      "and none of those refusals wrote anything")

# A seat token only ever reaches its own seat: this is the one thing that would
# let a player write on somebody else's name plate.
r.set_device({"seat_token": tok["Bo"], "device": "mobile"})
check(dev(r, 1) == "mobile" and dev(r, 0) == "",
      "a player's token moves their own seat and no other")

# Repeating the same push must not churn the room version, or every poll from
# every client would look like a change to every other client.
r.set_device({"seat_token": tok["Bo"], "device": "mobile"})
v = r.state_version
r.set_device({"seat_token": tok["Bo"], "device": "mobile"})
check(r.state_version == v, "re-reporting the same device does not bump the room")
r.set_device({"seat_token": tok["Bo"], "device": "computer"})
check(r.state_version > v, "…but a real change does, so it reaches every client")


# ══ 5. Watchers report one too ═══════════════════════════════════════════════
print("\nthe watcher list:")

r, tok = seated(humans=2, bots=1)
j = r.spectator_join("Nell")
stok = j["spectator_token"]
row = r.spectator_list()[0]
check("device" in row and row["device"] == "",
      "a watcher who has said nothing is listed with an empty device")
r.set_spectator_look(stok, device="mobile")
check(r.spectator_list()[0]["device"] == "mobile", "a watcher can report one")
check(r.spectator_list()[0]["avatar"] == "" and r.spectator_list()[0]["background"] == "",
      "…without touching the icon or background they never sent")
r.set_spectator_look(stok, avatar="/avatars/clownfish.png")
check(r.spectator_list()[0]["device"] == "mobile",
      "and an icon push later leaves the device where it was")
r.set_spectator_look(stok, device="hovercraft")
check(r.spectator_list()[0]["device"] == "", "a watcher gets the same two words, or none")
check(r.set_spectator_look("not-a-token", device="mobile").get("ok") is False,
      "a made-up watcher token is refused")


# ══ 6. It is decoration, so it decides nothing ═══════════════════════════════
print("\nit changes nothing about the game:")

r, tok = seated(humans=2, bots=1)
before = [(s.index, s.kind, s.claimed_name, s.token, s.is_host, s.team) for s in r.seats]
r.set_device({"seat_token": tok["Tim"], "device": "mobile"})
r.set_device({"seat_token": tok["Bo"], "device": "computer"})
after = [(s.index, s.kind, s.claimed_name, s.token, s.is_host, s.team) for s in r.seats]
check(before == after, "reporting a device moves nothing about who is sitting where")
check(r.phase == "lobby", "…and does not start, end or change the phase")

# A device is never a reason to treat somebody differently. If any rule ever
# reads one, it stops being decoration and starts being a thing to lie about.
for fn in ("start_game", "claim_seat", "_expire_left_seats_locked",
           "leave_room", "play_again"):
    i = src.index("def " + fn)
    j = src.index("\n    def ", i + 10)
    check("device" not in src[i:j], f"{fn} does not read a device")


print()
if failures:
    print(f"{failures} of {checks} checks FAILED")
    sys.exit(1)
print(f"All {checks} checks passed.")
