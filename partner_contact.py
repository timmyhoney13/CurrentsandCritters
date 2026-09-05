"""Currents and Critters: the "Partner With Us" enquiry form on the website.

WHAT THIS FIXES
The Partner With Us button was a `mailto:` link. A mailto: is a dead end for
most of the people it is aimed at:

  * on a phone, and in any browser where the reader's mail lives on a webmail
    tab rather than in a mail app, the link opens nothing at all (or worse, an
    empty Mail.app the reader has never signed into), so the click looks
    broken and the enquiry is never written;
  * it hands somebody a blank message with no idea what to say, so the ones
    that do arrive are often "hi, interested in partnering", which costs a
    whole round trip before there is anything to answer;
  * nothing on the page says what the address even is, so a reader who wants
    to write from their own work address has nothing to copy.

So the page now shows the address AND carries a real form with a filled-in
template, and this module is what turns a submitted form into an email that
lands in the inbox with the sender on Reply-To.

WHAT IS AND IS NOT STORED
Nothing is written to Firestore. An enquiry is a message to a person, and the
person is the store: it goes to the inbox, the reply goes back from the inbox,
and there is no second copy of somebody's business proposal sitting in a
database nobody prunes. The consequence is that a send failure must be told to
the sender rather than swallowed, which is why submit() reports it and the page
falls back to a prefilled mailto: with everything they typed still in it.

THE ABUSE SURFACE, AND WHAT IS DONE ABOUT IT
A public form that sends email is a spam relay if it is careless. Four rules:

  1. THE RECIPIENT IS NEVER IN THE REQUEST. Enquiries go to ADMIN_EMAIL and
     nowhere else. Nothing a caller sends can redirect a message.
  2. THE ACKNOWLEDGEMENT CARRIES NO CALLER TEXT. The sender does get a short
     "we got it" at the address they typed, because otherwise a form that
     answers "sent!" is indistinguishable from one that silently drops
     everything. Its body is a CONSTANT. If it quoted their name or message,
     this form would be a way to mail arbitrary text to an arbitrary stranger,
     which is precisely the thing worth not building.
  3. RATE LIMITED PER IP AND GLOBALLY. The per-IP window stops one person
     hammering it; the global hourly ceiling stops a spread-out flood burning
     the day's sending quota, which is shared with the newsletter and with
     every account password reset.
  4. EVERY FIELD IS LENGTH-CAPPED AND HEADER-SAFE. Newlines are stripped from
     everything that reaches a header, and every value is HTML-escaped into the
     message body.

    partner_contact.submit(body, client)   validate + send one enquiry
    partner_contact.handle_post(...)       POST /api/partner/contact
    partner_contact.handle_get(...)        GET  /api/partner/state
"""

from __future__ import annotations

import html as _html
import os
import re
import threading
import time
from typing import Any, Dict, List, Tuple

import newsletter_email as nl_email

# ═══════════════════════════════════════════════════════════════════════════
#  THE FORM'S SHAPE
# ═══════════════════════════════════════════════════════════════════════════
# The partnership kinds, as (value, label). This list is the SAME list the
# <select> on the website renders, and test_partner_contact.py compares the two
# character for character. A value the server does not know would otherwise be
# quietly rewritten to "Other" and the enquiry would arrive mislabelled, which
# is the sort of drift nobody notices because the mail still works.
KINDS: List[Tuple[str, str]] = [
    ("brand",        "Brand or product collaboration"),
    ("retail",       "Retail, distribution or wholesale"),
    ("conservation", "Ocean conservation partnership"),
    ("creator",      "Creator, press or content"),
    ("sponsor",      "Sponsorship or donation"),
    ("major",        "Supporter contribution over $100"),
    ("event",        "Event, convention or school"),
    ("other",        "Something else"),
]
KIND_VALUES = [v for v, _ in KINDS]
KIND_LABELS = dict(KINDS)

# THE ADDRESS SHOWN TO THE PUBLIC, which is not the same thing as the address
# an enquiry is DELIVERED to. Delivery goes to every ADMIN_EMAIL, and the first
# of those is a personal work address that the website does not advertise. When
# this form cannot send, it has to tell a stranger where to write instead, and
# that has to be the address printed on the page, or the site advertises one
# inbox and the error message quietly hands out another.
PUBLIC_INBOX_DEFAULT = "currentsandcritters@gmail.com"


def public_inbox() -> str:
    return (os.environ.get("PARTNER_PUBLIC_EMAIL") or "").strip() or PUBLIC_INBOX_DEFAULT


MAX_NAME    = 80
MAX_ORG     = 120
MAX_LINK    = 200
MAX_MESSAGE = 4000
MIN_MESSAGE = 25

# The template on the page writes its blanks as runs of underscores. Sending
# one back unfilled is not a partnership enquiry, it is the template, so it is
# refused with an error that says which blank to fill rather than mailing it.
BLANK_RE = re.compile(r"_{3,}")


# ═══════════════════════════════════════════════════════════════════════════
#  RATE LIMITING
# ═══════════════════════════════════════════════════════════════════════════
# Fixed-window counters, same shape as newsletter_server's. One process, one
# instance, no shared store: enough to stop a form being leaned on, and it does
# not pretend to be a distributed limiter.
PER_IP_LIMIT,  PER_IP_WINDOW  = 3, 900     # 3 enquiries / 15 min / IP
GLOBAL_LIMIT,  GLOBAL_WINDOW  = 30, 3600   # 30 enquiries / hour, everyone

_RL_LOCK = threading.Lock()
_RL: Dict[str, Tuple[int, float]] = {}


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
    """A single line, safe to drop into a header: no CR, no LF, no control
    characters, collapsed whitespace, capped."""
    text = str(value if value is not None else "")
    text = re.sub(r"[\x00-\x1f\x7f]", " ", text)
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _many_lines(value: Any, limit: int) -> str:
    """The message body: newlines survive (it is prose), everything else that
    could confuse a mail client does not. Runs of blank lines are collapsed so
    a pasted template does not arrive three screens tall."""
    text = str(value if value is not None else "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[:limit]


def validate(body: Dict[str, Any]) -> Tuple[Dict[str, str], str]:
    """(clean fields, error message). An empty error means it is sendable."""
    data = {
        "name":    _one_line(body.get("name"), MAX_NAME),
        "org":     _one_line(body.get("org"), MAX_ORG),
        "link":    _one_line(body.get("link"), MAX_LINK),
        "kind":    _one_line(body.get("kind"), 40).lower(),
        "email":   nl_email.normalize_email(body.get("email")),
        "message": _many_lines(body.get("message"), MAX_MESSAGE),
    }

    # The honeypot. A real person never sees this field (it is off-screen and
    # aria-hidden), so anything in it is a bot filling every input on the page.
    # It is answered with a normal-looking success by the caller, never an
    # error: telling a bot which check it failed is telling it how to pass.
    if _one_line(body.get("company_site"), 200):
        return data, "honeypot"

    if len(data["name"]) < 2:
        return data, "Please tell us your name."
    if not data["email"]:
        return data, "That email address doesn't look right."
    if data["kind"] not in KIND_VALUES:
        return data, "Pick what kind of partnership this is."
    if not data["message"]:
        return data, "Please write a message."
    if BLANK_RE.search(data["message"]):
        return data, ("The template still has blanks in it. Fill in the ____ parts "
                      "(or delete them) and send again.")
    if len(data["message"]) < MIN_MESSAGE:
        return data, "Tell us a little more, so there is something to reply to."
    return data, ""


# ═══════════════════════════════════════════════════════════════════════════
#  THE MESSAGES
# ═══════════════════════════════════════════════════════════════════════════
def _esc(value: Any) -> str:
    return _html.escape(str(value if value is not None else ""), quote=True)


def build_enquiry(data: Dict[str, str]) -> Tuple[str, str, str]:
    """(subject, html, text) for the email that reaches the inbox.

    The subject names the kind and who sent it, so a full inbox can be sorted
    without opening anything. Every value is escaped: this message is assembled
    from text a stranger typed.
    """
    kind = KIND_LABELS.get(data["kind"], "Partnership")
    who = data["org"] or data["name"]
    subject = _one_line("Partner enquiry (%s): %s" % (kind, who), 160)

    rows = [
        ("Name", data["name"]),
        ("Organisation", data["org"]),
        ("Email", data["email"]),
        ("Website or link", data["link"]),
        ("Partnership type", kind),
    ]
    text = "\n".join("%-16s %s" % (label + ":", value)
                     for label, value in rows if value)
    text += ("\n\n" + "-" * 52 + "\n\n" + data["message"]
             + "\n\n" + "-" * 52 + "\n"
             + "Sent from the Partner With Us form on currentsandcritters.com.\n"
             + "Reply to this email and it goes straight to %s.\n" % data["email"])

    row_html = "".join(
        '<tr><td style="padding:4px 14px 4px 0;color:#5a7b88;white-space:nowrap;'
        'vertical-align:top">%s</td><td style="padding:4px 0;color:#12303d">%s</td></tr>'
        % (_esc(label), _esc(value))
        for label, value in rows if value)
    html = (
        '<div style="font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;'
        'font-size:15px;line-height:1.55;color:#12303d;max-width:560px">'
        '<p style="margin:0 0 14px;font-size:13px;color:#5a7b88">'
        'New enquiry from the Partner With Us form.</p>'
        '<table style="border-collapse:collapse;font-size:14px;margin-bottom:16px">'
        + row_html +
        '</table>'
        '<div style="white-space:pre-wrap;background:#eef7fb;border:1px solid #cfe6f0;'
        'border-radius:10px;padding:14px 16px">' + _esc(data["message"]) + '</div>'
        '<p style="color:#5a7b88;font-size:13px;margin-top:16px">'
        'Reply to this email and it goes straight to '
        '<a href="mailto:%s">%s</a>.</p></div>' % (_esc(data["email"]), _esc(data["email"]))
    )
    return subject, html, text


# The acknowledgement is a CONSTANT. See rule 2 at the top of the file: the
# moment it quotes anything the sender typed, this form becomes a way to mail
# a stranger arbitrary text.
ACK_SUBJECT = "Thanks for getting in touch with Currents and Critters"
ACK_TEXT = (
    "Thanks for reaching out about partnering with Currents and Critters.\n\n"
    "Your message is in, and a real person (Tim) reads every one of these. "
    "You will get a reply at this address, usually within a few days.\n\n"
    "In the meantime the game is free to play at "
    "https://play.currentsandcritters.com.\n\n"
    "- Currents and Critters\n"
)
ACK_HTML = (
    '<div style="font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;'
    'font-size:15px;line-height:1.55;color:#12303d;max-width:520px">'
    '<p>Thanks for reaching out about partnering with Currents and Critters.</p>'
    '<p>Your message is in, and a real person (Tim) reads every one of these. '
    'You will get a reply at this address, usually within a few days.</p>'
    '<p>In the meantime the game is free to play at '
    '<a href="https://play.currentsandcritters.com">play.currentsandcritters.com</a>.</p>'
    '<p style="color:#5a7b88;font-size:13px">- Currents and Critters</p></div>'
)


# ═══════════════════════════════════════════════════════════════════════════
#  SENDING
# ═══════════════════════════════════════════════════════════════════════════
_SENT_MESSAGE = ("Thanks! Your message is on its way to Timothy, and a copy of the "
                 "confirmation is in your inbox. You'll get a reply at the "
                 "address you gave.")


def enabled() -> bool:
    """Can this server send at all? False means the form must not promise
    anything: the page falls back to the mailto: with the text still in it."""
    return bool(nl_email.transport()) and bool(nl_email.admin_emails())


def recipients() -> List[str]:
    return nl_email.admin_emails()


def submit(body: Dict[str, Any], client: str = "") -> Dict[str, Any]:
    """Validate one submission and mail it. Never raises."""
    data, error = validate(body)

    # A bot that filled the honeypot is told the same thing a person is told,
    # and nothing is sent. It must not be able to tell the difference.
    if error == "honeypot":
        return {"ok": True, "message": _SENT_MESSAGE}
    if error:
        return {"ok": False, "error": error}

    if not enabled():
        return {"ok": False, "error": "no_transport",
                "message": ("This form can't send mail right now. Email "
                            "%s directly and it will reach the same inbox."
                            % public_inbox())}

    if client and not _rate_ok("ip", client, PER_IP_LIMIT, PER_IP_WINDOW):
        return {"ok": False, "error": "too_many_requests",
                "message": ("That is a few messages in a row from here. Give it "
                            "a few minutes, or email %s directly."
                            % public_inbox())}
    if not _rate_ok("global", "all", GLOBAL_LIMIT, GLOBAL_WINDOW):
        return {"ok": False, "error": "too_many_requests",
                "message": ("The form is busy right now. Email %s directly and it "
                            "will reach the same inbox." % public_inbox())}

    subject, html_body, text_body = build_enquiry(data)

    # Every admin address gets its own message (send_email takes exactly one
    # recipient, deliberately). The enquiry counts as delivered if ANY of them
    # went: one bouncing address must not lose the message from the other.
    sent, last_error = 0, ""
    for address in recipients():
        try:
            nl_email.send_email(
                to_email=address, subject=subject, html_body=html_body,
                text_body=text_body, is_bulk=False, stream="partner",
                # Reply goes to the person who wrote it, not to the site's own
                # From address. Without this, answering an enquiry means
                # copying the address out of the body by hand every time.
                reply_to_override=data["email"],
            )
            sent += 1
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            print("[partner] enquiry to %s failed: %s" % (address, exc))

    if not sent:
        return {"ok": False, "error": "send_failed",
                "message": ("Something went wrong sending that. Email %s directly "
                            "and it will reach the same inbox."
                            % public_inbox()),
                "detail": last_error[:200]}

    # Best effort, and deliberately after the real one: the enquiry has already
    # arrived, so a failure here is worth a log line and nothing else.
    try:
        nl_email.send_email(to_email=data["email"], subject=ACK_SUBJECT,
                            html_body=ACK_HTML, text_body=ACK_TEXT,
                            is_bulk=False, stream="partner-ack")
    except Exception as exc:  # noqa: BLE001
        print("[partner] acknowledgement to sender failed: %s" % exc)

    print("[partner] enquiry from %s (%s) delivered to %d inbox(es)"
          % (data["email"], data["kind"], sent))
    return {"ok": True, "message": _SENT_MESSAGE}



# ═══════════════════════════════════════════════════════════════════════════
#  HTTP
# ═══════════════════════════════════════════════════════════════════════════
def state() -> Dict[str, Any]:
    """Answerable from outside without sending anything: is the form actually
    wired up on this deploy? A form that looks fine and mails nowhere is
    invisible from the website, and this is how that is checked."""
    return {"ok": True, "enabled": enabled(), "inboxes": len(recipients()),
            "transport": nl_email.transport_label() if enabled() else ""}


def handle_get(handler, parsed) -> bool:
    """GET /api/partner/state  ->  {ok, enabled, inboxes, transport}"""
    if parsed.path != "/api/partner/state":
        return False
    handler._send_json(state())
    return True


def handle_post(handler, parsed, body: Dict[str, Any]) -> bool:
    """POST /api/partner/contact  { name, org, email, link, kind, message }"""
    path = parsed.path
    if not path.startswith("/api/partner/"):
        return False
    action = path[len("/api/partner/"):].strip("/")

    if action == "state":
        handler._send_json(state())
        return True

    if action == "contact":
        out = submit(body or {}, _client_key(handler))
        status = 200
        if not out.get("ok"):
            status = 429 if out.get("error") == "too_many_requests" else 400
            if out.get("error") in ("no_transport", "send_failed"):
                status = 503
        handler._send_json(out, status=status)
        return True

    return False
