(function () {
  "use strict";

  // ── Completion tracking ───────────────────────────────────────────
  const LS = "tut3_done_v1";
  const OSPREY = "/avatars/osprey.png";
  function getDone() { try { return JSON.parse(localStorage.getItem(LS) || "{}") || {}; } catch (_) { return {}; } }
  function setDone(k) { const d = getDone(); d[k] = true; try { localStorage.setItem(LS, JSON.stringify(d)); } catch (_) {} grantOspreyIfComplete(); }
  function allDone() { const d = getDone(); return !!(d.menu && d.game && d.practice && d.online && d.competitive); }
  async function grantOspreyIfComplete() {
    if (!allDone()) return;
    try {
      const owned = (typeof window.__fishGetUnlockedIcons === "function") && window.__fishGetUnlockedIcons().includes(OSPREY);
      if (owned) return;
      if (await window.__fishGrantUnlockedIcon?.(OSPREY)) {
        window.__fishQueueAnimalUnlock?.("osprey");
        window.__fishShowAnimalUnlocks?.();
      }
    } catch (_) {}
  }

  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // ════════════════════════════════════════════════════════════════
  //  STYLES (lobby vibe: light frosted cyan)
  // ════════════════════════════════════════════════════════════════
  const css = `
  #tut3-chooser { position:fixed; inset:0; z-index:100040; display:none; align-items:center; justify-content:center; padding:22px 14px; background:rgba(6,26,46,.55); backdrop-filter:blur(4px); -webkit-backdrop-filter:blur(4px); overflow:auto; }
  #tut3-chooser.open { display:flex; }
  .tut3-card { width:100%; max-width:640px; margin:auto; background:linear-gradient(165deg,#fbfeff 0%,#eaf6ff 55%,#e3f0fb 100%); border:1px solid rgba(120,200,235,.6); border-radius:24px; box-shadow:0 24px 70px rgba(10,60,110,.45); padding:22px 22px 18px; font-family:"Nunito",sans-serif; color:#0f3a5e; }
  .tut3-hd { text-align:center; margin-bottom:6px; }
  .tut3-hd h2 { font-family:"Cinzel",serif; font-size:1.5rem; font-weight:900; color:#1769b0; margin:0; letter-spacing:.4px; }
  .tut3-hd p { font-size:.86rem; color:#4f7ba6; margin:5px 0 0; }
  .tut3-opts { display:flex; flex-direction:column; gap:12px; margin:16px 0 12px; }
  .tut3-opt { display:flex; align-items:center; gap:14px; text-align:left; background:#fff; border:1.5px solid rgba(130,200,235,.55); border-radius:16px; padding:14px 16px; cursor:pointer; transition:transform .12s, box-shadow .12s, border-color .12s; font-family:inherit; }
  .tut3-opt:hover:not(.tut3-locked) { transform:translateY(-2px); box-shadow:0 8px 24px rgba(30,120,190,.2); border-color:#3aa8e0; }
  .tut3-opt.tut3-locked { opacity:.6; cursor:default; }
  .tut3-opt-ico { font-size:2rem; width:46px; height:46px; flex-shrink:0; display:flex; align-items:center; justify-content:center; background:linear-gradient(145deg,#d7f1ff,#bfe4fb); border-radius:13px; }
  .tut3-opt-body { flex:1; min-width:0; }
  .tut3-opt-title { font-weight:900; font-size:1.05rem; color:#0f4d86; }
  .tut3-opt-desc { font-size:.8rem; color:#5a83a8; margin-top:2px; line-height:1.35; }
  .tut3-opt-status { flex-shrink:0; font-size:.72rem; font-weight:800; padding:5px 10px; border-radius:999px; }
  .tut3-st-start { background:#1f9ad7; color:#fff; }
  .tut3-st-done { background:#1fbb8a; color:#fff; }
  .tut3-st-soon { background:#e6eef5; color:#7a98b2; }
  .tut3-foot { text-align:center; margin-top:6px; padding-top:12px; border-top:1px solid rgba(120,190,225,.35); }
  .tut3-foot .tut3-reward { display:flex; align-items:center; justify-content:center; gap:9px; font-size:.86rem; font-weight:800; color:#1769b0; }
  .tut3-foot .tut3-reward img { width:30px; height:30px; object-fit:contain; }
  .tut3-prog { display:flex; gap:5px; justify-content:center; margin-top:8px; }
  .tut3-pip { width:34px; height:6px; border-radius:3px; background:#d6e6f1; }
  .tut3-pip.on { background:linear-gradient(90deg,#2ea8ea,#22d8c8); }
  .tut3-close { display:block; margin:8px auto 0; background:none; border:none; color:#6a92b5; font-size:.84rem; font-weight:700; cursor:pointer; font-family:inherit; }
  .tut3-close:hover { color:#1769b0; }

  /* Coachmark spotlight */
  /* Container is passthrough by default; only the popup and catch-layer are
     interactive. This lets real clicks reach the game during interactive steps. */
  #tut3-coach { position:fixed; inset:0; z-index:100050; display:none; pointer-events:none; }
  #tut3-coach.open { display:block; }
  #tut3-catch { position:fixed; inset:0; background:transparent; pointer-events:none; }
  #tut3-hole { position:fixed; top:0; left:0; width:0; height:0; border-radius:14px; border:2.5px solid #5fd0e8; box-shadow:0 0 0 9999px rgba(5,24,44,.72), 0 0 22px rgba(95,208,232,.6); transition:top .28s ease, left .28s ease, width .28s ease, height .28s ease; pointer-events:none; }
  /* No target → keep the full-screen dim (box-shadow) but hide the ring. */
  #tut3-hole.nohole { width:0!important; height:0!important; left:50%!important; top:-12px!important; border-color:transparent!important; box-shadow:0 0 0 9999px rgba(5,24,44,.74)!important; }
  /* Secondary highlight: glowing rings drawn over EXTRA elements a step wants to
     call out (e.g. the board drop-zone while the spotlight sits on the matching
     hand card). Painted above the dim but below the popup, so two things light up
     at once without ever covering the instructions. */
  #tut3-glows { position:fixed; inset:0; pointer-events:none; }
  .tut3-glow-ring { position:fixed; border-radius:14px; border:3px solid #ffd574; box-shadow:0 0 22px rgba(255,213,116,.9), inset 0 0 0 3px rgba(255,213,116,.3); animation:tut3-glow-pulse 1.15s ease-in-out infinite; pointer-events:none; }
  @keyframes tut3-glow-pulse { 0%,100%{ opacity:.62; } 50%{ opacity:1; } }
  /* Teaching card-row inside the popup: card images + glowing symbol badges, used
     by the Star-ability lesson to show matching symbols side by side. */
  .t2-row { display:flex; align-items:center; justify-content:center; gap:8px; margin:4px 0 12px; flex-wrap:wrap; }
  .t2-card { display:flex; flex-direction:column; align-items:center; gap:5px; margin:0; }
  .t2-card img { width:104px; max-width:32vw; height:auto; border-radius:9px; box-shadow:0 5px 16px rgba(8,40,80,.45); border:1px solid rgba(120,200,235,.55); background:#fff; display:block; }
  .t2-row-single .t2-card img { width:158px; max-width:52vw; }
  .t2-card figcaption { font-size:.72rem; font-weight:800; color:#1769b0; text-align:center; display:flex; align-items:center; gap:5px; line-height:1.1; }
  .t2-sym { display:inline-flex; align-items:center; justify-content:center; min-width:21px; height:21px; padding:0 3px; border-radius:50%; font-size:.92rem; font-weight:900; background:#fffae8; border:2px solid #ffcf4d; box-shadow:0 0 10px rgba(255,200,60,.85); animation:tut3-glow-pulse 1.15s ease-in-out infinite; }
  .t2-sym-heart, .t2-sym-diamond { color:#e23b5a; }
  .t2-sym-triangle, .t2-sym-square, .t2-sym-circle { color:#1f7ad0; }
  .t2-eq { font-size:1.5rem; font-weight:900; color:#2f8bd0; }
  #tut3-pop { position:fixed; max-width:440px; width:calc(100vw - 28px); background:linear-gradient(160deg,#fbfeff,#e9f5ff); border:1.5px solid rgba(120,200,235,.7); border-radius:20px; box-shadow:0 22px 58px rgba(8,40,80,.55); padding:20px 24px 18px; font-family:"Nunito",sans-serif; color:#0f3a5e; transition:top .28s ease, left .28s ease; box-sizing:border-box; pointer-events:auto; }
  #tut3-pop .t3-badge { font-size:.76rem; font-weight:800; text-transform:uppercase; letter-spacing:1px; color:#2f8bd0; }
  #tut3-pop h3 { font-family:"Cinzel",serif; font-size:1.42rem; font-weight:900; color:#1769b0; margin:4px 0 10px; line-height:1.2; }
  #tut3-pop .t3-text { font-size:1.04rem; line-height:1.62; color:#2b5575; }
  #tut3-pop .t3-text strong { color:#0d456f; font-weight:800; }
  #tut3-pop .t3-cta { display:inline-block; margin-top:13px; background:linear-gradient(135deg,#2ea8ea,#1a77c9); color:#fff; border:none; border-radius:12px; padding:10px 18px; font-weight:800; font-size:.98rem; cursor:pointer; font-family:inherit; }
  #tut3-pop .t3-cta:hover { filter:brightness(1.07); }
  #tut3-pop .t3-bar { display:flex; align-items:center; gap:9px; margin-top:18px; }
  #tut3-pop .t3-count { font-size:.84rem; color:#6a92b5; font-weight:700; flex:1; }
  #tut3-pop .t3-btn { border:none; border-radius:12px; padding:11px 18px; font-weight:800; font-size:.94rem; cursor:pointer; font-family:inherit; }
  #tut3-pop .t3-next { background:linear-gradient(135deg,#2ea8ea,#1a77c9); color:#fff; }
  #tut3-pop .t3-next:hover { filter:brightness(1.06); }
  #tut3-pop .t3-back { background:#e3eef7; color:#3a6e9c; }
  #tut3-pop .t3-back:disabled { opacity:.4; cursor:default; }
  #tut3-pop .t3-skip { position:absolute; top:10px; right:12px; background:none; border:none; color:#8aa8c2; font-size:.72rem; font-weight:700; cursor:pointer; }
  #tut3-pop .t3-skip:hover { color:#e8556c; }
  #tut3-arrow { position:fixed; width:0; height:0; border:10px solid transparent; transition:top .28s ease, left .28s ease; pointer-events:none; }
  /* Completion toast */
  #tut3-toast { position:fixed; left:50%; bottom:30px; transform:translateX(-50%) translateY(20px); z-index:100060; background:linear-gradient(135deg,#1fbb8a,#159e9e); color:#fff; padding:13px 22px; border-radius:14px; font-family:"Nunito",sans-serif; font-weight:800; box-shadow:0 12px 36px rgba(0,0,0,.35); opacity:0; pointer-events:none; transition:opacity .3s, transform .3s; display:flex; align-items:center; gap:10px; }
  #tut3-toast.show { opacity:1; transform:translateX(-50%) translateY(0); }
  #tut3-hole.pulse { animation:tut3pulse 1.25s ease-in-out infinite; }
  @keyframes tut3pulse { 0%,100%{ box-shadow:0 0 0 9999px rgba(5,24,44,.72), 0 0 16px rgba(95,208,232,.5); } 50%{ box-shadow:0 0 0 9999px rgba(5,24,44,.72), 0 0 30px 6px rgba(95,208,232,.95); } }
  /* Terminal-step action buttons (e.g. Keep Playing / Return to Tutorials). */
  #tut3-pop .t3-choices { display:flex; flex-direction:column; gap:9px; margin-top:14px; }
  #tut3-pop .t3-choice { width:100%; border:none; border-radius:12px; padding:12px 16px; font-weight:800; font-size:1rem; cursor:pointer; font-family:inherit; background:#e3eef7; color:#1769b0; transition:filter .12s, transform .12s; }
  #tut3-pop .t3-choice:hover { filter:brightness(1.05); transform:translateY(-1px); }
  #tut3-pop .t3-choice.primary { background:linear-gradient(135deg,#2ea8ea,#1a77c9); color:#fff; }

  `;
  const styleEl = document.createElement("style");
  styleEl.id = "tut3-styles";
  styleEl.textContent = css;
  document.head.appendChild(styleEl);

  // ════════════════════════════════════════════════════════════════
  //  REUSABLE COACHMARK ENGINE
  //  steps: [{ target:selector|null, badge, title, text, before?() }]
  // ════════════════════════════════════════════════════════════════
  let coach = null, coachSteps = [], coachIdx = 0, coachDone = null, coachCleanup = null, coachWait = null, coachPoll = null;

  function coachAdvance() {
    if (coachIdx >= coachSteps.length - 1) { const cb = coachDone; endCoach(); cb && cb(); }
    else gotoStep(coachIdx + 1);
  }

  // A step.target may be a CSS selector string OR a function that returns an
  // element (used for dynamic targets like "another player's seat").
  function coachResolveEl(step) {
    if (!step || step.target == null) return null;
    if (typeof step.target === "function") { try { return step.target(); } catch (_) { return null; } }
    try { return document.querySelector(step.target); } catch (_) { return null; }
  }

  // Resolve one glow descriptor (selector string OR function → element).
  function coachResolveOne(g) {
    if (g == null) return null;
    if (typeof g === "function") { try { return g(); } catch (_) { return null; } }
    try { return document.querySelector(g); } catch (_) { return null; }
  }
  // Secondary highlights (step.glow): draw pulsing rings over extra elements
  // alongside the main spotlight. Rings live inside #tut3-glows (above the dim,
  // below the popup) and are re-positioned every tick because the hand/board
  // re-render on each poll and shift around.
  function clearCoachGlows() {
    const box = coach && coach.querySelector("#tut3-glows");
    if (box) box.innerHTML = "";
  }
  function applyCoachGlows() {
    const box = coach && coach.querySelector("#tut3-glows");
    if (!box) return;
    const step = coachSteps[coachIdx];
    const want = (step && step.glow) ? (Array.isArray(step.glow) ? step.glow : [step.glow]) : [];
    const els = want.map(coachResolveOne).filter(el => el && isVisible(el));
    // Reuse ring nodes to avoid flicker; create/remove to match the target count.
    while (box.children.length > els.length) box.removeChild(box.lastChild);
    while (box.children.length < els.length) {
      const ring = document.createElement("div"); ring.className = "tut3-glow-ring"; box.appendChild(ring);
    }
    els.forEach((el, i) => {
      const r = el.getBoundingClientRect();
      const pad = 6;
      const ring = box.children[i];
      ring.style.left   = Math.max(2, r.left - pad) + "px";
      ring.style.top    = Math.max(2, r.top - pad) + "px";
      ring.style.width  = Math.max(0, r.width + pad * 2) + "px";
      ring.style.height = Math.max(0, r.height + pad * 2) + "px";
    });
  }

  // Called by an interactive mock element when the user performs an action.
  // If the current step is waiting for exactly that action, advance the tour.
  function coachHit(action) {
    if (!coach || !coach.classList.contains("open")) return;
    if (coachWait && action === coachWait) {
      coachWait = null;
      if (coachIdx >= coachSteps.length - 1) { const cb = coachDone; endCoach(); cb && cb(); }
      else setTimeout(() => gotoStep(coachIdx + 1), 350);
    }
  }

  function buildCoach() {
    if (coach) return coach;
    coach = document.createElement("div");
    coach.id = "tut3-coach";
    coach.innerHTML =
      `<div id="tut3-catch"></div>
       <div id="tut3-hole"></div>
       <div id="tut3-arrow"></div>
       <div id="tut3-glows"></div>
       <div id="tut3-pop">
         <button class="t3-skip" id="tut3-skip">Skip ✕</button>
         <div class="t3-badge" id="tut3-badge"></div>
         <h3 id="tut3-title"></h3>
         <div class="t3-text" id="tut3-text"></div>
         <div class="t3-bar">
           <span class="t3-count" id="tut3-count"></span>
           <button class="t3-btn t3-back" id="tut3-back">← Back</button>
           <button class="t3-btn t3-next" id="tut3-next">Next →</button>
         </div>
       </div>`;
    document.body.appendChild(coach);
    coach.querySelector("#tut3-skip").addEventListener("click", endCoach);
    coach.querySelector("#tut3-back").addEventListener("click", () => gotoStep(coachIdx - 1, true));
    coach.querySelector("#tut3-next").addEventListener("click", () => {
      if (coachIdx >= coachSteps.length - 1) { const cb = coachDone; endCoach(); cb && cb(); }
      else gotoStep(coachIdx + 1);
    });
    window.addEventListener("resize", positionCoach);
    window.addEventListener("scroll", positionCoach, true);
    return coach;
  }

  function runCoach(steps, onDone, onCleanup) {
    buildCoach();
    coachSteps = steps; coachIdx = 0; coachDone = onDone || null; coachCleanup = onCleanup || null;
    coach.classList.add("open");
    gotoStep(0);
  }

  function endCoach() {
    if (coach) {
      coach.classList.remove("open");
      const ce = coach.querySelector("#tut3-catch"); if (ce) ce.style.pointerEvents = "none";
      const h = coach.querySelector("#tut3-hole"); if (h) h.classList.remove("pulse");
    }
    if (coachPoll) { clearInterval(coachPoll); coachPoll = null; }
    clearCoachGlows();
    const cu = coachCleanup;
    coachSteps = []; coachDone = null; coachCleanup = null; coachWait = null;
    try { cu && cu(); } catch (_) {}
  }

  // goingBack=true: arrived here via the ← Back button. In back-mode we skip the
  // advanceWhen poll (so an already-satisfied condition never auto-jumps the player
  // forward again) and unlock Next so the player can continue when ready.
  function gotoStep(i, goingBack) {
    if (i < 0 || i >= coachSteps.length) return;
    if (coachPoll) { clearInterval(coachPoll); coachPoll = null; }
    coachIdx = i;
    const step = coachSteps[i];
    clearCoachGlows();
    try { step.before && step.before(); } catch (_) {}
    // Auto-advance once a real UI transition completes (modal opens, room
    // created, game started, etc.). Re-positions while it waits so the spotlight
    // follows the live element.
    // Skip the poll when going back, already-satisfied conditions must not
    // immediately jump the player forward again.
    if (!goingBack && typeof step.advanceWhen === "function") {
      coachPoll = setInterval(() => {
        try { if (step.advanceWhen()) { clearInterval(coachPoll); coachPoll = null; coachAdvance(); } else { positionCoach(); } } catch (_) {}
      }, 350);
    }
    coach.querySelector("#tut3-badge").textContent = step.badge || "";
    coach.querySelector("#tut3-title").innerHTML = step.title || "";
    // text may be a function so a step can build dynamic HTML (e.g. live card
    // images) the moment it opens, after its before() has run.
    let bodyHtml = (typeof step.text === "function" ? (step.text() || "") : (step.text || ""));
    if (step.cta) bodyHtml += `<div><button class="t3-cta" id="tut3-cta">${esc(step.cta.label)}</button></div>`;
    const hasChoices = Array.isArray(step.choices) && step.choices.length > 0;
    if (hasChoices) {
      bodyHtml += `<div class="t3-choices">` + step.choices.map((c, k) =>
        `<button class="t3-choice${c.primary ? " primary" : ""}" data-choice="${k}">${esc(c.label)}</button>`).join("") + `</div>`;
    }
    const textEl = coach.querySelector("#tut3-text");
    textEl.innerHTML = bodyHtml;
    if (step.cta) {
      const ctaBtn = textEl.querySelector("#tut3-cta");
      if (ctaBtn) ctaBtn.addEventListener("click", () => { try { step.cta.onClick(); } catch (_) {} });
    }
    if (hasChoices) {
      textEl.querySelectorAll("[data-choice]").forEach(b => b.addEventListener("click", () => {
        const c = step.choices[Number(b.dataset.choice)];
        if (c && typeof c.onClick === "function") { try { c.onClick(); } catch (_) {} }
      }));
    }
    coach.querySelector("#tut3-count").textContent = `Step ${i + 1} of ${coachSteps.length}`;
    // Back: only disabled on step 0 (can't go further back).
    const backBtn = coach.querySelector("#tut3-back");
    backBtn.disabled = (i === 0);
    // Interactive step: let real clicks/drags reach the highlighted element and
    // disable Next so the player must perform the action.
    // When going back we never trap the player, clicks pass through and Next is
    // always available so they can resume forward without repeating the action.
    const isInteractive = !goingBack && step.interactive;
    coachWait = isInteractive ? (step.wait || null) : null;
    const catchEl = coach.querySelector("#tut3-catch");
    const hole = coach.querySelector("#tut3-hole");
    if (catchEl) catchEl.style.pointerEvents = isInteractive ? "none" : "auto";
    if (hole) hole.classList.toggle("pulse", !!isInteractive);
    const nextBtn = coach.querySelector("#tut3-next");
    if (hasChoices) {
      // Terminal step with its own action buttons (Keep Playing / Return to
      // Tutorials): hide Next; Back and Skip stay available.
      nextBtn.style.display = "none";
    } else {
      nextBtn.style.display = "";
      if (i >= coachSteps.length - 1) {
        nextBtn.textContent = "Finish ✓"; nextBtn.disabled = false;
      } else if (isInteractive && !step.allowNext) {
        nextBtn.textContent = "Click above →"; nextBtn.disabled = true;
      } else {
        // Interactive + allowNext, non-interactive, or back-navigated: always enabled.
        nextBtn.textContent = "Next →"; nextBtn.disabled = false;
      }
    }
    // Scroll target into view, then position after a tick so layout settles.
    const el = coachResolveEl(step);
    if (el && el.scrollIntoView) { try { el.scrollIntoView({ block: "center", behavior: "smooth" }); } catch (_) {} }
    setTimeout(positionCoach, 120);
  }

  function isVisible(el) {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) return false;
    const st = getComputedStyle(el);
    return st.display !== "none" && st.visibility !== "hidden";
  }

  function positionCoach() {
    if (!coach || !coach.classList.contains("open")) return;
    const step = coachSteps[coachIdx];
    if (!step) return;
    applyCoachGlows();
    const hole = coach.querySelector("#tut3-hole");
    const pop = coach.querySelector("#tut3-pop");
    const arrow = coach.querySelector("#tut3-arrow");
    const el = coachResolveEl(step);
    const vw = window.innerWidth, vh = window.innerHeight;
    const pr = pop.getBoundingClientRect();
    const popW = pr.width || 320, popH = pr.height || 160;

    if (!el || !isVisible(el)) {
      // No target → center the popup, hide the spotlight.
      hole.classList.add("nohole");
      arrow.style.display = "none";
      pop.style.left = Math.max(8, (vw - popW) / 2) + "px";
      pop.style.top = Math.max(8, (vh - popH) / 2) + "px";
      return;
    }
    hole.classList.remove("nohole");
    const r = el.getBoundingClientRect();

    // Per-step popup anchor: pin the popup to a screen edge so its text can
    // never sit over the drag corridor (hand → board) or the drop zone. Every
    // "place a card" step uses popAnchor:"top" so the words stay out of the way
    // of dragging or clicking to put a card down. The spotlight still surrounds
    // the target card; the drop zone is lit separately via the step's glow.
    if (step.popAnchor === "top" || step.popAnchor === "bottom") {
      const p2 = 8;
      hole.style.left = Math.max(2, r.left - p2) + "px";
      hole.style.top = Math.max(2, r.top - p2) + "px";
      hole.style.width = Math.min(vw - 4, r.width + p2 * 2) + "px";
      hole.style.height = Math.min(vh - 4, r.height + p2 * 2) + "px";
      arrow.style.display = "none";
      pop.style.left = Math.max(8, (vw - popW) / 2) + "px";
      pop.style.top = (step.popAnchor === "top")
        ? "12px"
        : Math.max(8, vh - popH - 12) + "px";
      return;
    }

    // Tall target (a content panel or an opened overlay such as the Avatar
    // Gallery): spotlight it but anchor the popup at the bottom so it never
    // covers the thing we are explaining.
    if (r.height > vh * 0.62) {
      const p2 = 8;
      hole.style.left = Math.max(2, r.left - p2) + "px";
      hole.style.top = Math.max(2, r.top - p2) + "px";
      hole.style.width = Math.min(vw - 4, r.width + p2 * 2) + "px";
      hole.style.height = Math.min(vh - 4, r.height + p2 * 2) + "px";
      arrow.style.display = "none";
      pop.style.left = Math.max(8, (vw - popW) / 2) + "px";
      pop.style.top = Math.max(8, vh - popH - 16) + "px";
      return;
    }
    const pad = 8;
    const hx = Math.max(2, r.left - pad), hy = Math.max(2, r.top - pad);
    const hw = Math.min(vw - 4, r.width + pad * 2), hh = Math.min(vh - 4, r.height + pad * 2);
    hole.style.left = hx + "px"; hole.style.top = hy + "px";
    hole.style.width = hw + "px"; hole.style.height = hh + "px";

    // Place popup below the target if room, else above, else beside.
    const gap = 18;
    let top, left, arrowDir;
    if (r.bottom + gap + popH < vh) { top = r.bottom + gap; arrowDir = "up"; }
    else if (r.top - gap - popH > 0) { top = r.top - gap - popH; arrowDir = "down"; }
    else { top = Math.max(8, Math.min(vh - popH - 8, r.top)); arrowDir = "none"; }
    left = r.left + r.width / 2 - popW / 2;
    left = Math.max(8, Math.min(vw - popW - 8, left));
    pop.style.left = left + "px";
    pop.style.top = top + "px";

    // Arrow
    if (arrowDir === "none") { arrow.style.display = "none"; }
    else {
      arrow.style.display = "block";
      const ax = Math.max(left + 14, Math.min(left + popW - 14, r.left + r.width / 2));
      if (arrowDir === "up") {
        arrow.style.borderColor = "transparent transparent #eaf5ff transparent";
        arrow.style.top = (top - 18) + "px"; arrow.style.left = (ax - 10) + "px";
      } else {
        arrow.style.borderColor = "#fbfeff transparent transparent transparent";
        arrow.style.top = (top + popH - 2) + "px"; arrow.style.left = (ax - 10) + "px";
      }
    }
  }

  // ════════════════════════════════════════════════════════════════
  //  MAIN MENU TOUR
  // ════════════════════════════════════════════════════════════════
  // Helpers used by the tour to drive the real menu.
  function navTab(name) { const b = document.getElementById("snav-" + name); if (b) try { b.click(); } catch (_) {} }
  function openGallery() {
    if (typeof window.__fishOpenAvatarGallery === "function") { try { window.__fishOpenAvatarGallery(); return; } catch (_) {} }
    const av = document.getElementById("stats-avatar"); if (av) try { av.click(); } catch (_) {}
  }
  function closeMenuOverlays() {
    const gal = document.getElementById("avatar-gallery");
    if (gal && gal.classList.contains("open")) {
      const back = gal.querySelector(".gal-back-btn");
      if (back) { try { back.click(); } catch (_) {} }
      gal.classList.remove("open");
    }
    const sm = document.getElementById("settings-modal");
    if (sm) sm.classList.remove("open");
    // Tutorial sample match (the real History game-detail modal).
    const gdm = document.getElementById("ph-game-detail-modal");
    if (gdm) gdm.classList.remove("open");
    // Trade picker (top-right Trade button) and the chat background sheet.
    const tp = document.getElementById("cc-trade-pick");
    if (tp) tp.style.display = "none";
    const bs = document.getElementById("ccm-bgsheet");
    if (bs) bs.style.display = "none";
  }
  function openSettings() { const b = document.getElementById("stats-settings-top-btn"); if (b) try { b.click(); } catch (_) {} }

  // advanceWhen helpers for interactive tab/modal steps
  const gtTabActive    = id => () => !!document.getElementById(id)?.classList.contains("active");
  const gtGalOpen      = ()  => !!document.getElementById("avatar-gallery")?.classList.contains("open");
  const gtGalClosed    = ()  => !document.getElementById("avatar-gallery")?.classList.contains("open");
  const gtSettingsOpen = ()  => !!document.getElementById("settings-modal")?.classList.contains("open");
  const gtStreakCalOpen = ()  => !!document.getElementById("streak-cal-modal")?.classList.contains("open");
  const gtWeeklyPill   = ()  => !!document.getElementById("ph-cs-pill")?.classList.contains("weekly");

  const MENU_STEPS = [

    // ── Welcome ──────────────────────────────────────────────────────
    { target: null, badge: "Main Menu Tour", title: "Welcome to your home base",
      before: closeMenuOverlays,
      text: "This is the <strong>Main Menu</strong>, where you start games, track your progress, and customise your critter. We will walk through every part of it together." },

    // ── Profile card (top bar / XP) ───────────────────────────────────
    { target: ".ph-profile-card", badge: "Your Profile", title: "Your Profile Card",
      before: () => { closeMenuOverlays(); navTab("overview"); },
      text: "Your <strong>avatar, name, level, and rank</strong> all live here. The XP bar fills as you play, and every game you finish earns XP toward your next level." },

    // ── Trade (top-right, same area as profile card) ─────────────────
    { target: "#stats-trade-btn", badge: "Trade", title: "Trading",
      text: "The <strong>Trade</strong> button sits at the top right. Pick any friend (or search a player) and you can swap <strong>avatars, backgrounds and Critter Coins</strong> with them, no need to open Messages first." },

    // ── Avatar click (interactive, must actually open gallery) ──────
    { target: "#stats-avatar", badge: "Avatar Gallery", title: "Open Your Avatar Gallery",
      before: closeMenuOverlays,
      interactive: true,
      advanceWhen: gtGalOpen,
      text: "Click your <strong>avatar</strong> to open the Avatar Gallery." },

    // ── Avatar Gallery, intro context ───────────────────────────────
    { target: "#avatar-gallery", badge: "Avatar Gallery", title: "Your Avatar Gallery",
      text: "This is where you choose which critter represents you. New critters are <strong>unlocked by earning achievements, climbing the competitive ranks, or redeeming a code</strong>. Click any locked animal to see its fun fact and exactly what you need to do to earn it." },

    // ── Click the Osprey tile (interactive) ──────────────────────────
    { target: "[data-avatar-id='osprey']", badge: "Avatar Gallery", title: "Click an Animal",
      before: () => { try { document.querySelector("[data-avatar-id='osprey']")?.scrollIntoView({ block: "center", behavior: "instant" }); } catch(_) {} },
      interactive: true,
      advanceWhen: () => !!document.querySelector("[data-avatar-id='osprey'].gal-selected"),
      text: "Click the <strong>Osprey</strong> to see its fun fact and unlock requirement." },

    // ── Osprey detail panel ───────────────────────────────────────────
    { target: "#gal-detail", badge: "Avatar Gallery", title: "Fun Fact and Unlock",
      text: "Every animal shows its <strong>fun fact</strong> and exactly what you need to do to <strong>unlock</strong> it. The Osprey is your reward for completing all five tutorials. You are already on your way!" },

    // ── Back button, must click to exit gallery ──────────────────────
    { target: "#gal-back-btn", badge: "Avatar Gallery", title: "Back to the Menu",
      interactive: true,
      advanceWhen: gtGalClosed,
      text: "Click the <strong>back arrow</strong> at the top to close the gallery and return to the menu." },

    // ── Daily Challenges ──────────────────────────────────────────────
    { target: "#ph-cs-strip", badge: "Daily Challenges", title: "Daily Challenges",
      before: () => { closeMenuOverlays(); navTab("overview"); },
      text: "These are your <strong>Daily Challenges</strong>. Each day you get three new challenges to complete for <strong>XP</strong>. They reset <strong>24 hours after you complete them</strong>, so come back every day for fresh ones." },

    // ── Toggle to Weekly (interactive, must click the calendar icon) ──
    { target: "#ph-cs-toggle-btn", badge: "Weekly Challenges", title: "Switch to Weekly",
      interactive: true,
      advanceWhen: gtWeeklyPill,
      text: "Click the <strong>calendar icon</strong> to switch to your Weekly Challenges." },

    // ── Weekly Challenges description ─────────────────────────────────
    { target: "#ph-cs-strip", badge: "Weekly Challenges", title: "Weekly Challenges",
      text: "These are your <strong>Weekly Challenges</strong>. Completing them earns bigger XP rewards than daily challenges. They reset every <strong>Monday</strong>, so you have the whole week to finish them." },

    // ── Overview panel ────────────────────────────────────────────────
    { target: "#ph-panel-overview", badge: "Overview Tab", title: "Overview",
      before: () => { closeMenuOverlays(); navTab("overview"); },
      text: "This is the <strong>Overview</strong> tab. It shows your combined stats from every game you have ever played across all modes." },

    // ── Casual tab (click to navigate) ───────────────────────────────
    { target: "#snav-casual", badge: "Casual Tab", title: "Casual Stats",
      before: closeMenuOverlays,
      interactive: true,
      advanceWhen: gtTabActive("snav-casual"),
      text: "Click <strong>Casual</strong> to see your record." },

    // ── Casual panel description ──────────────────────────────────────
    { target: "#ph-panel-normal", badge: "Casual Tab", title: "Your Casual Record",
      text: "The Casual tab breaks down your record by player count, from <strong>2 all the way to 8 players</strong>. Each group shows your <strong>win percentage</strong>, your <strong>most played strategy</strong>, your <strong>average score</strong>, and your total <strong>games and wins</strong>." },

    // ── Competitive tab (click to navigate) ──────────────────────────
    { target: "#snav-competitive", badge: "Competitive Tab", title: "Competitive",
      before: closeMenuOverlays,
      interactive: true,
      advanceWhen: gtTabActive("snav-competitive"),
      text: "Click <strong>Competitive</strong> to see it." },

    // ── Competitive panel description ─────────────────────────────────
    { target: "#ph-panel-competitive", badge: "Competitive Tab", title: "Competitive",
      text: "When you play a Competitive game, there will be a rank bar here. <strong>Competitive games give you a rank.</strong> The more you win, the higher you climb toward <strong>King of the Critters</strong>. It is also unique because you <strong>control two hands at once</strong>, which doubles your options and strategy." },

    // ── History tab (click to navigate) ──────────────────────────────
    { target: "#snav-history", badge: "History Tab", title: "Match History",
      before: closeMenuOverlays,
      interactive: true,
      advanceWhen: gtTabActive("snav-history"),
      text: "Click <strong>History</strong> to see your past matches." },

    // ── History panel + sample match CTA ──────────────────────────────
    // Opens the REAL game-detail modal with one of TheFishManTim's matches and
    // makes the learner tap each opponent's board before the tour continues.
    { target: () => (document.getElementById("ph-game-detail-modal")?.classList.contains("open")
        ? document.getElementById("ph-gdm-box") : document.getElementById("ph-panel-history")),
      badge: "History Tab", title: "Match History",
      interactive: true, popAnchor: "top",
      advanceWhen: tutSampleAllOppsViewed,
      cta: { label: "View a sample match", onClick: () => showSampleMatch() },
      text: "The <strong>History</strong> tab lets you reopen any past match and see exactly what every player had on their board, along with the final scores. Select the button below to open a real example, a game played by <strong>TheFishManTim</strong>. Then <strong>tap each of the other players</strong> (CoralCara, ReefRiley, KelpQuinn) to inspect their boards." },

    // ── Friends tab (click to navigate) ──────────────────────────────
    { target: "#snav-friends", badge: "Friends Tab", title: "Friends",
      before: closeMenuOverlays,
      interactive: true,
      advanceWhen: gtTabActive("snav-friends"),
      text: "Click <strong>Friends</strong> to go there." },

    // ── Friends panel description ─────────────────────────────────────
    { target: "#ph-panel-friends", badge: "Friends Tab", title: "Friends",
      text: "The <strong>Friends</strong> tab shows everyone you have added, with their rank and status. You can challenge them to a private game directly from here." },

    // ── Friend code ───────────────────────────────────────────────────
    { target: "#ph-fc-display", badge: "Friends Tab", title: "Your Friend Code",
      text: "This is <strong>your friend code</strong>. Share it so others can add you. To add someone else, type their <strong>code or their name and code</strong> into the box just below and select Add." },

    // ── Messages tab (click to navigate) ─────────────────────────────
    { target: "#snav-messages", badge: "Messages Tab", title: "Messages",
      before: closeMenuOverlays,
      interactive: true,
      advanceWhen: gtTabActive("snav-messages"),
      text: "Click <strong>Messages</strong> to go there." },

    // ── Messages panel description ────────────────────────────────────
    { target: "#ph-panel-messages", badge: "Messages Tab", title: "Messages",
      text: "<strong>Messages</strong> is your full-page chat. Search a player to start a conversation or a group, and tap a chat to open it across the whole page (the <strong>back arrow</strong> takes you to your other chats). The <strong>🎨</strong> button gives that chat an ocean background, and only one of you needs to own it." },

    // ── Achievements tab (click to navigate) ─────────────────────────
    { target: "#snav-achievements", badge: "Achievements Tab", title: "Achievements",
      before: closeMenuOverlays,
      interactive: true,
      advanceWhen: gtTabActive("snav-achievements"),
      text: "Click <strong>Achievements</strong> to see your badges." },

    // ── Achievements panel description ────────────────────────────────
    { target: "#ph-panel-achievements", badge: "Achievements Tab", title: "Achievements",
      text: "The <strong>Achievements</strong> tab tracks every badge you earn for reaching milestones. Many achievements also <strong>unlock new critter avatars</strong>, and a few hidden ones are waiting to be discovered." },

    // ── Leaderboard tab (click to navigate) ──────────────────────────
    { target: "#snav-leaderboard", badge: "Leaderboard Tab", title: "Leaderboard",
      before: closeMenuOverlays,
      interactive: true,
      advanceWhen: gtTabActive("snav-leaderboard"),
      text: "Click <strong>Leaderboard</strong> to see the global rankings." },

    // ── Leaderboard panel description ─────────────────────────────────
    { target: "#ph-panel-leaderboard", badge: "Leaderboard Tab", title: "Reading the Leaderboard",
      text: "Each row shows a player's <strong>name, level, and score</strong>. The small <strong>button on the right of each row</strong> sends that player a friend request so you can add people straight from the board." },

    // ── Store tab (click to navigate) ────────────────────────────────
    { target: "#snav-store", badge: "Store Tab", title: "Store",
      before: closeMenuOverlays,
      interactive: true,
      advanceWhen: gtTabActive("snav-store"),
      text: "Click <strong>Store</strong> to go there." },

    // ── Store panel description ───────────────────────────────────────
    { target: "#ph-panel-store", badge: "Store Tab", title: "The Store",
      text: "The <strong>Store</strong> is where you buy <strong>backgrounds</strong> that frame your avatar, preview <strong>seasonal</strong> cosmetics, and redeem a donation code if you have one." },

    // ── Settings (click to open; the gear now lives in the top right) ─
    { target: "#stats-settings-top-btn", badge: "Settings", title: "Settings",
      before: closeMenuOverlays,
      interactive: true,
      advanceWhen: gtSettingsOpen,
      text: "Click the <strong>gear</strong> in the top right to open <strong>Settings</strong>. Here you can change your music volume, read the Terms, send us <strong>Help &amp; Feedback</strong>, and sign out." },

    // ── Daily Streak (after settings closes) ─────────────────────────
    { target: ".ph-sidebar-streak", badge: "Daily Streak", title: "Daily Streak",
      before: closeMenuOverlays,
      text: "The <strong>flame icon</strong> here shows your current streak. Every day you log in and play, the streak grows. The longer your streak, the <strong>more bonus XP</strong> you earn each day, so playing regularly pays off." },

    // ── View Streak Details (interactive, must click) ────────────────
    { target: "#ph-ss-details-btn", badge: "Daily Streak", title: "View Streak Details",
      interactive: true,
      advanceWhen: gtStreakCalOpen,
      text: "Click <strong>View streak details</strong> to open your streak calendar." },

    // ── Streak calendar, spotlight the entire calendar ───────────────
    { target: "#streak-cal-box", badge: "Daily Streak", title: "Your Streak Calendar",
      text: "This calendar shows <strong>every day you have logged in</strong>. The stats at the top show your current streak, your longest streak ever, and exactly how much <strong>bonus XP</strong> each consecutive day is worth. Keep your streak alive for bigger and bigger rewards." },

    // ── Click X to close the calendar (interactive) ───────────────────
    { target: "#streak-cal-close", badge: "Daily Streak", title: "Close the Calendar",
      interactive: true,
      advanceWhen: () => !document.getElementById("streak-cal-modal")?.classList.contains("open"),
      text: "Click the <strong>✕</strong> to close the calendar." },

    // ── All Done ──────────────────────────────────────────────────────
    { target: "#stats-tutorial-btn", badge: "All Done", title: "That is the whole menu",
      before: () => { closeMenuOverlays(); navTab("overview"); },
      text: "You have now seen every part of the Main Menu. You can reopen this <strong>Tutorial</strong> button at any time. Finish all five tutorials to unlock the <strong>Osprey</strong> avatar. Select Finish to mark this tour complete." },
  ];

  function runMenuTour() {
    closeChooser();
    const lobby = document.getElementById("auth-stats-lobby");
    if (!lobby || !lobby.classList.contains("visible")) {
      runCoach([{ target: null, badge: "Main Menu Tour", title: "Open from the Main Menu",
        text: "This tour spotlights the <strong>main menu</strong>. Please open it from the menu screen, then select <strong>Tutorial, then Main Menu Tour</strong>, so every part lines up correctly." }], null);
      return;
    }
    runCoach(MENU_STEPS, () => {
      closeMenuOverlays();
      navTab("overview");
      setDone("menu");
      showToast("Main Menu tour complete", completionSubtitle());
      setTimeout(openChooser, 900);
    }, () => { closeMenuOverlays(); });
  }

  // ── Tutorial-only sample match viewer ─────────────────────────────
  // Opens the REAL History game-detail modal with a real past game so the
  // learner sees exactly what an old match looks like, not a mock-up. The
  // game is one of TheFishManTim's, with every player's real final board.
  // The History step requires the learner to inspect each opponent's board
  // before advancing, so we record which player chips they open here.
  let _tutSampleOpponents = [];
  let _tutSampleViewed = new Set();
  function tutSampleAllOppsViewed() {
    return _tutSampleOpponents.length > 0 && _tutSampleOpponents.every(n => _tutSampleViewed.has(n));
  }
  function showSampleMatch() {
    // Final scores for an example 4-player game (TheFishManTim wins).
    const sAll = [
      { n: "TheFishManTim", s: 78 },
      { n: "CoralCara", s: 64 },
      { n: "ReefRiley", s: 58 },
      { n: "KelpQuinn", s: 41 },
    ];
    // Real end-game boards (ocean + each attachment slot) from actual games.
    const sBds = [
      { n: "TheFishManTim", bd: [
          {ou:207,on:"Pier",u:[{u:15,n:"Horned Puffin"}],d:[{u:22,n:"Orange Tube Sponge"}],l:[{u:175,n:"Yellowfin Tuna"}],r:[{u:174,n:"Cuttlefish"}]},
          {ou:224,on:"Coral Reef",u:[{u:41,n:"Osprey"}],d:[{u:26,n:"Red Beaded Anemone"}],l:[],r:[{u:188,n:"Clownfish"}]},
          {ou:245,on:"Arctic Ocean",u:[{u:11,n:"Horned Puffin"}],d:[],l:[],r:[]},
          {ou:253,on:"Kelp Forest",u:[{u:19,n:"California Gull"}],d:[{u:92,n:"Sea Urchin"}],l:[],r:[{u:156,n:"Big Eye Tuna"}]},
          {ou:209,on:"Deep Ocean",u:[{u:95,n:"Razorbill Auk"}],d:[],l:[],r:[]},
          {ou:254,on:"Kelp Forest",u:[{u:31,n:"Peruvian Pelican"}],d:[],l:[],r:[]},
          {ou:234,on:"Mangrove",u:[],d:[],l:[],r:[]}
        ] },
      { n: "CoralCara", bd: [
          {ou:209,on:"Deep Ocean",u:[{u:31,n:"Peruvian Pelican"}],d:[],l:[{u:115,n:"Reef Trigger Fish"}],r:[{u:166,n:"Whale Shark"}]},
          {ou:247,on:"Arctic Ocean",u:[{u:47,n:"Osprey"}],d:[{u:26,n:"Red Beaded Anemone"}],l:[{u:139,n:"Cuttlefish"}],r:[{u:110,n:"Common Octopus"}]},
          {ou:246,on:"Arctic Ocean",u:[],d:[],l:[],r:[{u:124,n:"Narwhal"}]},
          {ou:212,on:"Deep Ocean",u:[],d:[{u:78,n:"Loggerhead Sea Turtle"}],l:[],r:[]},
          {ou:211,on:"Deep Ocean",u:[{u:9,n:"Horned Puffin"}],d:[],l:[],r:[]},
          {ou:255,on:"Kelp Forest",u:[{u:35,n:"Great Albatross"}],d:[],l:[{u:171,n:"Bobtail Squid"}],r:[{u:150,n:"Giant Squid"}]}
        ] },
      { n: "ReefRiley", bd: [
          {ou:230,on:"Mangrove",u:[{u:33,n:"Great Albatross"}],d:[{u:30,n:"Cleaner Wrasse"}],l:[{u:151,n:"Tarpon"}],r:[{u:162,n:"King Salmon"}]},
          {ou:239,on:"Artificial Reef",u:[{u:37,n:"Great Albatross"}],d:[{u:22,n:"Orange Tube Sponge"}],l:[{u:133,n:"Goliath Grouper"}],r:[]},
          {ou:212,on:"Deep Ocean",u:[{u:11,n:"Horned Puffin"}],d:[],l:[{u:139,n:"Cuttlefish"}],r:[{u:122,n:"Manta Ray"}]},
          {ou:248,on:"Arctic Ocean",u:[{u:43,n:"Osprey"}],d:[{u:16,n:"Mantis Shrimp"}],l:[{u:103,n:"Spinner Dolphin"}],r:[]},
          {ou:245,on:"Arctic Ocean",u:[{u:13,n:"Horned Puffin"}],d:[],l:[{u:187,n:"Yellowfin Tuna"}],r:[{u:112,n:"Roosterfish"}]},
          {ou:211,on:"Deep Ocean",u:[],d:[],l:[],r:[]}
        ] },
      { n: "KelpQuinn", bd: [
          {ou:250,on:"Arctic Ocean",u:[{u:59,n:"Mullet"}],d:[{u:56,n:"Lobster"}],l:[{u:177,n:"Cuttlefish"}],r:[{u:128,n:"Sailfish"}]},
          {ou:253,on:"Kelp Forest",u:[{u:57,n:"Mullet"}],d:[],l:[{u:111,n:"Manta Ray"}],r:[{u:162,n:"King Salmon"}]},
          {ou:258,on:"Kelp Forest",u:[{u:89,n:"Bonito"}],d:[],l:[{u:167,n:"Goliath Grouper"}],r:[]},
          {ou:254,on:"Kelp Forest",u:[{u:77,n:"Flying Fish"}],d:[{u:32,n:"King Crab"}],l:[{u:135,n:"Mahi Mahi"}],r:[{u:156,n:"Big Eye Tuna"}]},
          {ou:249,on:"Arctic Ocean",u:[{u:65,n:"Bunker"}],d:[],l:[{u:145,n:"Mahi Mahi"}],r:[]},
          {ou:215,on:"Deep Ocean",u:[],d:[],l:[{u:101,n:"Tarpon"}],r:[]}
        ] },
    ];
    const g = {
      t: Date.UTC(2026, 4, 11),   // a believable past date
      mode: "normal",
      pc: 4,
      win: "TheFishManTim",
      all: sAll,
      bds: sBds,
    };
    _tutSampleOpponents = sAll.map(p => p.n).filter(n => n !== "TheFishManTim");
    _tutSampleViewed = new Set();
    if (typeof window.__fishOpenGameDetail !== "function") return;
    window.__fishOpenGameDetail(g);
    // Record which players the learner inspects. The chips' own handler still
    // switches the board view; this extra listener just tracks progress so the
    // History step can require every opponent to be opened before advancing.
    setTimeout(() => {
      document.querySelectorAll("#ph-game-detail-modal .ph-gdm-pchip").forEach(chip => {
        chip.addEventListener("click", () => {
          if (chip.dataset.pname) _tutSampleViewed.add(chip.dataset.pname);
        });
      });
    }, 0);
  }

  // ════════════════════════════════════════════════════════════════
  //  THE GAME TOUR (coaches the REAL create-game flow + real game)
  // ════════════════════════════════════════════════════════════════
  const gtVisible = (sel) => { const e = document.querySelector(sel); return !!(e && e.offsetParent !== null && e.getBoundingClientRect().width > 0); };
  const gtModalOpen = () => { const m = document.getElementById("new-current-modal"); return !!(m && m.classList.contains("open")); };
  const gtWaitingOpen = () => { const m = document.getElementById("pv-waiting-room"); return !!(m && m.classList.contains("open")); };
  const gtGameOpen = () => {
    const g = document.getElementById("pv-game");
    if (!g || getComputedStyle(g).display === "none") return false;
    // Wait until the waiting room is fully dismissed, joinRoom shows pv-game
    // immediately, but hideWaitingRoom() only fires after the first SSE update.
    const wr = document.getElementById("pv-waiting-room");
    if (wr && wr.classList.contains("open")) return false;
    return true;
  };
  // Set a number field and fire the events any listeners expect.
  function gtSetInput(id, val) {
    const el = document.getElementById(id);
    if (!el) return;
    el.value = String(val);
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }
  // Click every "Easy" difficulty pill that is not already active. Retries a few
  // times because the bot seats render asynchronously after the room is created.
  function gtAllBotsEasy() {
    let tries = 0;
    const t = setInterval(() => {
      tries++;
      const pills = document.querySelectorAll(".wr-diff-easy:not(.active)");
      pills.forEach(p => { try { p.click(); } catch (_) {} });
      if (tries > 8) clearInterval(t);
    }, 400);
  }

  // ── Live-game state predicates (read the real payload via the cc bridges) ──
  function gtPay() { try { return (window.__ccGetPayload && window.__ccGetPayload()) || null; } catch (_) { return null; } }
  function gtMe() {
    const p = gtPay(); if (!p) return null;
    const st = p.state || {};
    const players = Array.isArray(st.players) ? st.players : [];
    const vi = p.viewer && p.viewer.seat_index;
    return players.find(x => x.index === vi) || null;
  }
  function gtMyTurn() { const p = gtPay(); return !!(p && p.viewer && p.viewer.can_act); }
  function gtOceanCount() { const me = gtMe(); return (me && Array.isArray(me.board)) ? me.board.length : 0; }
  function gtCreatureCount() {
    const me = gtMe(); if (!me || !Array.isArray(me.board)) return 0;
    let n = 0;
    me.board.forEach(o => ["up", "down", "left", "right"].forEach(d => { if (Array.isArray(o[d])) n += o[d].length; }));
    return n;
  }
  function gtZoomOpen() { const m = document.getElementById("pv-zoom-modal"); return !!(m && m.classList.contains("open")); }
  function gtZoomClosed() { const m = document.getElementById("pv-zoom-modal"); return !(m && m.classList.contains("open")); }
  function gtBoardFocusOpen() { const m = document.getElementById("pv-board-focus"); return !!(m && m.classList.contains("open")); }
  function gtBoardFocusClosed() { const m = document.getElementById("pv-board-focus"); return !(m && m.classList.contains("open")); }
  function gtBoardPeek() { return gtBoardFocusOpen() || !!document.getElementById("pv-board-hover")?.classList.contains("visible"); }
  function gtEndgameOpen() { const el = document.getElementById("pv-endgame-overlay"); return !!(el && el.classList.contains("open")); }
  // Snapshot of the hand's left-to-right order, for detecting a manual reorder.
  function gtHandSig() { return Array.from(document.querySelectorAll("#pv-hand .pv-hand-card")).map(c => c.dataset.entryUid).join(","); }
  let _tutHandSig = "";
  // Draw-count baseline captured when the "Draw Your Cards" step opens. The step
  // advances once the player has drawn 2 cards (deck/pool, any mix). We can't use
  // gtDrawDone() here: drawing the 2nd card ends the turn, so can_act flips to
  // false and a turn-state check would never see the completed draw.
  let _gtDrawBase = 0;
  let _gtDrawSawTurn = false;
  let _gtHandBase = -1;  // DOM hand card count when the draw step opens
  function gtDrawCount() { try { return window.__ccDrawCount ? window.__ccDrawCount() : 0; } catch (_) { return 0; } }
  function gtHandCardCount() { try { return document.querySelectorAll("#pv-hand .pv-hand-card").length; } catch (_) { return 0; } }
  // "Opponents Are Going" step: only advance to the play steps once the turn has
  // actually left the player (bots took over) AND come back. Guards against the
  // brief window right after the 2nd draw where can_act may still read true
  // before the server processes the forced end-of-turn.
  let _gtSawOppTurn = false;
  // First seat belonging to another player (always an AI in the 1-human tutorial).
  function gtOtherSeat() {
    const me = gtMe();
    const myIdx = me ? me.index : 0;
    const seats = Array.from(document.querySelectorAll(".pv-seat[data-player-index]"));
    return seats.find(s => Number(s.dataset.playerIndex) !== myIdx) || seats[0] || null;
  }

  // ── Hand-card lookups (for spotlighting a SPECIFIC card the player must play) ──
  function gtFirstFace(entry) { return (entry && Array.isArray(entry.faces) && entry.faces[0]) ? entry.faces[0] : entry; }
  function gtEntryUid(entry) {
    const f = gtFirstFace(entry);
    return Number(entry?.entry_uid ?? f?.uid ?? entry?.uid ?? 0);
  }
  // DOM node for a hand entry by its entry_uid (so we can spotlight that card).
  function gtHandCardEl(entryUid) {
    if (!entryUid) return null;
    return document.querySelector(`#pv-hand .pv-hand-card[data-entry-uid="${entryUid}"]`) || null;
  }
  // The Mangrove ocean in the player's hand (rigged to always be present).
  function gtMangroveEntry() {
    const me = gtMe(); if (!me || !Array.isArray(me.hand)) return null;
    return me.hand.find(e => {
      const f = gtFirstFace(e);
      return String(f?.name || "").trim().toLowerCase() === "mangrove";
    }) || null;
  }
  function gtMangroveCardEl() { const e = gtMangroveEntry(); return e ? gtHandCardEl(gtEntryUid(e)) : null; }
  // Pick ONE creature for the player to deploy: prefer a payable (cost ≥ 1)
  // creature that has a real placement direction, so the "pay for it" step always
  // has something to pay. Cached per play so the claim + pay sub-steps agree.
  let _gtChosenCreatureUid = 0;
  function gtPickChosenCreature() {
    const me = gtMe(); if (!me || !Array.isArray(me.hand)) return 0;
    let fallback = 0;
    for (const e of me.hand) {
      const f = gtFirstFace(e);
      const sp = String(f?.species || "").toLowerCase();
      if (sp.includes("ocean")) continue;
      const dir = String(f?.direction || "").trim().toLowerCase();
      if (!dir || dir === "n/a") continue;
      const uid = gtEntryUid(e);
      if (Number(f?.cost || 0) >= 1) return uid;   // payable creature, ideal
      if (!fallback) fallback = uid;                // any placeable creature
    }
    return fallback;
  }
  function gtChosenCreatureEl() {
    if (!_gtChosenCreatureUid) _gtChosenCreatureUid = gtPickChosenCreature();  // late-bind if before() ran before state loaded
    return _gtChosenCreatureUid ? gtHandCardEl(_gtChosenCreatureUid) : null;
  }
  // Is the player mid-payment for an Ocean / a creature right now?
  function gtPendingPay() { try { return (window.__ccPendingPay && window.__ccPendingPay()) || null; } catch (_) { return null; } }
  function gtPayingOcean()   { const a = gtPendingPay(); return !!(a && a.kind === "play_ocean"); }
  function gtPayingCreature(){ const a = gtPendingPay(); return !!(a && a.kind === "play_to_ocean"); }
  // The live payment bar (spotlight target while the player picks discard cards).
  function gtPayBarEl() { const el = document.getElementById("pv-payment-mode-bar"); return (el && el.offsetParent !== null) ? el : null; }
  // The board's "play an Ocean here" drop zone (only present while it's legal);
  // falls back to the whole board so the destination always lights up.
  function gtOceanDropEl() { return document.getElementById("pv-ocean-drop-zone") || document.getElementById("pv-my-board"); }
  function gtPoolEl() { return document.getElementById("pv-pool-area"); }
  let _gtOceanBase = 0, _gtCreatureBase = 0;

  // ── Star-ability lesson plumbing (Tutorial Part 2) ───────────────────────
  // The server rigs an exact hand: Mangrove + Arctic Ocean share a symbol
  // (fires Mangrove's *Play again*), and Great Albatross + Kelp Forest share a
  // symbol (fires Albatross's *Draw one*). We locate those cards in the live
  // hand, cache their face uids + symbols BEFORE they're played/discarded (so we
  // can show them side by side afterward), force the ★ toggle on, and render the
  // matching symbols as glowing badges.
  const T2_GLYPH = { heart: "♥", diamond: "♦", triangle: "▲", square: "■", circle: "●" };
  const _t2 = { mangrove: null, arctic: null, albatross: null, kelp: null };
  function _t2FindByName(nameLc) {
    const me = gtMe(); if (!me || !Array.isArray(me.hand)) return null;
    for (const e of me.hand) {
      const faces = Array.isArray(e.faces) && e.faces.length ? e.faces : [e];
      for (const f of faces) {
        if (String(f && f.name || "").trim().toLowerCase() === nameLc) {
          return { entryUid: gtEntryUid(e), faceUid: Number(f.uid), sym: String(f.symbol || "").trim().toLowerCase() };
        }
      }
    }
    return null;
  }
  function t2ForceStarOn() {
    const t = document.getElementById("pv-star-toggle");
    if (t && !t.checked) { t.checked = true; try { t.dispatchEvent(new Event("change", { bubbles: true })); } catch (_) {} }
  }
  // Find ALL hand records (entry+face+symbol) matching a card name.
  function _t2FindAll(nameLc) {
    const me = gtMe(); const out = [];
    if (!me || !Array.isArray(me.hand)) return out;
    for (const e of me.hand) {
      const faces = Array.isArray(e.faces) && e.faces.length ? e.faces : [e];
      for (const f of faces) {
        if (String(f && f.name || "").trim().toLowerCase() === nameLc) {
          out.push({ entryUid: gtEntryUid(e), faceUid: Number(f.uid), sym: String(f.symbol || "").trim().toLowerCase() });
        }
      }
    }
    return out;
  }
  // Resolve the rigged teaching PAIR: prefer an A and B that share a symbol (the
  // coordinated pair), so a randomly-drawn duplicate never gets picked instead.
  function _t2FindPair(nameA, nameB) {
    const as = _t2FindAll(nameA), bs = _t2FindAll(nameB);
    for (const a of as) for (const b of bs) { if (a.sym && a.sym === b.sym) return [a, b]; }
    return (as.length && bs.length) ? [as[0], bs[0]] : null;
  }
  function t2CacheOceanPair() { const pr = _t2FindPair("mangrove", "arctic ocean"); if (pr) { _t2.mangrove = pr[0]; _t2.arctic = pr[1]; } }
  function t2CacheBirdPair()  { const pr = _t2FindPair("great albatross", "kelp forest"); if (pr) { _t2.albatross = pr[0]; _t2.kelp = pr[1]; } }
  // Spotlight targets: the specific teaching card in the hand (cache or live).
  function t2HandEl(slot, nameLc) {
    const e = _t2[slot] || _t2FindByName(nameLc);
    return e ? gtHandCardEl(e.entryUid) : null;
  }
  function gtMangroveHandEl()  { return t2HandEl("mangrove", "mangrove"); }
  function gtArcticHandEl()    { return t2HandEl("arctic", "arctic ocean"); }
  function gtAlbatrossHandEl() { return t2HandEl("albatross", "great albatross"); }
  function gtKelpHandEl()      { return t2HandEl("kelp", "kelp forest"); }
  // Golden (star-ability) hand cards excluding the 4 teaching cards, used to spotlight
  // valid payment options beyond the symbol-matched pair.
  function gtGoldenHandEls() {
    const me = gtMe();
    if (!me || !Array.isArray(me.hand)) return [];
    const skipUids = new Set([
      _t2.arctic    ? _t2.arctic.entryUid    : 0,
      _t2.mangrove  ? _t2.mangrove.entryUid  : 0,
      _t2.albatross ? _t2.albatross.entryUid : 0,
      _t2.kelp      ? _t2.kelp.entryUid      : 0,
    ]);
    const results = [];
    for (const e of me.hand) {
      const uid = gtEntryUid(e);
      if (skipUids.has(uid)) continue;
      const faces = Array.isArray(e.faces) && e.faces.length ? e.faces : [e];
      for (const f of faces) {
        if (starText(String(f && f.text || ""))) {
          const el = gtHandCardEl(uid);
          if (el) { results.push(el); break; }
        }
      }
    }
    return results;
  }
  // The player's own seat element.
  function gtMySeat() {
    const me = gtMe();
    const myIdx = me ? me.index : -1;
    return document.querySelector(`.pv-seat[data-player-index="${myIdx}"]`) || null;
  }
  // All opponent seat elements (every seat that isn't the viewer's).
  function gtOtherSeats() {
    const me = gtMe();
    const myIdx = me ? me.index : 0;
    return Array.from(document.querySelectorAll(".pv-seat[data-player-index]"))
      .filter(s => Number(s.dataset.playerIndex) !== myIdx);
  }
  function t2Glyph(sym) { return T2_GLYPH[String(sym || "").toLowerCase()] || "★"; }
  function t2Img(faceUid) { try { return (window.__ccCardImg && window.__ccCardImg(faceUid).url) || ""; } catch (_) { return ""; } }
  // Build a row of card images, each with a glowing symbol badge underneath.
  // `cards` = [{ rec:_t2.x, name:"…" }]; joined by an "=" to imply the match.
  function t2Row(cards) {
    const cells = cards.map(c => {
      const rec = c.rec || {};
      const glyph = t2Glyph(rec.sym), symCls = String(rec.sym || "").toLowerCase();
      return `<figure class="t2-card"><img src="${t2Img(rec.faceUid)}" alt="${esc(c.name)}">`
           + `<figcaption>${esc(c.name)} <span class="t2-sym t2-sym-${symCls}">${glyph}</span></figcaption></figure>`;
    });
    const single = cards.length === 1 ? " t2-row-single" : "";
    return `<div class="t2-row${single}">${cells.join('<div class="t2-eq">=</div>')}</div>`;
  }

  // ── New-tutorial helpers (Tutorial 2 free creature, B-Lob board state,
  //    Strategy-guide targeting). Function declarations so they hoist safely. ──
  // Tutorial 2: a simple FREE creature (the rigged Lobster) deployed via the
  // Mangrove's Play Again, free, so the step needs no separate payment.
  function gtFreeCreatureEntry() {
    const me = gtMe(); if (!me || !Array.isArray(me.hand)) return null;
    let fallback = null;
    for (const e of me.hand) {
      const f = gtFirstFace(e);
      const sp = String(f?.species || "").toLowerCase();
      if (sp.includes("ocean")) continue;
      const dir = String(f?.direction || "").trim().toLowerCase();
      if (!dir || dir === "n/a") continue;
      if (Number(f?.cost || 0) !== 0) continue;             // must be free
      if (String(f?.name || "").trim().toLowerCase() === "lobster") return e;  // ideal
      if (!fallback) fallback = e;                          // any free creature
    }
    return fallback;
  }
  function gtFreeCreatureEl() { const e = gtFreeCreatureEntry(); return e ? gtHandCardEl(gtEntryUid(e)) : null; }
  // Board-focus close control (Tutorial 2 "close the board view").
  function gtBoardFocusCloseEl() { const el = document.getElementById("pv-board-focus-close"); return (el && el.offsetParent !== null) ? el : null; }

  // ── B-Lob board-state validation (reads the live payload, not just counts) ──
  function blMyBoard() { const me = gtMe(); return (me && Array.isArray(me.board)) ? me.board : []; }
  function blReefOcean() {
    return blMyBoard().find(o => o && o.ocean && String(o.ocean.name || "").trim().toLowerCase() === "artificial reef") || null;
  }
  function blReefOnBoard() { return !!blReefOcean(); }
  // Lobsters live in Artificial Reef's DOWN lane (Lobster's only direction), and
  // "any number of lobsters can share the same spot", so 2 in that one lane is a
  // valid same-slot stack. Counting only reef.down rejects lobsters placed on a
  // different ocean or in a different lane.
  function blLobstersOnReef() {
    const reef = blReefOcean(); if (!reef) return 0;
    const down = Array.isArray(reef.down) ? reef.down : [];
    return down.filter(c => String((c && c.name) || "").trim().toLowerCase() === "lobster").length;
  }
  // California Gull attaches to Artificial Reef's UP lane (its only direction).
  function blGullOnReef() {
    const reef = blReefOcean(); if (!reef) return false;
    const up = Array.isArray(reef.up) ? reef.up : [];
    return up.some(c => String((c && c.name) || "").trim().toLowerCase() === "california gull");
  }
  // The Artificial Reef hub on the player's board (glow target for "drop here").
  function blReefHubEl() {
    const reef = blReefOcean();
    return reef ? document.querySelector(`#pv-my-board .pv-ocean-hub[data-ocean-uid="${reef.ocean_uid}"]`) : null;
  }
  // Two SAFE payment cards for California Gull's 2-card cost, the extra oceans
  // (Coral Reef + Mangrove) B-Lob doesn't need. Never the Lobsters (already on the
  // board by this step).
  function blGullPayEls() { return [blHandElByName("coral reef"), blHandElByName("mangrove")].filter(Boolean); }

  // ── Strategy-guide targeting (find cards by EXACT label, never by index) ──
  function blStratCardByLabel(label) {
    const list = document.getElementById("pv-help-list");
    if (!list) return null;
    const want = String(label || "").trim().toLowerCase();
    // Match a core/combo card (not the recommendation banner) by its visible name.
    const cards = list.querySelectorAll(".hs2-card, .hs2-combo");
    for (const c of cards) {
      const nm = c.querySelector(".hs2-card-name, .hs2-combo-name");
      if (nm && String(nm.textContent || "").trim().toLowerCase() === want) return c;
    }
    return null;
  }
  // Spotlight target for a strategy: the card's "Play this" button in list view,
  // or, if the player opened the strategy's detail page, the activation button.
  function blStratPlayEl(label) {
    const helpTut = window.__ccHelpTut;
    if (helpTut && helpTut.inDetail && helpTut.inDetail()) {
      const act = document.getElementById("hd-activate");
      const idx = helpTut.indexByLabel ? helpTut.indexByLabel(label) : -1;
      if (act && (idx < 0 || Number(act.dataset.strat) === idx)) return act;
    }
    const card = blStratCardByLabel(label);
    return card ? (card.querySelector(".hsc-toggle") || card) : null;
  }
  function blStratActive(label) { try { return !!(window.__ccHelpTut && window.__ccHelpTut.isActive(label)); } catch (_) { return false; } }
  function blHelpOpen() { try { return !!(window.__ccHelpTut && window.__ccHelpTut.isOpen()); } catch (_) { return !!document.getElementById("pv-help-modal")?.classList.contains("open"); } }
  // Suggested Combos row (Tutorial 3 "scroll the combos into view").
  function blCombosSectionEl() { const list = document.getElementById("pv-help-list"); return list ? (list.querySelector(".hs2-combos-row") || null) : null; }
  // Guarantee the Strategy modal is open AND showing the list (not a stale detail
  // page, and recovered if a stray backdrop click closed it) so the core/combo
  // cards are always present for the selection steps.
  function blEnsureHelpList() {
    try { const h = window.__ccHelpTut; if (h) { if (!h.isOpen()) h.open(); h.showList(); } } catch (_) {}
  }

  // Terminal-step handlers, assigned by runGameTour / runBLobTour before runCoach.
  let _t2Keep = null, _t2Return = null, _t3Keep = null, _t3Return = null;

  // ════════════════════════════════════════════════════════════════
  //  TUTORIAL 2, THE GAME (short: real setup + one Star ability)
  // ════════════════════════════════════════════════════════════════
  const GAME_STEPS = [
    { target: null, badge: "The Game", title: "Let's Play a Real Game",
      before: () => { try { navTab("overview"); } catch (_) {} },
      text: "We'll set up a real game, then learn a Star ability. Follow the glowing highlights." },

    // ── Setup (real create-game flow) ───────────────────────────────
    { target: "#stats-create-btn", badge: "Setup", title: "Create a Game", interactive: true, advanceWhen: gtModalOpen,
      before: () => { try { navTab("overview"); } catch (_) {} },
      text: "Click <strong>Create Game</strong>." },
    { target: "#nc-total", badge: "Setup", title: "Human Critters",
      before: () => { gtSetInput("nc-total", 1); gtSetInput("nc-ai", 2); },
      text: "This sets how many people are playing." },
    { target: "#nc-ai", badge: "Setup", title: "AI Critters",
      before: () => { gtSetInput("nc-total", 1); gtSetInput("nc-ai", 2); },
      text: "This sets how many computer opponents join." },
    { target: "#nc-create-btn", badge: "Setup", title: "Create the Room", interactive: true, advanceWhen: gtWaitingOpen,
      before: () => {
        const sel = document.getElementById("nc-visibility");
        if (sel && sel.value !== "public") { sel.value = "public"; sel.dispatchEvent(new Event("change", { bubbles: true })); }
        gtSetInput("nc-total", 1); gtSetInput("nc-ai", 2);
      },
      text: "Click <strong>Generate Current</strong>." },
    { target: "#wr-players-list", badge: "Setup", title: "Waiting Room",
      before: gtAllBotsEasy,
      text: "Players and computer opponents appear here." },
    { target: "#wr-start-btn", badge: "Setup", title: "Start the Game", interactive: true, advanceWhen: gtGameOpen,
      text: "Click <strong>Start Game</strong>." },

    // ── Gameplay ────────────────────────────────────────────────────
    { target: "#pv-guide-bar", badge: "Your Turn", title: "Follow the Guide",
      text: "This bar tells you what to do next." },
    { target: "#pv-draw-deck", badge: "Your Turn", title: "Draw Two", interactive: true,
      before: () => { _gtDrawBase = gtDrawCount(); _gtDrawSawTurn = false; _gtHandBase = gtHandCardCount(); },
      advanceWhen: () => {
        // Hand started full; drawing 2 brings it +2. Backups: optimistic draw
        // counter, and the 2nd draw ending the turn (can_act drops after we saw it).
        if (_gtHandBase >= 0 && gtHandCardCount() >= _gtHandBase + 2) return true;
        if (gtDrawCount() >= _gtDrawBase + 2) return true;
        if (gtMyTurn()) { _gtDrawSawTurn = true; return false; }
        return _gtDrawSawTurn;
      },
      text: "Draw two cards from the Deck or Pool." },
    { target: gtOtherSeat, badge: "Their Turn", title: "Opponents' Turns",
      before: () => { _gtSawOppTurn = false; },
      advanceWhen: () => { if (!gtMyTurn()) { _gtSawOppTurn = true; return false; } return _gtSawOppTurn; },
      text: "The computer players are taking their turns." },

    // ── One Star ability: Mangrove's Play Again ─────────────────────
    { target: gtMangroveHandEl, glow: [gtOceanDropEl], badge: "Play", title: "Play Mangrove",
      interactive: true, popAnchor: "top",
      before: () => { t2ForceStarOn(); t2CacheOceanPair(); _gtOceanBase = gtOceanCount(); },
      advanceWhen: () => gtPayingOcean() || gtOceanCount() > _gtOceanBase,
      text: "Drag Mangrove onto your board." },
    { target: gtArcticHandEl, glow: [gtArcticHandEl, "#pv-payment-confirm-btn", gtPayBarEl],
      badge: "Star", title: "Activate the Star", interactive: true, popAnchor: "top",
      advanceWhen: () => gtOceanCount() > _gtOceanBase,
      text: "Pay with the glowing matching-symbol card, then confirm." },
    { target: null, badge: "★ Star", title: "Star Activated!",
      text: "The symbols matched, so ★ Play Again activated." },
    { target: gtFreeCreatureEl, glow: ["#pv-my-board"], badge: "Play Again", title: "Play a Creature",
      interactive: true, popAnchor: "top",
      before: () => { _gtCreatureBase = gtCreatureCount(); },
      advanceWhen: () => gtCreatureCount() > _gtCreatureBase,
      text: "Use Play Again to place the glowing creature." },

    // ── Short explanations ──────────────────────────────────────────
    { target: gtPoolEl, glow: [gtPoolEl], badge: "The Pool", title: "The Pool",
      text: "Payments enter the Pool, where players can draw them later." },
    { target: gtOtherSeat, badge: "Scouting", title: "Inspect a Board",
      interactive: true, advanceWhen: gtBoardFocusOpen,
      text: "Click an opponent to inspect their board." },
    { target: gtBoardFocusCloseEl, badge: "Scouting", title: "Return to Your Board",
      interactive: true, advanceWhen: gtBoardFocusClosed,
      before: () => { try { const s = gtOtherSeat(); if (s && !gtBoardFocusOpen()) s.click(); } catch (_) {} },
      text: "Close the board view." },
    { target: "#pv-my-score-badge", badge: "Scoring", title: "Your Score",
      text: "Your current score appears here." },
    { target: "#pv-end-turn-inline", badge: "Your Turn", title: "End Your Turn",
      text: "Use End Turn when you are finished making moves." },
    { target: "#pv-draw-deck", badge: "Endgame", title: "Ending the Game",
      text: "The END GAME card starts the final round. The highest score wins." },

    // ── Complete (no full match required) ───────────────────────────
    { target: null, badge: "Complete", title: "Tutorial 2 Complete!",
      text: "You created a game, played Mangrove, and fired a Star ability. What next?",
      choices: [
        { label: "Keep Playing", primary: true, onClick: () => { try { _t2Keep && _t2Keep(); } catch (_) {} } },
        { label: "Return to Tutorials", onClick: () => { try { _t2Return && _t2Return(); } catch (_) {} } },
      ] },
  ];

  function runGameTour() {
    closeChooser();
    const lobby = document.getElementById("auth-stats-lobby");
    if (!lobby || !lobby.classList.contains("visible")) {
      runCoach([{ target: null, badge: "The Game", title: "Open from the Main Menu",
        text: "This tutorial creates a real game from the menu. Please open the <strong>Main Menu</strong> first, then choose <strong>Tutorial, then The Game</strong>." }], null);
      return;
    }
    // Flag the next created game as a tutorial so the server rigs a playable
    // opening hand. Consumed (cleared) by the create handler.
    window.__ccTutorialGame = true;
    let _exited = false, _keepPlaying = false;
    const _cleanup = async () => {
      if (_exited) return;
      _exited = true;
      window.__ccTutorialGame = false;
      if (_keepPlaying) return;             // Keep Playing: leave the player in the live match
      try { if (window.__tutLeaveGame) await window.__tutLeaveGame(); } catch (_) {}
      setTimeout(openChooser, 700);
    };
    _t2Keep = () => {
      setDone("game"); showToast("Tutorial 2 complete", completionSubtitle());
      _keepPlaying = true; endCoach();      // close coachmarks, stay in the match
    };
    _t2Return = () => {
      setDone("game"); showToast("Tutorial 2 complete", completionSubtitle());
      _keepPlaying = false; endCoach();     // close coachmarks, leave match, reopen chooser
    };
    // No onDone (the final step's own buttons drive completion); Skip → _cleanup
    // (leaves the match, no setDone).
    runCoach(GAME_STEPS, null, _cleanup);
  }

  // ════════════════════════════════════════════════════════════════
  //  PRACTICE GAME (B-Lob), real game, like "The Game" tour, but the
  //  server rigs an exact Bird+Lobster hand (tutorial_variant="blob") and
  //  the lesson teaches the B-Lob strategy on the real board.
  // ════════════════════════════════════════════════════════════════
  // Find the live hand entry whose any face matches a card name.
  function blHandEntryByName(nameLc) {
    const me = gtMe(); if (!me || !Array.isArray(me.hand)) return null;
    for (const e of me.hand) {
      const faces = (Array.isArray(e.faces) && e.faces.length) ? e.faces : [e];
      for (const f of faces) {
        if (String((f && f.name) || "").trim().toLowerCase() === nameLc) return e;
      }
    }
    return null;
  }
  function blHandElByName(nameLc) { const e = blHandEntryByName(nameLc); return e ? gtHandCardEl(gtEntryUid(e)) : null; }
  function blNarwhalEl() { return blHandElByName("big eye tuna") || blHandElByName("narwhal"); }
  function blEndTurnEl() { const el = document.getElementById("pv-end-turn-inline"); return (el && el.offsetParent !== null) ? el : null; }

  // ════════════════════════════════════════════════════════════════
  //  TUTORIAL 3, PRACTICE GAME (B-Lob): real setup, then the Strategy
  //  guide + a guided B-Lob turn sequence (one action per turn).
  // ════════════════════════════════════════════════════════════════
  const BLOB_STEPS = [
    { target: null, badge: "Practice Game", title: "Let's Practice B-Lob",
      before: () => { try { navTab("overview"); } catch (_) {} },
      text: "We'll set up a practice game, then learn the B-Lob strategy together." },

    // ── Setup (real create-game flow, 1 human + 3 AI) ───────────────
    { target: "#stats-create-btn", badge: "Setup", title: "Create a Practice Game", interactive: true, advanceWhen: gtModalOpen,
      before: () => { try { navTab("overview"); } catch (_) {} },
      text: "Click <strong>Create Game</strong>." },
    { target: "#nc-total", badge: "Setup", title: "Human Critters",
      before: () => { gtSetInput("nc-total", 1); gtSetInput("nc-ai", 3); },
      text: "This practice game has one human player." },
    { target: "#nc-ai", badge: "Setup", title: "AI Critters",
      before: () => { gtSetInput("nc-total", 1); gtSetInput("nc-ai", 3); },
      text: "Computer opponents complete the table." },
    { target: "#nc-create-btn", badge: "Setup", title: "Create the Room", interactive: true, advanceWhen: gtWaitingOpen,
      before: () => {
        const sel = document.getElementById("nc-visibility");
        if (sel && sel.value !== "public") { sel.value = "public"; sel.dispatchEvent(new Event("change", { bubbles: true })); }
        gtSetInput("nc-total", 1); gtSetInput("nc-ai", 3);
      },
      text: "Click <strong>Generate Current</strong>." },
    { target: "#wr-players-list", badge: "Setup", title: "Waiting Room",
      before: gtAllBotsEasy,
      text: "Your computer opponents are ready." },
    { target: "#wr-start-btn", badge: "Setup", title: "Start the Game", interactive: true, advanceWhen: gtGameOpen,
      text: "Click <strong>Start Game</strong>." },

    // ── Strategy guide (Help → pick Crustaceans → Bird Lobster) ─────
    { target: "#pv-guide-bar", badge: "Your Turn", title: "Follow the Guide",
      text: "This bar tells you what to do next." },
    { target: "#pv-help-btn", badge: "Strategy", title: "Open Strategy Help", interactive: true,
      // Reset the two tutorial strategies so the player turns them on themselves.
      before: () => { try { window.__ccHelpTut && window.__ccHelpTut.ensureInactive(["Crustaceans", "Bird Lobster"]); } catch (_) {} },
      advanceWhen: blHelpOpen,
      text: "On the left, click 💡 Help." },
    { target: () => blStratPlayEl("Crustaceans"), badge: "Strategy", title: "Choose Crustaceans",
      interactive: true, before: blEnsureHelpList, advanceWhen: () => blStratActive("Crustaceans"),
      text: "Select Crustaceans, then click Play this." },
    { target: () => blCombosSectionEl(), badge: "Strategy", title: "Suggested Combos",
      before: blEnsureHelpList,
      text: "These combos pair well with Crustaceans." },
    { target: () => blStratPlayEl("Bird Lobster"), badge: "Strategy", title: "Choose Bird Lobster",
      interactive: true, before: blEnsureHelpList, advanceWhen: () => blStratActive("Bird Lobster"),
      text: "Under Suggested Combos, select Bird Lobster and click Play this." },
    { target: "#pv-help-close", badge: "Strategy", title: "Return to the Game", interactive: true,
      advanceWhen: () => !blHelpOpen(),
      text: "Close the Strategy Guide." },
    { target: null, badge: "Strategy", title: "Your Strategy Is Ready",
      text: "B-Lob cards now glow in your hand and Pool." },

    // ── B-Lob card lesson (spotlight each key card) ─────────────────
    { target: null, badge: "B-Lob", title: "B-Lob",
      text: "B-Lob combines Birds and Lobsters." },
    { target: () => blHandElByName("artificial reef"), badge: "B-Lob", title: "Artificial Reef",
      text: "Artificial Reef scores +2 for every attached card." },
    { target: () => blHandElByName("lobster"), badge: "B-Lob", title: "Lobster",
      text: "Lobsters are free and can stack in one slot." },
    { target: () => blHandElByName("california gull"), badge: "B-Lob", title: "California Gull",
      text: "California Gull scores +2 per crustacean." },

    // ── Guided B-Lob turns (one action per turn) ────────────────────
    { target: () => blHandElByName("artificial reef"), glow: [gtOceanDropEl], badge: "Turn 1", title: "Play Artificial Reef",
      interactive: true, popAnchor: "top",
      before: () => { _gtOceanBase = gtOceanCount(); },
      advanceWhen: () => gtPayingOcean() || blReefOnBoard(),
      text: "Drag Artificial Reef onto your board." },
    { target: gtPayBarEl, glow: [blNarwhalEl, "#pv-payment-confirm-btn", gtPayBarEl], badge: "Turn 1", title: "Pay the Cost",
      interactive: true, popAnchor: "top", advanceWhen: () => blReefOnBoard(),
      text: "Use Narwhal or Big Eye Tuna as payment, then confirm." },
    { target: blEndTurnEl, badge: "Your Turn", title: "End Your Turn", interactive: true, allowNext: true,
      before: () => { _gtSawOppTurn = false; },
      advanceWhen: () => { if (!gtMyTurn()) { _gtSawOppTurn = true; return false; } return _gtSawOppTurn; },
      text: "End your turn so the computer players can play." },

    { target: () => blHandElByName("lobster"), glow: [blReefHubEl], badge: "Turn 2", title: "Play Lobster",
      interactive: true, popAnchor: "top",
      advanceWhen: () => blLobstersOnReef() >= 1,
      text: "Drag Lobster onto Artificial Reef." },
    { target: blEndTurnEl, badge: "Your Turn", title: "End Your Turn", interactive: true, allowNext: true,
      before: () => { _gtSawOppTurn = false; },
      advanceWhen: () => { if (!gtMyTurn()) { _gtSawOppTurn = true; return false; } return _gtSawOppTurn; },
      text: "End your turn." },

    { target: () => blHandElByName("lobster"), glow: [blReefHubEl], badge: "Turn 3", title: "Stack Another",
      interactive: true, popAnchor: "top",
      advanceWhen: () => blLobstersOnReef() >= 2,
      text: "Place the second Lobster in the same slot." },
    { target: null, badge: "B-Lob", title: "Lobsters Stacked!",
      text: "Two Lobsters now share one slot." },
    { target: blEndTurnEl, badge: "Your Turn", title: "End Your Turn", interactive: true, allowNext: true,
      before: () => { _gtSawOppTurn = false; },
      advanceWhen: () => { if (!gtMyTurn()) { _gtSawOppTurn = true; return false; } return _gtSawOppTurn; },
      text: "End your turn." },

    { target: () => blHandElByName("california gull"), glow: [blReefHubEl], badge: "Turn 4", title: "Play California Gull",
      interactive: true, popAnchor: "top",
      advanceWhen: () => gtPayingCreature() || blGullOnReef(),
      text: "Place California Gull above Artificial Reef." },
    { target: gtPayBarEl, glow: [() => blGullPayEls()[0], () => blGullPayEls()[1], "#pv-payment-confirm-btn", gtPayBarEl],
      badge: "Turn 4", title: "Pay Two Cards", interactive: true, popAnchor: "top",
      advanceWhen: () => blGullOnReef(),
      text: "Select the two glowing payment cards, then confirm." },
    { target: null, badge: "B-Lob", title: "B-Lob Working!",
      text: "California Gull gains +2 per Lobster because Lobsters are crustaceans." },

    // ── Complete (no full match required) ───────────────────────────
    { target: null, badge: "Complete", title: "Tutorial 3 Complete!",
      text: "You built the B-Lob combo: stacked Lobsters under Artificial Reef, topped with California Gull. What next?",
      choices: [
        { label: "Keep Playing", primary: true, onClick: () => { try { _t3Keep && _t3Keep(); } catch (_) {} } },
        { label: "Return to Tutorials", onClick: () => { try { _t3Return && _t3Return(); } catch (_) {} } },
      ] },
  ];

  function runBLobTour() {
    closeChooser();
    const lobby = document.getElementById("auth-stats-lobby");
    if (!lobby || !lobby.classList.contains("visible")) {
      runCoach([{ target: null, badge: "Practice", title: "Open from the Main Menu",
        text: "This tutorial sets up a real game from the menu. Please open the <strong>Main Menu</strong> first, then choose <strong>Tutorial, then Practice Game (B-Lob)</strong>." }], null);
      return;
    }
    // Save the strategies active BEFORE this tutorial so Return/Skip can restore
    // them. (Keep Playing leaves the tutorial's Crustaceans + Bird Lobster on.)
    let _blobPrevStrats = [];
    try { if (window.__ccHelpTut) _blobPrevStrats = window.__ccHelpTut.activeLabels(); } catch (_) {}
    // Flag the next created game as a B-Lob tutorial so the server rigs the exact
    // Bird+Lobster opening hand. Both flags are consumed by the create handler.
    window.__ccTutorialGame = true;
    window.__ccTutorialVariant = "blob";
    let _exited = false, _keepPlaying = false;
    const _cleanup = async () => {
      if (_exited) return;
      _exited = true;
      window.__ccTutorialGame = false;
      window.__ccTutorialVariant = "";
      try { if (window.__ccHelpTut) window.__ccHelpTut.close(); } catch (_) {}
      if (_keepPlaying) return;   // Keep Playing: stay in match, keep B-Lob strategies on
      // Return to Tutorials OR Skip: restore pre-tutorial strategies, leave match.
      try { if (window.__ccHelpTut) window.__ccHelpTut.setActiveLabels(_blobPrevStrats); } catch (_) {}
      try { if (window.__tutLeaveGame) await window.__tutLeaveGame(); } catch (_) {}
      setTimeout(openChooser, 700);
    };
    _t3Keep = () => {
      setDone("practice"); showToast("Tutorial 3 complete", completionSubtitle());
      _keepPlaying = true; endCoach();      // close coachmarks, stay in match, strategies stay on
    };
    _t3Return = () => {
      setDone("practice"); showToast("Tutorial 3 complete", completionSubtitle());
      _keepPlaying = false; endCoach();     // close coachmarks, restore strategies, leave match
    };
    // No onDone (final step's buttons drive completion); Skip → _cleanup (restores
    // strategies, leaves the match, no setDone).
    runCoach(BLOB_STEPS, null, _cleanup);
  }

  // ════════════════════════════════════════════════════════════════
  //  TUTORIAL 4, ONLINE PLAY & CONTROLS
  //  Creates a real game, then teaches the online/table features moved
  //  out of Tutorial 2: rooms (public/private + code), bot difficulty,
  //  chat, Surf's Up, AFK rules, and the card viewer + hand rearrange.
  // ════════════════════════════════════════════════════════════════
  const wrDiffBoxEl = () => document.querySelector("#wr-players-list .wr-diff-box");
  const ONLINE_STEPS = [
    { target: null, badge: "Online Play", title: "Online Play & Controls",
      before: () => { try { navTab("overview"); } catch (_) {} },
      text: "Rooms, bots, chat, breaks, AFK rules, and card controls, let's set up a game and see them." },

    // ── Group 1: setting up rooms ───────────────────────────────────
    { target: "#stats-create-btn", badge: "Rooms", title: "Create a Game", interactive: true, advanceWhen: gtModalOpen,
      before: () => { try { navTab("overview"); } catch (_) {} },
      text: "Click <strong>Create Game</strong>." },
    { target: "#nc-visibility", badge: "Rooms", title: "Public or Private", interactive: true,
      before: () => {
        gtSetInput("nc-total", 1); gtSetInput("nc-ai", 1);
        const sel = document.getElementById("nc-visibility");
        if (sel && sel.value !== "public") { sel.value = "public"; sel.dispatchEvent(new Event("change", { bubbles: true })); }
      },
      advanceWhen: () => { const sel = document.getElementById("nc-visibility"); return !!(sel && sel.value === "private"); },
      text: "Public rooms appear in Open Currents. Private rooms need a code. Switch to <strong>Private</strong>." },
    { target: "#nc-password", badge: "Rooms", title: "Your Room Code",
      before: () => {
        const row = document.getElementById("nc-password-row"); if (row) row.style.display = "";
        const pw = document.getElementById("nc-password");
        if (pw && !pw.value.trim()) { pw.value = "FISHY"; pw.dispatchEvent(new Event("input", { bubbles: true })); pw.dispatchEvent(new Event("change", { bubbles: true })); }
      },
      text: "This is your room code. A joiner enters it in <strong>Join Game → Private</strong>." },
    { target: "#nc-create-btn", badge: "Rooms", title: "Create the Room", interactive: true, advanceWhen: gtWaitingOpen,
      before: () => {
        const sel = document.getElementById("nc-visibility");
        if (sel && sel.value !== "private") { sel.value = "private"; sel.dispatchEvent(new Event("change", { bubbles: true })); }
        const row = document.getElementById("nc-password-row"); if (row) row.style.display = "";
        const pw = document.getElementById("nc-password");
        if (pw && !pw.value.trim()) { pw.value = "FISHY"; pw.dispatchEvent(new Event("input", { bubbles: true })); pw.dispatchEvent(new Event("change", { bubbles: true })); }
      },
      text: "Click <strong>Generate Current</strong>." },
    { target: "#wr-players-list", glow: [wrDiffBoxEl], badge: "Rooms", title: "Bot Difficulty",
      text: "Each bot can be Easy, Medium, or Hard. As host, change any bot's difficulty here." },
    { target: "#wr-start-btn", badge: "Rooms", title: "Start the Game", interactive: true, advanceWhen: gtGameOpen,
      before: gtAllBotsEasy,
      text: "Click <strong>Start Game</strong>." },

    // ── Group 2: chat ───────────────────────────────────────────────
    { target: "#pv-chat-btn", badge: "Chat", title: "Table Chat",
      text: "Tap 💬 Chat to talk with everyone in the current game." },

    // ── Group 3: Surf's Up & AFK rules ──────────────────────────────
    { target: "#pv-surf-btn", badge: "Breaks", title: "Surf's Up",
      text: "Tap 🏄 Surf's Up to go Away, you can't move while Away. Tap <strong>I'm Back</strong> to return." },
    { target: "#pv-chat-btn", badge: "AFK", title: "Reporting AFK",
      text: "If a player vanishes on their own turn, report them in chat. Capitals don't matter:<br><span style=\"display:inline-block;background:#0d2c4e;border:1px solid #1f4f7a;border-radius:6px;padding:3px 8px;margin:3px;color:#cfe6fb\">P1 AFK</span> <span style=\"display:inline-block;background:#0d2c4e;border:1px solid #1f4f7a;border-radius:6px;padding:3px 8px;margin:3px;color:#cfe6fb\">P1 away</span> <span style=\"display:inline-block;background:#0d2c4e;border:1px solid #1f4f7a;border-radius:6px;padding:3px 8px;margin:3px;color:#cfe6fb\">PlayerName away</span>" },
    { target: null, badge: "AFK", title: "The 20-Second Check",
      text: "The reported player sees this:<div style=\"margin:10px auto 6px;max-width:240px;background:linear-gradient(180deg,#0c2c4e,#08233f);border:1px solid #1f4f7a;border-radius:14px;padding:12px 12px 14px;text-align:center;box-shadow:0 8px 26px rgba(0,0,0,.45)\"><div style=\"font-size:22px;line-height:1\">⚠️</div><div style=\"font-weight:800;color:#eaf4ff;font-size:14px;margin:3px 0\">Are You There?</div><div style=\"width:48px;height:48px;border-radius:50%;border:3px solid #34c3ff;display:flex;align-items:center;justify-content:center;margin:9px auto 6px;font-weight:900;color:#eaf4ff;font-size:18px\">20</div><div style=\"font-size:10.5px;color:#9fc3e0;line-height:1.35\"><b style=\"color:#34c3ff\">Move your mouse or click</b> to stay in.</div></div>They have 20 seconds. Moving the mouse cancels it. If time runs out, 2 cards are drawn and their turn passes." },
    { target: "#pv-draw-deck", badge: "AFK", title: "The 20-Card Rule",
      text: "Auto-draws can grow a hand to 20 cards. Once back to normal play, the standard 10-card limit returns." },

    // ── Group 4: card controls ──────────────────────────────────────
    { target: "#pv-hand", badge: "Cards", title: "Enlarge a Card", interactive: true, advanceWhen: gtZoomOpen,
      text: "Click a card to enlarge it." },
    { target: "#pv-zoom-modal", badge: "Cards", title: "Flip & Close", interactive: true, advanceWhen: gtZoomClosed,
      text: "Use ‹ and › to view the previous and next card, then close the viewer." },
    { target: "#pv-hand", badge: "Cards", title: "Rearrange Your Hand", interactive: true,
      before: () => { _tutHandSig = gtHandSig(); },
      advanceWhen: () => gtHandSig() !== _tutHandSig,
      text: "Drag a card onto another to rearrange your hand." },

    { target: null, badge: "Complete", title: "Online Play & Controls Complete!",
      text: "That's rooms, bots, chat, breaks, AFK rules, and card controls. Select <strong>Finish</strong> to return to the tutorials." },
  ];

  function runOnlineTour() {
    closeChooser();
    const lobby = document.getElementById("auth-stats-lobby");
    if (!lobby || !lobby.classList.contains("visible")) {
      runCoach([{ target: null, badge: "Online Play", title: "Open from the Main Menu",
        text: "This tutorial creates a real game from the menu. Please open the <strong>Main Menu</strong> first, then choose <strong>Tutorial, then Online Play &amp; Controls</strong>." }], null);
      return;
    }
    window.__ccTutorialGame = true;
    let _exited = false;
    const _exit = async () => {
      if (_exited) return;
      _exited = true;
      window.__ccTutorialGame = false;
      try { if (window.__tutLeaveGame) await window.__tutLeaveGame(); } catch (_) {}
      setTimeout(openChooser, 700);
    };
    runCoach(ONLINE_STEPS, async () => {
      setDone("online");
      showToast("Online Play & Controls complete", completionSubtitle());
      await _exit();
    }, _exit);
  }

  // ════════════════════════════════════════════════════════════════
  //  COMPETITIVE 1v1 TOUR
  //  Walks the REAL competitive entry: Create Game opens the New Current
  //  setup modal, where Mode → ⚔️ Competitive locks it to the ranked format
  //  (4 humans = 2 players × 2 hands), then the standard waiting room. An
  //  opponent joins via Quick Match → Competitive. No live game is created,
  //  competitive needs two real human players, so we explain the setup
  //  and joining, then cover ranks, CP, the hand-switch, and strategy.
  // ════════════════════════════════════════════════════════════════
  const gtCompModalOpen   = () => !!document.getElementById("new-current-modal")?.classList.contains("open");
  const gtCompModalClosed = () => !document.getElementById("new-current-modal")?.classList.contains("open");
  function closeCompTourModal() {
    try { if (typeof closeNewCurrentModal === "function") closeNewCurrentModal(); } catch (_) {}
  }

  const COMP_STEPS = [

    // ── 1. Welcome ──────────────────────────────────────────────────
    { target: null, badge: "Competitive 1v1", title: "What is Competitive?",
      before: () => { closeMenuOverlays(); closeCompTourModal(); try { navTab("overview"); } catch (_) {} },
      text: "Welcome to <strong>Competitive 1v1</strong>, the ranked way to play Currents &amp; Critters. Two players go head-to-head, but here's the twist: <strong>each player controls TWO hands</strong> at the same table, playing both on their own device. At the end, your <strong>best-scoring hand</strong> is compared to your opponent's best, the higher score <strong>wins the match and earns CP</strong> (Competitive Points) toward your rank." },

    // ── 2. Open Create Game (interactive) → opens the setup modal ───
    { target: "#stats-create-btn", badge: "Step 1", title: "Open Create Game",
      before: () => { closeMenuOverlays(); closeCompTourModal(); try { navTab("overview"); } catch (_) {} },
      interactive: true, advanceWhen: gtCompModalOpen,
      text: "To <em>host</em> a ranked match you set one up from <strong>Create Game</strong>. <strong>Click it now</strong> to open the match-setup window. <em>(To instead <em>join</em> someone else's open match, use <strong>Quick Match → Competitive</strong>.)</em>" },

    // ── 3. Switch Mode to Competitive ───────────────────────────────
    { target: "#nc-field-mode", badge: "Match Setup", title: "Switch to Competitive",
      before: () => { try { applyNcMode(true); } catch (_) {} },
      text: "The setup window has a <strong>Mode</strong> dropdown. Set it to <strong>⚔️ Competitive</strong> to lock the match to the ranked format, we've flipped it for you. The title now reads <strong>⚔️ Competitive 1v1, 2 hands per player</strong>. Let's look at why the settings are fixed." },

    // ── 4. Human Critters locked to 4 ───────────────────────────────
    { target: "#nc-total", badge: "Match Setup", title: "Four Hands = Two Players",
      before: () => { closeMenuOverlays(); },
      text: "<strong>Human Critters is locked to 4.</strong> That's <strong>2 players × 2 hands each</strong>, a full four-seat table shared between just two people. You can't change it: competitive is always this exact shape." },

    // ── 5. AI Critters locked to 0 ──────────────────────────────────
    { target: "#nc-ai", badge: "Match Setup", title: "No Bots in Ranked",
      text: "<strong>AI Critters is locked to 0.</strong> Competitive is strictly human-vs-human, there are no bots in a ranked match, because CP and your rank are on the line. Every hand at the table is controlled by a real player." },

    // ── 6. Privacy locked to Public ─────────────────────────────────
    { target: "#nc-field-privacy", badge: "Match Setup", title: "Locked to Public",
      text: "In Competitive, <strong>Privacy is locked to 🌊 Public</strong>, your match appears in <strong>Open Currents</strong> so an opponent can find and join it. Everything is fixed for ranked play, so all you have to do is press the main button. <em>(Switch Mode back to 🐠 Normal any time for a fully customizable game where you can pick Public or Private.)</em>" },

    // ── 7. Generate Current (explain, don't create) ────────────────
    { target: "#nc-create-btn", badge: "Match Setup", title: "Generate the Match",
      text: "Clicking <strong>Generate Current →</strong> creates the room and drops you into the <strong>waiting room</strong>, where your room code is shown to share. You'd wait there for your opponent to join, then press Start.<br><br>We <em>won't</em> actually create one now, a real match needs a second human player. Let's close this and learn how that second player gets in." },

    // ── 8. Close the setup (interactive) ───────────────────────────
    { target: "#nc-close", badge: "Match Setup", title: "Close the Setup",
      interactive: true, advanceWhen: gtCompModalClosed,
      text: "Click the <strong>✕</strong> to close the setup window." },

    // ── 9. How Player 2 joins ───────────────────────────────────────
    { target: "#stats-quickmatch-btn", badge: "Joining", title: "How Player 2 Joins",
      before: () => { closeMenuOverlays(); closeCompTourModal(); try { navTab("overview"); } catch (_) {} },
      text: "Your opponent joins from <em>their</em> device. The easiest way: they tap <strong>Quick Match</strong> and choose <strong>⚔️ Competitive</strong>, the app finds your open match and <strong>automatically hands them BOTH of their seats</strong> (Hands 3 &amp; 4), no seat-picking. (They can also find it in <strong>Open Currents</strong> via Browse.) Once all four hands are filled, the host presses <strong>Start</strong> and the match begins." },

    // ── 10. Navigate to Competitive stats tab (interactive) ─────────
    { target: "#snav-competitive", badge: "Your Stats", title: "Your Competitive Record",
      before: () => { closeMenuOverlays(); try { navTab("overview"); } catch (_) {} },
      interactive: true, advanceWhen: gtTabActive("snav-competitive"),
      text: "Your personal ranked history lives in the <strong>Competitive tab</strong> on the menu sidebar. <strong>Click it now</strong> to see your rank." },

    // ── 11. Competitive panel, rank divisions ──────────────────────
    { target: "#ph-panel-competitive", badge: "Your Rank", title: "The Rank Ladder",
      text: "Once you've played ranked games, this panel shows your <strong>Competitive Points (CP)</strong>, your current <strong>rank division</strong>, and a bar toward the next. The ladder climbs through six tiers:<br><strong>🐠 Bronze Barracuda → 🦞 Silver Spiny Lobster → 🐡 Golden Grouper → 🐬 Diamond Dolphin → 🐧 Emerald Emperor Penguin → 👑 King of the Critters</strong>.<br>Each tier (except King) has three sub-divisions: <strong>I, II, III</strong>." },

    // ── 12. CP formula ─────────────────────────────────────────────
    { target: null, badge: "How CP Works", title: "Earning and Losing CP",
      text: "After every ranked match:<br>• <strong>Win</strong>, gain CP (about +18 to +26).<br>• <strong>Loss</strong>, lose CP.<br>• <strong>Draw</strong>, a small CP gain.<br>The higher your rank, the <strong>more CP a loss costs</strong> and the less a win gives, so the climb gets steeper near the top. If both players' best hands tie, the <strong>second hand breaks the tie</strong>. Reach <strong>1200 CP</strong> to become <strong>👑 King of the Critters</strong>, the season's top rank." },

    // ── 13. Hand-switch in the game ────────────────────────────────
    { target: null, badge: "In the Game", title: "The Hand-Switch Overlay",
      text: "In a competitive game, turns cycle through all four seats automatically. When the active turn reaches <em>your other hand</em>, a full-screen <strong>\"Your Turn\"</strong> overlay slides in, showing your name and which hand is now up. <strong>Tap anywhere to dismiss it</strong> and play that hand. It's there so you never accidentally play the wrong board." },

    // ── 14. Strategy: two hands ────────────────────────────────────
    { target: null, badge: "Strategy", title: "Playing Two Hands",
      text: "Controlling two hands is a real edge, if you use it well:<br>• <strong>Coordinate</strong>, a card that doesn't fit Hand 1's plan can be spent as <em>payment</em> on Hand 2 instead of being wasted.<br>• <strong>Diversify</strong>, run two different strategies (say B-Lob on Hand 1, a bird build on Hand 2) so at least one lands big.<br>• <strong>Feed your leader</strong>, only your <em>best</em> hand decides the match, so pour your strongest draws into whichever hand is pulling ahead." },

    // ── 15. In-game menu ───────────────────────────────────────────
    { target: null, badge: "In the Game", title: "Tracking All Four Hands",
      text: "During a ranked match the <strong>☰ Menu</strong> in the top bar gains a <strong>⚔️ Hands</strong> section listing the live score of every hand, including your opponent's. Check it any time to see whether you're ahead, behind, or tied without scrolling around the board." },

    // ── 16. Done ───────────────────────────────────────────────────
    { target: "#stats-create-btn", badge: "All Done!", title: "Ready to Compete!",
      before: () => { closeMenuOverlays(); closeCompTourModal(); try { navTab("overview"); } catch (_) {} },
      text: "That's Competitive 1v1! To host: tap <strong>Create Game</strong>, switch <strong>Mode</strong> to <strong>⚔️ Competitive</strong>, and <strong>Generate</strong> the match. To join someone else's, tap <strong>Quick Match → Competitive</strong>. Either way, once both players are in you start, build two strong hands and watch the scores. Climb from <strong>Bronze Barracuda</strong> all the way to <strong>👑 King of the Critters</strong>. Good luck! 🏆<br><br>Click <strong>Finish ✓</strong> to mark this tutorial complete." },
  ];

  function runCompTour() {
    closeChooser();
    const lobby = document.getElementById("auth-stats-lobby");
    if (!lobby || !lobby.classList.contains("visible")) {
      runCoach([{ target: null, badge: "Competitive Tour", title: "Open from the Main Menu",
        text: "This tour walks through Competitive match setup and your rank. Please open the <strong>Main Menu</strong> first, then choose <strong>Tutorial → Competitive 1v1</strong>." }], null);
      return;
    }
    runCoach(COMP_STEPS, () => {
      closeMenuOverlays();
      closeCompTourModal();
      try { navTab("overview"); } catch (_) {}
      setDone("competitive");
      showToast("Competitive tour complete", completionSubtitle());
      setTimeout(openChooser, 900);
    }, () => { closeMenuOverlays(); closeCompTourModal(); });
  }

  // ════════════════════════════════════════════════════════════════
  //  CHOOSER MODAL
  // ════════════════════════════════════════════════════════════════
  let chooser = null;
  function buildChooser() {
    if (chooser) return chooser;
    chooser = document.createElement("div");
    chooser.id = "tut3-chooser";
    document.body.appendChild(chooser);
    chooser.addEventListener("click", (e) => { if (e.target === chooser) closeChooser(); });
    return chooser;
  }

  const OPTS = [
    { key: "menu",        ico: "🗺️",  title: "Main Menu Tour",        desc: "Everything on the menu: profile, streak, tabs, and the store.",                       run: runMenuTour,   ready: true },
    { key: "game",        ico: "🎴",  title: "The Game",              desc: "Set up a real game, then play a card and fire a Star ability.",                       run: runGameTour,   ready: true },
    { key: "practice",    ico: "🦞",  title: "Practice Game (B-Lob)", desc: "Use the Strategy guide and build the Bird + Lobster combo on the real board.",        run: runBLobTour,   ready: true },
    { key: "online",      ico: "🛟",  title: "Online Play & Controls", desc: "Rooms, bots, chat, breaks, AFK rules, and card controls.",                            run: runOnlineTour, ready: true },
    { key: "competitive", ico: "⚔️", title: "Competitive 1v1",       desc: "Ranked 1v1 play: two hands each, CP, rank divisions, the hand-switch, and strategy.", run: runCompTour,   ready: true },
  ];

  function renderChooser() {
    const done = getDone();
    const c = buildChooser();
    const pips = ["menu", "game", "practice", "online", "competitive"].map(k => `<span class="tut3-pip ${done[k] ? "on" : ""}"></span>`).join("");
    const optHtml = OPTS.map(o => {
      const isDone = !!done[o.key];
      const locked = !o.ready;
      const status = isDone ? `<span class="tut3-opt-status tut3-st-done">✓ Done</span>`
        : locked ? `<span class="tut3-opt-status tut3-st-soon">Coming soon</span>`
        : `<span class="tut3-opt-status tut3-st-start">Start ▶</span>`;
      return `<button class="tut3-opt ${locked ? "tut3-locked" : ""}" data-key="${o.key}" ${locked ? "disabled" : ""}>
        <span class="tut3-opt-ico">${o.ico}</span>
        <span class="tut3-opt-body"><span class="tut3-opt-title">${esc(o.title)}</span><span class="tut3-opt-desc">${esc(o.desc)}</span></span>
        ${status}
      </button>`;
    }).join("");
    const count = ["menu", "game", "practice", "online", "competitive"].filter(k => done[k]).length;
    c.innerHTML = `<div class="tut3-card">
      <div class="tut3-hd"><h2>How to Play</h2><p>Five tutorials. Finish all five to earn a reward.</p></div>
      <div class="tut3-opts">${optHtml}</div>
      <div class="tut3-foot">
        <div class="tut3-reward"><img src="/avatars/osprey.png" alt="Osprey"> Complete all five to unlock the <span style="color:#0f4d86">Osprey</span> ${allDone() ? "(unlocked) ✓" : `(${count}/5)`}</div>
        <div class="tut3-prog">${pips}</div>
        <button class="tut3-close" id="tut3-chooser-close">Maybe later</button>
      </div>
    </div>`;
    c.querySelectorAll(".tut3-opt").forEach(btn => btn.addEventListener("click", () => {
      const o = OPTS.find(x => x.key === btn.getAttribute("data-key"));
      if (o && o.ready && o.run) o.run();
    }));
    c.querySelector("#tut3-chooser-close").addEventListener("click", closeChooser);
  }

  function openChooser() { renderChooser(); chooser.classList.add("open"); }
  function closeChooser() { if (chooser) chooser.classList.remove("open"); }

  // ── Completion toast ──────────────────────────────────────────────
  let toastEl = null;
  function completionSubtitle() {
    const n = ["menu", "game", "practice", "online", "competitive"].filter(k => getDone()[k]).length;
    return allDone() ? "All five done. Osprey unlocked! 🦅" : `${n} of 5 complete`;
  }
  function showToast(title, sub) {
    if (!toastEl) { toastEl = document.createElement("div"); toastEl.id = "tut3-toast"; document.body.appendChild(toastEl); }
    toastEl.innerHTML = `<span style="font-size:1.3rem">🎉</span><span><div>${esc(title)}</div><div style="font-size:.74rem;opacity:.9;font-weight:600">${esc(sub)}</div></span>`;
    toastEl.classList.add("show");
    setTimeout(() => toastEl.classList.remove("show"), 3200);
  }

  // ── Public entry ──────────────────────────────────────────────────
  window.__openTutorialChooser = openChooser;
})();
