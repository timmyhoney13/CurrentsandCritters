#!/usr/bin/env node
/* "This is the only time you will see this." Proving it.
 *
 * Run:  node test_consent_asked_once.js      (no browser, no network)
 *
 * ccTermsRecheck is the ONE thing in the game that puts the Privacy Policy in
 * front of a signed-in player who did not ask for it: the "Two quick things"
 * dialog, which asks for the age answer and the Terms agreement and links the
 * policy. Its own copy promises it is the only time they will see it, so a
 * second showing is the dialog calling itself a liar.
 *
 * The guard it shipped with was one line: does the profile carry the current
 * terms version. That is not enough, and the hole is a race rather than a
 * typo, which is why reading the source cannot find it:
 *
 *   onAuthStateChanged re-runs on EVERY Firebase token refresh and re-reads
 *   the profile each time. A read issued before the agreement was saved can
 *   arrive after the dialog closed. The profile in hand then has no
 *   terms_version, nothing is open, and the player is asked again.
 *
 * Two windows on one account is the same failure without needing the race.
 *
 * So: the real functions are sliced out of preview-app.js and run in a
 * sandbox, with a fake Firestore whose writes the test resolves or rejects on
 * demand, a localStorage that survives a simulated reload, and a DOM stub just
 * rich enough to open, fill in and submit the dialog. The markup itself is
 * covered by test_signup_consent.js and test_consent_in_browser.js; what is
 * under test here is HOW MANY TIMES the thing appears.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const APP = fs.readFileSync(
  path.join(__dirname, "multiplayer/client/js/preview-app.js"), "utf8");

let pass = 0;
const failures = [];
function check(name, cond, extra) {
  if (cond) { console.log("  ✓ " + name); pass++; }
  else {
    console.log("  ✗ FAIL: " + name + (extra != null ? "  → " + extra : ""));
    failures.push(name);
  }
}
function section(t) { console.log("\n" + t); }

// ── Slicing the real code out ────────────────────────────────────────────
// Each slice runs for real. If a rename moves one of these, the test dies
// loudly here rather than quietly passing against nothing.
function slice(startNeedle, endNeedle, label) {
  const i = APP.indexOf(startNeedle);
  if (i < 0) { console.log("CANNOT RUN: no " + label + " in preview-app.js"); process.exit(1); }
  const j = APP.indexOf(endNeedle, i + startNeedle.length);
  if (j < 0) { console.log("CANNOT RUN: no end of " + label); process.exit(1); }
  return APP.slice(i, j + endNeedle.length);
}

// The state block runs from the open flag down to (not including) the
// purchase gate's own flag, which is the next thing in the file.
const SRC_STATE = (() => {
  const i = APP.indexOf("    let _ccTermsRecheckOpen = false;");
  const j = APP.indexOf("    let _ccPurchaseAckOpen = false;", i);
  if (i < 0 || j < 0) { console.log("CANNOT RUN: no recheck state block"); process.exit(1); }
  return APP.slice(i, j);
})();
const SRC_RECHECK  = slice("function ccTermsRecheck(profile) {", "\n    }", "ccTermsRecheck");
const SRC_AGE      = slice("function ccAgeOnDate(dob, today) {", "\n    }", "ccAgeOnDate");
const SRC_TODAY    = slice("function ccTodayISO() {", "\n    }", "ccTodayISO");

const VERSION = (APP.match(/CC_TERMS_VERSION\s*=\s*"([0-9-]+)"/) || [])[1] || "";
const TEXT    = (APP.match(/const CC_TERMS_TEXT\s*=\s*\n?\s*"([^"]+)"/) || [])[1] || "";
if (!VERSION) { console.log("CANNOT RUN: no CC_TERMS_VERSION"); process.exit(1); }

check("the state block really carries the device marker",
  /CC_TERMS_ACK_PREFIX/.test(SRC_STATE), SRC_STATE.slice(0, 80));
check("…and the guard really reads it",
  /ccTermsAcked\(/.test(SRC_RECHECK));

// ── One "page load" ──────────────────────────────────────────────────────
// A fresh sandbox is a fresh page load. `store` is handed in, so a reload
// keeps the device's localStorage exactly as a browser would.
function pageLoad(store, opts) {
  opts = opts || {};
  const dialogs = [];           // every dialog ever appended, open or not
  const writes  = [];           // every Firestore write attempted
  const toasts  = [];
  const reports = [];
  let writeMode = opts.writeMode || "ok";   // "ok" | "fail" | "hang"
  const pending  = [];

  function stubEl() {
    const el = {
      value: "", checked: false, textContent: "", disabled: false,
      _h: {},
      addEventListener(t, f) { (el._h[t] = el._h[t] || []).push(f); },
      focus() {},
      fire(t) { (el._h[t] || []).forEach((f) => f({})); },
    };
    return el;
  }

  function stubWrap() {
    const els = {};
    const w = {
      className: "", innerHTML: "", _open: true,
      setAttribute() {},
      querySelector(sel) { return (els[sel] = els[sel] || stubEl()); },
      remove() { w._open = false; },
    };
    return w;
  }

  const sandbox = {
    console: { log() {}, warn() {}, error() {} },
    setTimeout, clearTimeout,
    localStorage: {
      getItem: (k) => (Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null),
      setItem: (k, v) => { store[k] = String(v); },
      removeItem: (k) => { delete store[k]; },
    },
    document: {
      body: { appendChild(w) { dialogs.push(w); } },
      createElement: () => stubWrap(),
    },
    firebase: { firestore: { FieldValue: { serverTimestamp: () => "SERVER_TS" } } },
  };
  sandbox.window = sandbox;
  sandbox.localStorage = sandbox.localStorage;

  // window.addEventListener, so the storage listener in the state block can
  // register and the test can fire a cross-window event at it.
  const winH = {};
  sandbox.addEventListener = (t, f) => { (winH[t] = winH[t] || []).push(f); };

  const code = `
    "use strict";
    let _authUser = null, _activeProfile = null;
    const CC_TERMS_VERSION = ${JSON.stringify(VERSION)};
    const CC_TERMS_TEXT = ${JSON.stringify(TEXT)};
    const CC_MIN_AGE = 13;
    function ccReport(k, d, l) { __reports.push([k, l]); }
    function ccErrDetail(e) { return String(e && e.message || e); }
    function showToast(m, t) { __toasts.push(String(m)); }
    const _db = {
      collection: () => ({ doc: (uid) => ({ set: (data, o) => {
        __writes.push({ uid, data, merge: !!(o && o.merge) });
        if (__mode() === "fail") return Promise.reject(new Error("firestore down"));
        if (__mode() === "hang") return new Promise((res, rej) => __pending.push({ res, rej }));
        return Promise.resolve();
      } }) }),
    };
    ${SRC_TODAY}
    ${SRC_AGE}
    ${SRC_STATE}
    ${SRC_RECHECK}
    // Handles the test drives the page through.
    __api = {
      signIn: (uid) => { _authUser = { uid }; },
      recheck: (profile) => ccTermsRecheck(profile),
      setActive: (p) => { _activeProfile = p; },
      active: () => _activeProfile,
    };
  `;

  sandbox.__writes = writes;
  sandbox.__toasts = toasts;
  sandbox.__reports = reports;
  sandbox.__pending = pending;
  sandbox.__mode = () => writeMode;
  sandbox.__api = null;
  vm.createContext(sandbox);
  vm.runInContext(code, sandbox, { filename: "recheck-slice.js" });

  const api = sandbox.__api;
  return {
    writes, toasts, reports, dialogs,
    signIn: api.signIn, setActive: api.setActive, active: api.active,
    recheck: api.recheck,
    mode(m) { writeMode = m; },
    settle() { pending.splice(0).forEach((p) => p.res()); },
    // How many dialogs are on the page right now.
    openCount: () => dialogs.filter((d) => d._open).length,
    // How many were ever shown to the player at all.
    shownCount: () => dialogs.length,
    // Fill the age + the tickbox and press the button.
    agree(i) {
      const w = dialogs[i == null ? dialogs.length - 1 : i];
      const dob = w.querySelector("#cc-tr-dob");
      const box = w.querySelector("#cc-tr-terms");
      const go  = w.querySelector(".cc-tr-go");
      dob.value = "1990-06-01"; dob.fire("input");
      box.checked = true; box.fire("change");
      if (go.disabled) return { armed: false };
      go.fire("click");
      return { armed: true };
    },
    storageEvent(key, val) {
      (winH.storage || []).forEach((f) => f({ key, newValue: val }));
    },
  };
}

const tick = () => new Promise((r) => setTimeout(r, 0));
const OLD = { nickname: "Reefy", friend_code: "AAAA" };   // predates the gate
const CUR = { nickname: "Reefy", terms_version: VERSION }; // already agreed

(async function run() {

// ════════════════════════════════════════════════════════════════════════
section("1. It comes up for an account that predates the gate");
// ════════════════════════════════════════════════════════════════════════
{
  const store = {};
  const p = pageLoad(store);
  p.signIn("uid-1"); p.setActive({ ...OLD });
  p.recheck({ ...OLD });
  check("asked once", p.shownCount() === 1, p.shownCount());
  check("…and an account that already agreed is never asked",
    (() => { const q = pageLoad({}); q.signIn("uid-9"); q.recheck({ ...CUR });
             return q.shownCount() === 0; })());
}

// ════════════════════════════════════════════════════════════════════════
section("2. Token refresh re-reads the profile. It must not ask again");
// ════════════════════════════════════════════════════════════════════════
{
  const store = {};
  const p = pageLoad(store);
  p.signIn("uid-1"); p.setActive({ ...OLD });

  // Four token refreshes while the dialog is still open, each handing back
  // the same pre-gate profile. This half already worked.
  for (let i = 0; i < 5; i++) p.recheck({ ...OLD });
  check("five callbacks, one dialog", p.shownCount() === 1, p.shownCount());

  // The player agrees. The write lands.
  const a = p.agree();
  check("the button arms once the age and the tickbox are in", a.armed);
  await tick(); await tick();
  check("the agreement was written", p.writes.length === 1);
  check("…with the pinned version", p.writes[0] && p.writes[0].data.terms_version === VERSION);
  check("…merged, so nothing else on the profile is lost", p.writes[0] && p.writes[0].merge);
  check("…with a server timestamp", p.writes[0] && p.writes[0].data.terms_accepted_at === "SERVER_TS");
  check("the dialog closed", p.openCount() === 0);

  // ── THE RACE ──────────────────────────────────────────────────────────
  // A read that went out BEFORE the save lands here AFTER it. This is the
  // one that showed the dialog a second time.
  p.recheck({ ...OLD });
  check("a stale read arriving after the save does NOT ask again",
    p.shownCount() === 1, "shown " + p.shownCount() + " times");
  for (let i = 0; i < 10; i++) p.recheck({ ...OLD });
  check("…nor does ten more of them", p.shownCount() === 1, p.shownCount());
}

// ════════════════════════════════════════════════════════════════════════
section("3. And not on the next visit either");
// ════════════════════════════════════════════════════════════════════════
{
  const store = {};
  const p1 = pageLoad(store);
  p1.signIn("uid-1"); p1.setActive({ ...OLD });
  p1.recheck({ ...OLD });
  p1.agree();
  await tick(); await tick();
  check("the device recorded the agreement",
    Object.keys(store).some((k) => store[k] === VERSION), JSON.stringify(store));

  // A new page load. Firestore is lagging and still hands back the old
  // profile: the dialog must stay away, and the version must be put back
  // without the player seeing anything.
  const p2 = pageLoad(store);
  p2.signIn("uid-1"); p2.setActive({ ...OLD });
  p2.recheck({ ...OLD });
  check("a fresh page load does not ask again", p2.shownCount() === 0, p2.shownCount());
  check("…it quietly repairs the stale profile instead", p2.writes.length === 1);
  check("…without replacing the real acceptance time",
    p2.writes[0] && !("terms_accepted_at" in p2.writes[0].data),
    p2.writes[0] && JSON.stringify(p2.writes[0].data));
  check("…and says nothing to the player", p2.toasts.length === 0);
  for (let i = 0; i < 6; i++) p2.recheck({ ...OLD });
  check("…repairing at most once per page load", p2.writes.length === 1, p2.writes.length);
}

// ════════════════════════════════════════════════════════════════════════
section("4. A failed save still means 'next time', not 'in a minute'");
// ════════════════════════════════════════════════════════════════════════
{
  const store = {};
  const p = pageLoad(store, { writeMode: "fail" });
  p.signIn("uid-1"); p.setActive({ ...OLD });
  p.recheck({ ...OLD });
  p.agree();
  await tick(); await tick();
  check("the dialog closed rather than trapping them", p.openCount() === 0);
  check("…they were told it will be asked again", p.toasts.length === 1, JSON.stringify(p.toasts));
  check("…the failure was reported", p.reports.some((r) => r[0] === "terms_recheck_save_failed"));
  check("…and nothing was recorded on the device, because nothing was saved",
    Object.keys(store).length === 0, JSON.stringify(store));

  // The next token refresh is one minute away. It must not re-ask.
  for (let i = 0; i < 8; i++) p.recheck({ ...OLD });
  check("the rest of this page load does not ask again", p.shownCount() === 1, p.shownCount());

  // "Next time" means the next page load, which is what they were promised.
  const p2 = pageLoad(store);
  p2.signIn("uid-1"); p2.setActive({ ...OLD });
  p2.recheck({ ...OLD });
  check("the next visit does ask, as promised", p2.shownCount() === 1, p2.shownCount());
}

// ════════════════════════════════════════════════════════════════════════
section("5. 'Per account' means per account");
// ════════════════════════════════════════════════════════════════════════
{
  const store = {};
  const p = pageLoad(store);
  p.signIn("uid-1"); p.setActive({ ...OLD });
  p.recheck({ ...OLD });
  p.agree();
  await tick(); await tick();

  // Somebody else's turn on the same computer. The first account's answer is
  // theirs alone and must not sign this one's name.
  p.signIn("uid-2"); p.setActive({ ...OLD });
  p.recheck({ ...OLD });
  check("a second account on the same device is asked for itself",
    p.shownCount() === 2, p.shownCount());
  p.agree();
  await tick(); await tick();
  check("…and each account has its own record on the device",
    Object.keys(store).length === 2, JSON.stringify(store));

  const back = pageLoad(store);
  back.signIn("uid-1"); back.setActive({ ...OLD });
  back.recheck({ ...OLD });
  check("…the first account coming back is still not asked", back.shownCount() === 0);

  // Both accounts come back in ONE page load, both holding a stale profile.
  // The quiet repair is per account, so the second one is not skipped because
  // the first one went first.
  const both = pageLoad(store);
  both.signIn("uid-1"); both.setActive({ ...OLD }); both.recheck({ ...OLD });
  both.signIn("uid-2"); both.setActive({ ...OLD }); both.recheck({ ...OLD });
  check("neither is asked", both.shownCount() === 0, both.shownCount());
  check("…and each one's profile is repaired, not just the first",
    both.writes.length === 2 && both.writes[0].uid === "uid-1" && both.writes[1].uid === "uid-2",
    JSON.stringify(both.writes.map((w) => w.uid)));
}

// ════════════════════════════════════════════════════════════════════════
section("6. A NEW version of the Terms is a new question, asked once");
// ════════════════════════════════════════════════════════════════════════
{
  // The device marker holds the version it accepted, not a bare "yes", so a
  // published change can still reach everybody exactly once.
  const store = { "cc_terms_ack_v1::uid-1": "1999-01-01" };
  const p = pageLoad(store);
  p.signIn("uid-1"); p.setActive({ ...OLD });
  p.recheck({ ...OLD });
  check("an old acceptance does not silence a new version", p.shownCount() === 1);
  p.agree();
  await tick(); await tick();
  check("…and the new version replaces it", store["cc_terms_ack_v1::uid-1"] === VERSION,
    store["cc_terms_ack_v1::uid-1"]);
  p.recheck({ ...OLD });
  check("…asked once, not twice", p.shownCount() === 1, p.shownCount());
}

// ════════════════════════════════════════════════════════════════════════
section("7. Two windows on one account is still one question");
// ════════════════════════════════════════════════════════════════════════
{
  const store = {};
  const w2 = pageLoad(store);
  w2.signIn("uid-1"); w2.setActive({ ...OLD });
  w2.recheck({ ...OLD });
  check("the second window has its own copy open", w2.openCount() === 1);

  // The player answers in the OTHER window. localStorage fires `storage` in
  // this one, which is exactly who needs telling.
  store["cc_terms_ack_v1::uid-1"] = VERSION;
  w2.storageEvent("cc_terms_ack_v1::uid-1", VERSION);
  check("answering in one window takes the other one down", w2.openCount() === 0);
  check("…and marks the profile it is holding as current",
    w2.active() && w2.active().terms_version === VERSION);
  w2.recheck({ ...OLD });
  check("…and this window never asks again", w2.shownCount() === 1, w2.shownCount());

  // An unrelated key, and a key for somebody else, must do nothing.
  const w3 = pageLoad({});
  w3.signIn("uid-1"); w3.setActive({ ...OLD });
  w3.recheck({ ...OLD });
  w3.storageEvent("fish_guest_nick", "Bob");
  w3.storageEvent("cc_terms_ack_v1::uid-OTHER", VERSION);
  check("another key, or another account's key, closes nothing",
    w3.openCount() === 1);
}

// ════════════════════════════════════════════════════════════════════════
section("8. A slow save cannot be answered twice");
// ════════════════════════════════════════════════════════════════════════
{
  const store = {};
  const p = pageLoad(store, { writeMode: "hang" });
  p.signIn("uid-1"); p.setActive({ ...OLD });
  p.recheck({ ...OLD });
  p.agree();
  await tick();
  check("the dialog stays while the save is in flight", p.openCount() === 1);
  // Token refreshes land all through the save.
  for (let i = 0; i < 5; i++) p.recheck({ ...OLD });
  check("…and they add nothing", p.shownCount() === 1, p.shownCount());
  p.settle();
  await tick(); await tick();
  check("the dialog closes when the save lands", p.openCount() === 0);
  check("…one write, not five", p.writes.length === 1, p.writes.length);
  p.recheck({ ...OLD });
  check("…and the stale read behind it asks nothing", p.shownCount() === 1);
}

// ════════════════════════════════════════════════════════════════════════
section("9. Storage switched off must not break the sign-in path");
// ════════════════════════════════════════════════════════════════════════
{
  // A private window can throw on every localStorage call. The guard then has
  // only the profile to go on, which is the behaviour this replaced, and the
  // one thing that must never happen is an exception on the way in.
  const store = {};
  Object.defineProperty(store, "cc_terms_ack_v1::uid-1", {
    get() { throw new Error("storage disabled"); },
    set() { throw new Error("storage disabled"); },
    enumerable: true, configurable: true,
  });
  const p = pageLoad(store);
  p.signIn("uid-1"); p.setActive({ ...OLD });
  let threw = null;
  try { p.recheck({ ...OLD }); p.agree(); } catch (e) { threw = e; }
  await tick(); await tick();
  check("nothing throws", threw === null, threw && threw.message);
  check("…they are still asked, and still let through", p.shownCount() === 1 && p.openCount() === 0);
}

// ════════════════════════════════════════════════════════════════════════
section("10. Nothing else shows the policy unprompted");
// ════════════════════════════════════════════════════════════════════════
{
  // The in-game reader is Settings → Legal and nowhere else. If a second
  // caller ever appears, this is where it gets noticed.
  const callers = APP.split("\n")
    .map((l, i) => [i + 1, l])
    .filter(([, l]) => /(?:^|[^.\w])ccShowPrivacy\s*\(/.test(l) && !/function ccShowPrivacy/.test(l));
  check("ccShowPrivacy has exactly one caller", callers.length === 1,
    callers.map(([n, l]) => n + ": " + l.trim()).join(" | "));
  const near = APP.slice(Math.max(0, APP.indexOf("function wireSettingsPrivacy")),
                         APP.indexOf("function wireSettingsPrivacy") + 600);
  check("…and it is a button in Settings", /settings-privacy-btn/.test(near));
  check("the policy reader is not opened on boot",
    !/\b(?:ccShowPrivacy|__ccShowPrivacy)\s*\(\s*\)\s*;/.test(
      APP.replace(/const open = \(\) => ccShowPrivacy\(\);/, "")));

  // One unprompted dialog, one call site.
  const rechecks = APP.split("\n")
    .map((l, i) => [i + 1, l])
    .filter(([, l]) => /(?:^|[^.\w])ccTermsRecheck\s*\(/.test(l) && !/function ccTermsRecheck/.test(l));
  check("ccTermsRecheck has exactly one caller", rechecks.length === 1,
    rechecks.map(([n, l]) => n + ": " + l.trim()).join(" | "));
  check("…and it is the signed-in reveal, which runs once per sign-in",
    /revealRegisteredLobby/.test(
      APP.slice(APP.lastIndexOf("function ", APP.indexOf("try { ccTermsRecheck(")),
                APP.indexOf("try { ccTermsRecheck("))));
}

console.log("\nasked-once checks: " + pass);
if (failures.length) {
  console.log("FAILED " + failures.length + ":");
  failures.forEach((f) => console.log("  - " + f));
  process.exit(1);
}
console.log("the policy is asked for once per account");
})();
