"""Currents and Critters: the website's "Which critter should be a plushy?" poll.

WHY THIS IS NOT A SIGN-IN FEATURE
The question it asks is aimed at people who have never opened the game: the
reader who just scrolled past Meet the Critters and has an opinion about which
one they would want on a shelf. Putting an account between that reader and that
opinion loses the opinion. So a ballot is anonymous, and the only identity in
it is a random id the browser makes for itself the first time it votes, kept in
localStorage so the SAME browser updates its own ballot instead of stuffing the
box every time the page is reloaded.

That id is not a secret and not a proof of anything. It is a cursor, nothing
more, so the abuse ceiling is held somewhere that does not depend on it:

  1. ONE BALLOT PER VOTER ID, and a voter id is a hex string this module
     insists on. A caller who makes up a fresh id gets a fresh ballot, which is
     why rule 2 exists.
  2. RATE LIMITED PER IP. A browser may update its own ballot freely, but the
     number of NEW ballots one address can open in an hour, and in a day, is
     capped. One household voting on four phones is fine; a script cycling
     thousands of ids is not.
  3. THE COLLECTION HAS A CEILING. Past MAX_BALLOTS the poll stops opening new
     ballots and says so, rather than letting a flood write Firestore until the
     quota bill arrives.
  4. NOTHING A VOTER TYPES IS SHOWN TO ANOTHER VOTER. The optional note goes
     into the ballot for the dev to read; the public tally is counts only. A
     poll that republished strangers' text would be a graffiti wall.

WHERE THE VOTES LIVE
Firestore, one document per ballot under BALLOTS_COLLECTION. NOT a file on
disk: the Render service this runs on is the free tier, its disk is not in
force, and the site's own stats have already been lost that way once. A ballot
is somebody's answer to a question that was asked in public, so losing it to a
redeploy is not acceptable.

The tally is counted by reading the ballots, not by keeping a counter document
next to them. A counter is the thing that drifts: it needs a transaction on
every write and it is wrong forever the moment one is lost. Reading a few
hundred small documents costs a fraction of a second, and the answer is cached
for TALLY_TTL_SEC so a page that is being shared around does not re-read the
collection once per visitor.

    plush_vote.init(get_firestore=...)      wire up Firestore
    plush_vote.cast(body, client)           validate + record one ballot
    plush_vote.tally()                      {counts, firsts, ballots}
    plush_vote.handle_get(...)              GET  /api/plush/tally
    plush_vote.handle_post(...)             POST /api/plush/vote
"""

from __future__ import annotations

import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

# ═══════════════════════════════════════════════════════════════════════════
#  WIRING
# ═══════════════════════════════════════════════════════════════════════════
_get_firestore: Callable[[], Any] = lambda: None


def init(*, get_firestore) -> None:
    global _get_firestore
    _get_firestore = get_firestore


# ═══════════════════════════════════════════════════════════════════════════
#  THE BALLOT PAPER
# ═══════════════════════════════════════════════════════════════════════════
# Every animal in the deck, as (slug, name, family). The slug is the art file
# name under assets/critters/, which is what the website's own CRITTERS table
# keys on, and test_plush_vote.py compares the two lists item for item: a vote
# for a critter this list does not know is refused, so a typo on either side
# would silently throw away real votes rather than drift quietly.
CRITTERS: List[Tuple[str, str, str]] = [
    ('emperor-penguin',         'Emperor Penguin',           'bird'),
    ('horned-puffin',           'Horned Puffin',             'bird'),
    ('california-gull',         'California Gull',           'bird'),
    ('peruvian-pelican',        'Peruvian Pelican',          'bird'),
    ('great-albatross',         'Great Albatross',           'bird'),
    ('osprey',                  'Osprey',                    'bird'),
    ('magnificent-frigatebird', 'Magnificent Frigatebird',   'bird'),
    ('razorbill-auk',           'Razorbill Auk',             'bird'),
    ('spinner-dolphin',         'Spinner Dolphin',           'mammal'),
    ('bottlenose-dolphin',      'Bottlenose Dolphin',        'mammal'),
    ('narwhal',                 'Narwhal',                   'mammal'),
    ('mahi-mahi',               'Mahi Mahi',                 'gamefish'),
    ('blue-marlin',             'Blue Marlin',               'gamefish'),
    ('big-eye-tuna',            'Bigeye Tuna',               'crosscurrent'),
    ('yellowfin-tuna',          'Yellowfin Tuna',            'gamefish'),
    ('roosterfish',             'Roosterfish',               'gamefish'),
    ('king-salmon',             'King Salmon',               'gamefish'),
    ('tarpon',                  'Tarpon',                    'gamefish'),
    ('barracuda',               'Barracuda',                 'gamefish'),
    ('sailfish',                'Sailfish',                  'gamefish'),
    ('goliath-grouper',         'Goliath Grouper',           'gamefish'),
    ('mullet',                  'Mullet',                    'baitfish'),
    ('bunker',                  'Bunker',                    'baitfish'),
    ('sardine',                 'Sardine',                   'baitfish'),
    ('flying-fish',             'Flying Fish',               'baitfish'),
    ('bonito',                  'Bonito',                    'baitfish'),
    ('mantis-shrimp',           'Mantis Shrimp',             'crustacean'),
    ('spiny-lobster',           'Spiny Lobster',             'crustacean'),
    ('lobster',                 'Lobster',                   'crustacean'),
    ('king-crab',               'King Crab',                 'crustacean'),
    ('hermit-crab',             'Hermit Crab',               'crustacean'),
    ('bobtail-squid',           'Bobtail Squid',             'cephalopod'),
    ('common-octopus',          'Common Octopus',            'cephalopod'),
    ('cuttlefish',              'Cuttlefish',                'cephalopod'),
    ('giant-squid',             'Giant Squid',               'cephalopod'),
    ('staghorn-coral',          'Staghorn Coral',            'coral'),
    ('deep-sea-coral',          'Deep Sea Coral',            'coral'),
    ('grooved-brain-coral',     'Grooved Brain Coral',       'coral'),
    ('elk-horn-coral',          'Elk Horn Coral',            'coral'),
    ('orange-tube-sponge',      'Orange Tube Sponge',        'invert'),
    ('sea-urchin',              'Sea Urchin',                'invert'),
    ('common-sea-star',         'Common Sea Star',           'invert'),
    ('johnsons-sea-cucumber',   'Johnson’s Sea Cucumber',    'invert'),
    ('red-beaded-anemone',      'Red Beaded Anemone',        'invert'),
    ('cleaner-wrasse',          'Cleaner Wrasse',            'crosscurrent'),
    ('mandarin-gobie',          'Mandarin Goby',             'crosscurrent'),
    ('loggerhead-sea-turtle',   'Loggerhead Sea Turtle',     'crosscurrent'),
    ('blue-tang',               'Blue Tang',                 'crosscurrent'),
    ('clownfish',               'Clownfish',                 'crosscurrent'),
    ('manta-ray',               'Manta Ray',                 'crosscurrent'),
    ('great-white-shark',       'Great White Shark',         'crosscurrent'),
    ('humuhumunukunukuapuaa',   'Humuhumunukunukuapua‘a',    'crosscurrent'),
    ('whale-shark',             'Whale Shark',               'crosscurrent'),
]
SLUGS: List[str] = [c[0] for c in CRITTERS]
SLUG_SET = set(SLUGS)
NAMES: Dict[str, str] = {c[0]: c[1] for c in CRITTERS}
FAMILIES: Dict[str, str] = {c[0]: c[2] for c in CRITTERS}

# ═══════════════════════════════════════════════════════════════════════════
#  LIMITS
# ═══════════════════════════════════════════════════════════════════════════
BALLOTS_COLLECTION = "plush_votes"

MAX_PICKS = 3            # "pick up to three, favourite first"
NOTE_MAX = 400           # the optional "why that one", for the dev to read
NAME_MAX = 40            # the optional "who are you", same
VOTER_RE = re.compile(r"^[a-f0-9]{16,40}$")

# A browser may change its mind as often as it likes; what is capped is how
# many NEW ballots one address can open. The per-minute cap is the only one an
# honest voter can reach, and only by hammering submit.
VOTES_PER_MIN = 10
NEW_PER_HOUR = 12
NEW_PER_DAY = 40

# Past this the poll stops opening ballots. Reading the tally walks the
# collection, so this is also what keeps that read cheap.
MAX_BALLOTS = 20000

# How long a counted tally is served before the collection is read again. A
# voter always sees their own vote immediately: cast() recounts.
TALLY_TTL_SEC = 45.0

_RL: Dict[str, Tuple[int, float]] = {}
_RL_LOCK = threading.Lock()

_TALLY_LOCK = threading.Lock()
_TALLY_CACHE: Dict[str, Any] = {"at": 0.0, "value": None}


def _rate_ok(bucket: str, client: str, limit: int, window: int) -> bool:
    now = time.time()
    key = "%s|%s|%d" % (bucket, client, int(now // window))
    with _RL_LOCK:
        if len(_RL) > 4000:
            for k in [k for k, v in _RL.items() if v[1] < now - 2 * window]:
                _RL.pop(k, None)
        count, _ = _RL.get(key, (0, now))
        count += 1
        _RL[key] = (count, now)
        return count <= limit


def _client_key(handler) -> str:
    """Best-effort client identity for rate limiting only. Render sits behind a
    proxy, so the left-most X-Forwarded-For entry is the real caller; it is
    spoofable, which is exactly why it never decides anything but a limit."""
    try:
        xff = handler.headers.get("X-Forwarded-For", "") if handler.headers else ""
        if xff:
            return xff.split(",")[0].strip()[:64]
        return str(handler.client_address[0])[:64]
    except Exception:  # noqa: BLE001
        return "unknown"


# ═══════════════════════════════════════════════════════════════════════════
#  VALIDATION
# ═══════════════════════════════════════════════════════════════════════════
def _one_line(value: Any, limit: int) -> str:
    """A single line of somebody else's typing: no CR, no LF, no control
    characters, collapsed whitespace, capped. The name on a ballot is read in a
    terminal next to 52 others, so it does not get to be three lines tall."""
    text = str(value if value is not None else "")
    text = re.sub(r"[\x00-\x1f\x7f]", " ", text)
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _many_lines(value: Any, limit: int) -> str:
    """The note: newlines survive, because it is prose. Everything else that
    can hide in a control character does not, and a CRLF becomes one LF so the
    stored document reads the same wherever it is opened."""
    text = str(value if value is not None else "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[\x00-\x09\x0b-\x1f\x7f]", " ", text)
    return re.sub(r"[ \t]+", " ", text).strip()[:limit]


def clean_picks(raw: Any) -> List[str]:
    """The picks, in the order they were sent, de-duplicated, every one of them
    a critter this deck actually has. An unknown slug is DROPPED rather than
    failing the whole ballot: one stale name in a cached page should not throw
    away the two good picks beside it."""
    out: List[str] = []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return out
    for item in raw:
        slug = str(item or "").strip().lower()
        if slug in SLUG_SET and slug not in out:
            out.append(slug)
        if len(out) >= MAX_PICKS:
            break
    return out


def enabled() -> bool:
    """Can this deploy actually record a vote? A poll that takes clicks and
    stores nothing looks perfectly healthy from the website, which is the whole
    reason /api/plush/tally reports this."""
    try:
        return _get_firestore() is not None
    except Exception:  # noqa: BLE001
        return False


# ═══════════════════════════════════════════════════════════════════════════
#  COUNTING
# ═══════════════════════════════════════════════════════════════════════════
def _count(db) -> Dict[str, Any]:
    """Walk the ballots once and add them up. `counts` is how many people put a
    critter anywhere on their ballot, `firsts` is how many made it their first
    pick: the second is the tie-break, and both are worth showing because
    "nearly everyone's number two" is a different fact from "nobody's number
    one"."""
    counts: Dict[str, int] = {}
    firsts: Dict[str, int] = {}
    ballots = 0
    for snap in db.collection(BALLOTS_COLLECTION).stream():
        data = snap.to_dict() or {}
        picks = clean_picks(data.get("picks"))
        if not picks:
            continue
        ballots += 1
        for i, slug in enumerate(picks):
            counts[slug] = counts.get(slug, 0) + 1
            if i == 0:
                firsts[slug] = firsts.get(slug, 0) + 1
    return {"counts": counts, "firsts": firsts, "ballots": ballots}


def tally(*, force: bool = False) -> Dict[str, Any]:
    """The public result: counts only, never a word anybody typed."""
    now = time.time()
    with _TALLY_LOCK:
        cached = _TALLY_CACHE.get("value")
        if cached is not None and not force and now - _TALLY_CACHE["at"] < TALLY_TTL_SEC:
            return dict(cached)

    db = None
    try:
        db = _get_firestore()
    except Exception as exc:  # noqa: BLE001
        print("[plush] firestore unavailable: %s" % exc)
    if db is None:
        return {"ok": True, "enabled": False, "ballots": 0,
                "counts": {}, "firsts": {}, "updated": int(now)}

    try:
        got = _count(db)
    except Exception as exc:  # noqa: BLE001
        print("[plush] tally failed: %s" % exc)
        with _TALLY_LOCK:
            stale = _TALLY_CACHE.get("value")
        if stale is not None:
            return dict(stale)
        return {"ok": False, "enabled": True, "error": "tally_failed",
                "ballots": 0, "counts": {}, "firsts": {}, "updated": int(now)}

    out = {"ok": True, "enabled": True, "ballots": got["ballots"],
           "counts": got["counts"], "firsts": got["firsts"], "updated": int(now)}
    with _TALLY_LOCK:
        _TALLY_CACHE["value"] = dict(out)
        _TALLY_CACHE["at"] = now
    return out


# ═══════════════════════════════════════════════════════════════════════════
#  VOTING
# ═══════════════════════════════════════════════════════════════════════════
def cast(body: Dict[str, Any], client: str = "") -> Dict[str, Any]:
    """Record one ballot. Returns the fresh tally on success so the page can
    show the result of the click that produced it without a second request."""
    body = body or {}

    voter = str(body.get("voter") or "").strip().lower()
    if not VOTER_RE.match(voter):
        return {"ok": False, "error": "bad_voter",
                "message": "That vote did not carry a voter id. Reload the page and try again."}

    picks = clean_picks(body.get("picks"))
    if not picks:
        return {"ok": False, "error": "no_picks",
                "message": "Pick at least one critter first."}

    if not _rate_ok("vote", client, VOTES_PER_MIN, 60):
        return {"ok": False, "error": "too_many_requests",
                "message": "That is a lot of votes at once. Give it a minute."}

    db = None
    try:
        db = _get_firestore()
    except Exception as exc:  # noqa: BLE001
        print("[plush] firestore unavailable: %s" % exc)
    if db is None:
        return {"ok": False, "error": "unavailable",
                "message": "The vote is not reachable right now. Try again in a minute."}

    try:
        ref = db.collection(BALLOTS_COLLECTION).document(voter)
        snap = ref.get()
        existing = (snap.to_dict() or {}) if getattr(snap, "exists", False) else None
    except Exception as exc:  # noqa: BLE001
        print("[plush] could not read ballot %s: %s" % (voter, exc))
        return {"ok": False, "error": "unavailable",
                "message": "The vote is not reachable right now. Try again in a minute."}

    now = int(time.time())
    if existing is None:
        # A NEW ballot is the only thing worth rationing: an update is the same
        # person changing their mind, and refusing that would freeze a vote
        # somebody regrets.
        if not _rate_ok("new-hour", client, NEW_PER_HOUR, 3600):
            return {"ok": False, "error": "too_many_requests",
                    "message": "That address has opened a lot of ballots today. Try again later."}
        if not _rate_ok("new-day", client, NEW_PER_DAY, 86400):
            return {"ok": False, "error": "too_many_requests",
                    "message": "That address has opened a lot of ballots today. Try again later."}
        counted = tally()
        if int(counted.get("ballots") or 0) >= MAX_BALLOTS:
            return {"ok": False, "error": "poll_full",
                    "message": "This poll has all the votes it can hold. Thank you anyway."}

    doc = {
        "picks": picks,
        "note": _many_lines(body.get("note"), NOTE_MAX),
        "name": _one_line(body.get("name"), NAME_MAX),
        "ip": _one_line(client, 64),
        "updated": now,
        "created": int((existing or {}).get("created") or now),
    }
    try:
        ref.set(doc)
    except Exception as exc:  # noqa: BLE001
        print("[plush] could not store ballot %s: %s" % (voter, exc))
        return {"ok": False, "error": "unavailable",
                "message": "That vote did not save. Try again in a minute."}

    print("[plush] ballot %s %s: %s" % (voter[:8], "updated" if existing else "cast",
                                        ", ".join(picks)))
    out = tally(force=True)
    out["ok"] = True
    out["yours"] = picks
    out["changed"] = existing is not None
    return out


# ═══════════════════════════════════════════════════════════════════════════
#  HTTP
# ═══════════════════════════════════════════════════════════════════════════
def handle_get(handler, parsed) -> bool:
    """GET /api/plush/tally  ->  {ok, enabled, ballots, counts, firsts}"""
    if parsed.path not in ("/api/plush/tally", "/api/plush/state"):
        return False
    out = tally()
    handler._send_json(out, status=200 if out.get("ok") else 503)
    return True


def handle_post(handler, parsed, body: Dict[str, Any]) -> bool:
    """POST /api/plush/vote  { voter, picks: [slug, ...], note?, name? }"""
    path = parsed.path
    if not path.startswith("/api/plush/"):
        return False
    action = path[len("/api/plush/"):].strip("/")

    if action in ("tally", "state"):
        out = tally()
        handler._send_json(out, status=200 if out.get("ok") else 503)
        return True

    if action == "vote":
        out = cast(body or {}, _client_key(handler))
        status = 200
        if not out.get("ok"):
            err = out.get("error")
            status = 429 if err == "too_many_requests" else 400
            if err in ("unavailable", "poll_full"):
                status = 503
        handler._send_json(out, status=status)
        return True

    return False
