/* ══ Hidden Cephalopods ════════════════════════════════════════════════
   Three secret avatars unlocked by finding & clicking a cephalopod tucked
   naturally into the UI: Common Octopus disguised as the "Most Played
   Strategy" stat icon, Bobtail Squid as the "What's bob doing here" card at
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

  // Common Octopus, disguised as the "Most Played Strategy" stat icon.
  function _wireOcto() {
    const host = document.getElementById("ceph-octo-stat");
    if (!host || host._cephWired) return;
    host._cephWired = true;
    host.addEventListener("click", function (e) {
      e.stopPropagation();
      window.__fishGrantHiddenCeph("common-octopus", "/avatars/common-octopus.png", host);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", _wireOcto);
  } else {
    _wireOcto();
  }
})();
