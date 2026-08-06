# Currents & Critters — Newsletter System

Everything that could be built in code is built and tested. What remains is
account-level setup in Google Cloud, Stripe, Render and your DNS — none of
which I can do for you. Each of those steps is written out below, in the order
you should do them.

**Nothing in this document contains a real secret, and nothing in the repo
does either.** Where a value is secret you will see the variable *name* and the
*shape* of the value, never the value.

---

## 0. What this system does, in one page

A customer completes a Stripe checkout. If — and only if — they typed an
address into the optional **"Enter your email to get updates"** field, Stripe's
`checkout.session.completed` webhook hands that address to the newsletter code,
which creates a subscriber, sends them a welcome email, and emails you to say
somebody joined.

You write and send newsletters at **`/admin/newsletter`**, signed in with your
Google account. Sending happens on the server in controlled batches through the
Gmail API, one individual message per subscriber, each with its own unsubscribe
link. Everything is recorded so a retry, a double-click or a server restart can
never send anyone the same newsletter twice.

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

## 1. Files

### Created

| File | Purpose |
|---|---|
| `newsletter_server.py` | The whole backend: subscribers, the Stripe hook, unsubscribe, drafts, campaigns, the send worker, the audit log, and every admin API route. Wired into `multiplayer_server` the same additive way as `clan_server` / `analytics_server`. |
| `newsletter_email.py` | The only file that talks to Gmail or turns admin HTML into an email: the strict HTML sanitiser, the branded email shell + footer, plain-text generation, MIME building, OAuth token refresh, and the send call. |
| `multiplayer/client/newsletter-admin.html` | The admin page shell. Deliberately contains **no data** — it is a sign-in card until Google auth succeeds. |
| `multiplayer/client/js/newsletter-admin.js` | The admin UI: 8 sections, the composer, previews, confirmations. |
| `multiplayer/client/css/newsletter.css` | Admin styling. Same design tokens as the Developer Analytics dashboard so the two admin tools read as one. |
| `multiplayer/client/unsubscribe.html` | The public unsubscribe confirmation page. |
| `multiplayer/client/email-logo.png` | 144×144 PNG logo for email headers, extracted from `assets/logo-icon.svg` (which is an SVG wrapper around a PNG — and SVG does not render in Gmail or Outlook). |
| `scripts/get_gmail_refresh_token.py` | Run **once, locally**, to turn your OAuth client into a refresh token. |
| `test_newsletter_server.py` | 83 backend tests: consent, idempotency, sanitising, CSV injection, unsubscribe tokens, authorisation, campaigns. |
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
| `multiplayer/client/js/privacy-policy.js` | Newsletter sections updated — **see §9, there is a correction in here you should read**. |
| `multiplayer/client/preview.html`, `privacy.html`, `js/preview-app.js`, `version.json` | Version bumped to **1.6.53 / 2026-08-06.4** and cache-busters bumped with it (project convention: an edited asset must get a fresh `?v=`). |
| `test_privacy_policy.js` | Last-updated date assertion moved to August 6, 2026. |

**Nothing was removed or replaced.** Deleting the newsletter module would
return the game, the store and the payment webhook to exactly their previous
behaviour.

---

## 2. Database

Firestore, using the same service account the rest of the server already uses.
**There are no migrations to run** — Firestore creates collections on first
write. There is nothing to roll back; deleting the collections below removes
the system entirely and touches nothing else.

| Collection | Document id | Holds |
|---|---|---|
| `newsletterSubscribers` | `sha256(emailLower)[:40]` | email, emailLower, status (`active`/`unsubscribed`), source, subscribedAt, resubscribedAt, unsubscribedAt, unsubId, tokenVersion, welcomeEmailStatus/At/Attempts/Error/Kind, stripeSessionId, stripeEventId, consentNote, createdAt, updatedAt |
| `newsletterWebhookEvents` | Stripe event id | eventId, sessionId, subscriberId, result, processedAt |
| `newsletterCampaigns` | auto | subject, previewText, contentHtml (sanitised), status, createdAt/By, startedAt/By, sentAt, intendedRecipients, sentCount, failedCount, skippedCount, interruptedCount |
| `newsletterCampaigns/{id}/recipients` | **subscriber id** | campaignId, subscriberId, email, status, attempts, gmailMessageId, lastErrorCategory, sentAt, leaseUntil, updatedAt |
| `newsletterAudit` | auto | action, at, atIso, admin, subscriberId, campaignId, summary, correlationId |
| `newsletterMeta/stripeFieldLabels` | fixed | The custom-field **labels** last seen on a checkout (label text only, never an answer) — the diagnostic in §8. |

**Indexes:** none to create. Every query is a single-field equality, which
Firestore indexes automatically. The subscriber list is searched, filtered,
sorted and paginated in Python over one cached scan — deliberately, so you
never have to create a composite index per (status, sort) pair, and never hit a
500 the first time one is missing. The honest ceiling is 20,000 subscriber
records; past that the list truncates and the UI says so.

**Firestore security rules:** no change needed. Every one of these collections
is written and read only by the server through the Admin SDK, which bypasses
rules. No browser ever reads them directly.

---

## 3. Google Cloud + Gmail API setup

Do this as **timothy.honey@beardedsealstudios.com**.

### 3.1 Project and API
1. <https://console.cloud.google.com> → pick or create a project (you can reuse
   the Firebase project — a Firebase project *is* a Google Cloud project).
2. **APIs & Services → Library** → search **Gmail API** → **Enable**.

### 3.2 OAuth consent screen
3. **APIs & Services → OAuth consent screen**.
4. User type: **Internal**.
   *Internal is available because beardedsealstudios.com is a Google Workspace
   domain, and it is the right answer: an Internal app needs no Google
   verification review and no 100-user cap, because only accounts in your own
   Workspace can ever authorise it.*
   If Google will not offer Internal, your account is not on a Workspace
   domain — see §3.5.
5. App name `Currents & Critters Newsletter`, user support email
   `timothy.honey@beardedsealstudios.com`, developer contact the same.
6. **Scopes → Add or remove scopes**, add exactly:
   - `https://www.googleapis.com/auth/gmail.send`
   - `openid`
   - `.../auth/userinfo.email`

   `gmail.send` is send-only — it cannot read a single message in your mailbox.
   `openid`/`email` exist so the server can confirm *which* account it is
   authorised as without asking for `gmail.readonly`. **If the consent screen
   offers to add anything else, say no.**
7. Save.

### 3.3 OAuth client
8. **APIs & Services → Credentials → Create credentials → OAuth client ID**.
9. Application type: **Web application**. Name: `Newsletter sender`.
10. Under **Authorised redirect URIs** add **exactly** this — trailing slashes
    and `localhost` vs `127.0.0.1` both matter:

    ```
    http://127.0.0.1:8765/oauth2callback
    ```

    This is a local, one-time URI used only by the helper script in §3.4. It is
    never used by the live server, which is why the production service never
    exposes an OAuth callback that could mint sending credentials.
11. **Create.** Copy the **Client ID** and **Client secret**.

### 3.4 Generate the refresh token

On your laptop, in the repo:

```bash
python3 scripts/get_gmail_refresh_token.py
```

Paste the client id and secret when asked, then click **Allow** in the browser
as `timothy.honey@beardedsealstudios.com`. It prints:

```
GOOGLE_REFRESH_TOKEN = 1//0g...
```

It also prints **which account** was authorised — that must match
`GMAIL_SENDER_EMAIL` exactly, or Gmail will refuse to send.

- The token is printed to your terminal and nowhere else. It is not written to
  a file and never logged.
- Treat it like a password. Revoke any time at
  <https://myaccount.google.com/permissions>.
- If it says *"Google did not return a refresh token"*, the account has
  authorised this client before — remove it at that same URL and re-run.

### 3.5 If Internal is not offered
Your sending account is not on a Workspace domain. The system still works with
an **External** app in *Testing* mode with your own address added as a test
user, but Google expires refresh tokens on testing-mode apps after **7 days**,
so you would have to re-run the script weekly. Getting the domain onto Google
Workspace is the real fix.

---

## 4. Stripe setup

The webhook endpoint **already exists and is already configured** — the
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

### 4.2 ⚠️ The one thing you must verify — the field label

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
email" — consent has to be legible in the question itself.

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
> label — see §9.

---

## 5. Render environment variables

**Render → your service → Environment.** Add each of these, then **Manual
Deploy → Deploy latest commit** (Render restarts on env changes, but a deploy
is the reliable way to be sure).

| Variable | Secret? | Purpose | Example shape | Where you get it | Redeploy? |
|---|---|---|---|---|---|
| `ADMIN_EMAIL` | No | The **only** Google account that can open `/admin/newsletter` | `timothy.honey@beardedsealstudios.com` | You | Yes |
| `NEWSLETTER_UNSUBSCRIBE_SECRET` | **Yes** | Signs unsubscribe links. **Required** — campaign sending refuses to start without it | 64 random urlsafe chars | `python3 -c "import secrets;print(secrets.token_urlsafe(48))"` | Yes |
| `GOOGLE_CLIENT_ID` | No (but don't publish) | Gmail OAuth client | `1234567890-abc.apps.googleusercontent.com` | §3.3 | Yes |
| `GOOGLE_CLIENT_SECRET` | **Yes** | Gmail OAuth client | `GOCSPX-xxxxxxxxxxxxxxxx` | §3.3 | Yes |
| `GOOGLE_REFRESH_TOKEN` | **Yes** | Long-lived Gmail send authorisation | `1//0gXXXXXXXXXXXXXXXX` | §3.4 | Yes |
| `GOOGLE_REDIRECT_URI` | No | Recorded for reference only; the live server never uses it | `http://127.0.0.1:8765/oauth2callback` | §3.3 | No |
| `GMAIL_SENDER_EMAIL` | No | The From address. **Must equal the authorised account** | `timothy.honey@beardedsealstudios.com` | You | Yes |
| `GMAIL_SENDER_NAME` | No | From display name | `Currents & Critters` | You | Yes |
| `APP_BASE_URL` | No | Where unsubscribe links point | `https://play.currentsandcritters.com` | You | Yes |
| `CURRENTS_AND_CRITTERS_URL` | No | The "Visit" button + email logo host | `https://currentsandcritters.com` | You | Yes |
| `PRIVACY_POLICY_URL` | No | Footer link | `https://currentsandcritters.com/privacy` | You | Yes |
| `NEWSLETTER_DAILY_SEND_CAP` | No | Messages/day this process will send | `1200` | You | Yes |
| `NEWSLETTER_FIELD_LABEL` | No | Extra accepted Stripe labels, `|`-separated | `Enter your email to get updates` | §4.2 | Yes |
| `STRIPE_WEBHOOK_SECRET` | **Yes** | *Already set* — reused, do not change | `whsec_…` | Stripe | — |

⚠️ **Changing `NEWSLETTER_UNSUBSCRIBE_SECRET` invalidates every unsubscribe
link already sitting in someone's inbox.** Set it once and leave it alone.

**No Render worker, cron job or background service is needed.** The send worker
is a daemon thread inside the existing web service. That works because the
service has a mounted disk, which forces Render to run exactly one instance —
and campaign state lives in Firestore, so a restart resumes rather than
restarts.

---

## 6. DNS: SPF, DKIM, DMARC

Without these, Gmail and Outlook will junk your newsletters. Do all three, at
your DNS provider for **beardedsealstudios.com**.

### SPF
One TXT record at the root. **If you already have an SPF record, edit it — a
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
`p=reject`. Do not start at `p=reject` — if anything is misconfigured, your
mail disappears silently.

### Verify
```bash
dig +short TXT beardedsealstudios.com
dig +short TXT google._domainkey.beardedsealstudios.com
dig +short TXT _dmarc.beardedsealstudios.com
```
Then send yourself a test (§8) and use Gmail's **Show original** — it must say
`SPF: PASS`, `DKIM: PASS`, `DMARC: PASS`.

---

## 7. Gmail sending limits — the real numbers

- A Google **Workspace** account may send to at most **2,000 recipients per
  rolling 24 hours** via the API (1,500 external). A consumer `@gmail.com`
  account is **500**.
- One message to one subscriber = one recipient. This system never puts more
  than one address on a message, so **your list size is your daily limit**.
- `NEWSLETTER_DAILY_SEND_CAP` defaults to **1,200** to leave headroom for
  welcome emails, test sends and your ordinary human email on the same account.
- Exceeding the limit does not bounce — Google returns 429 and can **stop the
  account sending for up to 24 hours**. That is why the cap is enforced before
  the wire.
- If the cap is hit mid-campaign, the campaign **pauses** and resumes
  automatically after 00:00 UTC. Nobody is dropped and nobody is sent twice.

**When your list passes ~1,500, move to a dedicated bulk provider** (Amazon
SES, Postmark, Mailgun). Gmail is not a bulk sender and Google will eventually
treat it as abuse. `newsletter_email.send_email()` is the single function to
swap; nothing else in the system knows what Gmail is.

---

## 8. Testing

Run the automated suites first — they need no accounts and no network:

```bash
python3 test_newsletter_server.py      # 83 tests
node   test_newsletter_admin_ui.js     # needs Chrome
```

Then, in order:

### 8.1 Deploy check
```bash
curl -s https://play.currentsandcritters.com/version.json
```
Must show **`1.6.53` / `2026-08-06.4`**. If it doesn't, the deploy hasn't
finished — Render Docker builds take ~10–15 minutes. *"Still broken" has often
meant "never shipped".*

### 8.2 Gmail connection
Open `/admin/newsletter` → **Connections**. You want:
- Gmail sending: **Connected**
- Authorised account: your address, and it must match the From address
- Granted scopes: `gmail.send`, and nothing broader
- Unsubscribe links: **Ready**

Any failure states exactly what to fix.

### 8.3 Admin authorisation
| Test | Expected |
|---|---|
| Open `/admin/newsletter` signed out | Sign-in card only. No data. |
| Sign in as `timothy.honey@beardedsealstudios.com` | Full admin |
| Sign in as any other Google account | "not authorised", no data |
| `curl -X POST https://play.currentsandcritters.com/api/newsletter/subscribers -H 'Content-Type: application/json' -d '{"idToken":"x"}'` | `403 {"ok":false,"error":"unauthorized"}` |
| Same with `-d '{"email":"timothy.honey@beardedsealstudios.com"}'` | Still 403 — body claims are never trusted |
| Repeat a failed call 40× | `429` |

### 8.4 Stripe signup (use TEST mode)
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

### 8.5 Welcome email
Check on phone and desktop: logo renders, layout is readable, **Visit Currents
& Critters** works, **Privacy Policy** works, **Unsubscribe** works, business
address is present. View the plain-text part (Gmail: Show original) — it must
be readable prose, not stripped tags.

### 8.6 Test email
Compose → **Send Test Email**. It arrives at your address only, subject
prefixed `[TEST]`, with a yellow TEST banner and **no live unsubscribe token**.
Click it three times fast — exactly one email arrives.

### 8.7 A safe mass-send rehearsal
**Do this before you ever send to the real list.**

1. Subscribers → **Add subscriber** → 2–3 addresses you own (tick the
   permission box).
2. Unsubscribe every real subscriber temporarily, *or* do this on a Render
   preview service pointed at a scratch Firebase project. **Do not skip this
   step** — there is no "cancel send" once a campaign starts.
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

### 8.8 Unsubscribe
| Test | Expected |
|---|---|
| Click the link in a real email | "You have been unsubscribed…" |
| Click it again | Same friendly page, no error |
| Change one character of the token | Same page, **nobody is unsubscribed** |
| Gmail's own **Unsubscribe** button (top of the message) | Works — one-click, RFC 8058 |
| Send a new campaign | The unsubscribed person is not in it |
| They buy again and re-enter their address | Reactivated, welcome email sent again, old link now dead |

### 8.9 Sanitising
Compose, paste `<script>alert(1)</script><img src=x onerror=alert(1)>` plus
some normal text. Save. Preview and test-send. The script and handler are
gone; your text remains.

---

## 9. ⚠️ Things for you to review

### 9.1 The Privacy Policy quoted a label that cannot exist
Your policy said the checkout field is labelled:

> "Enter your email to join the Currents & Critters newsletter and receive
> occasional updates."

That is **89 characters**, and **Stripe caps a custom-field label at 50**. So
that cannot be what the live Payment Links actually ask. I have changed the
policy to quote the label you gave me — **"Enter your email to get updates"** —
and added explicit sentences that leaving it blank, completing a purchase, or
giving an email for a receipt do **not** subscribe anyone.

**Please confirm the live label in Stripe matches** (§4.2), and read the
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

- A CSRF attack works because cookies are **ambient** — the browser attaches
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

## 10. Limitations and remaining risks

1. **Gmail is not a bulk mailer.** Fine to ~1,500/day. Past that, move to SES
   or Postmark (§7).
2. **A crash mid-send leaves a few "interrupted" recipients.** When the process
   dies between handing a message to Gmail and recording it, the outcome is
   genuinely unknown. Rather than guess, those are marked *interrupted* and
   **never auto-resent** — Sending Progress shows them with an explicit
   **Retry** button. This is the deliberate trade: a handful of manual clicks
   instead of a chance of double-sending your whole list.
3. **20,000 subscriber ceiling** on the admin list view (§2). Well beyond
   Gmail's practical limit anyway.
4. **Rate limiting is per-process**, not distributed. Correct for this service
   (one instance, mounted disk); it would need Redis if you ever scale out.
5. **The Stripe label is a behaviour key.** §4.2. The Connections tab makes a
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

## 11. Deploying

```bash
git push                     # Render auto-deploys from main
```

- **Render (game + newsletter):** ~10–15 min for a Docker build.
- **Vercel (marketing site):** ~1 min.
- Confirm with `curl -s https://play.currentsandcritters.com/version.json`.

**Rollback:** `git revert <commit> && git push`. The Firestore collections are
additive — nothing else reads them, so leaving them in place after a revert is
harmless.
