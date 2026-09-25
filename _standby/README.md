# Off standby

Everything that was taken off the live site on 2026-09-08 came back on
**2026-09-24**. The Store, the Critter Pass, the Supporter Reef Wall, the
homepage tiers, the donation goal, the Partner With Us form and the shop page
are all live again, and both pages are back on the sidebar.

**The one thing still deliberately not for sale: the four SUPPORTER TIERS.**
They render in full on both surfaces — every price, every perk — and neither
surface has a checkout on it, because they are being backed through the
Kickstarter. That is a display lock, not a refusal; see section 1.

The archive in `website/` is kept. It is where the homepage pastes came from,
and it is the fastest way to see what a band looked like before it was edited.

---

## What is live again, and what switched it back on

| what | switch | now |
|---|---|---|
| Both pages on the sidebar | `PH_CLOSED_TABS` in `js/preview-app.js` | `[]` |
| …and their sidebar buttons | `preview.html` `<nav>` | uncommented |
| The four menu-tour steps | `gtOnStandby` in `js/tutorials.js` | `() => false` |
| The in-game Store | `PHST_STORE_CLOSED` in `js/preview-app.js` | `false` |
| The Critter Pass | `CCCP_PASS_CLOSED` in `js/critter-pass.js` | `false` |
| The guest note on the Pass | `GUEST_NOTES.critterpass` | pasted back verbatim |
| The Supporter Reef Wall | `SUPPORTER_WALL_ON_STANDBY` in `multiplayer_server.py` | `False` |
| `/supporter-wall` | `multiplayer/client/supporter-wall.html` | restored from `13-*.original` |
| The wall links | `thanks.html`, `claim-rewards.html` | restored |
| `shop.html` | the whole file | restored from `12-shop.html.original` |
| The homepage bands | `index.html` | files 01, 03–08 pasted back |
| The homepage scripts | `index.html` | files 09–11 pasted back |
| The Luckiest Guy webfont | `index.html` `<head>` | three `<link>` tags back |
| The three Partner links | nav, footer, Our Story band | back |
| `score.html` / `shop.html` nav | `Sponsor`, `Buy the Game →` | back |

**The homepage stats band is FOUR numbers**, not the old four and not the
three that replaced them:

| shown | comes from |
|---|---|
| **$ Donated** | `refreshSupporterWall` → the sum of exactly the wall's own rows |
| Players | `/api/stats` → `players_total` (accounts **plus** guests) |
| **Hours Played Online** | `/api/stats` → `play_seconds`, divided by 3600 on the page |
| Online Games Played | `/api/stats` → `games_played` |

Hours Played Online was built *while* the tiers were off, and pasting the old
four-stat band (`02-impact-band.html`) back verbatim would have dropped it and
restored `Players Online Now`, which nothing fetches any more. So the band was
rebuilt from both: the donation tile from the archive, the three play numbers
from the live page. `test_stripe_payments.py` pins all four.

---

## 1. The Supporter Tiers: shown everywhere, sold nowhere

Two switches, one on each surface. Both are display locks: they stop us
**asking** for the money, they do not stop the webhook honouring it.

**The in-game Store** — `multiplayer/client/js/preview-app.js`:

```js
const PHST_TIERS_KICKSTARTER_ONLY = true;   // ← false puts the four buttons back
```

`renderPhStore()` reads it per card: `Become a <tier>` becomes a disabled
`On Kickstarter soon` button carrying no `data-stripe`, and a line under the
grid says where they will be. The four live Payment Links are untouched in
`PHST_SUPPORTER_TIERS`.

**The website** — `index.html`. Each of the four cards ends in

```html
<button class="btn btn-outline tier-locked" type="button" disabled>On Kickstarter soon</button>
```

and its live `<a href="https://buy.stripe.com/…">` sits in the comment directly
above it, verbatim, so opening the checkout is a paste. `.tier-locked` is in
the inline styles. The note under the grid is `.tier-ks-note`.

⚠️ **A COMMENTED-OUT LINK IS THE WHOLE GUARANTEE HERE.** An `<a href>` to one
of those four URLs is a live checkout no matter what the note under the grid
says. `test_stripe_payments.py` strips every HTML comment out of the page
(`_live()`) before looking, so a link that escapes its comment fails loudly.

⚠️ **THE WEBHOOK STILL HONOURS ALL FOUR PRICES, ON PURPOSE.** A tier is granted
by the `amount_total` of the session, never by the URL. Someone with an old tab,
a saved link or a link we mailed can still pay, and that money must still buy
exactly what it always bought. Locking the button is not cancelling the tier.

**Above $100 there is still no button anywhere**, unchanged: both surfaces hand
the reader a pre-written message instead (`CUSTOM_TIER_MIN_CENTS`).

---

## 2. Every purchase makes waves: the 5% pledge

The top of the in-game Store, above the first section title, and the same two
blocks above the homepage tier cards:

> **Kickstarter coming soon**
>
> **Every Purchase Makes Waves!**
> 5% of every purchase supports ocean conservation, including donations to the
> Surfrider Foundation. Together, we can Change the Tide!
>
> Currents and Critters is an independent supporter and is not sponsored by or
> officially partnered with the Surfrider Foundation.

The percentage is **one constant**, `CONSERVATION_SHARE_PCT` in
`multiplayer_server.py`, and `test_donation_report.py` fails if either page
prints a number that disagrees with it.

Styles: `.phst-ks-banner` / `.phst-waves*` / `.phst-tier-ks-note` in
`css/preview.css`; `.tier-ks-banner` / `.tier-waves*` / `.tier-ks-note` /
`.tier-locked` in `index.html`'s inline styles.

**The whole site says 5% now.** The pledge used to read "2% goes to ocean
conservation" on the hero, the value props, the sponsor head, Our Story, the
footer, `shop.html`, `rules.html`, `about.html` and `privacy.html`. All 17 of
those were swept to 5% on 2026-09-24 along with the new banner, so no page
promises a different number from any other. The two archived homepage bands
(`website/01-*`, `website/07-*`) were swept too, so a future paste cannot
quietly reintroduce 2%.

⚠️ If the pledge ever changes again, it is `CONSERVATION_SHARE_PCT` **and** that
sentence on nine pages. Grep `goes to ocean conservation` and change every hit,
or the site will promise two numbers at once.

---

## 3. What came in this month, and the 5% it owes

    python3 donation_report.py              # this month, UTC
    python3 donation_report.py 2026-08      # a named month
    python3 donation_report.py --json

Reads `/api/admin/donations` (behind `ADMIN_RECOVERY_KEY`, same key as the
supporter review list), which sums the **payments themselves** out of every
`supporters/*/payments` and `guestSupporters/*/payments` subcollection — not the
lifetime totals the wall is sized from, because a lifetime total cannot say
which month the money arrived in.

Three things it is careful about, each with a test:

- **Double counting.** Claiming a guest's payments copies each one onto the
  supporter doc under the same Stripe session id and leaves the guest row marked
  `claimed`. Dedup is by payment id across both collections, and claimed guest
  rows are skipped as well.
- **Under-donating.** The 5% is rounded **up** to the cent. A pledge is a
  promise, so the arithmetic errs towards a cent more, never a cent less.
- **Silence.** An unsettled payment (Stripe `payment_status` not `paid`) and a
  payment with no timestamp are both **reported** rather than quietly dropped,
  and a Firestore outage is an error, never a `$0` that reads as "donate
  nothing".

Every figure is UTC and labelled UTC: the server runs in UTC, Stripe stamps in
UTC, and a window that moves with whoever ran the report cannot be reconciled
against either.

---

## 4. Removed for good: the Summer Skin Hermit Crab

Not on standby — **deleted**, on 2026-09-24. It came out of three places that
have to agree, and `test_prestige_server.py` holds the first two equal:

- `ANIMAL_AVATARS` in `multiplayer/client/js/preview-app.js`
- `AVATAR_UNLOCK_TYPES` in `prestige_server.py`
- `PAID_IDS` in `test_guest_access.js`

`/avatars/summer-skin-hermit-crab.png` is **left on disk on purpose**, so it
still resolves for anyone already wearing it. The V1.6.5 changelog entry still
names it, because a changelog records what shipped at the time.

The other two Summer Skins (Gull, Goby) are untouched, and the Gull is still
Level 100 of the Critter Pass (`FINALE_AVATAR`).

---

## Putting it back on standby

Every switch above, reversed. The order that matters: flip
`PHST_STORE_CLOSED` / `CCCP_PASS_CLOSED` / `SUPPORTER_WALL_ON_STANDBY` **first**,
then take the two sidebar buttons out and fill `PH_CLOSED_TABS`, so the pages
are shut before they are hidden rather than after.

The homepage bands come out to `website/` as files 01–11 again; the CSS for all
of them is deliberately left in `index.html` either way, so it is always a paste
and never a rewrite.

## Tests

    python3 test_stripe_payments.py      # 131: the restore, and the tier lock on both surfaces
    python3 test_donation_report.py      #  24: the month window, the 5%, the dedup
    python3 test_warm_cache.py           #  50: the wall poller is gated again
    python3 test_prestige_server.py      #  74: the avatar tables still match
    node test_supporter_tiers_ui.js      # 159: the shelf, rendered three ways
    node test_critter_pass_ui.js         # 258: the page open, and one word from shut
    node test_guest_access.js            #  92: a guest walks the Store and the Pass again
    node test_tutorials.js               # 191: the four restored menu-tour steps
    node test_partner_form.js            #  66: gates on the form's own id, no edit needed

`test_closed_pages.js` is **deleted**. Every check in it asserted that one of
the two pages was unreachable, so there was nothing in it to re-point;
`test_guest_access.js` now walks both panels in a real browser instead.

`test_supporter_tiers_ui.js` renders the shelf three ways — as shipped, forced
shut, and with the tier lock off — so the standby cover and the four Payment
Links are both kept honest while nothing points at either.

`test_critter_pass_ui.js` does the same with two renders. ⚠️ Its
forced-closed source is built with a **line-anchored** regex, and it has to be:
the comment above the declaration spells out both settings, so a plain substring
replace rewrites the comment, leaves the real `const` alone, and the "closed"
iframe silently measures the open page and reports it shut.

Everything here is also in git, in the commit that restored it and its parent.
