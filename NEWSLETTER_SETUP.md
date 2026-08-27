# Currents & Critters: Newsletter System

Everything that could be built in code is built and tested. What remains is
account-level setup in Stripe, Render and your DNS.

**There is no Google Cloud project, no OAuth consent screen, no scopes, and no
refresh-token script.** Sending runs over an **HTTPS email API**: one API key
pasted into Render. (SMTP is also supported, but **not on Render**, which
blocks the outbound SMTP ports. See §4.)

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

> **One API key, pasted into Render.**

No console, no scripts, no Google Cloud project. It does mean one free account
with an email provider, because Render blocks the SMTP ports that would
otherwise have let the mailbox you already own do the job (§4).

---

## 1. What this system does, in one page

**People join in two ways.**

1. **The website.** Every "Join the Email List" button on currentsandcritters.com
   opens `/newsletter/join`. They enter an address, press the button, and they
   are a subscriber: **one step, no confirmation click**. The welcome email is
   what lands in their inbox.
   *(This used to be double opt-in, with a "confirm your email" message that
   had to be clicked first. It was removed because the people who ignore that
   message are not spammers, they are subscribers who thought they had joined
   and then never hear from you again. What replaces it is the welcome email
   itself: it goes to the address that was typed, it says plainly what it is,
   and it carries the same one-click unsubscribe as every other message, so
   anybody whose address was typed in by somebody else is one tap from off the
   list in the very first mail they get.)*
   *(Before that, those buttons pointed at a Google Form: signups landed in a
   spreadsheet no campaign ever read from, so nobody who used it was ever
   actually on the list.)*

   **Records still stuck in `pending`** are from the old flow: real people who
   filled in the form and never clicked. Nothing creates a new one, and nothing
   will ever confirm them on their own. The admin page counts them under
   *Stranded (old signups)* and each row has a **Confirm by hand** button that
   makes them active and sends the welcome. Old confirmation links still work
   forever, so anyone who digs one out of their inbox still gets in.

2. **Stripe checkout.** If, and only if, they typed an
address into the optional **"Enter your email to get updates"** field, Stripe's
`checkout.session.completed` webhook hands that address to the newsletter code,
which creates a subscriber directly, sends them a welcome email, and emails you
to say somebody joined. (This path never had a confirmation step: paying with
an address already proves they own it.)

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
| `multiplayer/client/preview.html`, `privacy.html`, `js/preview-app.js`, `version.json` | Version bumped to **1.6.80 / 2026-08-22.7** and cache-busters bumped with it (project convention: an edited asset must get a fresh `?v=`). |
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

> ### ⚠️ READ THIS FIRST: Render blocks SMTP. Use Option A.
>
> **Render blocks outbound connections on the SMTP ports (25, 465 and 587)**,
> on every plan. It is a deliberate anti-spam policy of the host, not a bug and
> not something a setting on our side can defeat.
>
> This is why no newsletter email was being delivered. `SMTP_HOST` was set to
> `smtp.gmail.com` and the credentials were perfectly correct, but the
> connection could never leave the container: the connect attempt just hung
> until it timed out, and the admin page reported
> *"Could not reach smtp.gmail.com:587"*.
>
> **Any SMTP settings will fail on Render, no matter how correct they are.**
> The fix is an HTTPS email API, which talks to port 443 like any other web
> request and is never blocked. That is Option A below, and it is the only
> option that works on this host.
>
> Nobody who signed up during the outage was lost. Every welcome email that
> could not be delivered is queued, and they are all sent automatically the
> next time the server starts with a working transport. See §4.1.

---

### Option A: an HTTPS email API (the one that works here, ~10 minutes)

1. Create an account with one of these. **Brevo** is suggested first because
   its free tier (300 emails/day) allows a *verified single sender*, so it
   works with the existing `currentsandcritters@gmail.com` From address
   without owning a domain. Resend and Postmark generally want a verified
   domain before they will send to arbitrary recipients.

   | Provider | Free tier | Notes |
   |---|---|---|
   | **Brevo** | 300/day | Verified single sender is enough; no domain needed |
   | **Resend** | 3,000/month | Wants a verified domain for real sending |
   | **Postmark** | 100/month trial | Excellent deliverability, small free tier |
   | **SendGrid** | 100/day | Single-sender verification available |

2. Verify the sender address they ask for. If you use Brevo with the current
   setup, verify **`currentsandcritters@gmail.com`**, which is the value of
   `NEWSLETTER_FROM_EMAIL`. It must match, or the provider rejects every
   message.

   *(Better, when you have a moment: verify the domain
   `beardedsealstudios.com` and move `NEWSLETTER_FROM_EMAIL` to
   `timothy.honey@beardedsealstudios.com`. Bulk mail from a consumer
   `@gmail.com` address is filtered much harder. See §8.)*

3. Create an API key.

4. **Render → your service → Environment → add:**

   | Key | Value |
   |---|---|
   | `NEWSLETTER_API_KEY` | the key from step 3 |
   | `NEWSLETTER_HTTP_PROVIDER` | `brevo` / `resend` / `postmark` / `sendgrid` |

   `NEWSLETTER_HTTP_PROVIDER` defaults to `resend`, so **set it explicitly** if
   you signed up anywhere else, or the key is sent to the wrong API.

5. **Manual Deploy → Deploy latest commit.**

6. Open `/admin/newsletter` → **Connections**. It should name your provider and
   say **Connected**. Then press **Send a self-test**: that sends one real
   email through the real path and reports the provider's own words if it
   fails.

**You do NOT have to delete the `SMTP_*` values.** An API key now takes
priority over SMTP automatically. (It did not used to, which is why the old
advice in the error message, "set `NEWSLETTER_API_KEY`", would not have worked
on its own: `SMTP_HOST` was still set and still won.)

---

### 4.1 What happens to everyone who signed up while it was broken

Nothing about the outage is permanent, and there is nothing you need to
remember to do.

* Every welcome email that failed because sending was down is kept **queued**,
  not written off. An outage never counts against a subscriber's retry budget.
* When the server next starts **with a working transport**, it sweeps for
  anyone still owed a welcome and sends it. This runs on its own, at boot.
* Anyone written off by the *older* code (marked `failed` during the outage,
  when that was a dead end) is found and re-queued by the same sweep.
* The dashboard shows **"N people are still owed their welcome email"** with a
  **Send the ones we missed** button, if you would rather watch it happen than
  wait for a restart.
* Addresses that genuinely bounced are left alone. Re-mailing known-bad
  addresses on every restart is what wrecks a sender's reputation.

---

### Option B: SMTP (only on a host that allows it, ~5 minutes)

**This cannot work on Render.** It is kept for a future move to a host that
permits outbound SMTP, and for local testing.

Your domain email is hosted somewhere already. Whoever hosts it will give you
these four values, usually on a page called *IMAP/SMTP*, *Mail client setup*,
or *Email clients*.

**Render → your service → Environment → add:**

| Key | What to put |
|---|---|
| `SMTP_HOST` | e.g. `smtp.gmail.com`, `smtp.zoho.com`, `mail.privateemail.com` |
| `SMTP_PORT` | `587`. Use `465` only if your host says so |
| `SMTP_USERNAME` | almost always `timothy.honey@beardedsealstudios.com` |
| `SMTP_PASSWORD` | the mailbox password, or an **app password**: see below |

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

Note that some providers also offer **port 2525**, which Render does *not*
block. If your provider supports it, `SMTP_PORT=2525` can make SMTP work here.
Gmail does not offer 2525.

---

### Option C: Gmail API over OAuth (optional, not recommended)

Still supported, and `scripts/get_gmail_refresh_token.py` still works if you
ever want it. It sends over HTTPS, so it is *not* blocked by Render either, but
it is the **only** route that needs a Google Cloud project, a consent screen
and scopes, which is why Option A is the recommendation.
Set `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` and `GOOGLE_REFRESH_TOKEN` if
you go this way.

---

### Which one is live?

`NEWSLETTER_TRANSPORT` forces a choice (`http` / `smtp` / `gmail_api`). Left
unset, the first fully-configured option wins, in the order
**HTTP API → SMTP → Gmail API**.

⚠️ **The HTTP API deliberately outranks SMTP.** It used to be the other way
round, and that turned the documented remedy for a blocked host into a no-op:
you would set `NEWSLETTER_API_KEY` exactly as instructed, `SMTP_HOST` would
still win, and mail would stay dead with no indication why. Setting an API key
is a deliberate act, so it takes over. Set `NEWSLETTER_TRANSPORT=smtp` if you
ever genuinely want SMTP while both are configured.

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

> **The admin panel now checks these for you.** `/admin/newsletter` →
> **Settings** → *Domain authentication (spam folder)* does live DNS lookups
> and tells you which of the three are actually published, with the exact
> record to add for anything missing. **Re-check DNS now** re-reads after an
> edit (the result is cached for ten minutes otherwise). That panel is the
> answer to "why is my email going to spam", and it reports what the internet
> currently sees, not what you meant to set up.
>
> **As of 2026-08-22 this domain has SPF ✅ and DKIM ✅ but NO DMARC record.**
> Gmail and Yahoo have required one on bulk senders since February 2024, so
> this is the single highest-value thing left to fix. It is the one record in
> the DMARC table below and it takes about two minutes.

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
Easiest: **Settings → Domain authentication** in the admin panel, then
**Re-check DNS now**. From a terminal:
```bash
dig +short TXT beardedsealstudios.com
dig +short TXT google._domainkey.beardedsealstudios.com
dig +short TXT _dmarc.beardedsealstudios.com
```
Then send yourself a test (§8) and use Gmail's **Show original**, it must say
`SPF: PASS`, `DKIM: PASS`, `DMARC: PASS`.

If your provider signs DKIM with a selector this check does not know about, the
panel says *Not confirmed* rather than *Missing*, on purpose: selectors cannot
be listed from outside, so "not on any name I know" is not the same statement
as "you have no DKIM". Set `NEWSLETTER_DKIM_SELECTOR` to the one your provider
gave you and it will find it.

---

## 7b. The rest of the anti-spam work (already done in code)

DNS is the half you have to do by hand. This is the half the code does, listed
so you know what is already covered and do not go looking for it.

**Every message carries the headers that mailbox providers look for.** They are
built in ONE place (`_deliverability_headers`) so the SMTP path and the HTTPS
API path cannot drift apart:

| Header | Why |
|---|---|
| `List-Unsubscribe` + `List-Unsubscribe-Post` | RFC 8058 one-click. **Required** by Gmail/Yahoo for bulk senders since Feb 2024. |
| `List-Id` | Names the list, so clients file it as subscribed mail instead of guessing. |
| `Feedback-ID` | Google Postmaster Tools groups spam-complaint rates by this, per campaign, instead of one blended number for the whole domain. |
| `X-Entity-Ref-ID` | Stops Gmail collapsing a run of same-subject newsletters into one thread, where they go unread. |
| `Precedence: bulk`, `Auto-Submitted` | Stops out-of-office autoresponders replying to every send. |

**Spam preflight before every send.** The send modal reads the draft the way a
filter will and lists what it finds: shouting subject, more than one `!`, fake
`Re:`, known trigger phrases, image-only body, missing alt text, shortened or
`http://` links, no preview text. It **never blocks a send**, it only advises,
because a false positive that stops your own newsletter is a worse bug than the
spam folder.

**Hard bounces come off the list automatically.** An address that is
permanently rejected (it does not exist) is suppressed on the spot and recorded
as `bounced`, not as an opt-out. A dead address re-mailed on every campaign for
a year is what a bought list looks like from the outside, and providers score
that against every other message the domain sends. A temporary failure (a
timeout, a 4xx) never removes anybody.

**Also true by design:** one recipient per message and no tracking pixel, so
there is nothing in an email that a filter reads as surveillance and no way for
one subscriber to learn that another exists.

Two things nobody can do in code, worth knowing:

* **Warm up.** If this domain has never sent bulk mail, do not send to
  thousands on day one. A few hundred, then grow. A cold domain that suddenly
  sends 2,000 messages looks exactly like a compromised account.
* **Google Postmaster Tools.** Add `beardedsealstudios.com` at
  <https://postmaster.google.com>. It is free, takes five minutes, and it is the
  only place you can see your real spam-complaint rate. Keep it under 0.3%.

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
python3 test_newsletter_server.py      # 189 tests
node   test_newsletter_admin_ui.js     # needs Chrome
```

Then, in order:

### 9.1 Deploy check
```bash
curl -s https://play.currentsandcritters.com/version.json
```
Must show **`1.6.80` / `2026-08-22.7`**. If it doesn't, the deploy hasn't
finished: Render Docker builds take ~10–15 minutes. *"Still broken" has often
meant "never shipped".*

### 9.1b "No email is arriving": start here
Open `/admin/newsletter`. The **Dashboard** now answers this at the top of the
page, before any of the numbers, and there is a **Send me a test email now**
button next to it that pushes a real message down the real transport and shows
you exactly which step failed, in the mail provider's own words.

**By far the most likely cause, and the one that actually happened:**

| Symptom | Cause | Fix |
|---|---|---|
| *"Could not reach smtp.gmail.com:587"*, or a `network` failure that never clears | **Render blocks outbound SMTP (ports 25/465/587).** Correct SMTP credentials cannot help; the connection never leaves the container | Set `NEWSLETTER_API_KEY` (§4 Option A). You do **not** need to remove `SMTP_*` |

The rest, in the order they bite:

| Symptom | Cause | Fix |
|---|---|---|
| Dashboard says *Email sending is not set up* | No transport configured | Set `NEWSLETTER_API_KEY` in Render (§4), redeploy |
| Signup form says *"Email delivery is being set up"* instead of *"a welcome email is on its way"* | Same as above, seen from the public side | Same |
| Self-test fails on *The mail server accepts our login* | Wrong key, or the wrong provider name | Check `NEWSLETTER_HTTP_PROVIDER` matches where the key came from; it defaults to `resend` |
| The provider rejects every message with a sender error | The From address is not verified with them | Verify `NEWSLETTER_FROM_EMAIL` in the provider's dashboard (§4 Option A step 2) |
| Admin page returns **502** on preview / test-send / save | The server was restarting, **or** a blocked SMTP port held the request open past the proxy timeout | Fixed: the SMTP timeout is now short and connection checks are cached, so a dead port returns an error instead of a 502 |

The Render deploy log shouts about a missing transport at boot with a boxed
`!! NO EMAIL TRANSPORT CONFIGURED` banner, so `Ctrl-F` for `[newsletter]` there
is the fastest check of all.

**Nobody is lost while this is broken.** Welcome emails that cannot be sent
stay queued and go out by themselves once a working transport is set: see
§4.1.

### 9.1c The consumer-Gmail trap
`NEWSLETTER_FROM_EMAIL` is `currentsandcritters@gmail.com`, a **free consumer
account**, not an address on beardedsealstudios.com. That matters more than it
looks:

* The real ceiling is about **500 recipients per rolling 24h**, not the ~2,000 a
  Workspace domain gets. Going over does not bounce, Google **suspends sending
  for up to 24 hours**, and while it is suspended nothing goes out at all,
  including the welcome email of everyone who signs up meanwhile. That is the
  most likely way for "no emails are sending" to be true with a perfectly
  correct configuration.
* `NEWSLETTER_DAILY_SEND_CAP` is therefore **no longer pinned in render.yaml**.
  `daily_send_cap()` derives it from the From address (400 consumer / 1200
  domain) exactly so that changing the sender cannot leave an oversized cap
  pointed at an undersized mailbox. Set it only to LOWER the number. **If the
  Render dashboard still has a stored value of 1200, delete it.**
* SPF/DKIM/DMARC for gmail.com are Google's own and always pass, so the Domain
  authentication panel has nothing to fix while this is the sender, and the
  missing DMARC record on beardedsealstudios.com does not affect it.
* Moving the From address to `timothy.honey@beardedsealstudios.com` is the
  biggest single deliverability upgrade available: that domain already has SPF
  and DKIM published and needs only the DMARC record in §7.

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
Sign up at `/newsletter/join` with an address you can read. You should be a
subscriber immediately (the page says so) and the welcome email should arrive
within a minute: **there is no confirmation step, and no confirmation email**.

Check on phone and desktop: logo renders, layout is readable, **Visit Currents
& Critters** works, **Privacy Policy** works, **Unsubscribe** works, business
address is present. View the plain-text part (Gmail: Show original), it must
be readable prose, not stripped tags.

While you have **Show original** open, this is the fastest place to confirm the
anti-spam work end to end:

| Look for | Expected |
|---|---|
| `SPF` / `DKIM` / `DMARC` | `PASS` on all three (see §7) |
| `List-Unsubscribe-Post` | `List-Unsubscribe=One-Click` |
| `List-Id` | `Currents and Critters Newsletter <newsletter.beardedsealstudios.com>` |
| `Feedback-ID` | ends in the sender id, all on one line |
| Where it landed | Inbox, not Promotions and not Spam |

If it lands in spam with all three of SPF/DKIM/DMARC passing, the cause is
reputation rather than configuration: the domain is new to bulk sending. Send
small for a while and add Google Postmaster Tools (§7b).

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
