"""test_level_pass_server.py, the Level Pass, where it pays out.

Every tier on the track hands over real currency: Critter Coins, Streak
Shields, backgrounds, XP boosts. So the tests that matter are not "does the
page render", they are the four things that must never happen:

  1. A tier paying twice.                → the ledger create() guard
  2. A tier paying at a level the account has not reached.
                                          → the level is re-derived from the
                                            account's OWN total_xp, and the
                                            request carries no level at all
  3. A payout that overwrites data it was only supposed to append to.
                                          → ArrayUnion, not read-modify-write
  4. A tier that CANNOT pay quietly burning itself.
                                          → no ledger entry written, so it
                                            stays claimable later

Plus the drift check that keeps the track honest: the milestone critters on the
pass have to be exactly the level-gated avatars in preview-app.js. Add a new
level avatar to the client and forget the pass, and this fails.

    python3 test_level_pass_server.py
"""
from __future__ import annotations

import ast
import copy
import os
import re
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import level_pass_server as lp  # noqa: E402

SERVER_PY = os.path.join(ROOT, "multiplayer_server.py")
PREVIEW_JS = os.path.join(ROOT, "multiplayer", "client", "js", "preview-app.js")


# ══════════════════════════════════════════════════════════════════════════
#  In-memory Firestore
#  Faithful in the two ways this module depends on: set(merge=True) merges
#  nested maps key-by-key, and create() on an existing id RAISES, which is
#  the entire "cannot be claimed twice" guarantee.
# ══════════════════════════════════════════════════════════════════════════
class ArrayUnion:
    """Stands in for firestore.ArrayUnion. Appending through this, rather than
    writing back a list the module just read and normalised: is what stops a
    payout deleting entries the normaliser did not recognise."""
    def __init__(self, items):
        self.items = list(items)


def _deep_merge(dst: dict, src: dict) -> dict:
    for k, v in src.items():
        if isinstance(v, ArrayUnion):
            cur = dst.get(k)
            cur = list(cur) if isinstance(cur, list) else []
            for item in v.items:
                if item not in cur:
                    cur.append(item)
            dst[k] = cur
        elif isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_merge(dst[k], v)
        else:
            dst[k] = v
    return dst


class FakeSnap:
    def __init__(self, doc_id, data):
        self.id = doc_id
        self._data = data
        self.exists = data is not None

    def to_dict(self):
        return copy.deepcopy(self._data) if self._data is not None else None


class AlreadyExists(Exception):
    pass


class FakeDoc:
    def __init__(self, coll, doc_id):
        self._coll = coll
        self.id = doc_id

    def get(self, transaction=None):
        return FakeSnap(self.id, self._coll._docs.get(self.id))

    def set(self, data, merge=False):
        cur = self._coll._docs.get(self.id)
        if merge and isinstance(cur, dict):
            _deep_merge(cur, copy.deepcopy(data))
        else:
            self._coll._docs[self.id] = _deep_merge({}, copy.deepcopy(data))

    def create(self, data):
        if self.id in self._coll._docs:
            raise AlreadyExists(self.id)
        self._coll._docs[self.id] = copy.deepcopy(data)


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def limit(self, _n):
        return self

    def stream(self):
        return list(self._rows)

    def get(self):
        return list(self._rows)


class FakeColl:
    def __init__(self, db, name):
        self._db = db
        self.name = name
        self._docs = {}

    def document(self, doc_id):
        return FakeDoc(self, doc_id)

    def where(self, field, op, value):
        assert op == "=="
        return _FakeQuery([FakeSnap(k, v) for k, v in self._docs.items()
                           if (v or {}).get(field) == value])

    def stream(self):
        return [FakeSnap(k, v) for k, v in self._docs.items()]


class FakeTxn:
    """Writes land immediately. Good enough here: the fake runs the body once,
    and the guarantee under test: create() refusing the second run: is
    reproduced exactly."""
    def __init__(self, db):
        self._db = db

    def set(self, ref, data, merge=False):
        ref.set(data, merge=merge)

    def create(self, ref, data):
        ref.create(data)


class FakeDb:
    def __init__(self):
        self._colls = {}

    def collection(self, name):
        if name not in self._colls:
            self._colls[name] = FakeColl(self, name)
        return self._colls[name]

    def transaction(self):
        return FakeTxn(self)


# ══════════════════════════════════════════════════════════════════════════
#  The real level curve, injected exactly the way multiplayer_server does
# ══════════════════════════════════════════════════════════════════════════
def _read_level_totals():
    src = open(SERVER_PY, encoding="utf-8").read()
    m = re.search(r"^LEVEL_XP_TOTALS = (\[[^\]]*\])", src, re.M)
    assert m, "LEVEL_XP_TOTALS not found in multiplayer_server.py"
    return ast.literal_eval(m.group(1))


LEVEL_TOTALS = _read_level_totals()
MAX_LEVEL = len(LEVEL_TOTALS)


def level_progress(total_xp):
    try:
        xp = max(0, int(total_xp))
    except (TypeError, ValueError):
        xp = 0
    lvl = 1
    for i in range(MAX_LEVEL, 0, -1):
        if xp >= LEVEL_TOTALS[i - 1]:
            lvl = i
            break
    if lvl >= MAX_LEVEL:
        cap = LEVEL_TOTALS[-1]
        return (MAX_LEVEL, cap, cap)
    start, nxt = LEVEL_TOTALS[lvl - 1], LEVEL_TOTALS[lvl]
    return (lvl, max(0, xp - start), max(1, nxt - start))


BACKGROUNDS = [
    "/backgrounds/bg-kelp.png",
    "/backgrounds/bg-coral-reef.png",
    "/backgrounds/bg-artificial-reef.png",
]


def xp_for_level(level):
    """Exactly enough XP to sit at `level`."""
    return LEVEL_TOTALS[max(1, min(MAX_LEVEL, level)) - 1]


class PassTestBase(unittest.TestCase):
    def setUp(self):
        self.db = FakeDb()
        lp.init(
            get_firestore=lambda: self.db,
            verify_token=lambda tok: {"uid": tok[5:]} if tok.startswith("good:") else None,
            level_for_xp=level_progress,
            level_totals=LEVEL_TOTALS,
            background_paths=list(BACKGROUNDS),
        )
        # The two firestore helpers, swapped for the fake's equivalents.
        lp._transactional = lambda: (lambda fn: fn)          # type: ignore[assignment]
        lp._array_union = lambda: ArrayUnion                 # type: ignore[assignment]

    def make_user(self, uid="u1", level=1, coins=0, **extra):
        doc = {
            "nickname": uid.upper(),
            "stats": {"total_xp": xp_for_level(level), "critter_coins": coins},
        }
        doc.update(extra)
        self.db.collection("users")._docs[uid] = doc
        return doc

    def user(self, uid="u1"):
        return self.db.collection("users").document(uid).get().to_dict()

    def coins(self, uid="u1"):
        return int((self.user(uid).get("stats") or {}).get("critter_coins") or 0)

    def ledger_ids(self):
        return sorted(self.db.collection("level_pass_claims")._docs.keys())

    def tier_of(self, level, rtype):
        for t in lp.track():
            if t["level"] == level and t["type"] == rtype:
                return t
        raise AssertionError(f"no {rtype} tier at level {level}")

    def first_tier_of(self, rtype):
        """The lowest-level tier of a given type.

        Tests ask for "a shield tier", not "the shield tier at level 5". The
        track's LEVELS are an economy dial and get retuned; the rules being
        tested here are not. Looking tiers up by type is what stops a rebalance
        showing up as a wall of failing tests about claiming and caps."""
        for t in sorted(lp.track(), key=lambda x: x["level"]):
            if t["type"] == rtype:
                return t
        raise AssertionError(f"the track has no {rtype} tier at all")

    def level_with_a_claimable_below(self, rtype):
        """A level high enough to have claimed both `rtype` and a coins tier,
        for the claim-all sweep tests."""
        t = self.first_tier_of(rtype)
        coins = self.first_tier_of("coins")
        return max(t["level"], coins["level"])


# ══════════════════════════════════════════════════════════════════════════
#  THE TRACK ITSELF
# ══════════════════════════════════════════════════════════════════════════
class TrackShape(PassTestBase):
    def test_tier_ids_are_unique(self):
        ids = [t["id"] for t in lp.track()]
        self.assertEqual(len(ids), len(set(ids)),
                         "two tiers share an id, they would share a ledger doc, "
                         "so claiming one would silently claim the other")

    def test_levels_are_in_range(self):
        for t in lp.track():
            self.assertGreaterEqual(t["level"], 1)
            self.assertLessEqual(t["level"], MAX_LEVEL,
                                 f"tier {t['id']} sits above the level cap")

    def test_at_most_one_reward_per_level_except_the_finale(self):
        by_level = {}
        for t in lp.track():
            by_level.setdefault(t["level"], []).append(t)
        for level, tiers in by_level.items():
            if level == MAX_LEVEL:
                continue
            self.assertEqual(len(tiers), 1,
                             f"level {level} carries {len(tiers)} rewards, the track "
                             "is meant to be a steady drip, not a pile")

    def test_critter_tiers_are_not_claimable(self):
        # Claiming a critter here would hand back one that was TRADED AWAY,
        # bypassing the re-earn rule the trading system depends on.
        for t in lp.track():
            if t["type"] == "critter":
                self.assertFalse(t["claimable"],
                                 f"{t['id']} is claimable, that reopens the traded-away hole")

    def test_every_claimable_tier_has_a_label_and_a_blurb(self):
        for t in lp.track():
            self.assertTrue(t["label"].strip(), f"{t['id']} has no label")
            self.assertTrue(t["blurb"].strip(), f"{t['id']} has no blurb")
            self.assertTrue(t["icon"].strip(), f"{t['id']} has no icon")

    TRACK_COIN_BUDGET = 4000

    def test_the_whole_track_pays_exactly_the_coin_budget(self):
        """4,000 Critter Coins across all 100 levels, and not a coin more.

        This is a deliberate ceiling, not an incidental total: a background is
        1,000 coins and a skin 2,000, so the whole climb from 1 to 100 is worth
        about two Store items. Retuning individual tiers is fine; the tiers
        have to be rebalanced against each other rather than the budget being
        quietly raised, which is what this assertion is here to force."""
        total = sum(t["amount"] for t in lp.track() if t["type"] == "coins")
        self.assertEqual(total, self.TRACK_COIN_BUDGET,
                         f"the track pays {total} coins, budget is "
                         f"{self.TRACK_COIN_BUDGET}")

    def test_milestone_critters_match_the_client_level_unlocks(self):
        """THE drift check.

        The critters on the pass must be exactly the avatars preview-app.js
        gates behind a level. Add a level avatar to the client and forget the
        pass, and the track quietly stops showing it."""
        with open(PREVIEW_JS, encoding="utf-8") as fh:
            src = fh.read()
        # Split ANIMAL_AVATARS into one slice per entry FIRST. Matching across
        # the whole file lets a `{ id:… }` with a non-level unlock swallow the
        # NEXT entry's `unlock:{type:"level"}`, which is exactly what a lazy
        # cross-entry regex did here, and it read Peruvian Pelican as the
        # level-80 avatar.
        starts = [m.start() for m in re.finditer(r'\{\s*id:"[a-z0-9-]+",\s*name:"', src)]
        client = {}
        for i, start in enumerate(starts):
            chunk = src[start:(starts[i + 1] if i + 1 < len(starts) else len(src))]
            g = re.search(r'unlock:\{\s*type:"level",\s*goal:(\d+)', chunk)
            if not g:
                continue
            name = re.search(r'name:"([^"]+)"', chunk)
            img = re.search(r'img:"(/avatars/[^"]+)"', chunk)
            if name and img:
                client[int(g.group(1))] = (name.group(1), img.group(1))
        self.assertTrue(client, "could not read any level-gated avatars from preview-app.js")

        pass_critters = {t["level"]: (t["critter"], t["img"])
                         for t in lp.track() if t["type"] == "critter"}
        self.assertEqual(
            sorted(client.keys()), sorted(pass_critters.keys()),
            f"level-gated avatars {sorted(client.keys())} but the pass shows "
            f"{sorted(pass_critters.keys())}, one of them was changed alone")
        for level, (name, img) in client.items():
            self.assertEqual(pass_critters[level][0], name,
                             f"level {level}: pass says {pass_critters[level][0]}, client says {name}")
            self.assertEqual(pass_critters[level][1], img,
                             f"level {level}: pass art {pass_critters[level][1]} != client {img}")

    def test_coins_cluster_around_the_critter_levels(self):
        """The brief was that coins sit AROUND the critter unlocks. Each critter
        level should have a coin tier within two levels of it."""
        coin_levels = {t["level"] for t in lp.track() if t["type"] == "coins"}
        for t in lp.track():
            if t["type"] != "critter":
                continue
            near = any(abs(t["level"] - c) <= 2 for c in coin_levels)
            self.assertTrue(near, f"critter level {t['level']} has no coins near it")


# ══════════════════════════════════════════════════════════════════════════
#  CLAIMING
# ══════════════════════════════════════════════════════════════════════════
class Claiming(PassTestBase):
    def test_coins_land_once_and_only_once(self):
        tier = self.first_tier_of("coins")
        self.make_user(level=5, coins=10)

        first = lp.claim(self.db, "u1", tier["id"])
        self.assertTrue(first["ok"], first)
        self.assertEqual(self.coins(), 10 + tier["amount"])

        second = lp.claim(self.db, "u1", tier["id"])
        self.assertFalse(second["ok"])
        self.assertEqual(second["error"], "already_claimed")
        self.assertEqual(self.coins(), 10 + tier["amount"],
                         "the second claim paid out again")
        self.assertEqual(len(self.ledger_ids()), 1)

    def test_a_level_you_have_not_reached_pays_nothing(self):
        tier = sorted((t for t in lp.track() if t["type"] == "coins"),
                      key=lambda x: x["level"])[-1]
        self.make_user(level=5, coins=0)
        res = lp.claim(self.db, "u1", tier["id"])
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"], "level_locked")
        self.assertEqual(self.coins(), 0)
        self.assertEqual(self.ledger_ids(), [],
                         "a refused claim wrote a ledger entry, the tier is now "
                         "unclaimable forever")

    def test_the_level_comes_from_the_account_not_the_request(self):
        """There is no level in a claim request at all, the only way to move
        the gate is to actually have the XP. Prove it by moving total_xp."""
        tier = sorted((t for t in lp.track() if t["type"] == "coins"),
                      key=lambda x: x["level"])[-1]
        self.make_user(level=5)
        self.assertFalse(lp.claim(self.db, "u1", tier["id"])["ok"])

        # Same request, same tier, only the stored XP changed.
        self.db.collection("users")._docs["u1"]["stats"]["total_xp"] = \
            xp_for_level(tier["level"])
        self.assertTrue(lp.claim(self.db, "u1", tier["id"])["ok"])

    def test_unknown_and_unclaimable_tiers_are_refused(self):
        self.make_user(level=100)
        self.assertEqual(lp.claim(self.db, "u1", "L999")["error"], "unknown_tier")
        critter = next(t for t in lp.track() if t["type"] == "critter")
        self.assertEqual(lp.claim(self.db, "u1", critter["id"])["error"], "not_claimable")
        self.assertEqual(self.ledger_ids(), [])

    def test_a_missing_account_is_refused(self):
        tier = self.first_tier_of("coins")
        self.assertEqual(lp.claim(self.db, "nobody", tier["id"])["error"], "no_account")

    def test_claiming_does_not_disturb_the_rest_of_stats(self):
        tier = self.first_tier_of("coins")
        self.make_user(level=5, coins=5)
        self.db.collection("users")._docs["u1"]["stats"]["daily_streak"] = 12
        lp.claim(self.db, "u1", tier["id"])
        self.assertEqual(self.user()["stats"]["daily_streak"], 12,
                         "the coin write clobbered the rest of the stats map")


class Consumables(PassTestBase):
    def test_shields_stack_up_to_the_cap_then_refuse(self):
        tier = self.first_tier_of("shield")
        self.make_user(level=tier["level"], streak_shields=lp.MAX_SHIELDS)
        res = lp.claim(self.db, "u1", tier["id"])
        self.assertEqual(res["error"], "shields_full")
        self.assertEqual(self.ledger_ids(), [],
                         "a refused shield burned the tier")

        self.db.collection("users")._docs["u1"]["streak_shields"] = 0
        self.assertTrue(lp.claim(self.db, "u1", tier["id"])["ok"])
        self.assertEqual(self.user()["streak_shields"], 1)

    def test_boost_and_reroll_tiers_grant_inventory_not_an_active_effect(self):
        boost = self.first_tier_of("boost")
        reroll = self.first_tier_of("reroll")
        self.make_user(level=max(boost["level"], reroll["level"]))
        self.assertTrue(lp.claim(self.db, "u1", boost["id"])["ok"])
        self.assertTrue(lp.claim(self.db, "u1", reroll["id"])["ok"])
        doc = self.user()
        self.assertEqual(doc["xp_boosts"], 1)
        self.assertEqual(doc["weekly_reroll_tokens"], 1)
        # Claiming must NOT start the clock, that is what activation is for.
        self.assertNotIn("xp_boost_until", doc)


class BackgroundsAndStickers(PassTestBase):
    def test_background_picks_the_first_one_not_owned(self):
        tier = self.first_tier_of("background")
        self.make_user(level=50, unlocked_backgrounds=[BACKGROUNDS[0]])
        res = lp.claim(self.db, "u1", tier["id"])
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["granted"]["path"], BACKGROUNDS[1])
        self.assertIn(BACKGROUNDS[1], self.user()["unlocked_backgrounds"])

    def test_owning_every_background_leaves_the_tier_claimable(self):
        tier = self.first_tier_of("background")
        self.make_user(level=50, unlocked_backgrounds=list(BACKGROUNDS))
        res = lp.claim(self.db, "u1", tier["id"])
        self.assertEqual(res["error"], "backgrounds_full")
        self.assertEqual(self.ledger_ids(), [],
                         "the tier burned itself on a reward it could not give: "
                         "it must wait for the next batch instead")

    def test_a_background_grant_appends_and_never_rewrites_the_list(self):
        """The normaliser lowercases and drops anything unrecognised. Writing
        that normalised copy back would delete the odd entry, so grants go
        through ArrayUnion. This is the regression test for exactly that."""
        tier = self.first_tier_of("background")
        self.make_user(level=50,
                       unlocked_backgrounds=["/backgrounds/CUSTOM-Legacy.PNG"])
        self.assertTrue(lp.claim(self.db, "u1", tier["id"])["ok"])
        after = self.user()["unlocked_backgrounds"]
        self.assertIn("/backgrounds/CUSTOM-Legacy.PNG", after,
                      "the odd entry was deleted by the payout")
        self.assertIn(BACKGROUNDS[0], after)

    def test_sticker_falls_back_to_the_starter_mullet(self):
        """A brand-new account has an EMPTY unlocked_icons: mullet is the
        starter and is never written there. Without seeding it, the level-3
        sticker would have nothing to give a new player."""
        tier = self.first_tier_of("sticker")
        self.make_user(level=tier["level"])
        res = lp.claim(self.db, "u1", tier["id"])
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["granted"]["path"], "/avatars/mullet.png")

    def test_sticker_skips_critters_that_already_have_one(self):
        tier = self.first_tier_of("sticker")
        self.make_user(level=tier["level"],
                       unlocked_icons=["/avatars/blue-tang.png"],
                       emote_icons=["/avatars/mullet.png"])
        res = lp.claim(self.db, "u1", tier["id"])
        self.assertEqual(res["granted"]["path"], "/avatars/blue-tang.png")

    def test_sticker_refuses_without_burning_the_tier(self):
        tier = self.first_tier_of("sticker")
        self.make_user(level=tier["level"], emote_icons=["/avatars/mullet.png"])
        res = lp.claim(self.db, "u1", tier["id"])
        self.assertEqual(res["error"], "stickers_full")
        self.assertEqual(self.ledger_ids(), [])


class ClaimAll(PassTestBase):
    def test_claims_everything_unlocked_and_nothing_above(self):
        self.make_user(level=12, coins=0)
        res = lp.claim_all(self.db, "u1")
        self.assertTrue(res["ok"], res)
        claimed_levels = {r["level"] for r in res["claimed"]}
        self.assertTrue(claimed_levels, "claim-all claimed nothing at level 12")
        self.assertTrue(all(l <= 12 for l in claimed_levels),
                        f"claim-all reached above the player's level: {claimed_levels}")
        # Every claimable tier at or below 12 should now be in the ledger.
        expected = {t["id"] for t in lp.track() if t["claimable"] and t["level"] <= 12}
        got = {d["tier"] for d in self.db.collection("level_pass_claims")._docs.values()}
        self.assertEqual(got, expected)

    def test_is_idempotent(self):
        self.make_user(level=12)
        first = lp.claim_all(self.db, "u1")
        coins_after_first = self.coins()
        second = lp.claim_all(self.db, "u1")
        self.assertEqual(second["count"], 0)
        self.assertEqual(self.coins(), coins_after_first)
        self.assertGreater(first["count"], 0)

    def test_one_refusing_tier_does_not_stop_the_others(self):
        """A full shield hoard must not swallow the coins on the same sweep."""
        self.make_user(level=self.level_with_a_claimable_below("shield"),
                       streak_shields=lp.MAX_SHIELDS)
        res = lp.claim_all(self.db, "u1")
        self.assertTrue(res["ok"])
        self.assertGreater(self.coins(), 0, "the coin tiers were skipped too")
        self.assertTrue(any(s["error"] == "shields_full" for s in res["skipped"]),
                        f"the refusal was swallowed instead of reported: {res['skipped']}")


# ══════════════════════════════════════════════════════════════════════════
#  ACTIVATION
# ══════════════════════════════════════════════════════════════════════════
class BoostActivation(PassTestBase):
    def test_spends_one_and_runs_for_the_advertised_window(self):
        self.make_user(level=20, xp_boosts=2)
        before = int(time.time() * 1000)
        res = lp.activate_boost(self.db, "u1")
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["percent"], lp.BOOST_PERCENT)
        self.assertEqual(res["hours"], lp.BOOST_HOURS)
        self.assertEqual(self.user()["xp_boosts"], 1)
        span = self.user()["xp_boost_until"] - before
        self.assertAlmostEqual(span, lp.BOOST_MS, delta=5000)

    def test_refuses_while_one_is_already_running(self):
        self.make_user(level=20, xp_boosts=2,
                       xp_boost_until=int(time.time() * 1000) + 3600_000)
        res = lp.activate_boost(self.db, "u1")
        self.assertEqual(res["error"], "boost_running")
        self.assertEqual(self.user()["xp_boosts"], 2, "the refused activation still spent one")

    def test_an_expired_boost_does_not_block_the_next_one(self):
        self.make_user(level=20, xp_boosts=1,
                       xp_boost_until=int(time.time() * 1000) - 1000)
        self.assertTrue(lp.activate_boost(self.db, "u1")["ok"])

    def test_refuses_with_nothing_held(self):
        self.make_user(level=20, xp_boosts=0)
        self.assertEqual(lp.activate_boost(self.db, "u1")["error"], "no_boost")


class RerollActivation(PassTestBase):
    def week(self, offset_days=0):
        return int(time.time() * 1000) + offset_days * 86400_000

    def test_spends_a_token_and_marks_the_week(self):
        self.make_user(level=20, weekly_reroll_tokens=1)
        wk = self.week()
        res = lp.activate_reroll(self.db, "u1", wk)
        self.assertTrue(res["ok"], res)
        self.assertEqual(self.user()["weekly_reroll_week"], wk)
        self.assertEqual(self.user()["weekly_reroll_tokens"], 0)

    def test_will_not_charge_twice_for_the_same_week(self):
        wk = self.week()
        self.make_user(level=20, weekly_reroll_tokens=2, weekly_reroll_week=wk)
        res = lp.activate_reroll(self.db, "u1", wk)
        self.assertEqual(res["error"], "already_active")
        self.assertEqual(self.user()["weekly_reroll_tokens"], 2)

    def test_a_far_off_week_is_refused(self):
        self.make_user(level=20, weekly_reroll_tokens=1)
        res = lp.activate_reroll(self.db, "u1", self.week(90))
        self.assertEqual(res["error"], "bad_week")
        self.assertEqual(self.user()["weekly_reroll_tokens"], 1)

    def test_garbage_weeks_are_refused(self):
        self.make_user(level=20, weekly_reroll_tokens=1)
        for bad in (0, -1, "soon", None):
            self.assertEqual(lp.activate_reroll(self.db, "u1", bad)["error"], "bad_week")

    def test_refuses_with_no_token(self):
        self.make_user(level=20, weekly_reroll_tokens=0)
        self.assertEqual(lp.activate_reroll(self.db, "u1", self.week())["error"], "no_token")


# ══════════════════════════════════════════════════════════════════════════
#  STATE
# ══════════════════════════════════════════════════════════════════════════
class State(PassTestBase):
    def test_signed_out_gets_the_catalogue_and_no_account_data(self):
        out = lp.state_payload(None)
        self.assertTrue(out["ok"])
        self.assertFalse(out["signedIn"])
        self.assertTrue(out["track"])
        self.assertEqual(out["claimed"], [])
        self.assertEqual(out["inventory"]["coins"], 0)

    def test_serves_the_level_curve_so_the_client_never_copies_it(self):
        out = lp.state_payload(None)
        self.assertEqual(out["levelTotals"], LEVEL_TOTALS,
                         "the served curve drifted from the server's own table")

    def test_reports_level_progress_and_claims(self):
        self.make_user(level=12, coins=40)
        tier = self.first_tier_of("coins")
        lp.claim(self.db, "u1", tier["id"])
        out = lp.state_payload("u1")
        self.assertEqual(out["level"], 12)
        self.assertIn(tier["id"], out["claimed"])
        self.assertEqual(out["inventory"]["coins"], 40 + tier["amount"])

    def test_an_expired_boost_reads_as_inactive(self):
        self.make_user(level=5, xp_boost_until=int(time.time() * 1000) - 1)
        inv = lp.state_payload("u1")["inventory"]
        self.assertFalse(inv["boostActive"])
        self.assertEqual(inv["boostUntil"], 0)


# ══════════════════════════════════════════════════════════════════════════
#  "THE PASS SAYS I AM LEVEL 1"
#  Two different ways a real account was told it was Level 1 while the rest of
#  the game had it at 39, and the flag that lets the page tell them apart from
#  a genuinely new account.
# ══════════════════════════════════════════════════════════════════════════
class AccountLevelIsTheGameLevel(PassTestBase):
    def test_an_account_that_stores_only_level_and_xp_current_is_not_level_1(self):
        # The shape preview-app.js's getStoredTotalXp has always fallen back to.
        # This module used to read total_xp alone and answer 1.
        self.db.collection("users")._docs["u1"] = {
            "stats": {"level": 39, "xp_current": 1550, "critter_coins": 0},
        }
        out = lp.state_payload("u1")
        self.assertEqual(out["level"], 39)
        self.assertEqual(out["totalXp"], LEVEL_TOTALS[38] + 1550)

    def test_total_xp_still_wins_when_both_are_stored(self):
        # The derived pair is a FALLBACK, never a correction: total_xp is what
        # every write path sets and what claims are re-derived from.
        self.db.collection("users")._docs["u1"] = {
            "stats": {"total_xp": xp_for_level(12), "level": 99, "xp_current": 5},
        }
        self.assertEqual(lp.state_payload("u1")["level"], 12)

    def test_a_zero_total_xp_is_a_real_zero_not_a_missing_field(self):
        # Prestige writes total_xp: 0 deliberately. Falling back to a stale
        # `level` there would undo the reset the player just chose.
        self.db.collection("users")._docs["u1"] = {
            "stats": {"total_xp": 0, "level": 100, "xp_current": 0},
        }
        out = lp.state_payload("u1")
        self.assertEqual(out["level"], 1)
        self.assertEqual(out["totalXp"], 0)

    def test_a_read_that_succeeds_says_so(self):
        self.make_user(level=12)
        self.assertTrue(lp.state_payload("u1")["accountRead"])

    def test_a_refusing_database_does_not_claim_the_player_is_level_1(self):
        # 2026-09-04: Firestore's daily quota ran out, every read threw, and the
        # payload's level-1 default went out with ok:true. The flag is what lets
        # the page paint the level the app already knows instead.
        self.make_user(level=39)

        class Boom:
            def collection(self, *_a, **_k):
                raise RuntimeError("429 Quota exceeded")

        lp._get_firestore = lambda: Boom()                # type: ignore[assignment]
        out = lp.state_payload("u1")
        self.assertTrue(out["ok"], "the reward catalogue is still servable")
        self.assertTrue(out["signedIn"])
        self.assertFalse(out["accountRead"],
                         "a failed read must not look like a brand-new account")

    def test_signed_out_is_not_a_failed_read(self):
        # Nobody to read is a true answer, but it is not "we tried and could
        # not": the client must not go looking for a level that isn't there.
        self.assertFalse(lp.state_payload(None)["accountRead"])


# ══════════════════════════════════════════════════════════════════════════
#  THE WIRING BEHIND IT
#  A flag the server sends and the page ignores is the same bug with an extra
#  field in it, so both halves are pinned here.
# ══════════════════════════════════════════════════════════════════════════
class LiveLevelWiring(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(ROOT, "multiplayer", "client", "js", "level-pass.js"),
                  encoding="utf-8") as fh:
            self.js = fh.read()
        with open(PREVIEW_JS, encoding="utf-8") as fh:
            self.app = fh.read()

    def test_the_page_falls_back_to_the_level_the_app_already_knows(self):
        self.assertIn("_state.accountRead !== false", self.js)
        self.assertIn("applyLiveAccountLevel", self.js)
        # …and it is actually called on the render path, not just defined.
        self.assertIn("applyLiveAccountLevel();", self.js)

    def test_it_only_fires_when_the_read_failed(self):
        # A successful read is authoritative even when the browser's copy is
        # newer: reconciling an unlanded XP write is the server's job.
        body = self.js.split("function applyLiveAccountLevel", 1)[1].split("\n  }", 1)[0]
        self.assertIn("!_state.signedIn", body)
        self.assertIn("accountRead !== false", body)

    def test_the_seams_it_reads_are_real_and_at_module_scope(self):
        # Both live in preview-app.js OUTSIDE the auth IIFE. Inside it, these
        # calls are a silent ReferenceError and the fallback never fires.
        self.assertIn("window.__fishGetMyStats     =", self.app)
        self.assertIn("window.__fishStoredTotalXp = ", self.app)


# ══════════════════════════════════════════════════════════════════════════
#  HTTP
# ══════════════════════════════════════════════════════════════════════════
class FakeHandler:
    def __init__(self):
        self.payload = None
        self.status = 200

    def _send_json(self, payload, status=200):
        self.payload = payload
        self.status = status


class Parsed:
    def __init__(self, path):
        self.path = path


class Http(PassTestBase):
    def post(self, path, body):
        h = FakeHandler()
        handled = lp.handle_post(h, Parsed(path), body)
        return handled, h

    def test_ignores_paths_that_are_not_ours(self):
        handled, _ = self.post("/api/clans/home", {})
        self.assertFalse(handled)

    def test_state_is_readable_signed_out(self):
        handled, h = self.post("/api/pass/state", {})
        self.assertTrue(handled)
        self.assertTrue(h.payload["ok"])
        self.assertFalse(h.payload["signedIn"])

    def test_every_mutating_route_needs_a_token(self):
        for action in ("claim", "claim-all", "boost", "reroll"):
            handled, h = self.post("/api/pass/" + action, {"idToken": "bad"})
            self.assertTrue(handled)
            self.assertEqual(h.status, 401, action)
            self.assertEqual(h.payload["error"], "unauthorized", action)

    def test_claim_over_http_pays_and_reports_inventory(self):
        tier = self.first_tier_of("coins")
        self.make_user(level=5, coins=0)
        handled, h = self.post("/api/pass/claim",
                               {"idToken": "good:u1", "tier": tier["id"]})
        self.assertTrue(handled)
        self.assertTrue(h.payload["ok"], h.payload)
        self.assertEqual(h.payload["inventory"]["coins"], tier["amount"])

    def test_a_refusal_carries_a_sentence_a_player_can_act_on(self):
        tier = sorted((t for t in lp.track() if t["type"] == "coins"),
                      key=lambda x: x["level"])[-1]
        self.make_user(level=1)
        _, h = self.post("/api/pass/claim", {"idToken": "good:u1", "tier": tier["id"]})
        self.assertFalse(h.payload["ok"])
        self.assertTrue(h.payload["message"])
        self.assertNotEqual(h.payload["message"], lp.ERROR_MESSAGES["server_error"],
                            "a known refusal fell through to the generic message")

    def test_unknown_action_404s(self):
        self.make_user()
        _, h = self.post("/api/pass/teleport", {"idToken": "good:u1"})
        self.assertEqual(h.status, 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)
