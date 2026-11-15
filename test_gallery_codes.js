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
 *      against a stub Firestore: the typed words look up FEBRUARY2ND, the
 *      unlimited fish code grants the fish, an account that already has the
 *      fish is told so WITHOUT spending a use, and the old 6-digit codes,
 *      single-use and spent, behave exactly as before.
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
const APP  = fs.readFileSync(path.join(ROOT, "multiplayer/client/js/preview-app.js"), "utf8");
const HTML = fs.readFileSync(path.join(ROOT, "multiplayer/client/preview.html"), "utf8");
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
const REDEEM    = grabFn("redeemDonationCode");
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
function makeWorld({ signedIn = true, owned = [], codes = {} } = {}) {
  const docs = JSON.parse(JSON.stringify(codes));
  const reads = [];
  const owns = new Set(owned);
  const granted = [];
  const ref = (id) => ({ id });
  const db = {
    collection(name) {
      if (name !== "donation_codes") throw new Error("unexpected collection " + name);
      return { doc: (id) => ref(id) };
    },
    async runTransaction(fn) {
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
    },
  };
  vm.createContext(sandbox);
  if (NORMALIZE) vm.runInContext(NORMALIZE, sandbox);
  vm.runInContext(REDEEM, sandbox);
  return { redeem: (c) => vm.runInContext("redeemDonationCode", sandbox)(c), docs, reads, granted, owns };
}

const FISH_CODE = { reward_type: "avatar", reward_img: "/avatars/fish.png", label: "Fish",
                    max_uses: 0, unlimited: true, uses: 5 };
const ONE_SHOT  = { reward_type: "avatar", reward_img: "/avatars/amberjack.png", label: "Amberjack",
                    max_uses: 1, unlimited: false, uses: 0 };

(async () => {
  console.log("\nB. the real redeemDonationCode() against a stub Firestore");
  {
    const w = makeWorld({ codes: { FEBRUARY2ND: FISH_CODE } });
    const r = await w.redeem("February 2nd");
    check('typing "February 2nd" looks up donation_codes/FEBRUARY2ND',
          w.reads.includes("FEBRUARY2ND"), JSON.stringify(w.reads));
    check("...and unlocks the Fish", r && r.ok === true && w.granted.includes("/avatars/fish.png"), JSON.stringify(r));
    check("...saying so in words", r && r.msg === "Unlocked Fish!", r && r.msg);
    check("...and the unlimited code counts the use (5 to 6) without running out",
          w.docs.FEBRUARY2ND.uses === 6, w.docs.FEBRUARY2ND.uses);

    const again = await w.redeem("FEBRUARY2ND");
    check("the same account entering it again is told it already has the Fish",
          again && again.ok === false && /already own Fish/.test(again.msg), again && again.msg);
    check("...and that second try does NOT spend a use", w.docs.FEBRUARY2ND.uses === 6, w.docs.FEBRUARY2ND.uses);
  }
  {
    // A second account, a fresh world: one printed code serves every tag.
    const w = makeWorld({ codes: { FEBRUARY2ND: { ...FISH_CODE, uses: 900 } } });
    const r = await w.redeem("february-2nd");
    check("a different account with the same printed code also gets the Fish",
          r && r.ok === true && w.docs.FEBRUARY2ND.uses === 901, JSON.stringify(r));
  }
  {
    const w = makeWorld({ signedIn: false, codes: { FEBRUARY2ND: FISH_CODE } });
    const r = await w.redeem("February 2nd");
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
