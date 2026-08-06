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
GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"
GOOGLE_TOKENINFO_URI = "https://oauth2.googleapis.com/tokeninfo"
GMAIL_SEND_URI = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"

# The minimum scopes that do the job:
#   gmail.send  — send only. It cannot read a single message in the mailbox,
#                 which is the whole point of not asking for gmail.modify.
#   openid,email — lets /tokeninfo tell us WHICH account the refresh token
#                 belongs to, so we can verify it is allowed to send as
#                 GMAIL_SENDER_EMAIL without requesting gmail.readonly or
#                 gmail.settings.basic (both far broader).
GMAIL_SCOPES = "https://www.googleapis.com/auth/gmail.send openid email"

BRAND_NAME = "Currents & Critters"
BUSINESS_NAME = "Bearded Seal Studios LLC"
BUSINESS_ADDRESS_LINES = (
    "916A South Douglas Avenue",
    "Nashville, TN 37204-2021",
)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def sender_email() -> str:
    return _env("GMAIL_SENDER_EMAIL", "timothy.honey@beardedsealstudios.com")


def sender_name() -> str:
    return _env("GMAIL_SENDER_NAME", BRAND_NAME)


def reply_to() -> str:
    return _env("NEWSLETTER_REPLY_TO", sender_email())


def admin_email() -> str:
    return _env("ADMIN_EMAIL", "timothy.honey@beardedsealstudios.com").lower()


def site_url() -> str:
    return _env("CURRENTS_AND_CRITTERS_URL", "https://currentsandcritters.com").rstrip("/")


def app_base_url() -> str:
    """Where the unsubscribe links point — the Render host that runs this code."""
    return _env("APP_BASE_URL", "https://play.currentsandcritters.com").rstrip("/")


def privacy_url() -> str:
    return _env("PRIVACY_POLICY_URL", site_url() + "/privacy")


def daily_send_cap() -> int:
    try:
        return max(1, int(_env("NEWSLETTER_DAILY_SEND_CAP", "1200")))
    except ValueError:
        return 1200


def gmail_configured() -> bool:
    return bool(_env("GOOGLE_CLIENT_ID") and _env("GOOGLE_CLIENT_SECRET")
                and _env("GOOGLE_REFRESH_TOKEN"))


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
    """Build a full MIME message. Returns (base64url_raw, message_id).

    Headers that matter and why:
      List-Unsubscribe / List-Unsubscribe-Post — Gmail and Outlook render a
        native "Unsubscribe" control from these, and since Feb 2024 Google
        REQUIRES one-click unsubscribe on bulk mail from any sender doing
        volume. Without them bulk mail is throttled or junked.
      Precedence: bulk / Auto-Submitted — tells well-behaved autoresponders
        not to reply, so an out-of-office does not bounce back per recipient.
      Message-ID — unique per message. Gmail assigns its own too, but a stable
        one of ours is what makes a delivery traceable in the logs.
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

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
    return raw, message_id


# ═══════════════════════════════════════════════════════════════════════════
#  GMAIL TRANSPORT
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


def connection_status() -> Dict[str, Any]:
    """Live Gmail connection check for the admin Settings panel.

    Refreshes the access token, then asks /tokeninfo which account it belongs
    to and which scopes it carries. That is how we confirm the authenticated
    account is genuinely allowed to send as GMAIL_SENDER_EMAIL rather than
    assuming it — a mismatch here means Gmail would reject or rewrite the From
    address, which is exactly the thing that must never be faked.

    Returns only booleans, the account email and scope names. No token, no
    fragment of a token, ever leaves this function.
    """
    out: Dict[str, Any] = {
        "configured": gmail_configured(),
        "connected": False,
        "senderEmail": sender_email(),
        "senderName": sender_name(),
        "replyTo": reply_to(),
        "authorizedAs": "",
        "canSendAsSender": False,
        "scopes": [],
        "sanitizer": sanitizer_name(),
        "dailyCap": daily_send_cap(),
        "error": "",
    }
    if not out["configured"]:
        out["error"] = ("Not connected. Set GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET "
                        "and GOOGLE_REFRESH_TOKEN in Render.")
        return out
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
        out["error"] = ("The connected account did not grant the gmail.send scope. "
                        "Re-run the refresh-token script and approve sending.")
        return out
    if not acct:
        out["error"] = ("Could not read the authorised account address — the 'email' "
                        "scope was not granted. Re-run the refresh-token script.")
        return out
    out["canSendAsSender"] = (acct == sender_email().strip().lower())
    if not out["canSendAsSender"]:
        out["error"] = (
            "Authorised as %s but GMAIL_SENDER_EMAIL is %s. Gmail will not let this "
            "account send as that address unless it is a verified 'Send mail as' alias. "
            "Either re-authorise as %s, or set GMAIL_SENDER_EMAIL to the authorised "
            "account." % (acct, sender_email(), sender_email())
        )
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

    Returns {"messageId": …, "gmailId": …}. Raises SendError on failure with a
    category the caller uses to decide whether to retry.
    """
    to_norm = normalize_email(to_email)
    if not to_norm:
        raise SendError("invalid recipient address", category="invalid_recipient",
                        retryable=False)
    if not _cap_take():
        raise SendError(
            "Daily sending cap reached (%d messages today). Sending resumes after "
            "00:00 UTC; raise NEWSLETTER_DAILY_SEND_CAP only if your Google "
            "Workspace quota genuinely allows it." % daily_send_cap(),
            category="daily_cap", retryable=True)

    raw, message_id = build_mime(
        to_email=to_norm, subject=subject, html_body=html_body, text_body=text_body,
        unsubscribe_url=unsubscribe_url, one_click_url=one_click_url, is_bulk=is_bulk,
    )
    token = _fetch_access_token()

    # Pace the wire without holding the lock across the network call.
    with _SEND_LOCK:
        gap = _SEND_GAP_SEC - (time.time() - float(_LAST_SEND["at"]))
        if gap > 0:
            time.sleep(gap)
        _LAST_SEND["at"] = time.time()

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
        return {"messageId": message_id, "gmailId": str(data.get("id") or "")}

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
