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

  1. ONE BALLOT PER ADDRESS, and a vote with the box left empty is its own
     ballot, keyed by the random id the browser made for itself. A caller who
     makes up a fresh id gets a fresh ballot, which is why rule 2 exists. See
     ballot_key() for why the address decides this and not the device: a phone
     going round a table is how this poll gets answered, and keying on the
     device meant each person at the table erased the one before.
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

import csv
import hashlib
import io
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import newsletter_email as nl_email

# ═══════════════════════════════════════════════════════════════════════════
#  WIRING
# ═══════════════════════════════════════════════════════════════════════════
_get_firestore: Callable[[], Any] = lambda: None
# The newsletter's own public-signup function, injected rather than imported:
# newsletter_server imports THIS module for the admin tab, so importing it
# back would be a cycle. Injection also keeps the consent rules where they
# belong, in the module that owns the list.
_public_signup: Optional[Callable[..., Dict[str, Any]]] = None
_newsletter_source: str = "Plushy Vote"


def init(*, get_firestore, public_signup=None, newsletter_source: str = "") -> None:
    global _get_firestore, _public_signup, _newsletter_source
    _get_firestore = get_firestore
    _public_signup = public_signup
    if newsletter_source:
        _newsletter_source = newsletter_source


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

# The optional "tell me who wins" box takes an email address or a phone
# number, whichever the voter would rather give. An email is judged by
# newsletter_email.normalize_email, the one definition of an address this
# codebase has, so the plushy list and the newsletter can never disagree about
# what an address is. A phone number is kept as digits (with the + if they
# typed one), which is what makes "(615) 555-0100" and "615-555-0100" one
# person rather than two.
CONTACT_MAX = 160
PHONE_MIN_DIGITS = 7      # the shortest real subscriber number
PHONE_MAX_DIGITS = 15     # E.164's ceiling, country code included

# A browser may change its mind as often as it likes; what is capped is how
# many NEW ballots one address can open. The per-minute cap is the only one an
# honest voter can reach, and only by hammering submit.
#
# These are set for a DEVICE BEING PASSED ROUND A TABLE, which the website
# supports deliberately: one phone, one ballot per person, all from a single
# address. A dozen an hour was the right ceiling when every address meant one
# voter and is the wrong one now, and a refused vote at a table is a person
# standing there holding a phone that says no.
VOTES_PER_MIN = 15
NEW_PER_HOUR = 25
NEW_PER_DAY = 100

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


def _truthy(value: Any) -> bool:
    """A checkbox as it arrives over JSON: true, "true", "on", 1. Anything
    else, including the string "false" that a hand-rolled form can send, is
    NOT consent. When in doubt the answer is no."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value == 1
    return str(value or "").strip().lower() in ("true", "on", "yes", "1")


def clean_contact(raw: Any) -> Tuple[str, str]:
    """(value, kind) for the optional "tell me who wins" box, where kind is
    "email", "phone" or "" for nothing usable.

    An unusable answer comes back as ("", ""), and the caller TELLS the voter
    rather than dropping it quietly: somebody who typed their number wrong and
    was thanked anyway is somebody who thinks they are on the list and is not.
    """
    s = str(raw if raw is not None else "").strip()[:CONTACT_MAX]
    if not s:
        return "", ""
    if "@" in s:
        email = nl_email.normalize_email(s)
        return (email, "email") if email else ("", "")
    digits = re.sub(r"\D", "", s)
    if PHONE_MIN_DIGITS <= len(digits) <= PHONE_MAX_DIGITS:
        return ("+" if s.startswith("+") else "") + digits, "phone"
    return "", ""


# ═══════════════════════════════════════════════════════════════════════════
#  WHOSE BALLOT IS THIS?
# ═══════════════════════════════════════════════════════════════════════════
# THE ADDRESS IS THE BALLOT. The device is only the fallback.
#
# One document per device meant one vote per device, and a phone going round a
# table is how this poll actually gets answered: every person at the table
# overwrote the one before, and real votes were lost that way. So the rule is
# now the one a reader can be told in a sentence, which is also the rule the
# box on the page states:
#
#   an address or a number  ->  the ballot is keyed by a hash of it. The same
#                               address from a second device is the same person
#                               changing their mind; two addresses on one phone
#                               are two people.
#   the box left empty      ->  keyed by the browser's own random id, which the
#                               page mints fresh for every anonymous vote, so
#                               the next pair of hands just taps and sends.
#
# The address is HASHED, never a document id in the clear. A document id is not
# a secret: it turns up in logs, in error messages and in every stack trace,
# and nobody gave their email address to have it printed in one. The address
# itself is still stored IN the ballot, which is where it is needed.
#
# WHAT THIS DOES NOT DO is prove the address is yours. Anybody who knows it
# could overwrite the vote cast with it, exactly as anybody holding your phone
# could before. This is a poll about a plush toy, and the ceiling that matters
# is the per-address rate limit above, not the identity of a ballot.
def ballot_key(voter: str, contact: str = "", kind: str = "") -> str:
    """The document id for one ballot: the address if there is one, else the
    device. Hex either way, so it stays the shape VOTER_RE describes."""
    if contact:
        seed = "plush-ballot|%s|%s" % (kind, contact)
        return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]
    return voter


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

    # WHOSE BALLOT (see ballot_key above). This is settled before the read,
    # because it is what decides which document is read.
    asked = "contact" in body
    raw_contact = str(body.get("contact") or "").strip() if asked else ""
    new_contact, new_kind = clean_contact(raw_contact) if raw_contact else ("", "")

    doc_id = ballot_key(voter, new_contact, new_kind)
    try:
        ref = db.collection(BALLOTS_COLLECTION).document(doc_id)
        snap = ref.get()
        existing = (snap.to_dict() or {}) if getattr(snap, "exists", False) else None
    except Exception as exc:  # noqa: BLE001
        print("[plush] could not read ballot %s: %s" % (doc_id[:8], exc))
        return {"ok": False, "error": "unavailable",
                "message": "The vote is not reachable right now. Try again in a minute."}

    # A ballot cast BEFORE the address decided the ballot is keyed by the
    # device and already carries this same address. It STAYS WHERE IT IS and is
    # updated in place: that person keeps one ballot, with the note they left
    # and the day they voted, and nothing in the collection is moved or
    # removed to make room for the new rule. Every ballot that existed before
    # this change still exists after it, address and all.
    if new_kind and existing is None and doc_id != voter:
        try:
            older = db.collection(BALLOTS_COLLECTION).document(voter)
            older_snap = older.get()
            was = (older_snap.to_dict() or {}) if getattr(older_snap, "exists", False) else None
            if was and str(was.get("contact") or "") == new_contact:
                ref, doc_id, existing = older, voter, was
        except Exception as exc:  # noqa: BLE001
            print("[plush] could not look for an earlier ballot: %s" % exc)

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

    # The "tell me who wins" box. With a usable answer it IS the ballot, so
    # there is nothing left to resolve. Without one the ballot is the device's
    # and the rules are the ones that were always here: a body with NO contact
    # key leaves whatever is on file alone (an older page, or a ballot held on
    # a device since before this change), a key that is there and empty is the
    # voter clearing it, because the box the page shows is pre-filled with what
    # they last sent and emptying it has to mean something, and an answer
    # nobody can read keeps what was on file and SAYS SO. The vote itself still
    # counts: somebody who mistyped their number and was thanked anyway thinks
    # they are on the list and is not, but refusing their vote over a typo in
    # an optional box would lose the vote, which is worse.
    contact_error = ""
    if new_kind:
        contact, kind = new_contact, new_kind
    else:
        had = str((existing or {}).get("contact") or "")
        had_kind = str((existing or {}).get("contactKind") or "")
        contact, kind = ("", "") if (asked and not raw_contact) else (had, had_kind)
        if raw_contact:
            contact_error = ("That did not look like an email address or a "
                             "phone number, so the vote counted but the "
                             "reminder did not.")

    doc = {
        "picks": picks,
        "note": _many_lines(body.get("note"), NOTE_MAX),
        "name": _one_line(body.get("name"), NAME_MAX),
        "contact": contact,
        "contactKind": kind,
        "ip": _one_line(client, 64),
        "updated": now,
        "created": int((existing or {}).get("created") or now),
    }
    try:
        ref.set(doc)
    except Exception as exc:  # noqa: BLE001
        print("[plush] could not store ballot %s: %s" % (doc_id[:8], exc))
        return {"ok": False, "error": "unavailable",
                "message": "That vote did not save. Try again in a minute."}

    # The newsletter box, which is a SEPARATE question from the contact box
    # and is never ticked for anybody. Leaving a way to be told who wins does
    # not put somebody on the mailing list; this tick is the only thing that
    # does, and it goes through the newsletter's own public signup so that the
    # address is validated, recorded and welcomed exactly as it would be from
    # the newsletter form itself.
    joined, join_error = "", ""
    if _truthy(body.get("newsletter")):
        if kind != "email":
            join_error = ("The newsletter goes out by email, so add an email address "
                          "if you would like it.")
        elif _public_signup is None:
            join_error = "The newsletter is not reachable right now, so that part did not happen."
        else:
            try:
                res = _public_signup(contact, source=_newsletter_source,
                                     note="Ticked the newsletter box on the plushy vote.")
            except Exception as exc:  # noqa: BLE001
                print("[plush] newsletter signup failed: %s" % exc)
                res = {"ok": False, "error": "Could not sign you up just now."}
            if res.get("ok"):
                joined = str(res.get("message") or "You are on the newsletter list.")
            else:
                join_error = str(res.get("error") or "Could not sign you up just now.")

    print("[plush] ballot %s %s: %s%s%s" % (doc_id[:8], "updated" if existing else "cast",
                                            ", ".join(picks),
                                            " (+%s)" % kind if kind else "",
                                            " (+newsletter)" if joined else ""))
    out = tally(force=True)
    out["ok"] = True
    out["yours"] = picks
    out["changed"] = existing is not None
    # Echoed back so the page can re-fill the box on a browser that has lost
    # its local copy, and so it can say WHICH of the two it saved.
    out["contact"] = contact
    out["contactKind"] = kind
    if contact_error:
        out["contactError"] = contact_error
    if joined:
        out["newsletter"] = joined
    if join_error:
        out["newsletterError"] = join_error
    return out


# ═══════════════════════════════════════════════════════════════════════════
#  THE ADMIN SIDE: who asked to be told who won
# ═══════════════════════════════════════════════════════════════════════════
# These people gave an address or a number for ONE purpose, which the box they
# typed it into states: to be told which critter wins. They are NOT newsletter
# subscribers, they have not consented to a newsletter, and nothing here puts
# them in newsletterSubscribers. They are shown on their own tab of the
# newsletter admin because that is where Tim already goes to look at a list of
# people, and the tab says what the list is for.
#
# Everything below is read by a route that has already verified an admin ID
# token. None of it is reachable from /api/plush/*, which is public.
_CSV_DANGEROUS = ("=", "+", "-", "@", "\t")


def _csv_safe(value: Any) -> str:
    """A cell a spreadsheet will not execute. A phone number typed with a
    leading + is the everyday case here, so the guard matters more on this
    list than on most."""
    s = "" if value is None else str(value)
    s = s.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    if s and s[0] in _CSV_DANGEROUS:
        return "'" + s
    return s


def contact_rows(limit: int = 5000) -> List[Dict[str, Any]]:
    """Every ballot that left a way to be reached, newest first, one row per
    contact. The same person voting from their phone and their laptop gives
    two ballots and one row: whoever is sent the result should be told once."""
    db = None
    try:
        db = _get_firestore()
    except Exception as exc:  # noqa: BLE001
        print("[plush] firestore unavailable: %s" % exc)
    if db is None:
        return []

    best: Dict[str, Dict[str, Any]] = {}
    for snap in db.collection(BALLOTS_COLLECTION).stream():
        data = snap.to_dict() or {}
        contact = str(data.get("contact") or "").strip()
        if not contact:
            continue
        kind = str(data.get("contactKind") or "")
        picks = clean_picks(data.get("picks"))
        row = {
            "contact": contact[:CONTACT_MAX],
            "kind": kind if kind in ("email", "phone") else "",
            "picks": [NAMES[p] for p in picks],
            "name": _one_line(data.get("name"), NAME_MAX),
            "note": _many_lines(data.get("note"), NOTE_MAX),
            "at": int(data.get("updated") or data.get("created") or 0),
        }
        row["atIso"] = (time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(row["at"]))
                        if row["at"] else "")
        key = contact.lower()
        if key not in best or row["at"] >= best[key]["at"]:
            best[key] = row
    rows = sorted(best.values(), key=lambda r: r["at"], reverse=True)
    return rows[:max(1, int(limit))]


def admin_view(limit: int = 5000) -> Dict[str, Any]:
    """What the Plushy Vote tab draws: the standings, and the people who asked
    to hear the result."""
    counted = tally()
    rows = contact_rows(limit)
    ranked = sorted(
        [s for s in SLUGS if counted.get("counts", {}).get(s)],
        key=lambda s: (-counted["counts"][s], -counted.get("firsts", {}).get(s, 0), NAMES[s]),
    )
    standings = [{"slug": s, "name": NAMES[s], "family": FAMILIES[s],
                  "votes": counted["counts"][s],
                  "firsts": counted.get("firsts", {}).get(s, 0)} for s in ranked]
    return {
        "enabled": bool(counted.get("enabled")),
        "ballots": int(counted.get("ballots") or 0),
        "standings": standings,
        "contacts": rows,
        "emails": sum(1 for r in rows if r["kind"] == "email"),
        "phones": sum(1 for r in rows if r["kind"] == "phone"),
        "updated": int(counted.get("updated") or 0),
    }


def contacts_csv(rows: List[Dict[str, Any]]) -> str:
    """The list as a file. Carries no voter ids and no IP addresses: a voter
    id is the cursor that lets a browser change its own ballot, and nothing
    outside the server has any business holding one."""
    buf = io.StringIO()
    w = csv.writer(buf, quoting=csv.QUOTE_ALL, lineterminator="\n")
    w.writerow(["Contact", "Kind", "Name", "First pick", "Second", "Third",
                "Note", "Voted (UTC)"])
    for r in rows:
        picks = list(r.get("picks") or []) + ["", "", ""]
        at = int(r.get("at") or 0)
        w.writerow([
            _csv_safe(r.get("contact")), _csv_safe(r.get("kind")),
            _csv_safe(r.get("name")),
            _csv_safe(picks[0]), _csv_safe(picks[1]), _csv_safe(picks[2]),
            _csv_safe(r.get("note")),
            time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(at)) if at else "",
        ])
    return buf.getvalue()


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
