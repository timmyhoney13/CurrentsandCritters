/* Currents and Critters: the reward pop (js/reward-pop.js).
 *
 * ONE animation, shared by the two reward tracks: the card that pops into the
 * middle of the screen when the Critter Pass is unlocked, and when a tier is
 * collected off either the Critter Pass or the free Level Pass.
 *
 *   window.__ccRewardPop({ kind, eyebrow, title, detail })   -> true if shown
 *
 * THE RETURN VALUE IS LOAD-BEARING. level-pass.js and critter-pass.js say the
 * same news as a plain toast the moment this answers false, so a build that
 * never served this file loses the animation and not the news. That is also
 * why this file is allowed to be missing: every call site is wrapped, and the
 * two passes were shipping their toasts long before the card existed.
 *
 * kind is "unlock" (the once-a-season Critter Pass purchase: wider, gold) or
 * "reward" (a collected tier). Anything else reads as "reward".
 *
 * NOTHING here decides anything. It is handed finished words, in a finished
 * order, and shows them: no server, no state, no reward table. A payout is
 * still the server's answer and still re-read by the page that claimed it.
 *
 * WHY IT IS A QUEUE. A claim-all on the free pass finishes by claiming one
 * background tier at a time, each awaited in turn, so two cards can be asked
 * for inside the same second. They are shown one after the other rather than
 * the second wiping the first off the screen half-read. Past QUEUE_MAX it
 * answers false instead of piling up, and the caller's toast takes over: a
 * hundred cards in a row is not a celebration, it is a lock-out.
 *
 * IT IS NOT A DIALOG. It takes no focus, traps none, and has no button, so it
 * is role="status" with aria-live="polite": a screen reader hears the words
 * instead of being dropped into something it has to escape. A tap anywhere
 * closes it early, Escape closes it early, and it closes itself after HOLD_MS
 * whatever happens.
 */
(function () {
  "use strict";

  const ID = "cc-reward-pop";
  // How long a card stays up on its own. The gold hairline across its foot is
  // given exactly this duration from here, so the bar and the timer are ONE
  // number: a CSS duration that drifted from this one would either wind down
  // over a card that has gone or sit full under a card about to go.
  const HOLD_MS = 2600;
  // Must match the .ccRP-out animation in css/reward-pop.css.
  const OUT_MS = 220;
  const GAP_MS = 160;    // a breath between two queued cards
  const QUEUE_MAX = 4;

  let _queue = [];
  let _open = false;
  let _hold = null;
  let _next = null;

  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function overlay() {
    let ov = document.getElementById(ID);
    if (ov) return ov;
    if (!document.body) return null;
    ov = document.createElement("div");
    ov.id = ID;
    ov.setAttribute("role", "status");
    ov.setAttribute("aria-live", "polite");
    // There is no button to aim at, so the whole screen is the close.
    ov.addEventListener("click", close);
    document.body.appendChild(ov);
    return ov;
  }

  function onKey(e) {
    if (e.key === "Escape" || e.key === "Esc") close();
  }

  function show(o) {
    const ov = overlay();
    if (!ov) return;
    _open = true;
    const kind = String(o.kind || "reward") === "unlock" ? "unlock" : "reward";
    ov.innerHTML =
      '<div class="ccRP-stage">'
        + '<span class="ccRP-ring" aria-hidden="true"></span>'
        + '<span class="ccRP-ring ccRP-ring-2" aria-hidden="true"></span>'
        + '<div class="ccRP-card ccRP-is-' + kind + '">'
          + '<span class="ccRP-sheen" aria-hidden="true"></span>'
          + (o.eyebrow ? '<div class="ccRP-eyebrow">' + esc(o.eyebrow) + '</div>' : '')
          + '<div class="ccRP-title">' + esc(o.title) + '</div>'
          + (o.detail ? '<div class="ccRP-detail">' + esc(o.detail) + '</div>' : '')
          + '<span class="ccRP-hair" aria-hidden="true" style="animation-duration:'
            + HOLD_MS + 'ms"></span>'
        + '</div>'
      + '</div>';
    ov.classList.remove("ccRP-out");
    ov.classList.add("ccRP-open");
    document.addEventListener("keydown", onKey);
    clearTimeout(_hold);
    _hold = setTimeout(close, HOLD_MS);
  }

  function close() {
    clearTimeout(_hold);
    _hold = null;
    document.removeEventListener("keydown", onKey);
    if (!_open) return;
    _open = false;
    const ov = document.getElementById(ID);
    if (!ov) { drain(); return; }
    ov.classList.add("ccRP-out");
    // Torn down on a timer, never on animationend: a background tab fires no
    // animation events at all, and coming back to a card frozen over the page
    // with nothing left listening for the click is a bug nobody can close.
    setTimeout(() => {
      const cur = document.getElementById(ID);
      if (cur && !_open) {
        cur.classList.remove("ccRP-open", "ccRP-out");
        cur.innerHTML = "";
      }
      drain();
    }, OUT_MS);
  }

  function drain() {
    clearTimeout(_next);
    if (_open || !_queue.length) return;
    _next = setTimeout(() => {
      const o = _queue.shift();
      if (o) show(o);
    }, GAP_MS);
  }

  window.__ccRewardPop = function (o) {
    if (!o || !o.title) return false;
    if (!document.body) return false;
    // Full: answer honestly so the caller says it as a toast instead. Dropping
    // it and reporting success would lose a reward the player earned.
    if (_queue.length >= QUEUE_MAX) return false;
    _queue.push(o);
    if (!_open) drain();
    return true;
  };
})();
