# On standby

Everything in here was taken off the live site on 2026-09-08 and is meant to
come back. Nothing was deleted: the markup, the styles and the scripts are all
either still in the files they came from or archived verbatim beside this note.

**The one-line version:** the site and the in-game Store take no money right
now, the Supporter Reef Wall is off, and the homepage shows three live play
numbers where the donation total and the tiers used to be.

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

### 2. shop.html — the website storefront

The quantity picker, **Add to Cart**, **Buy Now**, the toast and the whole
`<script>` that drove them are gone; the page has no button and no script left
on it. The head reads *Coming Soon*, the stock line says it is not on sale yet,
and a `.shop-closed` notice sits where the controls were.

Restore: `_standby/website/12-shop.html.original` is the whole file as it was.
(The buttons never took a real payment — they only raised a toast — but a shop
that looks open is a shop people expect to order from.)

### 3. The Supporter Reef Wall — one word, server-side

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

### 4. index.html — the tiers, the donation goal, the wall band, the form

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
| Registered Players | `/api/stats` → `registered_players` (unchanged) |
| **Hours Played Online** | `/api/stats` → `play_seconds`, divided by 3600 on the page |
| Online Games Played | `/api/stats` → `games_played` (unchanged) |

`play_seconds` is new. It is built the same way `games_played` is:

- **every finished game** adds its own `duration_sec` — the figure already
  written onto that game's history record — to `site_stats.json` and to the
  Firestore counter, in the same place the games counter is bumped;
- **on boot**, `heal_play_seconds_from_history()` sums `duration_sec` across
  every `game_*.json` and applies it as a **floor** to both stores, so a Render
  disk reset cannot walk the public number backwards. It runs on its own thread
  and only at startup: counting filenames is cheap enough to do per request, but
  opening every record is not.

`get_live_user_counts()` returns four values now, not three, and tolerates a
three-tuple left in the warm cache by the previous build.

Covered by `test_hours_played.py`.

---

## Putting it back

1. `preview-app.js`: `const PHST_STORE_CLOSED = false;`
2. `multiplayer_server.py`: `SUPPORTER_WALL_ON_STANDBY = False`, then
   `cp _standby/website/13-supporter-wall.html.original multiplayer/client/supporter-wall.html`
   and put the wall links back on `thanks.html` and `claim-rewards.html`.
3. `shop.html`: `cp _standby/website/12-shop.html.original shop.html`
4. `index.html`: paste files 01–11 back at the rows in the table above, restore
   the Luckiest Guy `<link>` tags, and put the three Partner links back.
5. Decide what the stats band should show — the old four (file 02) or the
   current three, or five. They are the same markup either way; if you keep
   Hours, keep `grid-template-columns` in step with how many stats there are
   (it is `repeat(3, 1fr)` in the inline styles now).
6. Run `python3 test_stripe_payments.py`, `node test_supporter_tiers_ui.js`,
   `node test_partner_form.js` and `python3 test_warm_cache.py`. Several of them
   check for the standby state on purpose and will fail loudly until they are
   pointed back at the restored page — that is the point of them.

`test_partner_form.js` needs nothing: it gates on the form's own `id` and
starts testing it again the moment the band is back.

Everything here is also in git, in the commit that removed it and its parent.
