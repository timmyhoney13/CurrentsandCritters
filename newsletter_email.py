"""Currents and Critters — newsletter email: sanitising, rendering, sending.

This module is the ONLY thing in the project that talks to Gmail, and the only
thing that turns admin-authored HTML into an email body. newsletter_server.py
decides WHO gets mail and WHEN; this file decides WHAT the message looks like
and puts it on the wire.

────────────────────────────────────────────────────────────────────────────
WHY THERE IS NO GOOGLE SDK HERE
The house style is stdlib-only transport (multiplayer_server verifies Stripe
webhook signatures by hand rather than pulling in the stripe SDK), and the
Docker image installs exactly one dependency. Gmail's send API is a single
POST of a base64url MIME blob, and the OAuth refresh is a single form POST, so
an SDK would add ~30MB of transitive dependencies to save ~40 lines. Everything
below is urllib + email.mime.

THE ONE THING THAT IS *NOT* HAND-ROLLED
HTML sanitising. `nh3` (Rust ammonia bindings — the maintained successor to
bleach) is installed in the image and is the real production path. The
hand-written allowlist parser below is a DEFENCE-IN-DEPTH FALLBACK for
environments where the wheel is missing (local test runs), not a preference:
it is deny-by-default and re-serialises from parsed tokens rather than
regex-stripping, but nh3 is the one that should actually run. `sanitizer_name()`
reports which is live, and the admin Settings tab prints it, so "which
sanitiser am I on" is never a guess.

────────────────────────────────────────────────────────────────────────────
GMAIL SENDING LIMITS (real numbers, not aspirations)
A Google Workspace account may send to at most 2,000 recipients per rolling
24h via the API (1,500 of which may be external; a plain @gmail.com consumer
account is 500). One message to one subscriber = one recipient, which is why
this system never puts more than one address on a message. DAILY_SEND_CAP
below defaults to 1,200/day to leave headroom for the welcome mails, test
sends and Tim's ordinary human email on the same account. Going over does not
bounce — Google returns 429/"User-rate limit exceeded" and stops the account
sending for up to 24h, so the cap is enforced HERE, before the wire.
"""
from __future__ import annotations

import base64
import html as _html
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Tuple

# ═══════════════════════════════════════════════════════════════════════════
#  CONFIG (every secret comes from a Render env var — nothing is hard-coded)
# ═══════════════════════════════════════════════════════════════════════════
#
#  THREE WAYS TO SEND. Pick ONE; the rest can stay unset.
#
#  There is no way to send mail to real inboxes without an authenticated
#  sender — that is how SMTP and the anti-spam world work, not a limitation
#  here. What IS negotiable is how much account setup that costs you, so this
#  module supports the cheap options and treats the expensive one as optional.
#
#    "smtp"    — DEFAULT AND EASIEST. Host, port, username, password. Every
#                mail provider on earth gives you these, including the one
#                already hosting timothy.honey@beardedsealstudios.com. If that
#                is Google Workspace, an App Password works and needs NO Google
#                Cloud project, NO OAuth consent screen, NO scopes and NO
#                refresh token — it is four values from a settings page.
#    "http"    — An HTTPS email API (Resend / Postmark / Mailgun / SendGrid /
#                Brevo). One API key. Use this when the host blocks outbound
#                SMTP ports, which some do.
#    "gmail_api" — The OAuth route. Still supported, no longer required, and
#                deliberately last: it is the only one that costs a Google
#                Cloud project.
#
#  NEWSLETTER_TRANSPORT forces a choice. Left unset (or "auto") the first
#  fully-configured transport in the order above wins, so setting SMTP_* is
#  all it takes to switch.
GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"
GOOGLE_TOKENINFO_URI = "https://oauth2.googleapis.com/tokeninfo"
GMAIL_SEND_URI = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"

# The minimum scopes that do the job (only relevant to the gmail_api transport):
#   gmail.send  — send only. It cannot read a single message in the mailbox,
#                 which is the whole point of not asking for gmail.modify.
#   openid,email — lets /tokeninfo tell us WHICH account the refresh token
#                 belongs to, so we can verify it is allowed to send as
#                 GMAIL_SENDER_EMAIL without requesting gmail.readonly or
#                 gmail.settings.basic (both far broader).
GMAIL_SCOPES = "https://www.googleapis.com/auth/gmail.send openid email"

# ── Known HTTPS email APIs ─────────────────────────────────────────────────
# Each entry says how to talk to that provider: where to POST, how to present
# the key, how to shape the body, and where the message id comes back. Adding
# another provider is a dict entry, not new code.
HTTP_PROVIDERS: Dict[str, Dict[str, Any]] = {
    "resend": {
        "label": "Resend",
        "url": "https://api.resend.com/emails",
        "auth": "bearer",
        "style": "resend",
        "id_path": ("id",),
        "signup": "https://resend.com",
    },
    "postmark": {
        "label": "Postmark",
        "url": "https://api.postmarkapp.com/email",
        "auth": "postmark",
        "style": "postmark",
        "id_path": ("MessageID",),
        "signup": "https://postmarkapp.com",
    },
    "brevo": {
        "label": "Brevo",
        "url": "https://api.brevo.com/v3/smtp/email",
        "auth": "brevo",
        "style": "brevo",
        "id_path": ("messageId",),
        "signup": "https://www.brevo.com",
    },
    "sendgrid": {
        "label": "SendGrid",
        "url": "https://api.sendgrid.com/v3/mail/send",
        "auth": "bearer",
        "style": "sendgrid",
        "id_path": (),           # SendGrid returns 202 with an empty body
        "signup": "https://sendgrid.com",
    },
}

BRAND_NAME = "Currents & Critters"
BUSINESS_NAME = "Bearded Seal Studios LLC"
BUSINESS_ADDRESS_LINES = (
    "916A South Douglas Avenue",
    "Nashville, TN 37204-2021",
)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def sender_email() -> str:
    # NEWSLETTER_FROM_EMAIL is the transport-neutral name; GMAIL_SENDER_EMAIL is
    # honoured too so an existing deployment keeps working after this change.
    return _env("NEWSLETTER_FROM_EMAIL") or _env(
        "GMAIL_SENDER_EMAIL", "timothy.honey@beardedsealstudios.com")


def sender_name() -> str:
    return _env("NEWSLETTER_FROM_NAME") or _env("GMAIL_SENDER_NAME", BRAND_NAME)


def reply_to() -> str:
    return _env("NEWSLETTER_REPLY_TO", sender_email())


# ADMIN_EMAIL may list SEVERAL accounts, separated by comma / pipe / space.
# Every one of them can open /admin/newsletter; the FIRST is the primary, and
# is where "new subscriber" notifications and test emails are delivered.
# Listing more than one is a deliberate widening of access — it is still an
# exact-match allowlist, never a domain or a pattern, so there is no wildcard
# to get wrong.
DEFAULT_ADMIN_EMAIL = "timothy.honey@beardedsealstudios.com"


def admin_emails() -> List[str]:
    """Every account allowed into the newsletter admin, lowercased, in order."""
    raw = _env("ADMIN_EMAIL", DEFAULT_ADMIN_EMAIL)
    parts = [p.strip().lower() for p in re.split(r"[,|;\s]+", raw) if p.strip()]
    out: List[str] = []
    for p in parts:
        if normalize_email(p) and p not in out:
            out.append(p)
    return out or [DEFAULT_ADMIN_EMAIL]


def admin_email() -> str:
    """The PRIMARY admin — where notifications and test emails are sent."""
    return admin_emails()[0]


def is_admin_email(candidate: Any) -> bool:
    """Exact-match membership test for the admin allowlist."""
    c = str(candidate or "").strip().lower()
    return bool(c) and c in admin_emails()


def site_url() -> str:
    return _env("CURRENTS_AND_CRITTERS_URL", "https://currentsandcritters.com").rstrip("/")


def app_base_url() -> str:
    """Where the unsubscribe links point — the Render host that runs this code."""
    return _env("APP_BASE_URL", "https://play.currentsandcritters.com").rstrip("/")


def privacy_url() -> str:
    return _env("PRIVACY_POLICY_URL", site_url() + "/privacy")


# A consumer @gmail.com account may send to ~500 recipients per rolling 24h.
# A Google Workspace account on your own domain gets ~2,000. Those are wildly
# different budgets, and exceeding either does not bounce — Google throttles or
# SUSPENDS the account, sometimes for a full day.
CONSUMER_GMAIL_CAP = 400          # 500 real limit, minus headroom for your own mail
WORKSPACE_CAP = 1200              # 2,000 real limit, minus the same headroom


def sender_is_consumer_gmail() -> bool:
    """True when mail goes out from a free @gmail.com / @googlemail.com account
    rather than a Workspace domain — which is a 4x smaller daily budget."""
    dom = sender_email().rsplit("@", 1)[-1].strip().lower()
    return dom in ("gmail.com", "googlemail.com")


def daily_send_cap() -> int:
    """How many messages this process will send per UTC day.

    The DEFAULT follows the sending account: a free Gmail address gets the
    small budget automatically, so switching the From address to @gmail.com
    cannot silently leave a 1,200/day cap pointed at a 500/day mailbox. An
    explicit NEWSLETTER_DAILY_SEND_CAP is always honoured — it is your account
    and you may know something we don't — but connection_status() flags it when
    it exceeds what the account can actually take.
    """
    default = CONSUMER_GMAIL_CAP if sender_is_consumer_gmail() else WORKSPACE_CAP
    raw = _env("NEWSLETTER_DAILY_SEND_CAP")
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


# ── Transport selection ────────────────────────────────────────────────────
def smtp_configured() -> bool:
    return bool(_env("SMTP_HOST") and _env("SMTP_USERNAME") and _env("SMTP_PASSWORD"))


def http_provider() -> str:
    """Which HTTPS email API is configured, or ""."""
    named = _env("NEWSLETTER_HTTP_PROVIDER").lower()
    if named in HTTP_PROVIDERS and _env("NEWSLETTER_API_KEY"):
        return named
    if _env("NEWSLETTER_API_KEY"):
        return "resend"                       # the default if only a key is set
    # Convenience: a provider-named key alone is enough to pick that provider.
    for name in HTTP_PROVIDERS:
        if _env(name.upper() + "_API_KEY"):
            return name
    return ""


def http_configured() -> bool:
    return bool(http_provider())


def gmail_configured() -> bool:
    return bool(_env("GOOGLE_CLIENT_ID") and _env("GOOGLE_CLIENT_SECRET")
                and _env("GOOGLE_REFRESH_TOKEN"))


def transport() -> str:
    """The active transport: "smtp" | "http" | "gmail_api" | "" (none).

    Order is cheapest-setup-first, so the moment SMTP_* is filled in it takes
    over and the Google variables become dead weight rather than a dependency.
    """
    forced = _env("NEWSLETTER_TRANSPORT").lower().replace("-", "_")
    if forced in ("smtp", "http", "gmail_api"):
        return forced
    if forced in ("resend", "postmark", "brevo", "sendgrid"):
        return "http"
    if smtp_configured():
        return "smtp"
    if http_configured():
        return "http"
    if gmail_configured():
        return "gmail_api"
    return ""


def transport_label() -> str:
    t = transport()
    if t == "smtp":
        return "SMTP (%s)" % (_env("SMTP_HOST") or "not set")
    if t == "http":
        p = http_provider()
        return (HTTP_PROVIDERS.get(p) or {}).get("label", p) or "HTTP API"
    if t == "gmail_api":
        return "Gmail API (OAuth)"
    return "not configured"


def _api_key_for(provider: str) -> str:
    return _env("NEWSLETTER_API_KEY") or _env(provider.upper() + "_API_KEY")


def configured() -> bool:
    return bool(transport())


# ═══════════════════════════════════════════════════════════════════════════
#  EMAIL ADDRESS VALIDATION / NORMALISATION
# ═══════════════════════════════════════════════════════════════════════════
# Deliberately stricter than the RFC: no quoted local parts, no IP-literal
# domains, no unicode. Anything this rejects is something Gmail would bounce or
# an attacker is probing with. The TLD must be >=2 alphabetic characters.
_EMAIL_RE = re.compile(
    r"^[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+"
    r"(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*"
    r"@"
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z]{2,63}$"
)
MAX_EMAIL_LEN = 254


def normalize_email(raw: Any) -> str:
    """Lowercased, trimmed address if it is valid, else "".

    Lowercasing is what makes duplicate detection work: Tim@x.com and
    tim@x.com are ONE subscriber. The local part is technically
    case-sensitive per RFC 5321, but no mail provider in real use treats it
    that way, and treating them as two people means sending the same human two
    copies of every newsletter.
    """
    s = str(raw or "").strip()
    # Strip a "Name <addr>" wrapper if somebody pastes one in.
    m = re.match(r"^.*<([^<>]+)>$", s)
    if m:
        s = m.group(1).strip()
    if not s or len(s) > MAX_EMAIL_LEN:
        return ""
    # A newline in an address is a header-injection attempt, full stop.
    if any(c in s for c in "\r\n\t,;"):
        return ""
    if not _EMAIL_RE.match(s):
        return ""
    return s.lower()


# ═══════════════════════════════════════════════════════════════════════════
#  HTML SANITISING — strict allowlist, deny by default
# ═══════════════════════════════════════════════════════════════════════════
# Everything an email body may legally contain. Note what is NOT here: script,
# style, iframe, object, embed, form, input, button, link, meta, svg, video,
# audio, and every event-handler attribute. A tag not on this list has its
# markup dropped; its TEXT is kept (so pasting <div>hello</div> shows "hello"
# rather than silently losing the sentence).
ALLOWED_TAGS = {
    "p", "br", "hr",
    "h1", "h2", "h3", "h4",
    "strong", "b", "em", "i", "u", "s", "sub", "sup", "small",
    "ul", "ol", "li",
    "blockquote", "pre", "code",
    "a", "img",
    "table", "thead", "tbody", "tr", "th", "td",
    "span", "div",
}
# Void elements: never get a closing tag.
VOID_TAGS = {"br", "hr", "img"}

ALLOWED_ATTRS: Dict[str, set] = {
    "a": {"href", "title", "target", "rel"},
    "img": {"src", "alt", "title", "width", "height"},
    "td": {"colspan", "rowspan", "align"},
    "th": {"colspan", "rowspan", "align"},
    "table": {"width", "align"},
    # `class` is allowed on these three so the composer's own button/lead
    # helpers survive; the classes are namespaced and styled by the shell.
    "span": {"class"},
    "div": {"class"},
    "p": {"class"},
}
# Only these class names survive on span/div/p. An arbitrary class attribute is
# an injection surface into whatever CSS the shell ships.
ALLOWED_CLASSES = {"cc-btn", "cc-lead", "cc-note", "cc-center"}

ALLOWED_URL_SCHEMES = {"http", "https", "mailto"}

# Tags whose CONTENT is dropped along with the tag. For every other
# disallowed tag we keep the inner text (pasting <div>hello</div> should not
# lose the sentence), but the text inside these is code, not prose: without
# this, "<script>alert(1)</script>" renders the words "alert(1)" in the
# newsletter. Harmless — it is escaped — but it is not what anyone meant.
DROP_CONTENT_TAGS = {"script", "style", "title", "head", "noscript",
                     "template", "textarea", "xml", "svg", "math"}


def _safe_url(value: str, *, image: bool = False) -> Optional[str]:
    """A URL that is safe to put in href/src, or None.

    Rejects javascript:, data:, vbscript:, file: and every other scheme, plus
    anything with control characters used to smuggle a scheme past a naive
    check (`java\\tscript:`). Protocol-relative //host is rejected too — an
    email has no "current protocol" to be relative to. Images additionally
    must be https, because an http image in an email is a downgrade every
    client will either block or flag.
    """
    s = str(value or "").strip()
    if not s:
        return None
    # Control characters / whitespace inside the scheme are always evasion.
    s = "".join(ch for ch in s if ord(ch) >= 0x20 and ch not in "\x7f")
    if not s or len(s) > 2000:
        return None
    if s.startswith("//"):
        return None
    low = s.lower()
    if low.startswith("#"):
        return None  # in-page anchors are meaningless in email
    scheme = low.split(":", 1)[0] if ":" in low else ""
    # A relative URL (no scheme) can't be resolved by a mail client.
    if not scheme or scheme not in ALLOWED_URL_SCHEMES:
        return None
    if image and scheme != "https":
        return None
    return s


class _AllowlistSanitizer(HTMLParser):
    """Deny-by-default HTML rebuilder (fallback when nh3 is unavailable).

    It does not "strip bad things" — it PARSES the input and re-emits only the
    tags and attributes on the allowlist, escaping every piece of text on the
    way out. Anything it does not understand simply never reaches the output,
    which is why an unknown tag or a malformed attribute cannot survive.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: List[str] = []
        self._open: List[str] = []
        self._drop_depth = 0

    # -- tags ------------------------------------------------------------
    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag in DROP_CONTENT_TAGS:
            self._drop_depth += 1
            return
        if self._drop_depth or tag not in ALLOWED_TAGS:
            return
        rendered = self._attrs_for(tag, attrs)
        if tag in VOID_TAGS:
            # img with no usable src is nothing but a broken-image icon.
            if tag == "img" and 'src="' not in rendered:
                return
            self.out.append("<%s%s />" % (tag, rendered))
            return
        # An <a> whose href was rejected is not a link — emit its text only,
        # so a javascript: anchor degrades to plain words rather than a dead
        # underlined stub.
        if tag == "a" and 'href="' not in rendered:
            return
        self.out.append("<%s%s>" % (tag, rendered))
        self._open.append(tag)

    def handle_startendtag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag in DROP_CONTENT_TAGS or self._drop_depth or tag not in ALLOWED_TAGS:
            return
        rendered = self._attrs_for(tag, attrs)
        if tag == "img" and 'src="' not in rendered:
            return
        self.out.append("<%s%s />" % (tag, rendered))

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in DROP_CONTENT_TAGS:
            self._drop_depth = max(0, self._drop_depth - 1)
            return
        if self._drop_depth or tag not in ALLOWED_TAGS or tag in VOID_TAGS:
            return
        # Close only tags we actually opened, innermost first, so a stray
        # </p> cannot unbalance the document.
        if tag not in self._open:
            return
        while self._open:
            top = self._open.pop()
            self.out.append("</%s>" % top)
            if top == tag:
                break

    def _attrs_for(self, tag: str, attrs) -> str:
        allowed = ALLOWED_ATTRS.get(tag, set())
        parts: List[str] = []
        seen: set = set()
        for name, value in attrs:
            name = (name or "").lower()
            # Belt and braces: on* handlers can never be allowlisted, but an
            # explicit reject here means a future edit to ALLOWED_ATTRS can't
            # accidentally let one through.
            if name.startswith("on") or name in ("style", "srcset", "formaction"):
                continue
            if name not in allowed or name in seen:
                continue
            value = "" if value is None else str(value)
            if name in ("href", "src"):
                safe = _safe_url(value, image=(name == "src"))
                if safe is None:
                    continue
                value = safe
            elif name == "class":
                keep = [c for c in value.split() if c in ALLOWED_CLASSES]
                if not keep:
                    continue
                value = " ".join(keep)
            elif name in ("width", "height", "colspan", "rowspan"):
                if not re.match(r"^\d{1,4}$", value.strip()):
                    continue
                value = value.strip()
            elif name == "align":
                if value.strip().lower() not in ("left", "right", "center"):
                    continue
                value = value.strip().lower()
            elif name == "target":
                value = "_blank"
            elif name == "rel":
                value = "noopener noreferrer"
            seen.add(name)
            parts.append(' %s="%s"' % (name, _html.escape(value, quote=True)))
        # Every outbound link opens safely, whether or not the author said so.
        if tag == "a" and any(p.startswith(' href=') for p in parts):
            if "target" not in seen:
                parts.append(' target="_blank"')
            if "rel" not in seen:
                parts.append(' rel="noopener noreferrer"')
        return "".join(parts)

    # -- text ------------------------------------------------------------
    def handle_data(self, data: str) -> None:
        if data and not self._drop_depth:
            self.out.append(_html.escape(data, quote=False))

    # Comments, doctypes, processing instructions and CDATA are dropped
    # entirely: `<!--[if IE]><script>` is a real vector.
    def handle_comment(self, data: str) -> None:
        return

    def handle_decl(self, decl: str) -> None:
        return

    def handle_pi(self, data: str) -> None:
        return

    def unknown_decl(self, data: str) -> None:
        return

    def result(self) -> str:
        while self._open:
            self.out.append("</%s>" % self._open.pop())
        return "".join(self.out)


_NH3 = None
_NH3_CHECKED = False


def _nh3():
    global _NH3, _NH3_CHECKED
    if not _NH3_CHECKED:
        _NH3_CHECKED = True
        try:
            import nh3 as _mod  # type: ignore
            _NH3 = _mod
        except Exception:  # noqa: BLE001
            _NH3 = None
    return _NH3


def sanitizer_name() -> str:
    """Which sanitiser is actually live, for the admin Settings panel."""
    return "nh3" if _nh3() is not None else "builtin-allowlist"


def sanitize_html(raw: Any) -> str:
    """Sanitise admin-authored newsletter HTML against the strict allowlist.

    Called on EVERY write of newsletter content (draft save, test send, real
    send) rather than only at render time, so what is stored is already safe
    and no later reader has to remember to clean it.
    """
    s = str(raw or "")
    if not s.strip():
        return ""
    if len(s) > 400_000:
        s = s[:400_000]
    mod = _nh3()
    if mod is not None:
        cleaned = mod.clean(
            s,
            tags=set(ALLOWED_TAGS),
            attributes={k: set(v) for k, v in ALLOWED_ATTRS.items()},
            url_schemes=set(ALLOWED_URL_SCHEMES),
            link_rel="noopener noreferrer",
            strip_comments=True,
        )
        # nh3 honours the attribute allowlist but not our narrower rules for
        # `class` and for https-only images, so the builtin pass runs after it
        # to apply those. Two passes over already-clean HTML is cheap.
        p = _AllowlistSanitizer()
        p.feed(cleaned)
        p.close()
        return p.result()
    p = _AllowlistSanitizer()
    p.feed(s)
    p.close()
    return p.result()


# ═══════════════════════════════════════════════════════════════════════════
#  HTML → PLAIN TEXT
# ═══════════════════════════════════════════════════════════════════════════
class _TextExtractor(HTMLParser):
    """Readable plain-text alternative for the multipart/alternative body.

    Not a "strip the tags" pass: block elements become blank lines, list items
    become "• ", and a link becomes "text <url>" so the plain-text reader can
    still follow it. A text part that is just the HTML with angle brackets
    removed is worse than none — it is the part spam filters read.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []
        self._href: Optional[str] = None
        self._link_text: List[str] = []
        self._list_stack: List[str] = []
        self._ol_index: List[int] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        d = dict((k.lower(), v or "") for k, v in attrs)
        if tag in ("p", "div", "blockquote", "tr", "h1", "h2", "h3", "h4", "pre"):
            self.parts.append("\n\n")
        elif tag == "br":
            self.parts.append("\n")
        elif tag == "hr":
            self.parts.append("\n\n" + ("-" * 40) + "\n\n")
        elif tag in ("ul", "ol"):
            self.parts.append("\n")
            self._list_stack.append(tag)
            if tag == "ol":
                self._ol_index.append(0)
        elif tag == "li":
            if self._list_stack and self._list_stack[-1] == "ol":
                self._ol_index[-1] += 1
                self.parts.append("\n%d. " % self._ol_index[-1])
            else:
                self.parts.append("\n• ")
        elif tag == "a":
            self._href = d.get("href") or ""
            self._link_text = []
        elif tag == "img":
            alt = (d.get("alt") or "").strip()
            if alt:
                self.parts.append("[%s]" % alt)
        elif tag in ("td", "th"):
            if self.parts and not self.parts[-1].endswith(("\n", " ")):
                self.parts.append("  ")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "a" and self._href is not None:
            text = "".join(self._link_text).strip()
            href = self._href.strip()
            if href and text and href != text:
                # mailto: reads better without the scheme repeated.
                shown = href[7:] if href.lower().startswith("mailto:") else href
                self.parts.append("%s <%s>" % (text, shown))
            elif text:
                self.parts.append(text)
            elif href:
                self.parts.append(href)
            self._href = None
            self._link_text = []
        elif tag in ("ul", "ol"):
            if self._list_stack:
                popped = self._list_stack.pop()
                if popped == "ol" and self._ol_index:
                    self._ol_index.pop()
            self.parts.append("\n")
        elif tag in ("p", "div", "blockquote", "h1", "h2", "h3", "h4", "pre", "tr"):
            self.parts.append("\n")

    def handle_data(self, data):
        if self._href is not None:
            self._link_text.append(data)
        else:
            self.parts.append(data)

    def text(self) -> str:
        raw = "".join(self.parts)
        raw = raw.replace("\r\n", "\n").replace("\r", "\n")
        # Collapse runs of spaces but never touch newlines.
        raw = re.sub(r"[ \t ]+", " ", raw)
        raw = re.sub(r" *\n *", "\n", raw)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        return raw.strip()


def html_to_text(raw: Any) -> str:
    p = _TextExtractor()
    p.feed(str(raw or ""))
    p.close()
    return p.text()


# ═══════════════════════════════════════════════════════════════════════════
#  THE BRANDED EMAIL SHELL
# ═══════════════════════════════════════════════════════════════════════════
# Table-based, inline-styled, 600px. Not a stylistic choice: Gmail strips
# <style> blocks in some clients, Outlook's Word renderer ignores most modern
# CSS, and float/flex/grid are unusable. Tables + inline styles are what
# actually render the same in Gmail, Apple Mail, Outlook and every phone.
#
# The palette is the game's own deep-ocean skin, the same values the in-game
# pages use (--deep #04263b, --teal #0fb6c4, --gold #ffd479) — an email that
# arrives looking like the game is the point.
_SHELL_BG = "#eef6fd"
_DEEP = "#04263b"
_DEEP_2 = "#0a4d6b"
_TEAL = "#0fb6c4"
_GOLD = "#ffd479"
_INK = "#12354f"
_INK_2 = "#3c637f"
_MUTED = "#5c7e99"
_LINE = "#dbe8f4"

_FONT = ("-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',"
         "Helvetica,Arial,sans-serif")


def _footer_html(unsubscribe_url: str, *, is_test: bool = False) -> str:
    """The legally-required footer. Automatically appended to every welcome
    email and every marketing newsletter — the admin never types it, so it can
    never be forgotten on a send.

    Three shapes, because a footer that lies is worse than no footer:
      • real marketing mail  → live per-recipient unsubscribe link,
      • a TEST send          → the link is replaced by a note saying so, since
                               a test must never carry a real subscriber token,
      • the owner's own      → no unsubscribe line at all (Tim did not
        notification            "sign up for the email list", and offering to
                                unsubscribe him from his own alerts is noise).
    """
    addr = "<br />".join(_html.escape(line) for line in BUSINESS_ADDRESS_LINES)
    marketing = bool(unsubscribe_url) or is_test
    if is_test:
        unsub_cell = ('<span style="color:%s;">[Unsubscribe link is disabled in test emails]</span>'
                      % _MUTED)
    elif unsubscribe_url:
        unsub_cell = ('<a href="%s" style="color:%s;text-decoration:underline;">'
                      'Unsubscribe from these emails</a>'
                      % (_html.escape(unsubscribe_url, quote=True), _INK_2))
    else:
        unsub_cell = ""

    consent_line = (
        '<div>You received this email because you signed up for the Currents &amp; Critters '
        'email list. You can unsubscribe at any time.</div>'
        '<div style="height:10px;line-height:10px;font-size:0;">&nbsp;</div>'
    ) if marketing else ""

    links = (('%s <span style="color:%s;">|</span> ' % (unsub_cell, _LINE)) if unsub_cell else "") + (
        '<a href="%s" style="color:%s;text-decoration:underline;">Privacy Policy</a>'
        % (_html.escape(privacy_url(), quote=True), _INK_2)
    )

    return (
        '<tr><td style="padding:0 32px;">'
        '<div style="height:1px;background:%(line)s;line-height:1px;font-size:0;">&nbsp;</div>'
        '</td></tr>'
        '<tr><td style="padding:22px 32px 30px;font-family:%(font)s;font-size:12px;'
        'line-height:1.65;color:%(muted)s;">'
        '<div style="font-weight:700;color:%(ink2)s;">%(biz)s</div>'
        '<div>%(addr)s</div>'
        '<div style="height:10px;line-height:10px;font-size:0;">&nbsp;</div>'
        '%(consent)s'
        '<div>%(links)s</div>'
        '</td></tr>'
    ) % {
        "line": _LINE, "font": _FONT, "muted": _MUTED, "ink2": _INK_2,
        "biz": _html.escape(BUSINESS_NAME), "addr": addr,
        "consent": consent_line, "links": links,
    }


def _footer_text(unsubscribe_url: str, *, is_test: bool = False) -> str:
    marketing = bool(unsubscribe_url) or is_test
    if is_test:
        unsub = "[Unsubscribe link is disabled in test emails]\n"
    elif unsubscribe_url:
        unsub = "Unsubscribe from these emails: " + unsubscribe_url + "\n"
    else:
        unsub = ""
    consent = (
        "You received this email because you signed up for the Currents & Critters "
        "email list. You can unsubscribe at any time.\n\n"
    ) if marketing else ""
    return (
        "\n\n" + ("-" * 46) + "\n"
        + BUSINESS_NAME + "\n"
        + "\n".join(BUSINESS_ADDRESS_LINES) + "\n\n"
        + consent + unsub
        + "Privacy Policy: " + privacy_url() + "\n"
    )


def render_email_html(
    *,
    body_html: str,
    unsubscribe_url: str,
    preview_text: str = "",
    is_test: bool = False,
    show_visit_button: bool = True,
) -> str:
    """Wrap sanitised body HTML in the Currents & Critters shell + footer."""
    logo = site_url() + "/email-logo.png"
    site = site_url()

    # The preheader: the grey line a mail client shows next to the subject.
    # Hidden in the body itself, then padded so the client doesn't spill the
    # first sentence of the newsletter into it.
    preheader = ""
    if preview_text:
        preheader = (
            '<div style="display:none;max-height:0;overflow:hidden;opacity:0;'
            'mso-hide:all;font-size:1px;line-height:1px;color:%s;">%s%s</div>'
            % (_SHELL_BG, _html.escape(preview_text), "&#847;&zwnj;&nbsp;" * 60)
        )

    test_banner = ""
    if is_test:
        test_banner = (
            '<tr><td style="padding:14px 32px;background:#5a4a12;font-family:%s;'
            'font-size:13px;font-weight:700;color:%s;letter-spacing:.3px;">'
            '&#9888;&#65039; TEST EMAIL &mdash; this is a preview send. '
            'No subscriber received this copy.</td></tr>' % (_FONT, _GOLD)
        )

    visit = ""
    if show_visit_button:
        visit = (
            '<tr><td align="center" style="padding:6px 32px 34px;">'
            '<a href="%(site)s" style="display:inline-block;background:%(teal)s;'
            'color:#022b33;font-family:%(font)s;font-size:15px;font-weight:800;'
            'text-decoration:none;padding:13px 30px;border-radius:10px;">'
            'Visit Currents &amp; Critters</a></td></tr>'
        ) % {"site": _html.escape(site, quote=True), "teal": _TEAL, "font": _FONT}

    return (
        '<!DOCTYPE html>\n'
        '<html lang="en" xmlns:v="urn:schemas-microsoft-com:vml">\n'
        '<head>\n'
        '<meta charset="utf-8" />\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1" />\n'
        '<meta name="x-apple-disable-message-reformatting" />\n'
        '<meta name="color-scheme" content="light" />\n'
        '<title>%(brand)s</title>\n'
        '<style>\n'
        '  /* Only progressive enhancement lives here. Everything that MUST\n'
        '     render is inline above, because several clients drop this block. */\n'
        '  @media only screen and (max-width:620px){\n'
        '    .cc-shell{width:100%% !important;}\n'
        '    .cc-pad{padding-left:20px !important;padding-right:20px !important;}\n'
        '    .cc-h1{font-size:22px !important;}\n'
        '  }\n'
        '  a.cc-btn{background:%(teal)s !important;color:#022b33 !important;\n'
        '    display:inline-block;padding:12px 26px;border-radius:10px;\n'
        '    font-weight:800;text-decoration:none;}\n'
        '</style>\n'
        '</head>\n'
        '<body style="margin:0;padding:0;background:%(bg)s;">\n'
        '%(preheader)s'
        '<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0"\n'
        '  style="background:%(bg)s;padding:24px 12px;">\n'
        '<tr><td align="center">\n'
        '  <table role="presentation" class="cc-shell" width="600" cellpadding="0" cellspacing="0"\n'
        '    border="0" style="width:600px;max-width:600px;background:#ffffff;border-radius:16px;\n'
        '    overflow:hidden;box-shadow:0 2px 10px rgba(18,53,79,.08);">\n'
        '%(banner)s'
        '    <tr><td align="center" style="background:%(deep)s;\n'
        '      background-image:linear-gradient(180deg,%(deep2)s 0%%,%(deep)s 100%%);padding:28px 32px 24px;">\n'
        '      <img src="%(logo)s" width="72" height="72" alt="%(brand)s"\n'
        '        style="display:block;border:0;width:72px;height:72px;margin:0 auto 12px;" />\n'
        '      <div style="font-family:%(font)s;font-size:20px;font-weight:800;color:#ffffff;\n'
        '        letter-spacing:.4px;">%(brand)s</div>\n'
        '      <div style="font-family:%(font)s;font-size:12px;color:%(teal)s;\n'
        '        letter-spacing:1.6px;text-transform:uppercase;margin-top:5px;">%(biz)s</div>\n'
        '    </td></tr>\n'
        '    <tr><td class="cc-pad" style="padding:30px 32px 8px;font-family:%(font)s;\n'
        '      font-size:16px;line-height:1.65;color:%(ink)s;">\n'
        '%(body)s\n'
        '    </td></tr>\n'
        '%(visit)s'
        '%(footer)s'
        '  </table>\n'
        '</td></tr>\n'
        '</table>\n'
        '</body>\n'
        '</html>\n'
    ) % {
        "brand": _html.escape(BRAND_NAME),
        "biz": _html.escape(BUSINESS_NAME),
        "bg": _SHELL_BG, "deep": _DEEP, "deep2": _DEEP_2, "teal": _TEAL,
        "ink": _INK, "font": _FONT,
        "logo": _html.escape(logo, quote=True),
        "preheader": preheader,
        "banner": test_banner,
        "body": body_html,
        "visit": visit,
        "footer": _footer_html(unsubscribe_url, is_test=is_test),
    }


def render_email_text(
    *,
    body_html: str,
    unsubscribe_url: str,
    is_test: bool = False,
    show_visit_button: bool = True,
) -> str:
    head = "%s\n%s\n%s\n\n" % (BRAND_NAME, BUSINESS_NAME, "=" * 46)
    if is_test:
        head = ("*** TEST EMAIL - this is a preview send. "
                "No subscriber received this copy. ***\n\n") + head
    visit = ("\n\nVisit Currents & Critters: " + site_url()) if show_visit_button else ""
    return head + html_to_text(body_html) + visit + _footer_text(unsubscribe_url, is_test=is_test)


# ── The welcome email ───────────────────────────────────────────────────────
# The copy is Tim's, reproduced exactly. Only the layout is ours.
WELCOME_SUBJECT = "Welcome to the Currents & Critters Community!"

_WELCOME_BULLETS = (
    "New game features and updates",
    "Online game nights and special events",
    "Progress on the physical card game",
    "Rewards and important announcements",
    "Opportunities to playtest and help improve the game",
)


def welcome_body_html() -> str:
    bullets = "".join(
        '<li style="margin:0 0 7px;">%s</li>' % _html.escape(b) for b in _WELCOME_BULLETS
    )
    return (
        '<p style="margin:0 0 16px;font-size:21px;font-weight:800;color:%(deep)s;">Hi!!!</p>'
        '<p style="margin:0 0 16px;">Thank you for joining the Currents &amp; Critters email '
        'list. I&rsquo;m excited to have you as part of the community!</p>'
        '<p style="margin:0 0 10px;">You&rsquo;ll receive occasional emails about:</p>'
        '<ul style="margin:0 0 18px;padding-left:22px;">%(bullets)s</ul>'
        '<p style="margin:0 0 16px;">Your support means a lot as I continue to develop the '
        'game and grow the Currents &amp; Critters community.</p>'
        '<p style="margin:0 0 20px;">Thank you for supporting Currents &amp; Critters and '
        'Bearded Seal Studios!</p>'
        '<p style="margin:0;font-weight:800;color:%(deep)s;">Timothy Honey</p>'
        '<p style="margin:0;font-size:14px;color:%(muted)s;">Creator of Currents &amp; Critters</p>'
        '<p style="margin:0 0 6px;font-size:14px;color:%(muted)s;">Bearded Seal Studios LLC</p>'
    ) % {"deep": _DEEP, "muted": _MUTED, "bullets": bullets}


# ═══════════════════════════════════════════════════════════════════════════
#  MIME
# ═══════════════════════════════════════════════════════════════════════════
def _clean_header(value: str, limit: int = 400) -> str:
    """Header-injection guard. A newline in a subject line lets an attacker add
    their own Bcc:, so CR/LF are removed from every header value we build —
    including ones that only an admin can set, because "only an admin" is not
    a security property worth betting the sending domain on."""
    s = str(value or "")
    s = s.replace("\r", " ").replace("\n", " ").replace("\x00", "")
    return s.strip()[:limit]


def build_message(
    *,
    to_email: str,
    subject: str,
    html_body: str,
    text_body: str,
    unsubscribe_url: str = "",
    one_click_url: str = "",
    is_bulk: bool = True,
) -> Tuple[MIMEMultipart, str]:
    """The MIME message object + its Message-ID.

    Split out from build_mime() because SMTP wants the object (smtplib walks it
    for the envelope) while the Gmail API wants base64url of its bytes. Both
    must be built the SAME way or a message would differ depending on which
    transport happened to be configured.
    """
    return _build_message(
        to_email=to_email, subject=subject, html_body=html_body,
        text_body=text_body, unsubscribe_url=unsubscribe_url,
        one_click_url=one_click_url, is_bulk=is_bulk)


def build_mime(
    *,
    to_email: str,
    subject: str,
    html_body: str,
    text_body: str,
    unsubscribe_url: str = "",
    one_click_url: str = "",
    is_bulk: bool = True,
) -> Tuple[str, str]:
    """Build a full MIME message. Returns (base64url_raw, message_id)."""
    msg, message_id = _build_message(
        to_email=to_email, subject=subject, html_body=html_body,
        text_body=text_body, unsubscribe_url=unsubscribe_url,
        one_click_url=one_click_url, is_bulk=is_bulk)
    return base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii"), message_id


def _build_message(
    *,
    to_email: str,
    subject: str,
    html_body: str,
    text_body: str,
    unsubscribe_url: str = "",
    one_click_url: str = "",
    is_bulk: bool = True,
) -> Tuple[MIMEMultipart, str]:
    """The one place a message is assembled, whatever the transport.

    Headers that matter and why:
      List-Unsubscribe / List-Unsubscribe-Post — Gmail and Outlook render a
        native "Unsubscribe" control from these, and since Feb 2024 Google
        REQUIRES one-click unsubscribe on bulk mail from any sender doing
        volume. Without them bulk mail is throttled or junked.
      Precedence: bulk / Auto-Submitted — tells well-behaved autoresponders
        not to reply, so an out-of-office does not bounce back per recipient.
      Message-ID — unique per message. The provider assigns its own too, but a
        stable one of ours is what makes a delivery traceable in the logs.
    """
    subject = _clean_header(subject, 900)
    to_email = _clean_header(to_email, MAX_EMAIL_LEN)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = formataddr((_clean_header(sender_name(), 120), sender_email()))
    msg["To"] = to_email
    msg["Reply-To"] = reply_to()
    msg["Date"] = formatdate(localtime=True)
    message_id = make_msgid(domain=(sender_email().split("@")[-1] or "beardedsealstudios.com"))
    msg["Message-ID"] = message_id
    msg["MIME-Version"] = "1.0"

    if unsubscribe_url:
        targets = ["<%s>" % _clean_header(unsubscribe_url, 900)]
        if one_click_url:
            targets.insert(0, "<%s>" % _clean_header(one_click_url, 900))
        # mailto fallback for clients that only support the RFC 2369 form.
        targets.append("<mailto:%s?subject=unsubscribe>" % reply_to())
        msg["List-Unsubscribe"] = ", ".join(targets)
        if one_click_url:
            msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    if is_bulk:
        msg["Precedence"] = "bulk"
        msg["Auto-Submitted"] = "auto-generated"
        msg["X-Auto-Response-Suppress"] = "OOF, AutoReply"

    # Plain text FIRST: multipart/alternative is "last part wins", so the
    # richest form must come last or clients show the text version.
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    return msg, message_id


# ═══════════════════════════════════════════════════════════════════════════
#  TRANSPORTS
# ═══════════════════════════════════════════════════════════════════════════
class SendError(Exception):
    """A send that failed. `category` drives retry policy; `retryable` says
    whether trying the same recipient again could ever work."""

    def __init__(self, message: str, *, category: str = "unknown", retryable: bool = True,
                 status: int = 0) -> None:
        super().__init__(message)
        self.category = category
        self.retryable = retryable
        self.status = status


_TOKEN_LOCK = threading.Lock()
_TOKEN_CACHE: Dict[str, Any] = {"access_token": "", "expires_at": 0.0, "email": "", "scopes": ""}


def _redact(text: str) -> str:
    """Never let a token reach a log line. Google's error bodies sometimes echo
    parts of the request back, so everything that looks like a credential is
    replaced before anything is printed."""
    s = str(text or "")
    s = re.sub(r"(ya29|1//|AIza)[A-Za-z0-9._\-]{6,}", r"\1<redacted>", s)
    s = re.sub(r'("(?:access_token|refresh_token|id_token|client_secret)"\s*:\s*")[^"]*',
               r"\1<redacted>", s)
    return s[:600]


def _http_json(url: str, *, data: Optional[bytes] = None, headers: Optional[Dict[str, str]] = None,
               method: str = "GET", timeout: int = 30) -> Tuple[int, Dict[str, Any]]:
    req = urllib.request.Request(url, data=data, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
            try:
                return resp.status, json.loads(body) if body else {}
            except json.JSONDecodeError:
                return resp.status, {"raw": body[:400]}
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", "replace")
            parsed = json.loads(body) if body else {}
        except Exception:  # noqa: BLE001
            parsed = {"raw": "<unparseable>"}
        return exc.code, parsed if isinstance(parsed, dict) else {"raw": str(parsed)[:400]}
    except urllib.error.URLError as exc:
        raise SendError("network error: %s" % _redact(str(exc.reason)),
                        category="network", retryable=True) from exc
    except TimeoutError as exc:
        raise SendError("timeout", category="network", retryable=True) from exc


# ═══════════════════════════════════════════════════════════════════════════
#  TRANSPORT 1 — SMTP  (the default: four values from your mail provider)
# ═══════════════════════════════════════════════════════════════════════════
def smtp_settings() -> Dict[str, Any]:
    """Host/port/security, with sane defaults so only HOST/USER/PASSWORD are
    genuinely required. Port 587 + STARTTLS is what essentially every provider
    wants; 465 implies implicit TLS and is auto-detected from the port so a
    mismatched pair cannot hang for 60 seconds looking like a dead network."""
    host = _env("SMTP_HOST")
    try:
        port = int(_env("SMTP_PORT", "587") or 587)
    except ValueError:
        port = 587
    sec = _env("SMTP_SECURITY").lower()
    if sec not in ("starttls", "ssl", "none"):
        sec = "ssl" if port == 465 else "starttls"
    return {
        "host": host,
        "port": port,
        "security": sec,
        "username": _env("SMTP_USERNAME"),
        # Google shows an app password as four space-separated groups
        # ("woff lfgo xgfb rhpv"), so that is what gets pasted into Render.
        # Whether Gmail tolerates the spaces on the wire is not something to
        # find out in production, and no provider has a password with a space
        # in it, so strip ALL whitespace and remove the question.
        "password": re.sub(r"\s+", "", _env("SMTP_PASSWORD")),
        "timeout": 30,
    }


_SMTP_LOCK = threading.Lock()
_SMTP_CONN: Dict[str, Any] = {"conn": None, "at": 0.0}
# Reuse a connection for this long. Opening a fresh TLS session per message
# turns a 2,000-message campaign into 2,000 handshakes; leaving one open
# forever gets it silently dropped by the provider and every later send fails.
_SMTP_REUSE_SEC = 60.0


def _smtp_close() -> None:
    conn = _SMTP_CONN.get("conn")
    _SMTP_CONN["conn"] = None
    if conn is not None:
        try:
            conn.quit()
        except Exception:  # noqa: BLE001
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


def _smtp_connect(cfg: Dict[str, Any]):
    import smtplib
    import ssl as _ssl
    ctx = _ssl.create_default_context()
    if cfg["security"] == "ssl":
        conn = smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=cfg["timeout"], context=ctx)
    else:
        conn = smtplib.SMTP(cfg["host"], cfg["port"], timeout=cfg["timeout"])
        conn.ehlo()
        if cfg["security"] == "starttls":
            conn.starttls(context=ctx)
            conn.ehlo()
    if cfg["username"]:
        conn.login(cfg["username"], cfg["password"])
    return conn


def _smtp_conn(cfg: Dict[str, Any]):
    """A live, authenticated connection — reused when fresh, reopened when not."""
    now = time.time()
    conn = _SMTP_CONN.get("conn")
    if conn is not None and (now - float(_SMTP_CONN.get("at") or 0)) < _SMTP_REUSE_SEC:
        try:
            status = conn.noop()[0]
            if status == 250:
                return conn
        except Exception:  # noqa: BLE001
            pass
    _smtp_close()
    conn = _smtp_connect(cfg)
    _SMTP_CONN["conn"] = conn
    _SMTP_CONN["at"] = now
    return conn


def _smtp_error(exc: Exception) -> "SendError":
    """Map an smtplib exception onto the retry policy.

    The distinction that matters: a bad password or a refused sender is
    permanent and must NOT be retried for every one of ten thousand recipients,
    whereas a dropped connection or a 4xx is exactly what retries are for.
    """
    import smtplib
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return SendError(
            "The mail server rejected the username/password. If this is Google "
            "Workspace you must use an App Password (not your normal password), "
            "and 2-Step Verification has to be on.",
            category="auth_revoked", retryable=False)
    if isinstance(exc, smtplib.SMTPSenderRefused):
        return SendError("The server refused the From address (%s). It usually must match "
                         "the SMTP username." % sender_email(),
                         category="forbidden", retryable=False)
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return SendError("The server refused that recipient address.",
                         category="invalid_recipient", retryable=False)
    if isinstance(exc, smtplib.SMTPDataError):
        code = getattr(exc, "smtp_code", 0) or 0
        # 4xx is temporary (greylisting, throttling); 5xx is permanent.
        if 400 <= code < 500:
            return SendError("Mail server temporary error %s." % code,
                             category="rate_limit", retryable=True)
        return SendError("Mail server rejected the message (%s)." % code,
                         category="invalid_message", retryable=False)
    if isinstance(exc, (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError)):
        return SendError("Lost the connection to the mail server.",
                         category="network", retryable=True)
    if isinstance(exc, (OSError, TimeoutError)):
        return SendError(
            "Could not reach %s:%s. Some hosts block outbound SMTP — if this keeps "
            "happening, switch to an HTTPS email API by setting NEWSLETTER_API_KEY."
            % (_env("SMTP_HOST"), smtp_settings()["port"]),
            category="network", retryable=True)
    return SendError("SMTP error: %s" % _redact(str(exc)), category="unknown", retryable=True)


def _send_smtp(msg, to_email: str) -> Dict[str, Any]:
    cfg = smtp_settings()
    if not (cfg["host"] and cfg["username"] and cfg["password"]):
        raise SendError("SMTP is not configured (need SMTP_HOST, SMTP_USERNAME, "
                        "SMTP_PASSWORD).", category="config", retryable=False)
    with _SMTP_LOCK:
        try:
            conn = _smtp_conn(cfg)
            conn.send_message(msg, from_addr=sender_email(), to_addrs=[to_email])
        except Exception as exc:  # noqa: BLE001
            _smtp_close()
            err = _smtp_error(exc)
            # One clean retry on a dropped connection: providers close idle
            # sockets routinely and that must not surface as a failed send.
            if err.retryable and err.category == "network":
                try:
                    conn = _smtp_conn(cfg)
                    conn.send_message(msg, from_addr=sender_email(), to_addrs=[to_email])
                    return {"providerId": ""}
                except Exception as exc2:  # noqa: BLE001
                    _smtp_close()
                    raise _smtp_error(exc2) from exc2
            raise err from exc
    return {"providerId": ""}


def _smtp_check() -> Dict[str, Any]:
    """Connect and authenticate without sending anything."""
    cfg = smtp_settings()
    out = {"connected": False, "error": ""}
    if not (cfg["host"] and cfg["username"] and cfg["password"]):
        out["error"] = "Set SMTP_HOST, SMTP_USERNAME and SMTP_PASSWORD."
        return out
    with _SMTP_LOCK:
        try:
            _smtp_close()
            conn = _smtp_connect(cfg)
            _SMTP_CONN["conn"] = conn
            _SMTP_CONN["at"] = time.time()
            out["connected"] = True
        except Exception as exc:  # noqa: BLE001
            _smtp_close()
            out["error"] = str(_smtp_error(exc))
    return out


# ═══════════════════════════════════════════════════════════════════════════
#  TRANSPORT 2 — HTTPS email API (Resend / Postmark / Brevo / SendGrid)
# ═══════════════════════════════════════════════════════════════════════════
def _http_headers(provider: str, key: str) -> Dict[str, str]:
    spec = HTTP_PROVIDERS[provider]
    h = {"Content-Type": "application/json"}
    if spec["auth"] == "bearer":
        h["Authorization"] = "Bearer " + key
    elif spec["auth"] == "postmark":
        h["X-Postmark-Server-Token"] = key
    elif spec["auth"] == "brevo":
        h["api-key"] = key
    return h


def _http_body(provider: str, *, to_email: str, subject: str, html_body: str,
               text_body: str, headers: Dict[str, str]) -> bytes:
    """Shape the request for one provider. The custom headers carry
    List-Unsubscribe, which every provider exposes differently and none of them
    add for you."""
    spec = HTTP_PROVIDERS[provider]
    frm = formataddr((_clean_header(sender_name(), 120), sender_email()))
    style = spec["style"]
    if style == "resend":
        body = {"from": frm, "to": [to_email], "subject": subject,
                "html": html_body, "text": text_body,
                "reply_to": reply_to(), "headers": headers}
    elif style == "postmark":
        body = {"From": frm, "To": to_email, "Subject": subject,
                "HtmlBody": html_body, "TextBody": text_body,
                "ReplyTo": reply_to(), "MessageStream": _env("POSTMARK_STREAM", "broadcast"),
                "Headers": [{"Name": k, "Value": v} for k, v in headers.items()]}
    elif style == "brevo":
        body = {"sender": {"email": sender_email(), "name": sender_name()},
                "to": [{"email": to_email}], "subject": subject,
                "htmlContent": html_body, "textContent": text_body,
                "replyTo": {"email": reply_to()}, "headers": headers}
    else:  # sendgrid
        body = {"personalizations": [{"to": [{"email": to_email}]}],
                "from": {"email": sender_email(), "name": sender_name()},
                "reply_to": {"email": reply_to()}, "subject": subject,
                "content": [{"type": "text/plain", "value": text_body},
                            {"type": "text/html", "value": html_body}],
                "headers": headers}
    return json.dumps(body).encode("utf-8")


def _send_http(*, to_email: str, subject: str, html_body: str, text_body: str,
               unsub: str, one_click: str, is_bulk: bool) -> Dict[str, Any]:
    provider = http_provider()
    if not provider:
        raise SendError("No email API is configured (set NEWSLETTER_API_KEY).",
                        category="config", retryable=False)
    key = _api_key_for(provider)
    spec = HTTP_PROVIDERS[provider]

    headers: Dict[str, str] = {}
    if unsub:
        targets = ["<%s>" % _clean_header(unsub, 900)]
        if one_click:
            targets.insert(0, "<%s>" % _clean_header(one_click, 900))
        targets.append("<mailto:%s?subject=unsubscribe>" % reply_to())
        headers["List-Unsubscribe"] = ", ".join(targets)
        if one_click:
            headers["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    if is_bulk:
        headers["Precedence"] = "bulk"

    body = _http_body(provider, to_email=to_email, subject=_clean_header(subject, 900),
                      html_body=html_body, text_body=text_body, headers=headers)
    status, data = _http_json(spec["url"], data=body,
                              headers=_http_headers(provider, key), method="POST")

    if 200 <= status < 300:
        pid = ""
        for k in spec["id_path"]:
            pid = str((data or {}).get(k) or "")
            if pid:
                break
        return {"providerId": pid}

    detail = _redact(json.dumps(data))
    if status in (401, 403):
        raise SendError("%s rejected the API key or the From address (%s): %s"
                        % (spec["label"], status, detail),
                        category="auth_revoked", retryable=False, status=status)
    if status == 429:
        raise SendError("%s rate limit: %s" % (spec["label"], detail),
                        category="rate_limit", retryable=True, status=429)
    if status >= 500:
        raise SendError("%s server error (%s): %s" % (spec["label"], status, detail),
                        category="server", retryable=True, status=status)
    raise SendError("%s rejected the message (%s): %s" % (spec["label"], status, detail),
                    category="invalid_message", retryable=False, status=status)


def _http_check() -> Dict[str, Any]:
    """Is the API key accepted? Deliberately does NOT send anything.

    Every one of these providers answers an unauthenticated/garbage request
    with 401 and an authenticated one with 4xx-about-the-payload, so an
    intentionally-empty POST separates "key is wrong" from "key is fine" with
    nobody receiving mail.
    """
    provider = http_provider()
    out = {"connected": False, "error": ""}
    if not provider:
        out["error"] = "Set NEWSLETTER_API_KEY."
        return out
    spec = HTTP_PROVIDERS[provider]
    key = _api_key_for(provider)
    try:
        status, data = _http_json(spec["url"], data=b"{}",
                                  headers=_http_headers(provider, key), method="POST")
    except SendError as exc:
        out["error"] = "Could not reach %s: %s" % (spec["label"], exc)
        return out
    if status in (401, 403):
        out["error"] = "%s rejected the API key." % spec["label"]
        return out
    # 400/422 = the key worked and it is complaining about the empty body.
    out["connected"] = True
    return out


# ═══════════════════════════════════════════════════════════════════════════
#  TRANSPORT 3 — Gmail API (optional; the only one needing a Google project)
# ═══════════════════════════════════════════════════════════════════════════
def _fetch_access_token(force: bool = False) -> str:
    """Exchange the stored refresh token for an access token, cached until ~1
    minute before it expires. Never logged, never returned to any client, never
    written to Firestore."""
    with _TOKEN_LOCK:
        now = time.time()
        if not force and _TOKEN_CACHE["access_token"] and _TOKEN_CACHE["expires_at"] > now + 60:
            return str(_TOKEN_CACHE["access_token"])

        cid, secret, refresh = _env("GOOGLE_CLIENT_ID"), _env("GOOGLE_CLIENT_SECRET"), _env("GOOGLE_REFRESH_TOKEN")
        if not (cid and secret and refresh):
            raise SendError("Gmail is not connected (missing GOOGLE_CLIENT_ID / "
                            "GOOGLE_CLIENT_SECRET / GOOGLE_REFRESH_TOKEN).",
                            category="config", retryable=False)

        payload = urllib.parse.urlencode({
            "client_id": cid,
            "client_secret": secret,
            "refresh_token": refresh,
            "grant_type": "refresh_token",
        }).encode("utf-8")
        status, data = _http_json(
            GOOGLE_TOKEN_URI, data=payload, method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if status != 200 or not data.get("access_token"):
            err = str(data.get("error") or "")
            # invalid_grant means the refresh token was revoked, the Google
            # password changed, or the OAuth client was deleted. Retrying will
            # never fix it — a human has to re-authorise.
            if err in ("invalid_grant", "unauthorized_client", "invalid_client"):
                raise SendError(
                    "Google authorisation was revoked or is invalid (%s). "
                    "Re-run scripts/get_gmail_refresh_token.py and update "
                    "GOOGLE_REFRESH_TOKEN in Render." % err,
                    category="auth_revoked", retryable=False, status=status)
            raise SendError("token refresh failed (%s): %s" % (status, _redact(json.dumps(data))),
                            category="auth", retryable=True, status=status)

        token = str(data["access_token"])
        try:
            ttl = int(data.get("expires_in") or 3600)
        except (TypeError, ValueError):
            ttl = 3600
        _TOKEN_CACHE["access_token"] = token
        _TOKEN_CACHE["expires_at"] = now + ttl
        return token


def _gmail_check() -> Dict[str, Any]:
    """Refresh the token, then ask /tokeninfo WHICH account it belongs to.

    That is how we confirm the authorised account is genuinely allowed to send
    as the From address rather than assuming it — a mismatch means Gmail would
    reject or rewrite the From, which is the thing that must never be faked.
    No token, or fragment of one, ever leaves this function.
    """
    out: Dict[str, Any] = {"connected": False, "error": "", "authorizedAs": "", "scopes": []}
    try:
        token = _fetch_access_token(force=True)
    except SendError as exc:
        out["error"] = str(exc)
        return out
    status, data = _http_json(GOOGLE_TOKENINFO_URI + "?access_token=" + urllib.parse.quote(token))
    if status != 200:
        out["error"] = "Google rejected the access token (%s)." % status
        return out
    out["connected"] = True
    acct = str(data.get("email") or "").strip().lower()
    out["authorizedAs"] = acct
    scopes = str(data.get("scope") or "").split()
    out["scopes"] = [s.rsplit("/", 1)[-1] for s in scopes]
    if not any(s.endswith("gmail.send") for s in scopes):
        out["connected"] = False
        out["error"] = "The connected account did not grant the gmail.send scope."
    return out


def connection_status() -> Dict[str, Any]:
    """Live sending check for the admin Connections panel.

    Reports on whichever transport is ACTIVE, and is careful to distinguish
    what it verified from what it merely assumed:

      connected        — we really did open a session / the key really was
                         accepted. Not a guess.
      canSendAsSender  — the From address is CONFIRMED usable. Only the Gmail
                         API can prove this (its /tokeninfo names the account).
                         SMTP and the HTTP APIs enforce it at send time and
                         give no way to ask in advance, so this stays optimistic
                         there and the panel says a first test send is the real
                         proof. Claiming a verification we did not perform is
                         exactly how a "configured" system quietly fails.

    Never returns a password, an API key, or a token.
    """
    t = transport()
    out: Dict[str, Any] = {
        "transport": t,
        "transportLabel": transport_label(),
        "configured": bool(t),
        "connected": False,
        "senderEmail": sender_email(),
        "senderName": sender_name(),
        "replyTo": reply_to(),
        "authorizedAs": "",
        "canSendAsSender": False,
        "senderVerified": False,      # True only when genuinely PROVEN
        "scopes": [],
        "sanitizer": sanitizer_name(),
        "dailyCap": daily_send_cap(),
        "consumerGmail": sender_is_consumer_gmail(),
        "capWarning": "",
        "error": "",
        "setupHint": "",
    }
    # A cap set higher than the account can actually take is worse than no cap:
    # it lets a campaign march straight into a 24-hour suspension.
    _real = 500 if out["consumerGmail"] else 2000
    if out["dailyCap"] > _real:
        out["capWarning"] = (
            "NEWSLETTER_DAILY_SEND_CAP is %d, but a %s account only allows about "
            "%d recipients per day. Going over does not bounce — the account gets "
            "throttled or suspended for up to 24 hours."
            % (out["dailyCap"],
               "free @gmail.com" if out["consumerGmail"] else "Google Workspace",
               _real))
    elif out["consumerGmail"]:
        out["capWarning"] = (
            "Sending from a free @gmail.com address, so the daily budget is about "
            "500 recipients. Past roughly 400 subscribers, move to a domain "
            "address or an email API.")

    if not t:
        out["error"] = "No way to send email is configured yet."
        out["setupHint"] = (
            "Easiest: set SMTP_HOST, SMTP_PORT, SMTP_USERNAME and SMTP_PASSWORD "
            "from your existing mail provider. Alternative: set NEWSLETTER_API_KEY "
            "for an HTTPS email API (Resend, Postmark, Brevo, SendGrid)."
        )
        return out

    if not normalize_email(sender_email()):
        out["error"] = "NEWSLETTER_FROM_EMAIL (%r) is not a valid address." % sender_email()
        return out

    if t == "smtp":
        res = _smtp_check()
        cfg = smtp_settings()
        out["authorizedAs"] = cfg["username"]
        out["connected"] = bool(res["connected"])
        out["error"] = res["error"]
        out["scopes"] = ["%s:%s (%s)" % (cfg["host"], cfg["port"], cfg["security"])]
        # Logged in successfully ⇒ the server will accept mail from us. Whether
        # it accepts THIS From address is only knowable by trying.
        out["canSendAsSender"] = out["connected"]
        if out["connected"] and cfg["username"].lower() == sender_email().lower():
            # The From matches the authenticated mailbox — as close to proven as
            # SMTP gets without sending.
            out["senderVerified"] = True
        elif out["connected"]:
            out["setupHint"] = (
                "The From address (%s) is not the same as the SMTP login (%s). Most "
                "providers only allow that if it is a verified alias — send a test "
                "email to confirm." % (sender_email(), cfg["username"]))
        return out

    if t == "http":
        provider = http_provider()
        spec = HTTP_PROVIDERS.get(provider) or {}
        res = _http_check()
        out["authorizedAs"] = spec.get("label", provider)
        out["connected"] = bool(res["connected"])
        out["error"] = res["error"]
        out["scopes"] = [spec.get("label", provider)]
        out["canSendAsSender"] = out["connected"]
        if out["connected"]:
            out["setupHint"] = (
                "%s will only send from a domain you have verified in its dashboard. "
                "If a send is refused, verify %s there."
                % (spec.get("label", provider), sender_email().split("@")[-1]))
        return out

    # gmail_api
    res = _gmail_check()
    out["connected"] = bool(res["connected"])
    out["authorizedAs"] = res["authorizedAs"]
    out["scopes"] = res["scopes"]
    out["error"] = res["error"]
    if out["connected"] and res["authorizedAs"]:
        proven = res["authorizedAs"] == sender_email().strip().lower()
        out["canSendAsSender"] = proven
        out["senderVerified"] = proven
        if not proven:
            out["error"] = (
                "Authorised as %s but the From address is %s. Gmail will not send as "
                "that address unless it is a verified 'Send mail as' alias."
                % (res["authorizedAs"], sender_email()))
    return out


# ── Daily-cap accounting ───────────────────────────────────────────────────
# In-process, per UTC day. It intentionally resets on restart: it is a guard
# against THIS process running away, not an accounting ledger. The real
# authority is Google's own quota, and the campaign's per-recipient records are
# what prevent duplicate delivery across restarts.
_CAP_LOCK = threading.Lock()
_CAP: Dict[str, Any] = {"day": "", "count": 0}


def _cap_day() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def sends_used_today() -> int:
    with _CAP_LOCK:
        if _CAP["day"] != _cap_day():
            return 0
        return int(_CAP["count"])


def _cap_take() -> bool:
    with _CAP_LOCK:
        day = _cap_day()
        if _CAP["day"] != day:
            _CAP["day"] = day
            _CAP["count"] = 0
        if int(_CAP["count"]) >= daily_send_cap():
            return False
        _CAP["count"] = int(_CAP["count"]) + 1
        return True


# Gmail's API is happy well above this, but pacing sends keeps us clear of the
# per-user rate limiter that produces 429s, and a newsletter has no deadline.
_SEND_GAP_SEC = 0.35
_LAST_SEND = {"at": 0.0}
_SEND_LOCK = threading.Lock()


def send_email(
    *,
    to_email: str,
    subject: str,
    html_body: str,
    text_body: str,
    unsubscribe_url: str = "",
    one_click_url: str = "",
    is_bulk: bool = True,
) -> Dict[str, Any]:
    """Send exactly one message to exactly one recipient.

    One address per message is not an efficiency oversight — it is the privacy
    requirement. No To/CC/BCC ever carries more than the single subscriber the
    message is for, so no subscriber can learn that another subscriber exists.

    Returns {"messageId": …, "gmailId": …} — `gmailId` keeps its name because
    the campaign records already store it; it now holds whichever id the active
    provider returned (empty for SMTP, which has none). Raises SendError on
    failure with a category the caller uses to decide whether to retry.
    """
    to_norm = normalize_email(to_email)
    if not to_norm:
        raise SendError("invalid recipient address", category="invalid_recipient",
                        retryable=False)

    t = transport()
    if not t:
        raise SendError(
            "No way to send email is configured. Set SMTP_HOST / SMTP_USERNAME / "
            "SMTP_PASSWORD, or NEWSLETTER_API_KEY.",
            category="config", retryable=False)

    if not _cap_take():
        raise SendError(
            "Daily sending cap reached (%d messages today). Sending resumes after "
            "00:00 UTC; raise NEWSLETTER_DAILY_SEND_CAP only if your provider's "
            "quota genuinely allows it." % daily_send_cap(),
            category="daily_cap", retryable=True)

    msg, message_id = _build_message(
        to_email=to_norm, subject=subject, html_body=html_body, text_body=text_body,
        unsubscribe_url=unsubscribe_url, one_click_url=one_click_url, is_bulk=is_bulk,
    )

    # Pace the wire. Done BEFORE the send and outside any transport lock, so a
    # slow provider does not compound with the deliberate gap.
    with _SEND_LOCK:
        gap = _SEND_GAP_SEC - (time.time() - float(_LAST_SEND["at"]))
        if gap > 0:
            time.sleep(gap)
        _LAST_SEND["at"] = time.time()

    if t == "smtp":
        res = _send_smtp(msg, to_norm)
    elif t == "http":
        res = _send_http(to_email=to_norm, subject=subject, html_body=html_body,
                         text_body=text_body, unsub=unsubscribe_url,
                         one_click=one_click_url, is_bulk=is_bulk)
    else:
        res = _send_gmail_api(msg)
    return {"messageId": message_id, "gmailId": str(res.get("providerId") or "")}


def _send_gmail_api(msg) -> Dict[str, Any]:
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
    token = _fetch_access_token()
    body = json.dumps({"raw": raw}).encode("utf-8")
    headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
    status, data = _http_json(GMAIL_SEND_URI, data=body, headers=headers, method="POST")

    if status == 401:
        # Access token rotated out from under us: refresh once and retry. A
        # second 401 is a real authorisation problem, not a stale token.
        token = _fetch_access_token(force=True)
        headers["Authorization"] = "Bearer " + token
        status, data = _http_json(GMAIL_SEND_URI, data=body, headers=headers, method="POST")

    if status in (200, 202):
        return {"providerId": str(data.get("id") or "")}

    detail = _redact(json.dumps(data))
    if status == 429 or status in (500, 502, 503, 504):
        raise SendError("Gmail temporary failure (%s): %s" % (status, detail),
                        category="rate_limit" if status == 429 else "server",
                        retryable=True, status=status)
    if status == 403:
        # 403 covers both "quota exceeded" (retryable tomorrow) and "this app
        # is not allowed to send" (never retryable). The reason string is the
        # only way to tell them apart.
        reason = json.dumps(data).lower()
        if "quota" in reason or "rate" in reason or "limit" in reason:
            raise SendError("Gmail quota/rate limit (403): %s" % detail,
                            category="rate_limit", retryable=True, status=403)
        raise SendError("Gmail refused the send (403): %s" % detail,
                        category="forbidden", retryable=False, status=403)
    if status == 400:
        raise SendError("Gmail rejected the message (400): %s" % detail,
                        category="invalid_message", retryable=False, status=400)
    raise SendError("Gmail send failed (%s): %s" % (status, detail),
                    category="unknown", retryable=(status >= 500), status=status)


# ═══════════════════════════════════════════════════════════════════════════
#  PREBUILT MESSAGES
# ═══════════════════════════════════════════════════════════════════════════
def build_welcome(unsubscribe_url: str, one_click_url: str = "") -> Dict[str, str]:
    body = welcome_body_html()
    return {
        "subject": WELCOME_SUBJECT,
        "html": render_email_html(body_html=body, unsubscribe_url=unsubscribe_url,
                                  preview_text="Thank you for joining the Currents & Critters "
                                               "email list."),
        "text": render_email_text(body_html=body, unsubscribe_url=unsubscribe_url),
    }


CONFIRM_SUBJECT = "Please confirm your Currents & Critters email signup"


def build_confirmation(confirm_url: str) -> Dict[str, str]:
    """The "click to confirm" email for a PUBLIC website signup.

    Why a public form needs this and a Stripe checkout does not: at checkout
    the person has already proven they control the address (they paid with it,
    and Stripe emailed them a receipt). A box on a web page proves nothing —
    anyone can type a stranger's address into it. Sending that stranger a
    newsletter they never asked for is how a sending account gets reported and
    suspended, which at a few hundred messages a day is the whole channel.

    So a website signup creates a PENDING record that no campaign can ever
    reach, and only the person holding the inbox can turn it into a subscriber.
    """
    body = (
        '<p style="margin:0 0 16px;font-size:21px;font-weight:800;color:%(deep)s;">'
        'One more tap</p>'
        '<p style="margin:0 0 16px;">Thanks for signing up for the Currents &amp; '
        'Critters email list! Please confirm your address so I know it&rsquo;s '
        'really you.</p>'
        '<p style="margin:0 0 26px;" class="cc-center">'
        '<a href="%(url)s" style="display:inline-block;background:%(teal)s;'
        'color:#022b33;font-size:16px;font-weight:800;text-decoration:none;'
        'padding:14px 32px;border-radius:10px;">Confirm my email</a></p>'
        '<p style="margin:0 0 16px;font-size:14px;color:%(muted)s;">'
        'If the button doesn&rsquo;t work, copy this link into your browser:<br />'
        '<span style="word-break:break-all;">%(url_text)s</span></p>'
        '<p style="margin:0;font-size:14px;color:%(muted)s;">'
        'If you didn&rsquo;t sign up, just ignore this email &mdash; you will not '
        'be added to the list and you will not hear from us again.</p>'
    ) % {"deep": _DEEP, "teal": _TEAL, "muted": _MUTED,
         "url": _html.escape(confirm_url, quote=True),
         "url_text": _html.escape(confirm_url)}

    text = (
        "One more tap\n\n"
        "Thanks for signing up for the Currents & Critters email list! "
        "Please confirm your address so I know it's really you:\n\n"
        + confirm_url + "\n\n"
        "If you didn't sign up, just ignore this email - you will not be added "
        "to the list and you will not hear from us again.\n"
    )
    return {
        "subject": CONFIRM_SUBJECT,
        # No unsubscribe footer: there is nothing to unsubscribe FROM yet, and
        # offering one would imply they are already on the list.
        "html": render_email_html(body_html=body, unsubscribe_url="",
                                  show_visit_button=False,
                                  preview_text="Confirm your email to join the list."),
        "text": "Currents & Critters\n" + ("=" * 46) + "\n\n" + text
                + _footer_text(""),
    }


OWNER_NOTIFY_SUBJECT = "New Currents & Critters Newsletter Subscriber"


def build_owner_notification(
    *, subscriber_email: str, subscribed_at: str, source: str, is_reactivation: bool,
    active_total: Optional[int] = None,
) -> Dict[str, str]:
    """The heads-up to Tim. Carries the subscriber's address, when, where from
    and whether it was new or a reactivation — and deliberately NOT the
    unsubscribe token, which would let anyone who ever saw this mailbox
    unsubscribe that person."""
    kind = "Reactivation (previously unsubscribed)" if is_reactivation else "New signup"
    rows = [
        ("Email address", subscriber_email),
        ("Date and time", subscribed_at),
        ("Signup source", source),
        ("Type", kind),
    ]
    if active_total is not None:
        rows.append(("Active subscribers now", "{:,}".format(active_total)))

    body_rows = "".join(
        '<tr>'
        '<td style="padding:7px 14px 7px 0;color:%s;font-size:14px;white-space:nowrap;'
        'vertical-align:top;">%s</td>'
        '<td style="padding:7px 0;font-size:15px;font-weight:700;color:%s;">%s</td>'
        '</tr>' % (_MUTED, _html.escape(k), _INK, _html.escape(str(v)))
        for k, v in rows
    )
    body = (
        '<p style="margin:0 0 18px;font-size:20px;font-weight:800;color:%s;">'
        'Someone joined the email list</p>'
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        'style="width:100%%;">%s</table>'
        '<p style="margin:20px 0 0;font-size:14px;color:%s;">'
        'Manage subscribers in the newsletter admin: '
        '<a href="%s/admin/newsletter" style="color:%s;">%s/admin/newsletter</a></p>'
    ) % (_DEEP, body_rows, _MUTED,
         _html.escape(app_base_url(), quote=True), _INK_2,
         _html.escape(app_base_url()))

    text_rows = "\n".join("%s: %s" % (k, v) for k, v in rows)
    return {
        "subject": OWNER_NOTIFY_SUBJECT,
        # No unsubscribe footer and no Visit button: this is an internal
        # operational notice to the owner, not marketing mail, so attaching a
        # marketing footer to it would be both wrong and confusing.
        "html": render_email_html(body_html=body, unsubscribe_url="", show_visit_button=False,
                                  preview_text="%s — %s" % (subscriber_email, kind)),
        "text": "New Currents & Critters newsletter subscriber\n\n" + text_rows
                + "\n\nAdmin: " + app_base_url() + "/admin/newsletter\n",
    }


def new_correlation_id() -> str:
    """Short id tying an audit entry to the request that caused it."""
    return uuid.uuid4().hex[:12]
