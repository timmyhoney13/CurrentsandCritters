#!/usr/bin/env python3
"""A watcher sees the hand and the cursor, the server half.

Run:  python3 test_spectator_hands.py

Spectating used to hand back boards only: every player's hand was blanked on
the way out (spectator_state_view), which is the half of the game that has
already happened. What a player is deciding between is in their hand, so
watching without it is reading the record of a game rather than watching one.

Two things are new and both are tested here:

  • spectator_state_view fills every seat's hand from latest_private_hands,
    bots included. Nothing else about who may watch changed, so the tests at
    the bottom re-check that a room can still refuse and still vote out.

  • A cursor channel of its own (set_seat_pointer / pointer_snapshot and the
    two routes that reach them). Its contract is unusual enough to be worth
    pinning in full: positions are fractions, not pixels; a stale entry is
    forgotten rather than drawn; writing one never bumps state_version,
    because a cursor moves many times a second and a bump re-renders the
    whole table on every device in the room; and ONLY a spectator may read
    them, because knowing which card an opponent is dithering over is not
    something a table gives you.
"""
import importlib.util
import os
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))

# multiplayer_server guards its own startup behind __main__, so a plain import
# runs nothing.
spec = importlib.util.spec_from_file_location(
    "mp_server_under_test", os.path.join(ROOT, "multiplayer_server.py"))
mp = importlib.util.module_from_spec(spec)
sys.modules["mp_server_under_test"] = mp
spec.loader.exec_module(mp)

SRC = open(os.path.join(ROOT, "multiplayer_server.py"), encoding="utf-8").read()

failures = 0
checks = 0


def check(cond, label, extra=""):
    global failures, checks
    checks += 1
    if cond:
        return
    failures += 1
    print("  ✗ " + label + (("  [" + str(extra) + "]") if extra else ""))


def fresh_room(total=4, humans=2, bots=2):
    room = mp.GameRoom(room_id="WATCH", host_name="Tim", total_players=total,
                       human_players=humans, ai_players=bots)
    room.phase = "running"
    return room


def card(uid, name):
    return {"entry_uid": uid, "label": f"{uid}:{name}",
            "faces": [{"uid": uid, "name": name, "species": "Fish",
                       "cost": 1, "text": "", "symbol": "o"}]}


# ── 1. A watcher gets the hands ─────────────────────────────────────────────
print("\n1. Spectator state carries every hand, not an empty list")
room = fresh_room()
room.latest_public_state = {
    "players": [
        {"index": 0, "name": "Tim", "board": [], "hand": [], "hand_count": 2},
        {"index": 1, "name": "Ada", "board": [], "hand": [], "hand_count": 1},
        {"index": 2, "name": "Nansen", "board": [], "hand": [], "hand_count": 1},
    ],
}
room.latest_private_hands = {
    0: [card(1, "Clownfish"), card(3, "Osprey")],
    1: [card(5, "Barracuda")],
    2: [card(7, "King Salmon")],
}
view = room.spectator_state_view("127.0.0.1:8777")
players = {p["index"]: p for p in (view.get("state") or {}).get("players", [])}
check(len(players) == 3, "all three seats come back", len(players))
check([e["entry_uid"] for e in players[0]["hand"]] == [1, 3],
      "the host's hand arrives in order", players[0]["hand"])
check(players[1]["hand"] and players[1]["hand"][0]["faces"][0]["name"] == "Barracuda",
      "a card arrives whole, with the face a client needs to draw it")
check([e["entry_uid"] for e in players[2]["hand"]] == [7],
      "a bot's hand comes too: a watcher holds no cards and has nothing to win")
check(view.get("hands_visible") is True,
      "the payload says so out loud, so a client never has to guess")
check(view.get("spectator") is True, "and it is still a spectator payload")
check(view.get("viewer", {}).get("can_act") is False,
      "a watcher still cannot act")
check(view.get("legal_actions") is None, "and is offered no actions to take")

# A seat with no hand recorded is an empty list, never a missing key: the
# client iterates it.
room.latest_private_hands = {}
view = room.spectator_state_view("127.0.0.1:8777")
check(all(p["hand"] == [] for p in (view.get("state") or {}).get("players", [])),
      "no hands recorded yet is an empty hand each, not a missing key")

# Filling the hands must not have reached back into the stored snapshot: the
# seated view reads the same object on every poll.
room.latest_private_hands = {0: [card(1, "Clownfish")]}
room.spectator_state_view("127.0.0.1:8777")
check(room.latest_public_state["players"][0]["hand"] == [],
      "the published snapshot is left exactly as it was found",
      room.latest_public_state["players"][0]["hand"])
check(len(room.latest_private_hands[0]) == 1,
      "and so are the private hands")

# ── 2. Writing a pointer ────────────────────────────────────────────────────
print("2. A seat's pointer: what is stored, and what is refused")
room = fresh_room()
tok = room.seats[0].token
before = room.state_version
out = room.set_seat_pointer({"seat_token": tok, "zone": "hand",
                             "nx": 0.42, "ny": 0.75, "hover_uid": 17})
check(out.get("ok") is True, "a seated player's push is accepted", out)
check(room.seat_pointers[0]["zone"] == "hand", "the zone is stored")
check(room.seat_pointers[0]["nx"] == 0.42 and room.seat_pointers[0]["ny"] == 0.75,
      "so is the position, as the fraction it was sent as")
check(room.seat_pointers[0]["hover_uid"] == 17, "so is the hovered card")
check(room.state_version == before,
      "and the room's state version did NOT move: a cursor is not a game event",
      room.state_version)
check(out.get("watchers") == 0,
      "the reply counts the watchers, which is how a client stops sending",
      out.get("watchers"))

room.spectator_join("Reader")
out = room.set_seat_pointer({"seat_token": tok, "zone": "board", "nx": 0.1, "ny": 0.2})
check(out.get("watchers") == 1, "and it climbs when somebody shows up", out)

check(room.set_seat_pointer({"seat_token": "nope", "zone": "hand"}).get("ok") is False,
      "an unknown seat token is refused")
check(room.set_seat_pointer({"zone": "hand"}).get("ok") is False,
      "so is no token at all")
ai_seat = next((s for s in room.seats if s.kind == "ai"), None)
check(ai_seat is not None, "the test room really has a bot seat in it")
check(room.set_seat_pointer({"seat_token": getattr(ai_seat, "token", None) or "",
                             "zone": "hand"}).get("ok") is False,
      "a bot seat has no cursor to report")

print("3. A tampered push cannot store anything strange")
room = fresh_room()
tok = room.seats[0].token
for bad in ["hands", "HAND", "deck", "", None, 7, "../../etc"]:
    room.set_seat_pointer({"seat_token": tok, "zone": "hand", "nx": .5, "ny": .5})
    room.set_seat_pointer({"seat_token": tok, "zone": bad, "nx": .5, "ny": .5})
    check(0 not in room.seat_pointers,
          f"an unknown zone clears the cursor rather than storing it: {bad!r}")
for raw, want in [(-3, 0.0), (99, 1.0), ("0.5", 0.5), (None, 0.0),
                  ("nonsense", 0.0), (float("nan"), 0.0), (float("inf"), 0.0)]:
    room.set_seat_pointer({"seat_token": tok, "zone": "hand", "nx": raw, "ny": raw})
    got = room.seat_pointers[0]["nx"]
    check(got == want, f"nx {raw!r} lands at {want}", got)
#
# json.loads accepts the literals NaN, Infinity and -Infinity, so a
# hand-written body really can carry one, and int(float("nan")) raises. Every
# number in here goes through _finite for exactly that reason.
for bad in ["17; DROP TABLE", True, None, [17], {"uid": 17},
            float("nan"), float("inf"), float("-inf")]:
    room.set_seat_pointer({"seat_token": tok, "zone": "hand", "nx": .5, "ny": .5,
                           "hover_uid": bad})
    check(room.seat_pointers[0]["hover_uid"] == 0,
          f"a hovered card that is not a card uid is none: {bad!r}",
          room.seat_pointers[0]["hover_uid"])
room.set_seat_pointer({"seat_token": tok, "zone": "hand", "nx": .5, "ny": .5,
                       "hover_uid": 17.0})
check(room.seat_pointers[0]["hover_uid"] == 17,
      "and a real uid still arrives, whole", room.seat_pointers[0]["hover_uid"])
check(mp._finite(float("nan")) is None and mp._finite(float("inf")) is None,
      "the shared number guard refuses NaN and infinity outright")
check(mp._finite("0.5") == 0.5 and mp._finite(3) == 3.0,
      "and lets a real number through")
check(mp._finite(True) is None, "a bool is not a number here")

print("4. One person, the right seat")
# Competitive gives one human two seats and the client says which hand is on
# screen. It may only ever pick a seat its own token owns.
room = fresh_room()
tok = room.seats[0].token
room.set_seat_pointer({"seat_token": tok, "zone": "hand", "nx": .3, "ny": .3,
                       "seat_index": 1})
check(1 not in room.seat_pointers,
      "a seat this token does not own cannot be written to")
check(room.seat_pointers.get(0, {}).get("zone") == "hand",
      "the push lands on the token's own seat instead", room.seat_pointers)
room.set_seat_pointer({"seat_token": tok, "zone": "hand", "nx": .3, "ny": .3,
                       "seat_index": "0"})
check(0 in room.seat_pointers and 1 not in room.seat_pointers,
      "a seat_index that is not an int is ignored, not crashed on")

print("5. Reading pointers back")
room = fresh_room()
tok = room.seats[0].token
check(room.pointer_snapshot() == [], "nobody pointing is an empty list")
room.set_seat_pointer({"seat_token": tok, "zone": "hand", "nx": .25, "ny": .5,
                       "hover_uid": 9})
snap = room.pointer_snapshot()
check(len(snap) == 1, "one cursor comes back", snap)
row = snap[0]
check(row["index"] == 0 and row["zone"] == "hand", "with the seat and the zone")
check(row["nx"] == .25 and row["ny"] == .5, "and the fractions as sent")
check(row["hover_uid"] == 9, "and the card being hovered")
check(isinstance(row["age_ms"], int) and row["age_ms"] >= 0,
      "and how old it is, so a client can fade it", row.get("age_ms"))

# Leaving the table says so, rather than freezing the cursor where it was.
room.set_seat_pointer({"seat_token": tok, "zone": "", "nx": 0, "ny": 0})
check(room.pointer_snapshot() == [],
      "an empty zone means the cursor left the table, and it goes at once")

# A browser that is closed or asleep stops sending; a cursor frozen where it
# was five seconds ago is a lie, so it is forgotten rather than drawn.
room.set_seat_pointer({"seat_token": tok, "zone": "hand", "nx": .9, "ny": .9})
room.seat_pointers[0]["ts"] = time.time() - (mp.POINTER_STALE_SEC + 1)
check(room.pointer_snapshot() == [], "a stale cursor is not reported")
check(0 not in room.seat_pointers, "and it is dropped, not re-read every poll")
check(mp.POINTER_STALE_SEC > 0, "the staleness window is a real number")

print("6. A new game starts with nobody pointing")
room = fresh_room()
room.set_seat_pointer({"seat_token": room.seats[0].token, "zone": "hand",
                       "nx": .5, "ny": .5})
check(0 in room.seat_pointers, "a cursor is on file")
room._reset_tracking(None, None)
check(room.seat_pointers == {},
      "and a fresh game clears it, like the hands beside it", room.seat_pointers)

print("7. Only a watcher may read a cursor")
# Source-level, because the rule lives in the route rather than the method:
# the method is the store and the route is the door.
route = SRC.split('parts[3] == "pointers"', 1)
check(len(route) == 2, "the /pointers route exists")
body = route[1].split("return", 2)[0] + route[1].split("return", 2)[1]
check("spectator_token" in body, "it asks for a spectator token")
check("room.spectators" in body, "and checks it against the room's watchers")
check("HTTPStatus.FORBIDDEN" in body, "and refuses anything else")
check("seat_token" not in body,
      "a seat token is not a way in: a player at the table cannot read these")
check('parts[3] == "pointer"' in SRC, "and the write route exists too")
check("room.set_seat_pointer(body)" in SRC,
      "which goes through the validating method, not into the dict directly")
# The write must not be reachable without a seat, or a watcher could puppet a
# cursor onto somebody else's hand.
check('"spectator_token"' not in SRC.split('parts[3] == "pointer"', 1)[1].split("return", 3)[0],
      "and the write route takes no spectator token")

print("8. None of this changed who may watch")
shut = mp.GameRoom(room_id="SHUT", host_name="Tim", total_players=2,
                   human_players=2, ai_players=0, visibility="private")
shut.phase = "running"
check(shut.allow_spectators is False, "a private room still starts closed")
check(shut.spectator_join("Nosy").get("ok") is False,
      "and still refuses a watcher")
done = fresh_room()
done.phase = "ended"
check(done.spectator_join("Late").get("ok") is False,
      "a finished game still refuses one")
live = fresh_room()
joined = live.spectator_join("Reader")
check(joined.get("ok") is True, "a public running room still lets one in")
check(live.spectator_leave(joined["spectator_token"]).get("ok") is True,
      "and still lets them go")

print(f"\nspectator hand + cursor checks: {checks}")
if failures:
    print(f"{failures} FAILED")
    sys.exit(1)
print("spectator hands OK")
