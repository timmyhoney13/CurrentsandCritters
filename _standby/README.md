# On standby

Everything in here was taken off the live site on 2026-09-08 and is meant to
come back. Nothing was deleted: the markup, the styles and the scripts are all
either still in the files they came from or archived verbatim beside this note.

**The one-line version:** the site and the in-game Store take no money right
now, the Critter Pass page is shut with it, the Supporter Reef Wall is off, and
the homepage shows three live play numbers where the donation total and the
tiers used to be.

---

## What is switched off, and where the switch is

### 1. The in-game Store — one word

`multiplayer/client/js/preview-app.js`

```js
const PHST_STORE_CLOSED = true;     // ← flip to false to open the Store
```

`renderPhStore()` reads it, paints a single "Coming soon" panel and returns
before it builds anything else, so the shelf emits no button at all: nothing
that charges a card (Critter Coin packs, Supporter Tiers, the physical game)
and nothing that spends Critter Coins either (skins, backgrounds, Player
Perks). Coins, skins and backgrounds people already own are untouched, and
every coin-spend elsewhere in the app — the Critter Pass, the streak-shield
prompt — still works.

The packs, the tiers and their **live Stripe Payment Links are all still in the
file**, unchanged and still correct. `test_supporter_tiers_ui.js` renders the
shelf twice on every run — once as shipped, once with the flag forced `false` —
so the shelf on standby is proved right while it is switched off.

Styles: `.phst-closed*` in `multiplayer/client/css/preview.css`.

`test_closed_pages.js` opens it the way a player does (see the Critter Pass
section below, which covers both).

### 2. The Critter Pass — one word

`multiplayer/client/js/critter-pass.js`

```js
const CCCP_PASS_CLOSED = true;      // ← flip to false to open the Critter Pass
```

`render()` reads it, paints a single "Coming soon" cover and returns before it
builds the reward rail, the purchase card, the header or `wire()`, so the page
emits no button at all: nothing that spends 4,000 Critter Coins, nothing that
spends a Season Pass voucher, and nothing that claims a tier. The page keeps
its kelp forest and its scrim, so the notice sits **over** the page's own art
rather than replacing it with a blank card, but there is nothing behind it —
the rail is never built, so there is nothing to tab into either.

`buyPass()`, `redeemVoucher()`, `claimTier()` and `claimAll()` each refuse at
their first line as well. Nothing draws a button that reaches them, so those
guards are unreachable through the page; they are there because this module
hangs its entry points off `window`, and a page that can only be trusted while
its own markup is intact has not really been switched off.

**The sidebar badge is held at zero** (`paintNavBadge`). A red "3 ready to
claim" that opens a page with no Claim button on it is worse than no badge.

**The guest note is out of `preview-app.js`.** `GUEST_NOTES.critterpass` said
"This is the whole Critter Pass at your level. Buying and claiming it needs an
account." That stopped being true, and it is not only a sentence:
`_ensureGuestNote` inserts a **Sign in button** into the panel, which would
have been the one clickable thing left on the page. The line is written down
verbatim in the comment that replaced it.

**This closes the page, not the pass.** Anyone who already owns it keeps it:
`__ccPassExtraSlots()` still hands the challenge strip their extra daily and
weekly slots, `__ccCritterPassOwned()` still answers true, and every perk
already paid for still works everywhere else in the game. Unclaimed tiers stay
unclaimed **on the server** and are still there to claim on the day this flips
back. Nothing is revoked and nothing expires.

**`critter_pass_server.py` is deliberately unchanged.** It still serves the
track and still honours `/api/critterpass/buy` and `/claim`, the same way the
Store's own standby leaves its live Payment Links in place: this is the page
being taken down, not the pass being cancelled. `__ccCritterPassSync()` is
still allowed to run, because that is what keeps the extra-slot counts fresh
for the people who own the pass, and the test proves a sync spends nothing.
If the intent ever changes from "not yet" to "not at all", the switch for that
is server-side, the way `SUPPORTER_WALL_ON_STANDBY` is.

Styles: `.ccCP-is-closed` and `.ccCP-closed*` in
`multiplayer/client/css/critter-pass.css`.

**Both closed pages are also opened the way a player opens them**, by
`test_closed_pages.js`: it boots the real app in a real browser, clicks the
real sidebar item for each, and audits **the whole panel** rather than the
module's own root. That is a different question from the ones the two suites
below answer, and it catches a different kind of mistake — `switchTab` does
more than render, and for a guest it inserts a note carrying a **Sign In
button** into the panel. A page can be perfectly empty and still be handed a
button by the thing that opened it.

It reads each element's real `tabIndex` instead of matching a list of tags, so
a focusable div, an `<a href>` or a stray `onclick` fails it too; it opens each
page twice, through the sidebar and through `_switchPhTab` (the deep-link
door), so a cover that only survives the first paint is caught; it dispatches a
click at every element in the panel and checks that nothing navigates and
nothing is sent; and it asserts that neither page has a horizontal `.ph-tab`,
so if one ever gains a third door, that is a door this suite is not opening and
it says so. It also checks both pages still **open** — "nothing to interact
with" is one mistake away from "nothing at all".

`test_critter_pass_ui.js` renders the page twice on every run — once exactly as
shipped, in its own frame, driven with the OWNER payload (the state with the
most to click on: a Claim on every reached tier, a Claim-all and a counting
badge), and once with the flag forced `false` at all five widths — so the page
on standby is proved shut while the 977 lines waiting behind it are proved
still right. The closed frame counts buttons, links **and** anything focusable,
because "no `<button>`" alone would miss a link or a stray `tabindex`.

---

### 3. shop.html — the website storefront

The quantity picker, **Add to Cart**, **Buy Now**, the toast and the whole
`<script>` that drove them are gone; the page has no button and no script left
on it. The head reads *Coming Soon*, the stock line says it is not on sale yet,
and a `.shop-closed` notice sits where the controls were.

Restore: `_standby/website/12-shop.html.original` is the whole file as it was.
(The buttons never took a real payment — they only raised a toast — but a shop
that looks open is a shop people expect to order from.)

### 4. The Supporter Reef Wall — one word, server-side

`multiplayer_server.py`

```python
SUPPORTER_WALL_ON_STANDBY = True    # ← flip to False to put the wall back up
```

`/api/supporters/wall` answers `{"ok": true, "standby": true, "supporters": [],
"totalRaisedCents": 0}` and never reaches Firestore. Off at the source, not
just hidden in the page that draws it: a wall taken off the site whose names
are still one fetch away has not been taken off the site.

`/supporter-wall` (and its `/wall` and `/reef-wall` aliases) still answers
**200** with a short "the wall is resting" notice. Those URLs are printed on
the thank-you page every past buyer has already seen, so a 404 there is a worse
answer than "not right now". The page's legend, grid and renderer are gone;
the original file is `13-supporter-wall.html.original`.

**Only the display is off.** The webhook still records every supporter, still
reads their wall name off the checkout, still keeps their lifetime total and
still resolves the tier and the size their name will be
(`_supporter_tier_for_total`). The reef comes back with everyone on it, at the
size they earned, including anyone who gives while it is resting.

**The admin review page is deliberately unaffected.** `supporter-admin.html`
reads `/api/admin/supporters`, a different endpoint behind an `ADMIN_EMAIL`
check, so names can still be approved while nobody can see the wall. Its
"Public wall ↗" link is left in place on purpose — the admin is the one person
who should be able to see what the public wall currently says.

Also removed: the **View the Supporter Reef Wall** link on `thanks.html` and on
`claim-rewards.html`, and the claim page's promise to "place you on the
Supporter Reef Wall (pending approval)" now says the placement is recorded
ready for when the wall is back.

### 5. index.html — the tiers, the donation goal, the wall band, the form

Removed from the page and archived here, in the order they appeared:

| file | what it is | where it went back into |
|---|---|---|
| `01-supporter-reef-wall.section.html` | the "Backed By People Who Care" band | between the Clan Prize band and How to Play |
| `13-supporter-wall.html.original` | the standalone `/supporter-wall` page | replaces `multiplayer/client/supporter-wall.html` |
| `02-impact-band.html` | the **old four** stats, incl. `$ Donated` and `Players Online Now` | top of `#sponsor` |
| `03-donation-goal.html` | the `$X / $25,000` bar | under the stats |
| `04-tier-intro.html` | "★ Supporter Tiers / Every tier makes a ripple." | under the goal bar |
| `05-supporter-tiers.html` | all four tier cards | under the intro |
| `06-custom-tier-card.html` | "Giving more than $100?" | under the cards |
| `07-tier-fineprint.html` | the cosmetic-rewards fine print | under that |
| `08-partner-form.html` | the whole Partner With Us form band | under the fine print, above Our Story |
| `09-donation-goal.js.txt` | `renderDonationGoal()` + its first call | in the live-stats IIFE, after the stat seeding |
| `10-supporter-wall.js.txt` | the wall fetch, renderer and tap handlers | same IIFE, after `pollWhileVisible(refreshRenderStats, …)` |
| `11-partner-form.js.html` | the form's whole `<script>` block | just above `</body>` |

**Kept in place on purpose** (so restoring is a paste, not a rewrite):

- every CSS rule for all of it — `.tiers`, `.tier*`, `.donation-goal*`,
  `.people-care*`, `.scn-name*`, `.partner-form-band`, `.pf-*`;
- `partner_contact.py` and its `/api/partner/contact` endpoint, still live and
  still tested. Nothing in the client posts to it right now.

**Also removed, and easy to miss on the way back:**

- the three `<link>` tags for the **Luckiest Guy** webfont in `<head>`. Its only
  reader was the wall's donor names, so it was costing every visitor a Google
  Fonts round trip for a face nothing rendered in. The `.scn-name` rules that
  need it are still in the stylesheet, with a note pointing here.
- the **`Partner` nav link** (now `Our Story`), the footer's **Partner With Us**
  link, and the **Partner With Us →** button in the Our Story band. They all
  pointed at `#partner-form`.
- `score.html` and `shop.html` both had a **Buy the Game →** nav CTA pointing at
  `shop.html`; both now point at the free online game. Their `Sponsor` nav link
  is now `Our Story`.

---

## What replaced it

The homepage stats band is **three live numbers** instead of four:

| shown | comes from |
|---|---|
| **Players** | `/api/stats` → `players_total` (accounts **plus guests**) |
| **Hours Played Online** | `/api/stats` → `play_seconds`, divided by 3600 on the page |
| Online Games Played | `/api/stats` → `games_played` (unchanged) |

All three go through `sync_totals_from_history()`, which rebuilds the totals
from the saved `game_*.json` records:

- **every finished game** adds `counted_play_seconds(record)` to
  `site_stats.json` and to the Firestore counter, in the same place the games
  counter is bumped. `game_counts_as_played()` decides whether it counts at
  all — the same bar the leaderboard uses, so the increment and the rebuild
  cannot disagree about what a game is, which they used to;
- a game's `duration_sec` is wall clock, so a room left open records 40, 71 or
  114 hours for an ordinary 47-round game. `MAX_COUNTED_GAME_SECONDS` (4h) caps
  each game's contribution. Nine saved games claimed 255 hours between them
  before this; the same nine are worth 28;
- **on boot and whenever a player boots the game** (`/api/health`), the rebuild
  runs again, throttled to 30s and skipped entirely unless the history
  directory's fingerprint has changed. It used to run only at server startup,
  so on a box that stays up for weeks the figure was as old as the deploy.
  Hours follow the rebuild **in both directions**; games only ever climb.
- an **empty or unreadable** history directory returns `None`, meaning "no
  information", never 0. The live server's directory is empty, and read as
  "nobody has played" it would wipe the totals Firestore is holding.

Guests are counted too. They never sign up, so `/api/user/register` never hears
about them and they were missing from the public player number entirely. The
client sends one random per-session token with its `/join`
(`ccGuestPlayToken()`, sessionStorage so it dies with the sitting), the server
stamps it on the seat, and `record_guest_players()` counts it **once, ever** at
the end of a game that was actually finished. There is deliberately **no
endpoint** that takes somebody's word for a guest: a guest has no account to
verify, so an open counter would be a curl loop away from printing anything it
was told, which is exactly what happened to registered players before that
endpoint required a verified token.

**The real totals live on the accounts.** Every account carries its own games
and hours in Firestore (`stats.completed_games`, `stats.hours_played`, and the
`*_games_by_size` maps), and the site was not reading them: it published 107
games and 0 hours while one account's own Player Home showed 155 games and 290
hours. `get_player_stat_totals()` sums them across every account and those
totals **outrank** anything derived on the server, because they have been
counting since long before the server kept a single duration. The per-account
games rule is copied from the client (`Math.max(completed_games, byNormal +
byComp)`) so the site total and the pages it sums cannot disagree. The two
sources are never ADDED - a game counted on the server was counted on the
account too. `/api/stats` publishes `account_games`, `account_play_seconds` and
`accounts_counted` alongside the headline.

⚠️ That scan is one Firestore read per account per refresh, against a free tier
that ran out once already. Hence `PLAYER_TOTALS_TTL_SEC` (30 min), a cache with
`keep_warm_window=0` so no sweeper rescans the collection on a timer, and
`PLAYER_TOTALS_MAX_ACCOUNTS`. Nobody can tell a half-hour-old lifetime total
from a live one.

**The games nobody timed.** The hours counter was added long after the games
counter, and the time those earlier games took was never recorded anywhere -
the history files are gone from the live disk, and the per-player copies in
Firestore keep only the moment a game *ended* (`t: Date.now()`), never how long
it ran. So the page said "101 games played" and "0 hours played" on the same
row, which cannot both be true.

A game the site claims is now counted at `AVERAGE_GAME_SECONDS` (45 min,
`FISH_AVERAGE_GAME_SECONDS` to override) when nothing ever measured it, and at
its real duration when something did - never both. `timed_games` is how many
games the measured seconds already cover, so the estimate **shrinks to nothing**
as real durations take over. `/api/stats` publishes the whole split:
`play_seconds_measured`, `play_seconds_estimated`, `timed_games`,
`untimed_games`, `average_game_seconds`.

The page no longer floors hours to a whole number either: `Math.floor` turned
40 minutes of genuine play into a `0` indistinguishable from a server nobody
had ever touched. Below ten hours the tile carries one decimal.

`/api/stats` also publishes `games_recorded` and `games_baseline` so the
headline games number can be **checked**: `games_played` is floored at
`STATS_SEED_GAMES`, a baseline hardcoded twice (80 in May 2026, raised to 101
in June) while the Render disk was failing to keep history files.
`games_recorded` is the part with saved records behind it.

`get_live_user_counts()` returns five values now, not four, and tolerates a
shorter tuple left in the warm cache by a previous build.

Covered by `test_hours_played.py`.

---

## Putting it back

1. `preview-app.js`: `const PHST_STORE_CLOSED = false;`
2. `js/critter-pass.js`: `const CCCP_PASS_CLOSED = false;`, and paste
   `GUEST_NOTES.critterpass` back into `preview-app.js` from the comment
   standing in its place.
3. `multiplayer_server.py`: `SUPPORTER_WALL_ON_STANDBY = False`, then
   `cp _standby/website/13-supporter-wall.html.original multiplayer/client/supporter-wall.html`
   and put the wall links back on `thanks.html` and `claim-rewards.html`.
4. `shop.html`: `cp _standby/website/12-shop.html.original shop.html`
5. `index.html`: paste files 01–11 back at the rows in the table above, restore
   the Luckiest Guy `<link>` tags, and put the three Partner links back.
6. Decide what the stats band should show — the old four (file 02) or the
   current three, or five. They are the same markup either way; if you keep
   Hours, keep `grid-template-columns` in step with how many stats there are
   (it is `repeat(3, 1fr)` in the inline styles now).
7. Run `python3 test_stripe_payments.py`, `node test_supporter_tiers_ui.js`,
   `node test_critter_pass_ui.js`, `node test_closed_pages.js`,
   `node test_partner_form.js` and `python3 test_warm_cache.py`. Several of them
   check for the standby state on purpose and will fail loudly until they are
   pointed back at the restored page — that is the point of them.

`test_partner_form.js` needs nothing: it gates on the form's own `id` and
starts testing it again the moment the band is back.

`test_closed_pages.js` is the suite to **delete** when both pages reopen: every
check in it asserts a page is shut, so there is nothing in it to re-point. It
fails on its first two lines until then, naming the switch that is still on.

`test_critter_pass_ui.js` needs one edit and it tells you which: it asserts the
shipped file still declares `const CCCP_PASS_CLOSED = true;` and **exits**
rather than quietly measuring a Coming soon card at five widths and reporting
the pass green. Delete its "the Critter Pass is shut" block and the
`PASSJS_OPEN` replace above it, and the remaining 240-odd checks are the ones
that were always testing the open page.

Everything here is also in git, in the commit that removed it and its parent.
