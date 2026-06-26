/* ══ Challenge of Challenges ══════════════════════════════════════════ */
(function () {
  "use strict";
  const NARWHAL_ICON = "/avatars/narwhal.png";

  function $coc(id) { return document.getElementById(id); }

  function _cocClose(id) {
    const m = $coc(id);
    if (m) m.classList.remove("open");
  }

  // ── Entry: clicking the & in any logo ────────────────────────
  document.addEventListener("click", function (e) {
    if (e.target && e.target.classList && e.target.classList.contains("coc-amp")) {
      _cocOpen();
    }
  });

  function _cocOpen() {
    const m = $coc("coc-confirm-modal");
    if (m) m.classList.add("open");
  }

  // ── Step 1: Confirmation ─────────────────────────────────────
  document.addEventListener("DOMContentLoaded", function () {
    $coc("coc-confirm-close")?.addEventListener("click", () => _cocClose("coc-confirm-modal"));
    $coc("coc-confirm-no")?.addEventListener("click",   () => _cocClose("coc-confirm-modal"));
    $coc("coc-confirm-yes")?.addEventListener("click",  () => {
      _cocClose("coc-confirm-modal");
      _cocShowRequirements();
    });

    // Backdrop click closes confirm and requirements modals
    $coc("coc-confirm-modal")?.addEventListener("click", function (e) {
      if (e.target === this) _cocClose("coc-confirm-modal");
    });
    $coc("coc-requirements-modal")?.addEventListener("click", function (e) {
      if (e.target === this) _cocClose("coc-requirements-modal");
    });

    // ── Step 2: Requirements ──────────────────────────────────
    $coc("coc-req-leave")?.addEventListener("click",   () => _cocClose("coc-requirements-modal"));
    $coc("coc-req-proceed")?.addEventListener("click", () => {
      _cocClose("coc-requirements-modal");
      _cocShowTrivia();
    });

    // ── Step 3: Trivia ────────────────────────────────────────
    $coc("coc-trivia-close")?.addEventListener("click", () => {
      _cocClose("coc-trivia-modal");
      if (typeof window.__fishShowStatsLobby === "function") window.__fishShowStatsLobby();
    });
    document.querySelectorAll(".coc-trivia-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        _cocHandleAnswer(this.dataset.cocAnswer);
      });
    });
  });

  // ── Requirements screen ───────────────────────────────────────
  function _cocGetStats() {
    if (typeof window.__fishGetMyStats === "function") {
      const s = window.__fishGetMyStats();
      if (s && typeof s === "object") return s;
    }
    if (typeof window.__fishGuestStatsGet === "function") {
      return window.__fishGuestStatsGet() || {};
    }
    return {};
  }

  function _cocShowRequirements() {
    const stats  = _cocGetStats();
    const wins   = Number(stats.normal_wins   || 0);
    const hours  = Number(stats.hours_played  || 0);
    const winsOk  = wins  >= 25;
    const hoursOk = hours >= 25;

    const winsIcon  = $coc("coc-req-wins-icon");
    const hoursIcon = $coc("coc-req-hours-icon");
    const winsVal   = $coc("coc-req-wins-val");
    const hoursVal  = $coc("coc-req-hours-val");
    const proceedBtn = $coc("coc-req-proceed");

    if (winsIcon)  {
      winsIcon.textContent = winsOk  ? "✓" : "✗";
      winsIcon.className   = "coc-req-icon " + (winsOk  ? "coc-check" : "coc-cross");
    }
    if (hoursIcon) {
      hoursIcon.textContent = hoursOk ? "✓" : "✗";
      hoursIcon.className   = "coc-req-icon " + (hoursOk ? "coc-check" : "coc-cross");
    }
    if (winsVal)  winsVal.textContent  = `${wins} / 25`;
    if (hoursVal) hoursVal.textContent = `${hours.toFixed(1)} / 25`;
    if (proceedBtn) proceedBtn.disabled = !(winsOk && hoursOk);

    const m = $coc("coc-requirements-modal");
    if (m) m.classList.add("open");
  }

  // ── Trivia screen ─────────────────────────────────────────────
  function _cocShowTrivia() {
    const resultEl = $coc("coc-trivia-result");
    if (resultEl) { resultEl.textContent = ""; resultEl.className = "coc-trivia-result"; }
    document.querySelectorAll(".coc-trivia-btn").forEach(function (b) {
      b.disabled = false;
      b.classList.remove("coc-correct", "coc-wrong");
    });
    const m = $coc("coc-trivia-modal");
    if (m) m.classList.add("open");
  }

  async function _cocHandleAnswer(answer) {
    // Disable all buttons immediately
    document.querySelectorAll(".coc-trivia-btn").forEach(function (b) { b.disabled = true; });
    const resultEl = $coc("coc-trivia-result");

    if (answer === "A") {
      const btn = document.querySelector('.coc-trivia-btn[data-coc-answer="A"]');
      if (btn) btn.classList.add("coc-correct");
      await _cocAwardNarwhal(resultEl);
    } else {
      const btn = document.querySelector(`.coc-trivia-btn[data-coc-answer="${answer}"]`);
      if (btn) btn.classList.add("coc-wrong");
      if (resultEl) {
        resultEl.textContent = "✗ Incorrect. Try again next time…";
        resultEl.className   = "coc-trivia-result coc-result-wrong";
      }
      setTimeout(function () {
        _cocClose("coc-trivia-modal");
        if (typeof window.__fishShowStatsLobby === "function") window.__fishShowStatsLobby();
      }, 2200);
    }
  }

  async function _cocAwardNarwhal(resultEl) {
    // Check if already unlocked
    const already = typeof window.__fishGetUnlockedIcons === "function"
      && window.__fishGetUnlockedIcons().includes(NARWHAL_ICON);

    if (resultEl) {
      resultEl.innerHTML = already
        ? "✓ You already own the <strong>Narwhal</strong> icon!"
        : "✓ Correct! You've unlocked the <strong>Narwhal</strong> avatar icon! 🦄";
      resultEl.className = "coc-trivia-result coc-result-correct";
    }

    if (!already && typeof window.__fishGrantUnlockedIcon === "function") {
      try { await window.__fishGrantUnlockedIcon(NARWHAL_ICON); } catch (err) { /* best-effort */ }
    }

    setTimeout(function () {
      _cocClose("coc-trivia-modal");
    }, 3500);
  }

})();
