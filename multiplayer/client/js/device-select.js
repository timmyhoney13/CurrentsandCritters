(function () {
  "use strict";
  // ── Device-type state (computer | mobile), per session ──────────────
  // sessionStorage = one choice per browser session/tab; a brand-new
  // session opening the game link is re-prompted. window.CC_* flags +
  // body classes let the rest of the app pick the right input system.
  var KEY = "cc_device_type";
  function read() {
    try { var v = sessionStorage.getItem(KEY); return (v === "computer" || v === "mobile") ? v : ""; }
    catch (e) { return ""; }
  }
  function apply(device) {
    var mobile = device === "mobile";
    window.CC_DEVICE = device;
    window.CC_IS_MOBILE = mobile;
    window.CC_IS_COMPUTER = device === "computer";
    var b = document.body;
    if (b) {
      b.classList.toggle("cc-device-mobile", mobile);
      b.classList.toggle("cc-device-computer", device === "computer");
    }
  }
  window.ccGetDevice = read;
  window.ccApplyDevice = apply;
  // Programmatically set the device without showing the screen.
  window.ccSetDevice = function (device) {
    if (device !== "computer" && device !== "mobile") return;
    try { sessionStorage.setItem(KEY, device); } catch (e) {}
    apply(device);
  };
  // Reset/change the selection later (console or a future UI hook).
  // ccResetDevice()         → clear + reload so the screen shows again.
  // ccResetDevice({reload:false}) → clear without reloading.
  window.ccResetDevice = function (opts) {
    try { sessionStorage.removeItem(KEY); } catch (e) {}
    window.CC_DEVICE = ""; window.CC_IS_MOBILE = false; window.CC_IS_COMPUTER = false;
    var b = document.body;
    if (b) b.classList.remove("cc-device-mobile", "cc-device-computer");
    if (!opts || opts.reload !== false) { try { location.reload(); } catch (e) {} }
  };

  // ── MOBILE GAME VIEWPORT (default zoom-out + native two-finger pinch) ──────
  // Desktop / Computer mode is NEVER touched. On Mobile, while the in-game
  // screen is showing, the game is laid out on a WIDER virtual viewport so a
  // lot more of the board + UI is visible at once (a gentle default zoom-out),
  // and the browser's own two-finger pinch then zooms the entire game (board
  // AND interface together, not just text) in for detail or out for a
  // strategic overview, with native two-finger panning while zoomed. This
  // scales the whole game uniformly without a CSS transform, so every existing
  // overlay/menu/modal/tooltip/fullscreen and the finger drag-and-drop keep
  // working exactly as before. Single-finger gestures stay with the game.
  // The Home/Lobby screens keep the normal device-width viewport.
  (function () {
    var meta = document.querySelector('meta[name="viewport"]');
    var NORMAL = "width=device-width, initial-scale=1.0";
    var ZOOM_OUT_TABLET = 1.9;   // tablets: show ~1.9x more by default
    var ZOOM_OUT_PHONE  = 1.5;   // phones: a gentler zoom-out so text stays legible
    var MAX_ZOOM_MULT   = 3;     // how far past the overview a player may pinch in
    var MIN_ZOOM_SLACK  = 0.9;   // see setGame(): a little room to pinch BELOW the fit
    var inGame = false;

    // ── How wide the page can actually be, in device-independent pixels ──────
    // This number decides initial-scale, so it has to be the width the browser
    // will really give the page, NOT the width of the display.
    //
    // screen.width is the display. On a notched phone held sideways Safari
    // carves the notch out of the page's box (viewport-fit is `contain`), so
    // screen.width over-reported the usable width by ~60px. width=screen*1.5
    // with initial-scale=1/1.5 then laid the game out ~7% wider than the space
    // it was being scaled into, and everything at the right end of the action
    // bar: End Turn, first: sat off the edge of the screen. Because
    // minimum-scale was pinned to that same wrong fit, a player could not even
    // pinch out to find it; the only way to reach End Turn was to pan sideways.
    //
    // documentElement.clientWidth IS the real box, but it only reads true while
    // the NORMAL meta is in effect (once we widen the viewport it reports the
    // widened width instead, which would feed back on itself). So measure on
    // the way in: entering a game always comes from a NORMAL-viewport screen,
    // and cache it per orientation. screen.* stays as the fallback.
    var metaIsGame = false;
    var fitW = { p: 0, l: 0 };
    function isPortrait() {
      try { return window.matchMedia("(orientation: portrait)").matches; }
      catch (e) { return (window.innerHeight || 0) >= (window.innerWidth || 0); }
    }
    function screenFallbackWidth() {
      var sw = screen.width  || window.innerWidth  || 360;
      var sh = screen.height || window.innerHeight || 640;
      return Math.round(Math.max(320, Math.min(2048,
        isPortrait() ? Math.min(sw, sh) : Math.max(sw, sh))));
    }
    function usableWidth() {
      var key = isPortrait() ? "p" : "l";
      if (!metaIsGame) {
        var cw = Math.round(document.documentElement.clientWidth || 0);
        if (cw >= 240) fitW[key] = Math.max(320, Math.min(2048, cw));
      }
      return fitW[key] || screenFallbackWidth();
    }
    function shortEdge() {
      var sw = screen.width || window.innerWidth || 360;
      var sh = screen.height || window.innerHeight || 640;
      return Math.min(sw, sh);
    }
    function setNormal() {
      metaIsGame = false;
      if (meta && meta.getAttribute("content") !== NORMAL) meta.setAttribute("content", NORMAL);
    }
    function setGame() {
      if (!meta) return;
      var dw  = usableWidth();
      var zo  = shortEdge() >= 700 ? ZOOM_OUT_TABLET : ZOOM_OUT_PHONE;
      var W   = Math.round(dw * zo);
      var fit = dw / W;  // ≈ 1 / zo, the default "fit the whole game" scale
      var content =
        "width=" + W +
        ", initial-scale=" + fit.toFixed(3) +
        // A little slack under the fit instead of pinning minimum-scale to it.
        // The fit is measured, not guessed, but if it is ever off by a few
        // percent on some browser the player must still be able to pinch out
        // and find whatever went over the edge, rather than be locked out of it.
        ", minimum-scale=" + (fit * MIN_ZOOM_SLACK).toFixed(3) +
        ", maximum-scale=" + (fit * MAX_ZOOM_MULT).toFixed(3) + // reasonable zoom-in cap
        ", user-scalable=yes";
      metaIsGame = true;
      // Idempotent: only write when the string actually changes, so re-renders
      // (which call ccGameViewport(true) every state tick) never re-apply
      // initial-scale and snap a player's in-progress pinch back to the overview.
      if (meta.getAttribute("content") !== content) meta.setAttribute("content", content);
    }
    function refresh() {
      if (inGame && window.CC_IS_MOBILE) setGame();
      else setNormal();
    }
    // Called with true when the in-game screen appears, false when it leaves.
    window.ccGameViewport = function (on) { inGame = !!on; refresh(); };
    // Re-fit (and reset to the overview) only on a real rotation, never during
    // a pinch, which doesn't fire orientationchange and must not be disturbed.
    // Drop back to NORMAL first so the new orientation's usable width can be
    // measured for real before the game viewport is rebuilt from it.
    window.addEventListener("orientationchange", function () {
      var wasInGame = inGame;
      setNormal();
      setTimeout(function () { if (wasInGame) refresh(); else setNormal(); }, 300);
    });
  })();

  // The boot waits on this promise: resolves the moment a device is chosen,
  // or immediately if one is already saved for this session.
  var resolveReady;
  window.ccDeviceReady = new Promise(function (res) { resolveReady = res; });

  var scr = document.getElementById("cc-device-screen");
  var existing = read();
  if (existing) {
    // Already chosen this session, apply silently and never show the screen.
    apply(existing);
    if (scr) { scr.classList.add("cc-device-hidden"); scr.setAttribute("aria-hidden", "true"); }
    resolveReady(existing);
    return;
  }
  if (!scr) { resolveReady(""); return; } // fail-open: never trap the player out

  // ── The click that closes this screen must not land on the next one ───────
  // Reported as "it prompted me for a username as if I'd clicked Play as
  // Guest, which I didn't". The auth screen's PLAY AS GUEST hot-zone is an
  // invisible box painted over login-bg.png, and it sits at exactly the spot
  // where the COMPUTER laptop and the MOBILE phone are painted HERE. Hide this
  // screen and the pixel under the player's cursor silently becomes that
  // button, so the second half of a double-click, or one impatient extra tap,
  // opened the guest nickname prompt for somebody who never chose guest.
  // (Measured, not guessed: test_auth_gate.js clicks the laptop and asks what
  // is under that pixel afterwards.)
  //
  // A transparent sheet over the page eats real pointer input for a beat. It is
  // a real ELEMENT rather than a capture listener on purpose: an element can
  // only ever swallow a physical click, so scripted .click(), the keyboard path
  // and assistive tech are all untouched.
  var SHIELD_MS = 700;   // covers a double-click (~500ms) and a touch ghost click
  function raiseClickShield() {
    var doc = document.body || document.documentElement;
    if (!doc) return;
    var sh = document.createElement("div");
    sh.id = "cc-gate-shield";
    sh.setAttribute("aria-hidden", "true");
    sh.style.cssText = "position:fixed;inset:0;background:transparent;border:0;" +
      "z-index:2147483001;pointer-events:auto;touch-action:manipulation;";
    doc.appendChild(sh);
    setTimeout(function () {
      if (sh.parentNode) sh.parentNode.removeChild(sh);
    }, SHIELD_MS);
  }

  var done = false;
  function choose(device) {
    if (done) return;
    done = true;
    window.ccSetDevice(device);
    scr.classList.add("cc-device-hidden");
    scr.setAttribute("aria-hidden", "true");
    raiseClickShield();
    resolveReady(device);
  }
  var halves = scr.querySelectorAll("[data-cc-device]");
  for (var i = 0; i < halves.length; i++) {
    (function (el) {
      var d = el.getAttribute("data-cc-device");
      el.addEventListener("click", function () { choose(d); });
      el.addEventListener("keydown", function (ev) {
        if (ev.key === "Enter" || ev.key === " " || ev.key === "Spacebar") { ev.preventDefault(); choose(d); }
      });
    })(halves[i]);
  }
})();
