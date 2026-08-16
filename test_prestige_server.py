"""Tests for the Prestige system (prestige_server.py).

Three jobs, in order of how much damage the bug would do:

 1. THE TABLES THAT MUST NOT DRIFT. AVATAR_UNLOCK_TYPES decides whether an
    avatar relocks on Prestige, and it mirrors ANIMAL_AVATARS in
    js/preview-app.js. If a new avatar is added to the client and not here, it
    either survives a Prestige it should not, or — much worse — a bought /
    donated / competitive-rank avatar gets taken away. Same idea for
    SKIN_ANIMALS vs the printed card lists.

 2. THE REWARD MATH. Every number the player is shown before they confirm.

 3. THE COMMIT TRANSACTION, against an in-memory Firestore fake: what resets,
    what survives, and every way a tampered request is supposed to bounce.

Run:  python3 test_prestige_server.py
"""
from __future__ import annotations

import ast
import collections
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import prestige_server as ps  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
CLIENT_JS = os.path.join(HERE, "multiplayer", "client", "js", "preview-app.js")
SERVER_PY = os.path.join(HERE, "multiplayer_server.py")


# ══════════════════════════════════════════════════════════════════════════
#  A tiny in-memory Firestore, enough for the transaction under test
# ══════════════════════════════════════════════════════════════════════════
def _deep_merge(dst: dict, src: dict) -> dict:
    """Firestore's set(..., merge=True): nested maps merge key-by-key, and a
    non-map value replaces whatever was there."""
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
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
        import copy
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
        import copy
        cur = self._coll._docs.get(self.id)
        if merge and isinstance(cur, dict):
            _deep_merge(cur, copy.deepcopy(data))
        else:
            self._coll._docs[self.id] = copy.deepcopy(data)

    def create(self, data):
        import copy
        if self.id in self._coll._docs:
            raise AlreadyExists(self.id)
        self._coll._docs[self.id] = copy.deepcopy(data)

    def collection(self, name):
        return self._coll._db.collection(self._coll.name + "/" + self.id + "/" + name)


class FakeColl:
    def __init__(self, db, name):
        self._db = db
        self.name = name
        self._docs = {}

    def document(self, doc_id):
        return FakeDoc(self, doc_id)

    def where(self, field, op, value):
        assert op == "=="
        rows = [FakeSnap(k, v) for k, v in self._docs.items() if (v or {}).get(field) == value]
        return _FakeQuery(rows)

    def stream(self):
        return [FakeSnap(k, v) for k, v in self._docs.items()]


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def limit(self, _n):
        return self

    def stream(self):
        return list(self._rows)

    def get(self):
        return list(self._rows)


class FakeTxn:
    """Applies writes immediately — good enough here because the fake runs the
    body exactly once and the real atomicity guarantee we care about (the
    ledger create() refusing a second run) is reproduced faithfully."""
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


def _fake_transactional(fn):
    return fn


# ══════════════════════════════════════════════════════════════════════════
#  Level curve, injected exactly the way multiplayer_server injects it
# ══════════════════════════════════════════════════════════════════════════
def _read_level_totals():
    src = open(SERVER_PY, encoding="utf-8").read()
    m = re.search(r"^LEVEL_XP_TOTALS = (\[[^\]]*\])", src, re.M)
    assert m, "LEVEL_XP_TOTALS not found in multiplayer_server.py"
    return ast.literal_eval(m.group(1))


LEVEL_TOTALS = _read_level_totals()
MAX_LEVEL = len(LEVEL_TOTALS)
CAP_XP = LEVEL_TOTALS[-1]


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
        return (MAX_LEVEL, CAP_XP, CAP_XP)
    return (lvl, xp - LEVEL_TOTALS[lvl - 1], LEVEL_TOTALS[lvl] - LEVEL_TOTALS[lvl - 1])


def install(db, *, uid_by_name=None):
    ps.init(
        get_firestore=lambda: db,
        verify_token=lambda tok: {"uid": tok[5:]} if str(tok).startswith("token") else None,
        level_progress=level_progress,
        max_level=MAX_LEVEL,
        find_uid_by_username=(uid_by_name or (lambda _db, n: None)),
    )
    ps._txn_helpers = lambda: _fake_transactional  # type: ignore[assignment]
    ps._XP_FOR_MAX_CACHE.clear()
    ps._NAMES_CACHE.clear()
    ps._NAME_UID_CACHE.clear()
    ps._DISABLED_CACHE.update({"at": 0.0, "data": {}})


# ══════════════════════════════════════════════════════════════════════════
#  Account fixtures
# ══════════════════════════════════════════════════════════════════════════
# One of each relockable kind + one of each keep-forever kind.
RELOCKABLE = [p for p, t in ps.AVATAR_UNLOCK_TYPES.items() if t in ps.RELOCKABLE_UNLOCK_TYPES]
FOREVER = [p for p, t in ps.AVATAR_UNLOCK_TYPES.items() if t in ps.KEEP_FOREVER_UNLOCK_TYPES]


def make_account(db, uid="u1", *, total_xp=None, prestige=None, icons=None,
                 coins=1000, nickname="Reeflord"):
    doc = {
        "nickname": nickname,
        "avatar_url": "/avatars/sea-star.png",
        "unlocked_icons": list(icons if icons is not None else (RELOCKABLE[:6] + FOREVER[:3])),
        "stats": {
            "critter_coins": coins,
            "total_xp": CAP_XP if total_xp is None else total_xp,
            # Things that must survive untouched:
            "rank_competitive": "Diamond Dolphin II",
            "competitive_points": 4321,
            "completed_games": 900,
            "lifetime_deck_draws": 5000,
            "streak_days": ["2026-08-01", "2026-08-02"],
        },
        # …and things outside stats that must survive untouched:
        "clan_id": "clan-abc",
        "friends": ["u2", "u3"],
        "unlocked_backgrounds": ["/backgrounds/bg-kelp.png"],
        "supporter_tier": "ocean-ally",
        "founder_number": 12,
        "achievements": {"quick_swim": {"completed": True}},
    }
    if prestige is not None:
        doc["prestige"] = prestige
    db.collection("users").document(uid).set(doc)
    return doc


def good_body(db, uid="u1", **over):
    """A request that should succeed, built from the account's OWN state."""
    snap = db.collection("users").document(uid).get()
    st = ps._state_payload(uid, snap.to_dict())
    keep = st["avatars"]["eligible"][:st["keep_quota"]]
    # First (animal, style) pair this account does not already own — a Prestige
    # can never re-take a skin it already has.
    owned = {(s["animal"], s["style"]) for s in st["owned_skins"]}
    skin = None
    for style in (st["next"]["skin_styles"] or ["golden"]):
        for animal in ps.SKIN_ANIMALS:
            if (animal["id"], style) not in owned:
                skin = {"animal": animal["id"], "style": style}
                break
        if skin:
            break
    body = {
        "confirm": "PRESTIGE",
        "idempotency_key": "k-" + uid,
        "keep_avatars": keep,
        "skin": skin,
    }
    choice = st["next"]["color_choice"]
    if choice:
        body["name_color"] = {"color": choice[0]}
    body.update(over)
    return body


# ══════════════════════════════════════════════════════════════════════════
#  1) THE TABLES THAT MUST NOT DRIFT
# ══════════════════════════════════════════════════════════════════════════
class TestCatalogueMirrors(unittest.TestCase):
    def test_avatar_table_matches_the_client_exactly(self):
        """AVATAR_UNLOCK_TYPES ⇄ ANIMAL_AVATARS in js/preview-app.js.

        This is the test that stops a bought avatar from being relocked. If it
        fails after adding an avatar to the client, add the same path here with
        its unlock.type — do not delete the assertion.
        """
        src = open(CLIENT_JS, encoding="utf-8").read()
        i = src.index("const ANIMAL_AVATARS = [")
        block = src[i:src.index("\n  ];", i)]
        imgs = re.findall(
            r'\{\s*id:"[a-z0-9\-]+",\s*name:"(?:[^"\\]|\\.)*",\s*species:"[^"]+",\s*img:"([^"]+)"',
            block)
        types = re.findall(r'unlock:\{\s*type:"([a-z_]+)"', block)
        self.assertEqual(len(imgs), len(types),
                         "avatar entry / unlock-type count mismatch while parsing the client")
        client = dict(zip(imgs, types))
        self.assertEqual(
            client, ps.AVATAR_UNLOCK_TYPES,
            "AVATAR_UNLOCK_TYPES has drifted from ANIMAL_AVATARS in js/preview-app.js")

    def test_every_avatar_is_classified(self):
        known = ps.RELOCKABLE_UNLOCK_TYPES | ps.KEEP_FOREVER_UNLOCK_TYPES
        for path, kind in ps.AVATAR_UNLOCK_TYPES.items():
            self.assertIn(kind, known, f"{path} has unclassified unlock type {kind!r}")

    def test_purchased_and_rank_avatars_are_never_relockable(self):
        for path, kind in ps.AVATAR_UNLOCK_TYPES.items():
            if kind in ("shop", "code", "rank", "starter"):
                self.assertNotIn(kind, ps.RELOCKABLE_UNLOCK_TYPES,
                                 f"{path} ({kind}) must never relock")

    def test_skin_roster_matches_the_printed_cards(self):
        rows = []
        for fname in ("cards_lr.txt", "cards_vertical.txt", "cards_oceans.txt"):
            for line in open(os.path.join(HERE, fname), encoding="utf-8"):
                parts = line.rstrip("\n").split("\t")
                if len(parts) >= 4 and parts[0].strip().isdigit():
                    rows.append((int(parts[0]), parts[1].strip(), parts[3].strip()))
        by = collections.OrderedDict()
        for uid, name, fam in rows:
            if fam in ("Ocean", "End Game"):
                continue          # not animals, so not skinnable
            by.setdefault(name, {"family": fam, "uid": uid})

        def slug(n):
            return re.sub(r"[^a-z0-9]+", "-", n.lower().replace("'", "").replace("’", "")).strip("-")

        want = [{"id": slug(n), "name": n, "family": v["family"], "uid": v["uid"]}
                for n, v in by.items()]
        self.assertEqual(want, ps.SKIN_ANIMALS,
                         "SKIN_ANIMALS has drifted from the printed card lists")

    def test_no_oceans_or_end_game_are_skinnable(self):
        names = {a["name"] for a in ps.SKIN_ANIMALS}
        for banned in ("Pier", "Deep Ocean", "Coral Reef", "Mangrove", "Artificial Reef",
                       "Arctic Ocean", "Kelp Forest", "Tide Pool", "END GAME"):
            self.assertNotIn(banned, names)

    def test_scene_ids_are_unique_and_named(self):
        ids = [b["id"] for b in ps.PRESTIGE_BACKGROUNDS]
        self.assertEqual(len(ids), len(set(ids)))
        # A background sharing a badge's name reads as a bug on the one screen
        # that has to be unambiguous.
        badge_names = {b["name"] for b in ps.PRESTIGE_BADGES}
        for bg in ps.PRESTIGE_BACKGROUNDS:
            self.assertNotIn(bg["name"], badge_names,
                             f"background {bg['name']!r} collides with a badge name")


# ══════════════════════════════════════════════════════════════════════════
#  2) THE REWARD MATH
# ══════════════════════════════════════════════════════════════════════════
class TestRewardMath(unittest.TestCase):
    def test_every_prestige_pays_a_flat_1000_coins(self):
        # Flat, not a ladder: the 40th Prestige pays exactly what the 1st did.
        for lvl in list(range(1, 11)) + [25, 40, 100, ps.MAX_PRESTIGE_LEVEL]:
            self.assertEqual(ps.coin_reward_for(lvl), 1000, f"Prestige {lvl}")
        self.assertEqual(ps.coin_reward_for(0), 0)
        self.assertEqual(ps.coin_reward_for(-3), 0)

    def test_xp_multiplier_stacks_by_25_percent(self):
        for lvl, mult in {0: 1.0, 1: 1.25, 2: 1.5, 3: 1.75, 4: 2.0, 5: 2.25}.items():
            self.assertAlmostEqual(ps.xp_multiplier_for(lvl), mult)

    def test_the_spec_example_100_base_at_prestige_3(self):
        self.assertEqual(ps.apply_xp_bonus(100, 3),
                         {"base": 100, "bonus": 75, "total": 175})

    def test_an_existing_reduction_survives_the_bonus(self):
        """An AI game is halved to 50 BEFORE the bonus. The reduction has to
        still be there afterwards — 87, not 175/2 and not 175."""
        halved = 100 // 2
        out = ps.apply_xp_bonus(halved, 3)
        self.assertEqual(out["base"], 50)
        self.assertEqual(out["total"], 87)
        self.assertLess(out["total"], ps.apply_xp_bonus(100, 3)["total"])

    def test_store_bonus_percent_stacks_by_5(self):
        for lvl, pct in {0: 0, 1: 5, 2: 10, 3: 15, 4: 20, 5: 25}.items():
            self.assertEqual(ps.store_bonus_pct_for(lvl), pct)

    def test_the_spec_example_1000_coin_pack_at_prestige_3(self):
        self.assertEqual(ps.store_bonus_coins(1000, 3), 150)

    def test_store_bonus_rounds_to_a_whole_coin(self):
        self.assertEqual(ps.store_bonus_coins(5250, 1), 262)   # 262.5 → 262
        self.assertEqual(ps.store_bonus_coins(11500, 1), 575)
        self.assertEqual(ps.store_bonus_coins(1, 1), 0)

    def test_store_bonus_is_zero_at_prestige_zero(self):
        self.assertEqual(ps.store_bonus_coins(25000, 0), 0)
        self.assertEqual(ps.store_bonus_for({"prestige": {"level": 0}}, 25000), 0)

    def test_store_bonus_reads_the_stored_level_not_a_client_value(self):
        doc = {"prestige": {"level": 4}}
        self.assertEqual(ps.store_bonus_for(doc, 1000), 200)
        # A doc with a hand-written multiplier is ignored; only `level` counts.
        doc["prestige"]["store_bonus_pct"] = 9999
        doc["prestige"]["xp_multiplier"] = 99.0
        self.assertEqual(ps.store_bonus_for(doc, 1000), 200)

    def test_badges_titles_backgrounds_exist_for_every_level(self):
        for lvl in range(1, 31):
            self.assertIsNotNone(ps.badge_for_level(lvl))
            self.assertTrue(ps.title_for_level(lvl))
            bg = ps.background_for_level(lvl)
            self.assertTrue(bg["id"] and bg["name"])

    def test_backgrounds_past_ten_are_numbered_variants_not_repeats(self):
        first = ps.background_for_level(4)
        again = ps.background_for_level(14)
        self.assertNotEqual(first["id"], again["id"])
        self.assertNotEqual(first["name"], again["name"])
        self.assertEqual(first["scene"], again["scene"])

    def test_prestige_ten_and_beyond_wears_the_crown(self):
        self.assertEqual(ps.badge_for_level(10)["id"], "crown")
        self.assertEqual(ps.badge_for_level(47)["id"], "crown")
        self.assertIsNone(ps.badge_for_level(0))


# ══════════════════════════════════════════════════════════════════════════
#  Colour safety
# ══════════════════════════════════════════════════════════════════════════
class TestColourGuards(unittest.TestCase):
    def test_a_normal_colour_passes(self):
        for hexv in ("#1f7ae0", "#12a37c", "#7a49d6", "#b8860b", "#ffffff", "#000000"):
            value, err = ps.validate_custom_color(hexv)
            self.assertIsNone(err, f"{hexv} should be allowed")
            self.assertEqual(value, hexv.lower())

    def test_every_accepted_colour_renders_at_wcag_aa_once_plated(self):
        """The invariant the readability floor actually guarantees.

        Sweeping the whole 6-bit RGB cube: any colour the gate accepts is
        readable at ≥ MIN_CONTRAST against the plate the client will pick for
        it. This is the assertion that would break if someone changed the plate
        polarity back to being chosen by SURFACE instead of by colour (a pale
        name on a white plate), which is the bug this pairs with.
        """
        worst = (None, 99.0)
        for r in range(0, 256, 17):
            for g in range(0, 256, 17):
                for b in range(0, 256, 17):
                    hexv = "#%02x%02x%02x" % (r, g, b)
                    value, err = ps.validate_custom_color(hexv)
                    if err in ("color_reserved",):
                        continue
                    self.assertIsNone(err, f"{hexv} was rejected as {err}")
                    ratio = ps.best_plated_contrast((r, g, b))
                    if ratio < worst[1]:
                        worst = (hexv, ratio)
                    self.assertGreaterEqual(ratio, ps.MIN_CONTRAST, hexv)
        # …and the floor is set where it actually bites: the worst colour in the
        # whole cube must not be comfortably clear of it, or the gate is fiction.
        self.assertLess(worst[1], ps.MIN_CONTRAST + 0.75,
                        f"MIN_CONTRAST is set far below what any colour can hit "
                        f"(worst is {worst[0]} at {worst[1]:.2f}) — it can never fire")

    def test_the_floor_still_refuses_a_colour_that_cannot_reach_it(self):
        """Defensive: if the plates are ever retuned so some colour can't clear
        AA, the gate has to catch it rather than store it."""
        real_light, real_dark = ps.LIGHT_PLATE, ps.DARK_PLATE
        try:
            # Two plates that are both mid-grey leave a mid-grey name nowhere to sit.
            ps.LIGHT_PLATE = ps.DARK_PLATE = (0x7d, 0x7d, 0x7d)
            ps.LIGHT_SURFACE = ps.DARK_SURFACE = (0x7d, 0x7d, 0x7d)
            _v, err = ps.validate_custom_color("#7d7d7d")
            self.assertEqual(err, "color_unreadable")
        finally:
            ps.LIGHT_PLATE, ps.DARK_PLATE = real_light, real_dark
            ps.LIGHT_SURFACE = (0xF4, 0xFB, 0xFF)
            ps.DARK_SURFACE = (0x0C, 0x2A, 0x44)

    def test_staff_and_system_colours_are_refused(self):
        for hexv, _label in ps.RESERVED_COLORS:
            _v, err = ps.validate_custom_color(hexv)
            self.assertEqual(err, "color_reserved", f"{hexv} must be reserved")
        # …and near-misses of them too, so a shade can't impersonate either.
        self.assertEqual(ps.validate_custom_color("#e42424")[1], "color_reserved")

    def test_junk_is_refused_without_raising(self):
        for bad in (None, "", "red", "#12345", "#gggggg", 42, {"r": 1}):
            _v, err = ps.validate_custom_color(bad)
            self.assertEqual(err, "bad_color")


# ══════════════════════════════════════════════════════════════════════════
#  Avatar split
# ══════════════════════════════════════════════════════════════════════════
class TestAvatarSplit(unittest.TestCase):
    def test_earned_avatars_are_eligible_and_bought_ones_are_automatic(self):
        rec = ps._blank_record()
        icons = ["/avatars/sea-star.png",              # level → relocks
                 "/avatars/summer-skin-gull.png",      # shop  → stays
                 "/avatars/amberjack.png",             # code  → stays
                 "/avatars/barracuda.png",             # rank  → stays
                 "/avatars/mullet.png"]                # starter → stays
        split = ps.split_avatars(icons, rec)
        self.assertEqual(split["eligible"], ["/avatars/sea-star.png"])
        self.assertEqual(len(split["automatic"]), 4)

    def test_an_avatar_kept_by_an_earlier_prestige_is_automatic_forever(self):
        rec = ps._blank_record()
        rec["kept_avatars"] = ["/avatars/sea-star.png"]
        split = ps.split_avatars(["/avatars/sea-star.png"], rec)
        self.assertEqual(split["eligible"], [])
        self.assertEqual(split["automatic"], ["/avatars/sea-star.png"])

    def test_an_unknown_path_is_kept_not_relocked(self):
        """When in doubt, never take something away from a player."""
        split = ps.split_avatars(["/avatars/not-a-real-avatar.png"], ps._blank_record())
        self.assertEqual(split["eligible"], [])
        self.assertEqual(split["unknown"], ["/avatars/not-a-real-avatar.png"])

    def test_paths_are_canonicalised_and_deduped(self):
        split = ps.split_avatars(
            ["/avatars/Sea-Star.PNG?v=ws12", "/avatars/sea-star.png", "  "],
            ps._blank_record())
        self.assertEqual(split["eligible"], ["/avatars/sea-star.png"])


# ══════════════════════════════════════════════════════════════════════════
#  3) THE COMMIT TRANSACTION
# ══════════════════════════════════════════════════════════════════════════
class TestCommit(unittest.TestCase):
    def setUp(self):
        self.db = FakeDb()
        install(self.db)
        make_account(self.db)

    def user(self, uid="u1"):
        return self.db.collection("users").document(uid).get().to_dict()

    # ── the happy path ────────────────────────────────────────────────────
    def test_a_valid_prestige_resets_level_and_pays_out(self):
        res = ps._commit(self.db, "u1", good_body(self.db))
        self.assertTrue(res.get("ok"), res)
        self.assertEqual(res["prestige"], 1)
        self.assertEqual(res["coins_awarded"], 1000)

        u = self.user()
        self.assertEqual(u["stats"]["total_xp"], 0)
        self.assertEqual(u["stats"]["level"], 1)
        self.assertEqual(u["stats"]["player_level"], 1)
        self.assertEqual(u["stats"]["critter_coins"], 2000)   # 1000 + 1000
        self.assertEqual(u["prestige"]["level"], 1)
        self.assertAlmostEqual(u["prestige"]["xp_multiplier"], 1.25)
        self.assertEqual(u["prestige"]["store_bonus_pct"], 5)

    def test_nothing_that_must_survive_is_touched(self):
        before = self.user()
        ps._commit(self.db, "u1", good_body(self.db))
        after = self.user()
        # Competitive, clan, friends, purchases, supporter, achievements.
        self.assertEqual(after["stats"]["rank_competitive"], before["stats"]["rank_competitive"])
        self.assertEqual(after["stats"]["competitive_points"], 4321)
        self.assertEqual(after["stats"]["completed_games"], 900)
        self.assertEqual(after["stats"]["lifetime_deck_draws"], 5000)
        self.assertEqual(after["stats"]["streak_days"], before["stats"]["streak_days"])
        self.assertEqual(after["clan_id"], "clan-abc")
        self.assertEqual(after["friends"], ["u2", "u3"])
        self.assertEqual(after["unlocked_backgrounds"], ["/backgrounds/bg-kelp.png"])
        self.assertEqual(after["supporter_tier"], "ocean-ally")
        self.assertEqual(after["founder_number"], 12)
        self.assertEqual(after["achievements"], before["achievements"])

    def test_earned_avatars_relock_and_the_chosen_two_survive(self):
        body = good_body(self.db)
        keep = body["keep_avatars"]
        res = ps._commit(self.db, "u1", body)
        self.assertTrue(res["ok"])
        icons = set(self.user()["unlocked_icons"])
        for k in keep:
            self.assertIn(k, icons, "a kept critter was relocked")
        for path in FOREVER[:3]:
            self.assertIn(path, icons, "a bought/rank critter was relocked")
        relocked = [p for p in RELOCKABLE[:6] if p not in keep]
        for path in relocked:
            self.assertNotIn(path, icons, "an earned critter survived when it should relock")

    def test_an_equipped_avatar_that_relocks_falls_back_to_the_starter(self):
        # The fixture equips /avatars/sea-star.png (a "level" unlock).
        make_account(self.db, "u2", icons=["/avatars/sea-star.png"] + RELOCKABLE[:3])
        self.db.collection("users").document("u2").set(
            {"avatar_url": "/avatars/sea-star.png"}, merge=True)
        body = good_body(self.db, "u2")
        # …and deliberately does not keep it.
        body["keep_avatars"] = [p for p in body["keep_avatars"] if p != "/avatars/sea-star.png"]
        if len(body["keep_avatars"]) < 2:
            st = ps._state_payload("u2", self.user("u2"))
            spare = [p for p in st["avatars"]["eligible"] if p != "/avatars/sea-star.png"]
            body["keep_avatars"] = spare[:2]
        res = ps._commit(self.db, "u2", body)
        self.assertTrue(res.get("ok"), res)
        self.assertEqual(self.user("u2")["avatar_url"], "/avatars/mullet.png")

    def test_the_kept_pair_is_remembered_forever(self):
        body = good_body(self.db)
        keep = list(body["keep_avatars"])
        ps._commit(self.db, "u1", body)
        rec = self.user()["prestige"]
        self.assertEqual(sorted(rec["kept_avatars"]), sorted(keep))
        # They are now "automatic" and cannot be picked again next time.
        split = ps.split_avatars(self.user()["unlocked_icons"], ps._record_of(self.user()))
        for k in keep:
            self.assertIn(k, split["automatic"])
            self.assertNotIn(k, split["eligible"])

    def test_a_ledger_row_is_written_as_the_admin_log(self):
        ps._commit(self.db, "u1", good_body(self.db))
        row = self.db.collection("prestige_ledger").document("u1_1").get().to_dict()
        self.assertIsNotNone(row)
        for field in ("uid", "username", "old_prestige", "new_prestige", "level_before",
                      "at", "coins_awarded", "avatars_kept", "avatars_relocked",
                      "skin", "background", "colors_unlocked", "badge",
                      "idempotency_key", "result"):
            self.assertIn(field, row, f"admin log is missing {field}")
        self.assertEqual(row["old_prestige"], 0)
        self.assertEqual(row["new_prestige"], 1)
        self.assertEqual(row["level_before"], MAX_LEVEL)

    def test_history_records_everything_the_player_can_look_back_at(self):
        ps._commit(self.db, "u1", good_body(self.db))
        hist = self.user()["prestige"]["history"]
        self.assertEqual(len(hist), 1)
        e = hist[0]
        for field in ("prestige", "at", "level_before", "coins", "avatars_kept",
                      "skin", "background", "colors", "badge", "xp_multiplier",
                      "store_bonus_pct", "title"):
            self.assertIn(field, e)

    # ── the gates ─────────────────────────────────────────────────────────
    def test_below_the_cap_is_refused(self):
        make_account(self.db, "low", total_xp=CAP_XP - 1)
        res = ps._commit(self.db, "low", good_body(self.db, "low"))
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"], "not_max_level")
        self.assertEqual(self.user("low")["stats"]["total_xp"], CAP_XP - 1)

    def test_the_confirmation_phrase_is_required(self):
        for bad in ("", "prestige!", "yes", None):
            res = ps._commit(self.db, "u1", good_body(self.db, confirm=bad))
            self.assertEqual(res["error"], "confirm_required")
        self.assertEqual(self.user()["stats"]["total_xp"], CAP_XP)

    def test_lowercase_confirmation_still_counts(self):
        res = ps._commit(self.db, "u1", good_body(self.db, confirm="  prestige  "))
        self.assertTrue(res.get("ok"), res)

    def test_an_idempotency_key_is_required(self):
        res = ps._commit(self.db, "u1", good_body(self.db, idempotency_key=""))
        self.assertEqual(res["error"], "idempotency_required")

    def test_exactly_two_avatars_or_nothing_happens(self):
        st = ps._state_payload("u1", self.user())
        elig = st["avatars"]["eligible"]
        for picks, err in ((elig[:1], "avatars_count"),
                           (elig[:3], "avatars_count"),
                           ([], "avatars_count"),
                           ("not-a-list", "avatars_required")):
            res = ps._commit(self.db, "u1", good_body(self.db, keep_avatars=picks))
            self.assertEqual(res["error"], err, picks)
        self.assertEqual(self.user()["stats"]["total_xp"], CAP_XP)

    def test_a_player_with_nothing_left_to_relock_can_still_prestige(self):
        """Prestige straight after a Prestige, or an account that only ever
        BOUGHT its critters, has fewer than two relockable ones. Demanding two
        there would lock them out of Prestige forever with no action available
        that could ever satisfy it."""
        make_account(self.db, "bought", icons=list(FOREVER))
        st = ps._state_payload("bought", self.user("bought"))
        self.assertEqual(st["avatars"]["eligible"], [])
        self.assertEqual(st["keep_quota"], 0)
        res = ps._commit(self.db, "bought", {
            "confirm": "PRESTIGE", "idempotency_key": "kb",
            "keep_avatars": [],
            "skin": {"animal": "clownfish", "style": "golden"},
            "name_color": {"color": "ocean"},
        })
        self.assertTrue(res.get("ok"), res)
        self.assertEqual(set(self.user("bought")["unlocked_icons"]), set(FOREVER),
                         "a bought-only account lost something to a Prestige")

    def test_a_player_with_exactly_one_relockable_critter_keeps_that_one(self):
        make_account(self.db, "one", icons=list(FOREVER) + [RELOCKABLE[0]])
        st = ps._state_payload("one", self.user("one"))
        self.assertEqual(st["keep_quota"], 1)
        # Two is now the WRONG count, and one is right.
        bad = ps._commit(self.db, "one", {
            "confirm": "PRESTIGE", "idempotency_key": "k-bad",
            "keep_avatars": [RELOCKABLE[0], FOREVER[0]],
            "skin": {"animal": "clownfish", "style": "golden"},
            "name_color": {"color": "ocean"}})
        self.assertEqual(bad["error"], "avatars_count")
        res = ps._commit(self.db, "one", {
            "confirm": "PRESTIGE", "idempotency_key": "k-one",
            "keep_avatars": [RELOCKABLE[0]],
            "skin": {"animal": "clownfish", "style": "golden"},
            "name_color": {"color": "ocean"}})
        self.assertTrue(res.get("ok"), res)
        self.assertIn(RELOCKABLE[0], self.user("one")["unlocked_icons"])

    def test_an_avatar_the_player_does_not_own_is_refused(self):
        res = ps._commit(self.db, "u1", good_body(
            self.db, keep_avatars=["/avatars/giant-squid.png", "/avatars/whale-shark.png"]))
        self.assertEqual(res["error"], "avatar_not_owned")

    def test_an_avatar_that_stays_anyway_cannot_be_chosen(self):
        """Choosing one would silently waste a slot."""
        st = ps._state_payload("u1", self.user())
        res = ps._commit(self.db, "u1", good_body(
            self.db, keep_avatars=[st["avatars"]["automatic"][0], st["avatars"]["eligible"][0]]))
        self.assertEqual(res["error"], "avatar_already_kept")

    def test_duplicate_picks_count_as_one(self):
        st = ps._state_payload("u1", self.user())
        one = st["avatars"]["eligible"][0]
        res = ps._commit(self.db, "u1", good_body(self.db, keep_avatars=[one, one]))
        self.assertEqual(res["error"], "avatars_count")

    def test_an_unknown_or_locked_skin_is_refused(self):
        for skin, err in (
            ({"animal": "loch-ness-monster", "style": "golden"}, "skin_unknown_animal"),
            ({"animal": ps.SKIN_ANIMALS[0]["id"], "style": "plaid"}, "skin_unknown_style"),
            ({"animal": ps.SKIN_ANIMALS[0]["id"], "style": "celest"}, "skin_style_locked"),
            (None, "skin_required"),
        ):
            res = ps._commit(self.db, "u1", good_body(self.db, skin=skin))
            self.assertEqual(res["error"], err, skin)

    def test_the_same_skin_cannot_be_taken_twice(self):
        rec = ps._blank_record()
        rec["level"] = 1
        rec["skins"] = [{"animal": "clownfish", "style": "golden"}]
        make_account(self.db, "u3", prestige=rec)
        res = ps._commit(self.db, "u3", good_body(
            self.db, "u3", skin={"animal": "clownfish", "style": "golden"}))
        self.assertEqual(res["error"], "skin_already_owned")
        # …but the same animal in a DIFFERENT style is fine.
        res2 = ps._commit(self.db, "u3", good_body(
            self.db, "u3", skin={"animal": "clownfish", "style": "albino"}))
        self.assertTrue(res2.get("ok"), res2)

    def test_prestige_one_requires_a_colour_choice(self):
        res = ps._commit(self.db, "u1", good_body(self.db, name_color=None))
        self.assertEqual(res["error"], "color_choice_required")
        res = ps._commit(self.db, "u1", good_body(self.db, name_color={"color": "gold"}))
        self.assertEqual(res["error"], "color_choice_required")

    def test_prestige_two_hands_back_the_colour_that_was_not_chosen(self):
        ps._commit(self.db, "u1", good_body(self.db))
        rec = self.user()["prestige"]
        self.assertEqual(rec["colors"], ["ocean"],
                         "Prestige 1 paid out BOTH colours; the second is Prestige 2's reward")
        # Back to the cap, and they earned some critters again on the way up.
        self.db.collection("users").document("u1").set(
            {"stats": {"total_xp": CAP_XP},
             "unlocked_icons": self.user()["unlocked_icons"] + RELOCKABLE[6:9]}, merge=True)
        res = ps._commit(self.db, "u1", good_body(self.db, idempotency_key="k2"))
        self.assertTrue(res.get("ok"), res)
        colors = self.user()["prestige"]["colors"]
        self.assertIn("ocean", colors)
        self.assertIn("seafoam", colors, "the unchosen Prestige-1 colour never came back")
        self.assertIn("purple", colors)

    # ── duplicates ────────────────────────────────────────────────────────
    def test_the_same_request_replays_instead_of_prestiging_twice(self):
        body = good_body(self.db)
        first = ps._commit(self.db, "u1", body)
        self.assertTrue(first["ok"])
        # Refresh / second device / double tap: identical key.
        second = ps._commit(self.db, "u1", body)
        self.assertTrue(second["ok"])
        self.assertTrue(second.get("replayed"))
        self.assertEqual(second["coins_awarded"], first["coins_awarded"])
        # Paid exactly once.
        self.assertEqual(self.user()["stats"]["critter_coins"], 2000)
        self.assertEqual(self.user()["prestige"]["level"], 1)
        self.assertEqual(len(self.user()["prestige"]["history"]), 1)

    def test_a_fresh_key_at_the_same_prestige_number_is_refused(self):
        """A determined caller who rotates the idempotency key still cannot
        claim Prestige 1 twice — the per-run ledger doc is the real guard."""
        ps._commit(self.db, "u1", good_body(self.db))
        # Put them back at the cap but do NOT let the prestige level move.
        self.db.collection("users").document("u1").set(
            {"stats": {"total_xp": CAP_XP}, "prestige": {"level": 0}}, merge=True)
        res = ps._commit(self.db, "u1", good_body(self.db, idempotency_key="fresh"))
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"], "already_prestiged")

    def test_a_failed_prestige_changes_absolutely_nothing(self):
        before = self.user()
        for body in (good_body(self.db, confirm="nope"),
                     good_body(self.db, keep_avatars=[]),
                     good_body(self.db, skin={"animal": "nope", "style": "golden"}),
                     good_body(self.db, name_color=None)):
            res = ps._commit(self.db, "u1", body)
            self.assertFalse(res.get("ok"))
        self.assertEqual(self.user(), before)

    def test_client_supplied_rewards_are_ignored(self):
        """The body may only carry CHOICES. A coin amount, a multiplier or a
        prestige level in the request has to be inert."""
        res = ps._commit(self.db, "u1", good_body(
            self.db,
            coins_awarded=999999, coins=999999,
            xp_multiplier=50.0, store_bonus_pct=100,
            prestige=42, level=1, new_level=42))
        self.assertTrue(res.get("ok"), res)
        self.assertEqual(res["coins_awarded"], 1000)
        self.assertEqual(res["prestige"], 1)
        u = self.user()
        self.assertEqual(u["stats"]["critter_coins"], 2000)
        self.assertAlmostEqual(u["prestige"]["xp_multiplier"], 1.25)
        self.assertEqual(u["prestige"]["store_bonus_pct"], 5)

    def test_a_hand_edited_multiplier_on_the_stored_record_is_re_derived(self):
        rec = ps._blank_record()
        rec["level"] = 2
        rec["xp_multiplier"] = 99.0
        rec["store_bonus_pct"] = 500
        make_account(self.db, "hax", prestige=rec)
        out = ps._record_of(self.user("hax"))
        self.assertAlmostEqual(out["xp_multiplier"], 1.5)
        self.assertEqual(out["store_bonus_pct"], 10)

    def test_a_missing_account_is_refused(self):
        res = ps._commit(self.db, "ghost", good_body(self.db))
        self.assertEqual(res["error"], "no_account")


# ══════════════════════════════════════════════════════════════════════════
#  State payload
# ══════════════════════════════════════════════════════════════════════════
class TestStatePayload(unittest.TestCase):
    def setUp(self):
        self.db = FakeDb()
        install(self.db)

    def test_at_the_cap_can_prestige_is_true(self):
        make_account(self.db, "u1")
        st = ps._state_payload("u1", self.db.collection("users").document("u1").get().to_dict())
        self.assertTrue(st["can_prestige"])
        self.assertEqual(st["level"], MAX_LEVEL)
        self.assertEqual(st["xp_to_max"], 0)
        self.assertEqual(st["next"]["prestige"], 1)
        self.assertEqual(st["next"]["coins"], 1000)

    def test_below_the_cap_reports_the_remaining_xp(self):
        make_account(self.db, "u1", total_xp=CAP_XP - 2500)
        st = ps._state_payload("u1", self.db.collection("users").document("u1").get().to_dict())
        self.assertFalse(st["can_prestige"])
        self.assertEqual(st["xp_to_max"], 2500)

    def test_the_xp_needed_for_max_is_derived_not_hardcoded(self):
        self.assertEqual(ps._xp_needed_for_max(), CAP_XP)

    def test_no_private_data_leaks_into_the_public_appearance(self):
        rec = ps._blank_record()
        rec["level"] = 3
        rec["history"] = [{"coins": 1000, "secret": "x"}]
        pub = ps._public_appearance(rec, "Reeflord")
        self.assertEqual(
            set(pub), {"nickname", "level", "badge", "title", "xp_bonus_pct",
                       "last_prestige_at", "name"})
        blob = repr(pub)
        for leak in ("coins", "history", "kept_avatars", "secret", "critter"):
            self.assertNotIn(leak, blob)

    def test_names_lookup_returns_only_players_with_something_to_show(self):
        make_account(self.db, "plain")
        rec = ps._blank_record(); rec["level"] = 2
        make_account(self.db, "fancy", prestige=rec)
        out = ps._names_payload(self.db, ["plain", "fancy"])
        self.assertIn("fancy", out["players"])
        self.assertNotIn("plain", out["players"])

    def test_names_lookup_resolves_a_display_name(self):
        rec = ps._blank_record(); rec["level"] = 5
        make_account(self.db, "byname", prestige=rec, nickname="Tideheart")
        install(self.db, uid_by_name=lambda _db, n: "byname" if n == "tideheart" else None)
        out = ps._names_payload(self.db, [], ["Tideheart", "Nobody"])
        self.assertIn("tideheart", out["by_name"])
        self.assertEqual(out["by_name"]["tideheart"]["level"], 5)
        self.assertNotIn("nobody", out["by_name"])


# ══════════════════════════════════════════════════════════════════════════
#  Appearance
# ══════════════════════════════════════════════════════════════════════════
class TestAppearance(unittest.TestCase):
    def setUp(self):
        self.db = FakeDb()
        install(self.db)

    def rec(self, **over):
        r = ps._blank_record()
        r.update(over)
        return r

    def test_a_locked_colour_is_refused(self):
        out, err = ps._validate_appearance({"mode": "solid", "colorId": "gold"}, self.rec())
        self.assertEqual(err, "color_locked")
        self.assertIsNone(out)

    def test_an_unlocked_colour_is_accepted(self):
        out, err = ps._validate_appearance(
            {"mode": "solid", "colorId": "ocean"}, self.rec(level=1, colors=["ocean"]))
        self.assertIsNone(err)
        self.assertEqual(out["color"], "#1f7ae0")

    def test_custom_colour_needs_prestige_four_and_passes_the_readability_gate(self):
        out, err = ps._validate_appearance({"mode": "custom", "color": "#1f7ae0"}, self.rec())
        self.assertEqual(err, "custom_color_locked")
        r = self.rec(level=4, custom_color=True)
        self.assertEqual(ps._validate_appearance({"mode": "custom", "color": "#e02020"}, r)[1],
                         "color_reserved")
        self.assertEqual(ps._validate_appearance({"mode": "custom", "color": "nope"}, r)[1],
                         "bad_color")
        out, err = ps._validate_appearance({"mode": "custom", "color": "#0f8f5f"}, r)
        self.assertIsNone(err)
        self.assertEqual(out["color"], "#0f8f5f")

    def test_gradients_need_prestige_five_and_three_colours_need_ten(self):
        self.assertEqual(
            ps._validate_appearance({"mode": "gradient", "gradientId": "ocean-seafoam"}, self.rec())[1],
            "gradient_locked")
        r = self.rec(level=5, custom_gradient=True, gradients=["ocean-seafoam"])
        out, err = ps._validate_appearance({"mode": "gradient", "gradientId": "ocean-seafoam"}, r)
        self.assertIsNone(err)
        self.assertEqual(out["from"], "#1f7ae0")
        self.assertEqual(
            ps._validate_appearance(
                {"mode": "gradient", "from": "#1f7ae0", "to": "#12a37c", "mid": "#7a49d6"}, r)[1],
            "three_color_locked")

    def test_a_locked_effect_or_background_or_skin_is_refused(self):
        r = self.rec(level=1)
        self.assertEqual(ps._validate_appearance({"effect": "glow"}, r)[1], "effect_locked")
        self.assertEqual(ps._validate_appearance({"background": "pbg-celestial"}, r)[1],
                         "background_locked")
        self.assertEqual(ps._validate_appearance({"skin": "clownfish:golden"}, r)[1], "skin_locked")

    def test_animation_can_be_switched_off_while_keeping_the_colour(self):
        r = self.rec(level=6, colors=["ocean"], effects=["glow"])
        out, err = ps._validate_appearance(
            {"mode": "solid", "colorId": "ocean", "effect": "glow", "animate": False}, r)
        self.assertIsNone(err)
        self.assertEqual(out["effect"], "glow")
        self.assertFalse(out["animate"])
        self.assertEqual(out["color"], "#1f7ae0")

    def test_appearance_never_grants_anything(self):
        """Saving an appearance must not be a back door to unlocking one."""
        make_account(self.db, "u1", prestige=self.rec(level=1, colors=["ocean"]))
        ps._set_appearance(self.db, "u1", {"appearance": {
            "mode": "solid", "colorId": "ocean",
            "colors": ["gold"], "level": 99, "custom_color": True,
        }})
        rec = ps._record_of(self.db.collection("users").document("u1").get().to_dict())
        self.assertEqual(rec["level"], 1)
        self.assertEqual(rec["colors"], ["ocean"])
        self.assertFalse(rec["custom_color"])


# ══════════════════════════════════════════════════════════════════════════
#  Admin
# ══════════════════════════════════════════════════════════════════════════
class TestAdmin(unittest.TestCase):
    def setUp(self):
        self.db = FakeDb()
        install(self.db)
        make_account(self.db)
        ps._commit(self.db, "u1", good_body(self.db))

    def test_history_is_visible_to_an_admin_with_the_ledger(self):
        out = ps._admin_history(self.db, "u1")
        self.assertTrue(out["ok"])
        self.assertEqual(out["prestige"], 1)
        self.assertEqual(len(out["ledger"]), 1)
        self.assertEqual(out["ledger"][0]["new_prestige"], 1)

    def test_an_admin_can_correct_a_skin_and_it_is_logged(self):
        rec = ps._record_of(self.db.collection("users").document("u1").get().to_dict())
        old = f"{rec['skins'][0]['animal']}:{rec['skins'][0]['style']}"
        out = ps._admin_correct(self.db, "u1", {
            "action": "replace_skin", "old": old, "animal": "narwhal", "style": "albino",
        }, "tim")
        self.assertTrue(out["ok"])
        rec2 = ps._record_of(self.db.collection("users").document("u1").get().to_dict())
        self.assertEqual(rec2["skins"][0]["animal"], "narwhal")
        self.assertTrue(rec2["skins"][0]["corrected"])
        self.assertEqual(len(self.db.collection("prestige_admin_log")._docs), 1)

    def test_restore_rewards_is_additive_and_safe_to_run_twice(self):
        self.db.collection("users").document("u1").set(
            {"prestige": {"colors": [], "backgrounds": []}}, merge=True)
        ps._admin_correct(self.db, "u1", {"action": "restore_rewards"}, "tim")
        a = ps._record_of(self.db.collection("users").document("u1").get().to_dict())
        ps._admin_correct(self.db, "u1", {"action": "restore_rewards"}, "tim")
        b = ps._record_of(self.db.collection("users").document("u1").get().to_dict())
        self.assertEqual(a["colors"], b["colors"])
        self.assertEqual(a["backgrounds"], b["backgrounds"])
        self.assertIn("pbg-shallows", a["backgrounds"])

    def test_an_admin_cannot_move_the_prestige_level_or_mint_coins(self):
        before = self.db.collection("users").document("u1").get().to_dict()
        out = ps._admin_correct(self.db, "u1", {
            "action": "grant_background", "background": "pbg-celestial"}, "tim")
        self.assertFalse(out["ok"])          # not earned at Prestige 1
        out2 = ps._admin_correct(self.db, "u1", {"action": "set_level", "level": 10}, "tim")
        self.assertEqual(out2["error"], "unknown_action")
        after = self.db.collection("users").document("u1").get().to_dict()
        self.assertEqual(after["prestige"]["level"], before["prestige"]["level"])
        self.assertEqual(after["stats"]["critter_coins"], before["stats"]["critter_coins"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
