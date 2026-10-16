#!/usr/bin/env node
/* Terms acceptance and the 13+ age screen, on every door that makes an account.
 *
 * Run:  node test_signup_consent.js      (no browser, no network needed)
 *
 * WHAT WAS WRONG. An audit on 2026-10-15 found that NOTHING in the client ever
 * asked a new player to accept the Terms of Service, and that the 13+ rule the
 * Privacy Policy states appeared nowhere in the interface: zero occurrences of
 * "at least 13" outside the legal documents themselves. So the two documents
 * that allocate risk, and the one age rule the product depends on, were
 * enforced entirely by hoping nobody looked.
 *
 * THE SHAPE OF THE FIX. There are two doors that end in a new account:
 *
 *   • the create pane  (#ao-pane-create)   username and password
 *   • the nickname pane (#ao-pane-nickname) Google, and any other provider
 *
 * Both now carry the same two controls, and finishNicknameSetup, which is the
 * ONE function both doors call, refuses to write a profile without them. That
 * is the important part: the gate is not on the button, it is on the function
 * that creates the account, so a second door added later cannot quietly skip
 * it.
 *
 * WHAT IS DELIBERATELY NOT CLAIMED. A date of birth is read to work out an age
 * and is then thrown away: only the boolean answer is stored. And an age
 * self-attestation is NOT COPPA compliance, it is an age screen. Nothing in
 * this file or in the policies says otherwise.
 */
"use strict";

const fs = require("fs");
const path = require("path");

const CLIENT = path.join(__dirname, "multiplayer/client");
const read = (p) => fs.readFileSync(path.join(CLIENT, p), "utf8");

const HTML  = read("preview.html");
const APP   = read("js/preview-app.js");
const TOS   = read("js/terms-of-service.js");
const PRIV  = read("js/privacy-policy.js");

let pass = 0;
const failures = [];
function check(name, cond) {
  if (cond) { console.log("  ✓ " + name); pass++; }
  else { console.log("  ✗ FAIL: " + name); failures.push(name); }
}
function section(t) { console.log("\n" + t); }

// The two panes, sliced out of the real markup so a control that exists
// somewhere else on the page cannot satisfy a check about this pane.
function pane(id) {
  const i = HTML.indexOf(`id="${id}"`);
  if (i < 0) return "";
  // To the start of the next pane, or the end of the form column.
  const rest = HTML.slice(i);
  const nxt = rest.indexOf('<div class="ao-pane"', 10);
  return nxt < 0 ? rest.slice(0, 9000) : rest.slice(0, nxt);
}
const CREATE = pane("ao-pane-create");
const NICK   = pane("ao-pane-nickname");

// Markup wraps, so "Terms of\n  Service" is the same sentence. Copy checks
// read a whitespace-collapsed copy; structural checks read the raw markup.
const flat = (s) => s.replace(/\s+/g, " ");
// The agreed SENTENCE, read as a reader sees it: tags stripped, whitespace
// collapsed. Checking it with the anchor markup still in makes the check
// about how the links are written rather than about what the sentence says.
const words = (s) => flat(s.replace(/<[^>]*>/g, " "));

const DOORS = [["the create pane", CREATE, "ao-new"], ["the nickname pane", NICK, "ao-nick"]];

// ════════════════════════════════════════════════════════════════════════
section("1. Both doors carry both controls");
// ════════════════════════════════════════════════════════════════════════

check("the create pane was found in the real markup", CREATE.length > 500);
check("the nickname pane was found in the real markup", NICK.length > 500);

for (const [name, src, pre] of DOORS) {
  check(`${name} has a terms checkbox`,
    new RegExp(`<input[^>]*type="checkbox"[^>]*id="${pre}-terms"`).test(src)
    || new RegExp(`<input[^>]*id="${pre}-terms"[^>]*type="checkbox"`).test(src));

  check(`${name}'s checkbox is NOT preselected`,
    !new RegExp(`id="${pre}-terms"[^>]*\\schecked`).test(src));

  check(`${name} states the agreement in the agreed words`,
    /I agree to the Terms of Service and acknowledge the Privacy Policy\s*\./
      .test(words(src)));

  check(`${name} links the Terms of Service`,
    /href="\/terms"/.test(src));
  check(`${name} links the Privacy Policy`,
    /href="\/privacy"/.test(src));

  // Opening a policy must not throw away a half-filled form.
  check(`${name}'s policy links open in a new tab, safely`,
    (src.match(/href="\/(terms|privacy)"[^>]*target="_blank"[^>]*rel="[^"]*noopener/g) || [])
      .length >= 2);

  check(`${name} asks for a date of birth`,
    new RegExp(`<input[^>]*id="${pre}-dob"[^>]*type="date"`).test(src)
    || new RegExp(`<input[^>]*type="date"[^>]*id="${pre}-dob"`).test(src));

  // Accessibility: a checkbox and a date field both need a real label bound
  // to them, or a screen reader announces an unnamed control.
  check(`${name}'s checkbox has a bound label`,
    new RegExp(`<label[^>]*for="${pre}-terms"`).test(src));
  check(`${name}'s date field has a bound label`,
    new RegExp(`<label[^>]*for="${pre}-dob"`).test(src));
}

// ════════════════════════════════════════════════════════════════════════
section("2. The age maths, on the real function");
// ════════════════════════════════════════════════════════════════════════

// Sliced and run, so these are assertions about shipped behaviour.
const AGEFN = (() => {
  const i = APP.indexOf("function ccAgeOnDate(");
  if (i < 0) return "";
  return APP.slice(i, APP.indexOf("\n    }", i) + 6);
})();
check("ccAgeOnDate exists in preview-app.js", AGEFN.length > 40);

let ccAgeOnDate = null;
if (AGEFN) {
  try { ccAgeOnDate = new Function(AGEFN + "; return ccAgeOnDate;")(); }
  catch (e) { /* reported by the checks below */ }
}
check("…and it evaluates on its own, with no page around it", typeof ccAgeOnDate === "function");

if (typeof ccAgeOnDate === "function") {
  check("a 13th birthday today is 13", ccAgeOnDate("2013-10-15", "2026-10-15") === 13);
  check("the day before a 13th birthday is 12", ccAgeOnDate("2013-10-16", "2026-10-15") === 12);
  check("the day after is 13", ccAgeOnDate("2013-10-14", "2026-10-15") === 13);
  check("a clear adult is counted correctly", ccAgeOnDate("1990-01-01", "2026-10-15") === 36);
  // Born later in the same calendar year: the bug a year-only screen has.
  check("a birthday later this year has not happened yet",
    ccAgeOnDate("2013-12-31", "2026-10-15") === 12);
  // 29 February, which is where naive date maths goes wrong.
  check("a leap-day birthday is handled", ccAgeOnDate("2012-02-29", "2026-02-28") === 13);
  check("nonsense is not an age", ccAgeOnDate("", "2026-10-15") === null);
  check("a future date is not an age", ccAgeOnDate("2030-01-01", "2026-10-15") === null);
}

// ════════════════════════════════════════════════════════════════════════
section("3. The gate is on the function that makes the account");
// ════════════════════════════════════════════════════════════════════════

const FINISH = (() => {
  const i = APP.indexOf("async function finishNicknameSetup(");
  if (i < 0) return "";
  return APP.slice(i, APP.indexOf("\n    $a(\"auth-nick-input\").addEventListener", i));
})();
check("finishNicknameSetup was found", FINISH.length > 500);

check("it refuses to run without a recorded consent",
  /_ccSignupConsent/.test(FINISH));
check("…and returns instead of creating a profile",
  /if \(!_ccSignupConsent[\s\S]{0,1200}?\n\s+return;/.test(FINISH));
check("the refusal happens BEFORE the profile is written",
  FINISH.indexOf("_ccSignupConsent") < FINISH.indexOf("saveNewProfile"));

check("the consent travels onto the profile document",
  /terms_version/.test(FINISH) && /age_attested_13_plus/.test(FINISH));
check("…and so does the moment it was given",
  /terms_accepted_at/.test(FINISH));
check("…and the exact wording that was agreed to",
  /terms_accepted_text/.test(FINISH));

check("the server is told too, on the call it already makes",
  /\/api\/user\/register/.test(FINISH) && /consent/.test(FINISH));

// DATA MINIMISATION. The date of birth is read to compute an age and is then
// gone. If it is ever written to a profile or posted to the server, this fails.
check("no date of birth is written to the profile",
  !/\bdob\b\s*:/.test(FINISH) && !/date_of_birth/.test(FINISH)
  && !/birth_date/.test(FINISH));

// ════════════════════════════════════════════════════════════════════════
section("4. Nothing is left enabled that should be gated");
// ════════════════════════════════════════════════════════════════════════

check("both panes' primary buttons are armed by one shared function",
  /function ccArmConsent\(/.test(APP));
check("the create pane's button is wired to it",
  /ccArmConsent\([\s\S]{0,120}ao-new/.test(APP));
check("the nickname pane's button is wired to it",
  /ccArmConsent\([\s\S]{0,120}ao-nick/.test(APP));

check("an under-13 answer is not invited to try a different one",
  !/try again|another date|re-?enter your/i.test(APP.slice(
    Math.max(0, APP.indexOf("ccArmConsent")), APP.indexOf("ccArmConsent") + 4000)));

// ════════════════════════════════════════════════════════════════════════
section("4b. An account that predates the gate is asked on its next visit");
// ════════════════════════════════════════════════════════════════════════

// Every account created before 2026-10-15 has no terms_version and no age
// answer on it. Gating only NEW sign-ups would have left the entire existing
// player base outside the thing the gate exists for, so the signed-in path
// asks once and records the answer.
check("there is a recheck for an account that already exists",
  /function ccTermsRecheck\(/.test(APP));

const RECHECK = (() => {
  const i = APP.indexOf("function ccTermsRecheck(");
  if (i < 0) return "";
  return APP.slice(i, APP.indexOf("\n    }", i) + 6);
})();

check("it compares the account against the pinned version",
  /CC_TERMS_VERSION/.test(RECHECK));
check("…and does nothing when the account is already current",
  /===\s*CC_TERMS_VERSION[\s\S]{0,120}return/.test(RECHECK));
check("it asks for the age answer too, because old accounts have none",
  /age_attested_13_plus/.test(RECHECK) && /dob/i.test(RECHECK));
check("it records a server timestamp, not the browser's clock",
  /serverTimestamp/.test(RECHECK));
check("a failed write does not lock the player out",
  /catch/.test(RECHECK));
check("the signed-in path actually calls it",
  /ccTermsRecheck\(/.test(APP.slice(APP.indexOf("function revealRegisteredLobby("),
                                   APP.indexOf("function revealRegisteredLobby(") + 9000)));

// ════════════════════════════════════════════════════════════════════════
section("5. The version pinned here is the version of the document");
// ════════════════════════════════════════════════════════════════════════

const ver = (APP.match(/CC_TERMS_VERSION\s*=\s*"([0-9-]+)"/) || [])[1] || "";
check("preview-app.js pins a terms version", /^\d{4}-\d{2}-\d{2}$/.test(ver));

// terms-of-service.js says `updated: "October 9, 2026"`. The pinned version
// must be that same date, or a player is recorded as accepting a version that
// was never published.
const MONTHS = { January: "01", February: "02", March: "03", April: "04", May: "05",
                 June: "06", July: "07", August: "08", September: "09",
                 October: "10", November: "11", December: "12" };
const m = TOS.match(/updated:\s*"([A-Za-z]+) (\d{1,2}), (\d{4})"/);
check("terms-of-service.js states an updated date", !!m);
if (m && ver) {
  const iso = `${m[3]}-${MONTHS[m[1]]}-${String(m[2]).padStart(2, "0")}`;
  check(`the pinned version matches the document (${iso})`, ver === iso);
}

// ════════════════════════════════════════════════════════════════════════
section("6. The policies say what the interface now does");
// ════════════════════════════════════════════════════════════════════════

check("the Privacy Policy still states the 13+ rule",
  /at least 13/.test(PRIV));
check("…and now says an age screen is what asks",
  /age screen|date of birth to work out|we ask for a date of birth/i.test(PRIV));
check("the Privacy Policy does not claim COPPA compliance",
  !/COPPA compliant|compliant with COPPA|fully compliant/i.test(PRIV));
check("the Privacy Policy says the date of birth is not kept",
  /we do not keep|not stored|we keep only/i.test(PRIV));
check("the Terms record that acceptance is now required",
  /accept these Terms|agree to these Terms/i.test(TOS));

// ────────────────────────────────────────────────────────────────────────
console.log(`\nsignup-consent checks: ${pass + failures.length}`);
if (failures.length) {
  console.log(`${failures.length} FAILED`);
  failures.forEach((f) => console.log("  FAILED: " + f));
  process.exit(1);
}
console.log("all green");
