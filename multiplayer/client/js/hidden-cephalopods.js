/* ══ Hidden Cephalopods ════════════════════════════════════════════════
   Three secret avatars unlocked by finding & clicking a cephalopod tucked
   naturally into the UI: Common Octopus sitting in the "Friends Quick Stats"
   title on the Overview, Bobtail Squid as the "What's bob doing here" card at
   the end of achievements, and Cuttlefish above your in-game score row.
   (Giant Squid uses the Level 80 path.) */
(function () {
  "use strict";

  window.__fishGrantHiddenCeph = async function (id, img, el) {
    const owned = (typeof window.__fishGetUnlockedIcons === "function"
      ? window.__fishGetUnlockedIcons() : []).includes(img);
    if (!owned && typeof window.__fishGrantUnlockedIcon === "function") {
      try { await window.__fishGrantUnlockedIcon(img); } catch (e) { /* best-effort */ }
      window.__fishQueueAnimalUnlock?.(id);
      window.__fishShowAnimalUnlocks?.();
    } else if (el) {
      // Already discovered, quick acknowledging pulse.
      el.style.transition = "transform .2s";
      el.style.transform = "scale(1.2)";
      setTimeout(() => { el.style.transform = ""; }, 240);
    }
  };

  // Common Octopus, sitting in the "Friends Quick Stats" title. That header is
  // redrawn every time a friend is picked and put back, so the click is
  // delegated from the document instead of bound to the node: whichever copy
  // of the icon is on screen is the live one. Capture, so nothing the card
  // itself listens for fires as well.
  document.addEventListener("click", function (e) {
    const host = e.target && e.target.closest ? e.target.closest("#ceph-octo-stat") : null;
    if (!host) return;
    e.stopPropagation();
    window.__fishGrantHiddenCeph("common-octopus", "/avatars/common-octopus.png", host);
  }, true);
})();
