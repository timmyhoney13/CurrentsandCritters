#!/usr/bin/env python3
"""The server's public surface: what a stranger is allowed to download, and
what the admin door does when nobody set a key.

Run:  python3 -m unittest test_security_surface -v

Two live holes are pinned shut here, both found by pointing curl at a running
server rather than by reading the code:

  1. THE STATIC ROOT.  do_GET() ends in SimpleHTTPRequestHandler.do_GET(), so
     the handler's `directory=` is downloadable by anyone who guesses a
     filename. It was BASE_DIR, the whole project. That published every server
     .py file, and far worse, multiplayer/state/<ROOM>.json, which holds each
     seat's token, the host control token, latest_private_hands (every
     player's hidden hand) and the private-room password hash. A seat token is
     the whole of a player's identity to this server: holding one lets you
     play their turns. Directory listing was on too, so /multiplayer/state/
     enumerated the live rooms first.

  2. THE ADMIN KEY.  /api/rooms/<id>/admin_mod fell back to the literal key
     "dog" when ADMIN_MOD_KEY was unset. This repository is public, so that
     was a published password on an endpoint whose ops include reveal (every
     opponent's hand), hand_add, deck_place and mint.

The other half of each test matters as much as the block: a fix that breaks
the game is not a fix, so the assets the browser really asks for are checked
to still be there.
"""

import json
import os
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

import multiplayer_server as mp


_SANDBOX = {}


def setUpModule():
    """Creating a room writes a checkpoint. Point that at a temp dir so these
    tests never leave rooms behind in the shipped state directory."""
    tmp = tempfile.mkdtemp(prefix="cc-security-test-")
    _SANDBOX["dir"] = tmp
    _SANDBOX["saved"] = {"ROOM_STATE_DIR": mp.ROOM_STATE_DIR}
    mp.ROOM_STATE_DIR = os.path.join(tmp, "state")
    os.makedirs(mp.ROOM_STATE_DIR, exist_ok=True)


def tearDownModule():
    for key, value in _SANDBOX.get("saved", {}).items():
        setattr(mp, key, value)
    shutil.rmtree(_SANDBOX.get("dir", ""), ignore_errors=True)


class LiveServerTest(unittest.TestCase):
    """Everything here goes through a real HTTP request to a real handler.
    The bug was in what the handler serves, so reading the routing table is
    exactly the check that missed it the first time."""

    @classmethod
    def setUpClass(cls):
        cls.server = mp.StableThreadingHTTPServer(("127.0.0.1", 0), mp.MultiplayerHandler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def get(self, path):
        """(status, body) for a GET, with the path sent EXACTLY as written so
        traversal probes are not helpfully normalised by the client."""
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code, exc.read()

    def post(self, path, payload):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code, json.loads(exc.read().decode())


class TheStaticRootIsTheClientDirectory(LiveServerTest):

    def test_the_handler_serves_from_the_client_directory(self):
        """The one line the whole leak hung on."""
        with open(os.path.join(mp.BASE_DIR, "multiplayer_server.py"), encoding="utf-8") as fh:
            source = fh.read()
        self.assertIn("directory=CLIENT_DIR", source)
        self.assertNotIn("directory=BASE_DIR", source,
                         "the static root is the whole project again")
        # The room state directory must live OUTSIDE the served tree. If a
        # future refactor moves either one, this is the assert that fires.
        served = os.path.realpath(mp.CLIENT_DIR)
        state = os.path.realpath(mp.ROOM_STATE_DIR)
        self.assertNotEqual(served, state)
        self.assertFalse(
            state.startswith(served + os.sep),
            "room checkpoints (seat tokens, private hands) are inside the static root")

    def test_saved_room_state_is_not_downloadable(self):
        """The worst of it: a checkpoint holds every seat token and every
        hidden hand, and it used to be one GET away."""
        for path in ("/multiplayer/state/",
                     "/multiplayer/state/IT2.json",
                     "/multiplayer/state/ITROOM.json",
                     "/multiplayer/competitive_games/leaderboard.json",
                     "/multiplayer/games_history/"):
            status, _ = self.get(path)
            self.assertNotEqual(status, 200, f"{path} is still being served")

    def test_the_server_source_is_not_downloadable(self):
        for path in ("/multiplayer_server.py", "/clan_server.py", "/account_email.py",
                     "/prestige_server.py", "/fish_ai_brain.json", "/Dockerfile",
                     "/render.yaml", "/requirements.txt"):
            status, _ = self.get(path)
            self.assertNotEqual(status, 200, f"{path} is still being served")

    def test_there_are_no_directory_listings(self):
        """Listing was how you found the room ids to ask for."""
        for path in ("/", "/multiplayer/", "/multiplayer/state/", "/avatars/", "/backgrounds/"):
            status, body = self.get(path)
            if status == 200:
                self.assertNotIn(b"Directory listing for", body,
                                 f"{path} still renders a browsable index")

    def test_traversal_out_of_the_client_directory_fails(self):
        for path in ("/../multiplayer_server.py",
                     "/../../multiplayer_server.py",
                     "/multiplayer/client/../../multiplayer_server.py",
                     "/multiplayer/client/..%2f..%2fclan_server.py",
                     "/..%2Fmultiplayer_server.py",
                     "/%2e%2e/%2e%2e/clan_server.py"):
            status, body = self.get(path)
            self.assertNotEqual(status, 200, f"{path} escaped the client directory")
            self.assertNotIn(b"import multiplayer_server", body)


class TheGameItselfStillLoads(LiveServerTest):
    """The block above is only correct if the browser can still fetch
    everything it needs. A 404 on the deck art is a broken game, not a secure
    one."""

    def test_the_pages_still_render(self):
        for path in ("/", "/rules", "/privacy", "/version.json",
                     "/manifest.webmanifest", "/sw.js", "/icon.svg"):
            status, _ = self.get(path)
            self.assertEqual(status, 200, f"{path} stopped working")

    def test_the_client_modules_still_load(self):
        for path in ("/js/preview-app.js", "/js/rulebook.js", "/css/preview.css"):
            status, body = self.get(path)
            self.assertEqual(status, 200, f"{path} stopped working")
            self.assertGreater(len(body), 100)

    def test_the_deck_art_still_loads(self):
        """index.html asks for the card back by its full historical path,
        which used to resolve through the old BASE_DIR root. It now has its
        own narrow route, and this is the test that says so."""
        status, body = self.get("/multiplayer/client/card-back.png")
        self.assertEqual(status, 200, "the deck art 404s: index.html shows a broken image")
        self.assertGreater(len(body), 100)

    def test_the_narrow_route_cannot_be_walked_out_of(self):
        """That route takes a filename, so it is worth proving the filename
        cannot be a path."""
        for path in ("/multiplayer/client/../../multiplayer_server.py",
                     "/multiplayer/client/subdir/card-back.png",
                     "/multiplayer/client/card-back.png.py"):
            status, _ = self.get(path)
            self.assertNotEqual(status, 200, f"{path} should not resolve")

    def test_the_html_carries_its_security_headers(self):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/")
        with urllib.request.urlopen(req, timeout=10) as resp:
            headers = {k.lower(): v for k, v in resp.getheaders()}
        # Clickjacking: nothing may frame the table.
        self.assertEqual(headers.get("x-frame-options"), "SAMEORIGIN")
        self.assertIn("frame-ancestors", headers.get("content-security-policy", ""))
        # Seat tokens ride in query strings, so full URLs must not leak offsite.
        self.assertEqual(headers.get("referrer-policy"), "strict-origin-when-cross-origin")
        self.assertEqual(headers.get("x-content-type-options"), "nosniff")


class TheAdminDoorHasNoDefaultKey(LiveServerTest):
    """admin_mod can reveal every hand and deal cards into one. It needs a
    secret that is not printed in a public repository."""

    def _room(self):
        status, out = self.post("/api/rooms", {
            "host_name": "Tester", "total_players": 2,
            "human_players": 1, "ai_players": 1})
        self.assertEqual(status, 200, out)
        return out["room_id"], out["seat_token"]

    def _admin(self, room, token, key, op="catalog"):
        return self.post(f"/api/rooms/{room}/admin_mod",
                         {"admin_key": key, "seat_token": token, "op": op})

    def setUp(self):
        self._saved_key = os.environ.get("ADMIN_MOD_KEY")
        os.environ.pop("ADMIN_MOD_KEY", None)

    def tearDown(self):
        if self._saved_key is None:
            os.environ.pop("ADMIN_MOD_KEY", None)
        else:
            os.environ["ADMIN_MOD_KEY"] = self._saved_key

    def test_dog_is_not_a_key(self):
        """The published default. Anyone holding a seat could read it off
        GitHub and reveal the table."""
        room, token = self._room()
        for guess in ("dog", "Dog", "DOG", "dog\n", "", "admin", "password"):
            status, out = self._admin(room, token, guess)
            self.assertEqual(status, 403, f"admin_key={guess!r} was accepted")
            self.assertFalse(out.get("ok"))

    def test_with_no_key_configured_the_tools_are_off(self):
        room, token = self._room()
        status, out = self._admin(room, token, "anything at all")
        self.assertEqual(status, 403)
        self.assertIn("disabled", str(out.get("error", "")).lower())

    def test_reveal_is_refused_the_same_way(self):
        """catalog is harmless; reveal is the one that hands over the hands."""
        room, token = self._room()
        status, out = self._admin(room, token, "dog", op="reveal")
        self.assertEqual(status, 403)
        self.assertNotIn("hands", out)
        self.assertNotIn("brain", out)

    def test_the_real_key_still_works(self):
        """Locking the door is only right if Tim's own key opens it."""
        os.environ["ADMIN_MOD_KEY"] = "a-real-secret-value"
        room, token = self._room()
        status, out = self._admin(room, token, "a-real-secret-value")
        self.assertEqual(status, 200, out)
        self.assertTrue(out.get("ok"))

    def test_a_wrong_key_is_refused_even_when_one_is_configured(self):
        os.environ["ADMIN_MOD_KEY"] = "a-real-secret-value"
        room, token = self._room()
        for guess in ("dog", "a-real-secret-valu", "a-real-secret-value ", "🐟" * 8):
            status, out = self._admin(room, token, guess)
            self.assertEqual(status, 403, f"admin_key={guess!r} was accepted")

    def test_the_key_alone_is_not_enough_without_a_seat(self):
        os.environ["ADMIN_MOD_KEY"] = "a-real-secret-value"
        room, _token = self._room()
        status, out = self._admin(room, "not-a-seat-token", "a-real-secret-value")
        self.assertEqual(status, 403)
        self.assertIn("seat token", str(out.get("error", "")).lower())


class TheKeyComparisonItself(unittest.TestCase):
    """_admin_key_ok is the one place every admin gate now goes through, so
    its two rules are worth stating outright."""

    def test_an_unset_secret_authorises_nobody(self):
        for expected in ("", None, 0, [], "   "[:0]):
            self.assertFalse(mp._admin_key_ok("anything", expected))
        # Most importantly, not even a matching empty string.
        self.assertFalse(mp._admin_key_ok("", ""))

    def test_a_matching_key_passes(self):
        self.assertTrue(mp._admin_key_ok("s3cret", "s3cret"))

    def test_a_non_matching_key_fails(self):
        for guess in ("s3cre", "s3cret ", "S3cret", "s3cret\n"):
            self.assertFalse(mp._admin_key_ok(guess, "s3cret"))

    def test_a_non_ascii_guess_is_refused_not_a_crash(self):
        """secrets.compare_digest() raises TypeError on a str holding
        non-ASCII, which turned a junk key into a 500. Both sides are bytes
        now, so a junk key is simply wrong."""
        self.assertFalse(mp._admin_key_ok("🐟🐟🐟", "s3cret"))
        self.assertFalse(mp._admin_key_ok("s3cret", "🐟🐟🐟"))
        self.assertTrue(mp._admin_key_ok("🐟🐟🐟", "🐟🐟🐟"))

    def test_a_non_string_is_refused(self):
        for junk in (None, 1234, ["s3cret"], {"key": "s3cret"}):
            self.assertFalse(mp._admin_key_ok(junk, "s3cret"))

    def test_no_admin_gate_compares_keys_with_a_plain_equals(self):
        """Three gates used `!=`, which leaks how many leading characters a
        guess got right. Read the source back so a new gate cannot quietly
        reintroduce one."""
        with open(os.path.join(mp.BASE_DIR, "multiplayer_server.py"), encoding="utf-8") as fh:
            source = fh.read()
        for leak in ("!= env_key", "== env_key", "!= effective_key", "== effective_key"):
            self.assertNotIn(leak, source,
                             f"an admin key is compared with `{leak}`: use _admin_key_ok()")


class RegistrationCountsRealAccounts(LiveServerTest):
    """/api/user/register took whatever uid the caller typed, added it to
    seen_uids in site_stats.json forever and incremented the public
    registered-players number on the marketing site."""

    def test_a_bare_uid_is_not_a_registration(self):
        status, out = self.post("/api/user/register", {"uid": "somebody-elses-uid"})
        self.assertEqual(status, 401)
        self.assertFalse(out.get("ok"))

    def test_a_junk_token_is_not_a_registration(self):
        for token in ("", "not-a-token", "a.b.c"):
            status, out = self.post("/api/user/register", {"idToken": token})
            self.assertEqual(status, 401)
            self.assertFalse(out.get("ok"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
