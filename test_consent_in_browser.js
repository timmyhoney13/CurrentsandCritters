#!/usr/bin/env node
/* The consent gate, driven in a real browser.
 *
 * Run:  node test_consent_in_browser.js      (needs Google Chrome / Chromium)
 *
 * WHY A BROWSER AND NOT JUST THE SOURCE. test_signup_consent.js reads
 * preview-app.js and proves the right code is there. It cannot prove the code
 * RUNS, and the three functions added on 2026-10-15 sit in a 34,000-line file
 * where two things could silently break them:
 *
 *   1. SCOPE. ccTermsRecheck and ccPurchaseConsent are defined ABOVE the
 *      `const CC_TERMS_VERSION` / `let _ccSignupConsent` block they read, and
 *      `const`/`let` have a temporal dead zone. That is fine when they are
 *      only CALLED after start-up, and it would be a ReferenceError if any of
 *      them ever ran during it. Reading the file cannot tell you which.
 *      _phstGoToStripe also calls ccPurchaseConsent from inside a nested
 *      IIFE, which only works if the name is reachable from there.
 *
 *   2. THE ARMING. ccArmConsent must actually disable CREATE ACCOUNT on the
 *      first paint. A gate that is wired but starts enabled is not a gate.
 *
 * So this loads the real preview.html, lets the app boot, and then asks the
 * live page the only questions that matter: did anything throw, is the button
 * disabled, does ticking both controls enable it, and does an under-13 date
 * keep it shut.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const { execFileSync, spawn } = require("child_process");

const CLIENT = path.join(__dirname, "multiplayer/client");
const HTML = fs.readFileSync(path.join(CLIENT, "preview.html"), "utf8");

let pass = 0;
const failures = [];
function check(name, cond, extra) {
  if (cond) { console.log("  ✓ " + name); pass++; }
  else {
    console.log("  ✗ FAIL: " + name + (extra != null ? "  → " + extra : ""));
    failures.push(name);
  }
}

const CHROME = [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
].find((p) => fs.existsSync(p));

if (!CHROME) {
  console.log("SKIP: no Chrome/Chromium found.");
  process.exit(0);
}

const PORT = 9710 + (process.pid % 200);
const SERVER_SRC = `
  const fs=require("fs"),path=require("path"),http=require("http");
  const ROOT=${JSON.stringify(CLIENT)};
  const MIME={".html":"text/html",".js":"text/javascript",".css":"text/css",
    ".json":"application/json",".png":"image/png",".jpg":"image/jpeg",
    ".webp":"image/webp",".svg":"image/svg+xml",".ico":"image/x-icon",".m4a":"audio/mp4"};
  http.createServer((req,res)=>{
    const rel=decodeURIComponent(req.url.split("?")[0]).replace(/^\\/+/,"");
    const f=path.join(ROOT,rel);
    if(!f.startsWith(ROOT)||!fs.existsSync(f)||fs.statSync(f).isDirectory()){res.writeHead(404);res.end();return;}
    res.writeHead(200,{"Content-Type":MIME[path.extname(f)]||"application/octet-stream"});
    fs.createReadStream(f).pipe(res);
  }).listen(${PORT});
`;
const server = spawn(process.execPath, ["-e", SERVER_SRC], { stdio: "ignore" });
const cleanupFiles = [];
function cleanup() {
  for (const f of cleanupFiles) { try { fs.unlinkSync(f); } catch (_) {} }
  try { server.kill(); } catch (_) {}
}
process.on("exit", cleanup);

// Collect every uncaught error and rejection from the real boot, so a
// ReferenceError in the new code shows up here rather than as a dead button.
const DRIVER = `
<div id="out">PENDING</div>
<script>
(function () {
  var log = { errors: [] };
  window.addEventListener("error", function (e) {
    log.errors.push(String((e && e.message) || e));
  });
  window.addEventListener("unhandledrejection", function (e) {
    log.errors.push("rejection: " + String((e && e.reason && e.reason.message) || (e && e.reason)));
  });
  var out = document.getElementById("out");
  function done() { out.textContent = JSON.stringify(log); }
  function $(id) { return document.getElementById(id); }
  var tick = 0;
  var iv = setInterval(function () {
    if (++tick > 400) { log.fatal = "timeout"; done(); clearInterval(iv); return; }
    try {
      var spl = $("cc-fs-splash");
      if (spl && getComputedStyle(spl).display !== "none") spl.style.display = "none";
      if (tick > 20) {
        var ls = $("auth-loading-screen"); if (ls) ls.classList.add("hidden");
        var as = $("auth-screen"); if (as) as.classList.remove("hidden");
      }
      var step = $("auth-step-choose");
      if (!step || getComputedStyle(step).display === "none") return;
      if (tick < 25) return;
      clearInterval(iv);
      step.classList.add("is-armed");

      // The one name the Store deliberately exports. Everything else is
      // inside preview-app.js's IIFE and is unreachable from here, which is
      // correct: these checks go through the DOM instead.
      log.hasStripe = typeof window._phstGoToStripe === "function";

      // ── The create pane's gate, as a user meets it ───────────────────
      var dob = $("ao-new-dob"), box = $("ao-new-terms"), go = $("ao-new-go");
      log.hasControls = !!(dob && box && go);
      if (log.hasControls) {
        log.startsUnchecked = (box.checked === false);
        log.startsDisabled  = (go.disabled === true);

        // Old enough, but has not agreed: still shut.
        dob.value = "1990-01-01";
        dob.dispatchEvent(new Event("input", { bubbles: true }));
        log.ageOnlyDisabled = (go.disabled === true);

        // Agreed as well: open.
        box.checked = true;
        box.dispatchEvent(new Event("change", { bubbles: true }));
        log.bothEnabled = (go.disabled === false);

        // Under 13 with the box still ticked: shut again.
        dob.value = "2020-01-01";
        dob.dispatchEvent(new Event("input", { bubbles: true }));
        log.under13Disabled = (go.disabled === true);
        var err = $("ao-new-consent-err");
        log.under13Msg = err ? String(err.textContent || "").slice(0, 120) : "";

        // Back to a valid date: open again, so a typo is recoverable.
        dob.value = "1990-01-01";
        dob.dispatchEvent(new Event("input", { bubbles: true }));
        log.recovers = (go.disabled === false);
      }

      // ── The username pane's gate ─────────────────────────────────────
      var ndob = $("ao-nick-dob"), nbox = $("ao-nick-terms"), ngo = $("auth-nick-go-btn");
      log.hasNickControls = !!(ndob && nbox && ngo);
      if (log.hasNickControls) log.nickStartsDisabled = (ngo.disabled === true);

      // ── The purchase step ────────────────────────────────────────────
      // _phstGoToStripe requires a signed-in account before it shows the
      // acknowledgement, and this harness has no Firebase, so the dialog
      // itself cannot be opened here. What IS checked: calling the exported
      // entry point signed-out neither throws nor navigates, which is the
      // failure that would break the Store for everybody. The dialog's own
      // markup and gating are covered by test_purchase_consent.js.
      if (log.hasStripe) {
        try {
          log.hrefBefore = location.href;
          window._phstGoToStripe("https://buy.stripe.com/_probe", "Probe product");
          log.buyNoThrow = true;
          log.buyNoNav = (location.href === log.hrefBefore);
          log.buyNoDialogWhenSignedOut = !document.querySelector(".cc-purchase-ack");
        } catch (e) { log.buyThrew = String(e && e.message); }
      }

      done();
    } catch (e) { log.err = String(e && e.message); done(); }
  }, 40);
})();
</script>`;

const name = "_consent_probe.html";
const file = path.join(CLIENT, name);
fs.writeFileSync(file, HTML + DRIVER);
cleanupFiles.push(file);

let r = null;
for (let attempt = 0; attempt < 3 && !r; attempt++) {
  let dom = "";
  try {
    dom = execFileSync(CHROME, ["--headless=new", "--disable-gpu", "--no-sandbox",
      "--hide-scrollbars", "--window-size=1440,900", "--virtual-time-budget=30000",
      "--dump-dom", `http://localhost:${PORT}/${name}?game_window=1`],
      { encoding: "utf8", maxBuffer: 64e6, stdio: ["ignore", "pipe", "ignore"], timeout: 90000 });
  } catch (_) { continue; }
  const m = /<div id="out">([\s\S]*?)<\/div>/.exec(dom);
  const raw = m ? m[1].replace(/&quot;/g, '"').replace(/&amp;/g, "&")
                      .replace(/&lt;/g, "<").replace(/&gt;/g, ">") : "";
  if (!raw || raw === "PENDING") continue;
  try { r = JSON.parse(raw); } catch (_) {}
}

if (!r) {
  console.log("  ✗ FAIL: the page never reported back");
  process.exit(1);
}

console.log("\n1. The new code is reachable, and nothing threw");
check("the page booted without an uncaught error", !r.err && !r.fatal,
      r.err || r.fatal);
// The app logs its own expected network failures (no Firebase here), so only
// ReferenceErrors from the new names are a problem.
const refErrs = (r.errors || []).filter((e) =>
  /ccAgeOnDate|ccArmConsent|ccTermsRecheck|ccPurchaseConsent|CC_TERMS_VERSION|_ccSignupConsent|_ccPurchaseAckOpen|_ccTermsRecheckOpen/.test(e));
check("no ReferenceError from the consent code", refErrs.length === 0,
      refErrs.join(" | "));
check("the Store's entry point is exported", r.hasStripe === true);

console.log("\n3. The create pane gates CREATE ACCOUNT");
check("both controls and the button exist", r.hasControls === true);
check("the checkbox starts unticked", r.startsUnchecked === true);
check("the button starts DISABLED", r.startsDisabled === true);
check("a date alone does not open it", r.ageOnlyDisabled === true);
check("date plus agreement opens it", r.bothEnabled === true);
check("an under-13 date shuts it again", r.under13Disabled === true);
check("…and says so without inviting another answer",
      typeof r.under13Msg === "string" && /13 and older/.test(r.under13Msg)
      && !/try again/i.test(r.under13Msg), r.under13Msg);
check("a corrected date opens it again", r.recovers === true);

console.log("\n4. The username pane gates its own button");
check("its controls exist", r.hasNickControls === true);
check("CREATE USERNAME starts DISABLED", r.nickStartsDisabled === true);

console.log("\n5. The Store's entry point, signed out");
check("calling it does not throw", !r.buyThrew, r.buyThrew);
check("…and navigates nowhere", r.buyNoNav === true);
check("…and shows no acknowledgment, because there is no account yet",
      r.buyNoDialogWhenSignedOut === true);

console.log(`\nconsent-in-browser checks: ${pass + failures.length}`);
if (failures.length) {
  console.log(`${failures.length} FAILED`);
  failures.forEach((f) => console.log("  FAILED: " + f));
  process.exit(1);
}
console.log("all green");
