/* Currents & Critters — Tournament Mode UI (self-contained, flag-gated).
 *
 * Loaded on every page but does NOTHING unless window.__ccTourney.ENABLED is
 * true (set in preview-app.js: TOURNAMENTS_PUBLIC, or ?tournaments=1, or
 * localStorage cc_tournaments=1). When disabled it never touches the DOM, so
 * existing modes are completely unaffected.
 *
 * Talks to the server-authoritative tournament API (/api/tournament/*). The
 * server owns winners/advancement/XP; this file only renders state + sends the
 * host/player intents. Original ocean-themed bracket — no third-party assets.
 */
(function () {
  "use strict";

  function bridge() { return window.__ccTourney; }
  if (!bridge() || !bridge().ENABLED) return;   // hard off-switch

  const REDUCED = !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  const $ = (sel, root) => (root || document).querySelector(sel);
  const el = (tag, cls, html) => { const n = document.createElement(tag); if (cls) n.className = cls; if (html != null) n.innerHTML = html; return n; };
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

  // ── live state ──────────────────────────────────────────────────────────
  const T = {
    tid: null, hostToken: "", myToken: "", pid: null,
    es: null, pollTimer: null, state: null, lastVersion: -1,
    screenOpen: false, view: "lobby",     // 'lobby' | 'bracket'
    zoom: 1, panX: 0, panY: 0,
    prevMatchWinners: {},                 // match_number -> winner pid (advancement detection)
    lastPendingSwitchFrom: null,
    dragFrom: null,
    hostSwapSel: null,                    // host tap-to-swap: first-selected pid
  };

  // ── auth helpers ─────────────────────────────────────────────────────────
  async function authBody(extra) {
    const b = bridge(); const body = Object.assign({}, extra || {});
    const tok = await b.idToken();
    if (tok) body.idToken = tok; else body.guest_id = b.guestId();
    return body;
  }
  async function post(path, extra) { const b = bridge(); return b.post(path, await authBody(extra)); }
  function get(path) { return bridge().get(path); }
  function toast(m, t) { bridge().toast(m, t); }

  // Return to the Player Home after a tournament. Drop any stale room id from the
  // URL first (mirrors the in-game leave cleanup) so the home screen lands on the
  // real Player Home — never the removed legacy create/join lobby — and a refresh
  // can't auto-join a finished match room.
  function goHome() {
    try {
      const u = new URL(location.href);
      u.pathname = u.pathname.replace(/\/[A-Z0-9]{5,8}\/?$/, "/") || "/";
      u.searchParams.delete("room");
      u.searchParams.delete("roomId");
      history.replaceState({}, "", u.pathname + (u.search || "") + (u.hash || ""));
    } catch (_) {}
    if (typeof window.__fishShowStatsLobby === "function") window.__fishShowStatsLobby();
  }

  // =========================================================================
  // Styles (ocean theme, light/dark aware, reduced-motion aware)
  // =========================================================================
  function injectStyles() {
    if ($("#cc-tourney-style")) return;
    const s = el("style"); s.id = "cc-tourney-style";
    s.textContent = `
    /* Ocean-surface light theme — matches the "New Current" (Create Game) modal:
       light blue gradient cards, Cinzel titles, white inputs, #2050a0 labels,
       warm-gold primary button. Cohesive with the rest of the game. */
    :root{ --ccT-ink:#0d3070; --ccT-ink2:#2050a0; --ccT-line-c:rgba(120,185,235,.55);
      --ccT-tealtxt:#0c6ab8; --ccT-gold:#f3a712; --ccT-goldsoft:#fbbf58; --ccT-coral:#ff7a59;
      --ccT-ok:#0a9d63; --ccT-warn:#c8720a; --ccT-glass:rgba(255,255,255,.88);
      --ccT-panel:rgba(255,255,255,.62); --ccT-cardgrad:linear-gradient(180deg,#cce6f8 0%,#b4d4ee 55%,#9ec4e4 100%); }
    .ccT-overlay{ position:fixed; inset:0; z-index:9000; display:none; }
    .ccT-overlay.open{ display:block; }
    .ccT-modal{ position:fixed; inset:0; z-index:9100; display:none; align-items:center; justify-content:center;
      background:rgba(10,40,100,.72); backdrop-filter:blur(8px); padding:16px; }
    .ccT-modal.open{ display:flex; }
    .ccT-card{ position:relative; width:min(540px,96vw); max-height:92vh; overflow:auto; border-radius:26px;
      background:var(--ccT-cardgrad); border:1.5px solid rgba(130,195,240,.55);
      color:var(--ccT-ink); box-shadow:0 10px 48px rgba(8,50,130,.22), 0 2px 8px rgba(8,50,130,.10);
      font-family:"Nunito",sans-serif; }
    .ccT-card::before{ content:""; position:absolute; inset:0; pointer-events:none; border-radius:26px;
      background:radial-gradient(ellipse at 25% 82%, rgba(255,255,255,.22) 0%, transparent 55%),
                 radial-gradient(ellipse at 85% 12%, rgba(180,230,255,.20) 0%, transparent 50%); }
    .ccT-card > *{ position:relative; z-index:1; }
    .ccT-card h2{ margin:0; padding:22px 24px 4px; font-family:"Cinzel",serif; font-size:1.5rem; font-weight:700;
      color:#0c3472; letter-spacing:1.2px; }
    .ccT-card .ccT-sub{ padding:0 24px 10px; color:#2b5a97; font-size:.9rem; }
    .ccT-body{ padding:4px 24px 22px; }
    .ccT-field{ margin:14px 0; }
    .ccT-field label{ display:block; font-size:13px; font-weight:700; letter-spacing:.3px; color:var(--ccT-ink2); margin-bottom:6px; }
    .ccT-input, .ccT-select{ width:100%; padding:13px 15px; border-radius:16px; border:1.5px solid rgba(140,200,240,.55);
      background:var(--ccT-glass); color:var(--ccT-ink); font-size:1rem; font-weight:700; font-family:"Nunito",sans-serif;
      box-sizing:border-box; transition:border-color .15s, box-shadow .15s; }
    .ccT-input:focus, .ccT-select:focus{ outline:none; border-color:rgba(50,140,240,.65); box-shadow:0 0 0 3px rgba(50,140,240,.15); }
    input[type=range].ccT-input{ padding:8px 2px; accent-color:var(--ccT-gold); font-weight:400; }
    .ccT-row{ display:flex; gap:12px; flex-wrap:wrap; }
    .ccT-row > *{ flex:1 1 46%; }
    .ccT-fmt-grid{ display:grid; grid-template-columns:repeat(auto-fill,minmax(120px,1fr)); gap:9px; }
    .ccT-fmt{ cursor:pointer; border:1.5px solid rgba(140,200,240,.55); border-radius:16px; padding:11px 8px; text-align:center;
      background:var(--ccT-glass); color:var(--ccT-ink); transition:transform .12s, border-color .12s, box-shadow .12s; }
    .ccT-fmt:hover{ transform:translateY(-2px); border-color:rgba(50,140,240,.6); }
    .ccT-fmt.sel{ border-color:var(--ccT-gold); box-shadow:0 0 0 3px rgba(243,167,18,.22); background:#fff; }
    .ccT-fmt .ccT-fmt-title{ font-weight:800; font-size:.98rem; color:#0c3472; }
    .ccT-fmt .ccT-fmt-mini{ display:flex; gap:3px; justify-content:center; margin:7px 0 5px; }
    .ccT-fmt .ccT-dot{ width:8px; height:8px; border-radius:50%; background:#2f8ce0; opacity:.9; }
    .ccT-fmt .ccT-fmt-meta{ font-size:.72rem; color:#3a6aa5; }
    .ccT-summary{ margin-top:8px; padding:13px 15px; border-radius:16px; background:rgba(255,255,255,.55);
      border:1.5px solid rgba(140,200,240,.5); font-size:.88rem; line-height:1.55; color:#123a70; }
    .ccT-summary b{ color:var(--ccT-tealtxt); }
    .ccT-toggle{ display:flex; align-items:center; gap:10px; cursor:pointer; font-weight:700; color:var(--ccT-ink2); font-size:.95rem; }
    .ccT-toggle input{ width:auto; accent-color:var(--ccT-gold); transform:scale(1.15); }
    .ccT-toggle .ccT-hint{ display:block; font-weight:500; font-size:.78rem; color:#4a72a8; margin-top:2px; }
    /* custom bracket builder */
    .ccT-custom-list{ display:flex; flex-direction:column; gap:7px; }
    .ccT-cm-row{ display:flex; align-items:center; gap:8px; padding:8px 10px; border-radius:14px;
      background:rgba(255,255,255,.6); border:1.5px solid rgba(140,200,240,.5); flex-wrap:wrap; }
    .ccT-cm-label{ font-weight:800; color:#0c3472; font-size:.86rem; min-width:60px; }
    .ccT-cm-step{ width:30px; height:30px; border-radius:9px; border:1.5px solid rgba(50,140,240,.5);
      background:#fff; color:#0c3472; font-size:1.15rem; font-weight:800; cursor:pointer; line-height:1; display:flex; align-items:center; justify-content:center; }
    .ccT-cm-step:hover{ background:#eaf4ff; }
    .ccT-cm-size{ min-width:66px; text-align:center; font-size:.84rem; color:#123a70; }
    .ccT-cm-size b{ color:var(--ccT-tealtxt); }
    .ccT-cm-dots{ display:flex; gap:3px; flex:1; flex-wrap:wrap; }
    .ccT-cm-dots .ccT-dot{ width:8px; height:8px; border-radius:50%; background:#2f8ce0; opacity:.9; }
    .ccT-cm-x{ width:28px; height:28px; border-radius:8px; border:1.5px solid rgba(198,42,78,.35);
      background:#fff; color:#c62a4e; cursor:pointer; font-weight:800; }
    .ccT-cm-x:disabled{ opacity:.3; cursor:not-allowed; }
    .ccT-cm-total{ margin-top:8px; font-size:.85rem; color:#123a70; font-weight:600; }
    /* lobby preview banner + host-draggable slots */
    .ccT-preview-banner{ margin:0 0 10px; padding:8px 13px; border-radius:12px; text-align:center;
      background:rgba(243,167,18,.16); border:1.5px solid rgba(243,167,18,.5); color:#8a5a00; font-weight:700; font-size:.85rem; }
    .ccT-slot.host-draggable{ cursor:grab; }
    .ccT-slot.host-draggable:active{ cursor:grabbing; }
    .ccT-slot.swap-over{ outline:2.5px dashed var(--ccT-gold); outline-offset:1px; border-radius:8px; }
    .ccT-slot.swap-sel{ outline:2.5px solid var(--ccT-gold); outline-offset:1px; border-radius:8px; background:rgba(243,167,18,.14); }
    .ccT-pl.host-draggable{ cursor:grab; }
    .ccT-pl.swap-over{ outline:2.5px dashed var(--ccT-gold); outline-offset:1px; }
    .ccT-pl.swap-sel{ outline:2.5px solid var(--ccT-gold); outline-offset:1px; background:rgba(243,167,18,.14); }
    .ccT-btn{ display:inline-flex; align-items:center; justify-content:center; gap:8px; cursor:pointer;
      border:none; border-radius:16px; padding:13px 18px; font-size:1rem; font-weight:800; color:#0c2858;
      font-family:"Nunito",sans-serif; letter-spacing:.2px;
      background:linear-gradient(100deg,#fbbf58 0%,#fdc964 50%,#fcc55c 100%); box-shadow:0 6px 20px rgba(200,130,10,.28);
      transition:filter .15s, transform .12s, box-shadow .15s; }
    .ccT-btn:hover:not(:disabled){ filter:brightness(1.05); transform:translateY(-1px); }
    .ccT-btn:disabled{ opacity:.5; cursor:not-allowed; box-shadow:none; }
    .ccT-btn.wide{ width:100%; margin-top:12px; }
    .ccT-btn.ghost{ background:rgba(255,255,255,.7); color:#1c4f92; border:1.5px solid rgba(120,185,235,.7); box-shadow:none; }
    .ccT-btn.ghost:hover:not(:disabled){ background:#fff; }
    .ccT-btn.gold{ background:linear-gradient(100deg,#f7b733 0%,#fdc964 100%); }
    .ccT-btn.coral{ background:linear-gradient(120deg,#ff8a6a,#ff6a86); color:#fff; box-shadow:0 6px 20px rgba(255,90,110,.3); }
    .ccT-btn.sm{ padding:8px 13px; font-size:.85rem; border-radius:12px; }

    /* full-screen tournament — light ocean surface */
    #ccT-screen{ position:fixed; inset:0; z-index:9000; display:none; flex-direction:column;
      background:radial-gradient(120% 90% at 50% -10%, #e2f2fc 0%, #bfdcf2 55%, #a3cbe7 100%);
      color:var(--ccT-ink); font-family:"Nunito",sans-serif; }
    #ccT-screen.open{ display:flex; }
    .ccT-head{ display:flex; align-items:center; gap:12px; padding:12px 16px; border-bottom:1.5px solid rgba(120,185,235,.45);
      background:rgba(255,255,255,.55); backdrop-filter:blur(6px); flex-wrap:wrap; }
    .ccT-head .ccT-title{ font-family:"Cinzel",serif; font-size:1.15rem; font-weight:700; color:#0c3472; letter-spacing:.5px; }
    .ccT-head .ccT-code{ font-family:ui-monospace,monospace; background:rgba(47,140,224,.16); color:#12508f; padding:3px 9px; border-radius:8px; letter-spacing:2px; font-weight:700; }
    .ccT-head .ccT-phase{ font-size:.72rem; text-transform:uppercase; letter-spacing:1px; padding:3px 10px; border-radius:20px; background:rgba(47,140,224,.14); color:#1c4f92; font-weight:700; }
    .ccT-spacer{ flex:1; }
    .ccT-tabs{ display:flex; gap:6px; padding:8px 16px 0; background:transparent; }
    .ccT-tab{ cursor:pointer; padding:8px 16px; border-radius:12px 12px 0 0; font-weight:800; font-size:.9rem; color:#3a6aa5; opacity:.7; }
    .ccT-tab.on{ opacity:1; color:#0c3472; background:rgba(255,255,255,.6); }
    .ccT-content{ flex:1; overflow:hidden; position:relative; }

    /* lobby */
    .ccT-lobby{ position:absolute; inset:0; overflow:auto; padding:16px; display:grid; grid-template-columns:1.2fr .8fr; gap:16px; }
    @media(max-width:820px){ .ccT-lobby{ grid-template-columns:1fr; } }
    .ccT-panel{ background:var(--ccT-panel); border:1.5px solid rgba(120,185,235,.5); border-radius:20px; padding:16px; box-shadow:0 4px 18px rgba(8,50,130,.08); }
    .ccT-panel h3{ margin:0 0 10px; font-family:"Cinzel",serif; font-size:1rem; color:#0c3472; letter-spacing:.4px; }
    .ccT-players{ display:flex; flex-direction:column; gap:8px; }
    .ccT-pl{ display:flex; align-items:center; gap:10px; padding:9px 11px; border-radius:14px; background:rgba(255,255,255,.78);
      border:1.5px solid rgba(140,200,240,.5); transition:border-color .12s, box-shadow .12s; }
    .ccT-pl.me{ border-color:var(--ccT-gold); box-shadow:0 0 0 2px rgba(243,167,18,.18); }
    .ccT-pl.bot{ background:rgba(232,242,252,.85); }
    .ccT-pl[draggable=true]{ cursor:grab; }
    .ccT-pl.dragover{ border-color:#2f8ce0; background:rgba(47,140,224,.12); }
    .ccT-av{ width:34px; height:34px; border-radius:50%; object-fit:cover; background:#cfe6f7; flex:none; }
    .ccT-pl .ccT-nm{ flex:1; font-weight:700; color:var(--ccT-ink); }
    .ccT-badge{ font-size:.68rem; padding:2px 8px; border-radius:20px; font-weight:800; letter-spacing:.3px; }
    .ccT-badge.ready{ background:rgba(10,157,99,.16); color:var(--ccT-ok); }
    .ccT-badge.not_ready{ background:rgba(200,114,10,.15); color:var(--ccT-warn); }
    .ccT-badge.playing{ background:rgba(47,140,224,.18); color:#1c6fc0; }
    .ccT-badge.waiting{ background:rgba(120,150,190,.16); color:#3a6aa5; }
    .ccT-badge.eliminated{ background:rgba(220,60,90,.14); color:#c62a4e; }
    .ccT-badge.champion{ background:rgba(243,167,18,.2); color:#b9760a; }
    .ccT-badge.bot{ background:rgba(90,120,170,.16); color:#42618f; }
    .ccT-badge.spectating,.ccT-badge.disconnected{ background:rgba(120,150,190,.14); color:#5a7bb0; }
    .ccT-host-dot{ color:var(--ccT-gold); }

    /* bracket */
    .ccT-bracket-wrap{ position:absolute; inset:0; overflow:hidden; cursor:grab; }
    .ccT-bracket-wrap.grabbing{ cursor:grabbing; }
    .ccT-bracket{ position:absolute; top:0; left:0; transform-origin:0 0; display:flex; gap:64px; padding:40px; align-items:center; }
    .ccT-round{ display:flex; flex-direction:column; justify-content:center; gap:18px; min-width:190px; }
    .ccT-round-label{ text-align:center; font-size:.8rem; letter-spacing:1.5px; text-transform:uppercase; color:#3a6aa5; font-weight:700; margin-bottom:4px; }
    .ccT-match{ position:relative; background:rgba(255,255,255,.85); border:1.5px solid rgba(140,200,240,.6); border-radius:14px;
      padding:6px; min-width:180px; box-shadow:0 3px 12px rgba(8,50,130,.08); }
    .ccT-match.mine{ border-color:var(--ccT-gold); box-shadow:0 0 0 2px rgba(243,167,18,.22); }
    .ccT-match.active{ border-color:#2f8ce0; box-shadow:0 0 16px rgba(47,140,224,.3); }
    .ccT-match.complete{ opacity:.97; }
    .ccT-match .ccT-mnum{ position:absolute; top:-9px; left:8px; font-size:.62rem; background:#2f8ce0; color:#fff; padding:1px 7px; border-radius:8px; }
    .ccT-match .ccT-mstatus{ position:absolute; top:-9px; right:8px; font-size:.6rem; padding:1px 7px; border-radius:8px; text-transform:uppercase; letter-spacing:.5px; background:rgba(47,140,224,.16); color:#1c6fc0; }
    .ccT-slot{ display:flex; align-items:center; gap:7px; padding:5px 7px; border-radius:9px; margin:3px 0; background:rgba(230,242,252,.85); }
    .ccT-slot.win{ background:linear-gradient(90deg,rgba(243,167,18,.24),rgba(243,167,18,.06)); }
    .ccT-slot.win .ccT-snm{ color:#b9760a; font-weight:800; }
    .ccT-slot .ccT-sav{ width:22px; height:22px; border-radius:50%; object-fit:cover; background:#cfe6f7; flex:none; }
    .ccT-slot .ccT-snm{ flex:1; font-size:.82rem; font-weight:600; color:var(--ccT-ink); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .ccT-slot .ccT-ssc{ font-size:.72rem; color:#3a6aa5; }
    .ccT-slot.empty .ccT-snm{ opacity:.45; font-style:italic; font-weight:500; }
    svg.ccT-lines{ position:absolute; top:0; left:0; pointer-events:none; overflow:visible; }
    .ccT-line{ stroke:rgba(90,150,215,.5); stroke-width:2; fill:none; }
    .ccT-line.lit{ stroke:var(--ccT-gold); stroke-width:3; }

    /* advancement flyer */
    .ccT-flyer{ position:fixed; z-index:9500; display:flex; align-items:center; gap:6px; padding:5px 10px; border-radius:20px;
      background:linear-gradient(120deg,var(--ccT-goldsoft),#f7b733); color:#5a3800; font-weight:800; box-shadow:0 10px 30px rgba(243,167,18,.45); pointer-events:none; }
    .ccT-flyer img{ width:22px; height:22px; border-radius:50%; }

    /* zoom controls */
    .ccT-zoom{ position:absolute; right:14px; bottom:14px; display:flex; flex-direction:column; gap:6px; z-index:20; }
    .ccT-zoom button{ width:40px; height:40px; border-radius:12px; border:1.5px solid rgba(120,185,235,.7);
      background:rgba(255,255,255,.85); color:#1c4f92; font-size:1.1rem; font-weight:800; cursor:pointer; }

    /* browse open tournaments */
    .ccT-list{ display:flex; flex-direction:column; gap:9px; margin:4px 0 6px; }
    .ccT-literow{ display:flex; align-items:center; gap:10px; padding:11px 13px; border-radius:16px; background:var(--ccT-glass);
      border:1.5px solid rgba(140,200,240,.55); }
    .ccT-literow .ccT-linm{ flex:1; min-width:0; }
    .ccT-literow .ccT-lit-title{ font-weight:800; color:#0c3472; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .ccT-literow .ccT-lit-meta{ font-size:.78rem; color:#3a6aa5; }
    .ccT-empty{ text-align:center; color:#3a6aa5; padding:18px 8px; font-weight:600; }

    /* end / champion overlays reuse .ccT-modal */
    .ccT-champ{ text-align:center; }
    .ccT-champ .ccT-crown{ font-size:3rem; }
    .ccT-xp-list{ text-align:left; margin:12px auto; max-width:340px; }
    .ccT-xp-row{ display:flex; justify-content:space-between; padding:5px 0; border-bottom:1px dashed rgba(120,160,210,.35); }
    .ccT-xp-row b{ color:var(--ccT-tealtxt); }
    .ccT-podium{ display:flex; justify-content:center; align-items:flex-end; gap:10px; margin:14px 0; }
    .ccT-pod{ background:rgba(255,255,255,.6); border:1.5px solid rgba(140,200,240,.5); border-radius:14px 14px 0 0; padding:10px 12px 8px; text-align:center; min-width:80px; color:var(--ccT-ink); }
    .ccT-pod.first{ height:120px; background:linear-gradient(180deg,rgba(243,167,18,.32),rgba(255,255,255,.2)); }
    .ccT-pod.second{ height:96px; } .ccT-pod.third{ height:76px; }

    .ccT-anncbar{ background:linear-gradient(90deg,rgba(243,167,18,.2),rgba(255,255,255,.15)); border-left:3px solid var(--ccT-gold);
      padding:8px 14px; margin:8px 16px; border-radius:10px; font-size:.9rem; color:#12508f; font-weight:600; }
    @media (prefers-reduced-motion: reduce){ .ccT-fmt,.ccT-flyer,.ccT-btn{ transition:none!important; } }
    `;
    document.head.appendChild(s);
  }

  // =========================================================================
  // CREATE overlay
  // =========================================================================
  let createState = { capacity: 8, ppm: 2, formats: [], summary: null, thirdPlace: false,
                      custom: false, customSizes: [2, 2, 2, 2] };

  function openCreate() {
    injectStyles();
    let modal = $("#ccT-create");
    if (!modal) { modal = el("div", "ccT-modal"); modal.id = "ccT-create"; document.body.appendChild(modal); }
    modal.innerHTML = `
      <div class="ccT-card">
        <h2>🏆 New Tournament</h2>
        <div class="ccT-sub">Bracket-style tournament — 4 to 32 players, your choice of match size.</div>
        <div class="ccT-body">
          <div class="ccT-field"><label>Tournament name</label>
            <input class="ccT-input" id="ccT-name" maxlength="40" placeholder="Currents Cup" value="Currents Cup"></div>
          <div class="ccT-field"><label class="ccT-toggle"><input type="checkbox" id="ccT-custom-toggle">
            <span>🧩 Custom bracket<span class="ccT-hint">Design each opening match yourself — e.g. a 3-player, then a 4-player, then a 2-player match.</span></span></label></div>
          <div id="ccT-uniform">
            <div class="ccT-row">
              <div class="ccT-field"><label>Total players: <b id="ccT-cap-val">8</b></label>
                <input class="ccT-input" id="ccT-cap" type="range" min="4" max="32" value="8"></div>
            </div>
            <div class="ccT-field"><label>Match format (players per match)</label>
              <div class="ccT-fmt-grid" id="ccT-fmts"></div></div>
          </div>
          <div id="ccT-custom" style="display:none">
            <div class="ccT-field"><label>Opening matches — set each match's size (2–8 players)</label>
              <div id="ccT-custom-list" class="ccT-custom-list"></div>
              <button class="ccT-btn sm ghost" id="ccT-custom-add" type="button" style="margin-top:6px">＋ Add match</button>
            </div>
          </div>
          <div class="ccT-summary" id="ccT-preview">…</div>
          <div class="ccT-row" style="margin-top:12px">
            <div class="ccT-field"><label>Visibility</label>
              <select class="ccT-select" id="ccT-vis"><option value="public">🌊 Public</option><option value="private">🔒 Private</option></select></div>
            <div class="ccT-field" id="ccT-pw-field" style="display:none"><label>Join code</label>
              <input class="ccT-input" id="ccT-pw" maxlength="12" placeholder="code"></div>
          </div>
          <div class="ccT-field"><label class="ccT-toggle"><input type="checkbox" id="ccT-fill-bots">
            <span>🤖 Fill empty seats with bots<span class="ccT-hint">Start any time — bots take every open spot so a full bracket runs even solo.</span></span></label></div>
          <div class="ccT-field"><label class="ccT-toggle"><input type="checkbox" id="ccT-spec" checked> Allow spectators</label></div>
          <div class="ccT-field"><label class="ccT-toggle"><input type="checkbox" id="ccT-third"> Play a 3rd-place match for the bronze</label></div>
          <div id="ccT-create-err" style="color:#c62a4e;min-height:18px;font-size:.85rem"></div>
          <button class="ccT-btn wide gold" id="ccT-create-go">Create Tournament →</button>
          <button class="ccT-btn ghost wide" id="ccT-create-browse">🔍 Browse open tournaments</button>
          <button class="ccT-btn ghost wide" id="ccT-create-cancel">Cancel</button>
        </div>
      </div>`;
    modal.classList.add("open");
    $("#ccT-create-browse", modal).addEventListener("click", () => { modal.classList.remove("open"); openBrowse(); });
    const capEl = $("#ccT-cap", modal), capVal = $("#ccT-cap-val", modal);
    capEl.addEventListener("input", () => { createState.capacity = +capEl.value; capVal.textContent = capEl.value; refreshFormats(); });
    $("#ccT-vis", modal).addEventListener("change", (e) => { $("#ccT-pw-field", modal).style.display = e.target.value === "private" ? "" : "none"; });
    $("#ccT-third", modal).addEventListener("change", (e) => { createState.thirdPlace = e.target.checked; refreshPreview(); });
    $("#ccT-custom-toggle", modal).addEventListener("change", (e) => { createState.custom = e.target.checked; toggleCustomMode(modal); });
    $("#ccT-custom-add", modal).addEventListener("click", addCustomMatch);
    $("#ccT-create-cancel", modal).addEventListener("click", () => modal.classList.remove("open"));
    $("#ccT-create-go", modal).addEventListener("click", submitCreate);
    // Sync the fresh DOM to any persisted createState (the modal is rebuilt each
    // open, but createState survives), then populate the uniform format tiles.
    $("#ccT-custom-toggle", modal).checked = createState.custom;
    toggleCustomMode(modal);
    refreshFormats();
  }

  // ── Custom bracket builder ────────────────────────────────────────────────
  function toggleCustomMode(modal) {
    const uni = $("#ccT-uniform", modal), cus = $("#ccT-custom", modal);
    if (uni) uni.style.display = createState.custom ? "none" : "";
    if (cus) cus.style.display = createState.custom ? "" : "none";
    if (createState.custom) renderCustomBuilder();
    refreshPreview();
  }
  function customTotal() { return createState.customSizes.reduce((a, b) => a + b, 0); }
  function addCustomMatch() {
    if (createState.customSizes.length >= 16) return;      // 16 opening matches is plenty
    if (customTotal() + 2 > 32) { toast("A tournament holds at most 32 players.", "warn"); return; }
    createState.customSizes.push(2);
    renderCustomBuilder(); refreshPreview();
  }
  function removeCustomMatch(i) {
    if (createState.customSizes.length <= 1) return;
    createState.customSizes.splice(i, 1);
    renderCustomBuilder(); refreshPreview();
  }
  function changeCustomSize(i, delta) {
    const cur = createState.customSizes[i] || 2;
    const next = clamp(cur + delta, 2, 8);
    if (next === cur) return;
    if (delta > 0 && customTotal() + delta > 32) { toast("A tournament holds at most 32 players.", "warn"); return; }
    createState.customSizes[i] = next;
    renderCustomBuilder(); refreshPreview();
  }
  function renderCustomBuilder() {
    const list = $("#ccT-custom-list"); if (!list) return;
    list.innerHTML = "";
    createState.customSizes.forEach((sz, i) => {
      const row = el("div", "ccT-cm-row");
      const dots = Array.from({ length: sz }, () => `<span class="ccT-dot"></span>`).join("");
      row.innerHTML = `<span class="ccT-cm-label">Match ${i + 1}</span>
        <button class="ccT-cm-step" data-act="dec" type="button" aria-label="fewer">－</button>
        <span class="ccT-cm-size"><b>${sz}</b> player${sz === 1 ? "" : "s"}</span>
        <button class="ccT-cm-step" data-act="inc" type="button" aria-label="more">＋</button>
        <span class="ccT-cm-dots">${dots}</span>
        <button class="ccT-cm-x" data-act="rm" type="button" aria-label="remove match"${createState.customSizes.length <= 1 ? " disabled" : ""}>✕</button>`;
      row.querySelector('[data-act="dec"]').addEventListener("click", () => changeCustomSize(i, -1));
      row.querySelector('[data-act="inc"]').addEventListener("click", () => changeCustomSize(i, +1));
      row.querySelector('[data-act="rm"]').addEventListener("click", () => removeCustomMatch(i));
      list.appendChild(row);
    });
    const total = customTotal();
    const foot = el("div", "ccT-cm-total");
    const bad = total < 4 || total > 32;
    foot.innerHTML = `Total players: <b style="color:${bad ? "#c62a4e" : "#1c6b2e"}">${total}</b>${bad ? " (need 4–32)" : ""} · ${createState.customSizes.length} opening match${createState.customSizes.length === 1 ? "" : "es"}`;
    list.appendChild(foot);
  }

  async function refreshFormats() {
    const cap = createState.capacity;
    let formats = [];
    try { const r = await get(`/api/tournament/formats?capacity=${cap}`); formats = (r.data && r.data.formats) || []; } catch (_) {}
    createState.formats = formats;
    if (!formats.some(f => f.players_per_match === createState.ppm)) createState.ppm = formats.length ? formats[0].players_per_match : 2;
    const grid = $("#ccT-fmts"); if (!grid) return;
    grid.innerHTML = "";
    formats.forEach(f => {
      const c = el("div", "ccT-fmt" + (f.players_per_match === createState.ppm ? " sel" : ""));
      const dots = Array.from({ length: f.players_per_match }, () => `<span class="ccT-dot"></span>`).join("");
      c.innerHTML = `<div class="ccT-fmt-title">${f.players_per_match} Player</div>
        <div class="ccT-fmt-mini">${dots}</div>
        <div class="ccT-fmt-meta">${f.num_rounds} rounds · ${f.num_opening_matches} opening${f.num_byes ? " · " + f.num_byes + " bye" + (f.num_byes > 1 ? "s" : "") : ""}</div>`;
      c.addEventListener("click", () => { createState.ppm = f.players_per_match; refreshFormats(); });
      grid.appendChild(c);
    });
    refreshPreview();
  }

  async function refreshPreview() {
    const box = $("#ccT-preview"); if (!box) return;
    let url;
    if (createState.custom) {
      const total = customTotal();
      if (total < 4 || total > 32) { box.innerHTML = `Add matches until the total is <b>4–32</b> players (currently <b>${total}</b>).`; return; }
      url = `/api/tournament/preview?opening_sizes=${createState.customSizes.join(",")}&third_place=${createState.thirdPlace ? 1 : 0}`;
    } else {
      url = `/api/tournament/preview?capacity=${createState.capacity}&players_per_match=${createState.ppm}&third_place=${createState.thirdPlace ? 1 : 0}`;
    }
    try {
      const r = await get(url);
      const s = r.data && r.data.summary;
      if (!s) { box.textContent = (r.data && r.data.error) || "…"; return; }
      const shape = (s.is_custom && s.opening_sizes) ? `Opening matches: <b>${s.opening_sizes.join(" · ")}</b> players. ` : "";
      box.innerHTML = `${shape}<b>${s.tournament_size}</b>-player bracket · <b>${s.num_rounds}</b> rounds ·
        <b>${s.num_opening_matches}</b> opening matches · <b>${s.playable_matches}</b> games total
        ${s.num_byes ? "· <b>" + s.num_byes + "</b> bye" + (s.num_byes > 1 ? "s" : "") : "· no byes"}.
        Winner of each match advances until <b>1 champion</b> remains.
        ${s.third_place_match ? " A 3rd-place match decides the bronze." : ""}`;
    } catch (_) { box.textContent = "Preview unavailable."; }
  }

  async function submitCreate() {
    const modal = $("#ccT-create"); const errEl = $("#ccT-create-err", modal);
    errEl.textContent = "";
    if (createState.custom) {
      const total = customTotal();
      if (total < 4 || total > 32) { errEl.textContent = `Custom bracket needs 4–32 players total (currently ${total}).`; return; }
      if (createState.customSizes.some(s => s < 2 || s > 8)) { errEl.textContent = "Each match must have 2–8 players."; return; }
    }
    const body = {
      name: ($("#ccT-name", modal).value || "Currents Cup").trim(),
      third_place_match: $("#ccT-third", modal).checked,
      visibility: $("#ccT-vis", modal).value,
      password: $("#ccT-pw", modal).value || undefined,
      spectators_allowed: $("#ccT-spec", modal).checked,
      fill_bots: $("#ccT-fill-bots", modal).checked,
      host_name: bridge().nickname(),
      avatar: bridge().myAvatar(),
    };
    if (createState.custom) {
      body.opening_sizes = createState.customSizes.slice();
    } else {
      body.total_capacity = createState.capacity;
      body.players_per_match = createState.ppm;
    }
    const go = $("#ccT-create-go", modal); go.disabled = true; go.textContent = "Creating…";
    try {
      const r = await post("/api/tournament/create", body);
      if (!r.data || !r.data.ok) { errEl.textContent = (r.data && r.data.error) || "Create failed."; go.disabled = false; go.textContent = "Create Tournament →"; return; }
      T.tid = r.data.tournament_id; T.hostToken = r.data.host_token; T.myToken = r.data.token; T.pid = r.data.pid;
      persistActive();
      modal.classList.remove("open");
      openScreen();
    } catch (e) { errEl.textContent = "Network error."; go.disabled = false; go.textContent = "Create Tournament →"; }
  }

  // Join by code (handles private tournaments' join-code prompt)
  async function promptJoin(preCode, pw) {
    injectStyles();
    const code = (preCode || window.prompt("Enter tournament code:") || "").trim().toUpperCase();
    if (!code) return;
    const body = { id: code, player_name: bridge().nickname(), avatar: bridge().myAvatar() };
    if (pw) body.password = pw;
    let r;
    try { r = await post("/api/tournament/join", body); } catch (_) { toast("Network error joining.", "err"); return; }
    if (r.data && r.data.error === "wrong password") {
      const p = window.prompt("This tournament is private — enter its join code:");
      if (p) return promptJoin(code, p);
      return;
    }
    if (!r.data || !r.data.ok) { toast((r.data && r.data.error) || "Could not join.", "err"); return; }
    T.tid = code; T.myToken = r.data.token || ""; T.pid = r.data.pid || null; T.hostToken = "";
    persistActive();
    openScreen();
  }

  function persistActive() { try { localStorage.setItem("cc_active_tournament", JSON.stringify({ tid: T.tid, hostToken: T.hostToken, pid: T.pid })); } catch (_) {} }
  function clearActive() { try { localStorage.removeItem("cc_active_tournament"); } catch (_) {} }

  // =========================================================================
  // BROWSE & JOIN — pick an open tournament from a list, or enter a code.
  // Reachable from the create modal and the Leaderboard "🏅 Tournaments" tab.
  // =========================================================================
  async function openBrowse() {
    injectStyles();
    let modal = $("#ccT-browse");
    if (!modal) { modal = el("div", "ccT-modal"); modal.id = "ccT-browse"; document.body.appendChild(modal); }
    modal.innerHTML = `
      <div class="ccT-card">
        <h2>🏆 Tournaments</h2>
        <div class="ccT-sub">Jump into an open bracket, or enter a code to join.</div>
        <div class="ccT-body">
          <div class="ccT-field"><label>Open tournaments</label>
            <div class="ccT-list" id="ccT-browse-list"><div class="ccT-empty">Loading…</div></div></div>
          <div class="ccT-field"><label>Join by code</label>
            <div class="ccT-row" style="gap:8px">
              <input class="ccT-input" id="ccT-browse-code" maxlength="12" placeholder="ABC123" style="flex:2 1 60%;text-transform:uppercase;letter-spacing:2px">
              <button class="ccT-btn" id="ccT-browse-go" style="flex:1 1 30%">Join →</button>
            </div></div>
          <button class="ccT-btn gold wide" id="ccT-browse-new">＋ Create a Tournament</button>
          <button class="ccT-btn ghost wide" id="ccT-browse-close">Close</button>
        </div>
      </div>`;
    modal.classList.add("open");
    modal.addEventListener("click", (e) => { if (e.target === modal) modal.classList.remove("open"); });
    $("#ccT-browse-close", modal).addEventListener("click", () => modal.classList.remove("open"));
    $("#ccT-browse-new", modal).addEventListener("click", () => { modal.classList.remove("open"); openCreate(); });
    const goJoin = () => {
      const code = ($("#ccT-browse-code", modal).value || "").trim().toUpperCase();
      if (code) { modal.classList.remove("open"); promptJoin(code); }
    };
    $("#ccT-browse-go", modal).addEventListener("click", goJoin);
    $("#ccT-browse-code", modal).addEventListener("keydown", (e) => { if (e.key === "Enter") goJoin(); });
    await refreshBrowseList(modal);
  }

  async function refreshBrowseList(modal) {
    const list = $("#ccT-browse-list", modal); if (!list) return;
    let rows = [];
    try { const r = await get("/api/tournament/list"); rows = (r.data && r.data.tournaments) || []; } catch (_) {}
    const open = rows.filter(t => t.phase === "lobby" && t.joined < t.capacity);
    if (!open.length) {
      list.innerHTML = `<div class="ccT-empty">No open tournaments right now — create one below!</div>`;
      return;
    }
    list.innerHTML = "";
    open.forEach(t => {
      const row = el("div", "ccT-literow");
      const lock = t.has_password ? "🔒 " : "";
      row.innerHTML = `<div class="ccT-linm">
          <div class="ccT-lit-title">${lock}${esc(t.name || "Tournament")}</div>
          <div class="ccT-lit-meta">${t.joined}/${t.capacity} players · ${t.players_per_match} per match · <b>${esc(t.tournament_id)}</b></div>
        </div>
        <button class="ccT-btn sm">Join →</button>`;
      row.querySelector("button").addEventListener("click", () => { modal.classList.remove("open"); promptJoin(t.tournament_id); });
      list.appendChild(row);
    });
  }

  // =========================================================================
  // FULL-SCREEN tournament
  // =========================================================================
  function ensureScreen() {
    let scr = $("#ccT-screen");
    if (scr) return scr;
    injectStyles();
    scr = el("div"); scr.id = "ccT-screen";
    scr.innerHTML = `
      <div class="ccT-head">
        <span class="ccT-title" id="ccT-h-name">Tournament</span>
        <span class="ccT-code" id="ccT-h-code">------</span>
        <span class="ccT-phase" id="ccT-h-phase">lobby</span>
        <span class="ccT-spacer"></span>
        <button class="ccT-btn sm ghost" id="ccT-h-host" style="display:none">⚙ Host</button>
        <button class="ccT-btn sm ghost" id="ccT-h-spectate">👁 Spectate</button>
        <button class="ccT-btn sm coral" id="ccT-h-leave">Leave</button>
      </div>
      <div class="ccT-anncbar" id="ccT-annc" style="display:none"></div>
      <div class="ccT-tabs">
        <div class="ccT-tab on" data-view="lobby" id="ccT-tab-lobby">Lobby</div>
        <div class="ccT-tab" data-view="bracket" id="ccT-tab-bracket">Bracket</div>
      </div>
      <div class="ccT-content" id="ccT-content"></div>`;
    document.body.appendChild(scr);
    scr.querySelectorAll(".ccT-tab").forEach(t => t.addEventListener("click", () => setView(t.dataset.view)));
    $("#ccT-h-leave", scr).addEventListener("click", onLeave);
    $("#ccT-h-spectate", scr).addEventListener("click", () => setView("bracket"));
    $("#ccT-h-host", scr).addEventListener("click", openHostPanel);
    return scr;
  }

  function openScreen() {
    hideReturnPill();
    const scr = ensureScreen();
    scr.classList.add("open");
    T.screenOpen = true;
    startSync();
  }
  function closeScreen() {
    const scr = $("#ccT-screen"); if (scr) scr.classList.remove("open");
    T.screenOpen = false; stopSync();
  }
  function setView(v) {
    T.view = v;
    document.querySelectorAll("#ccT-screen .ccT-tab").forEach(t => t.classList.toggle("on", t.dataset.view === v));
    render();
  }

  async function onLeave() {
    if (T.state && T.state.viewer && T.state.viewer.in_tournament &&
        (T.state.phase === "running") && T.state.viewer.status !== "eliminated" && T.state.viewer.status !== "champion") {
      if (!window.confirm("Leaving now forfeits your spot in the tournament. Continue?")) return;
    }
    try { await post("/api/tournament/leave", { id: T.tid }); } catch (_) {}
    clearActive(); closeScreen();
    goHome();
  }

  // ── live sync (SSE + poll fallback) ──────────────────────────────────────
  function streamUrl() {
    const base = (window.__FISH_API_BASE__ || "").replace(/\/+$/, "");
    const q = `id=${encodeURIComponent(T.tid)}&pid=${encodeURIComponent(T.pid || "")}&host_token=${encodeURIComponent(T.hostToken || "")}`;
    return `${base}/api/tournament/stream?${q}`;
  }
  function startSync() {
    stopSync();
    try {
      T.es = new EventSource(streamUrl());
      T.es.addEventListener("state", (e) => { try { applyState(JSON.parse(e.data)); } catch (_) {} });
      T.es.onerror = () => { /* fall back to poll below */ };
    } catch (_) { T.es = null; }
    // poll fallback (also covers browsers without EventSource cross-origin)
    T.pollTimer = setInterval(pollOnce, 2500);
    pollOnce();
  }
  function stopSync() {
    if (T.es) { try { T.es.close(); } catch (_) {} T.es = null; }
    if (T.pollTimer) { clearInterval(T.pollTimer); T.pollTimer = null; }
  }
  async function pollOnce() {
    if (!T.tid) return;
    try {
      const r = await get(`/api/tournament/state?id=${encodeURIComponent(T.tid)}&pid=${encodeURIComponent(T.pid || "")}&host_token=${encodeURIComponent(T.hostToken || "")}`);
      if (r.data && r.data.ok) applyState(r.data.state);
    } catch (_) {}
  }
  function applyState(st) {
    if (!st || typeof st.version !== "number") return;
    if (st.version === T.lastVersion && T.state) return;
    const prev = T.state;
    T.lastVersion = st.version; T.state = st;
    detectAdvancement(prev, st);
    detectSwitchRequest(st);
    render();
  }

  // =========================================================================
  // RENDER
  // =========================================================================
  function render() {
    const scr = $("#ccT-screen"); if (!scr || !T.state) return;
    const st = T.state;
    $("#ccT-h-name", scr).textContent = st.name || "Tournament";
    $("#ccT-h-code", scr).textContent = st.tournament_id || T.tid;
    $("#ccT-h-phase", scr).textContent = st.phase;
    $("#ccT-h-host", scr).style.display = st.viewer && st.viewer.is_host ? "" : "none";
    const annc = $("#ccT-annc", scr);
    if (st.announcement) { annc.style.display = ""; annc.textContent = "📣 " + st.announcement; } else annc.style.display = "none";
    // auto-switch to bracket once running
    if (st.phase !== "lobby" && T.view === "lobby" && !st._userChoseLobby) T.view = "bracket";
    document.querySelectorAll("#ccT-screen .ccT-tab").forEach(t => t.classList.toggle("on", t.dataset.view === T.view));
    $("#ccT-tab-lobby", scr).style.display = st.phase === "lobby" ? "" : "none";
    const brTab = $("#ccT-tab-bracket", scr);
    if (brTab) brTab.textContent = st.phase === "lobby" ? "Bracket Preview" : "Bracket";
    const content = $("#ccT-content", scr);
    if (T.view === "lobby" && st.phase === "lobby") renderLobby(content);
    else renderBracket(content);
    maybeMatchPrompt(st);
    maybeEndScreens(st);
  }

  function renderLobby(root) {
    const st = T.state; const me = st.viewer || {};
    root.innerHTML = `<div class="ccT-lobby">
      <div class="ccT-panel">
        <h3>Players — ${st.joined}/${st.capacity}</h3>
        <div class="ccT-players" id="ccT-players"></div>
      </div>
      <div class="ccT-panel">
        <h3>Get ready</h3>
        <div id="ccT-lobby-side"></div>
      </div></div>`;
    const list = $("#ccT-players", root);
    (st.participants || []).forEach(p => list.appendChild(playerRow(p, me)));
    renderLobbySide($("#ccT-lobby-side", root));
  }

  // ── Host seat arranging (direct swap, no consent) ─────────────────────────
  function hostCanArrange() {
    const st = T.state;
    return !!(st && st.phase === "lobby" && st.viewer && st.viewer.is_host);
  }
  async function doHostSwap(a, b) {
    if (!a || !b || a === b) return;
    const r = await post("/api/tournament/host", { id: T.tid, host_token: T.hostToken, cmd: "swap", pid: a, pid_b: b });
    if (!(r.data && r.data.ok)) toast((r.data && r.data.error) || "Swap failed", "err");
  }
  function clearSwapSelUI() { document.querySelectorAll(".swap-sel").forEach(n => n.classList.remove("swap-sel")); }
  // Tap-to-swap (works on desktop + mobile): tap one player, then tap another.
  function hostSwapSelect(pid, name) {
    if (!pid) return;
    if (T.hostSwapSel && T.hostSwapSel !== pid) {
      const a = T.hostSwapSel; T.hostSwapSel = null; clearSwapSelUI();
      doHostSwap(a, pid);
      return;
    }
    if (T.hostSwapSel === pid) { T.hostSwapSel = null; clearSwapSelUI(); return; }
    T.hostSwapSel = pid; clearSwapSelUI();
    document.querySelectorAll(`[data-swap-pid="${cssAttr(pid)}"]`).forEach(n => n.classList.add("swap-sel"));
    toast(`Now tap another player to swap them with ${name || "this player"}.`, "info");
  }
  function cssAttr(s) { return String(s).replace(/["\\]/g, "\\$&"); }
  // Wire a lobby row or bracket slot so the HOST can drag it onto — or tap-select
  // then tap — another to directly swap the two players' bracket positions.
  function wireHostSwapTarget(node, pid, name) {
    if (!pid) return;
    node.dataset.swapPid = pid;
    node.classList.add("host-draggable");
    if (T.hostSwapSel === pid) node.classList.add("swap-sel");
    node.draggable = true;
    // Grabbing a swap target must not also start the bracket's mouse-pan.
    node.addEventListener("mousedown", (e) => e.stopPropagation());
    node.addEventListener("dragstart", (e) => { T.dragFrom = pid; try { e.dataTransfer.setData("text/plain", pid); } catch (_) {} });
    node.addEventListener("dragend", () => { T.dragFrom = null; });
    node.addEventListener("dragover", (e) => { if (T.dragFrom && T.dragFrom !== pid) { e.preventDefault(); node.classList.add("swap-over"); } });
    node.addEventListener("dragleave", () => node.classList.remove("swap-over"));
    node.addEventListener("drop", (e) => {
      e.preventDefault(); e.stopPropagation(); node.classList.remove("swap-over");
      const from = T.dragFrom; T.dragFrom = null;
      if (from && from !== pid) doHostSwap(from, pid);
    });
    node.style.cursor = "pointer";
    node.addEventListener("click", (e) => { e.stopPropagation(); hostSwapSelect(pid, name); });
  }

  function playerRow(p, me) {
    const isHost = !!(me && me.is_host);
    const lobby = T.state.phase === "lobby";
    const row = el("div", "ccT-pl" + (p.me ? " me" : "") + (p.is_bot ? " bot" : ""));
    row.dataset.pid = p.pid;
    const botBadge = p.is_bot ? `<span class="ccT-badge bot">🤖 Bot</span>` : "";
    row.innerHTML = `<img class="ccT-av" src="${esc(bridge().avSrc(p.avatar) || "/avatars/mullet.png")}" onerror="this.src='/avatars/mullet.png'">
      <span class="ccT-nm">${esc(p.name)} ${p.is_host ? '<span class="ccT-host-dot" title="Host">★</span>' : ""}</span>
      ${botBadge}<span class="ccT-badge ${esc(p.status)}">${statusLabel(p)}</span>`;
    // avatar async upgrade (skip bots — their avatar is already assigned)
    if (!p.is_bot) bridge().avatarForNick(p.name).then(u => { if (u) { const img = row.querySelector(".ccT-av"); if (img) img.src = bridge().avSrc(u); } }).catch(() => {});
    if (lobby && isHost) {
      // Host arranges everyone directly: drag one card onto another, or tap two.
      wireHostSwapTarget(row, p.pid, p.name);
      row.title = "Drag onto another player — or tap two players — to swap their spots";
    } else if (lobby) {
      // Non-host player: drag your OWN card onto another to REQUEST a switch.
      if (p.me) {
        row.draggable = true;
        row.addEventListener("dragstart", () => { T.dragFrom = p.pid; });
        row.addEventListener("dragend", () => { T.dragFrom = null; });
      }
      row.addEventListener("dragover", (e) => { if (T.dragFrom && T.dragFrom !== p.pid) { e.preventDefault(); row.classList.add("dragover"); } });
      row.addEventListener("dragleave", () => row.classList.remove("dragover"));
      row.addEventListener("drop", (e) => {
        e.preventDefault(); row.classList.remove("dragover");
        const from = T.dragFrom; T.dragFrom = null;
        if (from && from !== p.pid) requestSwitch(p.pid, p.name);
      });
      // Mobile parity: native drag doesn't fire from touch, so tap another player's
      // row to request a switch (confirm guards against accidents).
      const touch = ("ontouchstart" in window) || window.CC_IS_MOBILE === true;
      if (touch && !p.me && me && me.in_tournament) {
        row.style.cursor = "pointer";
        row.addEventListener("click", () => {
          if (window.confirm(`Request a position switch with ${p.name}?`)) requestSwitch(p.pid, p.name);
        });
      }
    }
    return row;
  }
  function statusLabel(p) {
    const m = { not_ready: "Not Ready", ready: "Ready", playing: "Playing", waiting: "Waiting", eliminated: "Out", champion: "Champion", spectating: "Spectating", disconnected: "Offline" };
    return (p.ready && p.status === "not_ready") ? "Ready" : (m[p.status] || p.status);
  }

  function renderLobbySide(root) {
    const st = T.state; const me = st.viewer || {};
    const isHost = me.is_host;
    const amReady = me.ready;
    let html = "";
    html += `<button class="ccT-btn wide ${amReady ? "ghost" : ""}" id="ccT-ready">${amReady ? "✓ Ready — tap to unready" : "I'm Ready"}</button>`;
    html += `<button class="ccT-btn wide ghost" id="ccT-view-bracket">👁 View bracket preview</button>`;
    if (isHost) {
      html += `<button class="ccT-btn wide" id="ccT-randomize">🎲 Randomize Seeding</button>`;
      const full = st.joined >= st.capacity;
      if (!full) html += `<button class="ccT-btn wide" id="ccT-fillbots">🤖 Fill ${st.capacity - st.joined} empty seat${st.capacity - st.joined === 1 ? "" : "s"} with bots</button>`;
      html += `<button class="ccT-btn wide gold" id="ccT-start" ${st.can_start ? "" : "disabled"}>Start Tournament →</button>`;
      if (!st.can_start) html += `<div style="font-size:.8rem;color:#3a6aa5;margin-top:6px">${esc(st.can_start_reason || "")}</div>`;
      html += `<button class="ccT-btn wide ghost" id="ccT-open-host">⚙ Host Controls</button>`;
    }
    const shapeLine = st.summary
      ? (st.summary.is_custom && st.summary.opening_sizes
          ? `<br>Custom bracket: opening matches <b>${st.summary.opening_sizes.join(" · ")}</b> players, ${st.summary.num_rounds} rounds.`
          : `<br>Format: <b>${st.config.players_per_match} Player</b> matches, ${st.summary.num_rounds} rounds.`)
      : "";
    html += `<div style="margin-top:14px;font-size:.85rem;color:#2b5a97;line-height:1.6">
      <b>Tip:</b> ${isHost
        ? "drag a player onto another — or tap two players — to swap their bracket spots."
        : "drag your card onto another player to request a seat switch."}
      ${shapeLine}
      <br>Share code: <b>${esc(st.tournament_id)}</b>${st.visibility === "private" ? " (private)" : ""}.</div>`;
    root.innerHTML = html;
    const readyBtn = $("#ccT-ready", root); if (readyBtn) readyBtn.addEventListener("click", () => post("/api/tournament/ready", { id: T.tid, ready: !amReady }));
    const vb = $("#ccT-view-bracket", root); if (vb) vb.addEventListener("click", () => setView("bracket"));
    const rnd = $("#ccT-randomize", root); if (rnd) rnd.addEventListener("click", onRandomize);
    const fb = $("#ccT-fillbots", root); if (fb) fb.addEventListener("click", onFillBots);
    const start = $("#ccT-start", root); if (start) start.addEventListener("click", onStart);
    const oh = $("#ccT-open-host", root); if (oh) oh.addEventListener("click", openHostPanel);
  }

  async function onRandomize() {
    if (!window.confirm("Shuffle everyone into new opening positions? Players can still switch afterwards.")) return;
    const r = await post("/api/tournament/randomize", { id: T.tid, host_token: T.hostToken });
    if (r.data && r.data.ok) { flashShuffle(); } else toast((r.data && r.data.error) || "Randomize failed", "err");
  }
  function flashShuffle() {
    if (REDUCED) return;
    document.querySelectorAll("#ccT-players .ccT-pl").forEach((row, i) => {
      row.animate([{ transform: "translateX(0)" }, { transform: `translateX(${i % 2 ? 12 : -12}px)` }, { transform: "translateX(0)" }], { duration: 420, delay: i * 25 });
    });
  }
  async function onFillBots() {
    const st = T.state;
    const open = st.capacity - st.joined;
    if (open <= 0) return;
    const r = await post("/api/tournament/host", { id: T.tid, host_token: T.hostToken, cmd: "fill_bots" });
    if (r.data && r.data.ok) toast(`Added ${r.data.added || open} bot${(r.data.added || open) === 1 ? "" : "s"} 🤖`, "info");
    else toast((r.data && r.data.error) || "Could not add bots", "err");
  }
  async function onStart() {
    const st = T.state;
    if (!window.confirm(`Start "${st.name}" with ${st.joined} players? No one can join or switch after this.`)) return;
    const r = await post("/api/tournament/start", { id: T.tid, host_token: T.hostToken });
    if (!(r.data && r.data.ok)) toast((r.data && r.data.error) || "Cannot start yet", "err");
  }

  async function requestSwitch(toPid, toName) {
    const r = await post("/api/tournament/switch", { id: T.tid, target_pid: toPid });
    if (r.data && r.data.ok) toast(`Switch request sent to ${toName}.`, "info");
    else toast((r.data && r.data.error) || "Could not request switch", "err");
  }
  function detectSwitchRequest(st) {
    const pend = st.viewer && st.viewer.pending_switch;
    const from = pend && pend.from_pid;
    if (from && from !== T.lastPendingSwitchFrom) {
      T.lastPendingSwitchFrom = from;
      const fromP = (st.participants || []).find(p => p.pid === from);
      showSwitchDialog(fromP ? fromP.name : "A player");
    }
    if (!from) T.lastPendingSwitchFrom = null;
  }
  function showSwitchDialog(fromName) {
    let m = $("#ccT-switch"); if (!m) { m = el("div", "ccT-modal"); m.id = "ccT-switch"; document.body.appendChild(m); }
    m.innerHTML = `<div class="ccT-card" style="width:min(400px,94vw)"><div class="ccT-body" style="text-align:center">
      <div style="font-size:2rem">🔀</div><h2 style="padding:6px 0">Switch request</h2>
      <p><b>${esc(fromName)}</b> wants to switch tournament positions with you.</p>
      <button class="ccT-btn wide gold" id="ccT-sw-yes">Accept Switch</button>
      <button class="ccT-btn wide ghost" id="ccT-sw-no">Decline</button></div></div>`;
    m.classList.add("open");
    $("#ccT-sw-yes", m).addEventListener("click", async () => { m.classList.remove("open"); const r = await post("/api/tournament/switch_respond", { id: T.tid, accept: true }); if (r.data && r.data.ok) toast("Positions switched!", "ok"); });
    $("#ccT-sw-no", m).addEventListener("click", async () => { m.classList.remove("open"); await post("/api/tournament/switch_respond", { id: T.tid, accept: false }); });
  }

  // ── HOST control panel ───────────────────────────────────────────────────
  function openHostPanel() {
    const st = T.state; if (!st || !st.viewer || !st.viewer.is_host) return;
    let m = $("#ccT-host"); if (!m) { m = el("div", "ccT-modal"); m.id = "ccT-host"; document.body.appendChild(m); }
    const running = st.phase === "running" || st.phase === "paused";
    m.innerHTML = `<div class="ccT-card"><h2>⚙ Host Controls</h2><div class="ccT-body">
      <div class="ccT-field"><label>Tournament name</label><input class="ccT-input" id="ccT-hp-name" value="${esc(st.name)}"></div>
      <div class="ccT-row">
        <button class="ccT-btn sm" id="ccT-hp-rename">Rename</button>
        <button class="ccT-btn sm ghost" id="ccT-hp-lock">${st.locked ? "Unlock joins" : "Lock joins"}</button>
        <button class="ccT-btn sm ghost" id="ccT-hp-spec">${st.spectators_allowed ? "Disable spectators" : "Allow spectators"}</button>
      </div>
      <div class="ccT-field" style="margin-top:12px"><label>Announcement to everyone</label>
        <input class="ccT-input" id="ccT-hp-annc" placeholder="Type a message…"></div>
      <button class="ccT-btn sm" id="ccT-hp-annc-go">📣 Send announcement</button>
      ${st.phase === "lobby" ? `<div class="ccT-field" style="margin-top:12px"><label>Remove a player</label><div id="ccT-hp-remove"></div></div>` : ""}
      <div class="ccT-row" style="margin-top:14px">
        ${running ? `<button class="ccT-btn sm ghost" id="ccT-hp-pause">${st.phase === "paused" ? "Resume" : "Pause"}</button>` : ""}
        <button class="ccT-btn sm coral" id="ccT-hp-cancel">Cancel tournament</button>
      </div>
      ${st.host_log ? `<details style="margin-top:12px"><summary style="cursor:pointer;opacity:.7">Host action log</summary>
        <div style="font-size:.75rem;opacity:.7;max-height:120px;overflow:auto;margin-top:6px">${(st.host_log || []).slice().reverse().map(l => `• ${esc(l.action)} ${esc(l.detail || "")}`).join("<br>")}</div></details>` : ""}
      <button class="ccT-btn ghost wide" id="ccT-hp-close">Close</button>
    </div></div>`;
    m.classList.add("open");
    const hc = (id, fn) => { const b = $(id, m); if (b) b.addEventListener("click", fn); };
    hc("#ccT-hp-rename", () => post("/api/tournament/host", { id: T.tid, host_token: T.hostToken, cmd: "rename", name: $("#ccT-hp-name", m).value }));
    hc("#ccT-hp-lock", () => { post("/api/tournament/host", { id: T.tid, host_token: T.hostToken, cmd: "lock", locked: !st.locked }); m.classList.remove("open"); });
    hc("#ccT-hp-spec", () => { post("/api/tournament/host", { id: T.tid, host_token: T.hostToken, cmd: "spectators", spectators_allowed: !st.spectators_allowed }); m.classList.remove("open"); });
    hc("#ccT-hp-annc-go", () => { post("/api/tournament/host", { id: T.tid, host_token: T.hostToken, cmd: "announce", text: $("#ccT-hp-annc", m).value }); toast("Announcement sent", "ok"); });
    hc("#ccT-hp-pause", () => { post("/api/tournament/host", { id: T.tid, host_token: T.hostToken, cmd: "pause", paused: st.phase !== "paused" }); m.classList.remove("open"); });
    hc("#ccT-hp-cancel", () => { if (window.confirm("Cancel the whole tournament for everyone?")) { post("/api/tournament/host", { id: T.tid, host_token: T.hostToken, cmd: "cancel" }); m.classList.remove("open"); } });
    hc("#ccT-hp-close", () => m.classList.remove("open"));
    const rem = $("#ccT-hp-remove", m);
    if (rem) (st.participants || []).filter(p => !p.is_host).forEach(p => {
      const b = el("button", "ccT-btn sm ghost", "✕ " + esc(p.name)); b.style.margin = "3px";
      b.addEventListener("click", () => post("/api/tournament/host", { id: T.tid, host_token: T.hostToken, cmd: "remove", pid: p.pid }));
      rem.appendChild(b);
    });
  }

  // ── BRACKET rendering ─────────────────────────────────────────────────────
  const ROUND_NAMES = (n, i) => {
    const fromEnd = n - 1 - i;
    if (fromEnd === 0) return "Final";
    if (fromEnd === 1) return "Semifinals";
    if (fromEnd === 2) return "Quarterfinals";
    return "Round " + (i + 1);
  };

  function renderBracket(root) {
    const st = T.state; const br = st.bracket;
    if (!br) { root.innerHTML = `<div style="padding:40px;text-align:center;opacity:.7">The bracket appears when the tournament starts.<br>${st.phase === "lobby" ? "Add a few players to preview the bracket…" : ""}</div>`; return; }
    const hostArrange = !!(br.preview && hostCanArrange());
    const banner = br.preview
      ? `<div class="ccT-preview-banner">👁 Preview — this is how the bracket looks right now. Empty spots fill as players join${st.fill_bots ? " (or bots fill them at start)" : ""}.${hostArrange ? " Drag a player onto another — or tap two — to swap their spots." : ""}</div>`
      : "";
    root.innerHTML = `${banner}<div class="ccT-bracket-wrap" id="ccT-bwrap">
        <svg class="ccT-lines" id="ccT-lines"></svg>
        <div class="ccT-bracket" id="ccT-bgrid"></div>
      </div>
      <div class="ccT-zoom">
        <button id="ccT-zin">＋</button><button id="ccT-zout">－</button>
        <button id="ccT-zfit" title="Fit">⤢</button><button id="ccT-zmine" title="My match">◎</button>
      </div>`;
    const grid = $("#ccT-bgrid", root);
    const myPath = computeMyPath(br, st.viewer && st.viewer.pid);
    br.rounds.forEach((round, ri) => {
      const col = el("div", "ccT-round");
      col.appendChild(el("div", "ccT-round-label", ROUND_NAMES(br.n_rounds, ri)));
      round.forEach(m => col.appendChild(matchCard(m, st, myPath, hostArrange)));
      grid.appendChild(col);
    });
    if (br.third_place) {
      const col = el("div", "ccT-round");
      col.appendChild(el("div", "ccT-round-label", "3rd Place"));
      col.appendChild(matchCard(br.third_place, st, myPath, hostArrange));
      grid.appendChild(col);
    }
    wireBracketPanZoom(root);
    drawLines(root, br);   // synchronous (reading offsets forces layout) so lines never flash empty
    requestAnimationFrame(() => { drawLines(root, br); fitBracket(root, true); });
  }

  function matchCard(m, st, myPath, hostArrange) {
    const mine = (m.players || []).some(p => p && st.viewer && p.pid === st.viewer.pid);
    const card = el("div", "ccT-match " + (m.status === "active" || m.status === "ready" ? "active " : "") + (m.status === "complete" ? "complete " : "") + (mine ? "mine " : ""));
    card.dataset.mnum = m.match_number;
    card.id = "ccT-m-" + m.match_number;
    const statusTxt = { pending: "", ready: "ready", active: "live", complete: "done", bye: "bye" }[m.status] || "";
    let slots = "";
    (m.players || []).forEach((p, si) => {
      const isWin = m.winner && p && p.pid === m.winner.pid;
      const sc = m.scores && p ? m.scores[p.pid] : null;
      slots += `<div class="ccT-slot ${p ? "" : "empty"} ${isWin ? "win" : ""}" data-slot="${si}">
        ${p ? `<img class="ccT-sav" src="${esc(bridge().avSrc(p.avatar) || "/avatars/mullet.png")}" onerror="this.src='/avatars/mullet.png'">` : `<span class="ccT-sav"></span>`}
        <span class="ccT-snm">${p ? esc(p.name) : "—"}</span>
        ${sc != null ? `<span class="ccT-ssc">${sc}</span>` : ""}</div>`;
    });
    card.innerHTML = `<span class="ccT-mnum">${m.is_third_place ? "3rd" : "M" + m.match_number}</span>
      ${statusTxt ? `<span class="ccT-mstatus" style="background:${m.status === "active" || m.status === "ready" ? "rgba(47,140,224,.22)" : m.status === "complete" ? "rgba(243,167,18,.28)" : "rgba(120,150,190,.16)"}">${statusTxt}</span>` : ""}
      ${slots}`;
    // Host arranging: in the lobby PREVIEW, opening-round slots that hold a player
    // can be dragged/tapped to swap two players' bracket positions.
    if (hostArrange && m.round_index === 0) {
      card.querySelectorAll(".ccT-slot[data-slot]").forEach(slotEl => {
        const p = (m.players || [])[+slotEl.dataset.slot];
        if (p && p.pid) wireHostSwapTarget(slotEl, p.pid, p.name);
      });
    }
    // §10 View Match: click a live match to enter (if it's yours) or spectate it.
    if (m.room_id && (m.status === "active" || m.status === "ready")) {
      card.style.cursor = "pointer";
      card.title = mine ? "Enter your match" : "Watch this match";
      card.addEventListener("click", (e) => {
        e.stopPropagation();
        if (mine && st.viewer && st.viewer.my_match) enterMatch(st.viewer.my_match);
        else if (!mine && st.spectators_allowed !== false) spectateMatch(m.room_id);
      });
    }
    return card;
  }

  function computeMyPath(br, pid) {
    const set = new Set();
    if (!pid) return set;
    br.rounds.forEach(round => round.forEach(m => { if ((m.players || []).some(p => p && p.pid === pid)) set.add(m.match_number); }));
    return set;
  }

  function drawLines(root, br) {
    const svg = $("#ccT-lines", root), grid = $("#ccT-bgrid", root); if (!svg || !grid) return;
    svg.innerHTML = ""; svg.setAttribute("width", grid.scrollWidth); svg.setAttribute("height", grid.scrollHeight);
    const rectOf = (mnum) => { const c = $("#ccT-m-" + mnum, root); if (!c) return null; return { x: c.offsetLeft, y: c.offsetTop, w: c.offsetWidth, h: c.offsetHeight }; };
    br.rounds.forEach((round, ri) => {
      if (ri >= br.rounds.length - 1) return;
      round.forEach(m => {
        (m.feeds || []).forEach(f => {
          if (!f) return; const nextNum = matchNumberAt(br, f[0], f[1]); if (nextNum == null) return;
          const a = rectOf(m.match_number), b = rectOf(nextNum); if (!a || !b) return;
          const x1 = a.x + a.w, y1 = a.y + a.h / 2, x2 = b.x, y2 = b.y + b.h / 2, mx = (x1 + x2) / 2;
          const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
          p.setAttribute("d", `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`);
          p.setAttribute("class", "ccT-line" + (m.winner ? " lit" : ""));
          p.dataset.from = m.match_number;
          svg.appendChild(p);
        });
      });
    });
  }
  function matchNumberAt(br, ri, mi) { try { return br.rounds[ri][mi].match_number; } catch (_) { return null; } }

  // pan + zoom + pinch
  function wireBracketPanZoom(root) {
    const wrap = $("#ccT-bwrap", root), grid = $("#ccT-bgrid", root);
    const apply = () => { grid.style.transform = `translate(${T.panX}px,${T.panY}px) scale(${T.zoom})`; const svg = $("#ccT-lines", root); if (svg) svg.style.transform = grid.style.transform; };
    let dragging = false, sx = 0, sy = 0, px = 0, py = 0;
    wrap.addEventListener("mousedown", (e) => { dragging = true; wrap.classList.add("grabbing"); sx = e.clientX; sy = e.clientY; px = T.panX; py = T.panY; });
    window.addEventListener("mousemove", (e) => { if (!dragging) return; T.panX = px + (e.clientX - sx); T.panY = py + (e.clientY - sy); apply(); });
    window.addEventListener("mouseup", () => { dragging = false; wrap.classList.remove("grabbing"); });
    wrap.addEventListener("wheel", (e) => { e.preventDefault(); T.zoom = clamp(T.zoom * (e.deltaY < 0 ? 1.1 : 0.9), 0.3, 2.2); apply(); }, { passive: false });
    // touch pinch + pan
    let pinchDist = 0, touchStart = null;
    wrap.addEventListener("touchstart", (e) => {
      if (e.touches.length === 2) pinchDist = touchDist(e);
      else if (e.touches.length === 1) touchStart = { x: e.touches[0].clientX, y: e.touches[0].clientY, px: T.panX, py: T.panY };
    }, { passive: true });
    wrap.addEventListener("touchmove", (e) => {
      if (e.touches.length === 2) { const d = touchDist(e); if (pinchDist) { T.zoom = clamp(T.zoom * d / pinchDist, 0.3, 2.2); pinchDist = d; apply(); } }
      else if (e.touches.length === 1 && touchStart) { T.panX = touchStart.px + (e.touches[0].clientX - touchStart.x); T.panY = touchStart.py + (e.touches[0].clientY - touchStart.y); apply(); }
    }, { passive: true });
    $("#ccT-zin", root).addEventListener("click", () => { T.zoom = clamp(T.zoom * 1.2, 0.3, 2.2); apply(); });
    $("#ccT-zout", root).addEventListener("click", () => { T.zoom = clamp(T.zoom / 1.2, 0.3, 2.2); apply(); });
    $("#ccT-zfit", root).addEventListener("click", () => fitBracket(root, true));
    $("#ccT-zmine", root).addEventListener("click", () => centerOnMine(root));
    apply();
  }
  function touchDist(e) { const a = e.touches[0], b = e.touches[1]; return Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY); }
  function fitBracket(root, reset) {
    const wrap = $("#ccT-bwrap", root), grid = $("#ccT-bgrid", root); if (!wrap || !grid) return;
    const sw = grid.scrollWidth, sh = grid.scrollHeight, cw = wrap.clientWidth, ch = wrap.clientHeight;
    if (!sw || !sh) return;
    T.zoom = clamp(Math.min(cw / sw, ch / sh) * 0.96, 0.25, 1.2);
    T.panX = Math.max(0, (cw - sw * T.zoom) / 2); T.panY = Math.max(0, (ch - sh * T.zoom) / 2);
    grid.style.transform = `translate(${T.panX}px,${T.panY}px) scale(${T.zoom})`;
    const svg = $("#ccT-lines", root); if (svg) svg.style.transform = grid.style.transform;
  }
  function centerOnMine(root) {
    const st = T.state; if (!st || !st.viewer) return;
    // root IS #ccT-content, so scope to it directly (the old "#ccT-content …"
    // descendant selector never matched). Prefer my own match, any state.
    const mine = root.querySelector(".ccT-match.mine") || root.querySelector(".ccT-match.active");
    if (!mine) return;
    const wrap = $("#ccT-bwrap", root);
    T.zoom = clamp(T.zoom < 0.7 ? 0.9 : T.zoom, 0.5, 1.4);
    T.panX = wrap.clientWidth / 2 - (mine.offsetLeft + mine.offsetWidth / 2) * T.zoom;
    T.panY = wrap.clientHeight / 2 - (mine.offsetTop + mine.offsetHeight / 2) * T.zoom;
    const grid = $("#ccT-bgrid", root); grid.style.transform = `translate(${T.panX}px,${T.panY}px) scale(${T.zoom})`;
    const svg = $("#ccT-lines", root); if (svg) svg.style.transform = grid.style.transform;
  }

  // ── advancement animation (§7) ─────────────────────────────────────────────
  function detectAdvancement(prev, st) {
    if (!prev || !prev.bracket || !st.bracket || REDUCED) { snapshotWinners(st); return; }
    const newlyWon = [];
    st.bracket.rounds.forEach(round => round.forEach(m => {
      if (m.winner && T.prevMatchWinners[m.match_number] == null) newlyWon.push(m);
    }));
    snapshotWinners(st);
    if (T.view === "bracket") newlyWon.forEach((m, i) => setTimeout(() => animateAdvance(m), i * 300));
  }
  function snapshotWinners(st) {
    T.prevMatchWinners = {};
    if (st.bracket) st.bracket.rounds.forEach(round => round.forEach(m => { if (m.winner) T.prevMatchWinners[m.match_number] = m.winner.pid; }));
  }
  function animateAdvance(m) {
    const root = $("#ccT-content"); if (!root || !m.winner) return;
    const fromCard = $("#ccT-m-" + m.match_number, root);
    const nextNum = m.feeds && m.feeds[0] ? matchNumberAt(T.state.bracket, m.feeds[0][0], m.feeds[0][1]) : null;
    const toCard = nextNum ? $("#ccT-m-" + nextNum, root) : null;
    if (fromCard) fromCard.animate([{ boxShadow: "0 0 0 rgba(255,207,92,0)" }, { boxShadow: "0 0 26px rgba(255,207,92,.9)" }, { boxShadow: "0 0 0 rgba(255,207,92,0)" }], { duration: 900 });
    // light the connecting line
    document.querySelectorAll(`#ccT-lines path[data-from="${m.match_number}"]`).forEach(p => { p.classList.add("lit"); });
    if (!fromCard || !toCard) return;
    const a = fromCard.getBoundingClientRect(), b = toCard.getBoundingClientRect();
    const flyer = el("div", "ccT-flyer");
    flyer.innerHTML = `<img src="${esc(bridge().avSrc(winnerAvatar(m)) || "/avatars/mullet.png")}" onerror="this.src='/avatars/mullet.png'"><span>${esc(m.winner.name)}</span>`;
    document.body.appendChild(flyer);
    flyer.style.left = a.right - 20 + "px"; flyer.style.top = a.top + a.height / 2 - 14 + "px";
    const anim = flyer.animate([
      { left: a.right - 20 + "px", top: a.top + a.height / 2 - 14 + "px", opacity: 1 },
      { left: b.left + "px", top: b.top + b.height / 2 - 14 + "px", opacity: 1 },
      { left: b.left + "px", top: b.top + b.height / 2 - 14 + "px", opacity: 0 },
    ], { duration: 1100, easing: "cubic-bezier(.5,0,.3,1)" });
    anim.onfinish = () => { flyer.remove(); bubbleBurst(b.left, b.top + b.height / 2); };
  }
  function winnerAvatar(m) { const p = (m.players || []).find(x => x && m.winner && x.pid === m.winner.pid); return p ? p.avatar : ""; }
  function bubbleBurst(x, y) {
    if (REDUCED) return;
    for (let i = 0; i < 8; i++) {
      const bub = el("div"); bub.style.cssText = `position:fixed;left:${x}px;top:${y}px;width:6px;height:6px;border-radius:50%;background:#2f8ce0;z-index:9500;pointer-events:none;opacity:.8`;
      document.body.appendChild(bub);
      const ang = Math.random() * 6.28, dist = 20 + Math.random() * 30;
      bub.animate([{ transform: "translate(0,0)", opacity: .9 }, { transform: `translate(${Math.cos(ang) * dist}px,${Math.sin(ang) * dist - 20}px)`, opacity: 0 }], { duration: 700 + Math.random() * 400 }).onfinish = () => bub.remove();
    }
  }

  // ── match entry + end screens ──────────────────────────────────────────────
  let _lastMatchRoom = null, _lastEndShownFor = null;
  function maybeMatchPrompt(st) {
    const mm = st.viewer && st.viewer.my_match;
    if (mm && mm.room_id && (mm.status === "active" || mm.status === "ready") && mm.room_id !== _lastMatchRoom) {
      _lastMatchRoom = mm.room_id;
      showMatchReady(mm);
    }
    if (!mm) _lastMatchRoom = null;
  }
  function showMatchReady(mm) {
    let m = $("#ccT-matchready"); if (!m) { m = el("div", "ccT-modal"); m.id = "ccT-matchready"; document.body.appendChild(m); }
    m.innerHTML = `<div class="ccT-card" style="width:min(420px,94vw)"><div class="ccT-body" style="text-align:center">
      <div style="font-size:2.4rem">🌊</div><h2>Your match is ready!</h2>
      <p>Match ${mm.match_number} · Round ${mm.round_index + 1}. Only your assigned opponents are in this game.</p>
      <button class="ccT-btn wide gold" id="ccT-enter">▶ Enter Your Match</button>
      <button class="ccT-btn wide ghost" id="ccT-enter-later">View bracket instead</button></div></div>`;
    m.classList.add("open");
    $("#ccT-enter", m).addEventListener("click", () => { m.classList.remove("open"); enterMatch(mm); });
    $("#ccT-enter-later", m).addEventListener("click", () => { m.classList.remove("open"); setView("bracket"); });
  }
  function enterMatch(mm) {
    // The match runs as a normal private GameRoom. Each player has a pre-claimed
    // seat; the server gave us its seat_token so we reconnect straight into it.
    const roomId = (mm && mm.room_id) || mm;
    const seatTok = mm && mm.seat_token;
    persistActive();
    // Tell the server we've arrived (may trigger immediate auto-start once all in).
    post("/api/tournament/entered", { id: T.tid }).catch(() => {});
    // Deep-link into the room (the SPA loads the game table inline). We hand the
    // seat_token to the game via the URL (?seat_token=…) because that is the ONLY
    // place the game app's boot looks for it: it reads params.get("seat_token")
    // and calls setSeatToken(), which stores it into the tab-scoped sessionStorage
    // the app actually reads. Writing it to localStorage["fish_room_<ROOM>_seat_token"]
    // here landed in a key nothing ever reads, so the app fell through to the
    // seat-picker and reported the (fully pre-claimed) match room as "full".
    // ?t=<tid> lets us restore the tournament screen when the match ends.
    let url = "/play/" + encodeURIComponent(roomId) + "?t=" + encodeURIComponent(T.tid);
    if (seatTok) url += "&seat_token=" + encodeURIComponent(seatTok);
    try { window.location.href = url; }
    catch (_) { toast("Open match room " + roomId, "info"); }
  }

  function maybeEndScreens(st) {
    const me = st.viewer || {};
    // champion / tournament complete
    if (st.phase === "complete" && _lastEndShownFor !== "complete-" + st.version) {
      if (me.status === "champion") { _lastEndShownFor = "complete-" + st.version; showChampion(st); }
      else if (me.in_tournament && _lastEndShownFor !== "final-" + st.tournament_id) { _lastEndShownFor = "final-" + st.tournament_id; showFinalPlacement(st); }
    }
    // cancelled by host — don't strand clients on a dead screen
    if (st.phase === "cancelled" && _lastEndShownFor !== "cancelled-" + st.tournament_id) {
      _lastEndShownFor = "cancelled-" + st.tournament_id;
      showCancelled(st);
    }
  }

  function showCancelled(st) {
    let m = $("#ccT-cancelled"); if (!m) { m = el("div", "ccT-modal"); m.id = "ccT-cancelled"; document.body.appendChild(m); }
    m.innerHTML = `<div class="ccT-card ccT-champ"><div class="ccT-body">
      <div style="font-size:2.4rem">🌊</div><h2>Tournament Cancelled</h2>
      <p><b>${esc(st.name || "The tournament")}</b> was cancelled by the host.</p>
      <button class="ccT-btn wide ghost" id="ccT-cancelled-home">Back to Home</button></div></div>`;
    m.classList.add("open");
    $("#ccT-cancelled-home", m).addEventListener("click", () => { m.classList.remove("open"); clearActive(); closeScreen(); goHome(); });
  }

  function showChampion(st) {
    let m = $("#ccT-champ"); if (!m) { m = el("div", "ccT-modal"); m.id = "ccT-champ"; document.body.appendChild(m); }
    const xp = me_xp(st);
    m.innerHTML = `<div class="ccT-card ccT-champ"><div class="ccT-body">
      <div class="ccT-crown">👑</div><h2>Tournament Champion!</h2>
      <p>You won <b>${esc(st.name)}</b>.</p>
      ${podiumHtml(st)}
      ${xpHtml(xp)}
      <button class="ccT-btn wide gold" id="ccT-champ-bracket">View Final Bracket</button>
      <button class="ccT-btn wide ghost" id="ccT-champ-home">Back to Home</button></div></div>`;
    m.classList.add("open");
    if (!REDUCED) championConfetti();
    $("#ccT-champ-bracket", m).addEventListener("click", () => { m.classList.remove("open"); setView("bracket"); });
    $("#ccT-champ-home", m).addEventListener("click", () => { m.classList.remove("open"); clearActive(); closeScreen(); goHome(); });
  }
  function showFinalPlacement(st) {
    let m = $("#ccT-final"); if (!m) { m = el("div", "ccT-modal"); m.id = "ccT-final"; document.body.appendChild(m); }
    const place = st.viewer.final_place; const xp = me_xp(st);
    m.innerHTML = `<div class="ccT-card ccT-champ"><div class="ccT-body">
      <div style="font-size:2.4rem">${place === 2 ? "🥈" : place === 3 ? "🥉" : "🌊"}</div>
      <h2>${ordinal(place)} Place</h2><p>${esc(st.name)} is complete.</p>
      ${podiumHtml(st)} ${xpHtml(xp)}
      <button class="ccT-btn wide gold" id="ccT-final-bracket">View Final Bracket</button>
      <button class="ccT-btn wide ghost" id="ccT-final-home">Back to Home</button></div></div>`;
    m.classList.add("open");
    $("#ccT-final-bracket", m).addEventListener("click", () => { m.classList.remove("open"); setView("bracket"); });
    $("#ccT-final-home", m).addEventListener("click", () => { m.classList.remove("open"); clearActive(); closeScreen(); goHome(); });
  }
  function me_xp(st) { return st.viewer && st.viewer.xp; }
  function xpHtml(xp) {
    if (!xp || !xp.items || !xp.items.length) return "";
    const labels = { champion: "Champion", second: "Runner-up", third: "3rd place", fourth: "4th place", reached_final: "Reached final", reached_semifinal: "Reached semifinal", match_win: "Match wins", no_quit: "No-quit bonus", participation: "Participation" };
    return `<div class="ccT-xp-list">${xp.items.map(it => `<div class="ccT-xp-row"><span>${labels[it.kind] || it.kind}</span><b>+${it.amount} XP</b></div>`).join("")}
      <div class="ccT-xp-row" style="border:none"><span><b>Total tournament XP</b></span><b>+${xp.total_xp} XP</b></div></div>`;
  }
  function podiumHtml(st) {
    const p = st.final_placements || []; const nm = (pl) => { const e = p.find(x => x.place === pl); const part = (st.participants || []).find(x => x.pid === (e && e.player_id)); return part ? part.name : "—"; };
    return `<div class="ccT-podium">
      <div class="ccT-pod second"><div>🥈</div><div style="font-weight:700">${esc(nm(2))}</div><div style="opacity:.7;font-size:.8rem">2nd</div></div>
      <div class="ccT-pod first"><div>🥇</div><div style="font-weight:800">${esc(nm(1))}</div><div style="opacity:.7;font-size:.8rem">1st</div></div>
      <div class="ccT-pod third"><div>🥉</div><div style="font-weight:700">${esc(nm(3))}</div><div style="opacity:.7;font-size:.8rem">3rd</div></div></div>`;
  }
  function ordinal(n) { if (!n) return "Final"; const s = ["th", "st", "nd", "rd"], v = n % 100; return n + (s[(v - 20) % 10] || s[v] || s[0]); }
  function championConfetti() {
    for (let i = 0; i < 40; i++) {
      const c = el("div"); const col = ["#ffcf5c", "#38d0e0", "#ff7a59", "#39d98a"][i % 4];
      c.style.cssText = `position:fixed;left:${Math.random() * 100}vw;top:-20px;width:9px;height:9px;background:${col};z-index:9600;pointer-events:none;border-radius:2px`;
      document.body.appendChild(c);
      c.animate([{ transform: "translateY(0) rotate(0)", opacity: 1 }, { transform: `translateY(${window.innerHeight + 40}px) rotate(${720 + Math.random() * 360}deg)`, opacity: .9 }], { duration: 2200 + Math.random() * 1500, easing: "cubic-bezier(.3,.7,.5,1)" }).onfinish = () => c.remove();
    }
  }

  // =========================================================================
  // Wire into the app
  // =========================================================================
  function injectMenuOption() {
    const sel = document.getElementById("nc-mode");
    if (sel && !sel.querySelector('option[value="tournament"]')) {
      const o = el("option"); o.value = "tournament"; o.textContent = "🏆 Tournament"; sel.appendChild(o);
    }
  }
  // exposed for preview-app.js's mode-select interceptor + return-to-tournament
  window.__ccTourneyOpenCreate = openCreate;
  window.__ccTourneyBrowse = openBrowse;
  window.__ccTourneyJoin = promptJoin;
  window.__ccTourneyOpenById = function (tid, hostToken, pid) { T.tid = tid; T.hostToken = hostToken || ""; T.pid = pid || null; openScreen(); };

  function showReturnPill(name) {
    if (T.screenOpen) return;
    let pill = $("#ccT-return-pill");
    if (!pill) { pill = el("button"); pill.id = "ccT-return-pill"; pill.className = "ccT-btn gold"; document.body.appendChild(pill); }
    pill.style.cssText = "position:fixed;left:50%;bottom:18px;transform:translateX(-50%);z-index:8000;box-shadow:0 10px 34px rgba(0,0,0,.45)";
    pill.textContent = "🏆 Return to " + (name || "tournament");
    pill.onclick = () => { hideReturnPill(); openScreen(); };
  }
  function hideReturnPill() { const p = $("#ccT-return-pill"); if (p) p.remove(); }

  async function resumeActiveIfAny() {
    let a; try { a = JSON.parse(localStorage.getItem("cc_active_tournament") || "null"); } catch (_) {}
    if (!a || !a.tid) return;
    try {
      const r = await get(`/api/tournament/state?id=${encodeURIComponent(a.tid)}&pid=${encodeURIComponent(a.pid || "")}&host_token=${encodeURIComponent(a.hostToken || "")}`);
      const st = r.data && r.data.state;
      if (!st || st.phase === "cancelled" || !(st.viewer && (st.viewer.in_tournament || st.viewer.status === "spectating"))) {
        clearActive(); return;
      }
      T.tid = a.tid; T.hostToken = a.hostToken || ""; T.pid = a.pid || null;
      // Just came back from playing a match (/play/ROOM?t=<tid>)? Re-open the bracket.
      const qp = new URLSearchParams(location.search);
      if (qp.get("t") === a.tid && !T.screenOpen) { openScreen(); }
      else { showReturnPill(st.name); }
    } catch (_) { /* leave the blob; try again next load */ }
  }

  function handleUrlAutoJoin() {
    const qp = new URLSearchParams(location.search);
    const code = (qp.get("tournament") || qp.get("join_tournament") || "").trim().toUpperCase();
    if (code && /^[A-Z0-9]{4,12}$/.test(code)) promptJoin(code);
  }

  function spectateMatch(roomId) {
    persistActive();
    // Tournament match rooms are full (all seats pre-claimed), so landing here
    // with no seat token yields the read-only spectator view (hands stripped).
    try { window.location.href = "/play/" + encodeURIComponent(roomId) + "?t=" + encodeURIComponent(T.tid) + "&spectate=1"; }
    catch (_) { toast("Watch room " + roomId, "info"); }
  }

  function boot() {
    injectStyles();
    injectMenuOption();
    handleUrlAutoJoin();     // ?tournament=CODE deep-link
    resumeActiveIfAny();     // Return-to-tournament pill / auto-reopen after a match
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();
})();
