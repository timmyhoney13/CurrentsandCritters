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
  // AND interface together — not just text) in for detail or out for a
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
    var inGame = false;

    // screen.* is stable logical CSS pixels — it does NOT change with the
    // viewport meta or with a pinch, so it never feeds back on itself the way
    // innerWidth would once we widen the viewport. The device's SHORT edge
    // classifies phone vs tablet (intrinsic, orientation-independent); the
    // CURRENT-orientation width is what we actually widen.
    function screenDims() {
      var sw = screen.width  || window.innerWidth  || 360;
      var sh = screen.height || window.innerHeight || 640;
      var portrait = true;
      try { portrait = window.matchMedia("(orientation: portrait)").matches; } catch (e) {}
      return {
        cur:   Math.round(Math.max(320, Math.min(2048, portrait ? Math.min(sw, sh) : Math.max(sw, sh)))),
        short: Math.min(sw, sh)
      };
    }
    function setNormal() {
      if (meta && meta.getAttribute("content") !== NORMAL) meta.setAttribute("content", NORMAL);
    }
    function setGame() {
      if (!meta) return;
      var d   = screenDims();
      var dw  = d.cur;
      var zo  = d.short >= 700 ? ZOOM_OUT_TABLET : ZOOM_OUT_PHONE;
      var W   = Math.round(dw * zo);
      var fit = dw / W;  // = 1 / zo — the default "fit the whole game" scale
      var content =
        "width=" + W +
        ", initial-scale=" + fit.toFixed(3) +
        ", minimum-scale=" + fit.toFixed(3) +                  // can't zoom out past the overview
        ", maximum-scale=" + (fit * MAX_ZOOM_MULT).toFixed(3) + // reasonable zoom-in cap
        ", user-scalable=yes";
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
    // Re-fit (and reset to the overview) only on a real rotation — never during
    // a pinch, which doesn't fire orientationchange and must not be disturbed.
    window.addEventListener("orientationchange", function () { setTimeout(refresh, 250); });
  })();

  // The boot waits on this promise: resolves the moment a device is chosen,
  // or immediately if one is already saved for this session.
  var resolveReady;
  window.ccDeviceReady = new Promise(function (res) { resolveReady = res; });

  var scr = document.getElementById("cc-device-screen");
  var existing = read();
  if (existing) {
    // Already chosen this session — apply silently and never show the screen.
    apply(existing);
    if (scr) { scr.classList.add("cc-device-hidden"); scr.setAttribute("aria-hidden", "true"); }
    resolveReady(existing);
    return;
  }
  if (!scr) { resolveReady(""); return; } // fail-open: never trap the player out

  var done = false;
  function choose(device) {
    if (done) return;
    done = true;
    window.ccSetDevice(device);
    scr.classList.add("cc-device-hidden");
    scr.setAttribute("aria-hidden", "true");
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
