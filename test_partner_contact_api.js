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

/* A fake SMTP server on loopback. It speaks just enough of the protocol to be
   driven by a real client, and keeps every line it was sent so the test can
   read what actually went on the wire rather than what the code meant. */
function fakeSmtp({ authMode = "plain", failAuth = false } = {}) {
  const net = require("net");
  const sessions = [];
  const sockets = new Set();
  const server = net.createServer(sock => {
    sockets.add(sock);
    sock.on("close", () => sockets.delete(sock));
    sock.on("error", () => {});
    const session = { auth: [], from: "", to: [], data: "" };
    sessions.push(session);
    let buf = "", inData = false, expecting = "";
    sock.setEncoding("utf8");
    sock.write("220 fake.smtp ESMTP ready\r\n");
    sock.on("data", chunk => {
      buf += chunk;
      let at;
      while ((at = buf.indexOf("\r\n")) >= 0) {
        const line = buf.slice(0, at);
        buf = buf.slice(at + 2);
        if (inData) {
          if (line === ".") { inData = false; sock.write("250 2.0.0 OK queued\r\n"); }
          else session.data += line + "\n";
          continue;
        }
        const up = line.toUpperCase();
        if (expecting) {
          session.auth.push(Buffer.from(line, "base64").toString("utf8"));
          if (expecting === "user") { expecting = "pass"; sock.write("334 UGFzc3dvcmQ6\r\n"); }
          else { expecting = ""; sock.write(failAuth ? "535 5.7.8 Username and Password not accepted\r\n" : "235 2.7.0 Accepted\r\n"); }
        } else if (up.startsWith("EHLO")) {
          sock.write("250-fake.smtp\r\n250-SIZE 35882577\r\n250 AUTH " +
                     (authMode === "login" ? "LOGIN" : "LOGIN PLAIN") + "\r\n");
        } else if (up.startsWith("AUTH PLAIN")) {
          session.auth.push(Buffer.from(line.slice(11), "base64").toString("utf8"));
          sock.write(failAuth ? "535 5.7.8 Username and Password not accepted\r\n" : "235 2.7.0 Accepted\r\n");
        } else if (up === "AUTH LOGIN") {
          expecting = "user"; sock.write("334 VXNlcm5hbWU6\r\n");
        } else if (up.startsWith("MAIL FROM")) {
          session.from = line; sock.write("250 2.1.0 OK\r\n");
        } else if (up.startsWith("RCPT TO")) {
          session.to.push(line); sock.write("250 2.1.5 OK\r\n");
        } else if (up === "DATA") {
          inData = true; sock.write("354 Go ahead\r\n");
        } else if (up === "QUIT") {
          sock.write("221 2.0.0 Bye\r\n"); sock.end();
        } else {
          sock.write("250 OK\r\n");
        }
      }
    });
  });
  return new Promise(resolve => {
    server.listen(0, "127.0.0.1", () => resolve({
      port: server.address().port,
      sessions,
      close: () => new Promise(done => {
        for (const s of sockets) s.destroy();
        server.close(() => done());
      }),
    }));
  });
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
  SMTP_HOST: "", SMTP_PORT: "", SMTP_SECURITY: "",
  SMTP_USERNAME: "", SMTP_PASSWORD: "", SMTP_TIMEOUT: "",
  NEWSLETTER_TRANSPORT: "",
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
  ok(r.json && r.json.enabled === true && r.json.transport === "Resend",
     "GET names the transport a bare NEWSLETTER_API_KEY implies: " + (r.json && r.json.transport));
  ok(!r.payload && !JSON.stringify(r.json).includes("re_test_key"),
     "...and never the key itself");
  ok(r.headers["cache-control"] === "no-store", "...and is not cached");

  setEnv({ NEWSLETTER_API_KEY: "", BREVO_API_KEY: "xkeysib-test" });
  r = await call({ method: "GET" });
  ok(r.json && r.json.transport === "Brevo",
     "a provider-named key alone picks that provider: " + (r.json && r.json.transport));

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

  /* ══════════════════════════════════════════════════════════════════
     SMTP: the transport the game server was configured with and could not
     reach. Render blocks outbound 25/465/587, so those credentials have been
     sitting there unable to leave the container; from Vercel they work, which
     is the whole reason this endpoint exists. Nothing below touches the real
     Gmail: it talks to a fake SMTP server on loopback and reads back exactly
     what went on the wire.
     ══════════════════════════════════════════════════════════════════ */
  {
    const fake = await fakeSmtp();
    setEnv({
      NEWSLETTER_API_KEY: "",
      SMTP_HOST: "127.0.0.1", SMTP_PORT: String(fake.port), SMTP_SECURITY: "none",
      SMTP_USERNAME: "timothy.honey@beardedsealstudios.com",
      // The four-group shape Google shows an app password in. It must not
      // reach the wire with its spaces still in it.
      SMTP_PASSWORD: "woff lfgo xgfb rhpv",
    });
    r = await call({ method: "GET" });
    ok(r.json.enabled === true && r.json.transport === "SMTP (127.0.0.1)",
       "SMTP alone is a working transport: " + (r.json && r.json.transport));

    r = await call({ body: GOOD });
    ok(r.status === 200 && r.json.ok === true, "an enquiry sends over SMTP: " + r.status);
    ok(calls.length === 0, "...with no HTTPS provider involved at all");
    ok(fake.sessions.length === 2, "two SMTP conversations: enquiry and ack (" + fake.sessions.length + ")");

    const sess = fake.sessions[0];
    ok(sess.auth.length === 1 && sess.auth[0] ===
       "\u0000timothy.honey@beardedsealstudios.com\u0000wofflfgoxgfbrhpv",
       "the app password is sent with its spaces stripped: " + JSON.stringify(sess.auth[0]));
    ok(/MAIL FROM:<timothy\.honey@beardedsealstudios\.com>/.test(sess.from),
       "MAIL FROM is the site's own address: " + sess.from);
    ok(sess.to.length === 1 && /RCPT TO:<timothy\.honey@beardedsealstudios\.com>/.test(sess.to[0]),
       "RCPT TO is ADMIN_EMAIL: " + sess.to.join(" "));

    const head = sess.data.split("\n\n")[0];
    ok(/^From: Currents and Critters <timothy\.honey@beardedsealstudios\.com>$/m.test(head),
       "the From header names the game: " + (head.match(/^From:.*$/m) || [""])[0]);
    ok(/^Reply-To: alex@blueharbor\.org$/m.test(head),
       "Reply-To is the person who wrote in, so hitting reply answers THEM");
    ok(/^Subject: Partner enquiry \(Ocean conservation partnership\): Blue Harbor Aquarium$/m.test(head),
       "the subject survived: " + (head.match(/^Subject:.*$/m) || [""])[0]);
    ok(/^Content-Type: multipart\/alternative; boundary="cc-[0-9a-f]+"$/m.test(head),
       "it is a multipart message, so a plain-text client gets plain text");
    ok(/^Message-ID: <[0-9a-f]+@beardedsealstudios\.com>$/m.test(head),
       "...with a Message-ID from the sending domain");

    const bodies = sess.data.split(/--cc-[0-9a-f]+/).slice(1, 3)
      .map(part => Buffer.from(part.split("\n\n").slice(1).join("").replace(/\s+/g, ""), "base64")
        .toString("utf8"));
    ok(bodies[0].includes("coastal education program") && bodies[0].includes("Blue Harbor"),
       "the plain-text part carries the enquiry");
    ok(bodies[1].includes("coastal education program") && bodies[1].includes("<table"),
       "...and the HTML part carries the same thing, laid out");
    ok(bodies[1].includes("alex@blueharbor.org"),
       "...with the address to reply to in the body as well");

    const ack = fake.sessions[1];
    ok(ack.to.length === 1 && /RCPT TO:<alex@blueharbor\.org>/.test(ack.to[0]),
       "the acknowledgement goes to the sender: " + ack.to.join(" "));
    const ackText = Buffer.from(
      ack.data.split(/--cc-[0-9a-f]+/)[1].split("\n\n").slice(1).join("").replace(/\s+/g, ""),
      "base64").toString("utf8");
    ok(ackText === T.ACK_TEXT, "...and it is still the constant, byte for byte");
    ok(!ackText.includes("Alex") && !ackText.includes("coastal"),
       "...quoting nothing the caller typed");

    await fake.close();
  }

  /* AUTH LOGIN, for a server that offers nothing else. */
  {
    const fake = await fakeSmtp({ authMode: "login" });
    setEnv({
      NEWSLETTER_API_KEY: "",
      SMTP_HOST: "127.0.0.1", SMTP_PORT: String(fake.port), SMTP_SECURITY: "none",
      SMTP_USERNAME: "tim", SMTP_PASSWORD: "secret",
    });
    r = await call({ body: GOOD });
    ok(r.json.ok === true && fake.sessions[0].auth.join("|") === "tim|secret",
       "a server that only offers AUTH LOGIN is signed into one line at a time: " +
       fake.sessions[0].auth.join("|"));
    await fake.close();
  }

  /* A mailbox that refuses the password. The reader is told it failed; the
     password itself is never in the message, the log line or the response. */
  {
    const fake = await fakeSmtp({ failAuth: true });
    setEnv({
      NEWSLETTER_API_KEY: "",
      SMTP_HOST: "127.0.0.1", SMTP_PORT: String(fake.port), SMTP_SECURITY: "none",
      SMTP_USERNAME: "tim", SMTP_PASSWORD: "wrong-password-here",
    });
    r = await call({ body: GOOD });
    ok(r.status === 503 && r.json.error === "send_failed",
       "a rejected password is reported, not swallowed: " + r.status);
    ok(/535/.test(r.json.detail || "") && !JSON.stringify(r.json).includes("wrong-password-here"),
       "...with the server's code and NOT the password: " + r.json.detail);
    await fake.close();
  }

  /* Plaintext SMTP to a real host would hand the mailbox password to anything
     on the path. A dashboard typo must not be able to do that. */
  setEnv({
    NEWSLETTER_API_KEY: "",
    SMTP_HOST: "smtp.gmail.com", SMTP_PORT: "587", SMTP_SECURITY: "none",
    SMTP_USERNAME: "tim", SMTP_PASSWORD: "secret",
  });
  r = await call({ body: GOOD });
  ok(r.status === 503 && /localhost/.test(r.json.detail || ""),
     "SMTP_SECURITY=none is refused against a real host: " + r.json.detail);

  /* Both configured: the API key wins, same as newsletter_email.transport(). */
  {
    const fake = await fakeSmtp();
    setEnv({
      SMTP_HOST: "127.0.0.1", SMTP_PORT: String(fake.port), SMTP_SECURITY: "none",
      SMTP_USERNAME: "tim", SMTP_PASSWORD: "secret",
    });
    r = await call({ body: GOOD });
    ok(r.json.ok === true && calls.length === 2 && fake.sessions.length === 0,
       "with both set the HTTPS key wins and SMTP is left alone");
    setEnv({
      SMTP_HOST: "127.0.0.1", SMTP_PORT: String(fake.port), SMTP_SECURITY: "none",
      SMTP_USERNAME: "tim", SMTP_PASSWORD: "secret",
      NEWSLETTER_TRANSPORT: "smtp",
    });
    r = await call({ body: GOOD });
    ok(r.json.ok === true && calls.length === 0 && fake.sessions.length === 2,
       "...and NEWSLETTER_TRANSPORT=smtp forces it back");
    await fake.close();
  }

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
