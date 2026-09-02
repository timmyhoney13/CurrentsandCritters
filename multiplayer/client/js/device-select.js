/* Currents and Critters: which device am I on?
 *
 * There used to be a full-screen gate here that asked the player to pick
 * COMPUTER or MOBILE before anything else loaded. It is gone. A browser can
 * tell what it is being touched with, so asking was a screen of work handed to
 * the player for information they should never have had to supply, and it cost
 * two real bugs: the artwork was baked into a PNG with invisible buttons over
 * it, and the click that dismissed the gate landed on whatever the NEXT screen
 * painted at that same pixel (see the click shield this file no longer needs).
 *
 * HOW IT DECIDES, in order of authority:
 *
 *   1. A REAL INPUT EVENT. The moment a finger or a mouse actually touches the
 *      page, that is the answer, and it is not a guess: a touch means mobile,
 *      a mouse click means computer. This is the rule the whole file exists to
 *      serve, so it OVERRIDES the opening guess whenever the two disagree.
 *   2. THE OPENING GUESS, from what the browser reports about its pointer
 *      hardware. Needed because the app has to lay itself out before anybody
 *      has touched anything.
 *   3. A MANUAL OVERRIDE, if one was set with ccSetDevice(). A player who says
 *      what they are on is never argued with.
 *
 * Mobile mode is not cosmetic. It turns on the finger drag-and-drop shim (the
 * cards use HTML5 drag events, which a touchscreen does not fire) and the
 * wider in-game viewport. Getting it wrong makes the game unplayable, which is
 * why a real input event is allowed to correct the guess at any time rather
 * than only once at boot: a 2-in-1 laptop that started with a mouse and is now
 * being tapped needs the touch shim from that moment on.
 */
(function () {
  "use strict";
  // ── Device-type state (computer | mobile) ───────────────────────────
  // window.CC_* flags + body classes let the rest of the app pick the right
  // input system. sessionStorage now only ever holds a MANUAL choice; the
  // automatic answer is recomputed every load, because the automatic answer is
  // free and a cached one can only be stale (a phone is never yesterday's
  // desktop, but a stored "computer" on a phone would be, forever).
  var KEY = "cc_device_type";          // manual override only
  var _device = "";                    // what we are applying right now
  var _fromRealInput = false;          // has a real finger/mouse spoken yet?

  function readManual() {
    try { var v = sessionStorage.getItem(KEY); return (v === "computer" || v === "mobile") ? v : ""; }
    catch (e) { return ""; }
  }
  function apply(device) {
    var mobile = device === "mobile";
    var changed = device !== _device;
    _device = device;
    window.CC_DEVICE = device;
    window.CC_IS_MOBILE = mobile;
    window.CC_IS_COMPUTER = device === "computer";
    var b = document.body;
    if (b) {
      b.classList.toggle("cc-device-mobile", mobile);
      b.classList.toggle("cc-device-computer", device === "computer");
    }
    // The in-game viewport is built from CC_IS_MOBILE, so a mode that changes
    // after boot has to rebuild it or the player keeps the old one.
    try { if (typeof window.ccRefreshGameViewport === "function") window.ccRefreshGameViewport(); } catch (e) {}
    // The rest of the room is told what we are on, so the chip on our seat is
    // right for everybody else. Only on a real change: this fires on the first
    // touch of a laptop that guessed "computer", which is exactly the moment
    // the table's copy of us goes stale.
    if (changed) {
      try { if (typeof window.ccOnDeviceChange === "function") window.ccOnDeviceChange(device); } catch (e) {}
    }
  }

  // ── How a device is SAID, everywhere it is printed ───────────────────
  // The waiting room, the in-game seats, the spectator list and the Friends
  // tab all show this, and they must not each invent their own wording. One
  // table, one answer. Returns null for "we were not told", which every caller
  // draws as nothing at all rather than as a guess.
  var LABELS = {
    computer: { icon: "\uD83D\uDCBB", label: "Computer", short: "PC" },
    mobile:   { icon: "\uD83D\uDCF1", label: "Mobile",   short: "Mobile" }
  };
  window.ccDeviceLabel = function (device) {
    var key = String(device || "").trim().toLowerCase();
    var row = LABELS[key];
    if (!row) return null;
    return {
      device: key,
      icon: row.icon,
      label: row.label,
      short: row.short,
      text: row.icon + " " + row.label,
      // Said in full on hover, because the icon alone is a rebus.
      title: key === "mobile"
        ? "Playing on a phone or tablet"
        : "Playing on a computer"
    };
  };
  // read() is kept for callers that want the current answer.
  function read() { return _device; }

  // ── The opening guess ────────────────────────────────────────────────
  // Only ever consulted before a real input event. Deliberately conservative
  // about calling something mobile: a touchscreen LAPTOP reports touch points
  // but is a computer until somebody actually touches it, and it is the touch
  // that will say so.
  function guess() {
    try {
      var mm = window.matchMedia ? window.matchMedia.bind(window) : null;
      var coarse = !!(mm && mm("(pointer: coarse)").matches);
      var fine   = !!(mm && mm("(pointer: fine)").matches);
      var hover  = !!(mm && mm("(hover: hover)").matches);
      var touchPoints = Number(navigator.maxTouchPoints || 0);
      // A real mouse present (fine pointer that can hover) means computer, even
      // on a machine that also has a touchscreen.
      if (fine && hover) return "computer";
      // No mouse, and the primary pointer is a fingertip.
      if (coarse || touchPoints > 0) return "mobile";
      // iPadOS ships a desktop-class user agent; multi-touch gives it away.
      if (touchPoints > 1) return "mobile";
      // Nothing conclusive (old browsers with no matchMedia): fall back to the
      // screen's short edge, which is the only other thing worth reading.
      var shortEdge = Math.min(screen.width || 9999, screen.height || 9999);
      return shortEdge > 0 && shortEdge <= 820 ? "mobile" : "computer";
    } catch (e) {
      return "computer";
    }
  }

  window.ccGetDevice = read;
  window.ccApplyDevice = apply;
  window.ccGuessDevice = guess;
  // A manual choice. Sticks for the session and outranks both the guess and
  // any later input, which is the point of saying it out loud.
  window.ccSetDevice = function (device) {
    if (device !== "computer" && device !== "mobile") return;
    try { sessionStorage.setItem(KEY, device); } catch (e) {}
    _fromRealInput = true;   // nothing may quietly overrule a stated choice
    apply(device);
  };
  // Drop the manual choice and go back to detecting.
  // ccResetDevice()               → clear + reload.
  // ccResetDevice({reload:false}) → clear and re-detect in place.
  window.ccResetDevice = function (opts) {
    try { sessionStorage.removeItem(KEY); } catch (e) {}
    _fromRealInput = false;
    if (!opts || opts.reload !== false) { try { location.reload(); } catch (e) {} }
    else apply(guess());
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
    // Re-apply for the CURRENT mode without changing whether we are in a
    // game. Called when detection flips computer <-> mobile after boot: the
    // widened viewport belongs to mobile mode, so it has to follow it.
    window.ccRefreshGameViewport = refresh;
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

  // ── Boot ─────────────────────────────────────────────────────────────
  // ccDeviceReady is kept because the app awaits it before it lays out, but it
  // no longer waits for a human: there is nothing to wait for. It resolves on
  // the spot with the opening guess (or a manual override), and the listeners
  // below correct that answer the instant a real finger or mouse turns up.
  var manual = readManual();
  apply(manual || guess());
  window.ccDeviceReady = Promise.resolve(_device);
  if (manual) _fromRealInput = true;   // a stated choice is never overruled

  // ── The rule: a touch means mobile, a click means computer ───────────
  // Read from the pointer event itself (pointerType), which is the browser
  // telling us what physically touched the screen rather than us inferring it.
  //
  // Two traps this has to survive:
  //   • A touch is followed ~300ms later by a SYNTHETIC mouse event, so an
  //     unguarded mousedown listener would flip every phone back to computer
  //     one tick after every tap. _lastTouchMs is that guard.
  //   • Pointer Events are missing on old browsers, where touchstart/mousedown
  //     are all there is, so both paths exist and agree.
  var GHOST_MS = 1200;     // a synthetic mouse event arrives well inside this
  var _lastTouchMs = 0;
  var _gestureOpen = false;   // is a press/drag currently in progress?

  // `startsGesture` marks the events that BEGIN a press (pointerdown,
  // touchstart, mousedown). Those are always allowed to decide: they fire
  // before the gesture has moved anywhere, which is the safe instant to switch
  // the drag shim. A mid-gesture signal (a stray mousemove during a finger
  // drag) is not, so it waits for the gesture to finish.
  function sawInput(kind, startsGesture) {
    if (kind === "touch") _lastTouchMs = Date.now();
    if (readManual()) return;          // a stated choice wins over observation
    _fromRealInput = true;
    var device = kind === "touch" ? "mobile" : "computer";
    if (device === _device) return;
    if (_gestureOpen && !startsGesture) return;
    apply(device);
  }

  function gestureEnd() { _gestureOpen = false; }

  var OPTS = { capture: true, passive: true };
  try {
    if (window.PointerEvent) {
      document.addEventListener("pointerdown", function (ev) {
        var t = ev && ev.pointerType;
        // An empty/unknown pointerType tells us nothing; the touch/mouse
        // listeners below still cover that browser.
        if (t === "touch" || t === "pen") sawInput("touch", true);
        else if (t === "mouse") sawInput("mouse", true);
        _gestureOpen = true;
      }, OPTS);
      document.addEventListener("pointerup", gestureEnd, OPTS);
      document.addEventListener("pointercancel", gestureEnd, OPTS);
    }
    document.addEventListener("touchstart", function () {
      sawInput("touch", true);
      _gestureOpen = true;
    }, OPTS);
    document.addEventListener("touchend", gestureEnd, OPTS);
    document.addEventListener("touchcancel", gestureEnd, OPTS);
    document.addEventListener("mousedown", function () {
      // Ignore the ghost mouse event every touch generates.
      if (Date.now() - _lastTouchMs < GHOST_MS) return;
      sawInput("mouse", true);
      _gestureOpen = true;
    }, OPTS);
    document.addEventListener("mouseup", gestureEnd, OPTS);
    // A mouse that MOVES is proof of a mouse before anything is clicked, and on
    // a touchscreen laptop it is the earliest honest signal there is. Ignored
    // while a touch is in play, because a finger drag synthesises mousemove.
    document.addEventListener("mousemove", function (ev) {
      if (Date.now() - _lastTouchMs < GHOST_MS) return;
      // A synthetic move reports no movement at all; a real mouse does.
      if (!ev || ((ev.movementX || 0) === 0 && (ev.movementY || 0) === 0)) return;
      sawInput("mouse", false);
    }, OPTS);
  } catch (e) {}

  // For tests and for anything that wants to know how sure we are.
  window.ccDeviceIsConfirmed = function () { return _fromRealInput; };
})();
