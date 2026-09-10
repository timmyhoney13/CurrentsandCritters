#!/usr/bin/env python3
"""The bot ladder: ten named opponents, and the Elo printed next to each one.

Run:  python3 test_bot_grades.py

The rungs are people now (Gilbert Thomas Carter at the bottom, Charles Darwin
at the top, the Giant Squid past him) and each one wears a tier, F to S++, and
shows players a number. Five things have to hold for any of that to be worth
printing, and all five are the sort of thing that breaks quietly:

 1. THE LADDER IS A LADDER. Every grade must be handicapped LESS than the one
    below it. If B blunders more often than B- while claiming a hundred more
    Elo, the ladder is decoration and the number on the screen is a lie.

 2. THE OLD WORDS STILL MEAN SOMETHING. Rooms saved to disk, tournament
    brackets, the Current Controller and every older test still say "medium".
    A room that comes back from disk with a word this build has never heard of
    must still seat a bot, and it must be the same bot every time.

 3. A GRADE SURVIVES THE ROUND TRIP. The host picks a grade in the lobby, the
    server stores it, the seat snapshot carries it back, and the engine is
    handed it at launch. Anywhere along there it could be silently replaced by
    the default and nobody would see anything except a bot playing oddly.

 4. THE HEAD TO HEAD TABLE IS WHAT IT SAYS. Four seats, one person, three bots
    at the three grades that were asked for, and the game already running.

 5. THE CLIMB IS A CHAIN. Every rung names the rung below it as the one you
    have to beat first, the bottom rung names nobody, and the chain reaches
    every rung exactly once. A chain with a hole in it is a ladder a player
    cannot finish.

It deliberately does NOT check that a grade's Elo is CORRECT: that is measured,
not asserted, and calibrate_bots.py is what measures it. What is checked here
is that the numbers are ordered, spaced far enough apart to be distinguishable,
and actually reach the bot.
"""
import atexit
import json
import os
import shutil
import tempfile
import urllib.error
import urllib.request

# A room that starts a real game writes a training record, a history file and a
# leaderboard entry. Redirect all of it before the server is imported, because
# it resolves those paths at import time.
_SANDBOX = tempfile.mkdtemp(prefix="cc-grades-test-")
atexit.register(shutil.rmtree, _SANDBOX, True)
os.environ["FISH_ROOM_STATE_DIR"] = os.path.join(_SANDBOX, "state")
os.environ["FISH_GAMES_HISTORY_DIR"] = os.path.join(_SANDBOX, "games_history")
os.environ["FISH_COMPETITIVE_GAMES_DIR"] = os.path.join(_SANDBOX, "competitive_games")

import fish_game_all_in_one as fish  # noqa: E402
import multiplayer_server as mp  # noqa: E402

mp.DATASET_PATH = os.path.join(_SANDBOX, "human_game_dataset.jsonl")

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


# ── 1. the ladder is the ladder that was asked for ──────────────────────────
section("ten rungs, Gilbert Thomas Carter to the Giant Squid")

WANTED = ["Gilbert Thomas Carter", "Jeanne Villepreux-Power", "Edward Forbes",
          "Steve Irwin", "William Beebe", "Eugenie Clark", "Rachel Carson",
          "Jacques Cousteau", "Charles Darwin", "Giant Squid"]
WANTED_TIERS = ["F", "E", "D", "C", "B", "A", "S", "S+", "S++", "GS"]
table = fish.bot_grade_table()
check([row["grade"] for row in table] == WANTED,
      "the ladder is the nine marine scientists, weakest first, then the Squid",
      str([row["grade"] for row in table]))
check(len(fish.BOT_GRADE_ORDER) == 10, "ten of them")
check(len(set(fish.BOT_GRADE_ORDER)) == 10, "no grade id is repeated")
check(table[-1]["id"] == "giant_squid", "the Squid is last, and it is the top")
check(table[-2]["id"] == "charles_darwin",
      "Charles Darwin is the rung directly below the Squid")
check([r["tier"] for r in table] == WANTED_TIERS,
      "each rung names its own badge tier, so S+ is not mistaken for S",
      str([r["tier"] for r in table]))
check(len(set(r["tier"] for r in table)) == 10,
      "…and no two rungs share a tier")
check(all(row["id"] in fish.AI_DIFFICULTY_CONFIGS for row in table),
      "every published grade has a config behind it")

section("the Elo column is ordered, and spaced far enough apart to mean something")
elos = [row["elo"] for row in table]
check(all(b > a for a, b in zip(elos, elos[1:])),
      "every grade's Elo is strictly above the grade below it", str(elos))
gaps = [b - a for a, b in zip(elos, elos[1:])]
# 25 Elo is roughly a 54% head-to-head: below that two grades are the same bot
# wearing different letters, which is worse than not having the grade at all.
# Nine rungs over ~900 Elo of real range is ~110 a step. The bar is 100,
# because a step smaller than that is inside the error bars of any calibration
# run we can afford, and a grade nobody can tell apart from its neighbour is
# not a grade.
check(min(gaps) >= 100,
      "no two neighbouring grades are within 100 Elo of each other",
      f"tightest gap {min(gaps)}")
check(elos[-1] - elos[0] >= 900,
      "the ladder spans a real range end to end",
      f"{elos[0]} → {elos[-1]}")


# ── 2. every handicap moves one way ─────────────────────────────────────────
section("a higher grade is handicapped less, on every knob, without exception")

cfgs = [fish.AI_DIFFICULTY_CONFIGS[key] for key in fish.BOT_GRADE_ORDER]
# (knob, direction) — "up" must never decrease as the grade rises, "down" must
# never increase.
for knob, direction in [
    ("pick_bias", "up"), ("raw_chance", "down"),
    ("strategy_weight", "up"), ("block_weight", "up"), ("future_weight", "up"),
    ("switch_margin", "up"), ("plan_candidates", "up"), ("plan_samples", "up"),
    ("confirm_weight", "up"), ("runoff_samples", "up"), ("plan_budget", "up"),
]:
    vals = [c[knob] for c in cfgs]
    if direction == "up":
        ok = all(b >= a for a, b in zip(vals, vals[1:]))
    else:
        ok = all(b <= a for a, b in zip(vals, vals[1:]))
    check(ok, f"{knob} only goes {direction} as the grade rises", str(vals))

check(all(c["payment_smart"] for c in cfgs[4:]),
      "William Beebe and up all pay their costs carefully")
# The curated candidate list drops moves that overbuild an ocean or feed a
# dead engine. Seeing those moves is a handicap, so it may only ever be
# switched OFF as the grade rises, never back on.
raws = [c["raw_chance"] for c in cfgs]
check(all(b <= a for a, b in zip(raws, raws[1:])),
      "the uncurated move list is only ever consulted LESS as the grade rises",
      str(raws))
check(cfgs[-1]["raw_chance"] == 0.0, "the top grade never considers junk moves")
check(cfgs[0]["raw_chance"] >= 0.99, "the bottom grade always does")
# It is a probability, not a switch, and that is the whole point: as a switch
# it was one 458-Elo step in a ladder whose other rungs were worth 120.
check(any(0.0 < c["raw_chance"] < 1.0 for c in cfgs),
      "…and at least one grade sits between the two, so the drop is graded",
      str(raws))

# The one knob that has to cross zero. A ladder whose weakest bot still
# prefers good moves cannot reach a beginner: measured against a policy that
# plays at random, such a bot sits ABOVE random, and every grade above it is
# then squeezed into whatever is left.
check(cfgs[0]["pick_bias"] < 0.0,
      "the bottom grade leans towards the WORSE move, so the ladder can reach "
      "below a random opponent", str(cfgs[0]["pick_bias"]))
# The ladder has to CROSS the no-preference mark, not merely approach it.
# Below zero the bot is worse than an opponent playing at random, which is the
# only way the bottom rung reaches a real beginner. It does not matter whether
# a grade lands exactly on zero, only that the ladder spans it.
check(min(c["pick_bias"] for c in cfgs) < 0.0 < max(c["pick_bias"] for c in cfgs),
      "…and the ladder spans the random-play mark rather than starting above it",
      str([c["pick_bias"] for c in cfgs]))
check(cfgs[-1]["pick_bias"] >= 6.0,
      "the top grade takes the best move essentially always")
skills = [c["skill_level"] for c in cfgs]
rank = {"beginner": 0, "intermediate": 1, "advanced": 2, "expert": 3}
check(all(rank[b] >= rank[a] for a, b in zip(skills, skills[1:])),
      "the strategy book only ever opens further up the ladder", str(skills))
# The whole point of the bottom of the ladder is that it is cheap: a lobby of
# low grades must not buy the server's most expensive work.
check(all(fish.AI_DIFFICULTY_CONFIGS[k]["plan_candidates"] == 0
          for k in ("gilbert_carter", "jeanne_villepreux_power",
                    "edward_forbes", "steve_irwin")),
      "nobody below William Beebe pays for rollouts at all",
      "a lobby of low rungs must not buy the server's most expensive work")
check(max(c["plan_budget"] for c in cfgs) <= 3.0,
      "no grade thinks for longer than three seconds a move",
      "a player sits through every bot's turn")


# ── 3. every spelling of a grade lands somewhere sensible ───────────────────
section("a grade is recognised however it is written")

check(fish.normalize_bot_grade("charles_darwin") == "charles_darwin", "its own id")
check(fish.normalize_bot_grade("Charles Darwin") == "charles_darwin", "the printed name")
check(fish.normalize_bot_grade("S++") == "charles_darwin", "the tier")
check(fish.normalize_bot_grade(" s + + ") == "charles_darwin", "…however it is spaced")
check(fish.normalize_bot_grade("S+") == "jacques_cousteau", "S+ is not S++")
check(fish.normalize_bot_grade("S") == "rachel_carson", "…and S is neither")
check(fish.normalize_bot_grade("F") == "gilbert_carter", "the bottom of the ladder")
check(fish.normalize_bot_grade("E") == "jeanne_villepreux_power", "the rung above it")
check(fish.normalize_bot_grade("Jeanne Villepreux-Power") == "jeanne_villepreux_power",
      "…by name, hyphen and all")

# The ids the ladder used before the rungs were people. A room saved to disk,
# a bracket and an older client all still speak these, and each one has to
# land on the rung with its Elo and its exact knobs.
section("the ids the ladder used to use still land where they always did")
for old_id, want, elo in (("f", "gilbert_carter", 500), ("d", "edward_forbes", 700),
                          ("c", "steve_irwin", 900), ("b", "william_beebe", 1100),
                          ("a", "eugenie_clark", 1300), ("s", "rachel_carson", 1500),
                          ("ss", "jacques_cousteau", 1700),
                          ("ss_plus", "charles_darwin", 1900),
                          ("SS+", "charles_darwin", 1900)):
    got = fish.normalize_bot_grade(old_id)
    check(got == want, f"the old id {old_id!r} still means {want}", got)
    check(fish.bot_grade_elo(old_id) == elo,
          f"…at the Elo it always had ({elo})", str(fish.bot_grade_elo(old_id)))
check(fish.normalize_bot_grade("Giant Squid") == "giant_squid", "the Squid, by name")
check(fish.normalize_bot_grade("giant squid") == "giant_squid", "…in any case")
check(fish.normalize_bot_grade("giantsquid") == "giant_squid", "…with or without the space")

for old in ("easy", "medium", "hard"):
    got = fish.normalize_bot_grade(old)
    check(got in fish.AI_DIFFICULTY_CONFIGS, f"the old word {old!r} still seats a bot")
check(fish.normalize_bot_grade("easy") == fish.LEGACY_DIFFICULTY_ALIASES["easy"],
      "…and the same bot every time")
check(fish.AI_DIFFICULTY_CONFIGS[fish.normalize_bot_grade("easy")]["plan_candidates"] == 0,
      "'easy' keeps its old promise of costing no rollouts",
      "several tests use it precisely because it is cheap")
check(fish.bot_grade_rank(fish.normalize_bot_grade("hard"))
      > fish.bot_grade_rank(fish.normalize_bot_grade("medium"))
      > fish.bot_grade_rank(fish.normalize_bot_grade("easy")),
      "the three old words are still in their old order")
check(fish.bot_grade_unlock(fish.normalize_bot_grade("hard")) != "story",
      "…and none of them lands on the story reward",
      "an old saved room must never resolve into the Giant Squid")

check(fish.normalize_bot_grade(None) == fish.DEFAULT_BOT_GRADE, "nothing → the default")
check(fish.normalize_bot_grade("") == fish.DEFAULT_BOT_GRADE, "empty → the default")
check(fish.normalize_bot_grade("Z++++") == fish.DEFAULT_BOT_GRADE, "nonsense → the default")
check(fish.normalize_bot_grade(12345) == fish.DEFAULT_BOT_GRADE, "a number → the default")
check(fish.DEFAULT_BOT_GRADE in fish.AI_DIFFICULTY_CONFIGS, "the default is a real grade")


# ── 3b. the Giant Squid has to be earned ────────────────────────────────────
section("the Giant Squid is shown to everyone and handed to nobody")

squid = fish.AI_DIFFICULTY_CONFIGS["giant_squid"]
check(squid["unlock"] == "story",
      "the Squid is gated behind the story, not behind a price or a level")
check(fish.STORY_LOCKED_GRADES.get("giant_squid") == "story",
      "…and it says so in one place the clients can read")
check(list(fish.STORY_LOCKED_GRADES) == ["giant_squid"],
      "…and it is the ONLY gated grade: everything else is there for the taking",
      str(list(fish.STORY_LOCKED_GRADES)))
check(all(fish.bot_grade_unlock(k) != "story" for k in fish.BOT_GRADE_ORDER[:-1]),
      "no rung below it is behind the STORY gate")
check(squid["elo"] == max(c["elo"] for c in fish.AI_DIFFICULTY_CONFIGS.values()),
      "the reward for finishing the story is the strongest bot in the game")
check(squid["pick_bias"] >= 6.0 and squid["future_weight"] >= 1.0
      and squid["raw_chance"] == 0.0,
      "…with every handicap switched off")
check(squid["plan_candidates"] >= max(
          c["plan_candidates"] for k, c in fish.AI_DIFFICULTY_CONFIGS.items()
          if k != "giant_squid"),
      "…and more rollout confirmation than anything under it")
check(fish.bot_grade_unlock("Giant Squid") == "story",
      "the gate is found by the printed name too, not only the id")
# The gate must not be reachable by accident from an old room or an old client.
for legacy in ("easy", "medium", "hard", "", "nonsense"):
    check(fish.normalize_bot_grade(legacy) != "giant_squid",
          f"{legacy!r} never resolves into the Squid")


# ── 4. the knobs actually reach the engine ──────────────────────────────────
section("the grade a seat carries is the grade the bot plays at")

card_db = mp.CARD_DB
players = [fish.PlayerState("A"), fish.PlayerState("B")]
for i, grade in enumerate(["f", "giant_squid"]):
    cfg = fish.ai_difficulty_config(grade)
    p = players[i]
    p.flags["_ai_difficulty"] = cfg["difficulty"]
    p.flags["_ai_pick_bias"] = float(cfg["pick_bias"])
    p.flags["_ai_future_weight"] = float(cfg["future_weight"])
    p.flags["_ai_plan_candidates"] = int(cfg["plan_candidates"])
check(players[0].flags["_ai_pick_bias"] < 0.0,
      "an F bot really is drawn to the worse move")
check(players[1].flags["_ai_pick_bias"] >= 6.0,
      "the Squid really does take the best one")
check(players[0].flags["_ai_future_weight"] == 0.0,
      "an F bot cannot see past this turn")
check(players[1].flags["_ai_future_weight"] >= 1.0,
      "the Squid sees the whole game")

# The picker itself, which is where a signed bias either works or silently
# does nothing. Ten thousand draws from a known ranking is enough to see it.
import collections as _c
fake = [(f"rank{i}", 10.0 - i) for i in range(6)]
rank_of = {name: i for i, (name, _) in enumerate(fake)}
for bias, expect in ((6.0, "best"), (0.0, "flat"), (-0.5, "worst")):
    counts = _c.Counter()
    for _ in range(4000):
        counts[rank_of[mp._pick_by_rank(fake, bias)]] += 1
    top, bottom = counts[0] / 4000.0, counts[5] / 4000.0
    if expect == "best":
        check(top > 0.99, "bias 6 takes the best move essentially always", f"{top:.3f}")
    elif expect == "flat":
        check(0.12 < top < 0.21 and 0.12 < bottom < 0.21,
              "bias 0 is a coin flip across every move",
              f"best {top:.3f} worst {bottom:.3f}")
    else:
        check(bottom > top,
              "a negative bias really does prefer the worse move",
              f"best {top:.3f} worst {bottom:.3f}")
check(fish.bot_grade_fraction("f") == 0.0 and fish.bot_grade_fraction("giant_squid") == 1.0,
      "the ladder position runs 0 to 1 end to end")


# ── 3c. the climb is a chain ────────────────────────────────────────────────
section("every rung is unlocked by beating the one below it")

order = list(fish.BOT_GRADE_ORDER)
check(fish.bot_grade_requires(order[0]) == "",
      "the bottom rung needs nobody beaten: there has to be a way on")
check(fish.bot_grade_unlock(order[0]) == "",
      "…and it is not locked")
check(all(fish.bot_grade_requires(b) == a for a, b in zip(order, order[1:])),
      "every other rung names exactly the rung below it",
      str([(k, fish.bot_grade_requires(k)) for k in order]))
check(all(fish.bot_grade_unlock(k) for k in order[1:]),
      "…and every one of them says it has to be earned")
# Walk it: following `requires` down from the Squid must reach every rung
# exactly once. A chain with a hole is a ladder that cannot be finished.
seen, cur, guard = [], order[-1], 0
while cur and guard < 50:
    guard += 1
    seen.append(cur)
    nxt = fish.bot_grade_requires(cur)
    check(nxt != cur, "no rung requires itself", cur)
    if nxt == cur:
        break
    cur = nxt
check(list(reversed(seen)) == order,
      "the chain from the Squid down reaches every rung, once, in ladder order",
      str(list(reversed(seen))))
check(fish.bot_grade_requires("giant_squid") == "charles_darwin",
      "the Squid sits behind Charles Darwin as well as behind the story")
check(fish.bot_grade_unlock("giant_squid") == "story",
      "…and the story gate is the one it publishes")
check(all("requires" in row for row in table),
      "the published ladder carries the chain the clients draw the gate from")
check([row["requires"] for row in table] == [""] + order[:-1],
      "…and it is the same chain", str([row["requires"] for row in table]))


# ── 5. the server: setting, storing and reporting a grade ───────────────────
section("the lobby round trip")

room = mp.ROOMS.create_room("Host", 4, 1, 3)
host_token = room.host_control_token
seat_token = room.host_seat().token
ai_indices = [s.index for s in room.seats if s.kind == "ai"]
check(len(ai_indices) == 3, "a 1-human, 3-bot table has three bot seats")

out = room.set_seat_difficulty(host_token, seat_token, ai_indices[0], "Charles Darwin")
check(out.get("ok") and out.get("difficulty") == "charles_darwin",
      "the host can set a rung by its printed name", json.dumps(out))
check(out.get("grade") == "Charles Darwin"
      and out.get("grade_elo") == fish.bot_grade_elo("charles_darwin"),
      "…and is told the name and the Elo it means")

out = room.set_seat_difficulty(host_token, seat_token, ai_indices[1], "william_beebe")
check(out.get("ok") and out.get("difficulty") == "william_beebe", "…or by its id")
out = room.set_seat_difficulty(host_token, seat_token, ai_indices[1], "S++")
check(out.get("ok") and out.get("difficulty") == "charles_darwin", "…or by its tier")
out = room.set_seat_difficulty(host_token, seat_token, ai_indices[1], "b")
check(out.get("ok") and out.get("difficulty") == "william_beebe",
      "…or by the id that rung used to have")
out = room.set_seat_difficulty(host_token, seat_token, ai_indices[2], "medium")
check(out.get("ok"), "…or by an old word")

bad = room.set_seat_difficulty(host_token, seat_token, ai_indices[0], "impossible")
check(not bad.get("ok"), "a grade that does not exist is refused, not quietly swapped",
      json.dumps(bad))
check(room.seats[ai_indices[0]].difficulty == "charles_darwin",
      "…and the seat keeps the grade it had")

nothost = room.set_seat_difficulty("not-the-host", None, ai_indices[0], "f")
check(not nothost.get("ok"), "only the host may change a grade")

human_seat = room.host_seat().index
onhuman = room.set_seat_difficulty(host_token, seat_token, human_seat, "s")
check(not onhuman.get("ok"), "a person does not have a grade")

with room.cond:
    snap = room.seat_snapshot_locked()
by_index = {s["index"]: s for s in snap}
check(by_index[ai_indices[0]]["grade"] == "Charles Darwin",
      "the seat snapshot carries the printed name")
check(by_index[ai_indices[0]]["grade_elo"] == fish.bot_grade_elo("charles_darwin"),
      "…and the Elo behind it, so the lobby need not know the ladder")
check(by_index[ai_indices[0]]["difficulty"] == "charles_darwin",
      "…and the id, so the list can preselect the right row")
check(by_index[ai_indices[0]]["grade_tier"] == "S++",
      "…and the badge tier, which a name cannot get from its first letter")


# ── 6. a room saved before grades existed ───────────────────────────────────
section("a room that comes back from disk saying 'medium'")

# The restore path normalizes whatever string it finds, so test that directly:
# it is the one line standing between an old save file and a crash.
check(fish.normalize_bot_grade("medium") in fish.AI_DIFFICULTY_CONFIGS,
      "an old save's 'medium' resolves to a real grade")
check(fish.normalize_bot_grade("Easy") in fish.AI_DIFFICULTY_CONFIGS,
      "…whatever case it was written in")
check(fish.normalize_bot_grade("wobble") == fish.DEFAULT_BOT_GRADE,
      "…and a corrupted one falls back rather than failing to load the room")


# ── 7. the Head to Head table, through the real HTTP handler ───────────────────
section("a Head to Head opens a four-seat table, already running")

server, thread = None, None
try:
    import threading
    server = mp.StableThreadingHTTPServer(("127.0.0.1", 0), mp.MultiplayerHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def post(path, body):
        req = urllib.request.Request(
            base + path, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return json.loads(e.read().decode())

    def get(path):
        with urllib.request.urlopen(base + path, timeout=15) as r:
            return json.loads(r.read().decode())

    ladder = get("/api/bot_grades")
    check(ladder.get("ok") and len(ladder.get("grades", [])) == 10,
          "the server publishes the whole ladder, so no client carries a copy")
    check([g["grade"] for g in ladder["grades"]] == WANTED,
          "…in ladder order, weakest first")
    check(ladder.get("default") == fish.DEFAULT_BOT_GRADE,
          "…and says which grade is the default")
    check(ladder.get("locked") == {"giant_squid": "story"},
          "…and which grades have to be earned, so no client keeps its own list",
          json.dumps(ladder.get("locked")))
    squid_row = next((g for g in ladder["grades"] if g["id"] == "giant_squid"), None)
    check(squid_row is not None,
          "the Squid is PUBLISHED, not hidden: a locked thing nobody can see "
          "is not a reward, it is just an absence")
    check(squid_row and squid_row.get("unlock") == "story",
          "…and every row carries its own gate")
    check(all(g.get("tier") for g in ladder["grades"]),
          "every published rung carries its badge tier")
    check([g.get("requires", "") for g in ladder["grades"]]
          == [""] + list(fish.BOT_GRADE_ORDER)[:-1],
          "…and the rung you have to beat to reach it",
          str([g.get("requires") for g in ladder["grades"]]))

    # Asked for by the ids the ladder USED to use, which is what an older
    # client still sends: the table has to come back with the rungs those ids
    # have always meant.
    wanted = ["d", "b", "ss"]
    wanted_ids = ["edward_forbes", "william_beebe", "jacques_cousteau"]
    made = post("/api/rooms", {
        "host_name": "Diver", "total_players": 4, "human_players": 1,
        "ai_players": 3, "visibility": "private", "password": "ABCDE",
        "ai_difficulties": wanted, "start_now": True,
    })
    check(made.get("ok"), "the table opens", json.dumps(made)[:200])
    check(made.get("ai_difficulties") == wanted_ids,
          "the three grades asked for are the three grades seated",
          str(made.get("ai_difficulties")))
    check(made.get("ai_grades") == ["Edward Forbes", "William Beebe", "Jacques Cousteau"],
          "…and it says which rungs those are, by name")
    check(made.get("ai_elos") == [fish.bot_grade_elo(g) for g in wanted],
          "…and the Elo of each")
    check(made.get("started") is True,
          "the game is already running: a Head to Head has nobody to wait for",
          made.get("start_error", ""))

    made_room = mp.ROOMS.get(made["room_id"])
    check(made_room is not None, "the room is really there")
    with made_room.cond:
        kinds = [s.kind for s in made_room.seats]
        bots = [(s.claimed_name, s.difficulty) for s in made_room.seats if s.kind == "ai"]
        phase = made_room.phase
    check(len(kinds) == 4 and kinds.count("human") == 1 and kinds.count("ai") == 3,
          "four seats: one person and three bots", str(kinds))
    # Starting a game shuffles the seats, so who sits where is not the request
    # order any more. What must survive is the PAIRING: the bot the screen
    # called Bot 2 and graded B has to still be Bot 2, and still be a B. If the
    # grades came unstuck from the names here, the Head to Head screen would be
    # describing a table that does not exist.
    check(len(bots) == 3, "three bots at the table")
    by_name = dict(bots)
    check(by_name == {"Bot 1": wanted_ids[0], "Bot 2": wanted_ids[1],
                      "Bot 3": wanted_ids[2]},
          "each named bot kept the grade it was given, wherever it ended up sitting",
          str(sorted(bots)))
    check(len({g for _, g in bots}) == 3,
          "the three bots are three DIFFERENT grades, which is the whole idea")
    check(phase != "lobby", "and the table is past the lobby", phase)

    # The grade has to survive the API too, not just the room object: the
    # in-game seat badge reads it straight off this payload.
    with urllib.request.urlopen(
            f"{base}/api/rooms/{made['room_id']}/state"
            f"?seat_token={made['seat_token']}", timeout=15) as r:
        state = json.loads(r.read().decode())
    check(state.get("ok"), "the table's state can be read back")
    seats = state.get("seats") or state.get("room", {}).get("seats") or []
    ai_rows = [s for s in seats if s.get("kind") == "ai"]
    check(len(ai_rows) == 3, "three bot seats come back over the API", str(len(ai_rows)))
    check(all(row.get("grade") for row in ai_rows),
          "every bot seat carries its printed grade",
          str([row.get("grade") for row in ai_rows]))
    check(all(int(row.get("grade_elo") or 0) > 0 for row in ai_rows),
          "…and its Elo, so the seat badge needs no second request")
    check({row["grade"] for row in ai_rows}
          == {"Edward Forbes", "William Beebe", "Jacques Cousteau"},
          "…and they are the three that were asked for",
          str(sorted(row.get("grade") for row in ai_rows)))
    check(all(not s.get("grade") for s in seats if s.get("kind") == "human"),
          "a person's seat carries no grade")

    # ── Where the Squid's gate is, and where it is not ──────────────────────
    # This is deliberate, and it is written down here so nobody later reads it
    # as a hole. The unlock is a REWARD gate, enforced on the client against
    # the player's own collection, exactly like every avatar, background and
    # emote in this game. The server will seat a Squid if a hand-written
    # request asks for one, because the server does not know who is asking:
    # room creation carries no signed-in identity, and checking one would mean
    # a Firestore read per request on a quota that has already run out once.
    #
    # What is actually at stake is a surprise, not a secret. The test is here
    # so that if someone ever DOES add identity to room creation, they find
    # this and tighten it on purpose rather than by accident.
    direct = post("/api/rooms", {
        "host_name": "Curious", "total_players": 4, "human_players": 1,
        "ai_players": 3, "ai_difficulties": ["giant_squid", "c", "d"],
    })
    check(direct.get("ok"), "the server answers a request that asks for the Squid")
    check(direct.get("ai_grades", [None])[0] == "Giant Squid",
          "…and seats it: the gate is on the client, by design, and the code "
          "says so where it is enforced",
          str(direct.get("ai_grades")))

    # A hand-written request must not be able to seat a bot that does not exist.
    junk = post("/api/rooms", {
        "host_name": "Diver", "total_players": 4, "human_players": 1,
        "ai_players": 3, "ai_difficulties": ["nonsense", "", None],
    })
    check(junk.get("ok"), "a request with junk grades still opens a table")
    check(all(g == fish.DEFAULT_BOT_GRADE for g in junk.get("ai_difficulties", [])),
          "…with the default grade rather than a broken bot",
          str(junk.get("ai_difficulties")))
finally:
    if server is not None:
        server.shutdown()
        server.server_close()

print(f"\n{'=' * 50}\nRESULT: {PASS} passed, {FAIL} failed")
raise SystemExit(1 if FAIL else 0)
