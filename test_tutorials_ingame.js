#!/usr/bin/env node
/* The three IN-GAME tutorials, played end to end against a real server.
 *
 * test_tutorials.js drives the two tours that need no game server (Main Menu
 * and Competitive). The other three, The Game, Practice Game (B-Lob) and
 * Online Play & Controls, create a REAL room, start a REAL match and then ask
 * the player to draw, play, pay, scout and rearrange. Nothing about those steps
 * can be checked by reading the source: the selectors are all valid, the
 * handlers are all wired, and the tour still strands you if the card a step
 * asks for was spent two steps earlier or the slot it points at is off screen
 * on a phone.
 *
 * So this file plays them. It boots multiplayer_server.py, serves the real
 * client with its API base repointed at that server, and drives headless
 * Chrome over CDP in REAL time (a networked app cannot be fast-forwarded with
 * --virtual-time-budget: the server's turn clock is real).
 *
 * The driver behaves like a player who is FOLLOWING THE TUTORIAL, which is the
 * only honest way to run it:
 *   • press Next whenever it is offered,
 *   • otherwise do what the step asks, using the tour's own highlights as the
 *     instruction: play the SPOTLIGHTED hand card, pay with the GLOWING ones.
 * A driver that plays whatever it likes spends the cards the later steps are
 * built on and then reports dead ends that no real player would ever hit.
 *
 * Run:  node test_tutorials_ingame.js          (needs Chrome + python3)
 */
"use strict";

const fs   = require("fs");
const os   = require("os");
const path = require("path");
const http = require("http");
const { spawn, execFileSync } = require("child_process");

const ROOT   = __dirname;
const CLIENT = path.join(ROOT, "multiplayer/client");

let pass = 0, fail = 0;
function check(name, cond) {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { fail++; console.log("  ✗ FAIL: " + name); }
}
function done() {
  console.log("\n" + "=".repeat(42));
  console.log(`RESULT: ${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
}

const CHROME = [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
].find(p => fs.existsSync(p));

if (!CHROME) {
  console.log("SKIP: no Chrome/Chromium found: the in-game tours cannot be driven.");
  done();
}

// ════════════════════════════════════════════════════════════════════════
//  THE DRIVER (runs inside the page)
// ════════════════════════════════════════════════════════════════════════
// Every branch below is "what would a player who is reading this step do?".
// It reports, per step, the things a player would notice: is anything lit up,
// can I get past it, is the popup over the thing I am told to touch, is the
// thing I am told to touch even on screen.
const DRIVER = (tourKey) => `
(function () {
  if (window.__tutDriving) return; window.__tutDriving = true;
  var log = [], finished = false;
  window.__tutLog = function () { return finished ? JSON.stringify(log) : "PENDING"; };
  // A run that outlasts its budget has to be able to say WHERE it got to.
  // "timed out" on its own names no step and fixes nothing.
  window.__tutPartial = function () { return JSON.stringify(log); };
  function q(s) { return document.querySelector(s); }
  function vis(el) {
    if (!el) return false;
    var r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) return false;
    var cs = getComputedStyle(el);
    return cs.display !== "none" && cs.visibility !== "hidden";
  }
  function click(el) { if (el) try { el.click(); return true; } catch (e) {} return false; }
  function stop(why) { log.push({ end: why }); finished = true; clearInterval(iv); }

  // ── Doing what the step asks ──────────────────────────────────────────
  // The spotlight is the instruction. For a hand card that means "play this
  // one", which the app offers two ways: drag it, or pick it in the action
  // dropdown and press Play Card. The dropdown is the one a driver can hit
  // reliably, and it is the same action either way.
  function playSpotlightedCard(cardEl) {
    var entryUid = Number(cardEl.dataset.entryUid || 0);
    if (!entryUid) return false;
    var acts = [];
    try { acts = window.__ccLegalActions ? window.__ccLegalActions() : []; } catch (e) { return false; }
    var mine = acts.filter(function (x) {
      return x && (x.kind === "play_ocean" || x.kind === "play_to_ocean")
             && Number(x.card_uid) === entryUid;
    });
    if (!mine.length) return false;
    // A card is TWO animals, and each face is its own action with its own cost
    // and its own lane. Taking the first match plays whichever face happens to
    // be listed first: on the B-Lob "Play Lobster" step that is the Razorbill
    // Auk on the front of the same card, which costs 1, so the game drops into
    // payment mode for a card the step never mentioned. Pick the face the step
    // is actually pointing at: the one whose destination lane is the glowing
    // one, and failing that the free face (the tour's guided plays are free or
    // separately paid for).
    var a = null;
    var rings = document.querySelectorAll("#tut3-glows .tut3-glow-ring");
    for (var gi = 0; gi < rings.length && !a; gi++) {
      var gr = rings[gi].getBoundingClientRect();
      for (var mi = 0; mi < mine.length; mi++) {
        var act = mine[mi];
        var dir = String(act.face_direction || "").toLowerCase();
        var hub = document.querySelector('#pv-my-board .pv-ocean-hub[data-ocean-uid="' + act.ocean_uid + '"]');
        var lane = hub && dir ? hub.querySelector(".pv-lane-" + dir) : null;
        if (!lane) continue;
        var lr = lane.getBoundingClientRect();
        if (Math.abs(lr.left - gr.left) < 20 && Math.abs(lr.top - gr.top) < 20) { a = act; break; }
      }
    }
    // Then by NAME: the step's own title says which animal it means ("Play
    // Mangrove", "Play Lobster", "Play California Gull"). This is the rule that
    // matters for an Ocean, whose drop zone is the whole board rather than one
    // lane, so the lane match above cannot separate the two faces: without it
    // the cost-0 fallback happily plays the free creature on the BACK of the
    // Mangrove, no ★ fires, and the free-creature step that follows has nothing
    // left to point at.
    if (!a) {
      var title = ((q("#tut3-title") || {}).textContent || "").toLowerCase();
      a = mine.find(function (x) {
        var nm = String(x.face_name || "").toLowerCase();
        return nm && title.indexOf(nm) !== -1;
      }) || null;
    }
    if (!a) a = mine.find(function (x) { return Number(x.cost_to_pay || 0) === 0; }) || mine[0];
    var sel = q("#pv-action-select"), btn = q("#pv-play-btn");
    if (!sel || !btn || sel.disabled) return false;
    var opt = Array.prototype.find.call(sel.options, function (o) { return Number(o.value) === Number(a.index); });
    if (!opt) return false;
    sel.value = opt.value;
    sel.dispatchEvent(new Event("change", { bubbles: true }));
    return click(btn);
  }

  // ── Which hand card is a highlight actually on? ───────────────────────
  // NOT elementFromPoint. The hand is a fan: cards are painted outside their
  // layout boxes and overlap, so the element under a card's own centre is
  // routinely the NEIGHBOUR, which is why the app itself re-aims every real
  // pointer event through _handHitTestIdx. A driver that skips that plays the
  // card next to the one the tutorial spotlighted, and then every later step
  // asks for a card it just spent. Match on the rectangle instead: the
  // spotlight is drawn 8px outside its target and a glow ring 6px outside.
  function handCardAtRect(r, slack) {
    var cards = document.querySelectorAll("#pv-hand .pv-hand-card");
    var best = null, bestD = Infinity;
    for (var i = 0; i < cards.length; i++) {
      var cr = cards[i].getBoundingClientRect();
      var d = Math.abs(cr.left - r.left) + Math.abs(cr.top - r.top)
            + Math.abs(cr.width - r.width) + Math.abs(cr.height - r.height);
      if (d < bestD) { bestD = d; best = cards[i]; }
    }
    return bestD <= (slack || 60) ? best : null;
  }

  // Pay with the GLOWING cards and only those: the rest of the tutorial is
  // built on the cards it did not light up.
  function payWithGlowingCards() {
    var rings = document.querySelectorAll("#tut3-glows .tut3-glow-ring");
    var paid = false;
    for (var i = 0; i < rings.length; i++) {
      var card = handCardAtRect(rings[i].getBoundingClientRect());
      if (!card) continue;
      var pb = card.querySelector(".pv-pay-discard-btn");
      if (pb && /Use as Payment/.test(pb.textContent || "")) { click(pb); paid = true; }
    }
    // Deliberately NO "top up with any other card" fallback. Paying with a card
    // the tutorial did not light up is how a driver spends the Lobster that
    // "Play a Creature" is about to ask for, and then reports a dead end that
    // no player following the instructions would ever reach. If the glowing
    // cards do not cover the cost, that is the TOUR under-lighting a step, and
    // this test should say so rather than paper over it.
    var cf = q("#pv-payment-confirm-btn");
    if (cf && vis(cf) && !cf.disabled) { click(cf); return true; }
    return paid;
  }

  // The one gesture with no click alternative anywhere in the tutorials.
  function reorderHand() {
    var cards = document.querySelectorAll("#pv-hand .pv-hand-card");
    if (cards.length < 2) return false;
    // Carry real coordinates: dragstart re-aims through _handCardAt(clientX,
    // clientY) because the fan overlaps, and an event with no coordinates at
    // all takes a different branch from the one a finger takes.
    function mid(el) { var r = el.getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; }
    var a = cards[0], b = cards[cards.length - 1];
    var pa = mid(a), pb = mid(b);
    var dt = new DataTransfer();
    function fire(el, type, p) {
      return el.dispatchEvent(new DragEvent(type, {
        bubbles: true, cancelable: true, composed: true, dataTransfer: dt,
        clientX: p.x, clientY: p.y, screenX: p.x, screenY: p.y,
      }));
    }
    fire(a, "dragstart", pa);
    fire(b, "dragenter", pb);
    fire(b, "dragover",  pb);
    fire(b, "drop",      pb);
    fire(a, "dragend",   pb);
    return true;
  }

  // Is the thing the spotlight is on actually PRESSABLE at its middle? Both
  // real phone bugs this harness found were invisible to every other check:
  // the Board Size cluster sat on top of the draw deck (elementFromPoint
  // returned #bs-readout), and the Strategy guide's horizontal card row left
  // "Crustaceans" clipped by the right edge (elementFromPoint returned the
  // list behind it). In both, the ring was drawn in exactly the right place
  // over something no finger could reach.
  function centreIsPressable(hole, coach) {
    if (hole.classList.contains("nohole")) return true;
    var r = hole.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return true;
    var cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    if (cx < 0 || cy < 0 || cx > window.innerWidth || cy > window.innerHeight) return false;
    var at = document.elementFromPoint(cx, cy);
    if (!at || coach.contains(at)) return false;
    return !!at.closest("button, a, select, input, label, .pv-seat, .pv-hand-card, "
      + ".pv-pool-card, .pv-deck-pile, .gal-tile, .ph-snav-item, .tut3-opt, "
      + ".hsc-toggle, .pv-lane-up, .pv-lane-down, .pv-lane-left, .pv-lane-right, "
      + "[onclick], [role=button]");
  }

  // Whatever the spotlight is sitting on, pressed the way a finger would.
  // The walk-up matters: elementFromPoint returns the deepest node, which for a
  // seat is the <span> inside .pv-seat-name, and the handler that opens a
  // board lives on .pv-seat. (The old loop condition, "while el has no .click",
  // never iterated, because every HTMLElement has one.) So climb to the nearest
  // element that actually carries a handler or is a known control.
  function clickSpotlight(hole) {
    var r = hole.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    var el = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
    var coach = q("#tut3-coach");
    if (!el || (coach && coach.contains(el))) return false;
    var hit = el.closest("button, a, select, input, .pv-seat, .pv-hand-card, .pv-pool-card, "
                       + ".pv-deck-pile, .gal-tile, .ph-snav-item, .tut3-opt, [onclick], [role=button]");
    return click(hit || el);
  }

  var phase = 0, tick = 0, guard = 0, lastStep = "", acted = 0;
  var iv = setInterval(function () {
    if (++tick > 9000) { stop("timeout in phase " + phase); return; }
    try {
      var spl = q("#cc-fs-splash");
      if (spl && getComputedStyle(spl).display !== "none") { click(q("#ccfs-window")); spl.style.display = "none"; }
      var dev = q("#cc-device-screen");
      if (dev && getComputedStyle(dev).display !== "none") {
        click(q('[data-cc-device="computer"]'));
        if (tick > 30) dev.classList.add("cc-device-hidden");
      }
      if (phase === 0) { phase = 1; return; }
      if (phase === 1) {
        if (tick > 40) {
          var ls = q("#auth-loading-screen"); if (ls) ls.classList.add("hidden");
          var as = q("#auth-screen"); if (as) as.classList.remove("hidden");
        }
        var g = q("#auth-guest-btn");
        if (g && vis(g)) { click(g); phase = 2; }
        return;
      }
      if (phase === 2) {
        var go = q("#auth-guest-go-btn"), nk = q("#auth-guest-nick");
        if (go && vis(go)) {
          if (nk) { nk.value = "TutBot"; nk.dispatchEvent(new Event("input", { bubbles: true })); }
          click(go); phase = 3;
        }
        return;
      }
      if (phase === 3) {
        var lob = q("#auth-stats-lobby");
        if (lob && lob.classList.contains("visible")) {
          if (typeof window.__openTutorialChooser !== "function") { stop("__openTutorialChooser missing"); return; }
          window.__openTutorialChooser(); phase = 4;
        }
        return;
      }
      if (phase === 4) {
        var o = q('#tut3-chooser .tut3-opt[data-key="${tourKey}"]');
        if (o) { click(o); phase = 5; }
        return;
      }

      var coach = q("#tut3-coach");
      if (!coach || !coach.classList.contains("open")) { log.push({ finished: true }); stop("tour ended"); return; }
      var countTxt = (q("#tut3-count") || {}).textContent || "";
      var title = (q("#tut3-title") || {}).textContent || "";
      var hole = q("#tut3-hole"), nextBtn = q("#tut3-next"), pop = q("#tut3-pop");
      if (countTxt + title !== lastStep) { lastStep = countTxt + title; guard = 0; acted = 0; }
      guard++;
      if (guard < 10) return;                        // let the step settle
      if (guard === 10) {
        var hr = hole.getBoundingClientRect(), pr = pop.getBoundingClientRect();
        var over = !(pr.right < hr.left || pr.left > hr.right || pr.bottom < hr.top || pr.top > hr.bottom);
        var side = "none";
        if (!hole.classList.contains("nohole")) {
          if (pr.top >= hr.bottom - 1) side = "above";
          else if (pr.bottom <= hr.top + 1) side = "below";
          else if (pr.left >= hr.right - 1) side = "left";
          else if (pr.right <= hr.left + 1) side = "right";
          else side = "overlap";
        }
        log.push({
          step: countTxt, title: title,
          hasTarget: !hole.classList.contains("nohole"),
          targetW: Math.round(hr.width), targetH: Math.round(hr.height),
          // Fully inside the viewport, not merely "has a rectangle": a slot at
          // the bottom of a board that scrolled away is not something a player
          // can be told to drop a card on.
          targetOnScreen: hole.classList.contains("nohole") ||
            (hr.top >= -1 && hr.left >= -1 && hr.bottom <= window.innerHeight + 1 && hr.right <= window.innerWidth + 1),
          popCoversTarget: !hole.classList.contains("nohole") && over,
          popOffscreen: pr.left < -1 || pr.right > window.innerWidth + 1
                     || pr.top < -1 || pr.bottom > window.innerHeight + 1,
          nextDisabled: !!nextBtn.disabled,
          nextLabel: nextBtn.textContent,
          targetSide: side,
          // The live line under the text: "it is your turn" vs "waiting".
          live: ((q("#tut3-live") || {}).textContent || "").trim(),
          centreClickable: centreIsPressable(hole, coach),
          // Several steps deliberately spotlight the CONTEXT and glow the
          // control inside it ("Flip & Close" lights the whole card viewer and
          // rings its ✕). For those the question is whether the GLOW can be
          // pressed and stays clear of the popup, not the middle of the frame.
          glowPressable: (function () {
            var rings = document.querySelectorAll("#tut3-glows .tut3-glow-ring");
            for (var i = 0; i < rings.length; i++) {
              var rr = rings[i].getBoundingClientRect();
              if (rr.width < 2 || rr.height < 2) continue;
              var x = rr.left + rr.width / 2, y = rr.top + rr.height / 2;
              if (x < 0 || y < 0 || x > window.innerWidth || y > window.innerHeight) continue;
              var g = document.elementFromPoint(x, y);
              if (g && !coach.contains(g)) return true;
            }
            return null;   // no usable ring on this step
          })(),
          glows: document.querySelectorAll("#tut3-glows .tut3-glow-ring").length,
          drags: document.querySelectorAll("#tut3-drags .tut3-drag-ghost").length,
        });
      }
      // A miss at first sample is usually a re-render caught mid-paint. Give the
      // step the rest of its life to prove the highlight IS pressable.
      if (guard > 10 && log.length) {
        var lastRow = log[log.length - 1];
        if (lastRow && lastRow.step === countTxt && lastRow.centreClickable === false
            && centreIsPressable(hole, coach)) {
          lastRow.centreClickable = true;
        }
      }
      if (!nextBtn.disabled) { click(nextBtn); return; }
      // ── Interactive: do what the step is asking for ────────────────────
      // Re-tried on a beat, because most of these depend on the turn coming
      // back round from three computer players taking real turns.
      if (guard % 8 !== 0) return;
      acted++;
      // 1. Mid-payment? Finish the payment before anything else.
      if (vis(q("#pv-payment-mode-bar")) && payWithGlowingCards()) return;
      // 2. The spotlight is on a hand card ⇒ "play this card".
      var hr3 = hole.getBoundingClientRect();
      if (!hole.classList.contains("nohole") && hr3.width > 2) {
        var handCard = handCardAtRect(hr3, 80);
        if (handCard && playSpotlightedCard(handCard)) return;
      }
      // 3. The hand-rearrange step, the only drag with no click alternative.
      if (/Rearrange Your Hand/.test(title) && reorderHand()) return;
      // 4. A native <select> cannot be operated by clicking it. The tour has
      //    steps that ask for a specific option ("Switch to Private"), so walk
      //    the options and let the step's own advanceWhen decide when the right
      //    one is chosen, which is what a player poking at the dropdown does.
      if (!hole.classList.contains("nohole") && hr3.width > 2) {
        var atSel = document.elementFromPoint(hr3.left + hr3.width / 2, hr3.top + hr3.height / 2);
        var selEl = atSel && atSel.closest ? atSel.closest("select") : null;
        if (selEl && selEl.options.length > 1) {
          var next = (selEl.selectedIndex + 1) % selEl.options.length;
          selEl.selectedIndex = next;
          selEl.dispatchEvent(new Event("input",  { bubbles: true }));
          selEl.dispatchEvent(new Event("change", { bubbles: true }));
          return;
        }
      }
      // 5. A glow ring on a CONTROL is an instruction too: "Flip & Close" lights
      //    the card viewer's ✕ while the spotlight sits on the whole viewer, so
      //    pressing the middle of the spotlight hits the card, not the button.
      var rings2 = document.querySelectorAll("#tut3-glows .tut3-glow-ring");
      for (var ri = 0; ri < rings2.length; ri++) {
        var rr2 = rings2[ri].getBoundingClientRect();
        if (rr2.width < 2 || rr2.height < 2) continue;
        var cx2 = rr2.left + rr2.width / 2, cy2 = rr2.top + rr2.height / 2;
        if (cx2 < 0 || cy2 < 0 || cx2 > window.innerWidth || cy2 > window.innerHeight) continue;
        var g = document.elementFromPoint(cx2, cy2);
        if (!g || coach.contains(g)) continue;
        var gb = g.closest("button, a, [role=button]");
        if (gb && !gb.disabled) { click(gb); return; }
      }
      // 6. Anything else: press what is lit.
      if (clickSpotlight(hole)) return;
      if (acted > 45) {
        log.push({ stuck: countTxt + ": " + title });
        nextBtn.disabled = false; click(nextBtn);     // force on, so the rest is still audited
      }
    } catch (e) { log.push({ err: String(e && e.message) }); }
  }, 120);
})();`;

// ════════════════════════════════════════════════════════════════════════
//  HARNESS
// ════════════════════════════════════════════════════════════════════════
const GAME_PORT   = 8791 + (process.pid % 60);
const STATIC_PORT = 8951 + (process.pid % 60);
// One port PER RUN, never reused. Chrome does not release a debug port the
// instant it is killed, so a second launch on the same port hands /json/list
// the DYING browser's target: the run then connects to a browser that is on
// its way out, every Emulation override lands on nothing, and the viewport
// comes back as the raw window size. That looked exactly like "the phone
// emulation does not work", and only ever on the second run onward.
const DEBUG_PORT_BASE = 9333 + (process.pid % 40) * 10;
let _runSeq = 0;

const APP  = fs.readFileSync(path.join(CLIENT, "js/preview-app.js"), "utf8");
const HTML = fs.readFileSync(path.join(CLIENT, "preview.html"), "utf8");

// Repoint the app at the local server. Line 6 hard-codes production.
const PATCHED = APP.replace(
  /window\.__FISH_API_BASE__\s*=\s*"[^"]*";/,
  `window.__FISH_API_BASE__ = "http://127.0.0.1:${GAME_PORT}";`
);
if (PATCHED === APP) { console.log("FAIL: could not repoint __FISH_API_BASE__"); process.exit(1); }

const tmp = [];
function writeTmp(name, body) { const f = path.join(CLIENT, name); fs.writeFileSync(f, body); tmp.push(f); return name; }
writeTmp("_tutig_app.js", PATCHED);

function drivePage(device) {
  let page = HTML;
  page = page.replace('<script defer src="/js/device-select.js',
    `<script>try{sessionStorage.setItem("cc_device_type",${JSON.stringify(device)});}catch(e){}</script>\n<script defer src="/js/device-select.js`);
  page = page.replace(/\/js\/preview-app\.js\?[^"]*/, "/_tutig_app.js");
  return page;
}
// "computer" and "mobile" are the app's own two device values (device-select.js).
// A phone player picks mobile, which turns on the touch-drag shim and the
// zoomed-out in-game viewport, so a phone run has to say so.
const PAGES = {
  computer: writeTmp("_tutig_page.html", drivePage("computer")),
  mobile:   writeTmp("_tutig_page_m.html", drivePage("mobile")),
};

function get(url, timeoutMs = 4000) {
  return new Promise((resolve, reject) => {
    const req = http.get(url, res => {
      let b = ""; res.on("data", d => b += d); res.on("end", () => resolve(b));
    });
    req.on("error", reject);
    req.setTimeout(timeoutMs, () => { req.destroy(new Error("timeout")); });
  });
}
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function waitFor(fn, ms, every = 300) {
  const until = Date.now() + ms;
  while (Date.now() < until) {
    try { const v = await fn(); if (v) return v; } catch (_) {}
    await sleep(every);
  }
  return null;
}

// ── CDP ────────────────────────────────────────────────────────────────
class Cdp {
  constructor(ws) { this.ws = ws; this.id = 0; this.waiting = new Map(); }
  static async open(wsUrl) {
    const ws = new WebSocket(wsUrl);
    const c = new Cdp(ws);
    ws.addEventListener("message", ev => {
      let m; try { m = JSON.parse(ev.data); } catch (_) { return; }
      const w = c.waiting.get(m.id);
      if (w) { c.waiting.delete(m.id); w(m); }
    });
    await new Promise((res, rej) => {
      ws.addEventListener("open", res, { once: true });
      ws.addEventListener("error", rej, { once: true });
      setTimeout(() => rej(new Error("CDP connect timed out")), 15000);
    });
    return c;
  }
  // Every call is bounded. Without this one unanswered message hangs the whole
  // run for as long as the process is allowed to live: waitFor() awaits fn(),
  // so its own deadline can never fire while a CDP promise is still pending,
  // and a laptop that sleeps mid-run wakes up to a test still sitting there.
  send(method, params, timeoutMs = 20000) {
    const id = ++this.id;
    return new Promise(resolve => {
      const t = setTimeout(() => { this.waiting.delete(id); resolve(null); }, timeoutMs);
      this.waiting.set(id, m => { clearTimeout(t); resolve(m); });
      try { this.ws.send(JSON.stringify({ id, method, params: params || {} })); }
      catch (_) { clearTimeout(t); this.waiting.delete(id); resolve(null); }
    });
  }
  async eval(expr) {
    const r = await this.send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: false });
    return r && r.result && r.result.result ? r.result.result.value : undefined;
  }
  close() { try { this.ws.close(); } catch (_) {} }
}

// One run: a fresh Chrome, one tour, one window size.
async function run(tourKey, w, h, budgetMs, device) {
  const DEBUG_PORT = DEBUG_PORT_BASE + (_runSeq++);
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), "tutig-"));
  const chrome = spawn(CHROME, [
    "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
    "--no-first-run", "--no-default-browser-check", "--disable-extensions",
    `--user-data-dir=${profile}`, `--window-size=${Math.max(w, 500)},${h}`,
    `--remote-debugging-port=${DEBUG_PORT}`,
    `http://localhost:${STATIC_PORT}/${PAGES[device] || PAGES.computer}?game_window=1`,
  ], { stdio: "ignore" });

  let cdp = null;
  try {
    const target = await waitFor(async () => {
      const list = JSON.parse(await get(`http://127.0.0.1:${DEBUG_PORT}/json/list`));
      return list.find(t => t.type === "page" && t.webSocketDebuggerUrl) || null;
    }, 20000);
    if (!target) return null;
    cdp = await Cdp.open(target.webSocketDebuggerUrl);
    await cdp.send("Runtime.enable");
    // ── A phone has to actually BE a phone ──────────────────────────────
    // --window-size cannot do it: headless Chrome clamps its window to a
    // 500px minimum width, so --window-size=390,844 quietly lays the page out
    // at 500 and every "phone" assertion is really a second narrow-laptop
    // assertion. setDeviceMetricsOverride sets the LAYOUT viewport directly,
    // below that floor.
    //
    // Deliberately NOT mobile:true with a device scale factor. That makes
    // Chrome emulate a device whose own metrics then fight the page's viewport
    // meta, and the layout viewport comes back 980 wide instead of 390. What
    // this test needs is the CSS width the media queries see, which is exactly
    // what a plain override gives. The touch path is selected by the app's own
    // device switch below, not by Chrome.
    // Applied AND verified, with retries. A page target that is still coming
    // up accepts the command and lays out at the old size anyway, and a run
    // that then proceeds is silently testing the wrong width, which is the
    // whole failure this override exists to end. So measure it, and say so
    // plainly if it never takes.
    let real = "";
    for (let attempt = 0; attempt < 12; attempt++) {
      await cdp.send("Emulation.setDeviceMetricsOverride",
        { width: w, height: h, deviceScaleFactor: 1, mobile: false });
      real = await cdp.eval(`window.innerWidth + "x" + window.innerHeight`);
      if (real === `${w}x${h}`) break;
      await sleep(500);
    }
    if (real !== `${w}x${h}`) return { badViewport: real };
    if (device === "mobile") {
      await cdp.send("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 5 });
    }
    // Measured at BOOT, which is the only honest moment: on a real phone the
    // app rewrites the viewport meta when the game screen opens (device-select
    // widens it by ZOOM_OUT_PHONE so the whole board fits), so in-game
    // innerWidth is legitimately not the screen width any more.
    // Kill transitions so a measured rect is this step's, not the last one's.
    await cdp.eval(`(function(){var s=document.createElement("style");
      s.textContent="*,*::before,*::after{transition:none!important;animation:none!important}html{scroll-behavior:auto!important}";
      (document.head||document.documentElement).appendChild(s);})()`);
    // The app boots on its own clock; inject the driver once it is parsed.
    await waitFor(async () => await cdp.eval(`!!document.getElementById("cc-device-screen") || !!document.getElementById("auth-screen")`), 20000);
    await cdp.eval(DRIVER(tourKey));

    const raw = await waitFor(async () => {
      const v = await cdp.eval(`window.__tutLog ? window.__tutLog() : "PENDING"`);
      return (v && v !== "PENDING") ? v : null;
    }, budgetMs, 1000);
    if (!raw) {
      // Time ran out: report what it managed, so a slow run names the step it
      // died on instead of just going red.
      let rows = [];
      try { rows = JSON.parse(await cdp.eval(`window.__tutPartial ? window.__tutPartial() : "[]"`) || "[]"); } catch (_) {}
      return { timedOut: true, rows };
    }
    return { timedOut: false, rows: JSON.parse(raw) };
  } catch (e) {
    return null;
  } finally {
    if (cdp) cdp.close();
    try { chrome.kill("SIGKILL"); } catch (_) {}
    await new Promise(r => { chrome.once("exit", r); setTimeout(r, 5000); });
    try { fs.rmSync(profile, { recursive: true, force: true }); } catch (_) {}
  }
}

// ── Assertions shared by every run ─────────────────────────────────────
function audit(label, res) {
  if (!res) { check(`${label}: the harness reached the tour`, false); return; }
  if (res.badViewport) {
    check(`${label}: the browser really is that size (got ${res.badViewport})`, false);
    return;
  }
  if (res.timedOut) {
    const last = res.rows.filter(r => r.step).slice(-1)[0];
    check(`${label}: finishes inside its time budget` +
          (last ? ` (stalled on ${last.step}, "${last.title}"` +
                  `, target ${last.hasTarget ? last.targetW + "x" + last.targetH : "none"}` +
                  `${last.targetOnScreen === false ? ", OFF SCREEN" : ""}` +
                  `${last.live ? ", live: " + last.live : ""})` : ""), false);
    return;
  }
  const rows = res.rows;
  const steps = rows.filter(r => r.step);
  const stuck = rows.filter(r => r.stuck).map(r => r.stuck);

  check(`${label}: runs to the end`, rows.some(r => r.finished));
  check(`${label}: no step traps the player${stuck.length ? ": " + stuck.join(" | ") : ""}`, stuck.length === 0);
  check(`${label}: every step was reached (${steps.length} steps)`, steps.length >= 10);

  const blind = steps.filter(s => s.nextDisabled && !s.hasTarget).map(s => s.title);
  check(`${label}: nothing to click is never asked for${blind.length ? ": " + blind.join(", ") : ""}`, blind.length === 0);

  // A step that glows its control is judged on the glow: the popup sitting over
  // the middle of a full-screen viewer is fine when the ✕ it tells you to press
  // is out in the corner, clear of it.
  const covered = steps.filter(s => s.nextDisabled && s.popCoversTarget && s.glowPressable !== true)
                       .map(s => s.title);
  check(`${label}: the popup never covers the thing you must click${covered.length ? ": " + covered.join(", ") : ""}`, covered.length === 0);

  const off = steps.filter(s => s.popOffscreen).map(s => s.title);
  check(`${label}: the popup is always fully on screen${off.length ? ": " + off.join(", ") : ""}`, off.length === 0);

  // The one that only an in-game tour can fail: a board slot or a hand card
  // that has a rectangle but is scrolled out of the window.
  const gone = steps.filter(s => s.nextDisabled && !s.targetOnScreen).map(s => s.title);
  check(`${label}: what you must touch is on screen${gone.length ? ": " + gone.join(", ") : ""}`, gone.length === 0);

  // The check that catches a highlight drawn over something unreachable: an
  // overlay on top of it, or a horizontal scroller that clipped it away.
  const unreachable = steps.filter(s => s.nextDisabled && s.hasTarget
                                    && s.centreClickable === false && s.glowPressable !== true)
                           .map(s => s.title);
  check(`${label}: what a step highlights can actually be pressed${unreachable.length ? ": " + unreachable.join(", ") : ""}`,
        unreachable.length === 0);

  const slivers = steps.filter(s => s.hasTarget && (s.targetW < 24 || s.targetH < 10))
                       .map(s => `${s.title} ${s.targetW}x${s.targetH}`);
  check(`${label}: no spotlight is a sliver${slivers.length ? ": " + slivers.join(", ") : ""}`, slivers.length === 0);

  const misPointed = steps.filter(s => {
    const m = /^Click (above|below|left|right)/.exec(s.nextLabel || "");
    return m && s.targetSide !== "overlap" && s.targetSide !== "none" && m[1] !== s.targetSide;
  }).map(s => `${s.title}: says ${(/^Click (\w+)/.exec(s.nextLabel) || [])[1]}, target is ${s.targetSide}`);
  check(`${label}: "Click <way>" always points at the target${misPointed.length ? ": " + misPointed.join(", ") : ""}`,
        misPointed.length === 0);

  return steps;
}

// ════════════════════════════════════════════════════════════════════════
(async function main() {
  // ── The real game server ────────────────────────────────────────────
  const gameLog = fs.openSync(path.join(os.tmpdir(), `tutig-server-${process.pid}.log`), "w");
  const game = spawn("python3", [path.join(ROOT, "multiplayer_server.py"), "--port", String(GAME_PORT)],
                     { cwd: ROOT, stdio: ["ignore", gameLog, gameLog] });
  // ── The static client ───────────────────────────────────────────────
  const SERVER_SRC = `
    const fs=require("fs"),path=require("path"),http=require("http");
    const ROOT=${JSON.stringify(CLIENT)};
    const MIME={".html":"text/html",".js":"text/javascript",".css":"text/css",
      ".json":"application/json",".png":"image/png",".jpg":"image/jpeg",
      ".webp":"image/webp",".svg":"image/svg+xml",".ico":"image/x-icon",
      ".m4a":"audio/mp4",".mp3":"audio/mpeg",".woff2":"font/woff2"};
    http.createServer((req,res)=>{
      const rel=decodeURIComponent(req.url.split("?")[0]).replace(/^\\/+/,"");
      const f=path.join(ROOT,rel);
      if(!f.startsWith(ROOT)||!fs.existsSync(f)||fs.statSync(f).isDirectory()){res.writeHead(404);res.end();return;}
      res.writeHead(200,{"Content-Type":MIME[path.extname(f)]||"application/octet-stream"});
      fs.createReadStream(f).pipe(res);
    }).listen(${STATIC_PORT});
  `;
  const stat = spawn(process.execPath, ["-e", SERVER_SRC], { stdio: "ignore" });

  try {
    const up = await waitFor(async () => {
      const b = await get(`http://127.0.0.1:${GAME_PORT}/api/health`, 2000);
      return /"ok"\s*:\s*true/.test(b);
    }, 45000);
    console.log("\nthe real game server");
    check("multiplayer_server.py answers /api/health", !!up);
    if (!up) { done(); return; }

    const TOURS = [
      ["game",     "The Game"],
      ["practice", "Practice Game (B-Lob)"],
      ["online",   "Online Play & Controls"],
    ];
    // Desktop first, then a phone: the in-game layout is a different layout,
    // not the same one narrower (see the stacked hand zone and the measured
    // --pv-bottom-ui), and a board slot that scrolls out of the window there
    // is a step nobody can complete.
    const SIZES = [[1440, 900, "desktop", "computer"], [390, 844, "phone", "mobile"]];

    // node test_tutorials_ingame.js [tourKey] [device], to re-run one case
    // while chasing a failure instead of sitting through all six.
    const onlyTour = process.argv[2] || "";
    const onlyDev  = process.argv[3] || "";

    for (const [key, name] of TOURS) {
      if (onlyTour && key !== onlyTour) continue;
      for (const [w, h, dev, device] of SIZES) {
        if (onlyDev && dev !== onlyDev) continue;
        console.log(`\n${name}, ${dev} (${w}x${h})`);
        const res = await run(key, w, h, key === "practice" ? 480000 : 300000, device);
        const steps = audit(`${name} ${dev}`, res);
        if (steps && key !== "online") {
          // A guided play step must say, live, whether it is even your turn:
          // playing a card ends the turn, so between two guided plays the bots
          // really are going and the instruction cannot be followed yet.
          const plays = steps.filter(s => /^Play |Stack Another/.test(s.title));
          check(`${name} ${dev}: every guided play says whose turn it is`,
                plays.length > 0 && plays.every(s => /your turn|Waiting for the other/i.test(s.live || "")));
          check(`${name} ${dev}: every guided play shows where the card goes`,
                plays.length > 0 && plays.every(s => s.glows >= 1));
        }
      }
    }
  } finally {
    try { game.kill("SIGKILL"); } catch (_) {}
    try { stat.kill(); } catch (_) {}
    try { fs.closeSync(gameLog); } catch (_) {}
    tmp.forEach(f => { try { fs.unlinkSync(f); } catch (_) {} });
  }
  done();
})();
