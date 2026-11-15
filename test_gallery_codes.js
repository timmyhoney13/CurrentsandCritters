#!/usr/bin/env node
/* A CODE CAN BE A WORD: FEBRUARY2ND UNLOCKS THE FISH.
 *
 * Run:  node test_gallery_codes.js      (the width check needs Google Chrome)
 *
 * Asked for as: "make the code february 2nd have it on the thing below the QR
 * code and make it so you can put in codes in the top of the avatar gallery".
 *
 * The hang tag on the box says "Scan for a FREE EXCLUSIVE SKIN!" and prints a
 * code under the QR. The skin is the Fish avatar (unlock type "code"), and a
 * code is typed into the box at the top of the Avatar Gallery, which reads
 * Firestore `donation_codes/<CODE>`. That box used to take six digits and
 * nothing else: it stripped every letter AS YOU TYPED, so a word code could not
 * even be entered, and the phone keyboard it asked for had no letters on it.
 *
 * What this pins:
 *   A. normalizeRedeemCode(): case, spaces, dashes and dots do not matter, so
 *      "February 2nd", "february-2nd" and "FEBRUARY2ND" are ONE code, and the
 *      6-digit donation codes come through unchanged.
 *   B. redeemDonationCode(), the REAL one sliced out of preview-app.js, run
 *      against a stub Firestore: a word code kept in Firestore is looked up
 *      however it is typed, an unlimited one grants once per account WITHOUT
 *      spending a use on a repeat, and the old 6-digit codes, single-use and
 *      spent, behave exactly as before.
 *   H. THE HANG TAG'S CODE NEEDS NO FIRESTORE DOCUMENT. It is printed on every
 *      tag, so it is built into the game: with an EMPTY donation_codes it still
 *      unlocks the Fish, and it forgives the spellings people really type
 *      (FEBUARARY2ND was the first one tried, and it said "Invalid code.").
 *   I. The Fish is real: the actual ANIMAL_AVATARS entry, run through the
 *      actual normalizeAvatarUrl(), is a "code" unlock whose art is on disk.
 *   C. The gallery's own input and Redeem handlers (sliced, not paraphrased):
 *      letters survive typing, and the click hands the code on.
 *   D. The markup: no numeric keypad, room for the code, an honest label.
 *   E. No em dash in anything this path can put on screen.
 *   F. Real pixels in headless Chrome: FEBRUARY2ND fits inside the box at
 *      desktop and phone widths, in the box's own font.
 */
"use strict";
const fs = require("fs");
const os = require("os");
const path = require("path");
const vm = require("vm");
const { execFileSync } = require("child_process");

const ROOT = __dirname;
// GALLERY_APP_FILE / GALLERY_HTML_FILE point the same checks at copies of the
// DEPLOYED files (curl them from play.currentsandcritters.com), to prove what
// players are actually served, not just what is in the repo.
const APP  = fs.readFileSync(process.env.GALLERY_APP_FILE || path.join(ROOT, "multiplayer/client/js/preview-app.js"), "utf8");
const HTML = fs.readFileSync(process.env.GALLERY_HTML_FILE || path.join(ROOT, "multiplayer/client/preview.html"), "utf8");
const CSS  = fs.readFileSync(path.join(ROOT, "multiplayer/client/css/preview.css"), "utf8");

let pass = 0, fail = 0;
function check(name, cond, detail) {
  if (cond) { pass++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + (detail !== undefined ? "  [" + detail + "]" : "")); }
}

// Slice `function NAME(...) {...}` out of preview-app.js, `async` included
// (the repo's usual grabFn cuts from the word `function`, which would drop it
// and turn every `await` in the body into a SyntaxError).
function grabFn(name) {
  const m = APP.match(new RegExp("\\n(\\s*)((?:async\\s+)?function " + name + "\\()"));
  if (!m) return null;
  const start = m.index + 1 + m[1].length;
  let i = APP.indexOf("(", start), paren = 0;
  for (; i < APP.length; i++) {
    if (APP[i] === "(") paren++;
    else if (APP[i] === ")") { paren--; if (paren === 0) break; }
  }
  let depth = 0;
  for (let j = APP.indexOf("{", i); j < APP.length; j++) {
    if (APP[j] === "{") depth++;
    else if (APP[j] === "}") { depth--; if (depth === 0) return APP.slice(start, j + 1); }
  }
  throw new Error("unbalanced braces reading " + name + "()");
}

const NORMALIZE = grabFn("normalizeRedeemCode");
const PRINTED   = grabFn("printedCodeReward");
const TYPO      = grabFn("codeTypoDistance");
const REDEEM    = grabFn("redeemDonationCode");
const CLAIM     = grabFn("claimPrintedCode");
if (!REDEEM) { console.log("FAIL: redeemDonationCode() is gone from preview-app.js"); process.exit(1); }

// ── A. one code, however it is typed ──────────────────────────────────────
console.log("\nA. the code is read without case, spaces or dashes");
{
  check("normalizeRedeemCode() exists in preview-app.js", !!NORMALIZE);
  if (NORMALIZE) {
    const norm = vm.runInNewContext("(" + NORMALIZE + ")");
    for (const typed of ["February 2nd", "february 2nd", "FEBRUARY2ND", "february-2nd",
                         "  February 2ND  ", "February 2nd.", "february_2nd"]) {
      check(`"${typed}" is FEBRUARY2ND`, norm(typed) === "FEBRUARY2ND", norm(typed));
    }
    for (const typed of ["481902", "481 902", "481-902", " 481902 "]) {
      check(`the 6-digit donation code "${typed}" is still 481902`, norm(typed) === "481902", norm(typed));
    }
    check("nothing typed is nothing", norm("") === "" && norm(null) === "" && norm("  - ") === "");
  }
}

// ── B. the real redeemDonationCode against a stub Firestore ───────────────
// The server's side of the hang tag code (printed_codes.py, pinned by
// test_printed_codes.py): one claim per account, a cap, and the grant.
function makeServer({ cap = 250, claimed = 0, down = false } = {}) {
  const srv = { cap, claimed, ledger: new Set(), calls: [] };
  srv.bridge = {
    async idToken() { return "tok-u1"; },
    async post(path, body) {
      srv.calls.push({ path, body });
      if (down) throw new Error("offline");
      if (path !== "/api/printed-code/claim") return { ok: false, status: 404, data: { ok: false } };
      if (!body.idToken) return { ok: false, status: 401, data: { ok: false, error: "unauthorized", message: "Sign in to redeem a code." } };
      if (body.code !== "FEBRUARY2ND") return { ok: true, status: 200, data: { ok: false, error: "bad_code", message: "Invalid code." } };
      if (srv.ledger.has(body.idToken)) return { ok: true, status: 200, data: { ok: false, error: "already_claimed", message: "This account has already used the hang tag code." } };
      if (srv.claimed >= srv.cap) return { ok: true, status: 200, data: { ok: false, error: "sold_out",
        message: "Every Fish from the first run of plushies has been claimed. The Fish came only with the first run, so it can't be unlocked anymore." } };
      srv.claimed++; srv.ledger.add(body.idToken);
      return { ok: true, status: 200, data: { ok: true, number: srv.claimed, cap: srv.cap } };
    },
  };
  return srv;
}

function makeWorld({ signedIn = true, owned = [], codes = {}, server = makeServer() } = {}) {
  const docs = JSON.parse(JSON.stringify(codes));
  const reads = [];
  const txs = [];
  const owns = new Set(owned);
  const granted = [];
  const ref = (id) => ({ id });
  const db = {
    collection(name) {
      if (name !== "donation_codes") throw new Error("unexpected collection " + name);
      return { doc: (id) => ref(id) };
    },
    async runTransaction(fn) {
      txs.push(1);
      const tx = {
        async get(r) { return { exists: r.id in docs, data: () => docs[r.id] }; },
        update(r, patch) { Object.assign(docs[r.id], patch); },
      };
      return fn(tx);
    },
  };
  // ref.get() lives on the object the code reads first.
  const _doc = db.collection;
  db.collection = (name) => {
    const c = _doc.call(db, name);
    return { doc: (id) => ({ id, async get() { reads.push(id); return { exists: id in docs, data: () => docs[id] }; } }) };
  };
  const ANIMALS = { "/avatars/fish.png": { id: "fish", name: "Fish" },
                    "/avatars/amberjack.png": { id: "amberjack", name: "Amberjack" } };
  const sandbox = {
    _authUser: signedIn ? { uid: "u1" } : null,
    _db: db,
    normalizeAvatarUrl: (u) => String(u || ""),
    animalByImg: (u) => ANIMALS[u] || null,
    isAvatarUnlocked: (u) => owns.has(u),
    normalizeBgUrl: (u) => String(u || ""),
    _BG_BY_IMG: {},
    isBackgroundUnlocked: () => false,
    window: {
      __fishGrantUnlockedIcon: async (u) => { if (owns.has(u)) return false; owns.add(u); granted.push(u); return true; },
      __fishQueueAnimalUnlock: () => {},
      __fishGrantUnlockedBackground: async () => true,
      __ccRedeem: server.bridge,
    },
  };
  vm.createContext(sandbox);
  for (const src of [NORMALIZE, PRINTED, TYPO, CLAIM]) if (src) vm.runInContext(src, sandbox);
  vm.runInContext(REDEEM, sandbox);
  return { redeem: (c) => vm.runInContext("redeemDonationCode", sandbox)(c), docs, reads, granted, owns, txs, sandbox, server };
}

// A word code kept in Firestore, like a promo made with --code. NOT the hang
// tag's: that one is built in (section H) and never reads Firestore.
const WORD_CODE = { reward_type: "avatar", reward_img: "/avatars/amberjack.png", label: "Amberjack",
                    max_uses: 0, unlimited: true, uses: 5 };
const ONE_SHOT  = { reward_type: "avatar", reward_img: "/avatars/amberjack.png", label: "Amberjack",
                    max_uses: 1, unlimited: false, uses: 0 };

(async () => {
  console.log("\nB. the real redeemDonationCode() against a stub Firestore");
  {
    const w = makeWorld({ codes: { TIDEPOOL7: WORD_CODE } });
    const r = await w.redeem("Tide Pool 7");
    check('typing "Tide Pool 7" looks up donation_codes/TIDEPOOL7',
          w.reads.includes("TIDEPOOL7"), JSON.stringify(w.reads));
    check("...and unlocks its avatar", r && r.ok === true && w.granted.includes("/avatars/amberjack.png"), JSON.stringify(r));
    check("...saying so in words", r && r.msg === "Unlocked Amberjack!", r && r.msg);
    check("...and the unlimited code counts the use (5 to 6) without running out",
          w.docs.TIDEPOOL7.uses === 6, w.docs.TIDEPOOL7.uses);

    const again = await w.redeem("TIDEPOOL7");
    check("the same account entering it again is told it already has it",
          again && again.ok === false && /already own Amberjack/.test(again.msg), again && again.msg);
    check("...and that second try does NOT spend a use", w.docs.TIDEPOOL7.uses === 6, w.docs.TIDEPOOL7.uses);
  }
  {
    const w = makeWorld({ codes: { TIDEPOOL7: { ...WORD_CODE, uses: 900 } } });
    const r = await w.redeem("tide-pool-7");
    check("a different account with the same word code also gets it",
          r && r.ok === true && w.docs.TIDEPOOL7.uses === 901, JSON.stringify(r));
  }
  {
    const w = makeWorld({ signedIn: false, codes: { TIDEPOOL7: WORD_CODE } });
    const r = await w.redeem("Tide Pool 7");
    check("signed out: asked to sign in, nothing read", r && r.ok === false && /Sign in/.test(r.msg) && w.reads.length === 0,
          JSON.stringify(r));
  }
  {
    const w = makeWorld({ codes: {} });
    const r = await w.redeem("March 3rd");
    check("a word that is not a code is an invalid code", r && r.ok === false && r.msg === "Invalid code.", r && r.msg);
    const e = await w.redeem("   ");
    check("an empty box is not sent to Firestore at all", e && e.ok === false && w.reads.length === 1,
          JSON.stringify(w.reads));
  }
  {
    const w = makeWorld({ codes: { "481902": ONE_SHOT } });
    const r = await w.redeem("481 902");
    check("an old 6-digit donation code still works, spaces and all",
          r && r.ok === true && w.reads.includes("481902") && w.granted.includes("/avatars/amberjack.png"),
          JSON.stringify(r));
    const w2 = makeWorld({ codes: { "481902": { ...ONE_SHOT, uses: 1 } } });
    const s = await w2.redeem("481902");
    check("...and a spent single-use code is still refused",
          s && s.ok === false && s.msg === "This code has already been used.", s && s.msg);
  }


  // ── H. the hang tag's code: built in, forgiving, no Firestore needed ─────
  console.log("\nH. FEBRUARY2ND works with NO Firestore document, however it is spelled");
  {
    check("printedCodeReward() exists in preview-app.js", !!PRINTED);
    check("codeTypoDistance() exists in preview-app.js", !!TYPO);
    const typed = ["FEBRUARY2ND", "February 2nd", "february-2nd", " FEBRUARY 2ND ",
                   "FEBUARARY2ND", "FEBUARY2ND", "FEBURARY2ND", "FEBRAURY2ND", "FEBUARY 2ND",
                   "FBRUARY2ND", "FERBUARY2ND", "FEBRUARY2N", "FEBRUARY 2", "Feb 2nd", "FEB2",
                   "February second", "2nd February", "2nd of February"];
    for (const t of typed) {
      const w = makeWorld({ codes: {} });                 // donation_codes is EMPTY
      const r = await w.redeem(t);
      check(`"${t}" unlocks the Fish`, r && r.ok === true && r.msg === "Unlocked Fish!"
            && w.granted.length === 1 && w.granted[0] === "/avatars/fish.png", JSON.stringify(r));
      check(`...without reading or writing Firestore`, w.reads.length === 0 && w.txs.length === 0,
            JSON.stringify({ reads: w.reads, txs: w.txs.length }));
      check(`...by claiming ONE of the first run's codes on the server, as FEBRUARY2ND`,
            w.server.calls.length === 1 && w.server.calls[0].path === "/api/printed-code/claim"
            && w.server.calls[0].body.code === "FEBRUARY2ND" && w.server.claimed === 1,
            JSON.stringify(w.server.calls));
    }
    {
      // THE LIMIT: one claim per first-run plushie. When they are gone, the
      // Fish cannot be had this way again, and nothing is granted.
      const server = makeServer({ cap: 250, claimed: 250 });
      const w = makeWorld({ codes: {}, server });
      const r = await w.redeem("FEBRUARY2ND");
      check("after the first run's 250 claims are gone, the code is sold out",
            r && r.ok === false && /first run/.test(r.msg) && w.granted.length === 0, JSON.stringify(r));
    }
    {
      const server = makeServer({ cap: 250, claimed: 249 });
      const w = makeWorld({ codes: {}, server });
      const r = await w.redeem("FEBRUARY2ND");
      check("claim 250 of 250 still works", r && r.ok === true && server.claimed === 250, JSON.stringify(r));
      w.owns.delete("/avatars/fish.png");                 // traded away
      const again = await w.redeem("FEBRUARY2ND");
      check("...and the same account cannot claim a second one, even after trading it away",
            again && again.ok === false && /already used/.test(again.msg) && server.claimed === 250,
            JSON.stringify(again));
    }
    {
      const w = makeWorld({ codes: {}, server: makeServer({ down: true }) });
      const r = await w.redeem("FEBRUARY2ND");
      check("server unreachable: says so, grants nothing", r && r.ok === false && /server/.test(r.msg)
            && w.granted.length === 0, JSON.stringify(r));
    }
    {
      const w = makeWorld({ codes: {}, owned: ["/avatars/fish.png"] });
      const r = await w.redeem("FEBRUARY2ND");
      check("an account that already has the Fish is told so, and nothing is granted",
            r && r.ok === false && r.msg === "You already own Fish." && w.granted.length === 0, JSON.stringify(r));
      check("...and it does not use up one of the first run's claims",
            w.server.calls.length === 0 && w.server.claimed === 0, JSON.stringify(w.server.calls));
    }
    {
      const w = makeWorld({ signedIn: false, codes: {} });
      const r = await w.redeem("FEBRUARY2ND");
      check("signed out: asked to sign in first", r && r.ok === false && /Sign in/.test(r.msg), JSON.stringify(r));
    }
    {
      // Even if someone DID make a Firestore doc for it, the built-in answer wins
      // and nothing is spent, so a half-made or misspelt doc can never break it.
      const w = makeWorld({ codes: { FEBRUARY2ND: { reward_type: "avatar", reward_img: "/avatars/WRONG.png", max_uses: 1, uses: 1 } } });
      const r = await w.redeem("FEBRUARY2ND");
      check("a broken Firestore doc of the same name cannot get in the way",
            r && r.ok === true && w.granted[0] === "/avatars/fish.png" && w.reads.length === 0, JSON.stringify(r));
    }
    for (const t of ["MARCH3RD", "FISH", "FEBRUARY", "2ND", "SECOND", "FEBRUARY2026", "DECEMBER2ND"]) {
      const w = makeWorld({ codes: {} });
      const r = await w.redeem(t);
      check(`"${t}" is NOT the tag's code`, r && r.ok === false && r.msg === "Invalid code." && w.granted.length === 0,
            JSON.stringify(r));
    }
    for (const t of ["481902", "222222", "200200"]) {
      const w = makeWorld({ codes: { [t]: ONE_SHOT } });
      const r = await w.redeem(t);
      check(`the 6-digit donation code ${t} still goes to Firestore`, w.reads.includes(t) && r && r.ok === true,
            JSON.stringify([w.reads, r]));
    }
  }

  // ── I. the Fish the code grants is the real one ─────────────────────────
  console.log("\nI. the real Fish: catalog entry, URL, art");
  {
    const start = APP.indexOf("const ANIMAL_AVATARS = [");
    let list = null;
    if (start >= 0) {
      // balance [ ] while skipping string literals AND comments (one apostrophe
      // in a // comment otherwise opens a "string" that never closes)
      let i = APP.indexOf("[", start), depth = 0, q = null;
      for (; i < APP.length; i++) {
        const ch = APP[i], nx = APP[i + 1];
        if (q) { if (ch === "\\") { i++; continue; } if (ch === q) q = null; continue; }
        if (ch === "/" && nx === "/") { i = APP.indexOf("\n", i); continue; }
        if (ch === "/" && nx === "*") { i = APP.indexOf("*/", i) + 1; continue; }
        if (ch === '"' || ch === "'" || ch === "`") { q = ch; continue; }
        if (ch === "[") depth++;
        else if (ch === "]") { depth--; if (depth === 0) break; }
      }
      try { list = vm.runInNewContext("(" + APP.slice(APP.indexOf("[", start), i + 1) + ")"); } catch (e) { list = null; }
    }
    check("read the real ANIMAL_AVATARS list", Array.isArray(list) && list.length > 50, list && list.length);
    const normalizeAvatarUrl = vm.runInNewContext("(" + grabFn("normalizeAvatarUrl") + ")");
    const byImg = {}; (list || []).forEach((a) => { byImg[a.img] = a; });
    const img = normalizeAvatarUrl("/avatars/fish.png");
    const fish = byImg[String(img).trim().toLowerCase()];
    check("the real normalizeAvatarUrl keeps /avatars/fish.png as it is", img === "/avatars/fish.png", img);
    check("...which is the Fish in the real catalog", !!fish && fish.id === "fish" && fish.name === "Fish",
          fish && fish.id);
    check("...a \"code\" unlock (kept forever, never re-earned)", !!fish && fish.unlock && fish.unlock.type === "code",
          fish && JSON.stringify(fish.unlock));
    check("...whose art is on disk", fs.existsSync(path.join(ROOT, "multiplayer/client/avatars/fish.png")));
  }

  // ── C. the gallery's own handlers ────────────────────────────────────────
  console.log("\nC. the gallery box: letters survive typing, Redeem hands the code on");
  {
    const startMark = 'const codeInput = $a("gal-code-input");';
    const s = APP.indexOf(startMark);
    const clickMark = APP.indexOf('codeBtn.addEventListener("click"', s);
    let block = null;
    if (s >= 0 && clickMark > s) {
      let depth = 0, j = APP.indexOf("{", clickMark);
      for (; j < APP.length; j++) {
        if (APP[j] === "{") depth++;
        else if (APP[j] === "}") { depth--; if (depth === 0) break; }
      }
      block = APP.slice(s, APP.indexOf(";", j) + 1);
    }
    check("found the gallery's code handlers in preview-app.js", !!block);
    if (block) {
      const el = (extra) => Object.assign({
        value: "", disabled: false, textContent: "", className: "", _l: {},
        addEventListener(t, f) { (this._l[t] = this._l[t] || []).push(f); },
        fire(t, ev) { return Promise.all((this._l[t] || []).map((f) => f(ev || {}))); },
        click() { return this.fire("click"); },
      }, extra || {});
      const input = el(), btn = el(), msg = el();
      const sent = [];
      const sb = {
        $a: (id) => ({ "gal-code-input": input, "gal-code-btn": btn, "gal-code-msg": msg })[id] || null,
        _galReadOnly: false,
        redeemDonationCode: async (c) => { sent.push(c); return { ok: true, msg: "Unlocked Fish!" }; },
        _galRenderHeader() {}, _galRenderTabs() {}, _galRenderGrid() {},
        setTimeout: () => 0, String, console,
      };
      vm.createContext(sb);
      if (NORMALIZE) vm.runInContext(NORMALIZE, sb);
      vm.runInContext(block, sb);

      input.value = "February 2nd";
      await input.fire("input");
      check("typing February 2nd leaves the letters in the box", /FEBRUARY/i.test(input.value), JSON.stringify(input.value));
      await btn.click();
      const norm = (c) => String(c || "").toUpperCase().replace(/[^A-Z0-9]/g, "");
      check("Redeem sends it on as FEBRUARY2ND", sent.length === 1 && norm(sent[0]) === "FEBRUARY2ND", JSON.stringify(sent));
      check("...and the box says what the server said", msg.textContent === "Unlocked Fish!", msg.textContent);

      input.value = "481902"; await input.fire("input"); await btn.click();
      check("a 6-digit code still goes through the same button", sent.length === 2 && norm(sent[1]) === "481902", JSON.stringify(sent));

      input.value = "  "; await input.fire("input"); await btn.click();
      check("an empty box asks for a code and sends nothing", sent.length === 2 && /code/i.test(msg.textContent) && msg.className.includes("err"),
            JSON.stringify([msg.textContent, msg.className]));
      check("...and no longer insists on six digits", !/6-digit/.test(msg.textContent), msg.textContent);

      // Now the whole chain, as a player meets it: the gallery's handlers with the
      // REAL redeemDonationCode behind them and an EMPTY donation_codes.
      const w = makeWorld({ codes: {} });
      const input2 = el(), btn2 = el(), msg2 = el();
      const sb2 = w.sandbox;
      Object.assign(sb2, {
        $a: (id) => ({ "gal-code-input": input2, "gal-code-btn": btn2, "gal-code-msg": msg2 })[id] || null,
        _galReadOnly: false, _galRenderHeader() {}, _galRenderTabs() {}, _galRenderGrid() {},
        setTimeout: () => 0, console,
      });
      vm.runInContext(block, sb2);
      input2.value = "FEBUARARY2ND";
      await input2.fire("input");
      await btn2.click();
      check('typed into the gallery box, "FEBUARARY2ND" says Unlocked Fish!', msg2.textContent === "Unlocked Fish!"
            && msg2.className.includes("ok"), JSON.stringify([msg2.textContent, msg2.className]));
      check("...grants the Fish", w.granted.includes("/avatars/fish.png"), JSON.stringify(w.granted));
      check("...and clears the box", input2.value === "", JSON.stringify(input2.value));
    }
  }

  // ── D. the markup ──────────────────────────────────────────────────────────
  console.log("\nD. the input itself");
  const tagM = HTML.match(/<input[^>]*id="gal-code-input"[^>]*>/);
  check("the gallery code input is in preview.html", !!tagM);
  const TAG = tagM ? tagM[0] : "";
  check("it no longer asks phones for a numbers-only keypad", !/inputmode="numeric"/.test(TAG), TAG);
  const ml = Number((TAG.match(/maxlength="(\d+)"/) || [])[1] || 0);
  check("it has room for FEBRUARY2ND and some (maxlength >= 16)", ml >= 16, ml);
  check("its label no longer says 6-digit", !/6-digit/i.test(TAG), TAG);
  check("phones capitalise as they type", /autocapitalize="characters"/.test(TAG), TAG);
  check("the Redeem button is still there", /id="gal-code-btn"/.test(HTML));

  // ── E. no em dash on screen ──────────────────────────────────────────────
  console.log("\nE. no em dash in anything this path can show");
  {
    const strings = [REDEEM, NORMALIZE || "", TAG].join("\n").match(/"[^"\n]*"|`[^`\n]*`/g) || [];
    const bad = strings.filter((s) => s.includes("—"));
    check("no em dash in the redeem strings or the input", bad.length === 0, bad.join(" | "));
  }

  // ── G. the admin script and the game agree ──────────────────────────────
  // The code is WRITTEN by scripts/make_donation_code.py --code and LOOKED UP by
  // the game. If the two spell it differently it can never be found.
  console.log("\nG. the admin script writes the code the game will look up");
  {
    const script = path.join(ROOT, "scripts/make_donation_code.py");
    if (!fs.existsSync(script) || !NORMALIZE) {
      console.log("  SKIP: scripts/make_donation_code.py is not here (it is untracked)");
    } else {
      const inputs = ["February 2nd", "february-2nd", "FEBRUARY2ND", " 481 902 ", "Feb. 2nd!", "ab_c-1"];
      let py = "";
      try {
        py = execFileSync("python3", ["-I", "-c",
          "import importlib.util,json,sys\n"
          + "s=importlib.util.spec_from_file_location('m', sys.argv[1]); m=importlib.util.module_from_spec(s); s.loader.exec_module(m)\n"
          + "print(json.dumps([m.normalize_code(x) for x in json.loads(sys.argv[2])]))",
          script, JSON.stringify(inputs)], { encoding: "utf8", timeout: 30000, stdio: ["ignore", "pipe", "pipe"] });
      } catch (_) { py = ""; }
      const norm = vm.runInNewContext("(" + NORMALIZE + ")");
      let got = null; try { got = JSON.parse(py); } catch (_) {}
      check("the script has a normalize_code()", Array.isArray(got), py.trim());
      if (Array.isArray(got)) inputs.forEach((x, i) =>
        check(`script and game agree on "${x}" (${norm(x)})`, got[i] === norm(x), got[i]));
    }
  }

  // ── F. the code fits, in real pixels ─────────────────────────────────────
  console.log("\nF. FEBRUARY2ND fits in the box (headless Chrome)");
  const CHROME = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/usr/bin/google-chrome", "/usr/bin/chromium",
  ].find((p) => fs.existsSync(p));
  if (!CHROME) {
    console.log("  SKIP: no Chrome/Chromium found");
  } else {
    // The whole gallery header as preview.html writes it (back button, title,
    // code box, sort menu), balanced on its own <div>s, so a wider box is
    // measured against the neighbours it really has.
    const bs = HTML.indexOf('<div class="gal-header">');
    let box = "";
    if (bs >= 0) {
      const re = /<\/?div\b[^>]*>/g; re.lastIndex = bs; let d = 0, m;
      while ((m = re.exec(HTML))) {
        d += m[0][1] === "/" ? -1 : 1;
        if (d === 0) { box = HTML.slice(bs, m.index + m[0].length); break; }
      }
    }
    check("found the gallery header markup", !!box && box.includes('id="gal-code-input"'));
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "gal-codes-"));
    const page = `<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Nunito:wght@700;800;900&display=swap">
<style>${CSS}</style></head><body>
<div id="avatar-gallery" class="open" aria-hidden="false">${box}</div>
<div id="out">RUNNING</div>
<script>
(async () => {
  const out = [];
  let font = "fallback";
  try { const f = await Promise.race([document.fonts.load('800 15px "Nunito"'),
                                      new Promise(r => setTimeout(() => r([]), 4000))]);
        if (f && f.length) font = "Nunito"; } catch (e) {}
  const inp = document.getElementById("gal-code-input");
  for (const v of ["FEBRUARY2ND", "481902"]) {
    inp.value = v;
    const cs = getComputedStyle(inp);
    const c = document.createElement("canvas").getContext("2d");
    c.font = cs.fontWeight + " " + cs.fontSize + " " + cs.fontFamily;
    const ls = parseFloat(cs.letterSpacing) || 0;
    const text = c.measureText(v).width + ls * v.length;
    const room = inp.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
    out.push(["FIT", v, Math.round(text), Math.round(room), inp.scrollWidth, inp.clientWidth, cs.width, cs.boxSizing, cs.fontSize, cs.letterSpacing].join(" "));
  }
  // Nothing in the header row may sit on top of the code box.
  const r = (sel) => { const e = document.querySelector(sel); return e ? e.getBoundingClientRect() : null; };
  const boxR = r(".gal-code-box");
  for (const sel of [".gal-hero-info", ".gal-sort-sel", ".gal-back-btn"]) {
    const o = r(sel);
    if (!o || !boxR) { out.push("OVER " + sel + " missing"); continue; }
    const sameRow = o.top < boxR.bottom && boxR.top < o.bottom;
    const overlap = sameRow && o.left < boxR.right && boxR.left < o.right;
    out.push("OVER " + sel + " " + (overlap ? "yes" : "no") + " " + Math.round(o.left) + "-" + Math.round(o.right)
             + " vs " + Math.round(boxR.left) + "-" + Math.round(boxR.right));
  }
  out.push("FONT " + font);
  document.getElementById("out").textContent = out.join("\\n");
})();
</script></body></html>`;
    const file = path.join(tmp, "page.html");
    fs.writeFileSync(file, page);
    for (const w of [{ label: "desktop 1440", size: "1440,900" }, { label: "laptop 1024", size: "1024,768" },
                     { label: "narrow 700", size: "700,900" }, { label: "phone 390", size: "390,844" }]) {
      let lines = null;
      for (let attempt = 0; attempt < 3 && !lines; attempt++) {
        const profile = fs.mkdtempSync(path.join(tmp, "profile-"));
        try {
          const dom = execFileSync(CHROME, [
            "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
            "--no-first-run", "--no-default-browser-check", "--disable-extensions",
            "--user-data-dir=" + profile, "--window-size=" + w.size,
            "--virtual-time-budget=10000", "--dump-dom", "file://" + file,
          ], { encoding: "utf8", maxBuffer: 1 << 26, stdio: ["ignore", "pipe", "ignore"], timeout: 90000 });
          const m = dom.match(/<div id="out">([\s\S]*?)<\/div>/);
          if (m && m[1] !== "RUNNING") lines = m[1].split("\n").filter(Boolean);
        } catch (_) {}
      }
      if (!lines) { check(w.label + ": the page produced measurements", false); continue; }
      const font = (lines.find((l) => l.startsWith("FONT ")) || "FONT ?").slice(5);
      for (const l of lines.filter((x) => x.startsWith("FIT "))) {
        const [, v, text, room, sw, cw] = l.split(" ");
        check(`${w.label} (${font}): ${v} fits (${text}px of text in ${room}px)`,
              Number(text) <= Number(room) && Number(sw) <= Number(cw) && Number(cw) > 50, l);
      }
      for (const l of lines.filter((x) => x.startsWith("OVER "))) {
        const [, sel, verdict] = l.split(" ");
        check(`${w.label}: ${sel} does not sit on the code box`, verdict === "no", l);
      }
    }
  }

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
})().catch((e) => { console.log("FAIL: " + (e && e.stack || e)); process.exit(1); });
