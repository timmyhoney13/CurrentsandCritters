#!/usr/bin/env python3
"""Critter emotes in room chat, the server half.

Run:  python3 test_chat_emotes.py

An emote is a chat line that carries an animal's id instead of text. Three
server rules make that safe and make it work:

  1. The id is VALIDATED, not filtered. Running it through the profanity
     masker would asterisk innocent slug segments, and accepting free text
     would let a client put markup or a path where every other client builds
     an <img src>. Only a plain avatar slug survives.
  2. An emote line has no text, so it is the one case where an empty message
     is a real message, but a line with neither text nor emote is still
     rejected.
  3. The emote is stored on the chat entry, which is what reaches every
     client (and what a restored room replays).
"""
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))

# Import multiplayer_server without starting anything: it guards its own
# startup behind __main__, so a plain import is enough.
spec = importlib.util.spec_from_file_location(
    "mp_server_under_test", os.path.join(ROOT, "multiplayer_server.py"))
mp = importlib.util.module_from_spec(spec)
sys.modules["mp_server_under_test"] = mp
spec.loader.exec_module(mp)

failures = 0
checks = 0


def check(cond, label):
    global failures, checks
    checks += 1
    if not cond:
        failures += 1
        print("  ✗ " + label)


print("\n1. Emote ids are validated, never filtered")
clean = mp._clean_emote_id

# Real avatar slugs, taken from the avatars folder, must all survive intact.
avatar_dir = os.path.join(ROOT, "multiplayer/client/avatars")
slugs = sorted({f[:-4] for f in os.listdir(avatar_dir) if f.endswith(".png")})
check(len(slugs) > 40, f"the avatar folder has plenty of critters ({len(slugs)})")
bad = [s for s in slugs if clean(s) != s]
check(not bad, f"every avatar slug is a valid emote id, these were rejected: {bad[:5]}")

# The profanity masker is what we are deliberately bypassing: prove it would
# actually have damaged ids, so the bypass is justified rather than incidental.
mangled = [s for s in slugs if mp._censor_profanity(s) != s]
print(f"   (profanity filter would have mangled {len(mangled)} slug(s): {mangled[:3]})")

check(clean("blue-tang") == "blue-tang", "a hyphenated slug is kept")
check(clean("  Blue-Tang  ") == "blue-tang", "case and surrounding space are normalized")
check(clean("mullet") == "mullet", "a plain slug is kept")

print("2. Anything that isn't a slug is refused")
for bad_value in [
    "../../etc/passwd",          # path traversal
    "/avatars/mullet.png",       # a path, not an id
    "<img src=x onerror=1>",     # markup
    "mullet.png",                # dots
    "blue tang",                 # spaces
    "-mullet",                   # leading separator
    "mullet-",                   # trailing separator
    "blue--tang",                # doubled separator
    "MULLET!",                   # punctuation
    "https://example.com/x.png",  # a URL
    "",
    "   ",
    None,
    123,
    {"id": "mullet"},
    ["mullet"],
]:
    check(clean(bad_value) == "", f"refused: {bad_value!r}")

check(len(clean("a" * 200)) <= 40, "an absurdly long id is truncated, not stored whole")

print("3. The empty-message rule")
src = open(os.path.join(ROOT, "multiplayer_server.py"), encoding="utf-8").read()
check('if not message and not emote:' in src,
      "a line with neither text nor an emote is still rejected")
check('emote = _clean_emote_id(body.get("emote"))' in src,
      "submit_chat reads and validates the emote field")
check('entry["emote"] = emote' in src,
      "a valid emote is stored on the chat entry")
# The emote must not slip into the word-matching paths: an emote line has an
# empty message, so those must be safe with "".
check(mp._is_good_game_message("") is False,
      "an emote-only line never counts as saying 'good game'")

print("4. A real room accepts an emote and shows it to everyone")
# Drive the actual submit_chat on a real room object rather than trusting the
# source read above.
room = None
for name in ("GameRoom", "Room"):
    if hasattr(mp, name):
        room = getattr(mp, name)
        break
check(room is not None, "the room class is importable")

if room is not None:
    import inspect
    sig = inspect.signature(room.submit_chat)
    check(list(sig.parameters) == ["self", "body"],
          f"submit_chat takes a body dict, got {list(sig.parameters)}")

    # A minimal stand-in with just what submit_chat touches, so this stays a
    # unit test of the chat path and not a whole-game integration run.
    import threading
    import time as _t

    class FakeSeat:
        kind = "human"
        claimed_name = "Tim"
        label = "P1"
        avatar = "/avatars/mullet.png"
        background = ""
        index = 0
        token = "tok"

    class Stub:
        def __init__(self):
            self.cond = threading.Condition()
            self.chat_messages = []
            self.clan_gg_names = set()
            self.phase = "running"
            self.active_action_seat = None
            self.bumped = 0

        def _seat_from_token_locked(self, tok):
            return FakeSeat() if tok == "tok" else None

        def _bump_locked(self):
            self.bumped += 1

        def _record_event(self, msg):
            pass

        def _afk_handle_chat_locked(self, seat, message):
            # The real parser; an empty emote message must not blow it up.
            return room._afk_handle_chat_locked(self, seat, message)

        def _afk_resolve_target_locked(self, name):
            return None

        def _add_system_chat(self, text):
            self.chat_messages.append({"sender": "System", "message": text})

    s = Stub()
    res = room.submit_chat(s, {"seat_token": "tok", "message": "", "emote": "blue-tang"})
    check(res.get("ok") is True, f"an emote-only line is accepted, got {res}")
    check(len(s.chat_messages) == 1, "the emote landed in the chat log")
    entry = s.chat_messages[0] if s.chat_messages else {}
    check(entry.get("emote") == "blue-tang", f"the emote id is on the entry, got {entry.get('emote')}")
    check(entry.get("message") == "", "an emote line carries no text")
    check(entry.get("sender") == "Tim", "the sender is the seat's name")
    check(s.bumped == 1, "the room version bumps so SSE pushes it instantly")

    s2 = Stub()
    res2 = room.submit_chat(s2, {"seat_token": "tok", "message": "", "emote": "<script>"})
    check(res2.get("ok") is False, "an emote-only line with a bogus id is rejected")
    check(not s2.chat_messages, "nothing is stored for a rejected line")

    s3 = Stub()
    res3 = room.submit_chat(s3, {"seat_token": "tok", "message": "nice one", "emote": "osprey"})
    check(res3.get("ok") is True, "text plus an emote is allowed")
    check(s3.chat_messages[0].get("message") == "nice one", "the caption survives")
    check(s3.chat_messages[0].get("emote") == "osprey", "so does the emote")

    s4 = Stub()
    res4 = room.submit_chat(s4, {"seat_token": "tok", "message": "", "emote": ""})
    check(res4.get("ok") is False, "a line with neither text nor emote is refused")

    s5 = Stub()
    res5 = room.submit_chat(s5, {"seat_token": "nope", "message": "", "emote": "osprey"})
    check(res5.get("ok") is False, "an emote still needs a valid player seat")

    # An ordinary chat line must be completely unaffected by all of this.
    s6 = Stub()
    res6 = room.submit_chat(s6, {"seat_token": "tok", "message": "good game"})
    check(res6.get("ok") is True, "an ordinary message still works")
    check("emote" not in s6.chat_messages[0], "a text line carries no emote key")
    check("tim" in s6.clan_gg_names, "'good game' is still credited on a text line")

print(f"\nchat-emote checks: {checks}")
if failures:
    print(f"{failures} FAILED")
    sys.exit(1)
print("chat emotes OK")
