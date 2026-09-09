"""scripts/set_clan_points.py against the in-memory Firestore fake, seeded with
Goby Gang's REAL live shape (555.5 points, 498 of them from 32 challenges).

The point of this suite is the promise the tool makes: that setting one field
makes the new number the one every reader sees. So it does not check the write
in isolation, it re-reads through the SAME functions the site reads through
(_clan_card, _leaderboard_rows) and asserts what those hand back.

Run:  python3 test_set_clan_points.py
"""
import importlib.util
import os
import sys
from urllib.parse import urlparse

# Reuse test_clan_server's fake firebase_admin + FakeDB WITHOUT running its
# suite: that file is top-level code that ends in sys.exit, so it is executed
# only down to the end of the harness (class FakeDB), never past it.
import types as _types
H = _types.ModuleType("clan_fake_harness")
_lines = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "test_clan_server.py")).read().splitlines(True)
_end = next(i for i, l in enumerate(_lines) if l.startswith("# \u2500\u2500 Load clan_server"))
exec("".join(_lines[:_end]), H.__dict__)

import clan_server as cs
import multiplayer_server as ms

FAILED = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: {got!r}" + ("" if ok else f"  (want {want!r})"))
    if not ok:
        FAILED.append(label)


def load_tool():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "scripts", "set_clan_points.py")
    spec = importlib.util.spec_from_file_location("set_clan_points", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SID = "2026-Q3"
CLAN_ID = "c64f5e450ba"
# Six members whose contributions add up to the real 555.5 total.
MEMBER_POINTS = {"u1": 210.5, "u2": 150, "u3": 95, "u4": 60, "u5": 28, "u6": 12}


def seed(db):
    db.store["clans/" + CLAN_ID] = {
        "name": "Goby Gang", "icon": "/avatars/mandarin-goby.png",
        "icon_name": "Mandarin Goby", "privacy": "public", "description": "",
        "xp": 0, "lifetime": {"points": 555.5},
        "members": {u: {"role": "member"} for u in MEMBER_POINTS},
        "activity": [],
        "seasons": {SID: {
            "points": 555.5, "comp_wins": 2, "comp_losses": 2, "casual_wins": 37,
            "games": 60, "challenge_points": 498, "challenges_completed": 32,
            "trade_points": 0, "gameplay_points": 57.5, "bonus_points": 0,
            "challenges_done": [f"ch{i}" for i in range(32)],
            "last_gain_ts": 1788000000,
            "contrib": {u: {"name": u.upper(), "points": p, "game_points": p,
                            "trade_points": 0, "challenge_points": 0}
                        for u, p in MEMBER_POINTS.items()},
        }},
    }
    # A second clan, so ranking is a real comparison and not a single row.
    db.store["clans/cd0effcff32"] = {
        "name": "Beef", "members": {"z1": {"role": "owner"}}, "xp": 0,
        "lifetime": {"points": 1},
        "seasons": {SID: {"points": 1, "contrib": {}, "last_gain_ts": 1788000001}},
    }


def run(mod, db, argv):
    """Drive the tool's main() exactly as the command line would."""
    cs._LB_WARM.invalidate()
    old_argv, old_get_fs = sys.argv, cs._get_firestore
    sys.argv = ["set_clan_points.py"] + argv
    mod.firebase_admin = H.fake_admin
    cs._get_firestore = lambda: db
    try:
        mod.main()
    except SystemExit as exc:
        if exc.code:
            raise
    finally:
        sys.argv, cs._get_firestore = old_argv, old_get_fs


def main():
    mod = load_tool()
    # The tool builds its own client; hand it the fake instead.
    H.fake_fs.client = lambda: DB
    ms.get_season_id = lambda: SID

    global DB
    DB = H.FakeDB()
    seed(DB)

    print("\n1. dry run writes NOTHING")
    run(mod, DB, ["--clan", "Goby Gang", "--points", "95", "--dry-run"])
    slot = DB.store["clans/" + CLAN_ID]["seasons"][SID]
    check("points untouched by --dry-run", slot["points"], 555.5)
    check("no ledger written", [k for k in DB.store if "/ledger/" in k], [])

    print("\n2. the real set: 555.5 -> 95")
    run(mod, DB, ["--clan", "Goby Gang", "--points", "95",
                  "--note", "season correction"])
    clan = DB.store["clans/" + CLAN_ID]
    slot = clan["seasons"][SID]
    check("stored season points", slot["points"], 95)
    check("delta parked in admin_adjust", slot["admin_adjust"], -460.5)

    print("\n3. what every reader gets back (the actual promise)")
    card = cs._clan_card(CLAN_ID, clan, SID)
    check("_clan_card points (hero, browse, home, rival)", card["points"], 95)
    rows = cs._leaderboard_rows(DB, SID, fresh=True)
    goby = [r for r in rows if r["id"] == CLAN_ID][0]
    check("leaderboard row points (site + marketing homepage)", goby["points"], 95)
    check("still rank 1 at 95", goby["rank"], 1)
    check("second place unchanged", [r["points"] for r in rows if r["id"] != CLAN_ID][0], 1)

    print("\n4. history is left alone")
    check("challenge_points", slot["challenge_points"], 498)
    check("challenges_completed", slot["challenges_completed"], 32)
    check("challenges_done still 32", len(slot["challenges_done"]), 32)
    check("games", slot["games"], 60)
    check("casual_wins", slot["casual_wins"], 37)
    check("contrib NOT scaled by default", slot["contrib"]["u1"]["points"], 210.5)
    check("lifetime NOT moved by default", clan["lifetime"]["points"], 555.5)

    print("\n5. the correction is on the record")
    led = [v for k, v in DB.store.items() if "/ledger/" in k]
    check("one ledger row", len(led), 1)
    check("ledger before", led[0]["before"], 555.5)
    check("ledger after", led[0]["after"], 95)
    check("ledger delta", led[0]["points"], -460.5)
    check("activity feed line", clan["activity"][0]["text"],
          "⚙️ Clan Points set to 95 by an admin: season correction")

    print("\n6. setting the SAME value again is a no-op, not a second -460.5")
    run(mod, DB, ["--clan", "Goby Gang", "--points", "95"])
    slot = DB.store["clans/" + CLAN_ID]["seasons"][SID]
    check("points still 95", slot["points"], 95)
    check("admin_adjust not double-counted", slot["admin_adjust"], -460.5)
    check("still one ledger row", len([k for k in DB.store if "/ledger/" in k]), 1)

    print("\n7. --scale-contrib (the other reading of the ask)")
    DB2 = H.FakeDB(); seed(DB2)
    globals()["DB"] = DB2
    H.fake_fs.client = lambda: DB2
    run(mod, DB2, ["--clan", "Goby Gang", "--points", "95", "--scale-contrib"])
    s2 = DB2.store["clans/" + CLAN_ID]["seasons"][SID]
    check("total", s2["points"], 95)
    contrib_sum = cs._num(sum(c["points"] for c in s2["contrib"].values()))
    check("contributions sum EXACTLY to the total", contrib_sum, 95)
    check("remainder absorbed by the largest contributor", s2["contrib"]["u1"]["points"], 35.9)
    check("u4 (60) drops under MVP 25", s2["contrib"]["u4"]["points"] < cs.MVP_MIN_POINTS, True)
    check("u5 (28) drops under reward min 10",
          s2["contrib"]["u5"]["points"] < cs.SEASON_REWARD_MIN_POINTS, True)

    print("\n8. raising works too (the tool is not lower-only)")
    DB3 = H.FakeDB(); seed(DB3)
    globals()["DB"] = DB3
    H.fake_fs.client = lambda: DB3
    run(mod, DB3, ["--clan", CLAN_ID, "--points", "700", "--lifetime"])
    c3 = DB3.store["clans/" + CLAN_ID]
    check("raised by clan id", c3["seasons"][SID]["points"], 700)
    check("--lifetime moved the lifetime total", c3["lifetime"]["points"], 700)

    print("\n9. the exact command Tim chose: --scale-contrib --lifetime")
    DB4 = H.FakeDB(); seed(DB4)
    globals()["DB"] = DB4
    H.fake_fs.client = lambda: DB4
    run(mod, DB4, ["--clan", "Goby Gang", "--points", "95",
                   "--scale-contrib", "--lifetime", "--note", "correction"])
    c4 = DB4.store["clans/" + CLAN_ID]
    s4 = c4["seasons"][SID]
    check("season total", s4["points"], 95)
    check("lifetime total", c4["lifetime"]["points"], 95)
    check("contributions sum to exactly 95",
          cs._num(sum(c["points"] for c in s4["contrib"].values())), 95)
    # Each member's printed breakdown must not exceed their own new total.
    worst = max(s4["contrib"].values(),
                key=lambda c: c["game_points"] - c["points"])
    check("no member's game_points exceeds their own points",
          worst["game_points"] <= worst["points"], True)
    check("u1 breakdown scaled with the total", s4["contrib"]["u1"]["game_points"], 35.9)
    check("clan card reads 95", cs._clan_card(CLAN_ID, c4, SID)["points"], 95)
    rows4 = cs._leaderboard_rows(DB4, SID, fresh=True)
    check("leaderboard reads 95", [r for r in rows4 if r["id"] == CLAN_ID][0]["points"], 95)
    check("still rank 1", [r for r in rows4 if r["id"] == CLAN_ID][0]["rank"], 1)
    check("challenge history preserved", s4["challenges_completed"], 32)

    print("\n10. the LIVE path: POST /api/admin/clan-set-points")
    # The script needs a service account and no machine here has one, so the
    # correction that actually reaches production goes over HTTP to the server,
    # which holds the credentials. Same clan_server function underneath; what
    # is pinned here is the surface: the key, the dry run, resolving by name,
    # and the read-back that says what the site will show.
    DB5 = H.FakeDB(); seed(DB5)
    globals()["DB"] = DB5
    H.fake_fs.client = lambda: DB5
    old_get_fs, old_key = cs._get_firestore, os.environ.get("ADMIN_RECOVERY_KEY")
    cs._get_firestore = lambda: DB5
    os.environ["ADMIN_RECOVERY_KEY"] = "test-key"

    class FakeHandler:
        def __init__(self):
            self.sent, self.status = None, 200

        def _send_json(self, payload, status=200):
            self.sent, self.status = payload, status

    def post(body):
        h = FakeHandler()
        took = cs.handle_post(h, urlparse("/api/admin/clan-set-points"), body)
        return took, h

    try:
        took, h = post({"admin_key": "wrong", "clan": "Goby Gang", "points": 95})
        check("wrong key is refused", (took, h.status, h.sent.get("error")),
              (True, 403, "unauthorized"))
        check("refused key wrote nothing",
              DB5.store["clans/" + CLAN_ID]["seasons"][SID]["points"], 555.5)

        took, h = post({"admin_key": "test-key", "clan": "Nope Gang", "points": 95})
        check("unknown clan is a 404, not a write", (h.status, h.sent.get("error")),
              (404, "no_clan"))

        _, h = post({"admin_key": "test-key", "clan": "Goby Gang", "points": 95,
                     "scale_contrib": True, "dry_run": True})
        prev = h.sent["preview"]
        check("dry run previews before -> after", (prev["before"], prev["after"]),
              (555.5, 95))
        check("dry run names who loses season coins",
              sorted(m["name"] for m in prev["members"] if m["loses_coins"]),
              ["U5", "U6"])
        check("dry run wrote nothing",
              DB5.store["clans/" + CLAN_ID]["seasons"][SID]["points"], 555.5)

        cs._LB_WARM.invalidate()
        # Warm the standings cache first, exactly as a live server's is: the
        # endpoint has to drop it, or the site keeps answering 555.5 after the
        # write, which looks precisely like the write never happened.
        cs._leaderboard_rows(DB5, SID)
        _, h = post({"admin_key": "test-key", "clan": "Goby Gang", "points": 95,
                     "scale_contrib": True, "lifetime": True, "note": "correction"})
        out = h.sent
        check("endpoint reports ok", out.get("ok"), True)
        check("stored points", DB5.store["clans/" + CLAN_ID]["seasons"][SID]["points"], 95)
        check("read back through _clan_card", out.get("now_reads"), 95)
        check("read back through the standings", out.get("leaderboard_reads"), 95)
        check("still rank 1", out.get("rank"), 1)
        check("lifetime moved", DB5.store["clans/" + CLAN_ID]["lifetime"]["points"], 95)
        check("ledger row written", len([k for k in DB5.store if "/ledger/" in k]), 1)
        # The cache the endpoint dropped: a fresh reader must see 95, not the
        # 555.5 that was warmed a moment ago.
        cached = [r for r in cs._leaderboard_rows(DB5, SID) if r["id"] == CLAN_ID][0]
        check("standings cache no longer serves the old total", cached["points"], 95)

        _, h = post({"admin_key": "test-key", "clan": "Goby Gang", "points": 95})
        check("running it twice is a no-op", h.sent.get("noop"), True)
        check("no second ledger row", len([k for k in DB5.store if "/ledger/" in k]), 1)

        _, h = post({"admin_key": "test-key", "clan": "Goby Gang"})
        check("missing points is refused", (h.status, h.sent.get("error")),
              (400, "points_required"))
        check("history survived the live path",
              DB5.store["clans/" + CLAN_ID]["seasons"][SID]["challenges_completed"], 32)
    finally:
        cs._get_firestore = old_get_fs
        if old_key is None:
            os.environ.pop("ADMIN_RECOVERY_KEY", None)
        else:
            os.environ["ADMIN_RECOVERY_KEY"] = old_key

    print("\n" + ("ALL PASS" if not FAILED else f"{len(FAILED)} FAILED: {FAILED}"))
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
