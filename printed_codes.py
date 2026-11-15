"""Currents and Critters: codes PRINTED on products, limited to the first run.

WHAT THIS IS
The plush hang tag prints one code under its QR: FEBRUARY2ND. It unlocks the
Fish avatar. The same word is on every tag in the first run of plushies, and
the Fish comes with THAT run only: once those plushies are sold, nobody can get
the Fish this way again. So the code has a fixed number of claims, one per
plushie in the first run, and when they are gone it stops working for good.

The browser cannot keep that count. Two people redeeming at the same moment
would both read "249 used" and both take the 250th, and anyone can edit a page's
JavaScript. So the count lives here, and ONE Firestore transaction does all of:

    printed_code_claims/{CODE}                 {claimed, cap?}   the counter
    printed_code_claims/{CODE}/accounts/{uid}  {number, at}      one per account
    users/{uid}.unlocked_icons                 + the reward       the grant

Either all three happen or none do, so the counter can never say a claim was
spent that did not unlock anything, and it can never go past the cap.

THE RULES
- Signed in only. The uid comes off a verified Firebase ID token, never the body.
- Once per account, forever. The ledger doc stays even if the Fish is traded
  away, so trading it off and typing the code again gets nothing.
- An account that already owns the Fish (from a trade or a donation code) is
  told so and does NOT use up one of the claims.
- When `claimed` reaches the cap: "sold_out", for everybody, permanently.

THE CAP
Default FIRST_RUN_DEFAULT (250, the first production run). Change it without a
deploy by setting `cap` on the printed_code_claims/FEBRUARY2ND document in the
Firebase console (0 closes it now), or with the PRINTED_CODE_FEBRUARY2ND_CAP env
var on Render. The document wins over the env var, the env var over the default.

    printed_codes.init(...)        wire up (multiplayer_server)
    printed_codes.match(code)      which printed code was typed, or None
    printed_codes.claim(uid, code) spend one claim, transactionally
    printed_codes.status()         {cap, claimed, remaining, soldOut} per reward
    handle_get / handle_post       GET /api/printed-code/status,
                                   POST /api/printed-code/claim
"""

from __future__ import annotations

import os
import re
from typing import Any, Callable, Dict, List, Optional

_get_firestore: Optional[Callable[[], Any]] = None
_verify_token: Optional[Callable[[str], Optional[dict]]] = None


def init(*, get_firestore, verify_token) -> None:
    global _get_firestore, _verify_token
    _get_firestore = get_firestore
    _verify_token = verify_token


# The first run of plushies. One claim per plushie.
FIRST_RUN_DEFAULT = 250

COLLECTION = "printed_code_claims"

# id: the canonical code. `key`: the public name the status endpoint uses, so
# the website can show "how many are left" without printing the code itself.
PRINTED: List[Dict[str, Any]] = [
    {
        "id": "FEBRUARY2ND",
        "key": "fish",
        "reward_img": "/avatars/fish.png",
        "label": "Fish",
        "loose": [re.compile(r"^FEB[A-Z]{0,9}(?:2|2ND|SECOND)$"),
                  re.compile(r"^(?:2|2ND|SECOND)(?:OF)?FEB[A-Z]{0,9}$")],
    },
]

MESSAGES = {
    "unauthorized":    "Sign in to redeem a code.",
    "bad_code":        "Invalid code.",
    "no_profile":      "Your account is still being set up. Try again in a moment.",
    "already_owned":   "You already own {label}.",
    "already_claimed": "This account has already used the hang tag code.",
    "sold_out":        ("Every {label} from the first run of plushies has been claimed. "
                        "The {label} came only with the first run, so it can't be "
                        "unlocked anymore."),
    "unavailable":     "Could not redeem, try again.",
}


def normalize(raw: Any) -> str:
    """Same as normalizeRedeemCode() in preview-app.js: letters and digits only,
    uppercase. "February 2nd" and FEBRUARY2ND are one code."""
    return re.sub(r"[^A-Z0-9]", "", str(raw if raw is not None else "").upper())


def typo_distance(a: str, b: str) -> int:
    """codeTypoDistance() in preview-app.js: insert, delete, change, or swap two
    neighbouring letters, each ONE typo."""
    s, t = str(a or ""), str(b or "")
    d = [[0] * (len(t) + 1) for _ in range(len(s) + 1)]
    for i in range(len(s) + 1):
        d[i][0] = i
    for j in range(len(t) + 1):
        d[0][j] = j
    for i in range(1, len(s) + 1):
        for j in range(1, len(t) + 1):
            cost = 0 if s[i - 1] == t[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and s[i - 1] == t[j - 2] and s[i - 2] == t[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[len(s)][len(t)]


def match(code: Any) -> Optional[Dict[str, Any]]:
    """printedCodeReward() in preview-app.js. Six-digit donation codes are all
    digits, so nothing here can ever shadow one."""
    c = normalize(code)
    if not c or c.isdigit() or len(c) < 4 or len(c) > 32:
        return None
    for p in PRINTED:
        if c == p["id"] or any(r.match(c) for r in p["loose"]) or typo_distance(c, p["id"]) <= 2:
            return p
    return None


def _int_or_none(v: Any) -> Optional[int]:
    if isinstance(v, bool):
        return None
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None


def cap_for(p: Dict[str, Any], counter_doc: Optional[Dict[str, Any]] = None) -> int:
    doc_cap = _int_or_none((counter_doc or {}).get("cap"))
    if doc_cap is not None:
        return doc_cap
    env_cap = _int_or_none(os.environ.get("PRINTED_CODE_" + p["id"] + "_CAP", "").strip() or None)
    if env_cap is not None:
        return env_cap
    return FIRST_RUN_DEFAULT


def _norm_icon(s: Any) -> str:
    v = str(s or "").strip().lower().split("?")[0].split("#")[0]
    if v and not v.startswith("/"):
        v = "/" + v
    return v


def _err(code: str, p: Optional[Dict[str, Any]] = None, **extra: Any) -> Dict[str, Any]:
    label = (p or {}).get("label", "it")
    out = {"ok": False, "error": code, "message": MESSAGES[code].format(label=label)}
    out.update(extra)
    return out


def _transactional():
    from firebase_admin import firestore
    fn = getattr(firestore, "transactional", None)
    if fn is None:
        from google.cloud.firestore_v1 import transactional as fn  # type: ignore
    return fn


def _server_timestamp():
    try:
        from firebase_admin import firestore
        return getattr(firestore, "SERVER_TIMESTAMP", None)
    except Exception:  # noqa: BLE001
        return None


def claim(uid: str, code: Any) -> Dict[str, Any]:
    if not uid:
        return _err("unauthorized")
    p = match(code)
    if p is None:
        return _err("bad_code")
    db = _get_firestore() if _get_firestore else None
    if db is None:
        return _err("unavailable", p)

    counter_ref = db.collection(COLLECTION).document(p["id"])
    ledger_ref = counter_ref.collection("accounts").document(uid)
    user_ref = db.collection("users").document(uid)
    img = p["reward_img"]
    stamp = _server_timestamp()

    @_transactional()
    def _spend(t) -> Dict[str, Any]:
        # Every read before any write: Firestore transactions require it.
        csnap = counter_ref.get(transaction=t)
        lsnap = ledger_ref.get(transaction=t)
        usnap = user_ref.get(transaction=t)
        cdoc = (csnap.to_dict() or {}) if csnap.exists else {}
        claimed = _int_or_none(cdoc.get("claimed")) or 0
        cap = cap_for(p, cdoc)

        if not usnap.exists:
            return _err("no_profile", p)
        if lsnap.exists:
            return _err("already_claimed", p)
        udoc = usnap.to_dict() or {}
        icons = udoc.get("unlocked_icons")
        icons = list(icons) if isinstance(icons, list) else []
        if any(_norm_icon(i) == img for i in icons):
            return _err("already_owned", p)
        if claimed >= cap:
            return _err("sold_out", p, cap=cap, claimed=claimed, remaining=0)

        number = claimed + 1
        t.update(user_ref, {"unlocked_icons": icons + [img]})
        t.set(counter_ref, {"claimed": number, "updatedAt": stamp}, merge=True)
        t.set(ledger_ref, {"uid": uid, "number": number, "at": stamp})
        return {"ok": True, "reward_type": "avatar", "reward_img": img, "label": p["label"],
                "number": number, "cap": cap, "remaining": max(0, cap - number)}

    try:
        out = _spend(db.transaction())
    except Exception as exc:  # noqa: BLE001
        print(f"[printed-code] claim failed for {uid}: {exc}")
        return _err("unavailable", p)
    if out.get("ok"):
        print(f"[printed-code] {uid} claimed {p['id']} #{out['number']} of {out['cap']}")
    return out


def status() -> Dict[str, Any]:
    """Public, and it never names the code: {ok, fish: {cap, claimed, remaining, soldOut}}."""
    db = _get_firestore() if _get_firestore else None
    out: Dict[str, Any] = {"ok": True}
    for p in PRINTED:
        cdoc: Dict[str, Any] = {}
        known = False
        if db is not None:
            try:
                snap = db.collection(COLLECTION).document(p["id"]).get()
                cdoc = (snap.to_dict() or {}) if snap.exists else {}
                known = True
            except Exception as exc:  # noqa: BLE001
                print(f"[printed-code] status read failed: {exc}")
        cap = cap_for(p, cdoc)
        claimed = _int_or_none(cdoc.get("claimed")) or 0
        out[p["key"]] = {"label": p["label"], "cap": cap, "claimed": claimed if known else None,
                         "remaining": max(0, cap - claimed) if known else None,
                         "soldOut": (claimed >= cap) if known else None}
    return out


def _auth_uid(body: Dict[str, Any]) -> str:
    if not _verify_token:
        return ""
    claims = _verify_token(str(body.get("idToken") or ""))
    return str((claims or {}).get("uid") or "")


def handle_get(handler, parsed) -> bool:
    if parsed.path != "/api/printed-code/status":
        return False
    handler._send_json(status())
    return True


def handle_post(handler, parsed, body: Dict[str, Any]) -> bool:
    if parsed.path == "/api/printed-code/status":
        handler._send_json(status())
        return True
    if parsed.path != "/api/printed-code/claim":
        return False
    uid = _auth_uid(body if isinstance(body, dict) else {})
    if not uid:
        handler._send_json(_err("unauthorized"), status=401)
        return True
    handler._send_json(claim(uid, (body or {}).get("code")))
    return True
