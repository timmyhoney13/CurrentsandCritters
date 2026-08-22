# Currents & Critters: Newsletter System

Everything that could be built in code is built and tested. What remains is
account-level setup in Stripe, Render and your DNS.

**There is no Google Cloud project, no OAuth consent screen, no scopes, and no
refresh-token script.** Sending now runs over ordinary SMTP: four values from
whoever already hosts your email, or over an HTTPS email API if you prefer.

**Nothing in this document contains a real secret, and nothing in the repo
does either.** Where a value is secret you will see the variable *name* and the
*shape* of the value, never the value.

---

## 0. The one thing I cannot remove

Email to real inboxes always needs an **authenticated sender**. That is how
SMTP and the anti-spam world work, not a limitation of this code, no server
can simply emit mail from nowhere and have Gmail or Outlook accept it. So
*something* has to hold a credential for beardedsealstudios.com.

What I could remove is how much that costs you. It is now:

> **Four values you already have, pasted into Render.**

No new account, no console, no scripts, as long as your domain email is
hosted somewhere, which it is.

---

## 1. What this system does, in one page

**People join in two ways.**

1. **The website.** Every "Join the Email List" button on currentsandcritters.com
   opens `/newsletter/join`. They enter an address, get a *confirm your email*
   message, and only become a subscriber when they click it. Until then the
   record is `pending` and no campaign can reach it.
   *(Those buttons used to point at a Google Form: signups there landed in a
   spreadsheet no campaign ever read from, so nobody who used it was ever
   actually on the list.)*

2. **Stripe checkout.** If, and only if, they typed an
address into the optional **"Enter your email to get updates"** field, Stripe's
`checkout.session.completed` webhook hands that address to the newsletter code,
which creates a subscriber directly (no confirmation needed: paying
proves they own the address), sends them a welcome email, and emails you to say
somebody joined.

You write and send newsletters at **`/admin/newsletter`**, signed in with your
Google account *(that is Google **sign-in**, which the game already uses, not
a Google Cloud project)*. Sending happens on the server in controlled batches,
one individual message per subscriber, each with its own unsubscribe link.
Everything is recorded so a retry, a double-click or a server restart can never
send anyone the same newsletter twice.

**Three things are true by construction, not by care:**

| Guarantee | What enforces it |
|---|---|
| One subscriber per email address | The Firestore document id **is** `sha256(lowercased email)` |
| One Stripe event processed once | `newsletterWebhookEvents/{stripe_event_id}` is created in the same transaction as the subscriber |
| One campaign email per subscriber | The recipient document id **is** the subscriber id |

Firestore has no `UNIQUE` index. Document ids are unique by definition, so
deriving them this way makes a duplicate structurally impossible rather than
merely unlikely.

---

## 2. Files

### Created

| File | Purpose |
|---|---|
| `newsletter_server.py` | The whole backend: subscribers, the Stripe hook, unsubscribe, drafts, campaigns, the send worker, the audit log, and every admin API route. Wired into `multiplayer_server` the same additive way as `clan_server` / `analytics_server`. |
| `newsletter_email.py` | The only file that sends mail or turns admin HTML into an email: the strict HTML sanitiser, the branded shell + footer, plain-text generation, MIME building, and the three transports (SMTP / HTTPS API / Gmail API). |
| `multiplayer/client/newsletter-admin.html` | The admin page shell. Deliberately contains **no data**, it is a sign-in card until Google auth succeeds. |
| `multiplayer/client/js/newsletter-admin.js` | The admin UI: 8 sections, the composer, previews, confirmations. |
| `multiplayer/client/css/newsletter.css` | Admin styling. Same design tokens as the Developer Analytics dashboard so the two admin tools read as one. |
| `multiplayer/client/unsubscribe.html` | The public unsubscribe confirmation page. |
| `multiplayer/client/email-logo.png` | 144×144 PNG logo for email headers, extracted from `assets/logo-icon.svg` (which is an SVG wrapper around a PNG, and SVG does not render in Gmail or Outlook). |
| `scripts/get_gmail_refresh_token.py` | **Optional.** Only needed for §4 Option C (Gmail API over OAuth). Not used by the recommended SMTP path. |
| `test_newsletter_server.py` | 105 backend tests: consent, idempotency, sanitising, CSV injection, unsubscribe tokens, authorisation, campaigns, and all three transports. |
| `test_newsletter_admin_ui.js` | Real-browser admin UI checks, driven by payloads from the real server. |
| `NEWSLETTER_SETUP.md` | This file. |

### Modified

| File | Change |
|---|---|
| `multiplayer_server.py` | Import + `init()`; the newsletter hook inside the existing Stripe webhook; routes for `/admin/newsletter`, `/newsletter/unsubscribe/…` (GET and one-click POST), `/api/newsletter/*`, and `/email-logo.png`. |
| `Dockerfile` | `pip install nh3`; `COPY` the two new modules. |
| `.dockerignore`, `.gitignore` | Allowlist entries for the new files (both are `*`-then-`!` allowlists). |
| `render.yaml` | The new environment variables, documented inline. |
| `vercel.json` | Redirects for `/admin/newsletter` and `/newsletter/unsubscribe/:token` to the Render host, and a rewrite so `/email-logo.png` resolves on the marketing site. |
| `multiplayer/client/js/privacy-policy.js` | Newsletter sections updated: **see §10, there is a correction in here you should read**. |
| `multiplayer/client/preview.html`, `privacy.html`, `js/preview-app.js`, `version.json` | Version bumped to **1.6.57 / 2026-08-15.1** and cache-busters bumped with it (project convention: an edited asset must get a fresh `?v=`). |
| `test_privacy_policy.js` | Last-updated date assertion moved to August 6, 2026. |

**Nothing was removed or replaced.** Deleting the newsletter module would
return the game, the store and the payment webhook to exactly their previous
behaviour.

---

## 3. Database

Firestore, using the same service account the rest of the server already uses.
**There are no migrations to run**: Firestore creates collections on first
write. There is nothing to roll back; deleting the collections below removes
the system entirely and touches nothing else.

| Collection | Document id | Holds |
|---|---|---|
| `newsletterSubscribers` | `sha256(emailLower)[:40]` | email, emailLower, status (`active`/`pending`/`unsubscribed`), source, subscribedAt, resubscribedAt, unsubscribedAt, unsubId, tokenVersion, welcomeEmailStatus/At/Attempts/Error/Kind, stripeSessionId, stripeEventId, consentNote, createdAt, updatedAt |
| `newsletterWebhookEvents` | Stripe event id | eventId, sessionId, subscriberId, result, processedAt |
| `newsletterCampaigns` | auto | subject, previewText, contentHtml (sanitised), status, createdAt/By, startedAt/By, sentAt, intendedRecipients, sentCount, failedCount, skippedCount, interruptedCount |
| `newsletterCampaigns/{id}/recipients` | **subscriber id** | campaignId, subscriberId, email, status, attempts, gmailMessageId, lastErrorCategory, sentAt, leaseUntil, updatedAt |
| `newsletterAudit` | auto | action, at, atIso, admin, subscriberId, campaignId, summary, correlationId |
| `newsletterMeta/stripeFieldLabels` | fixed | The custom-field **labels** last seen on a checkout (label text only, never an answer), the diagnostic in §5.2. |

**Indexes:** none to create. Every query is a single-field equality, which
Firestore indexes automatically. The subscriber list is searched, filtered,
sorted and paginated in Python over one cached scan: deliberately, so you
never have to create a composite index per (status, sort) pair, and never hit a
500 the first time one is missing. The honest ceiling is 20,000 subscriber
records; past that the list truncates and the UI says so.

**Firestore security rules:** no change needed. Every one of these collections
is written and read only by the server through the Admin SDK, which bypasses
rules. No browser ever reads them directly.

---

## 4. Set up sending: pick ONE

The system supports three ways to send. **Option A is the one to use.** You only
ever configure one; the others can stay completely unset.

---

### Option A: SMTP (recommended, ~5 minutes, no new accounts)

Your domain email is hosted somewhere already. Whoever hosts it will give you
these four values, usually on a page called *IMAP/SMTP*, *Mail client setup*,
or *Email clients*.

**Render → your service → Environment → add:**

| Key | What to put |
|---|---|
| `SMTP_HOST` | e.g. `smtp.gmail.com`, `smtp.zoho.com`, `mail.privateemail.com` |
| `SMTP_PORT` | `587` (already set for you). Use `465` only if your host says so |
| `SMTP_USERNAME` | almost always `timothy.honey@beardedsealstudios.com` |
| `SMTP_PASSWORD` | the mailbox password, or an **app password**: see below |

Then **Manual Deploy → Deploy latest commit**, open
`/admin/newsletter` → **Connections**, and it should say **Connected**.

**If your email is on Google Workspace** (very likely, since you sign in with
Google), the host is `smtp.gmail.com` and the password must be an **App
Password**, not your normal one:

1. <https://myaccount.google.com/security> → turn on **2-Step Verification** if
   it is not already on (App Passwords do not exist without it).
2. <https://myaccount.google.com/apppasswords> → name it `Currents & Critters
   Newsletter` → **Create**.
3. Google shows a 16-character password. Paste it into `SMTP_PASSWORD`, the
   spaces Google puts in it are stripped for you, so either form works.
   **Make sure you are signed in as the account the password is for**, the
   single most common failure is generating it on a different Google account,
   which fails with `535 BadCredentials` and no other clue.

That is a settings page, not a Google Cloud project. No consent screen, no
scopes, no refresh token, nothing to re-authorise later.

**If your email is somewhere else**: Namecheap Private Email, Zoho, Fastmail,
iCloud+, Proton Bridge, your registrar, the same four values apply; look for
"SMTP settings" in their help. Nothing else changes.

---

### Option B, an HTTPS email API (if SMTP is blocked, or when you outgrow it)

Some hosts block outbound SMTP ports. If **Connections** reports a network
error that never clears, use this instead. It is also the right move once your
list grows past a few thousand (see §8).

1. Create an account at one of: **Resend** (simplest), **Postmark**, **Brevo**,
   **SendGrid**.
2. Add and verify the domain `beardedsealstudios.com` in their dashboard,
   they will give you DNS records, which are the same SPF/DKIM records you need
   anyway (§8).
3. Create an API key.

**Render → Environment:**

| Key | Value |
|---|---|
| `NEWSLETTER_API_KEY` | the key from step 3 |
| `NEWSLETTER_HTTP_PROVIDER` | `resend` (default) / `postmark` / `brevo` / `sendgrid` |

Leave the `SMTP_*` values blank, or set `NEWSLETTER_TRANSPORT=http` to force it.

---

### Option C: Gmail API over OAuth (optional, not recommended)

Still supported, and `scripts/get_gmail_refresh_token.py` still works if you
ever want it. It is the **only** route that needs a Google Cloud project, a
consent screen and scopes, which is exactly why nothing requires it any more.
Set `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` and `GOOGLE_REFRESH_TOKEN` if
you go this way.

---

### Which one is live?

`NEWSLETTER_TRANSPORT` forces a choice. Left unset, the first fully-configured
option wins, in the order **SMTP → HTTP API → Gmail API**. So the moment you
fill in `SMTP_*`, that is what sends, any leftover Google variables become
dead weight rather than a dependency.

**Connections** always names the method that is actually in use, so this is
never a guess.

---

## 5. Stripe setup

The webhook endpoint **already exists and is already configured**, the
newsletter reuses `/api/stripe/webhook`, which your store has been using. There
is no second endpoint to create.

### 4.1 Confirm the endpoint

Stripe Dashboard → **Developers → Webhooks**. You should already have:

| Setting | Value |
|---|---|
| Endpoint URL | `https://play.currentsandcritters.com/api/stripe/webhook` |
| Events | `checkout.session.completed` **and** `checkout.session.async_payment_succeeded` |
| Signing secret | already in Render as `STRIPE_WEBHOOK_SECRET` |

Both events are required. Delayed payment methods (bank debits, Cash App) send
the second one; without it, those buyers are charged and never subscribed.

### 4.2 ⚠️ The one thing you must verify, the field label

**This is the single most likely thing to silently break the whole system.**

Stripe echoes a Payment Link's custom-question label back to us verbatim, and
we find the newsletter answer by matching that label. If it does not match,
the lookup returns nothing, **nobody is ever subscribed, and there is no error
anywhere.**

On **every** Payment Link (Stripe → Payment Links → edit → Custom fields),
confirm the optional newsletter question's label is exactly:

```
Enter your email to get updates
```

The code already accepts several spellings of this, plus a heuristic for any
label that asks for an **email** *and* mentions **updates / newsletter /
mailing list**. It deliberately will **not** match a bare "Email" or "Billing
email": consent has to be legible in the question itself.

If your label is something else, either change it in Stripe, or add it in
Render:

```
NEWSLETTER_FIELD_LABEL = Your exact label here|Another accepted label
```

**How to check without guessing:** after any test purchase, open
`/admin/newsletter` → **Connections**. It shows *"Last checkout asked:"* with
the actual labels Stripe sent, and whether one matched. That panel exists
precisely so this failure can never be invisible.

> Note: Stripe caps a custom-field label at 50 characters. The longer sentence
> that was quoted in your Privacy Policy (89 characters) cannot be the live
> label: see §9.

---

## 6. Render environment variables

**Render → your service → Environment.** Add each of these, then **Manual
Deploy → Deploy latest commit** (Render restarts on env changes, but a deploy
is the reliable way to be sure).

**Only two things are genuinely required:** the unsubscribe secret, and ONE
way to send. Everything else already has a sensible default.

#### Required

| Variable | Secret? | Purpose | Example shape | Where you get it |
|---|---|---|---|---|
| `NEWSLETTER_UNSUBSCRIBE_SECRET` | **Yes** | Signs unsubscribe links. Campaign sending refuses to start without it | 64 random urlsafe chars | `python3 -c "import secrets;print(secrets.token_urlsafe(48))"` |

#### Sending: fill in ONE group

**Option A, SMTP (recommended):**

| Variable | Secret? | Purpose | Example shape | Where you get it |
|---|---|---|---|---|
| `SMTP_HOST` | No | Your mail provider's SMTP server | `smtp.gmail.com` | Your email host |
| `SMTP_PORT` | No | Already set to `587` | `587` (or `465`) | Your email host |
| `SMTP_USERNAME` | No | The mailbox to log in as | `timothy.honey@beardedsealstudios.com` | You |
| `SMTP_PASSWORD` | **Yes** | Mailbox or **app** password | 16 chars for a Google App Password | Your email host |
| `SMTP_SECURITY` | No | Optional override | `starttls` / `ssl` | Auto-detected from the port |

**Option B, HTTPS API:**

| Variable | Secret? | Purpose | Example shape | Where you get it |
|---|---|---|---|---|
| `NEWSLETTER_API_KEY` | **Yes** | Provider API key | `re_xxxxxxxxxxxx` | Resend / Postmark / Brevo / SendGrid |
| `NEWSLETTER_HTTP_PROVIDER` | No | Which one | `resend` | You |

**Option C, Gmail API (optional):** `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`
(secret), `GOOGLE_REFRESH_TOKEN` (secret). Only needed if you deliberately
choose §4 Option C.

#### Already set for you (change only if you want to)

| Variable | Purpose | Default |
|---|---|---|
| `ADMIN_EMAIL` | The **only** account that can open `/admin/newsletter` | `timothy.honey@beardedsealstudios.com` |
| `NEWSLETTER_FROM_EMAIL` | The From address | `timothy.honey@beardedsealstudios.com` |
| `NEWSLETTER_FROM_NAME` | From display name | `Currents & Critters` |
| `APP_BASE_URL` | Where unsubscribe links point | `https://play.currentsandcritters.com` |
| `CURRENTS_AND_CRITTERS_URL` | "Visit" button + email logo host | `https://currentsandcritters.com` |
| `PRIVACY_POLICY_URL` | Footer link | `https://currentsandcritters.com/privacy` |
| `NEWSLETTER_DAILY_SEND_CAP` | Messages/day this process will send | `1200` |
| `NEWSLETTER_TRANSPORT` | Force `smtp` / `http` / `gmail_api` | auto-detect |
| `NEWSLETTER_FIELD_LABEL` | Extra accepted Stripe labels, `|`-separated | unset: see §5.2 |
| `STRIPE_WEBHOOK_SECRET` | *Already set*: reused, do not change |: |

Every variable takes effect on the next deploy.

⚠️ **Changing `NEWSLETTER_UNSUBSCRIBE_SECRET` invalidates every unsubscribe
link already sitting in someone's inbox.** Set it once and leave it alone.

**No Render worker, cron job or background service is needed.** The send worker
is a daemon thread inside the existing web service. That works because the
service has a mounted disk, which forces Render to run exactly one instance,
and campaign state lives in Firestore, so a restart resumes rather than
restarts.

---

## 7. DNS: SPF, DKIM, DMARC

Without these, Gmail and Outlook will junk your newsletters. Do all three, at
your DNS provider for **beardedsealstudios.com**.

### SPF
One TXT record at the root. **If you already have an SPF record, edit it, a
domain with two SPF records fails SPF entirely.**

| Type | Name | Value |
|---|---|---|
| TXT | `@` | `v=spf1 include:_spf.google.com ~all` |

### DKIM
1. Google Admin console → **Apps → Google Workspace → Gmail → Authenticate
   email**.
2. Select `beardedsealstudios.com`, **Generate new record** (2048-bit, prefix
   `google`).
3. Add the TXT record it gives you:

| Type | Name | Value |
|---|---|---|
| TXT | `google._domainkey` | `v=DKIM1; k=rsa; p=<long key Google shows you>` |

4. Wait for DNS to propagate (up to ~48h, usually minutes), then click **Start
   authentication** in the Admin console.

### DMARC
Start in monitor mode, and only tighten once you have seen a week of clean
reports.

| Type | Name | Value |
|---|---|---|
| TXT | `_dmarc` | `v=DMARC1; p=none; rua=mailto:timothy.honey@beardedsealstudios.com; fo=1` |

After a week with SPF and DKIM passing, move to `p=quarantine`, and later
`p=reject`. Do not start at `p=reject`, if anything is misconfigured, your
mail disappears silently.

### Verify
```bash
dig +short TXT beardedsealstudios.com
dig +short TXT google._domainkey.beardedsealstudios.com
dig +short TXT _dmarc.beardedsealstudios.com
```
Then send yourself a test (§8) and use Gmail's **Show original**, it must say
`SPF: PASS`, `DKIM: PASS`, `DMARC: PASS`.

---

## 8. Sending limits, the real numbers

Whatever you send through has a cap, and going over it does not bounce, the
provider throttles or suspends you, sometimes for 24 hours. So the cap is
enforced **here, before the wire**, by `NEWSLETTER_DAILY_SEND_CAP`.

| Sending account | Real daily limit | Cap used by default |
|---|---|---|
| Google Workspace (your own domain) | **2,000 recipients / 24h** | 1,200 |
| Free `@gmail.com` | **500 / day** | **400** |
| Resend | 100/day free; paid plans far higher | 1,200 |
| Postmark / Brevo / SendGrid | per plan | 1,200 |

The default follows the From address automatically: set
`NEWSLETTER_FROM_EMAIL` to an `@gmail.com` address and the cap drops to 400 on
its own, so a Workspace-sized cap can never end up pointed at a 500/day
mailbox. Setting `NEWSLETTER_DAILY_SEND_CAP` explicitly overrides it, and the
**Connections** tab warns if you set it above what the account can take.

- One message to one subscriber = one recipient. This system never puts more
  than one address on a message, so **your list size is your daily send**.
- The default cap of **1,200** leaves headroom for welcome emails, test sends
  and your ordinary human email on the same mailbox. Raise it only to what your
  provider genuinely allows.
- If the cap is hit mid-campaign the campaign **pauses** and resumes
  automatically after 00:00 UTC. Nobody is dropped and nobody is sent twice.

**When your list passes ~1,500, move to Option B** (§4). Google's mail service
is not a bulk sender and will eventually treat volume as abuse.
`newsletter_email.send_email()` is the only function that knows how mail leaves
the building: switching is one environment variable, not a rewrite.

---

## 9. Testing

Run the automated suites first, they need no accounts and no network:

```bash
python3 test_newsletter_server.py      # 105 tests
node   test_newsletter_admin_ui.js     # needs Chrome
```

Then, in order:

### 9.1 Deploy check
```bash
curl -s https://play.currentsandcritters.com/version.json
```
Must show **`1.6.57` / `2026-08-15.1`**. If it doesn't, the deploy hasn't
finished: Render Docker builds take ~10–15 minutes. *"Still broken" has often
meant "never shipped".*

### 9.2 Sending connection
Open `/admin/newsletter` → **Connections**. You want:
- **Method**: names the transport actually in use (e.g. `SMTP (smtp.gmail.com)`)
- **Connection: Connected**, this is a real login/handshake, not a guess
- **Unsubscribe links: Ready**

Any failure states exactly what to fix. Note that with SMTP or an HTTP API the
panel will say the From address is *not independently verifiable*, that is
deliberate honesty: only a test send proves it, and claiming otherwise is how a
"configured" system quietly fails.

### 9.3 Admin authorisation
| Test | Expected |
|---|---|
| Open `/admin/newsletter` signed out | Sign-in card only. No data. |
| Sign in as `timothy.honey@beardedsealstudios.com` | Full admin |
| Sign in as any other Google account | "not authorised", no data |
| `curl -X POST https://play.currentsandcritters.com/api/newsletter/subscribers -H 'Content-Type: application/json' -d '{"idToken":"x"}'` | `403 {"ok":false,"error":"unauthorized"}` |
| Same with `-d '{"email":"timothy.honey@beardedsealstudios.com"}'` | Still 403: body claims are never trusted |
| Repeat a failed call 40× | `429` |

### 9.4 Stripe signup (use TEST mode)
1. Buy the $1 coin pack through a TEST Payment Link.
2. Type a real address you control into **"Enter your email to get updates"**.
3. Complete the payment.
4. Within ~a minute: a **welcome email** to that address, and a **"New
   Currents & Critters Newsletter Subscriber"** email to you.
5. `/admin/newsletter` → **Subscribers** shows them as Active, source *Stripe
   Checkout*.

Then check the paths that must produce **nothing**:

| Test | Expected |
|---|---|
| Checkout, field left **blank** | No subscriber. Paying is not consent. |
| Checkout, field = `not-an-email` | No subscriber |
| Start a checkout, abandon it | No subscriber |
| Buy again with the **same** address | No second record, no second welcome, no second notification |
| Stripe → Webhooks → **Resend** that event | Nothing changes. Idempotent. |
| Stripe → Webhooks → send with a bad signature | `400`, nothing recorded |

### 9.5 Welcome email
Check on phone and desktop: logo renders, layout is readable, **Visit Currents
& Critters** works, **Privacy Policy** works, **Unsubscribe** works, business
address is present. View the plain-text part (Gmail: Show original), it must
be readable prose, not stripped tags.

### 9.6 Test email
Compose → **Send Test Email**. It arrives at your address only, subject
prefixed `[TEST]`, with a yellow TEST banner and **no live unsubscribe token**.
Click it three times fast, exactly one email arrives.

### 9.7 A safe mass-send rehearsal
**Do this before you ever send to the real list.**

1. Subscribers → **Add subscriber** → 2–3 addresses you own (tick the
   permission box).
2. Unsubscribe every real subscriber temporarily, *or* do this on a Render
   preview service pointed at a scratch Firebase project. **Do not skip this
   step**, there is no "cancel send" once a campaign starts.
3. Compose a newsletter, **Send to all subscribers**, type `SEND`.
4. While it runs, check **Sending Progress**: counts move, percentage climbs.
5. Verify:
   - each address gets exactly **one** email;
   - each has a **different** unsubscribe link;
   - no address appears in anyone else's copy;
   - To/CC/BCC contain exactly one address.
6. Refresh the page mid-send → no duplicates.
7. Press **Send** again on the same newsletter → refused ("already sending").
8. Unsubscribe one of them mid-send → they are **skipped**, not emailed.

### 9.8 Unsubscribe
| Test | Expected |
|---|---|
| Click the link in a real email | "You have been unsubscribed…" |
| Click it again | Same friendly page, no error |
| Change one character of the token | Same page, **nobody is unsubscribed** |
| Gmail's own **Unsubscribe** button (top of the message) | Works: one-click, RFC 8058 |
| Send a new campaign | The unsubscribed person is not in it |
| They buy again and re-enter their address | Reactivated, welcome email sent again, old link now dead |

### 9.9 Sanitising
Compose, paste `<script>alert(1)</script><img src=x onerror=alert(1)>` plus
some normal text. Save. Preview and test-send. The script and handler are
gone; your text remains.

---

## 10. ⚠️ Things for you to review

### 9.1 The Privacy Policy quoted a label that cannot exist
Your policy said the checkout field is labelled:

> "Enter your email to join the Currents & Critters newsletter and receive
> occasional updates."

That is **89 characters**, and **Stripe caps a custom-field label at 50**. So
that cannot be what the live Payment Links actually ask. I have changed the
policy to quote the label you gave me: **"Enter your email to get updates"**,
and added explicit sentences that leaving it blank, completing a purchase, or
giving an email for a receipt do **not** subscribe anyone.

**Please confirm the live label in Stripe matches** (§5.2), and read the
updated §6 of the policy before you consider it published.

### 9.2 Privacy Policy changes are legal text
I updated section 6 (what is stored, no tracking pixels, individual sends),
section 9 (added Gmail and Firebase/Firestore as processors) and the
last-updated date. I did not invent any legal claim. **Read them before
publishing.**

### 9.3 Sessions are bearer tokens, not cookies
You asked for HTTP-only cookies and CSRF tokens. This system instead uses the
Firebase ID token scheme the rest of your codebase already uses (`/api/trade/*`,
`/api/analytics/*`), and I think that is the stronger choice rather than a
shortcut:

- A CSRF attack works because cookies are **ambient**, the browser attaches
  them to any request, including one triggered by evil.com. There are no
  cookies here. Authorisation is a token this page's own JavaScript puts in
  the request body, so a forged cross-site request simply arrives
  unauthenticated. **CSRF is structurally impossible rather than mitigated.**
- Adding a cookie session *plus* a CSRF token would create the ambient
  authority the token scheme avoids, to get back to the same place.

Your brief said to use the project's existing conventions where they are
stronger. This is that case. If you would still prefer cookie sessions, say so
and I will add them.

---

## 11. Limitations and remaining risks

1. **Google's mail service is not a bulk mailer.** Fine to ~1,500/day over
   SMTP. Past that, switch to §4 Option B, one environment variable.
2. **A crash mid-send leaves a few "interrupted" recipients.** When the process
   dies between handing a message to Gmail and recording it, the outcome is
   genuinely unknown. Rather than guess, those are marked *interrupted* and
   **never auto-resent**: Sending Progress shows them with an explicit
   **Retry** button. This is the deliberate trade: a handful of manual clicks
   instead of a chance of double-sending your whole list.
3. **20,000 subscriber ceiling** on the admin list view (§3). Well beyond
   Gmail's practical limit anyway.
4. **Rate limiting is per-process**, not distributed. Correct for this service
   (one instance, mounted disk); it would need Redis if you ever scale out.
5. **The Stripe label is a behaviour key.** §5.2. The Connections tab makes a
   mismatch visible, but nothing can make it impossible.
6. **`nh3` is the production sanitiser**; a deny-by-default parser in
   `newsletter_email.py` is the fallback if the wheel is ever missing.
   Connections shows which is live. Both are tested.
7. **No open/click tracking**, as you asked. If you ever want it, that is a new
   decision with its own privacy-policy consequences.
8. **I could not verify anything requiring your accounts.** I have not seen
   your Stripe Dashboard, Google Cloud project, Render environment or DNS. Every
   claim in this document about code is tested; every claim about your accounts
   is an instruction, not a confirmation.

---

## 12. Deploying

```bash
git push                     # Render auto-deploys from main
```

- **Render (game + newsletter):** ~10–15 min for a Docker build.
- **Vercel (marketing site):** ~1 min.
- Confirm with `curl -s https://play.currentsandcritters.com/version.json`.

**Rollback:** `git revert <commit> && git push`. The Firestore collections are
additive, nothing else reads them, so leaving them in place after a revert is
harmless.
