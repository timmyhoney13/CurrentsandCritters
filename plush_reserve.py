"""Currents and Critters: plush RESERVATIONS (the free, no-payment pre-order list).

WHAT THIS IS, AND WHAT IT IS NOT
The Weighted Whale Shark and the Weighted Loggerhead Sea Turtle are $44.99
each plus shipping and applicable sales tax; buy 3 or more (any mix) and every
plushie is $5 off, with free standard shipping to U.S. addresses. Paid pre-orders are not open yet:
the Squarespace shop has no payment processor and no shipping rates connected,
and this site never invents a shipping rate. So the pre-order page on both
websites (currentsandcritters.com/preorder and beardedsealstudios.com/preorder)
takes a RESERVATION: who you are, how many of each you want, and where it would
ship. Nothing is charged and nothing is binding. When pre-orders open, the
studio emails each reservation to confirm and pay at the real checkout, where
shipping and sales tax are calculated.

THE ONE CATALOG
Prices, the free-shipping threshold and the per-order caps are read from
plush/catalog.json, the same file the page's script draws from. Every total in
a stored reservation is RECOMPUTED HERE from that file. A browser's idea of the
price is never stored, so a page that has been edited, cached or tampered with
cannot put a different number into the list Tim will invoice from.

THE RULES (mirrors plush_vote.py and partner_contact.py)
  1. ONE RESERVATION PER EMAIL ADDRESS. The document id is a hash of the
     address, so a double click, a retry after a slow cold start or somebody
     coming back to change their mind all land on the same document: an update,
     never a duplicate. The page is told which happened and says so.
  2. RATE LIMITED PER IP. New reservations per address per hour and per day are
     capped; an update of an existing one is not, beyond a per-minute ceiling.
  3. A CEILING ON THE COLLECTION, so a flood cannot write Firestore until the
     quota bill arrives.
  4. NOTHING A CALLER SENDS DECIDES A PRICE, AN ADDRESS OR A RECIPIENT. The
     admin notification goes to ADMIN_EMAIL only and is best effort.
  5. THE NEWSLETTER BOX IS ITS OWN QUESTION. A reservation is not consent to a
     newsletter. Only the separate, never-pre-ticked box signs somebody up, and
     it does so through the newsletter's own public signup.
  6. NO FIRESTORE, NO SILENT SUCCESS. A form that takes clicks and stores
     nothing looks healthy from the website, so the status route reports
     enabled:false and reserve() refuses out loud.

    plush_reserve.init(get_firestore=..., public_signup=..., notify=...)
    plush_reserve.quote(items, country)     the pricing rules, in one place
    plush_reserve.reserve(body, client)     validate + store one reservation
    plush_reserve.handle_get / handle_post  GET/POST /api/plush/reserve
    plush_reserve.admin_view / reservations_csv    the admin tab
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import newsletter_email as nl_email

# ═══════════════════════════════════════════════════════════════════════════
#  THE CATALOG (plush/catalog.json, shared with the website)
# ═══════════════════════════════════════════════════════════════════════════
CATALOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plush", "catalog.json")


def _load_catalog(path: str = CATALOG_PATH) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    products = [p for p in data.get("products", []) if isinstance(p, dict) and p.get("id")]
    if not products:
        raise ValueError("plush/catalog.json has no products")
    for p in products:
        cents = int(p.get("priceCents"))
        if cents <= 0:
            raise ValueError("plush/catalog.json: %s has no price" % p.get("id"))
    return data


CATALOG: Dict[str, Any] = _load_catalog()
PRODUCTS: List[Dict[str, Any]] = list(CATALOG["products"])
PRODUCT_IDS: List[str] = [p["id"] for p in PRODUCTS]
PRICE_CENTS: Dict[str, int] = {p["id"]: int(p["priceCents"]) for p in PRODUCTS}
NAMES: Dict[str, str] = {p["id"]: str(p["name"]) for p in PRODUCTS}
FREE_SHIPPING_MIN: int = int(CATALOG.get("freeShippingMin", 3))
# 3 or more plushies: every plushie is this much off (any country).
BUNDLE_DISCOUNT_CENTS: int = int(CATALOG.get("bundleDiscountCents", 0))
FREE_SHIPPING_COUNTRIES = {str(c).upper() for c in CATALOG.get("freeShippingCountries", ["US"])}
MAX_PER_ITEM: int = int(CATALOG.get("maxPerItem", 10))
MAX_PER_ORDER: int = int(CATALOG.get("maxPerOrder", 20))

# The countries the form offers. "OTHER" is a real answer (the studio will tell
# them what shipping costs), not a refusal.
COUNTRIES: List[Tuple[str, str]] = [
    ("US", "United States"),
    ("CA", "Canada"),
    ("GB", "United Kingdom"),
    ("AU", "Australia"),
    ("OTHER", "Another country"),
]
COUNTRY_NAMES = dict(COUNTRIES)

# ═══════════════════════════════════════════════════════════════════════════
#  WIRING
# ═══════════════════════════════════════════════════════════════════════════
_get_firestore: Callable[[], Any] = lambda: None
_public_signup: Optional[Callable[..., Dict[str, Any]]] = None
_newsletter_source: str = "Plush Reservation"
_notify: Optional[Callable[[Dict[str, Any]], None]] = None


def init(*, get_firestore, public_signup=None, newsletter_source: str = "",
         notify: Optional[Callable[[Dict[str, Any]], None]] = None) -> None:
    global _get_firestore, _public_signup, _newsletter_source, _notify
    _get_firestore = get_firestore
    _public_signup = public_signup
    if newsletter_source:
        _newsletter_source = newsletter_source
    _notify = notify


def enabled() -> bool:
    try:
        return _get_firestore() is not None
    except Exception:  # noqa: BLE001
        return False


# ═══════════════════════════════════════════════════════════════════════════
#  LIMITS
# ═══════════════════════════════════════════════════════════════════════════
COLLECTION = "plush_reservations"
NAME_MAX = 60
ZIP_MAX = 12
MAX_RESERVATIONS = 20000

PER_MIN = 10          # any reservation call, per address per minute
NEW_PER_HOUR = 12     # NEW reservations (new emails) per address per hour
NEW_PER_DAY = 40

_RL: Dict[str, Tuple[int, float]] = {}
_RL_LOCK = threading.Lock()
_COUNT_LOCK = threading.Lock()
_COUNT_CACHE: Dict[str, Any] = {"at": 0.0, "value": None}
COUNT_TTL_SEC = 60.0


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
    try:
        xff = handler.headers.get("X-Forwarded-For", "") if handler.headers else ""
        if xff:
            return xff.split(",")[0].strip()[:64]
        return str(handler.client_address[0])[:64]
    except Exception:  # noqa: BLE001
        return "unknown"


# ═══════════════════════════════════════════════════════════════════════════
#  PRICING: the one place the rules live on the server
# ═══════════════════════════════════════════════════════════════════════════
def clean_items(raw: Any) -> Dict[str, int]:
    """{product id: whole quantity} for products the catalog has. Unknown ids
    are dropped; a quantity that is not a whole number from 0 to MAX_PER_ITEM
    is clamped into that range. Returned in catalog order, zeros included, so
    every stored reservation has the same shape."""
    out = {pid: 0 for pid in PRODUCT_IDS}
    if not isinstance(raw, dict):
        return out
    for pid in PRODUCT_IDS:
        value = raw.get(pid)
        # A bool is not a quantity (and True == 1 in Python, so it has to be
        # ruled out by type, not by value). NaN and infinity are not either.
        if isinstance(value, bool) or value is None or value == "":
            n = 0
        else:
            try:
                f = float(value)
                n = int(f) if f == f and f not in (float("inf"), float("-inf")) else 0
            except (TypeError, ValueError):
                n = 0
        out[pid] = max(0, min(MAX_PER_ITEM, n))
    return out


def quote(items: Dict[str, int], country: str = "US") -> Dict[str, Any]:
    """The order summary, worked out from the catalog. Shipping is never a
    number here: either free standard shipping (3 or more plushies, to a
    country the promotion covers) or calculated at checkout. Sales tax is
    always calculated at checkout."""
    items = clean_items(items)
    count = sum(items.values())
    lines = [{"id": pid, "name": NAMES[pid], "qty": items[pid],
              "unitCents": PRICE_CENTS[pid], "lineCents": items[pid] * PRICE_CENTS[pid]}
             for pid in PRODUCT_IDS if items[pid] > 0]
    list_cents = sum(line["lineCents"] for line in lines)
    country = (country or "US").upper()
    qualifies = count >= FREE_SHIPPING_MIN
    discount = count * BUNDLE_DISCOUNT_CENTS if qualifies else 0
    subtotal = list_cents - discount
    covered = country in FREE_SHIPPING_COUNTRIES
    return {
        "items": items,
        "lines": lines,
        "count": count,
        "listCents": list_cents,
        "discountCents": discount,
        "subtotalCents": subtotal,
        "qualifiesForFreeShipping": qualifies,
        "freeShipping": qualifies and covered,
        "shipping": "free" if (qualifies and covered) else "calculated",
        "tax": "calculated",
        "remainingForFreeShipping": max(0, FREE_SHIPPING_MIN - count),
    }


def money(cents: int) -> str:
    return "$%d.%02d" % (int(cents) // 100, int(cents) % 100)


# ═══════════════════════════════════════════════════════════════════════════
#  VALIDATION
# ═══════════════════════════════════════════════════════════════════════════
def _one_line(value: Any, limit: int) -> str:
    text = str(value if value is not None else "")
    text = re.sub(r"[\x00-\x1f\x7f]", " ", text)
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value == 1
    return str(value or "").strip().lower() in ("true", "on", "yes", "1")


ZIP_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 \-]{1,10}[A-Za-z0-9]$")
US_ZIP_RE = re.compile(r"^\d{5}(?:-\d{4})?$")


def validate(body: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
    """(clean fields, error). An empty error means it can be stored. The page
    makes these same checks first; this copy is the authority."""
    body = body or {}
    country = _one_line(body.get("country"), 8).upper() or "US"
    data = {
        "first": _one_line(body.get("first"), NAME_MAX),
        "last": _one_line(body.get("last"), NAME_MAX),
        "email": nl_email.normalize_email(body.get("email")),
        "country": country if country in COUNTRY_NAMES else "",
        "zip": _one_line(body.get("zip"), ZIP_MAX).upper(),
        "items": clean_items(body.get("items")),
        "newsletter": _truthy(body.get("newsletter")),
        "site": "bss" if str(body.get("site") or "").strip().lower() == "bss" else "cc",
    }
    if _one_line(body.get("company_site"), 200):
        return data, "honeypot"
    if not data["first"]:
        return data, "Please add your first name."
    if not data["last"]:
        return data, "Please add your last name."
    if not data["email"]:
        return data, "That email address doesn't look right."
    if not data["country"]:
        return data, "Please choose where it would ship."
    if data["zip"]:
        if data["country"] == "US" and not US_ZIP_RE.match(data["zip"]):
            return data, "That ZIP code doesn't look right. Use 5 digits, like 37203."
        if data["country"] != "US" and not ZIP_RE.match(data["zip"]):
            return data, "That postal code doesn't look right."
    count = sum(data["items"].values())
    if count < 1:
        return data, "Choose at least one plushie first."
    if count > MAX_PER_ORDER:
        return data, ("That's a lot of plushies! Reservations hold up to %d per order. "
                      "For a bigger order, email us." % MAX_PER_ORDER)
    return data, ""


def reservation_id(email: str) -> str:
    """The document id: a hash of the address, never the address in the clear
    (a document id turns up in logs and stack traces)."""
    return hashlib.sha256(("plush-reserve|" + email).encode("utf-8")).hexdigest()[:32]


def reference_for(doc_id: str) -> str:
    """A short reference a customer can quote in an email. Derived from the
    document id, so the same reservation always has the same reference."""
    return "CCR-" + doc_id[:6].upper()


def _stored_count(db) -> int:
    now = time.time()
    with _COUNT_LOCK:
        if _COUNT_CACHE["value"] is not None and now - _COUNT_CACHE["at"] < COUNT_TTL_SEC:
            return int(_COUNT_CACHE["value"])
    n = sum(1 for _ in db.collection(COLLECTION).stream())
    with _COUNT_LOCK:
        _COUNT_CACHE["value"] = n
        _COUNT_CACHE["at"] = now
    return n


# ═══════════════════════════════════════════════════════════════════════════
#  RESERVING
# ═══════════════════════════════════════════════════════════════════════════
UNAVAILABLE = ("Reservations aren't reachable right now. Please try again in a "
               "minute, or email us and we'll add you by hand.")


def reserve(body: Dict[str, Any], client: str = "") -> Dict[str, Any]:
    data, error = validate(body or {})
    if error == "honeypot":
        # A bot filled the hidden field. It is answered like a success and
        # nothing is stored: telling a bot which check failed teaches it.
        return {"ok": True, "reference": "CCR-000000", "updated": False,
                **quote(data["items"], data["country"] or "US")}
    if error:
        return {"ok": False, "error": "invalid", "message": error}

    if not _rate_ok("reserve-min", client, PER_MIN, 60):
        return {"ok": False, "error": "too_many_requests",
                "message": "That's a lot of reservations at once. Give it a minute."}

    db = None
    try:
        db = _get_firestore()
    except Exception as exc:  # noqa: BLE001
        print("[reserve] firestore unavailable: %s" % exc)
    if db is None:
        return {"ok": False, "error": "unavailable", "message": UNAVAILABLE}

    doc_id = reservation_id(data["email"])
    try:
        ref = db.collection(COLLECTION).document(doc_id)
        snap = ref.get()
        existing = (snap.to_dict() or {}) if getattr(snap, "exists", False) else None
    except Exception as exc:  # noqa: BLE001
        print("[reserve] could not read %s: %s" % (doc_id[:8], exc))
        return {"ok": False, "error": "unavailable", "message": UNAVAILABLE}

    if existing is None:
        if not _rate_ok("reserve-new-hour", client, NEW_PER_HOUR, 3600) or \
           not _rate_ok("reserve-new-day", client, NEW_PER_DAY, 86400):
            return {"ok": False, "error": "too_many_requests",
                    "message": "This connection has made a lot of reservations today. "
                               "Please try again later or email us."}
        try:
            if _stored_count(db) >= MAX_RESERVATIONS:
                return {"ok": False, "error": "full",
                        "message": "The reservation list is full for now. Email us and we'll add you."}
        except Exception as exc:  # noqa: BLE001
            print("[reserve] could not count reservations: %s" % exc)

    q = quote(data["items"], data["country"])
    now = int(time.time())
    doc = {
        "first": data["first"],
        "last": data["last"],
        "email": data["email"],
        "country": data["country"],
        "zip": data["zip"],
        "items": q["items"],
        "count": q["count"],
        "discountCents": q["discountCents"],
        "subtotalCents": q["subtotalCents"],
        "freeShipping": q["freeShipping"],
        "newsletter": bool(data["newsletter"]),
        "site": data["site"],
        "status": "reserved",
        "catalogVersion": int(CATALOG.get("version", 1)),
        "ip": _one_line(client, 64),
        "created": int((existing or {}).get("created") or now),
        "updated": now,
        "revisions": int((existing or {}).get("revisions") or 0) + (1 if existing else 0),
    }
    try:
        ref.set(doc)
    except Exception as exc:  # noqa: BLE001
        print("[reserve] could not store %s: %s" % (doc_id[:8], exc))
        return {"ok": False, "error": "unavailable", "message": UNAVAILABLE}
    with _COUNT_LOCK:
        if existing is None and _COUNT_CACHE["value"] is not None:
            _COUNT_CACHE["value"] = int(_COUNT_CACHE["value"]) + 1

    joined, join_error = "", ""
    if data["newsletter"]:
        if _public_signup is None:
            join_error = "The newsletter isn't reachable right now, so that part didn't happen."
        else:
            try:
                res = _public_signup(data["email"], source=_newsletter_source,
                                     note="Ticked the newsletter box on the plush reservation form.")
            except Exception as exc:  # noqa: BLE001
                print("[reserve] newsletter signup failed: %s" % exc)
                res = {"ok": False, "error": "Couldn't sign you up just now."}
            if res.get("ok"):
                joined = str(res.get("message") or "You're on the newsletter list.")
            else:
                join_error = str(res.get("error") or "Couldn't sign you up just now.")

    reference = reference_for(doc_id)
    if _notify is not None:
        try:
            _notify({**doc, "reference": reference, "updatedExisting": existing is not None})
        except Exception as exc:  # noqa: BLE001
            print("[reserve] notify failed: %s" % exc)

    print("[reserve] %s %s: %s plushie(s), %s%s" % (
        reference, "updated" if existing else "new", q["count"], money(q["subtotalCents"]),
        " (free shipping)" if q["freeShipping"] else ""))
    out = {"ok": True, "reference": reference, "updated": existing is not None, **q}
    if joined:
        out["newsletter"] = joined
    if join_error:
        out["newsletterError"] = join_error
    return out


# ═══════════════════════════════════════════════════════════════════════════
#  ADMIN NOTIFICATION (best effort, in the background)
# ═══════════════════════════════════════════════════════════════════════════
def _esc(value: Any) -> str:
    return (str(value if value is not None else "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#x27;"))


def build_admin_email(doc: Dict[str, Any]) -> Tuple[str, str, str]:
    q = quote(doc.get("items") or {}, doc.get("country") or "US")
    who = _one_line("%s %s" % (doc.get("first", ""), doc.get("last", "")), 130)
    verb = "updated" if doc.get("updatedExisting") else "new"
    subject = _one_line("Plush reservation (%s) %s: %s, %d plushie(s)"
                        % (verb, doc.get("reference", ""), who, q["count"]), 160)
    rows = [("Reference", doc.get("reference", "")), ("Name", who), ("Email", doc.get("email", "")),
            ("Ships to", "%s %s" % (COUNTRY_NAMES.get(doc.get("country", ""), doc.get("country", "")),
                                    doc.get("zip", "")))]
    rows += [(line["name"], "%d x %s = %s" % (line["qty"], money(line["unitCents"]), money(line["lineCents"])))
             for line in q["lines"]]
    if q.get("discountCents"):
        rows += [("3+ plushie deal", "-%s ($%d off each)" % (money(q["discountCents"]), BUNDLE_DISCOUNT_CENTS // 100))]
    rows += [("Merchandise subtotal", money(q["subtotalCents"])),
             ("Shipping", "Free standard shipping" if q["freeShipping"] else "Calculated at checkout"),
             ("Sales tax", "Calculated at checkout"),
             ("Newsletter", "Yes, ticked the box" if doc.get("newsletter") else "No"),
             ("From", "beardedsealstudios.com" if doc.get("site") == "bss" else "currentsandcritters.com")]
    text = "\n".join("%-22s %s" % (k + ":", v) for k, v in rows) + \
        "\n\nNo payment was taken. Reply to confirm when pre-orders open.\n"
    html = ('<div style="font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;'
            'font-size:15px;color:#12303d;max-width:560px"><p style="color:#5a7b88;font-size:13px">'
            'A plush reservation came in. No payment was taken.</p><table style="border-collapse:collapse">'
            + "".join('<tr><td style="padding:4px 14px 4px 0;color:#5a7b88">%s</td><td>%s</td></tr>'
                      % (_esc(k), _esc(v)) for k, v in rows) + "</table></div>")
    return subject, html, text


def email_admin(doc: Dict[str, Any]) -> None:
    """Tell the studio inbox, in a background thread so a slow mail provider
    never holds the customer's request open. Silently skipped when the deploy
    has no mail transport: the reservation is already safely stored."""
    def _send() -> None:
        try:
            if not nl_email.transport():
                return
            subject, html, text = build_admin_email(doc)
            for to in nl_email.admin_emails()[:3]:
                nl_email.send_email(to_email=to, subject=subject, html_body=html, text_body=text,
                                    is_bulk=False, stream="plush-reservation",
                                    reply_to_override=str(doc.get("email") or ""))
        except Exception as exc:  # noqa: BLE001
            print("[reserve] admin email failed: %s" % exc)
    threading.Thread(target=_send, daemon=True).start()


# ═══════════════════════════════════════════════════════════════════════════
#  THE ADMIN SIDE (read by a route that has already verified an admin token)
# ═══════════════════════════════════════════════════════════════════════════
_CSV_DANGEROUS = ("=", "+", "-", "@", "\t")


def _csv_safe(value: Any) -> str:
    s = "" if value is None else str(value)
    s = s.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    if s and s[0] in _CSV_DANGEROUS:
        return "'" + s
    return s


def reservation_rows(limit: int = 5000) -> List[Dict[str, Any]]:
    db = None
    try:
        db = _get_firestore()
    except Exception as exc:  # noqa: BLE001
        print("[reserve] firestore unavailable: %s" % exc)
    if db is None:
        return []
    rows = []
    for snap in db.collection(COLLECTION).stream():
        d = snap.to_dict() or {}
        q = quote(d.get("items") or {}, d.get("country") or "US")
        at = int(d.get("updated") or d.get("created") or 0)
        rows.append({
            "reference": reference_for(str(getattr(snap, "id", "") or reservation_id(str(d.get("email") or "")))),
            "first": _one_line(d.get("first"), NAME_MAX), "last": _one_line(d.get("last"), NAME_MAX),
            "email": str(d.get("email") or ""), "country": str(d.get("country") or ""),
            "zip": str(d.get("zip") or ""), "items": q["items"], "count": q["count"],
            "subtotalCents": q["subtotalCents"], "freeShipping": q["freeShipping"],
            "newsletter": bool(d.get("newsletter")), "site": str(d.get("site") or "cc"),
            "status": str(d.get("status") or "reserved"), "at": at,
            "atIso": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(at)) if at else "",
        })
    rows.sort(key=lambda r: r["at"], reverse=True)
    return rows[:max(1, int(limit))]


def admin_view(limit: int = 5000) -> Dict[str, Any]:
    rows = reservation_rows(limit)
    per = {pid: sum(r["items"].get(pid, 0) for r in rows) for pid in PRODUCT_IDS}
    return {
        "enabled": enabled(),
        "reservations": len(rows),
        "plushies": sum(r["count"] for r in rows),
        "perProduct": [{"id": pid, "name": NAMES[pid], "qty": per[pid]} for pid in PRODUCT_IDS],
        "freeShippingOrders": sum(1 for r in rows if r["freeShipping"]),
        "subtotalCents": sum(r["subtotalCents"] for r in rows),
        "rows": rows,
    }


def reservations_csv(rows: List[Dict[str, Any]]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, quoting=csv.QUOTE_ALL, lineterminator="\n")
    w.writerow(["Reference", "First name", "Last name", "Email", "Country", "ZIP / postal code"]
               + [NAMES[pid] for pid in PRODUCT_IDS]
               + ["Total plushies", "Merchandise subtotal", "Free shipping", "Newsletter",
                  "Site", "Status", "Updated (UTC)"])
    for r in rows:
        w.writerow([_csv_safe(r["reference"]), _csv_safe(r["first"]), _csv_safe(r["last"]),
                    _csv_safe(r["email"]), _csv_safe(r["country"]), _csv_safe(r["zip"])]
                   + [str(r["items"].get(pid, 0)) for pid in PRODUCT_IDS]
                   + [str(r["count"]), money(r["subtotalCents"]),
                      "Yes" if r["freeShipping"] else "No", "Yes" if r["newsletter"] else "No",
                      _csv_safe(r["site"]), _csv_safe(r["status"]), r["atIso"]])
    return buf.getvalue()


# ═══════════════════════════════════════════════════════════════════════════
#  HTTP
# ═══════════════════════════════════════════════════════════════════════════
def status() -> Dict[str, Any]:
    """Public: whether reservations can be stored, and the rules the page
    shows. Also what the page calls on load to wake a sleeping server before
    anybody presses the button."""
    return {"ok": True, "enabled": enabled(), "freeShippingMin": FREE_SHIPPING_MIN,
            "bundleDiscountCents": BUNDLE_DISCOUNT_CENTS,
            "maxPerItem": MAX_PER_ITEM, "maxPerOrder": MAX_PER_ORDER,
            "prices": dict(PRICE_CENTS), "catalogVersion": int(CATALOG.get("version", 1))}


def handle_get(handler, parsed) -> bool:
    if parsed.path.rstrip("/") not in ("/api/plush/reserve", "/api/plush/reserve/status"):
        return False
    handler._send_json(status())
    return True


def handle_post(handler, parsed, body: Dict[str, Any]) -> bool:
    if parsed.path.rstrip("/") != "/api/plush/reserve":
        return False
    out = reserve(body or {}, _client_key(handler))
    code = 200
    if not out.get("ok"):
        err = out.get("error")
        code = {"too_many_requests": 429, "unavailable": 503, "full": 503}.get(err, 400)
    handler._send_json(out, status=code)
    return True
