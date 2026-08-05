#!/usr/bin/env node
/* Player Perks — the Critter-Coin consumables sold in the Store.
 *
 * Run:  node test_store_perks.js
 *
 * Four things are bought with coins that are neither an avatar nor a
 * background: a Streak Shield, a Name Change Token, an Emote Pack and a
 * Critter Re-Earn. Each one has a rule that costs a player real money (or a
 * real streak) when it breaks, so each one is pinned here:
 *
 *   1. The Streak Shield offer fires ONLY when one shield can actually save
 *      the run — the last day played was the day before yesterday. Alive
 *      streaks, two-day holes and 1-day "streaks" must not prompt.
 *   2. Spending a shield writes the missed day into stats.streak_days, which
 *      is what every streak number in the app derives from — so the run really
 *      does continue, and it can't be spent twice on the same date.
 *   3. The Emote Pack can only ever grant critters you have UNLOCKED and don't
 *      already have an emote for, and it warns with the REAL shortfall number
 *      before taking any coins.
 *   4. Every perk spend re-reads the balance inside the transaction (the same
 *      rule the audited background/skin purchases follow) and Critter Re-Earn
 *      clears the trade-away snapshot that was gating the avatar.
 *
 * The pure helpers are lifted straight out of preview-app.js and run for real;
 * the transaction bodies are executed against a fake Firestore. Nothing here
 * needs a browser or a network.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = __dirname;
const APP = fs.readFileSync(path.join(ROOT, "multiplayer/client/js/preview-app.js"), "utf8");
const CSS = fs.readFileSync(path.join(ROOT, "multiplayer/client/css/preview.css"), "utf8");
const HTML = fs.readFileSync(path.join(ROOT, "multiplayer/client/preview.html"), "utf8");
const SERVER = fs.readFileSync(path.join(ROOT, "multiplayer_server.py"), "utf8");

let failures = 0;
let checks = 0;
function check(cond, label) {
  checks++;
  if (!cond) { failures++; console.log("  ✗ " + label); }
}

// ── Lift the real helpers out of preview-app.js ─────────────────────────────
// The file is one huge IIFE that needs the whole app to boot, so pull out just
// the functions under test and run them in a sandbox.
function grabFn(name, indent) {
  const pad = " ".repeat(indent === undefined ? 2 : indent);
  let start = APP.indexOf(`\n${pad}function ${name}(`);
  if (start < 0) start = APP.indexOf(`\n${pad}async function ${name}(`);
  if (start < 0) throw new Error(`function ${name}() not found at indent ${indent === undefined ? 2 : indent}`);
  let i = APP.indexOf("{", start);
  let depth = 0;
  for (let j = i; j < APP.length; j++) {
    const ch = APP[j];
    if (ch === "{") depth++;
    else if (ch === "}") { depth--; if (depth === 0) return APP.slice(start, j + 1); }
  }
  throw new Error(`unbalanced braces reading ${name}()`);
}

const sandbox = { console };
vm.createContext(sandbox);
vm.runInContext(
  [
    grabFn("_streakLocalDateStr"),
    grabFn("_streakParseDate"),
    grabFn("_streakDayDiff"),
    grabFn("_computeStreakInfo"),
    grabFn("_streakAddDay"),
    grabFn("_streakShieldOffer", 6),
    grabFn("_streakTimeLeftLabel", 6),
  ].join("\n"),
  sandbox
);

const dayStr = (offset) => sandbox._streakLocalDateStr(new Date(Date.now() + offset * 86400000));

// ═══════════════════════════════════════════════════════════════════════════
console.log("\n1. The Streak Shield offer fires only when one shield can save it");
// ═══════════════════════════════════════════════════════════════════════════
{
  const offerFor = (days) => sandbox._streakShieldOffer({ streak_days: days });

  // Played today → the streak is alive, there is nothing to rescue.
  check(offerFor([dayStr(-2), dayStr(-1), dayStr(0)]) === null,
        "a streak played today must not prompt");
  // Played yesterday → still alive (today just hasn't happened yet).
  check(offerFor([dayStr(-3), dayStr(-2), dayStr(-1)]) === null,
        "a streak played yesterday is still alive and must not prompt");

  // The one case a shield fixes: last played the day before yesterday, so
  // yesterday is a single hole.
  const one = offerFor([dayStr(-5), dayStr(-4), dayStr(-3), dayStr(-2)]);
  check(one !== null, "a run broken by exactly one missed day must prompt");
  check(one && one.missed === dayStr(-1),
        `the day covered must be yesterday, got ${one && one.missed}`);
  check(one && one.run === 4,
        `the prompt must quote the run that broke (4), got ${one && one.run}`);

  // Two missed days cannot be covered by one shield — prompting would sell a
  // shield that doesn't save anything.
  check(offerFor([dayStr(-6), dayStr(-5), dayStr(-4), dayStr(-3)]) === null,
        "a two-day hole is beyond one shield and must not prompt");

  // A single day of play isn't a streak worth interrupting someone over.
  check(offerFor([dayStr(-2)]) === null,
        "a 1-day run must not prompt");
  check(offerFor([]) === null, "no history at all must not prompt");
  check(offerFor(undefined) === null, "missing streak_days must not throw or prompt");

  // A gap earlier in the history must not be mistaken for the current one.
  const withOldGap = offerFor([dayStr(-30), dayStr(-20), dayStr(-3), dayStr(-2)]);
  check(withOldGap !== null && withOldGap.run === 2,
        "the run quoted is the CURRENT one, not the longest in history");
}

// ═══════════════════════════════════════════════════════════════════════════
console.log("2. Spending a shield really does continue the run");
// ═══════════════════════════════════════════════════════════════════════════
{
  const before = [dayStr(-5), dayStr(-4), dayStr(-3), dayStr(-2)];
  const infoBefore = sandbox._computeStreakInfo(before);
  check(infoBefore.alive === false, "precondition: the run is broken before the shield");
  check(infoBefore.current === 0, "precondition: a broken run reads as 0");

  const after = sandbox._streakAddDay(before, dayStr(-1));
  const infoAfter = sandbox._computeStreakInfo(after);
  check(infoAfter.alive === true, "after the shield the run is alive again");
  check(infoAfter.current === 5,
        `the covered day extends the run to 5, got ${infoAfter.current}`);

  // Playing today then continues it, which is the whole point.
  const played = sandbox._streakAddDay(after, dayStr(0));
  check(sandbox._computeStreakInfo(played).current === 6,
        "playing today after a shield continues to day 6");

  // The list stays sorted and deduped, so the shield can't corrupt it.
  check(after.join(",") === [...new Set(after)].sort().join(","),
        "streak_days stays sorted and deduped after a shield");
  check(sandbox._streakAddDay(after, dayStr(-1)).length === after.length,
        "covering the same day twice adds no second entry");

  // ── The LONGEST streak has to survive (and can grow through) a shield ──
  // This is the number on the profile, so getting it wrong silently robs the
  // player of a record they paid to keep.
  check(infoAfter.longest === 5,
        `bridging the gap makes the longest run 5, got ${infoAfter.longest}`);
  check(sandbox._computeStreakInfo(played).longest === 6,
        "playing on after a shield raises the longest run to 6");

  // Covering a hole MERGES the two runs either side of it — the longest-ever
  // can therefore jump past the sum of what either side was worth alone.
  {
    const split = [dayStr(-9), dayStr(-8), dayStr(-7), dayStr(-6), dayStr(-5),
                   /* hole at -4 */ dayStr(-3), dayStr(-2)];
    const beforeBest = sandbox._computeStreakInfo(split).longest;
    check(beforeBest === 5, `before the merge the best run is 5, got ${beforeBest}`);
    // 5 days + the covered day + 2 days = one unbroken 8-day run.
    const merged = sandbox._computeStreakInfo(sandbox._streakAddDay(split, dayStr(-4)));
    check(merged.longest === 8,
          `covering the hole merges 5 + 1 + 2 into an 8-day best, got ${merged.longest}`);
  }

  // A previously-set record is never lowered by a shield: the write takes the
  // max of the recomputed longest and what was already stored.
  {
    const src = APP.slice(APP.indexOf("window.__fishUseStreakShield"));
    check(/const longest = Math\.max\(Number\(info\.longest\) \|\| 0, Number\(stats\.streak_longest\) \|\| 0\);/.test(src),
          "the shield write keeps the higher of the recomputed and stored longest");
    check(/"stats\.streak_longest": longest,/.test(src),
          "the longest streak is written back with the shield");
    check(/"stats\.daily_streak": info\.current,/.test(src),
          "the current streak is written back with the shield");
    check(/if \(days\.includes\(day\)\) throw new Error\("covered"\);/.test(src),
          "a day already in the history can't have a second shield spent on it");
    check(/if \(held < 1\) throw new Error\("none"\);/.test(src),
          "a shield you don't hold can't be spent");
    check(/const nextDays = _streakAddDay\(days, day\);/.test(src),
          "the day is added through _streakAddDay, so the list stays sorted and bounded");
  }
}

// ═══════════════════════════════════════════════════════════════════════════
console.log("2b. The 24-hour rescue window and its countdown");
// ═══════════════════════════════════════════════════════════════════════════
{
  // You get until the end of today to cover yesterday. At local midnight the
  // hole becomes two days wide and no single shield can bridge it — so the
  // deadline the countdown shows is the real one, not a sales timer.
  const offer = sandbox._streakShieldOffer({
    streak_days: [dayStr(-4), dayStr(-3), dayStr(-2)],
  });
  check(offer !== null, "precondition: the rescue window is open");

  const midnight = new Date(); midnight.setHours(24, 0, 0, 0);
  check(offer.deadline === midnight.getTime(),
        "the deadline is local midnight tonight");
  check(offer.msLeft > 0 && offer.msLeft <= 24 * 60 * 60 * 1000,
        `the window is at most 24h, got ${offer.msLeft}ms`);
  check(Math.abs(offer.deadline - Date.now() - offer.msLeft) < 1000,
        "msLeft agrees with the deadline");

  // Once the window has passed, the offer is gone entirely — no countdown, no
  // sale. (Simulated by a history whose gap is already two days wide.)
  check(sandbox._streakShieldOffer({ streak_days: [dayStr(-5), dayStr(-4), dayStr(-3)] }) === null,
        "after the window closes there is no offer and nothing to sell");

  // The label a player actually reads.
  const L = sandbox._streakTimeLeftLabel;
  check(L(6 * 3600000 + 12 * 60000) === "6h 12m", `6h 12m, got ${L(6 * 3600000 + 12 * 60000)}`);
  check(L(2 * 3600000) === "2 hours", `a whole number of hours drops the minutes, got ${L(2 * 3600000)}`);
  check(L(3600000) === "1 hour", `singular hour, got ${L(3600000)}`);
  check(L(48 * 60000) === "48 minutes", `under an hour reads in minutes, got ${L(48 * 60000)}`);
  check(L(60000) === "1 minute", `singular minute, got ${L(60000)}`);
  check(L(30000) === "under a minute", `the last minute has its own wording, got ${L(30000)}`);
  check(L(0) === "under a minute", "zero never renders as a negative or NaN");
  check(L(-5000) === "under a minute", "a passed deadline never renders negative time");

  // The banner itself: hidden by default, wired, and driven by the same offer.
  check(/id="ph-ss-rescue"[^>]*style="display:none;"/.test(HTML),
        "the countdown banner starts hidden");
  check(/id="ph-ss-rescue-time"/.test(HTML) && /id="ph-ss-rescue-msg"/.test(HTML),
        "the banner has a time and a message slot");
  check(/id="ph-ss-rescue-btn"/.test(HTML), "the banner has its own save button");
  check(/\.ph-ss-rescue \{/.test(CSS), "the banner is styled");
  const rescueSrc = /function _renderStreakRescue\(\)[\s\S]*?\n      \}/.exec(APP);
  check(!!rescueSrc, "the countdown renderer exists");
  const rs = rescueSrc ? rescueSrc[0] : "";
  check(/if \(!offer \|\| offer\.msLeft <= 0\)/.test(rs),
        "the banner hides itself when the window is shut");
  check(/clearInterval\(_rescueTimer\)/.test(rs),
        "the ticking stops when the banner goes away");
  check(/setInterval\(_renderStreakRescue, 60000\)/.test(rs),
        "the countdown re-renders every minute");
  check(/_streakTimeLeftLabel\(offer\.msLeft\)/.test(rs),
        "the banner shows the real time remaining");
  check(/window\._renderStreakRescue === "function"/.test(APP),
        "finishing a game (or spending a shield) refreshes the banner");
}

// ═══════════════════════════════════════════════════════════════════════════
console.log("3. The perk spend transactions");
// ═══════════════════════════════════════════════════════════════════════════
{
  // A fake Firestore that behaves like the real one for the parts these
  // transactions use: a doc read inside runTransaction, arrayUnion/increment
  // sentinels, and dotted field paths.
  function makeDb(doc) {
    const store = JSON.parse(JSON.stringify(doc));
    const applied = [];
    const api = {
      collection: () => ({ doc: () => ref }),
      runTransaction: async (fn) => fn(tx),
      _doc: () => store,
      _applied: () => applied,
    };
    const ref = { __ref: true };
    const tx = {
      get: async () => ({ exists: true, data: () => JSON.parse(JSON.stringify(store)) }),
      // Real signature is tx.update(ref, patch) — the ref is ignored here
      // because this fake owns exactly one document.
      update: (_ref, patch) => {
        applied.push(patch);
        for (const [k, v] of Object.entries(patch)) {
          const parts = k.split(".");
          let node = store;
          while (parts.length > 1) { const p = parts.shift(); node[p] = node[p] || {}; node = node[p]; }
          const leaf = parts[0];
          if (v && v.__union) {
            const cur = Array.isArray(node[leaf]) ? node[leaf] : [];
            node[leaf] = [...new Set([...cur, ...v.__union])];
          } else if (v && v.__inc !== undefined) {
            node[leaf] = (Number(node[leaf]) || 0) + v.__inc;
          } else {
            node[leaf] = v;
          }
        }
      },
    };
    return api;
  }
  const FieldValue = {
    arrayUnion: (...vals) => ({ __union: vals }),
    increment: (n) => ({ __inc: n }),
  };

  // The prices the Store advertises — read out of the source so the test can
  // never drift from the tags on the cards.
  const priceOf = (name) => {
    const m = new RegExp(`const ${name}\\s*=\\s*(\\d+)`).exec(APP);
    return m ? Number(m[1]) : null;
  };
  const SHIELD = priceOf("PHST_SHIELD_COIN_PRICE");
  const NAMETOK = priceOf("PHST_NAMETOK_COIN_PRICE");
  const PACK = priceOf("PHST_EMOTE_PACK_PRICE");
  const PACK_SIZE = priceOf("PHST_EMOTE_PACK_SIZE");
  const REEARN = priceOf("PHST_REEARN_COIN_PRICE");

  check(SHIELD === 500, `Streak Shield is 500 coins, source says ${SHIELD}`);
  check(NAMETOK === 100, `Name Change Token is 100 coins, source says ${NAMETOK}`);
  check(PACK === 500, `Emote Pack is 500 coins, source says ${PACK}`);
  check(PACK_SIZE === 5, `an Emote Pack is 5 emotes, source says ${PACK_SIZE}`);
  check(REEARN === 2500, `Critter Re-Earn is 2,500 coins, source says ${REEARN}`);

  // ── The shared spend primitive, run for real ──────────────────────────────
  const spendSrc = grabFn("_perkSpend", 4);
  const spendCtx = {
    console, firebase: { firestore: { FieldValue } },
    _galReadOnly: false, _authUser: { uid: "u1" }, _db: null,
  };
  vm.createContext(spendCtx);
  vm.runInContext(spendSrc + "\nthis.__spend = _perkSpend;", spendCtx);

  const run = async (docData, price, build) => {
    spendCtx._db = makeDb(docData);
    const res = await spendCtx.__spend(price, build);
    return { res, doc: spendCtx._db._doc() };
  };

  (async () => {
    // Not enough coins → nothing is written at all.
    {
      const { res, doc } = await run({ stats: { critter_coins: 499 } }, SHIELD,
        () => ({ streak_shields: 1 }));
      check(res.ok === false && res.reason === "coins",
            "a spend with too few coins fails with reason 'coins'");
      check(doc.streak_shields === undefined,
            "a failed spend must not grant the perk");
      check(doc.stats.critter_coins === 499,
            "a failed spend must not touch the balance");
    }
    // Enough coins → charged exactly once, perk granted in the same write.
    {
      const { res, doc } = await run({ stats: { critter_coins: 1200 } }, SHIELD,
        (d) => ({ streak_shields: (Number(d.streak_shields) || 0) + 1 }));
      check(res.ok === true, "a funded spend succeeds");
      check(doc.stats.critter_coins === 700,
            `500 is deducted once, balance should be 700, got ${doc.stats.critter_coins}`);
      check(doc.streak_shields === 1, "the shield lands on the doc");
      check(res.newBalance === 700, "the caller is told the new balance");
    }
    // The balance is read INSIDE the transaction, not trusted from the client:
    // a doc whose coins were spent elsewhere still refuses.
    {
      const { res } = await run({ stats: { critter_coins: 0 } }, REEARN, () => ({ x: 1 }));
      check(res.ok === false && res.reason === "coins",
            "the transaction re-reads the balance server-side and refuses when it's gone");
    }
    // A build() that throws its own reason aborts without charging.
    {
      const { res, doc } = await run({ stats: { critter_coins: 9999 } }, PACK,
        () => { throw new Error("owned"); });
      check(res.ok === false && res.reason === "owned",
            "a build() rejection surfaces its reason");
      check(doc.stats.critter_coins === 9999,
            "a build() rejection must not charge the player");
    }
    // Read-only mode (viewing someone else's gallery) can never spend.
    {
      spendCtx._galReadOnly = true;
      spendCtx._db = makeDb({ stats: { critter_coins: 9999 } });
      const res = await spendCtx.__spend(SHIELD, () => ({ streak_shields: 1 }));
      check(res.ok === false && res.reason === "readonly",
            "no perk can be bought while viewing another player's collection");
      spendCtx._galReadOnly = false;
    }

    // ── The stack cap, taken from the real build() in __fishBuyStreakShield ──
    {
      const capSrc = /if \(held >= PHST_PERK_STACK_MAX\) throw new Error\("max"\);/.test(APP);
      check(capSrc, "the shield purchase enforces the stack cap");
      const capMax = priceOf("PHST_PERK_STACK_MAX");
      const build = (d) => {
        const held = Math.max(0, Math.floor(Number(d.streak_shields) || 0));
        if (held >= capMax) throw new Error("max");
        return { streak_shields: held + 1 };
      };
      const { res, doc } = await run({ stats: { critter_coins: 9999 }, streak_shields: capMax },
                                     SHIELD, build);
      check(res.ok === false && res.reason === "max",
            "buying past the stack cap is refused");
      check(doc.stats.critter_coins === 9999, "a capped purchase costs nothing");
    }

    // ── Critter Re-Earn drops the trade-away snapshot ────────────────────────
    // If the snapshot survived, the re-earn gate (reEarnState) would still
    // consider the avatar owed and the next automatic sweep could re-block it.
    {
      const target = "/avatars/horned-puffin.png";
      const build = (data) => {
        const owned = (Array.isArray(data.unlocked_icons) ? data.unlocked_icons : [])
          .map(s => String(s || "").toLowerCase());
        if (owned.includes(target)) throw new Error("owned");
        const away = Array.isArray(data.traded_away) ? data.traded_away : [];
        const kept = away.filter(e => !(e && typeof e === "object"
          && String(e.item || "").split("?")[0].toLowerCase() === target));
        if (kept.length === away.length) throw new Error("nottraded");
        return { unlocked_icons: FieldValue.arrayUnion(target), traded_away: kept };
      };
      const { res, doc } = await run({
        stats: { critter_coins: 3000 },
        unlocked_icons: ["/avatars/mullet.png"],
        traded_away: [{ item: target, stats: { lifetime_play_again: 80 } },
                      { item: "/avatars/osprey.png", stats: {} }],
      }, REEARN, build);
      check(res.ok === true, "re-earning a traded-away critter succeeds");
      check(doc.unlocked_icons.includes(target), "the critter is unlocked again");
      check(doc.traded_away.length === 1 && doc.traded_away[0].item === "/avatars/osprey.png",
            "only THIS critter's trade-away snapshot is cleared, others survive");
      check(doc.stats.critter_coins === 500, "2,500 coins are charged");

      // A critter you never traded away can't be bought back.
      const notTraded = await run({
        stats: { critter_coins: 3000 }, unlocked_icons: [], traded_away: [],
      }, REEARN, build);
      check(notTraded.res.ok === false && notTraded.res.reason === "nottraded",
            "a critter that was never traded away cannot be re-earned");
      check(notTraded.doc.stats.critter_coins === 3000,
            "a refused re-earn costs nothing");
    }

    // ── The Emote Pack never pays for a duplicate ────────────────────────────
    {
      const want = ["/avatars/mullet.png", "/avatars/osprey.png"];
      const build = (data) => {
        const have = new Set((Array.isArray(data.emote_icons) ? data.emote_icons : [])
          .map(s => String(s || "").toLowerCase()));
        const fresh = want.filter(p => !have.has(p));
        if (!fresh.length) throw new Error("owned");
        return { emote_icons: FieldValue.arrayUnion(...fresh) };
      };
      const { res, doc } = await run({
        stats: { critter_coins: 900 }, emote_icons: ["/avatars/mullet.png"],
      }, PACK, build);
      check(res.ok === true, "a pack with at least one new critter goes through");
      check(doc.emote_icons.length === 2,
            "the already-owned emote is not duplicated on the doc");

      const allOwned = await run({
        stats: { critter_coins: 900 }, emote_icons: want,
      }, PACK, build);
      check(allOwned.res.ok === false && allOwned.res.reason === "owned",
            "a pack of nothing but duplicates is refused");
      check(allOwned.doc.stats.critter_coins === 900,
            "a refused pack costs nothing");
    }

    // ═══════════════════════════════════════════════════════════════════════
    console.log("4. The Emote Pack shortfall warning");
    // ═══════════════════════════════════════════════════════════════════════
    // The user-visible promise: you are told the REAL number you'd get before
    // any coins move, and you can back out.
    {
      const buySrc = /async function _perkBuyEmotes\(\)[\s\S]*?\n      \}/.exec(APP);
      check(!!buySrc, "the Emote Pack purchase flow exists");
      const src = buySrc ? buySrc[0] : "";
      check(/if \(!pool\.length\)/.test(src),
            "owning an emote for everything you've unlocked is handled before any spend");
      check(/if \(pool\.length < size\)/.test(src),
            "a pool smaller than a full pack triggers the shortfall warning");
      check(/You'd only get " \+ pool\.length \+ " of " \+ size/.test(src),
            "the warning states the real number you'd get, not a generic message");
      check(src.indexOf("pool.length < size") < src.indexOf("__fishBuyEmotePack"),
            "the warning is shown BEFORE the purchase call, never after");
      check(/if \(short\.action !== "confirm"\) return;/.test(src),
            "declining the shortfall warning cancels without spending");
      check(/const take = Math\.min\(size, pool\.length\)/.test(src),
            "the picker asks for at most what is actually available");
      check(/requireFull: true/.test(src),
            "the pack can't be bought half-filled");
    }

    // The eligible pool is unlocked-minus-already-emoted. Run the real filter.
    {
      const eligSrc = grabFn("_emoteEligible", 4);
      const ctx = {
        console,
        ANIMAL_AVATARS: [
          { id: "mullet", name: "Mullet", img: "/avatars/mullet.png" },
          { id: "osprey", name: "Osprey", img: "/avatars/osprey.png" },
          { id: "bunker", name: "Bunker", img: "/avatars/bunker.png" },
          { id: "locked", name: "Locked", img: "/avatars/locked.png" },
        ],
        _owned: ["/avatars/mullet.png"],
        _unlocked: ["/avatars/mullet.png", "/avatars/osprey.png", "/avatars/bunker.png"],
        normalizeAvatarUrl: (s) => String(s || "").toLowerCase(),
      };
      ctx.isAvatarUnlocked = (img) => ctx._unlocked.includes(String(img).toLowerCase());
      ctx._ownedEmotes = () => ctx._owned;
      vm.createContext(ctx);
      vm.runInContext(eligSrc + "\nthis.__elig = _emoteEligible;", ctx);

      const pool = ctx.__elig();
      check(pool.length === 2, `2 critters are eligible, got ${pool.length}`);
      check(!pool.some(a => a.id === "locked"),
            "a critter you have NOT unlocked can never be an emote");
      check(!pool.some(a => a.id === "mullet"),
            "a critter you already have an emote for is not offered again");

      ctx._owned = ["/avatars/mullet.png", "/avatars/osprey.png", "/avatars/bunker.png"];
      check(ctx.__elig().length === 0,
            "with an emote for every unlocked critter the pool is empty");
    }

    // ═══════════════════════════════════════════════════════════════════════
    console.log("5. Emotes in chat, end to end");
    // ═══════════════════════════════════════════════════════════════════════
    {
      // Server: an emote-only line is allowed through the empty-message guard,
      // and the id is validated rather than run through the profanity filter
      // (which would asterisk out innocent slug segments).
      check(/def _clean_emote_id/.test(SERVER),
            "the server validates emote ids");
      check(/_EMOTE_ID_RE = re\.compile\(r"\^\[a-z0-9\]\+\(\?:-\[a-z0-9\]\+\)\*\$"\)/.test(SERVER),
            "an emote id is a plain avatar slug — no paths, no markup");
      check(/if not message and not emote:\s*\n\s*return \{"ok": False, "error": "empty message"\}/.test(SERVER),
            "an emote with no text is a valid message; a line with neither is not");
      check(/if emote:\s*\n\s*entry\["emote"\] = emote/.test(SERVER),
            "the emote rides on the stored chat entry");

      // Client: the message renderer paints the picture, and falls back rather
      // than showing a broken image for an id with no art.
      check(/if \(m\.emote\) \{/.test(APP), "the chat renderer handles an emote line");
      check(/body\.className = "cm-emote"/.test(APP), "an emote gets its own class, not .cm-text");
      check(/eImg\.onerror = \(\) => \{ eImg\.onerror = null; eImg\.src = "\/avatars\/mullet\.png"; \}/.test(APP),
            "an unknown emote id falls back to the default icon");
      check(/emote: id/.test(APP), "the send path posts the emote field");
      check(/if \(isSpectating\(\)\) return;/.test(APP),
            "spectators (whose endpoint has no emote support) can't send one");

      // The tray only exists for people who own emotes.
      check(/btn\.style\.display = "none";/.test(APP) && /const mine = \(typeof window\.__fishGetEmotes/.test(APP),
            "the smiley hides itself when you own no emotes");
      check(/id="pv-chat-emote-btn"[^>]*style="display:none;"/.test(HTML),
            "the emote button starts hidden in the markup");
      check(/id="pv-chat-emote-tray"/.test(HTML), "the emote tray exists in the chat panel");
      check(/#pv-chat-emote-tray\.open \{ display: flex; \}/.test(CSS),
            "the tray has an open state");
      check(/\.chat-msg \.cm-emote img \{/.test(CSS), "sent emotes are styled in the message list");
    }

    // ═══════════════════════════════════════════════════════════════════════
    console.log("6. Store + Settings wiring");
    // ═══════════════════════════════════════════════════════════════════════
    {
      check(/data-perk="\$\{p\.key\}"/.test(APP), "each perk card carries its key");
      check(/el\.querySelectorAll\("\[data-perk\]"\)/.test(APP), "perk buttons are wired on render");
      check(/window\._phstBuyPerk = async function/.test(APP), "the perk buy entry point exists");
      for (const k of ["shield", "nametok", "emotes", "reearn"]) {
        check(new RegExp(`key === "${k}"`).test(APP), `the '${k}' perk is dispatched`);
      }
      check(/\.phst-perk-grid \{/.test(CSS), "the perk grid is styled");
      check(/#cc-perk-modal \{/.test(CSS) && /#cc-perk-modal\.open \{ display: flex; \}/.test(CSS),
            "the perk dialog is styled and has an open state");

      // The name token is burned by the rename itself, so a failed rename can
      // never eat one.
      const renameBlock = /nickname_changed_at: now,[\s\S]{0,400}?\}\);/.exec(APP);
      check(!!renameBlock && /name_change_tokens: firebase\.firestore\.FieldValue\.increment\(-1\)/.test(renameBlock[0]),
            "the token is decremented in the SAME write as the rename");
      check(/let spendNameToken = false;/.test(APP),
            "the rename decides up front whether a token is being spent");
      check(/if \(!toks\) \{[\s\S]{0,220}?return;/.test(APP),
            "with no token in hand the cooldown still blocks the rename");

      // The "first change is free" promise: a player who has never renamed is
      // told so instead of being sold a token they don't need.
      check(/Your first change is free/.test(APP),
            "a player who never renamed is told the first change costs nothing");
    }

    // ═══════════════════════════════════════════════════════════════════════
    console.log("7. Retired Supporter-tier coin copy stays retired");
    // ═══════════════════════════════════════════════════════════════════════
    // These lines told players exactly what their tier coins would buy. They
    // were wrong the moment prices moved, so they are gone from BOTH surfaces
    // (the marketing site and the in-game Store) and must not creep back.
    {
      const INDEX = fs.readFileSync(path.join(ROOT, "index.html"), "utf8");
      const gone = [
        "5 of the 8 backgrounds",
        "Backgrounds are already yours",
        "A full year of seasonal skins",
      ];
      for (const phrase of gone) {
        check(!INDEX.includes(phrase),
              `the marketing site must not say "${phrase}"`);
        check(!APP.includes(phrase),
              `the in-game Store must not say "${phrase}"`);
      }
      check(!/coinsNote/.test(APP),
            "no supporter tier carries a coins-buy-you-this note, and the renderer slot is gone too");
      check(!/perk-coins-note/.test(INDEX),
            "the marketing site's note markup is gone, not just its text");
      // The coin AMOUNTS themselves stay — it's only the "this buys you…"
      // claim that was removed.
      check(/5,000 Critter Coins/.test(INDEX) && /phst-tier-coins-amt/.test(APP),
            "the tiers still show how many coins they include");
    }

    console.log(`\nplayer-perk checks: ${checks}`);
    if (failures) { console.log(`${failures} FAILED`); process.exit(1); }
    console.log("player perks OK");
  })().catch((e) => { console.log("FATAL " + e.stack); process.exit(1); });
}
