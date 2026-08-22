#!/usr/bin/env python3
"""A spectator wears the icon they have equipped, the server half.

Run:  python3 test_spectator_avatar.py

A seated player's icon travels on their SEAT (Seat.avatar, pushed with
/api/rooms/<id>/avatar and stamped onto every chat line they send). A
spectator has no seat, so none of that applied to them: they showed up in the
spectator list as a bare name, and their chat lines arrived with no avatar at
all, which made every client fall back to a hash of the sender string. That
string is "[Spectator] Tim", not "Tim", so the fallback wasn't even the same
default face the person wears everywhere else in the app.

The fix stores the equipped icon + background on the spectator record. These
tests pin the three places it has to come back out (join → list → chat), the
mid-session re-equip, and the validation that keeps a tampered client from
pointing anyone's <img> somewhere else.
"""
import importlib.util
import os
import sys
import threading

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


class FakeSeat:
    kind = "human"
    claimed_name = "Tim"
    index = 0


class Stub:
    """Just the room state the spectator methods touch, so this stays a unit
    test of that path rather than a whole-game run."""

    def __init__(self, allow=True, phase="running"):
        self.cond = threading.Condition()
        self.allow_spectators = allow
        self.phase = phase
        self.spectators = {}
        self._spectator_kick_votes = {}
        self.seats = [FakeSeat()]
        self.chat_messages = []
        self.bumped = 0

    def _add_system_chat(self, text):
        self.chat_messages.append({"sender": "System", "message": text, "system": True})

    def _bump_locked(self, force_persist=False):
        self.bumped += 1


def join(stub, name="Tim", avatar="/avatars/clownfish.png", background=""):
    return Room.spectator_join(stub, name, avatar, background)


print("\n1. Only our own asset paths are ever stored")
for bad in ["../../etc/passwd", "https://evil.example/x.png", "<img src=x onerror=1>",
            "/avatars/../secret.png", "avatars/mullet.png", "/avatars/mullet.jpg",
            "/backgrounds/reef.png", "", "   ", None, 7, ["/avatars/mullet.png"]]:
    check(mp._clean_avatar_path(bad) == "", f"avatar refused: {bad!r}")
check(mp._clean_avatar_path("/avatars/blue-tang.png") == "/avatars/blue-tang.png",
      "a real avatar path survives intact")
check(mp._clean_avatar_path("  /avatars/mullet.png  ") == "/avatars/mullet.png",
      "surrounding space is trimmed")
check(mp._clean_background_path("/backgrounds/bg-coral-reef.png") == "/backgrounds/bg-coral-reef.png",
      "a real background path survives intact")
check(mp._clean_background_path("/avatars/mullet.png") == "",
      "an avatar is not a background")
check(mp._clean_background_path("javascript:alert(1)") == "", "a script URL is refused")

# The seat path must use the exact same rule, one definition, not two that
# can drift apart.
src = open(os.path.join(ROOT, "multiplayer_server.py"), encoding="utf-8").read()
check('avatar = _clean_avatar_path(body.get("avatar"))' in src,
      "set_avatar validates through the shared helper")
check('background = _clean_background_path(body.get("background"))' in src,
      "set_background validates through the shared helper")

print("2. Joining carries the equipped look")
s = Stub()
res = join(s)
check(res.get("ok") is True, f"the join is accepted, got {res}")
tok = res.get("spectator_token")
check(bool(tok), "a spectator token comes back")
rec = s.spectators.get(tok, {})
check(rec.get("avatar") == "/avatars/clownfish.png", f"the icon is stored, got {rec.get('avatar')}")
check(rec.get("name") == "Tim", "so is the name")
check(rec.get("background") == "", "no background equipped stays empty, not None")

s2 = Stub()
tok2 = join(s2, "Reader", "/avatars/osprey.png", "/backgrounds/bg-coral-reef.png")["spectator_token"]
check(s2.spectators[tok2].get("background") == "/backgrounds/bg-coral-reef.png",
      "an equipped background rides along too")

s3 = Stub()
tok3 = join(s3, "Old Client", avatar="", background="")["spectator_token"]
check(s3.spectators[tok3].get("avatar") == "",
      "a client that sends no icon still joins (the client falls back to the name default)")

s4 = Stub()
tok4 = join(s4, "Sneak", "https://evil.example/track.gif")["spectator_token"]
check(s4.spectators[tok4].get("avatar") == "",
      "a bogus icon is dropped at the door, never relayed to other clients")

print("3. The spectator list hands the icon to every client")
lst = Room.spectator_list(s)
check(len(lst) == 1, f"one spectator listed, got {len(lst)}")
row = lst[0] if lst else {}
check(row.get("avatar") == "/avatars/clownfish.png",
      f"the list row carries the icon, got {row.get('avatar')}")
check("background" in row, "…and the background key is always present")
check(row.get("name") == "Tim", "the name is still there")
check("token_tail" in row and tok not in str(row),
      "the full token is still never published")
check(Room.spectator_list(s3)[0].get("avatar") == "",
      "a spectator with no stored icon reports \"\", not a missing key")

print("4. Chat lines wear the same face")
chat = Room.submit_spectator_chat(s, tok, "nice pull")
check(chat.get("ok") is True, f"the message is accepted, got {chat}")
entry = s.chat_messages[-1] if s.chat_messages else {}
check(entry.get("avatar") == "/avatars/clownfish.png",
      f"the chat entry carries the icon, got {entry.get('avatar')}")
check(entry.get("spectator") is True, "it is still flagged as a spectator line")
check(entry.get("sender") == "[Spectator] Tim", "the sender label is unchanged")
check("background" in entry, "the background key is always present on the entry")

# This is the whole point: the name the client would hash for a default face is
# NOT the player's name, so relaying the real icon is the only way to get it right.
check(entry.get("sender") != "Tim",
      "the sender string is prefixed, so a name-hash fallback could never match")

s5 = Stub()
tok5 = join(s5, "Reader", "/avatars/osprey.png", "/backgrounds/bg-coral-reef.png")["spectator_token"]
Room.submit_spectator_chat(s5, tok5, "hello")
check(s5.chat_messages[-1].get("background") == "/backgrounds/bg-coral-reef.png",
      "the equipped background rides on the chat line too")

print("5. Equipping something else mid-watch")
out = Room.set_spectator_look(s, tok, avatar="/avatars/manta-ray.png")
check(out.get("ok") is True, f"the update is accepted, got {out}")
check(s.spectators[tok]["avatar"] == "/avatars/manta-ray.png", "the new icon is stored")
check(s.spectators[tok]["background"] == "",
      "a background left out of the call is untouched, not cleared")
before = s.bumped
Room.set_spectator_look(s, tok, avatar="/avatars/manta-ray.png")
check(s.bumped == before, "re-sending the same icon does not bump the room version")
Room.set_spectator_look(s, tok, background="/backgrounds/bg-coral-reef.png")
check(s.spectators[tok]["background"] == "/backgrounds/bg-coral-reef.png",
      "a background can be set on its own")
check(s.bumped == before + 1, "a real change does bump, so other clients see it at once")
Room.set_spectator_look(s, tok, background="")
check(s.spectators[tok]["background"] == "", "and cleared again")

Room.submit_spectator_chat(s, tok, "changed my mind")
check(s.chat_messages[-1].get("avatar") == "/avatars/manta-ray.png",
      "the next chat line wears the NEW icon")

# Same rule as a seat (set_avatar): an unusable value is treated as "clear",
# never stored. The client then falls back to the name default, which is safe;
# what must never happen is a foreign path reaching another player's <img>.
bad = Room.set_spectator_look(s, tok, avatar="/etc/passwd")
check(bad.get("ok") is True and s.spectators[tok]["avatar"] == "",
      f"a bogus icon clears rather than storing, got {s.spectators[tok]['avatar']!r}")
Room.set_spectator_look(s, tok, avatar="/avatars/manta-ray.png")

check(Room.set_spectator_look(s, "not-a-token", avatar="/avatars/mullet.png").get("ok") is False,
      "an unknown spectator token is refused")

print("6. None of this changed who may watch")
check(Room.spectator_join(Stub(allow=False), "Tim", "/avatars/mullet.png").get("ok") is False,
      "a room with spectating off still refuses")
check(Room.spectator_join(Stub(phase="ended"), "Tim", "/avatars/mullet.png").get("ok") is False,
      "a finished game still refuses")

print(f"\nspectator-icon checks: {checks}")
if failures:
    print(f"{failures} FAILED")
    sys.exit(1)
print("spectator icons OK")
