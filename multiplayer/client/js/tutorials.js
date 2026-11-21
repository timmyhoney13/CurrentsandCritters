(function () {
  "use strict";

  // ════════════════════════════════════════════════════════════════
  //  TUTORIALS: short videos
  // ════════════════════════════════════════════════════════════════
  // The tutorials used to be click-through tours: a popup per step, and a
  // rigged game the player had to drive to get to the next one. They are
  // videos now, each about a minute, and nothing has to be clicked through.
  //
  //   • Start here: the main menu, on its own at the top of the list, one
  //     screen per narrated line.
  //   • The rules: the How to Play film, cut into four chapters on its own
  //     scene boundaries (~/cc-howto-video/scripts/cut-rules-chapters.py), so
  //     the picture and the narration are exactly the film's.
  //   • The app: screen recordings of the real game, narrated in the film's
  //     voice (~/cc-howto-video/scripts/tutorials/take-*.js).
  //
  // Every file lives in /video (multiplayer/client/video), served with byte
  // ranges by multiplayer_server.py and rewritten by vercel.json, and named
  // tut-<file>.mp4 / .jpg (poster) / .vtt (captions, off until switched on).
  //
  // Watching one to the end ticks it off. Watch them all for the Osprey.

  const LS = "tut3_done_v1";
  const OSPREY = "/avatars/osprey.png";

  // `key` is what is stored as watched. Four of them are the old tours' keys
  // (menu, game, practice, online): someone who finished that tour already
  // knows what its video shows, so it stays ticked.
  const VIDEOS = [
    { key: "menu",     group: "start", file: "tut-menu",     len: "1:07", title: "The main menu",
      desc: "Your profile, the four ways to play, and every tab down the side." },
    { key: "rules1",   group: "rules", file: "tut-rules-1",  len: "0:59", title: "The goal and setup",
      desc: "What you are building, and how to set up the table." },
    { key: "rules2",   group: "rules", file: "tut-rules-2",  len: "0:59", title: "Cards and turns",
      desc: "What a card tells you, your one action a turn, and where animals go." },
    { key: "rules3",   group: "rules", file: "tut-rules-3",  len: "1:04", title: "Paying and stars",
      desc: "Sand dollars, matching symbols, star abilities, and how cards score." },
    { key: "rules4",   group: "rules", file: "tut-rules-4",  len: "0:58", title: "The Pool and scoring",
      desc: "The Pool, the end game card, and adding up a board." },
    { key: "game",     group: "app",   file: "tut-game",     len: "1:06", title: "Playing a game",
      desc: "Start a game, draw, play an Ocean, pay for it, and fire a star." },
    { key: "online",   group: "app",   file: "tut-online",   len: "0:56", title: "Online play and controls",
      desc: "Private rooms, bot grades, chat, breaks, and card controls." },
    { key: "practice", group: "app",   file: "tut-practice", len: "1:00", title: "Practice: B-Lob",
      desc: "Let Strategy pick a plan, then build the Bird and Lobster combo." },
  ];
  const GROUPS = [
    { id: "start", title: "Start here" },
    { id: "rules", title: "The rules" },
    { id: "app",   title: "Playing on screen" },
  ];
  const KEYS = VIDEOS.map(v => v.key);
  const TOTAL = VIDEOS.length;

  // ── Completion tracking ───────────────────────────────────────────
  function getDone() { try { return JSON.parse(localStorage.getItem(LS) || "{}") || {}; } catch (_) { return {}; } }
  function doneCount() { const d = getDone(); return KEYS.filter(k => d[k]).length; }
  function allDone() { return doneCount() === TOTAL; }
  function setDone(k) {
    const d = getDone();
    if (d[k]) return false;
    d[k] = true;
    try { localStorage.setItem(LS, JSON.stringify(d)); } catch (_) {}
    grantOspreyIfComplete();
    return true;
  }
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
  const src = (v, ext) => `/video/${v.file}.${ext}`;
  const PLAY_ICON = '<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true"><path d="M8 5.5v13l10.5-6.5z" fill="currentColor"/></svg>';
  const CHECK_ICON = '<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path d="M5 12.5l4.2 4.2L19 7" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/></svg>';

  // ════════════════════════════════════════════════════════════════
  //  STYLES (lobby vibe: light frosted cyan)
  // ════════════════════════════════════════════════════════════════
  const css = `
  #tut3-chooser { position:fixed; inset:0; z-index:100040; display:none; align-items:center; justify-content:center; padding:22px 14px; background:rgba(6,26,46,.55); backdrop-filter:blur(4px); -webkit-backdrop-filter:blur(4px); overflow:auto; }
  #tut3-chooser.open { display:flex; }
  .tut3-card { width:100%; max-width:760px; margin:auto; background:linear-gradient(165deg,#fbfeff 0%,#eaf6ff 55%,#e3f0fb 100%); border:1px solid rgba(120,200,235,.6); border-radius:24px; box-shadow:0 24px 70px rgba(10,60,110,.45); padding:22px 22px 18px; font-family:"Nunito",sans-serif; color:#0f3a5e; }
  /* As wide as the window allows, but never so tall that the buttons under
     the video fall off a short laptop screen: width follows from the height. */
  .tut3-card.tut3-watching { max-width:min(1040px, calc((100vh - 190px) * 16 / 9 + 44px)); max-width:min(1040px, calc((100dvh - 190px) * 16 / 9 + 44px)); }
  .tut3-hd { text-align:center; margin-bottom:4px; }
  .tut3-hd h2 { font-family:"Cinzel",serif; font-size:1.5rem; font-weight:900; color:#1769b0; margin:0; letter-spacing:.4px; }
  .tut3-hd p { font-size:.86rem; color:#4f7ba6; margin:5px 0 0; }
  .tut3-group { margin-top:14px; }
  .tut3-group-hd { font-size:.72rem; font-weight:900; letter-spacing:1.2px; text-transform:uppercase; color:#4f7ba6; margin:0 2px 8px; }
  .tut3-grid { display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); gap:10px; }
  .tut3-vid { display:flex; align-items:center; gap:12px; text-align:left; background:#fff; border:1.5px solid rgba(130,200,235,.55); border-radius:14px; padding:8px; cursor:pointer; font-family:inherit; color:inherit; transition:transform .12s, box-shadow .12s, border-color .12s; }
  .tut3-vid:hover, .tut3-vid:focus-visible { transform:translateY(-2px); box-shadow:0 8px 24px rgba(30,120,190,.2); border-color:#3aa8e0; outline:none; }
  .tut3-thumb { position:relative; flex-shrink:0; width:128px; aspect-ratio:16/9; border-radius:9px; overflow:hidden; background:#cfe6f5; }
  .tut3-thumb img { width:100%; height:100%; object-fit:cover; display:block; }
  .tut3-thumb .tut3-play { position:absolute; inset:0; display:flex; align-items:center; justify-content:center; color:#fff; background:rgba(6,40,70,.18); }
  .tut3-thumb .tut3-play svg { filter:drop-shadow(0 2px 4px rgba(0,0,0,.5)); }
  .tut3-len { position:absolute; right:5px; bottom:5px; font-size:.66rem; font-weight:800; color:#fff; background:rgba(6,26,46,.78); border-radius:5px; padding:1px 5px; }
  .tut3-vid-body { flex:1; min-width:0; }
  .tut3-vid-title { display:block; font-weight:900; font-size:.98rem; color:#0f4d86; line-height:1.2; }
  .tut3-vid-desc { display:block; font-size:.76rem; color:#5a83a8; margin-top:3px; line-height:1.3; }
  .tut3-vid-done { display:inline-flex; align-items:center; gap:4px; margin-top:5px; font-size:.7rem; font-weight:800; color:#fff; background:#1fbb8a; border-radius:999px; padding:2px 8px 2px 6px; }
  .tut3-foot { text-align:center; margin-top:16px; padding-top:12px; border-top:1px solid rgba(120,190,225,.35); }
  .tut3-foot .tut3-reward { display:flex; align-items:center; justify-content:center; gap:9px; font-size:.86rem; font-weight:800; color:#1769b0; flex-wrap:wrap; }
  .tut3-foot .tut3-reward img { width:30px; height:30px; object-fit:contain; }
  .tut3-prog { display:flex; gap:5px; justify-content:center; margin-top:8px; }
  .tut3-pip { width:26px; height:6px; border-radius:3px; background:#d6e6f1; }
  .tut3-pip.on { background:linear-gradient(90deg,#2ea8ea,#22d8c8); }
  .tut3-close { display:block; margin:8px auto 0; background:none; border:none; color:#6a92b5; font-size:.84rem; font-weight:700; cursor:pointer; font-family:inherit; }
  .tut3-close:hover { color:#1769b0; }

  /* The player */
  .tut3-player-hd { display:flex; align-items:center; gap:10px; margin-bottom:12px; }
  .tut3-player-hd h3 { flex:1; min-width:0; margin:0; font-family:"Cinzel",serif; font-size:1.2rem; font-weight:900; color:#1769b0; }
  .tut3-player-hd .tut3-count { font-size:.74rem; font-weight:800; color:#4f7ba6; white-space:nowrap; }
  .tut3-x { flex-shrink:0; width:36px; height:36px; border-radius:50%; border:1px solid rgba(120,190,225,.6); background:#fff; color:#1769b0; font-size:1.2rem; line-height:1; cursor:pointer; }
  .tut3-x:hover { background:#eaf6ff; }
  .tut3-frame { position:relative; width:100%; aspect-ratio:16/9; background:#061a2e; border-radius:14px; overflow:hidden; box-shadow:0 10px 30px rgba(10,60,110,.3); }
  .tut3-frame video { width:100%; height:100%; display:block; background:#061a2e; }
  .tut3-player-ft { display:flex; align-items:center; gap:10px; margin-top:12px; flex-wrap:wrap; }
  .tut3-player-ft .tut3-desc { flex:1; min-width:200px; font-size:.84rem; color:#4f7ba6; line-height:1.35; }
  .tut3-btn { font-family:inherit; font-weight:800; font-size:.86rem; border-radius:999px; padding:9px 16px; cursor:pointer; border:1.5px solid rgba(130,200,235,.8); background:#fff; color:#1769b0; }
  .tut3-btn:hover { border-color:#3aa8e0; }
  .tut3-btn-primary { background:linear-gradient(135deg,#1f9ad7,#22b8c8); border-color:transparent; color:#fff; }
  .tut3-btn-primary.tut3-ready { box-shadow:0 0 0 4px rgba(34,184,200,.25); }

  #tut3-toast { position:fixed; left:50%; bottom:30px; transform:translateX(-50%) translateY(20px); z-index:100060; background:linear-gradient(135deg,#1fbb8a,#159e9e); color:#fff; padding:13px 22px; border-radius:14px; font-family:"Nunito",sans-serif; font-weight:800; box-shadow:0 12px 36px rgba(0,0,0,.35); opacity:0; pointer-events:none; transition:opacity .3s, transform .3s; display:flex; align-items:center; gap:10px; }
  #tut3-toast.show { opacity:1; transform:translateX(-50%) translateY(0); }

  @media (max-width: 640px) {
    .tut3-card { padding:16px 12px 14px; border-radius:18px; }
    .tut3-grid { grid-template-columns:1fr; }
    .tut3-thumb { width:112px; }
    .tut3-player-hd h3 { font-size:1rem; }
  }
  `;
  const styleEl = document.createElement("style");
  styleEl.id = "tut3-styles";
  styleEl.textContent = css;
  document.head.appendChild(styleEl);

  // ════════════════════════════════════════════════════════════════
  //  CHOOSER + PLAYER (one modal, two views)
  // ════════════════════════════════════════════════════════════════
  let chooser = null;
  let playing = null;   // the video element while one is open

  function buildChooser() {
    if (chooser) return chooser;
    chooser = document.createElement("div");
    chooser.id = "tut3-chooser";
    chooser.setAttribute("role", "dialog");
    chooser.setAttribute("aria-modal", "true");
    chooser.setAttribute("aria-label", "How to Play videos");
    document.body.appendChild(chooser);
    chooser.addEventListener("click", (e) => { if (e.target === chooser) closeChooser(); });
    // Capture phase on window: the app has its own Escape handling further in,
    // and this dialog sits on top of all of it.
    window.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && chooser.classList.contains("open")) { e.stopPropagation(); closeChooser(); }
    }, true);
    return chooser;
  }

  function rewardLine() {
    const n = doneCount();
    return `<div class="tut3-reward"><img src="${OSPREY}" alt="Osprey"> Watch all ${TOTAL} to unlock the <span style="color:#0f4d86">Osprey</span> ${allDone() ? "(unlocked)" : `(${n}/${TOTAL})`}</div>`;
  }

  function renderList() {
    stopVideo();
    const done = getDone();
    const c = buildChooser();
    const pips = KEYS.map(k => `<span class="tut3-pip ${done[k] ? "on" : ""}"></span>`).join("");
    const groups = GROUPS.map(g => {
      const items = VIDEOS.filter(v => v.group === g.id).map(v => `
        <button type="button" class="tut3-vid" data-key="${v.key}" aria-label="Watch ${esc(v.title)}, ${esc(v.len)}">
          <span class="tut3-thumb"><img src="${src(v, "jpg")}" alt="" loading="lazy"><span class="tut3-play">${PLAY_ICON}</span><span class="tut3-len">${esc(v.len)}</span></span>
          <span class="tut3-vid-body">
            <span class="tut3-vid-title">${esc(v.title)}</span>
            <span class="tut3-vid-desc">${esc(v.desc)}</span>
            ${done[v.key] ? `<span class="tut3-vid-done">${CHECK_ICON}Watched</span>` : ""}
          </span>
        </button>`).join("");
      return `<div class="tut3-group"><div class="tut3-group-hd">${esc(g.title)}</div><div class="tut3-grid">${items}</div></div>`;
    }).join("");
    c.innerHTML = `<div class="tut3-card">
      <div class="tut3-hd"><h2>How to Play</h2><p>Short videos, about a minute each. Nothing to click through.</p></div>
      ${groups}
      <div class="tut3-foot">
        ${rewardLine()}
        <div class="tut3-prog">${pips}</div>
        <button type="button" class="tut3-close" id="tut3-chooser-close">Maybe later</button>
      </div>
    </div>`;
    c.querySelectorAll(".tut3-vid").forEach(btn => btn.addEventListener("click", () => play(btn.getAttribute("data-key"))));
    c.querySelector("#tut3-chooser-close").addEventListener("click", closeChooser);
  }

  function play(key) {
    const i = VIDEOS.findIndex(v => v.key === key);
    if (i < 0) return;
    stopVideo();
    const v = VIDEOS[i];
    const next = VIDEOS[i + 1] || null;
    const c = buildChooser();
    c.innerHTML = `<div class="tut3-card tut3-watching">
      <div class="tut3-player-hd">
        <h3>${esc(v.title)}</h3>
        <span class="tut3-count">${i + 1} of ${TOTAL}</span>
        <button type="button" class="tut3-x" id="tut3-player-close" aria-label="Close">&times;</button>
      </div>
      <div class="tut3-frame">
        <video id="tut3-video" controls playsinline preload="auto" poster="${src(v, "jpg")}">
          <source src="${src(v, "mp4")}" type="video/mp4">
          <track kind="captions" srclang="en" label="English" src="${src(v, "vtt")}">
        </video>
      </div>
      <div class="tut3-player-ft">
        <div class="tut3-desc">${esc(v.desc)}</div>
        <button type="button" class="tut3-btn" id="tut3-all">All videos</button>
        ${next ? `<button type="button" class="tut3-btn tut3-btn-primary" id="tut3-next">Next: ${esc(next.title)}</button>` : ""}
      </div>
    </div>`;
    c.classList.add("open");
    const vid = c.querySelector("#tut3-video");
    playing = vid;
    let counted = false;
    const finish = () => {
      if (counted) return;
      counted = true;
      if (setDone(v.key)) showToast(`Watched: ${v.title}`, completionSubtitle());
      const nb = c.querySelector("#tut3-next");
      if (nb) nb.classList.add("tut3-ready");
    };
    vid.addEventListener("ended", finish);
    // The last half second is a fade to black: reaching it is reaching the end.
    vid.addEventListener("timeupdate", () => {
      if (vid.duration && vid.currentTime >= vid.duration - 0.75) finish();
    });
    vid.addEventListener("play", () => hushTheme(true));
    vid.addEventListener("pause", () => hushTheme(false));
    vid.addEventListener("ended", () => hushTheme(false));
    c.querySelector("#tut3-player-close").addEventListener("click", closeChooser);
    c.querySelector("#tut3-all").addEventListener("click", renderList);
    const nb = c.querySelector("#tut3-next");
    if (nb) nb.addEventListener("click", () => play(next.key));
    // Opened by a click, so sound is allowed. If the browser still says no,
    // the poster and the play button are there.
    try { const p = vid.play(); if (p && p.catch) p.catch(() => {}); } catch (_) {}
  }

  // The lobby's theme song would play over the narrator. Only the gain moves
  // (window.__ccHushTheme in preview-app.js); the player's volume is untouched.
  function hushTheme(on) { try { window.__ccHushTheme && window.__ccHushTheme(on); } catch (_) {} }

  function stopVideo() {
    if (playing) {
      try { playing.pause(); playing.removeAttribute("src"); playing.querySelectorAll("source").forEach(s => s.remove()); playing.load(); } catch (_) {}
      playing = null;
    }
    hushTheme(false);
  }

  function openChooser() { renderList(); chooser.classList.add("open"); }
  function closeChooser() { stopVideo(); if (chooser) chooser.classList.remove("open"); }

  // ── Completion toast ──────────────────────────────────────────────
  let toastEl = null;
  function completionSubtitle() {
    return allDone() ? `All ${TOTAL} watched. Osprey unlocked!` : `${doneCount()} of ${TOTAL} watched`;
  }
  function showToast(title, sub) {
    if (!toastEl) { toastEl = document.createElement("div"); toastEl.id = "tut3-toast"; document.body.appendChild(toastEl); }
    toastEl.innerHTML = `<span><div>${esc(title)}</div><div style="font-size:.74rem;opacity:.9;font-weight:600">${esc(sub)}</div></span>`;
    toastEl.classList.add("show");
    setTimeout(() => toastEl.classList.remove("show"), 3200);
  }

  // ── Public entry ──────────────────────────────────────────────────
  window.__openTutorialChooser = openChooser;
  window.__tutorialVideos = VIDEOS.map(v => ({ key: v.key, file: v.file, title: v.title, len: v.len }));
})();
