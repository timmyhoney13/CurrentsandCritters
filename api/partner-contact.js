/* Currents and Critters: the "Partner With Us" enquiry form, sent from Vercel.
 *
 * WHY THIS EXISTS WHEN partner_contact.py ALREADY DID THE JOB
 * The form used to POST to play.currentsandcritters.com, because the game
 * server was the only thing in the system with a mail transport. That server
 * is on Render's free tier and HIBERNATES: the first request after a quiet
 * spell pays a 30-60 second cold start, and a sleeping instance sometimes
 * never answers at all. So the one message on the whole website that is worth
 * real money - somebody offering to stock the game, sponsor it, or contribute
 * over $100 - was the one message that had to sit through a spinner, read
 * "the game server may be waking up", and fall back to a mailto: if the wake
 * never finished.
 *
 * This page is served by Vercel, and a Vercel Function is a few hundred
 * milliseconds cold. It talks to the SAME email provider the newsletter uses,
 * sends from the same address and delivers to the same inbox, so nothing about
 * the mail changes - only which host puts it on the wire. The game server is
 * not involved, and is not woken.
 *
 * WHAT IT NEEDS IN THE VERCEL DASHBOARD (Settings -> Environment Variables)
 *   NEWSLETTER_API_KEY         the provider key, the same value Render has
 *   NEWSLETTER_HTTP_PROVIDER   brevo | resend | postmark | sendgrid
 *                              (optional; defaults to resend, as in Python)
 *   ADMIN_EMAIL                optional, where enquiries land. Defaults to
 *                              timothy.honey@beardedsealstudios.com, exactly
 *                              as newsletter_email.py does.
 *   NEWSLETTER_FROM_EMAIL      optional, the verified From address.
 * With no key set, this answers 503 with a message naming the public inbox,
 * and the page falls back to a prefilled mailto: with everything typed still
 * in it. A form that cannot send must say so, never swallow a message.
 *
 * THIS IS A DELIBERATE SECOND COPY of partner_contact.py's rules, in the one
 * language Vercel runs. The parts that MUST NOT drift are pinned by tests:
 * test_partner_contact_api.js compares KINDS here against the <select> in
 * index.html and against KINDS in partner_contact.py, character for
 * character, because a kind this file does not know arrives mislabelled.
 *
 * THE ABUSE SURFACE, AND WHAT IS DONE ABOUT IT (same four rules as Python)
 *   1. THE RECIPIENT IS NEVER IN THE REQUEST. Enquiries go to ADMIN_EMAIL and
 *      nowhere else; nothing a caller sends can redirect a message.
 *   2. THE ACKNOWLEDGEMENT CARRIES NO CALLER TEXT. Its body is a CONSTANT. If
 *      it quoted their name or message, this form would be a way to mail
 *      arbitrary text to an arbitrary stranger.
 *   3. RATE LIMITED PER IP AND GLOBALLY. Best effort only here: a Function
 *      instance is per-region and short-lived, so a warm instance catches one
 *      person hammering it and a cold one starts its window again. It is a
 *      speed bump, and is not sold as more than that.
 *   4. EVERY FIELD IS LENGTH-CAPPED AND HEADER-SAFE. Newlines are stripped
 *      from everything that reaches a header, and every value is HTML-escaped
 *      into the message body.
 *
 *   GET  /api/partner-contact   -> {ok, enabled, provider, inboxes}
 *   POST /api/partner-contact   {name, org, email, link, kind, message}
 */
"use strict";

// ═══════════════════════════════════════════════════════════════════════════
//  THE FORM'S SHAPE  (mirror of KINDS in partner_contact.py)
// ═══════════════════════════════════════════════════════════════════════════
const KINDS = [
  ["brand",        "Brand or product collaboration"],
  ["retail",       "Retail, distribution or wholesale"],
  ["conservation", "Ocean conservation partnership"],
  ["creator",      "Creator, press or content"],
  ["sponsor",      "Sponsorship or donation"],
  ["major",        "Supporter contribution over $100"],
  ["event",        "Event, convention or school"],
  ["other",        "Something else"],
];
const KIND_LABELS = new Map(KINDS);

const MAX_NAME = 80;
const MAX_ORG = 120;
const MAX_LINK = 200;
const MAX_MESSAGE = 4000;
const MIN_MESSAGE = 25;
const MAX_EMAIL_LEN = 254;
const MAX_REQUEST_BYTES = 64 * 1024;

// The template on the page writes its blanks as runs of underscores, and the
// over-$100 one shouts them instead. Sending either back unfilled is not an
// enquiry, it is the template, so it is refused with an error that says which
// to fill rather than mailed to somebody who cannot reply to it.
const BLANK_RE = /_{3,}/;
const INSERT_RE = /\[INSERT/i;

// The address PRINTED on the page, which is not the address an enquiry is
// DELIVERED to. When this cannot send it has to tell a stranger where to write
// instead, and that has to be the address the site advertises.
const PUBLIC_INBOX_DEFAULT = "currentsandcritters@gmail.com";
const DEFAULT_ADMIN_EMAIL = "timothy.honey@beardedsealstudios.com";
const BRAND_NAME = "Currents and Critters";

function env(name, fallback = "") {
  const raw = process.env[name];
  return (typeof raw === "string" ? raw.trim() : "") || fallback;
}

const publicInbox = () => env("PARTNER_PUBLIC_EMAIL", PUBLIC_INBOX_DEFAULT);
const senderEmail = () =>
  env("NEWSLETTER_FROM_EMAIL") || env("GMAIL_SENDER_EMAIL", DEFAULT_ADMIN_EMAIL);
const senderName = () =>
  env("NEWSLETTER_FROM_NAME") || env("GMAIL_SENDER_NAME", BRAND_NAME);

/* ADMIN_EMAIL may list several accounts, separated by comma / pipe / space,
   same as newsletter_email.admin_emails(). The first is the primary. */
function adminEmails() {
  const out = [];
  for (const part of env("ADMIN_EMAIL", DEFAULT_ADMIN_EMAIL).split(/[,|;\s]+/)) {
    const clean = normalizeEmail(part);
    if (clean && !out.includes(clean)) out.push(clean);
  }
  return out.length ? out : [DEFAULT_ADMIN_EMAIL];
}

// ═══════════════════════════════════════════════════════════════════════════
//  PROVIDERS  (mirror of HTTP_PROVIDERS in newsletter_email.py)
// ═══════════════════════════════════════════════════════════════════════════
const PROVIDERS = {
  resend: {
    label: "Resend",
    url: "https://api.resend.com/emails",
    headers: key => ({ "Content-Type": "application/json", Authorization: "Bearer " + key }),
    body: m => ({
      from: m.from, to: [m.to], subject: m.subject,
      html: m.html, text: m.text, reply_to: m.replyTo, headers: m.extraHeaders,
    }),
  },
  postmark: {
    label: "Postmark",
    url: "https://api.postmarkapp.com/email",
    headers: key => ({ "Content-Type": "application/json", "X-Postmark-Server-Token": key }),
    // An enquiry is transactional, so it goes down the transactional stream.
    // The newsletter's POSTMARK_STREAM default ("broadcast") is wrong for a
    // one-to-one message: Postmark throttles broadcast streams differently and
    // asks that they carry bulk mail only.
    body: m => ({
      From: m.from, To: m.to, Subject: m.subject,
      HtmlBody: m.html, TextBody: m.text, ReplyTo: m.replyTo,
      MessageStream: env("POSTMARK_STREAM", "outbound"),
      Headers: Object.entries(m.extraHeaders).map(([Name, Value]) => ({ Name, Value })),
    }),
  },
  brevo: {
    label: "Brevo",
    url: "https://api.brevo.com/v3/smtp/email",
    headers: key => ({ "Content-Type": "application/json", "api-key": key }),
    body: m => ({
      sender: { email: m.fromEmail, name: m.fromName },
      to: [{ email: m.to }], subject: m.subject,
      htmlContent: m.html, textContent: m.text,
      replyTo: { email: m.replyTo }, headers: m.extraHeaders,
    }),
  },
  sendgrid: {
    label: "SendGrid",
    url: "https://api.sendgrid.com/v3/mail/send",
    headers: key => ({ "Content-Type": "application/json", Authorization: "Bearer " + key }),
    body: m => ({
      personalizations: [{ to: [{ email: m.to }] }],
      from: { email: m.fromEmail, name: m.fromName },
      reply_to: { email: m.replyTo }, subject: m.subject,
      content: [{ type: "text/plain", value: m.text }, { type: "text/html", value: m.html }],
      headers: m.extraHeaders,
    }),
  },
};

/* Which provider is configured, or "". Same precedence as
   newsletter_email.http_provider(): an explicitly named one wins, a bare
   NEWSLETTER_API_KEY means Resend, and a provider-named key alone is enough to
   pick that provider. */
function providerName() {
  const named = env("NEWSLETTER_HTTP_PROVIDER").toLowerCase();
  if (PROVIDERS[named] && apiKeyFor(named)) return named;
  if (env("NEWSLETTER_API_KEY")) return "resend";
  for (const name of Object.keys(PROVIDERS)) {
    if (env(name.toUpperCase() + "_API_KEY")) return name;
  }
  return "";
}

function apiKeyFor(provider) {
  return env("NEWSLETTER_API_KEY") || env(provider.toUpperCase() + "_API_KEY");
}

function enabled() {
  return Boolean(providerName()) && adminEmails().length > 0;
}

// ═══════════════════════════════════════════════════════════════════════════
//  VALIDATION
// ═══════════════════════════════════════════════════════════════════════════
/* A single line, safe to drop into a header: no CR, no LF, no control
   characters, collapsed whitespace, capped. */
function oneLine(value, limit) {
  const text = String(value === undefined || value === null ? "" : value)
    .replace(/[\u0000-\u001f\u007f]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  return text.slice(0, limit);
}

/* The message body: newlines survive (it is prose), everything else that could
   confuse a mail client does not. Runs of blank lines are collapsed so a
   pasted template does not arrive three screens tall. */
function manyLines(value, limit) {
  const text = String(value === undefined || value === null ? "" : value)
    .replace(/\r\n?/g, "\n")
    .replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g, " ")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
  return text.slice(0, limit);
}

/* Deliberately stricter than the RFC, and the same expression as
   newsletter_email.normalize_email(): no quoted local parts, no IP-literal
   domains, no unicode. A newline or a comma in an address is a header
   injection attempt, full stop. */
const EMAIL_RE = new RegExp(
  "^[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+" +
  "(?:\\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*" +
  "@" +
  "(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\\.)+" +
  "[A-Za-z]{2,63}$"
);

function normalizeEmail(raw) {
  let s = String(raw === undefined || raw === null ? "" : raw).trim();
  const wrapped = s.match(/^.*<([^<>]+)>$/);      // "Name <addr>" pasted in
  if (wrapped) s = wrapped[1].trim();
  if (!s || s.length > MAX_EMAIL_LEN) return "";
  if (/[\r\n\t,;]/.test(s)) return "";
  if (!EMAIL_RE.test(s)) return "";
  return s.toLowerCase();
}

/* [clean fields, error message]. An empty error means it is sendable. The
   browser makes these same checks first, so a mistake costs a glance rather
   than a round trip; this copy is the authority, not the convenience. */
function validate(body) {
  const data = {
    name: oneLine(body.name, MAX_NAME),
    org: oneLine(body.org, MAX_ORG),
    link: oneLine(body.link, MAX_LINK),
    kind: oneLine(body.kind, 40).toLowerCase(),
    email: normalizeEmail(body.email),
    message: manyLines(body.message, MAX_MESSAGE),
  };

  // The honeypot. A real person never sees this field (it is off-screen and
  // aria-hidden), so anything in it is a bot filling every input on the page.
  // It is answered with a normal-looking success by the caller, never an
  // error: telling a bot which check it failed is telling it how to pass.
  if (oneLine(body.company_site, 200)) return [data, "honeypot"];

  if (data.name.length < 2) return [data, "Please tell us your name."];
  if (!data.email) return [data, "That email address doesn't look right."];
  if (!KIND_LABELS.has(data.kind)) return [data, "Pick what kind of partnership this is."];
  if (!data.message) return [data, "Please write a message."];
  if (BLANK_RE.test(data.message)) {
    return [data, "The template still has blanks in it. Fill in the ____ parts " +
                  "(or delete them) and send again."];
  }
  if (INSERT_RE.test(data.message)) {
    return [data, "The template still has [INSERT ...] parts in it. Replace them " +
                  "with your own words and send again."];
  }
  if (data.message.length < MIN_MESSAGE) {
    return [data, "Tell us a little more, so there is something to reply to."];
  }
  return [data, ""];
}

// ═══════════════════════════════════════════════════════════════════════════
//  THE MESSAGES
// ═══════════════════════════════════════════════════════════════════════════
function esc(value) {
  return String(value === undefined || value === null ? "" : value)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#x27;");
}

/* The address line. A name carrying a comma, a quote or an angle bracket is
   quoted rather than pasted raw, which is what keeps it one address instead of
   two. (email.utils.formataddr does this on the Python side.) */
function addressLine(name, address) {
  const clean = oneLine(name, 120).replace(/["\\]/g, "");
  if (!clean) return address;
  return (/[(),.:;<>@\[\]]/.test(clean) ? '"' + clean + '"' : clean) + " <" + address + ">";
}

/* [subject, html, text] for the email that reaches the inbox. The subject
   names the kind and who sent it, so a full inbox can be sorted without
   opening anything. Every value is escaped: this message is assembled from
   text a stranger typed. */
function buildEnquiry(data) {
  const kind = KIND_LABELS.get(data.kind) || "Partnership";
  const who = data.org || data.name;
  const subject = oneLine("Partner enquiry (" + kind + "): " + who, 160);

  const rows = [
    ["Name", data.name],
    ["Organisation", data.org],
    ["Email", data.email],
    ["Website or link", data.link],
    ["Partnership type", kind],
  ].filter(([, value]) => value);

  const pad = label => (label + ":").padEnd(16, " ");
  const rule = "-".repeat(52);
  const text =
    rows.map(([label, value]) => pad(label) + " " + value).join("\n") +
    "\n\n" + rule + "\n\n" + data.message + "\n\n" + rule + "\n" +
    "Sent from the Partner With Us form on currentsandcritters.com.\n" +
    "Reply to this email and it goes straight to " + data.email + ".\n";

  const rowHtml = rows.map(([label, value]) =>
    '<tr><td style="padding:4px 14px 4px 0;color:#5a7b88;white-space:nowrap;' +
    'vertical-align:top">' + esc(label) + '</td><td style="padding:4px 0;color:#12303d">' +
    esc(value) + "</td></tr>").join("");
  const html =
    '<div style="font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;' +
    'font-size:15px;line-height:1.55;color:#12303d;max-width:560px">' +
    '<p style="margin:0 0 14px;font-size:13px;color:#5a7b88">' +
    'New enquiry from the Partner With Us form.</p>' +
    '<table style="border-collapse:collapse;font-size:14px;margin-bottom:16px">' +
    rowHtml + "</table>" +
    '<div style="white-space:pre-wrap;background:#eef7fb;border:1px solid #cfe6f0;' +
    'border-radius:10px;padding:14px 16px">' + esc(data.message) + "</div>" +
    '<p style="color:#5a7b88;font-size:13px;margin-top:16px">' +
    'Reply to this email and it goes straight to ' +
    '<a href="mailto:' + esc(data.email) + '">' + esc(data.email) + "</a>.</p></div>";

  return [subject, html, text];
}

// The acknowledgement is a CONSTANT. See rule 2 at the top of the file: the
// moment it quotes anything the sender typed, this form becomes a way to mail
// a stranger arbitrary text.
const ACK_SUBJECT = "Thanks for getting in touch with Currents and Critters";
const ACK_TEXT =
  "Thanks for reaching out about partnering with Currents and Critters.\n\n" +
  "Your message is in, and a real person (Tim) reads every one of these. " +
  "You will get a reply at this address, usually within a few days.\n\n" +
  "In the meantime the game is free to play at " +
  "https://play.currentsandcritters.com.\n\n" +
  "- Currents and Critters\n";
const ACK_HTML =
  '<div style="font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;' +
  'font-size:15px;line-height:1.55;color:#12303d;max-width:520px">' +
  '<p>Thanks for reaching out about partnering with Currents and Critters.</p>' +
  '<p>Your message is in, and a real person (Tim) reads every one of these. ' +
  'You will get a reply at this address, usually within a few days.</p>' +
  '<p>In the meantime the game is free to play at ' +
  '<a href="https://play.currentsandcritters.com">play.currentsandcritters.com</a>.</p>' +
  '<p style="color:#5a7b88;font-size:13px">- Currents and Critters</p></div>';

// ═══════════════════════════════════════════════════════════════════════════
//  SENDING
// ═══════════════════════════════════════════════════════════════════════════
const SENT_MESSAGE =
  "Thanks! Your message is on its way to Timothy, and a copy of the " +
  "confirmation is in your inbox. You'll get a reply at the address you gave.";

/* One message to one recipient over the provider's HTTPS API. Throws with the
   provider's own complaint attached: a 401 here means the key or the From
   address, and that distinction is the whole of debugging mail. */
async function sendEmail({ to, subject, html, text, replyTo, timeoutMs = 9000 }) {
  const name = providerName();
  if (!name) throw new Error("No email API is configured (set NEWSLETTER_API_KEY).");
  const spec = PROVIDERS[name];
  const fromEmail = senderEmail();
  const fromName = senderName();

  const payload = spec.body({
    to,
    from: addressLine(fromName, fromEmail),
    fromEmail,
    fromName,
    subject: oneLine(subject, 900),
    html,
    text,
    replyTo: normalizeEmail(replyTo) || fromEmail,
    // Unique per message, so a mail client that collapses similar subjects
    // does not thread two unrelated enquiries together.
    extraHeaders: { "X-Entity-Ref-ID": randomRef() },
  });

  const res = await fetch(spec.url, {
    method: "POST",
    headers: spec.headers(apiKeyFor(name)),
    body: JSON.stringify(payload),
    signal: AbortSignal.timeout(timeoutMs),
  });
  if (res.ok) return true;

  let detail = "";
  try { detail = (await res.text()).slice(0, 300); } catch (_) { /* nothing to add */ }
  throw new Error(spec.label + " refused the message (" + res.status + "): " + detail);
}

function randomRef() {
  if (globalThis.crypto && typeof globalThis.crypto.randomUUID === "function") {
    return globalThis.crypto.randomUUID().replace(/-/g, "");
  }
  return Math.random().toString(16).slice(2) + Date.now().toString(16);
}

// ── Rate limiting (best effort; see rule 3 at the top) ────────────────────
const PER_IP_LIMIT = 3, PER_IP_WINDOW = 900;      // 3 enquiries / 15 min / IP
const GLOBAL_LIMIT = 30, GLOBAL_WINDOW = 3600;    // 30 / hour, everyone
const RL = new Map();

function rateOk(bucket, client, limit, window) {
  const now = Date.now() / 1000;
  const key = bucket + "|" + client + "|" + Math.floor(now / window);
  if (RL.size > 4000) {
    for (const [k, v] of RL) if (v.seen < now - 2 * window) RL.delete(k);
  }
  const entry = RL.get(key) || { count: 0, seen: now };
  entry.count += 1;
  entry.seen = now;
  RL.set(key, entry);
  return entry.count <= limit;
}

/* Best-effort client identity, for rate limiting ONLY. Vercel puts the real
   caller in x-forwarded-for's left-most entry; it is spoofable, which is
   exactly why it never decides anything but a limit. */
function clientKey(req) {
  const h = req.headers || {};
  const xff = String(h["x-forwarded-for"] || h["x-real-ip"] || "");
  if (xff) return xff.split(",")[0].trim().slice(0, 64);
  return String((req.socket && req.socket.remoteAddress) || "unknown").slice(0, 64);
}

/* Validate one submission and mail it. Never throws. */
async function submit(body, client) {
  const [data, error] = validate(body || {});

  // A bot that filled the honeypot is told the same thing a person is told,
  // and nothing is sent. It must not be able to tell the difference.
  if (error === "honeypot") return { status: 200, out: { ok: true, message: SENT_MESSAGE } };
  if (error) return { status: 400, out: { ok: false, error: "invalid", message: error } };

  if (!enabled()) {
    console.error("[partner] no email transport configured on this deployment: " +
                  "set NEWSLETTER_API_KEY (and NEWSLETTER_HTTP_PROVIDER) in Vercel");
    return { status: 503, out: { ok: false, error: "no_transport",
      message: "This form can't send mail right now. Email " + publicInbox() +
               " directly and it will reach the same inbox." } };
  }

  if (client && !rateOk("ip", client, PER_IP_LIMIT, PER_IP_WINDOW)) {
    return { status: 429, out: { ok: false, error: "too_many_requests",
      message: "That is a few messages in a row from here. Give it a few minutes, " +
               "or email " + publicInbox() + " directly." } };
  }
  if (!rateOk("global", "all", GLOBAL_LIMIT, GLOBAL_WINDOW)) {
    return { status: 429, out: { ok: false, error: "too_many_requests",
      message: "The form is busy right now. Email " + publicInbox() +
               " directly and it will reach the same inbox." } };
  }

  const [subject, html, text] = buildEnquiry(data);

  // Every admin address gets its own message. The enquiry counts as delivered
  // if ANY of them went: one bouncing address must not lose the message from
  // the other.
  let sent = 0, lastError = "";
  for (const address of adminEmails()) {
    try {
      await sendEmail({
        to: address, subject, html, text,
        // Reply goes to the person who wrote it, not to the site's own From
        // address. Without this, answering an enquiry means copying the
        // address out of the body by hand every time.
        replyTo: data.email,
      });
      sent += 1;
    } catch (err) {
      lastError = String((err && err.message) || err);
      console.error("[partner] enquiry to " + address + " failed: " + lastError);
    }
  }

  if (!sent) {
    return { status: 503, out: { ok: false, error: "send_failed",
      message: "Something went wrong sending that. Email " + publicInbox() +
               " directly and it will reach the same inbox.",
      detail: lastError.slice(0, 200) } };
  }

  // Best effort, and deliberately after the real one: the enquiry has already
  // arrived, so a failure here is worth a log line and nothing else.
  try {
    await sendEmail({ to: data.email, subject: ACK_SUBJECT, html: ACK_HTML,
                      text: ACK_TEXT, replyTo: "", timeoutMs: 6000 });
  } catch (err) {
    console.error("[partner] acknowledgement to sender failed: " +
                  String((err && err.message) || err));
  }

  console.log("[partner] enquiry from " + data.email + " (" + data.kind + ") " +
              "delivered to " + sent + " inbox(es) from Vercel");
  return { status: 200, out: { ok: true, message: SENT_MESSAGE } };
}

/* Answerable from outside without sending anything: is the form actually
   wired up on THIS deployment? A form that looks fine and mails nowhere is
   invisible from the website, and this is how that is checked:
     curl https://currentsandcritters.com/api/partner-contact
   It names the provider and the number of inboxes, and no secret. */
function state() {
  const name = providerName();
  return {
    ok: true,
    enabled: enabled(),
    provider: name ? PROVIDERS[name].label : "",
    inboxes: adminEmails().length,
    from: senderEmail(),
  };
}

// ═══════════════════════════════════════════════════════════════════════════
//  HTTP
// ═══════════════════════════════════════════════════════════════════════════
function sendJson(res, status, payload) {
  res.statusCode = status;
  res.setHeader("Content-Type", "application/json; charset=utf-8");
  res.setHeader("Cache-Control", "no-store");
  res.end(JSON.stringify(payload));
}

/* Vercel parses a JSON body for us, but not every runtime version does, and a
   proxy can hand this a string. Read whichever shape turned up, capped: an
   enquiry is 4KB of prose, so a megabyte of it is an attack, not a message. */
async function readBody(req) {
  // A Buffer is an object too, and would otherwise sail through as a body with
  // no fields in it: an enquiry refused for having no name, which is a baffling
  // thing to read when you typed one.
  if (Buffer.isBuffer(req.body) || req.body instanceof Uint8Array) {
    try { return JSON.parse(Buffer.from(req.body).toString("utf8") || "{}"); }
    catch (_) { return null; }
  }
  if (req.body && typeof req.body === "object") return req.body;
  if (typeof req.body === "string") {
    try { return JSON.parse(req.body || "{}"); } catch (_) { return null; }
  }
  // A double in a test, or a runtime that hands this something that is not a
  // stream, is not worth an exception: there is simply no body to read.
  if (!req || typeof req[Symbol.asyncIterator] !== "function") return {};
  let size = 0;
  const chunks = [];
  for await (const chunk of req) {
    size += chunk.length;
    if (size > MAX_REQUEST_BYTES) return null;
    chunks.push(chunk);
  }
  if (!chunks.length) return {};
  try { return JSON.parse(Buffer.concat(chunks).toString("utf8")); } catch (_) { return null; }
}

module.exports = async function handler(req, res) {
  if (req.method === "GET" || req.method === "HEAD") {
    return sendJson(res, 200, state());
  }
  if (req.method !== "POST") {
    res.setHeader("Allow", "GET, POST");
    return sendJson(res, 405, { ok: false, error: "method_not_allowed" });
  }

  let body;
  try {
    body = await readBody(req);
  } catch (err) {
    console.error("[partner] could not read the request body: " + err);
    body = null;
  }
  if (!body || typeof body !== "object") {
    return sendJson(res, 400, { ok: false, error: "invalid",
      message: "That didn't arrive in one piece. Try again, or email " +
               publicInbox() + " directly." });
  }

  try {
    const { status, out } = await submit(body, clientKey(req));
    return sendJson(res, status, out);
  } catch (err) {
    // Nothing above is meant to throw. If it does, the sender still has to be
    // told, because the page's fallback is what saves their message.
    console.error("[partner] unhandled: " + String((err && err.stack) || err));
    return sendJson(res, 503, { ok: false, error: "send_failed",
      message: "Something went wrong sending that. Email " + publicInbox() +
               " directly and it will reach the same inbox." });
  }
};

// Exported for test_partner_contact_api.js only. Vercel calls the default
// export; these ride along so the rules can be tested without a browser.
module.exports.__test = {
  KINDS, PROVIDERS, validate, normalizeEmail, oneLine, manyLines,
  buildEnquiry, addressLine, submit, state, providerName, adminEmails,
  publicInbox, senderEmail, ACK_TEXT, ACK_HTML, ACK_SUBJECT, SENT_MESSAGE,
  resetRateLimits: () => RL.clear(),
};
