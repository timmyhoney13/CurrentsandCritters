#!/usr/bin/env python3
"""What a bot is CALLED and what it LOOKS LIKE.

Run:  python3 test_bot_names.py

Two things a player reads off a bot at the table, and neither of them is the
Elo:

 1. A SURNAME, one word. The pool is ocean explorers, and every entry is the
    single word a person would actually call them: "Murray", not "John
    Murray". A seat label sits under a face in a row of up to six, and a name
    with a first name on it is the one that wraps or clips. Two entries have no
    surname to cut down to (Pytheas, Piri) and are allowed through as the one
    word they are known by.

    Cutting the pool down to surnames is what makes a COLLISION possible: the
    grade ladder's own rungs are people too, and a bot called "Forbes" playing
    at rank "Edward Forbes" reads as a bug. So the two lists are checked
    against each other here rather than in a comment.

 2. ITS RANK'S FACE. Head to Head is a reef with one animal standing on each
    rank, and that animal IS the rank as far as a player is concerned: they
    learn "Bobtail Squid" long before they learn that F is below E. So an F bot
    is a Bobtail Squid at the table too, an E is a Hermit Crab, and so on up.
    Every tier on the ladder needs an animal, every animal needs a file on
    disk, and the seat payload the client draws from has to carry it.
"""
import atexit
import io
import os
import random
import shutil
import tempfile

_SANDBOX = tempfile.mkdtemp(prefix="cc-botnames-test-")
atexit.register(shutil.rmtree, _SANDBOX, True)
os.environ["FISH_ROOM_STATE_DIR"] = os.path.join(_SANDBOX, "state")
os.environ["FISH_GAMES_HISTORY_DIR"] = os.path.join(_SANDBOX, "games_history")
os.environ["FISH_COMPETITIVE_GAMES_DIR"] = os.path.join(_SANDBOX, "competitive_games")

import fish_game_all_in_one as fish  # noqa: E402
import multiplayer_server as mp  # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
AVATARS = os.path.join(ROOT, "multiplayer", "client", "avatars")

PASS = 0
FAIL = 0


def check(cond, name, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  ✗ FAIL: {name}" + (f"  → {extra}" if extra else ""))


def section(title):
    print(f"\n{title}")


# ── 1. surnames, one word each ──────────────────────────────────────────────
section("the bot name pool is surnames, one word each")

NAMES = list(fish.OCEAN_EXPLORER_NAMES)
check(len(NAMES) >= 24, "there are enough of them to fill a table without repeating",
      str(len(NAMES)))
check(len(set(NAMES)) == len(NAMES), "no name appears twice",
      str([n for n in NAMES if NAMES.count(n) > 1]))

long_ones = [n for n in NAMES if len(n) > fish.BOT_NAME_MAX_CHARS]
check(not long_ones,
      f"none is longer than {fish.BOT_NAME_MAX_CHARS} characters, so no seat label clips",
      str(long_ones))

multi = [n for n in NAMES if len(n.split()) > fish.SURNAME_MAX_WORDS]
check(not multi, "every one is a single word: the surname and nothing else", str(multi))

check(all(n == n.strip() and n for n in NAMES),
      "none is blank or padded with spaces")

# The names that WERE two words before this change. Each one is checked by the
# surname that should have survived it, so a revert shows up here rather than
# on a seat label.
for whole, surname in [("Zheng He", "Zheng"), ("Ibn Majid", "Majid"),
                       ("James Ross", "Ross"), ("Carl Chun", "Chun"),
                       ("John Murray", "Murray")]:
    check(whole not in NAMES and surname in NAMES,
          f"  {whole!r} is now just {surname!r}")
# Prince Albert I of Monaco had no surname to cut to: the family name is
# Grimaldi, and that is a house still ruling, which is the one live interest
# the pool's own rule exists to avoid. He was replaced outright.
check("Albert I" not in NAMES and "Grimaldi" not in NAMES,
      "  Prince Albert I is out rather than reduced to a living family's name")
check("Agassiz" in NAMES, "  …and Alexander Agassiz took the slot")

# ── 2. no bot shares a name with a rank ─────────────────────────────────────
section("no bot can be called the same thing as the rank it is playing at")

ladder_people = [row["grade"] for row in fish.bot_grade_table()]
ladder_words = set()
for whole in ladder_people:
    for word in whole.replace("-", " ").split():
        ladder_words.add(word.lower())
clashes = sorted(n for n in NAMES if n.lower() in ladder_words)
check(not clashes,
      "no explorer surname appears anywhere in a rung's name",
      f"{clashes} vs {ladder_people}")
check(not (set(n.lower() for n in NAMES) & set(p.lower() for p in ladder_people)),
      "and no bot name IS a rung's name")

# ── 3. drawing names for a table ────────────────────────────────────────────
section("a table never shows the same name twice")

for count in (1, 3, 5, 8, len(NAMES), len(NAMES) + 4):
    drawn = fish.explorer_names(count, random.Random(count))
    check(len(drawn) == count, f"  {count} asked for, {count} handed back", str(len(drawn)))
    check(len(set(drawn)) == count, f"  …all {count} distinct", str(drawn))

a = fish.explorer_names(8, random.Random(1))
b = fish.explorer_names(8, random.Random(2))
check(a != b, "two tables are not dealt the same eight names", f"{a} / {b}")

# Past the end of the pool it numbers rather than repeats: two bots called
# Nansen at one table is worse than a bot called "Bot 33".
over = fish.explorer_names(len(NAMES) + 2, random.Random(7))
check(sum(1 for n in over if n.startswith("Bot ")) == 2,
      "past the end of the pool it numbers instead of repeating", str(over[-4:]))

# ── 4. every rank has an animal, and every animal has a file ────────────────
section("every rank wears the animal it stands on in Head to Head")

table = fish.bot_grade_table()
tiers = [row["tier"] for row in table]
missing = [t for t in tiers if t not in fish.BOT_GRADE_ANIMALS]
check(not missing, "every tier on the ladder has an animal", str(missing))
check(len(set(fish.BOT_GRADE_ANIMALS[t] for t in tiers)) == len(tiers),
      "no two ranks wear the same animal",
      str([(t, fish.BOT_GRADE_ANIMALS[t]) for t in tiers]))

# These are the pairings the reef draws, named outright: the whole point is
# that the table agrees with the reef, so the reef's own list is written here.
EXPECTED = {
    "F": "bobtail-squid", "E": "hermit-crab", "D": "peruvian-pelican",
    "C": "staghorn-coral", "B": "narwhal", "A": "great-white-shark",
    "S": "mandarin-goby", "S+": "bunker", "GS": "giant-squid",
}
for tier, animal in EXPECTED.items():
    check(fish.BOT_GRADE_ANIMALS.get(tier) == animal,
          f"  rank {tier} is the {animal.replace('-', ' ')}",
          str(fish.BOT_GRADE_ANIMALS.get(tier)))
check(set(fish.BOT_GRADE_ANIMALS) == set(EXPECTED),
      "and the map holds nothing else",
      str(set(fish.BOT_GRADE_ANIMALS) ^ set(EXPECTED)))

section("the pictures exist on disk")
for tier, animal in sorted(fish.BOT_GRADE_ANIMALS.items()):
    png = os.path.join(AVATARS, animal + ".png")
    check(os.path.exists(png), f"  /avatars/{animal}.png is there", png)

# ── 5. the lookups ──────────────────────────────────────────────────────────
section("a grade in any spelling resolves to the right face")

for row in table:
    want = "/avatars/" + fish.BOT_GRADE_ANIMALS[row["tier"]] + ".png"
    check(fish.bot_grade_avatar(row["id"]) == want,
          f"  by id: {row['id']} → {want}", fish.bot_grade_avatar(row["id"]))
    check(fish.bot_grade_avatar(row["tier"]) == want,
          f"  by tier: {row['tier']} → {want}", fish.bot_grade_avatar(row["tier"]))
    check(fish.bot_grade_avatar(row["grade"]) == want,
          f"  by name: {row['grade']} → {want}", fish.bot_grade_avatar(row["grade"]))
    check(row["animal"] == fish.BOT_GRADE_ANIMALS[row["tier"]]
          and row["avatar"] == want,
          f"  …and the served ladder row carries it: {row['id']}",
          f"{row.get('animal')} / {row.get('avatar')}")

# A saved room, an old client or a tournament bracket still says "a" or "hard".
# Those have to land on a real face, not on nothing.
check(fish.bot_grade_avatar("a") == fish.bot_grade_avatar("eugenie_clark"),
      "a legacy single-letter id lands on the same face as its rung")
check(fish.bot_grade_avatar("hard") == fish.bot_grade_avatar("eugenie_clark"),
      "and so does one of the three old words")
# Junk must never draw a broken image.
for junk in ("", None, "nonsense", "   ", "zzz"):
    got = fish.bot_grade_avatar(junk)
    check(got.startswith("/avatars/") and got.endswith(".png"),
          f"  junk grade {junk!r} still resolves to a real picture", got)
    check(os.path.exists(os.path.join(AVATARS, os.path.basename(got))),
          f"  …and that picture is on disk: {got}")

# ── 6. the seat the client actually draws ───────────────────────────────────
section("the seat payload carries the face")

for row in table:
    seat = mp.Seat(index=0, kind="ai", label="Player 1", difficulty=row["id"])
    check(seat.display_avatar() == row["avatar"],
          f"  an {row['tier']} bot seat wears {row['avatar']}", seat.display_avatar())

human = mp.Seat(index=1, kind="human", label="Player 2")
check(human.display_avatar() == "",
      "a person with nothing equipped is handed no face, not a bot's",
      human.display_avatar())
human.avatar = "/avatars/clownfish.png"
check(human.display_avatar() == "/avatars/clownfish.png",
      "a person wears what they equipped")

# A kicked player's chair is played out by a bot but keeps the face the table
# already knows, so an explicitly set avatar has to win over the rank's.
kicked = mp.Seat(index=2, kind="ai", label="Player 3", difficulty="rachel_carson")
kicked.avatar = "/avatars/clownfish.png"
check(kicked.display_avatar() == "/avatars/clownfish.png",
      "an avatar already set on a bot seat wins, so a kicked chair keeps its face")

room = mp.GameRoom(room_id="FACES", host_name="Diver", total_players=4,
                   human_players=1, ai_players=3)
for i, gid in enumerate(["gilbert_carter", "eugenie_clark", "giant_squid"]):
    room.seats[i + 1].kind = "ai"
    room.seats[i + 1].difficulty = gid
with room.cond:
    snap = room.seat_snapshot_locked()
for i, gid in enumerate(["gilbert_carter", "eugenie_clark", "giant_squid"]):
    seat = snap[i + 1]
    check(seat["avatar"] == fish.bot_grade_avatar(gid),
          f"  the snapshot's seat {i + 1} ({gid}) carries {fish.bot_grade_avatar(gid)}",
          str(seat["avatar"]))
    check(seat["grade_tier"] == fish.ai_difficulty_config(gid)["tier"],
          f"  …beside the tier the badge needs: {seat['grade_tier']}")
check(snap[0]["avatar"] == "",
      "the human seat's avatar is still empty, so nothing paints a bot on a person",
      str(snap[0]["avatar"]))

# ── 7. every path that makes a bot names it ─────────────────────────────────
# This is the half that was broken. The explorer pool existed, the Head to Head
# lobby drew from it, and every OTHER way of getting a bot to a table wrote
# f"Bot {n}" itself: an ordinary room opened with three bots in it sat down
# against Bot 1, Bot 2 and Bot 3.
section("a bot gets a name whichever way it reaches the table")


def bot_seats(room):
    return [s for s in room.seats if s.kind == "ai"]


def names_of(room):
    return [str(s.claimed_name or "") for s in bot_seats(room)]


def check_named(room, where):
    got = names_of(room)
    check(got and all(n in NAMES for n in got),
          f"  {where}: every bot wears an explorer surname", str(got))
    check(len(set(got)) == len(got), f"  {where}: and no two of them share one", str(got))
    check(not any(n.startswith("Bot ") for n in got),
          f"  {where}: nobody is called 'Bot 1'", str(got))
    return got


# Straight out of room creation, which is how an ordinary game begins.
made = mp.GameRoom(room_id="MADE", host_name="Diver", total_players=5,
                   human_players=1, ai_players=4)
created = check_named(made, "a room opened with four bots")
check(all(s.bot_name == s.claimed_name for s in bot_seats(made)),
      "  the name is remembered on the seat, not just printed")
check(all(s.display_avatar() == fish.bot_grade_avatar(s.difficulty)
          for s in bot_seats(made)),
      "  and each one already wears its rank's face",
      str([s.display_avatar() for s in bot_seats(made)]))

# Adding and removing seats renumbers the table. The bots already sitting at it
# are the same opponents, so they must keep their names through it.
with made.cond:
    made.configure_lobby_seats(made.host_control_token, None, 6)
after_add = names_of(made)
check(all(n in after_add for n in created),
      "adding a seat keeps every bot already at the table", f"{created} -> {after_add}")
check_named(made, "after a seat was added")
with made.cond:
    made.configure_lobby_seats(made.host_control_token, None, 4)
check_named(made, "after a seat was removed")

# The Head to Head search giving up and botting out the empty chairs.
qp = mp.GameRoom(room_id="QUITP", host_name="Diver", total_players=4,
                 human_players=4, ai_players=0, quick_play=True)
qp.seats[0].kind = "human"
qp.seats[0].claimed_name = "Diver"
qp.seats[0].token = "tok0"
qp.seats[0].is_host = True
with qp.cond:
    out = qp.quick_play_fill_with_bots(qp.host_control_token, "tok0", {})
check(out.get("ok"), "the search gives up and fills the table", str(out.get("error")))
check_named(qp, "after the search gave up")

# A server restart mid game. claimed_name comes back off disk, and so must
# bot_name, or the next pass over the table draws everybody a new name and the
# opponent a player is halfway through a game against becomes someone else.
section("a restart does not rename the bots")
with made.cond:
    saved = made._serialize_checkpoint_locked()
before = names_of(made)
back = mp.GameRoom.from_checkpoint(saved)
check(back is not None, "the room comes back off disk")
if back is not None:
    check(names_of(back) == before, "with the same bots", f"{before} -> {names_of(back)}")
    check(all(s.bot_name for s in bot_seats(back)),
          "and the drawn name came back too, not just the printed one",
          str([s.bot_name for s in bot_seats(back)]))
    # The pass that renames is the one that runs on the next state change.
    with back.cond:
        for seat in bot_seats(back):
            back._name_bot_seat(seat)
    check(names_of(back) == before,
          "so the next pass over the table leaves them alone",
          f"{before} -> {names_of(back)}")

# A room saved BEFORE bot_name was kept has no such field. Its printed name is
# the name to keep.
old_save = {k: v for k, v in saved.items()}
old_save["seats"] = [{k: v for k, v in st.items() if k != "bot_name"}
                     for st in saved["seats"]]
legacy = mp.GameRoom.from_checkpoint(old_save)
check(legacy is not None, "an older save still opens")
if legacy is not None:
    check(names_of(legacy) == before,
          "and keeps the names it was saved with", f"{before} -> {names_of(legacy)}")
    check(all(s.bot_name == s.claimed_name for s in bot_seats(legacy)),
          "adopting them as the drawn name, so nothing renames them later")

# ── 8. drawing names for one table ──────────────────────────────────────────
section("a bot seat is given a name and keeps it")

room2 = mp.GameRoom(room_id="NAMES", host_name="Diver", total_players=6,
                    human_players=1, ai_players=5)
with room2.cond:
    taken = names_of(room2)
check(len(set(taken)) == len(taken), "five bots, five different names", str(taken))
check(all(len(n.split()) == 1 for n in taken),
      "every one of them is a single word", str(taken))
check(all(n in NAMES or n.startswith("Bot ") for n in taken),
      "and every one came out of the pool", str(taken))

# ── 9. a bot must still READ as a bot to the client ─────────────────────────
# The one real danger in giving bots people's names. Playing bots pays half XP
# and the new-opponent achievements are about real people, and the client used
# to decide both by the SHAPE of the name: /\bbot\b/ matched "Bot 2" and
# nothing else. "Shackleton" does not match it, so without a second answer a
# table of bots would pay full XP and count as new friends.
#
# The second answer is the seat snapshot, which says kind:"ai" outright. This
# checks the two halves that have to line up for that to work: the server has
# to send the bot's name on a seat marked "ai", and the client has to look
# there rather than at the name.
section("a bot still reads as a bot, so XP and achievements stay honest")

APP = io.open(os.path.join(ROOT, "multiplayer", "client", "js", "preview-app.js"),
              encoding="utf-8").read()

check("the client asks the seat snapshot, not just the shape of the name",
      "function isLikelyAiName(name) {\n    return _ccSeatIsBotNamed(name) || _ccAiStyleName(name);\n  }" in APP)
check("…and the seat question is answered off kind:\"ai\"",
      'st.kind === "ai"' in APP.split("function _ccSeatIsBotNamed")[1].split("}")[0]
      if "function _ccSeatIsBotNamed" in APP else False)
check("the old shape test is kept for saved games and older clients",
      "function _ccAiStyleName(name) {" in APP
      and r"/\bbot\b/i.test(n)" in APP)
check("halved XP is still decided through it",
      "const hasAi = (Array.isArray(finalScores)?finalScores:[]).some(p=>isLikelyAiName(p?.name))" in APP)
check("and so is who counts as a real opponent",
      "isLikelyAiName(n)))" in APP)

# The server half: the name has to arrive on a seat the client can recognise.
with made.cond:
    snap2 = made.seat_snapshot_locked()
bot_rows = [r for r in snap2 if r["kind"] == "ai"]
check(bot_rows, "the snapshot has bot rows at all")
check(all(r["claimed_name"] in NAMES for r in bot_rows),
      "every bot row carries its explorer surname",
      str([r["claimed_name"] for r in bot_rows]))
check(all(r["kind"] == "ai" for r in bot_rows),
      "…on a row marked ai, which is what the client matches on")
# And the name must not collide with a person's, or the person reads as a bot.
human_names = {str(r["claimed_name"] or "").lower() for r in snap2 if r["kind"] == "human"}
check(not (human_names & {str(r["claimed_name"]).lower() for r in bot_rows}),
      "no bot took a name a person at the table is using", str(human_names))

coll = mp.GameRoom(room_id="COLL", host_name="Nansen", total_players=4,
                   human_players=1, ai_players=3)
coll.seats[0].claimed_name = "Nansen"
with coll.cond:
    for seat in [s for s in coll.seats if s.kind == "ai"]:
        seat.bot_name = ""
        seat.claimed_name = None
    for seat in [s for s in coll.seats if s.kind == "ai"]:
        coll._name_bot_seat(seat)
drawn = [s.claimed_name for s in coll.seats if s.kind == "ai"]
check("Nansen" not in drawn,
      "a player's own name is off the table for the bots beside them", str(drawn))

print(f"\n{'=' * 50}\nRESULT: {PASS} passed, {FAIL} failed")
raise SystemExit(1 if FAIL else 0)
