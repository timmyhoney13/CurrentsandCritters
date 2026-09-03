"""test_partner_contact.py, the website's Partner With Us form.

This is a PUBLIC form that SENDS EMAIL, which is two dangerous things at once,
so most of what is pinned here is about what it refuses to do:

  1. The recipient is never in the request. Whatever a caller puts in the body,
     the mail goes to ADMIN_EMAIL and nowhere else, or this is a spam relay.
  2. The acknowledgement to the sender quotes NOTHING they typed. The moment it
     does, the form becomes a way to mail arbitrary text to a stranger.
  3. The honeypot is answered like a success. Telling a bot which check it
     failed is telling it how to pass.
  4. Both rate limits hold: per IP, and a global ceiling, because the sending
     quota is shared with the newsletter and with every password reset.
  5. Nothing a stranger typed reaches a header unescaped (a newline in a name
     must not become a second header) or the HTML body unescaped.
  6. Reply-To really is the enquirer, on the MIME path AND in all four HTTPS
     provider payloads. In production an HTTPS provider is what sends, so an
     override that only worked over SMTP would look right in a unit test and do
     nothing at all in the inbox.
  7. The one list of partnership kinds: the <select> on the website and KINDS
     here must agree exactly, or an enquiry arrives labelled as something the
     sender did not pick.
  8. A send that fails says so. Nothing is stored anywhere, so a swallowed
     failure is a lost message.

    python3 test_partner_contact.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import newsletter_email as nl_email  # noqa: E402
import partner_contact as pc  # noqa: E402


GOOD = {
    "name": "Alex Rivera",
    "org": "Blue Harbor Aquarium",
    "email": "alex@blueharbor.org",
    "link": "blueharbor.org",
    "kind": "conservation",
    "message": "We run a coastal education program and would love to put "
               "Currents and Critters in front of our visitors this summer.",
}


class FakeHandler:
    """The one method partner_contact calls on the real handler, plus the two
    attributes _client_key reads."""

    def __init__(self, ip="203.0.113.7"):
        self.payload = None
        self.status = 200
        self.headers = {"X-Forwarded-For": ip}
        self.client_address = (ip, 1234)

    def _send_json(self, payload, status=200):
        self.payload = payload
        self.status = status


class Parsed:
    def __init__(self, path):
        self.path = path


class Base(unittest.TestCase):
    """Every test runs against a captured transport: nothing leaves the process
    and the real ADMIN_EMAIL is replaced by two known addresses."""

    ADMINS = ["tim@example.com", "studio@example.com"]

    def setUp(self):
        pc._RL.clear()
        self.sent = []
        self.fail_addresses = set()

        def fake_send(**kw):
            if kw.get("to_email") in self.fail_addresses:
                raise RuntimeError("provider said no")
            self.sent.append(kw)
            return {"messageId": "<x@y>", "gmailId": ""}

        self._real = (nl_email.send_email, nl_email.transport,
                      nl_email.admin_emails, nl_email.admin_email)
        nl_email.send_email = fake_send
        nl_email.transport = lambda: "http"
        nl_email.admin_emails = lambda: list(self.ADMINS)
        nl_email.admin_email = lambda: self.ADMINS[0]

    def tearDown(self):
        (nl_email.send_email, nl_email.transport,
         nl_email.admin_emails, nl_email.admin_email) = self._real

    def submit(self, **over):
        body = dict(GOOD)
        body.update(over)
        return pc.submit(body, over.pop("_client", "203.0.113.7"))

    def to_admins(self):
        return [m for m in self.sent if m["to_email"] in self.ADMINS]

    def to_sender(self):
        return [m for m in self.sent if m["to_email"] not in self.ADMINS]


# ══════════════════════════════════════════════════════════════════════════
#  1. The recipient is never in the request
# ══════════════════════════════════════════════════════════════════════════
class Recipients(Base):
    def test_goes_to_every_admin_address_and_nowhere_else(self):
        out = self.submit()
        self.assertTrue(out["ok"], out)
        self.assertEqual(sorted(m["to_email"] for m in self.to_admins()),
                         sorted(self.ADMINS))

    def test_a_recipient_named_in_the_body_is_ignored(self):
        # Every spelling somebody might try to smuggle a destination in with.
        out = self.submit(to="victim@example.com", to_email="victim@example.com",
                          recipients=["victim@example.com"], cc="victim@example.com",
                          bcc="victim@example.com")
        self.assertTrue(out["ok"], out)
        addressed = {m["to_email"] for m in self.sent}
        self.assertNotIn("victim@example.com", addressed)
        self.assertEqual(addressed, set(self.ADMINS) | {GOOD["email"]})

    def test_the_only_non_admin_message_is_the_acknowledgement(self):
        self.submit()
        acks = self.to_sender()
        self.assertEqual([m["to_email"] for m in acks], [GOOD["email"]])


# ══════════════════════════════════════════════════════════════════════════
#  2. The acknowledgement carries no caller text
# ══════════════════════════════════════════════════════════════════════════
class Acknowledgement(Base):
    def test_it_quotes_nothing_the_sender_typed(self):
        self.submit(name="BUY CHEAP PILLS AT evil.example",
                    org="evil.example",
                    message="Click http://evil.example right now to claim your prize today.")
        ack = self.to_sender()[0]
        blob = ack["subject"] + ack["html_body"] + ack["text_body"]
        for leak in ("PILLS", "evil.example", "claim your prize"):
            self.assertNotIn(leak, blob,
                             "the acknowledgement must not echo caller text: %r" % leak)

    def test_it_is_the_constant_body(self):
        self.submit()
        ack = self.to_sender()[0]
        self.assertEqual(ack["subject"], pc.ACK_SUBJECT)
        self.assertEqual(ack["text_body"], pc.ACK_TEXT)
        self.assertEqual(ack["html_body"], pc.ACK_HTML)

    def test_a_failed_acknowledgement_never_fails_the_enquiry(self):
        self.fail_addresses = {GOOD["email"]}
        out = self.submit()
        self.assertTrue(out["ok"], "the enquiry arrived; only the receipt bounced")
        self.assertEqual(len(self.to_admins()), 2)


# ══════════════════════════════════════════════════════════════════════════
#  3. Validation, and the honeypot
# ══════════════════════════════════════════════════════════════════════════
class Validation(Base):
    def test_the_good_case_passes(self):
        _, err = pc.validate(dict(GOOD))
        self.assertEqual(err, "")

    def test_refusals(self):
        cases = [
            ("no name", {"name": " "}, "name"),
            ("one-letter name", {"name": "A"}, "name"),
            ("no email", {"email": ""}, "email"),
            ("junk email", {"email": "not-an-address"}, "email"),
            ("unknown kind", {"kind": "whatever"}, "partnership"),
            ("no kind", {"kind": ""}, "partnership"),
            ("empty message", {"message": "   "}, "message"),
            ("too short", {"message": "hi there"}, "more"),
        ]
        for label, over, word in cases:
            body = dict(GOOD)
            body.update(over)
            _, err = pc.validate(body)
            self.assertTrue(err, label + " should be refused")
            self.assertIn(word, err.lower(), "%s: %r" % (label, err))

    def test_an_unfilled_template_is_refused_by_name(self):
        body = dict(GOOD)
        body["message"] = ("Hi Tim,\n\nI'm ____ from ____. We'd love to work with "
                           "Currents and Critters on ____.\n\nThanks!")
        _, err = pc.validate(body)
        self.assertIn("blank", err.lower())
        out = self.submit(message=body["message"])
        self.assertFalse(out["ok"])
        self.assertEqual(self.sent, [], "an unfilled template must not be mailed")

    def test_a_refusal_sends_nothing(self):
        out = self.submit(email="nope")
        self.assertFalse(out["ok"])
        self.assertEqual(self.sent, [])

    def test_the_honeypot_looks_exactly_like_a_success(self):
        clean = self.submit()
        self.sent = []
        trapped = self.submit(company_site="http://spam.example")
        self.assertTrue(trapped["ok"])
        self.assertEqual(trapped, clean, "a bot must not be able to tell it was caught")
        self.assertEqual(self.sent, [], "a honeypot hit sends no mail at all")


# ══════════════════════════════════════════════════════════════════════════
#  4. Rate limits
# ══════════════════════════════════════════════════════════════════════════
class RateLimits(Base):
    def test_per_ip(self):
        for i in range(pc.PER_IP_LIMIT):
            self.assertTrue(pc.submit(dict(GOOD), "198.51.100.4")["ok"], "send %d" % i)
        out = pc.submit(dict(GOOD), "198.51.100.4")
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "too_many_requests")
        self.assertIn(pc.public_inbox(), out["message"], "it must still say where to write")

    def test_another_ip_is_unaffected(self):
        for _ in range(pc.PER_IP_LIMIT + 2):
            pc.submit(dict(GOOD), "198.51.100.4")
        self.assertTrue(pc.submit(dict(GOOD), "198.51.100.5")["ok"])

    def test_the_global_ceiling_holds_across_ips(self):
        ok = 0
        for i in range(pc.GLOBAL_LIMIT + 10):
            if pc.submit(dict(GOOD), "10.0.%d.%d" % (i // 250, i % 250))["ok"]:
                ok += 1
        self.assertEqual(ok, pc.GLOBAL_LIMIT,
                         "the hourly ceiling is what stops a spread-out flood")

    def test_a_refused_submission_does_not_spend_the_allowance(self):
        for _ in range(10):
            pc.submit(dict(GOOD, email="junk"), "198.51.100.9")
        self.assertTrue(pc.submit(dict(GOOD), "198.51.100.9")["ok"],
                        "validation happens before the limiter, so typos are free")


# ══════════════════════════════════════════════════════════════════════════
#  5. Nothing a stranger typed escapes into a header or the HTML
# ══════════════════════════════════════════════════════════════════════════
class Injection(Base):
    def test_a_newline_in_a_name_cannot_become_a_second_header(self):
        out = self.submit(name="Alex\r\nBcc: victim@example.com",
                          org="Reef\nX-Spam: no")
        self.assertTrue(out["ok"], out)
        subject = self.to_admins()[0]["subject"]
        self.assertNotIn("\n", subject)
        self.assertNotIn("\r", subject)

    def test_markup_in_the_message_is_escaped_in_the_html(self):
        out = self.submit(name='<img src=x onerror="alert(1)">',
                          message="Partnering sounds great <script>alert(1)</script> "
                                  "and here is a & sign to escape too.")
        self.assertTrue(out["ok"], out)
        html = self.to_admins()[0]["html_body"]
        # No tag a stranger typed survives as a tag. ("onerror=" as literal
        # text is fine and is what escaping looks like; `<img` is not.)
        self.assertNotIn("<script>", html)
        self.assertNotIn("<img", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("&lt;img src=x onerror=&quot;alert(1)&quot;&gt;", html)
        self.assertIn("&amp;", html)

    def test_the_message_survives_intact_in_the_text_part(self):
        out = self.submit(message="Line one.\n\nLine two, with an ampersand & a <tag>.")
        self.assertTrue(out["ok"], out)
        text = self.to_admins()[0]["text_body"]
        self.assertIn("Line one.", text)
        self.assertIn("Line two, with an ampersand & a <tag>.", text)

    def test_every_field_is_length_capped(self):
        out = self.submit(name="A" * 5000, org="B" * 5000, link="C" * 5000,
                          message="D" * 9000)
        self.assertTrue(out["ok"], out)
        data, _ = pc.validate({**GOOD, "name": "A" * 5000, "org": "B" * 5000,
                               "link": "C" * 5000, "message": "D" * 9000})
        self.assertEqual(len(data["name"]), pc.MAX_NAME)
        self.assertEqual(len(data["org"]), pc.MAX_ORG)
        self.assertEqual(len(data["link"]), pc.MAX_LINK)
        self.assertEqual(len(data["message"]), pc.MAX_MESSAGE)


# ══════════════════════════════════════════════════════════════════════════
#  6. Reply-To is the enquirer, on every transport
# ══════════════════════════════════════════════════════════════════════════
class ReplyTo(Base):
    def test_the_enquiry_asks_for_the_sender_on_reply_to(self):
        self.submit()
        for msg in self.to_admins():
            self.assertEqual(msg["reply_to_override"], GOOD["email"])
            self.assertFalse(msg["is_bulk"], "an enquiry is not bulk mail")

    def test_the_acknowledgement_keeps_the_site_reply_address(self):
        self.submit()
        self.assertFalse(self.to_sender()[0].get("reply_to_override"))

    def test_the_mime_path_really_sets_it(self):
        msg, _ = nl_email._build_message(
            to_email="tim@example.com", subject="s", html_body="<p>h</p>",
            text_body="t", is_bulk=False, reply_to_override="alex@blueharbor.org")
        self.assertEqual(msg["Reply-To"], "alex@blueharbor.org")

    def test_no_override_keeps_the_default(self):
        msg, _ = nl_email._build_message(
            to_email="tim@example.com", subject="s", html_body="<p>h</p>",
            text_body="t", is_bulk=False)
        self.assertEqual(msg["Reply-To"], nl_email.reply_to())

    def test_all_four_http_providers_carry_it(self):
        # This is the one that matters in production: Render blocks outbound
        # SMTP, so an HTTPS provider is what actually sends.
        for provider in ("resend", "postmark", "brevo", "sendgrid"):
            payload = json.loads(nl_email._http_body(
                provider, to_email="tim@example.com", subject="s", html_body="h",
                text_body="t", headers={}, reply_to_addr="alex@blueharbor.org"))
            blob = json.dumps(payload)
            self.assertIn("alex@blueharbor.org", blob,
                          "%s payload dropped Reply-To" % provider)

    def test_a_newline_cannot_ride_in_on_the_override(self):
        msg, _ = nl_email._build_message(
            to_email="tim@example.com", subject="s", html_body="<p>h</p>",
            text_body="t", is_bulk=False,
            reply_to_override="a@b.com\r\nBcc: victim@example.com")
        self.assertNotIn("\n", msg["Reply-To"])
        self.assertNotIn("\r", msg["Reply-To"])


# ══════════════════════════════════════════════════════════════════════════
#  7. ONE list of partnership kinds
# ══════════════════════════════════════════════════════════════════════════
class KindsMatchTheWebsite(unittest.TestCase):
    def test_the_select_on_the_website_is_the_servers_list(self):
        with open(os.path.join(ROOT, "index.html"), encoding="utf-8") as fh:
            html = fh.read()
        block = re.search(r'<select id="pf-kind"[^>]*>(.*?)</select>', html, re.S)
        self.assertTrue(block, "the partnership-type <select> is gone from index.html")
        options = re.findall(r'<option value="([^"]+)"[^>]*>([^<]+)</option>',
                             block.group(1))
        self.assertEqual([(v, l.strip()) for v, l in options], pc.KINDS,
                         "index.html and partner_contact.KINDS have drifted apart")

    def test_every_kind_has_a_label(self):
        for value in pc.KIND_VALUES:
            self.assertTrue(pc.KIND_LABELS.get(value))


# ══════════════════════════════════════════════════════════════════════════
#  8. A send that fails says so
# ══════════════════════════════════════════════════════════════════════════
class Failures(Base):
    def test_one_bad_address_does_not_lose_the_message(self):
        self.fail_addresses = {self.ADMINS[0]}
        out = self.submit()
        self.assertTrue(out["ok"])
        self.assertEqual([m["to_email"] for m in self.to_admins()], [self.ADMINS[1]])

    def test_every_address_failing_is_reported_with_the_address_to_write_to(self):
        self.fail_addresses = set(self.ADMINS) | {GOOD["email"]}
        out = self.submit()
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "send_failed")
        self.assertIn(pc.public_inbox(), out["message"])

    def test_no_transport_refuses_rather_than_pretending(self):
        nl_email.transport = lambda: ""
        out = self.submit()
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "no_transport")
        self.assertIn(pc.public_inbox(), out["message"])
        self.assertEqual(self.sent, [])
        self.assertFalse(pc.enabled())

    def test_no_admin_address_is_also_off(self):
        nl_email.admin_emails = lambda: []
        self.assertFalse(pc.enabled())


# ══════════════════════════════════════════════════════════════════════════
#  8b. The address a stranger is told to write to
# ══════════════════════════════════════════════════════════════════════════
class PublicAddress(Base):
    """Mail is DELIVERED to every ADMIN_EMAIL, the first of which is a personal
    work address the website does not advertise. What a failure message hands
    back to a stranger has to be the address printed on the page instead."""

    def test_it_is_the_address_the_website_prints(self):
        with open(os.path.join(ROOT, "index.html"), encoding="utf-8") as fh:
            html = fh.read()
        shown = re.findall(r">\s*([\w.+-]+@[\w-]+\.[\w.]+)\s*<", html)
        self.assertIn(pc.public_inbox(), shown,
                      "the address the form falls back to is not printed on the page")

    def test_no_failure_message_leaks_a_private_admin_address(self):
        private = "timothy.honey@beardedsealstudios.com"
        nl_email.admin_emails = lambda: [private, pc.public_inbox()]
        nl_email.admin_email = lambda: private
        cases = []
        nl_email.transport = lambda: ""
        cases.append(self.submit())                       # no transport
        nl_email.transport = lambda: "http"
        self.fail_addresses = {private, pc.public_inbox(), GOOD["email"]}
        cases.append(self.submit())                       # every send failed
        self.fail_addresses = set()
        for _ in range(pc.PER_IP_LIMIT):
            pc.submit(dict(GOOD), "198.51.100.31")
        cases.append(pc.submit(dict(GOOD), "198.51.100.31"))   # rate limited
        for out in cases:
            self.assertFalse(out["ok"])
            self.assertNotIn(private, out.get("message", ""),
                             "a public error message must not hand out the private inbox")
            self.assertIn(pc.public_inbox(), out.get("message", ""))


# ══════════════════════════════════════════════════════════════════════════
#  9. The HTTP surface
# ══════════════════════════════════════════════════════════════════════════
class Http(Base):
    def post(self, path, body, ip="203.0.113.7"):
        h = FakeHandler(ip)
        handled = pc.handle_post(h, Parsed(path), body)
        return handled, h

    def test_other_paths_are_not_ours(self):
        handled, _ = self.post("/api/redeem/code", {})
        self.assertFalse(handled)
        self.assertFalse(pc.handle_get(FakeHandler(), Parsed("/api/stats")))

    def test_a_good_submission_is_202_shaped_ok(self):
        handled, h = self.post("/api/partner/contact", dict(GOOD))
        self.assertTrue(handled)
        self.assertEqual(h.status, 200)
        self.assertTrue(h.payload["ok"])

    def test_a_bad_submission_is_400(self):
        handled, h = self.post("/api/partner/contact", dict(GOOD, email="junk"))
        self.assertTrue(handled)
        self.assertEqual(h.status, 400)

    def test_rate_limited_is_429(self):
        for _ in range(pc.PER_IP_LIMIT):
            self.post("/api/partner/contact", dict(GOOD), "198.51.100.77")
        _, h = self.post("/api/partner/contact", dict(GOOD), "198.51.100.77")
        self.assertEqual(h.status, 429)

    def test_a_dead_transport_is_503(self):
        nl_email.transport = lambda: ""
        _, h = self.post("/api/partner/contact", dict(GOOD))
        self.assertEqual(h.status, 503)

    def test_an_empty_body_is_refused_not_crashed(self):
        handled, h = self.post("/api/partner/contact", {})
        self.assertTrue(handled)
        self.assertEqual(h.status, 400)
        self.assertFalse(h.payload["ok"])

    def test_state_is_answerable_from_outside(self):
        h = FakeHandler()
        self.assertTrue(pc.handle_get(h, Parsed("/api/partner/state")))
        self.assertTrue(h.payload["enabled"])
        self.assertEqual(h.payload["inboxes"], len(self.ADMINS))

    def test_state_never_names_the_inboxes(self):
        h = FakeHandler()
        pc.handle_get(h, Parsed("/api/partner/state"))
        self.assertNotIn(self.ADMINS[0], json.dumps(h.payload),
                         "the state endpoint is public; it counts inboxes, it does "
                         "not publish an address list to scrape")

    def test_state_says_off_when_it_cannot_send(self):
        nl_email.transport = lambda: ""
        h = FakeHandler()
        pc.handle_get(h, Parsed("/api/partner/state"))
        self.assertFalse(h.payload["enabled"])


# ══════════════════════════════════════════════════════════════════════════
#  10. What actually lands in the inbox
# ══════════════════════════════════════════════════════════════════════════
class TheEmail(Base):
    def test_the_subject_names_the_kind_and_who_sent_it(self):
        self.submit()
        subject = self.to_admins()[0]["subject"]
        self.assertIn("Ocean conservation partnership", subject)
        self.assertIn(GOOD["org"], subject)

    def test_the_subject_falls_back_to_the_person_with_no_organisation(self):
        self.submit(org="")
        self.assertIn(GOOD["name"], self.to_admins()[0]["subject"])

    def test_every_field_is_in_the_message(self):
        self.submit()
        msg = self.to_admins()[0]
        for part in (GOOD["name"], GOOD["org"], GOOD["email"], GOOD["link"],
                     GOOD["message"]):
            self.assertIn(part, msg["text_body"])
            self.assertIn(part.replace("&", "&amp;"), msg["html_body"])

    def test_an_empty_optional_field_leaves_no_empty_row(self):
        self.submit(org="", link="")
        msg = self.to_admins()[0]
        self.assertNotIn("Organisation:", msg["text_body"])
        self.assertNotIn("Website or link", msg["html_body"])

    def test_it_says_where_a_reply_goes(self):
        self.submit()
        self.assertIn(GOOD["email"], self.to_admins()[0]["text_body"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
