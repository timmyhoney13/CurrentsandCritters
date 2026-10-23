#!/usr/bin/env node
/* The Partner With Us mail endpoint, api/partner-contact.js.
 *
 * Run:  node test_partner_contact_api.js        (no browser, no network)
 *
 * This is the half of the form that holds a secret and sends mail, so it is
 * the half worth being paranoid about. The provider API is stubbed, so nothing
 * here leaves the machine and no inbox receives anything; what is checked is
 * the shape of what WOULD go on the wire.
 *
 *   1. THE KINDS MATCH THE PAGE, and match partner_contact.py, character for
 *      character. Three copies of one list is two chances to drift, and a kind
 *      this file does not know arrives labelled "Partnership" or is refused.
 *   2. NOTHING IS SENT TO AN ADDRESS THE CALLER CHOSE. The recipient comes
 *      from ADMIN_EMAIL and nowhere else, whatever the request body says.
 *   3. THE ACKNOWLEDGEMENT QUOTES NOTHING. A form that mails caller text to a
 *      caller-chosen address is a spam relay; the ack body is a constant.
 *   4. THE REFUSALS are the same ones the page makes, including the template's
 *      own blanks, because the page's copy is a convenience and this one is
 *      the authority.
 *   5. HEADERS CANNOT BE INJECTED: a newline in a name or an address does not
 *      reach the provider.
 *   6. EVERY PROVIDER'S PAYLOAD is the shape that provider documents, with the
 *      key presented the way it wants it.
 *   7. A FAILURE IS REPORTED, NOT SWALLOWED, and names the public inbox, so
 *      the page can offer the mailto: with the message still in it.
 */
"use strict";

const fs = require("fs");
const path = require("path");

const ROOT = __dirname;
const handler = require(path.join(ROOT, "api", "partner-contact.js"));
const T = handler.__test;

const lines = [];
const ok = (cond, label) => lines.push((cond ? "PASS " : "FAIL ") + label);

/* ── the request / response doubles ───────────────────────────────────── */
function call(opts = {}) {
  const req = {
    method: opts.method || "POST",
    headers: Object.assign({ "x-forwarded-for": opts.ip || "203.0.113.9" }, opts.headers),
    body: "body" in opts ? opts.body : {},
    socket: { remoteAddress: "127.0.0.1" },
  };
  const res = {
    statusCode: 0, headers: {}, payload: "",
    setHeader(k, v) { this.headers[k.toLowerCase()] = v; },
    end(text) { this.payload = text || ""; },
  };
  return handler(req, res).then(() => ({
    status: res.statusCode,
    headers: res.headers,
    json: (() => { try { return JSON.parse(res.payload); } catch (_) { return null; } })(),
  }));
}

const GOOD = {
  name: "Alex Rivera",
  org: "Blue Harbor Aquarium",
  email: "Alex@BlueHarbor.org",
  link: "blueharbor.org",
  kind: "conservation",
  message: "We run a coastal education program and would love to work together this summer.",
  company_site: "",
};

/* ── the provider stub ────────────────────────────────────────────────── */
let calls = [];
function stubFetch({ status = 200, failFor = null } = {}) {
  calls = [];
  global.fetch = async (url, init) => {
    const body = JSON.parse(init.body);
    calls.push({ url, headers: init.headers, body });
    const recipient = JSON.stringify(body);
    const failing = failFor && recipient.includes(failFor);
    const code = failing ? 500 : status;
    return {
      ok: code >= 200 && code < 300,
      status: code,
      text: async () => (code >= 300 ? '{"error":"stubbed refusal"}' : ""),
    };
  };
}

const BASE_ENV = {
  NEWSLETTER_API_KEY: "re_test_key_do_not_use",
  NEWSLETTER_HTTP_PROVIDER: "",
  RESEND_API_KEY: "", POSTMARK_API_KEY: "", BREVO_API_KEY: "", SENDGRID_API_KEY: "",
  ADMIN_EMAIL: "timothy.honey@beardedsealstudios.com",
  NEWSLETTER_FROM_EMAIL: "timothy.honey@beardedsealstudios.com",
  NEWSLETTER_FROM_NAME: "Currents and Critters",
  PARTNER_PUBLIC_EMAIL: "", POSTMARK_STREAM: "",
};

function setEnv(extra = {}) {
  Object.assign(process.env, BASE_ENV, extra);
  T.resetRateLimits();
  stubFetch();
}

/* ══════════════════════════════════════════════════════════════════════
   1. The three copies of the partnership kinds are one list
   ══════════════════════════════════════════════════════════════════════ */
const INDEX = fs.readFileSync(path.join(ROOT, "index.html"), "utf8");
const selectBlock = (INDEX.match(/<select id="pf-kind"[\s\S]*?<\/select>/) || [""])[0];
const pageKinds = [...selectBlock.matchAll(/<option value="([^"]+)">([^<]+)<\/option>/g)]
  .map(m => [m[1], m[2].trim()]);
ok(pageKinds.length === 8, "index.html still offers eight partnership kinds (" + pageKinds.length + ")");
ok(JSON.stringify(pageKinds) === JSON.stringify(T.KINDS),
   "the kinds here are the page's kinds, value and label");

const PY = fs.readFileSync(path.join(ROOT, "partner_contact.py"), "utf8");
const pyBlock = (PY.match(/KINDS: List\[Tuple\[str, str\]\] = \[[\s\S]*?\n\]/) || [""])[0];
const pyKinds = [...pyBlock.matchAll(/\("([^"]+)",\s*"([^"]+)"\)/g)].map(m => [m[1], m[2]]);
ok(JSON.stringify(pyKinds) === JSON.stringify(T.KINDS),
   "...and partner_contact.py's kinds, so the two senders cannot disagree");

/* ══════════════════════════════════════════════════════════════════════
   The scenarios
   ══════════════════════════════════════════════════════════════════════ */
(async () => {
  /* ── state, and what it does not leak ───────────────────────────────── */
  setEnv({ NEWSLETTER_API_KEY: "" });
  let r = await call({ method: "GET" });
  ok(r.status === 200 && r.json && r.json.enabled === false,
     "GET says the form is NOT wired up when no key is set");

  setEnv();
  r = await call({ method: "GET" });
  ok(r.json && r.json.enabled === true && r.json.provider === "Resend",
     "GET names the provider a bare NEWSLETTER_API_KEY implies: " + (r.json && r.json.provider));
  ok(!r.payload && !JSON.stringify(r.json).includes("re_test_key"),
     "...and never the key itself");
  ok(r.headers["cache-control"] === "no-store", "...and is not cached");

  setEnv({ NEWSLETTER_API_KEY: "", BREVO_API_KEY: "xkeysib-test" });
  r = await call({ method: "GET" });
  ok(r.json && r.json.provider === "Brevo",
     "a provider-named key alone picks that provider: " + (r.json && r.json.provider));

  /* ── the happy path ─────────────────────────────────────────────────── */
  setEnv();
  r = await call({ body: GOOD });
  ok(r.status === 200 && r.json && r.json.ok === true, "a good enquiry is accepted: " + r.status);
  ok(calls.length === 2, "two messages go out: the enquiry and the acknowledgement (" + calls.length + ")");

  const enquiry = calls[0], ack = calls[1];
  ok(enquiry.url === "https://api.resend.com/emails", "the enquiry goes to Resend: " + enquiry.url);
  ok(enquiry.headers.Authorization === "Bearer re_test_key_do_not_use",
     "...with the key as a bearer token");
  ok(JSON.stringify(enquiry.body.to) === JSON.stringify(["timothy.honey@beardedsealstudios.com"]),
     "...to ADMIN_EMAIL: " + JSON.stringify(enquiry.body.to));
  ok(enquiry.body.reply_to === "alex@blueharbor.org",
     "...with the sender on Reply-To, so answering it needs no copy and paste");
  ok(enquiry.body.from === "Currents and Critters <timothy.honey@beardedsealstudios.com>",
     "...from the site's own verified address: " + enquiry.body.from);
  ok(/Ocean conservation partnership/.test(enquiry.body.subject) &&
     /Blue Harbor Aquarium/.test(enquiry.body.subject),
     "the subject says what it is and who sent it: " + enquiry.body.subject);
  ok(enquiry.body.text.includes("coastal education program") &&
     enquiry.body.html.includes("coastal education program"),
     "both bodies carry the message");
  ok(enquiry.body.text.includes("alex@blueharbor.org"),
     "the address is in the body too, for a client that drops Reply-To");

  /* ── the acknowledgement is a constant ──────────────────────────────── */
  ok(JSON.stringify(ack.body.to) === JSON.stringify(["alex@blueharbor.org"]),
     "the acknowledgement goes to the person who wrote in");
  ok(ack.body.text === T.ACK_TEXT && ack.body.html === T.ACK_HTML,
     "...and its body is the constant, byte for byte");
  ok(!ack.body.text.includes("Alex Rivera") && !ack.body.html.includes("Alex Rivera") &&
     !ack.body.text.includes("coastal") && !ack.body.html.includes("coastal"),
     "...quoting NOTHING the caller typed, so this is not a way to mail a stranger text");

  /* ── the recipient is never in the request ──────────────────────────── */
  setEnv();
  r = await call({ body: Object.assign({}, GOOD, {
    to: "victim@example.com", recipient: "victim@example.com",
    admin_email: "victim@example.com", ADMIN_EMAIL: "victim@example.com",
  }) });
  const everything = JSON.stringify(calls);
  ok(r.json.ok === true && !everything.includes("victim@example.com"),
     "a request that names its own recipient is mailed to ADMIN_EMAIL anyway");

  /* ── header injection ───────────────────────────────────────────────── */
  setEnv();
  await call({ body: Object.assign({}, GOOD, {
    name: "Alex\r\nBcc: victim@example.com",
    org: "Blue\nHarbor",
  }) });
  ok(!calls[0].body.subject.includes("\n") && !calls[0].body.subject.includes("\r"),
     "a newline in a name never reaches the subject line");
  ok(!JSON.stringify(calls[0].body.subject).includes("Bcc"),
     "...and neither does the header it was trying to smuggle in");

  setEnv();
  r = await call({ body: Object.assign({}, GOOD, { email: "alex@blueharbor.org\nBcc: x@y.com" }) });
  ok(r.status === 400 && calls.length === 0,
     "an address with a newline in it is refused outright, not cleaned up");

  /* ── the refusals, same list as the page's ──────────────────────────── */
  const refusals = [
    ["no name", { name: "" }, /name/i],
    ["a junk address", { email: "not-an-address" }, /email/i],
    ["a kind the server does not know", { kind: "sponsorship-ish" }, /kind of partnership/i],
    ["an empty message", { message: "" }, /message/i],
    ["the template's own ____ blanks", { message: "Hi Timothy,\n\nI'm ____ from ____ and we want ____." }, /blank/i],
    ["the over-$100 template's [INSERT ...]", { message: "Hi Timothy, I'd like to give [INSERT AMOUNT HERE] to this." }, /INSERT/],
    ["a two-word message", { message: "Hi there" }, /little more/i],
  ];
  for (const [label, override, expect] of refusals) {
    setEnv();
    r = await call({ body: Object.assign({}, GOOD, override) });
    ok(r.status === 400 && calls.length === 0 && expect.test(r.json.message || ""),
       label + " is refused and nothing is sent: " + (r.json && r.json.message));
  }

  /* ── the honeypot ───────────────────────────────────────────────────── */
  setEnv();
  r = await call({ body: Object.assign({}, GOOD, { company_site: "http://spam.example" }) });
  ok(r.status === 200 && r.json.ok === true && r.json.message === T.SENT_MESSAGE,
     "a filled honeypot is told exactly what a person is told");
  ok(calls.length === 0, "...and nothing is sent, so the bot cannot tell the difference");

  /* ── every provider's payload shape ─────────────────────────────────── */
  const shapes = {
    resend: c => c.headers.Authorization === "Bearer k" &&
                 Array.isArray(c.body.to) && !!c.body.html && !!c.body.reply_to,
    postmark: c => c.headers["X-Postmark-Server-Token"] === "k" &&
                   c.body.To === "timothy.honey@beardedsealstudios.com" &&
                   !!c.body.HtmlBody && c.body.MessageStream === "outbound" &&
                   Array.isArray(c.body.Headers),
    brevo: c => c.headers["api-key"] === "k" &&
                c.body.sender.email === "timothy.honey@beardedsealstudios.com" &&
                c.body.to[0].email === "timothy.honey@beardedsealstudios.com" &&
                !!c.body.htmlContent && c.body.replyTo.email === "alex@blueharbor.org",
    sendgrid: c => c.headers.Authorization === "Bearer k" &&
                   c.body.personalizations[0].to[0].email === "timothy.honey@beardedsealstudios.com" &&
                   c.body.content.length === 2 && !!c.body.reply_to.email,
  };
  for (const [name, shaped] of Object.entries(shapes)) {
    setEnv({ NEWSLETTER_API_KEY: "k", NEWSLETTER_HTTP_PROVIDER: name });
    r = await call({ body: GOOD });
    ok(r.json.ok === true && calls.length === 2 && shaped(calls[0]),
       name + ": the payload and the key are the shape that provider documents");
    ok(calls[0].url === T.PROVIDERS[name].url, "..." + name + " is posted to " + calls[0].url);
  }

  /* ── several inboxes, one of them broken ────────────────────────────── */
  setEnv({ ADMIN_EMAIL: "tim@beardedsealstudios.com, broken@beardedsealstudios.com" });
  stubFetch({ failFor: "broken@beardedsealstudios.com" });
  r = await call({ body: GOOD });
  ok(r.json.ok === true, "an enquiry that reached one of two inboxes counts as sent");
  ok(calls.some(c => JSON.stringify(c.body.to).includes("tim@beardedsealstudios.com")),
     "...and the working inbox did get it");

  /* ── nothing configured, and a provider that refuses ────────────────── */
  setEnv({ NEWSLETTER_API_KEY: "", PARTNER_PUBLIC_EMAIL: "" });
  r = await call({ body: GOOD });
  ok(r.status === 503 && r.json.error === "no_transport" && calls.length === 0,
     "with no key set the form says it cannot send rather than pretending: " + r.status);
  ok(/currentsandcritters@gmail\.com/.test(r.json.message),
     "...and names the inbox printed on the page: " + r.json.message);

  setEnv();
  stubFetch({ status: 500 });
  r = await call({ body: GOOD });
  ok(r.status === 503 && r.json.error === "send_failed",
     "a provider that refuses everything is reported, not swallowed: " + r.status);
  ok(/currentsandcritters@gmail\.com/.test(r.json.message) && /stubbed refusal/.test(r.json.detail || ""),
     "...with somewhere to write instead, and the provider's own complaint kept for the log");

  /* ── rate limiting ──────────────────────────────────────────────────── */
  setEnv();
  const statuses = [];
  for (let i = 0; i < 4; i++) {
    statuses.push((await call({ body: GOOD, ip: "198.51.100.77" })).status);
  }
  ok(JSON.stringify(statuses) === JSON.stringify([200, 200, 200, 429]),
     "the fourth enquiry in fifteen minutes from one address is held: " + statuses.join(","));
  setEnv();
  ok((await call({ body: GOOD, ip: "198.51.100.78" })).status === 200,
     "...and it was that address, not everyone");

  /* ── the plumbing ───────────────────────────────────────────────────── */
  setEnv();
  r = await call({ method: "PUT", body: GOOD });
  ok(r.status === 405 && calls.length === 0, "PUT is refused: " + r.status);
  setEnv();
  r = await call({ body: "this is not json" });
  ok(r.status === 400 && calls.length === 0, "a body that is not JSON is refused: " + r.status);
  setEnv();
  r = await call({ body: undefined });
  ok(r.status === 400 || r.status === 503 || r.status === 200,
     "a missing body does not throw (" + r.status + ")");

  console.log(lines.join("\n"));
  const failed = lines.filter(l => l.startsWith("FAIL"));
  console.log("\n" + lines.filter(l => l.startsWith("PASS")).length +
              " passed, " + failed.length + " failed");
  process.exit(failed.length ? 1 : 0);
})().catch(err => {
  console.log(lines.join("\n"));
  console.error("\nthe suite itself threw: " + (err && err.stack || err));
  process.exit(1);
});
