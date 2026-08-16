# Discord join reward — setup

**250 Critter Coins, once, for actually being in the Discord server.**

The code is written, tested and deployed. It stays switched **off** until you
add three values from the Discord Developer Portal to Render. Until then the
server logs `[discord] join reward OFF …`, the game hides the offer completely,
and every claim is refused — it never falls back to paying out unchecked.

Budget about 10 minutes. You need to be the **owner** of the Discord server.

---

## 1. Get the Server ID (30 seconds)

1. Open Discord → **User Settings** (the cog) → **Advanced** → turn on
   **Developer Mode**.
2. Right-click your **Currents and Critters** server in the left rail →
   **Copy Server ID**.

That long number is `DISCORD_GUILD_ID`. Paste it somewhere for a moment.

---

## 2. Create the OAuth app (5 minutes)

1. Go to <https://discord.com/developers/applications> and sign in with the
   account that owns the server.
2. **New Application** → name it `Currents and Critters` → **Create**.
3. In the left rail choose **OAuth2**.
4. Copy **Client ID** → that is `DISCORD_CLIENT_ID`.
5. Under **Client Secret**, press **Reset Secret** → **Yes, do it** → copy the
   value → that is `DISCORD_CLIENT_SECRET`.
   *Discord shows a client secret once. If you lose it, reset it again and
   update Render — nothing else breaks.*
6. Still on the OAuth2 page, find **Redirects** → **Add Redirect** and paste
   **exactly** this, then **Save Changes**:

   ```
   https://play.currentsandcritters.com/api/discord/callback
   ```

   > ⚠️ This is the one step that silently breaks everything. It must match
   > character for character — no trailing slash, `https` not `http`. If it
   > doesn't, Discord rejects every claim with a 400 and the server log says
   > exactly that, naming the URI it sent.

You do **not** need a bot, a bot token, or any privileged intents. Nothing is
ever added to your server.

---

## 3. Put the values on Render (2 minutes)

Render dashboard → the **fish-game-multiplayer** service → **Environment** →
**Add Environment Variable**, three times:

| Key | Value |
| --- | --- |
| `DISCORD_CLIENT_ID` | the Client ID from step 2.4 |
| `DISCORD_CLIENT_SECRET` | the Client Secret from step 2.5 |
| `DISCORD_GUILD_ID` | the Server ID from step 1 |

`DISCORD_REDIRECT_URI` is already set in `render.yaml`, so leave it alone unless
you move the game to a different domain.

**Save Changes.** Render redeploys (10–15 minutes, as always).

---

## 4. Check it came up

In the Render logs after the deploy you want to see:

```
[discord] join reward ON — 250 coins, redirect https://play.currentsandcritters.com/api/discord/callback
```

If you see `join reward OFF` instead, the line names which variables are still
missing.

Then, in the game: open **Player Home** and look beside the Join-the-Discord
button. A gold **+250 Critter Coins** chip should be sitting there. Click it,
approve on Discord, and the coins land — the balance in the header updates
without a refresh.

---

## Optional knobs

| Variable | Default | What it does |
| --- | --- | --- |
| `DISCORD_REWARD_COINS` | `250` | The amount paid. Changing it changes the advert, the payout and the ledger together — there is only one number. |
| `DISCORD_INVITE_URL` | the community invite | The invite the game links to, if you ever regenerate it. |
| `DISCORD_STATE_SECRET` | the client secret | Signs the OAuth handshake. Only set this if you want it separate from the client secret. |

---

## How it actually works

Three things are guaranteed, and each has tests behind it
(`test_discord_rewards.py`, `test_discord_reward_ui.js`):

1. **You cannot be paid without being a member.** When a player claims, the
   *server* asks Discord "is this account in guild X?" and pays only on a yes.
   Discord being down, a rejected sign-in, or a garbled reply are all "no" —
   never a payout.

2. **You cannot be paid twice.** Two ledger documents are written in the *same
   Firestore transaction* as the coins: `discord_rewards/u_{uid}` (this game
   account has been paid) and `discord_rewards/d_{discordId}` (this Discord
   account has been paid). Because they are created by document ID, a second
   attempt — a double-tap, two tabs, two devices, an alt account using the same
   Discord login — collides and the whole transaction is abandoned, coins
   included.

3. **People already in the server just claim it.** Membership is checked live,
   at claim time. Someone who joined months ago clicks the same chip, Discord
   says yes, and they are paid. There is no backfill list to maintain and
   nobody can be missed.

Where to look if you need to:

| | |
| --- | --- |
| Server logic | [`discord_server.py`](discord_server.py) |
| The chip + claim UI | [`multiplayer/client/js/discord-reward.js`](multiplayer/client/js/discord-reward.js) |
| Who has been paid | Firestore → `discord_rewards` collection (each doc is also the audit record: uid, Discord id and name, amount, balance before and after, timestamp) |
| Tests | `python3 test_discord_rewards.py` · `node test_discord_reward_ui.js` |

### Turning it off again

Delete `DISCORD_CLIENT_SECRET` (or any of the three) in Render. The offer
disappears from the game on the next deploy. Coins already paid stay paid, and
the ledger keeps its record, so switching it back on later does not pay anyone
a second time.
