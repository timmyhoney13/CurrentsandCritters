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
    readyLobbyMatch: null,                // match_number whose ready-check modal is open
    readyLobbyDismissed: {},              // match_number -> true (player closed the ready check)
    panHandlers: null,                    // window pan listeners, so they can be torn down
    watchTimer: null,                     // background poll (keeps us connected + pulls us into the next round)
    bg: null,                             // last background state snapshot (read while the overlay is closed)
    status: null,                         // what the status chip / waiting bar is currently showing
    outAnnounced: null,                   // tournament id we've already told "you're knocked out"
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
    /* Above #pv-endgame-overlay (9850): a ready check or champion card that opens
       while the end-of-game screen is still up must be VISIBLE, not stacked under it. */
    .ccT-modal{ position:fixed; inset:0; z-index:9880; display:none; align-items:center; justify-content:center;
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
    .ccT-mode-grid{ display:grid; grid-template-columns:repeat(3,1fr); gap:8px; }
    @media(max-width:420px){ .ccT-mode-grid{ grid-template-columns:1fr; } }
    .ccT-mode-grid .ccT-fmt{ padding:10px 8px; }
    .ccT-mode-grid .ccT-fmt-meta{ margin-top:4px; line-height:1.35; }
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
    .ccT-field > .ccT-hint{ display:block; font-weight:500; font-size:.78rem; color:#4a72a8; margin-top:4px; line-height:1.4; }
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

    /* full-screen tournament — light ocean surface. Sits above the end-of-game
       screen (9850) so opening the bracket from a finished match actually shows it. */
    #ccT-screen{ position:fixed; inset:0; z-index:9860; display:none; flex-direction:column;
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
    .ccT-match .ccT-mlabel{ font-size:.66rem; font-weight:800; letter-spacing:.4px; text-transform:uppercase;
      color:#3a6aa5; text-align:center; padding:3px 2px 1px; }
    .ccT-slot{ display:flex; align-items:center; gap:7px; padding:5px 7px; border-radius:9px; margin:3px 0; background:rgba(230,242,252,.85); }
    .ccT-slot.win{ background:linear-gradient(90deg,rgba(243,167,18,.24),rgba(243,167,18,.06)); }
    .ccT-slot.win .ccT-snm{ color:#b9760a; font-weight:800; }
    .ccT-slot .ccT-sav{ width:22px; height:22px; border-radius:50%; object-fit:cover; background:#cfe6f7; flex:none; }
    .ccT-slot .ccT-snm{ flex:1; font-size:.82rem; font-weight:600; color:var(--ccT-ink); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .ccT-slot .ccT-ssc{ font-size:.72rem; color:#3a6aa5; }
    .ccT-slot.empty .ccT-snm{ opacity:.45; font-style:italic; font-weight:500; }
    /* top-N advancement: which place each surviving player finished in */
    .ccT-match .ccT-madv{ font-size:.6rem; font-weight:800; letter-spacing:.4px; text-transform:uppercase;
      color:#b9760a; text-align:center; padding:1px 2px 2px; }
    .ccT-slot .ccT-splace{ font-size:.62rem; font-weight:900; color:#fff; background:#f3a712;
      padding:0 5px; border-radius:7px; flex:none; }
    /* transform-origin MUST match .ccT-bracket. The lines SVG is given the same
       transform as the grid, so a different origin slid every connector away from
       the matches it joins by half the grid size at any zoom but 1. */
    svg.ccT-lines{ position:absolute; top:0; left:0; transform-origin:0 0; pointer-events:none; overflow:visible; }
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

    /* per-match READY CHECK lobby */
    .ccT-mr-count{ font-size:.95rem; font-weight:800; color:var(--ccT-tealtxt); margin:2px 0 10px; }
    .ccT-mr-list{ display:flex; flex-direction:column; gap:7px; margin:0 0 12px; text-align:left; }
    .ccT-mr-row{ display:flex; align-items:center; gap:10px; padding:8px 11px; border-radius:14px;
      background:rgba(255,255,255,.72); border:1.5px solid rgba(140,200,240,.5); }
    .ccT-mr-row.me{ border-color:var(--ccT-gold); box-shadow:0 0 0 2px rgba(243,167,18,.16); }
    .ccT-mr-row.ready{ background:linear-gradient(90deg,rgba(10,157,99,.14),rgba(255,255,255,.5)); }
    .ccT-mr-row .ccT-av{ width:30px; height:30px; }
    .ccT-mr-nm{ flex:1; font-weight:700; color:var(--ccT-ink); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .ccT-mr-badge{ font-size:.72rem; font-weight:800; padding:3px 9px; border-radius:20px; letter-spacing:.2px; }
    .ccT-mr-badge.on{ background:rgba(10,157,99,.16); color:var(--ccT-ok); }
    .ccT-mr-badge.off{ background:rgba(200,114,10,.15); color:var(--ccT-warn); }
    .ccT-mr-badge.gone{ background:rgba(120,150,190,.16); color:#5a7bb0; }
    .ccT-mr-go{ margin-top:10px; font-weight:800; color:var(--ccT-tealtxt); letter-spacing:.5px; }

    /* ── status surface: in-game chip + Player-Home waiting bar ──────────────
       The old "Return to <tournament>" pill floated at bottom-CENTRE, right on
       top of the hand and the action bar — permanently in the way. In a game the
       status now DOCKS into the game's own notice bar; everywhere else it's a
       waiting banner on the Player Home, so it can never cover anything. */
    .ccT-chip{ display:inline-flex; align-items:center; gap:6px; flex:none; max-width:min(46vw,300px);
      padding:3px 11px; border-radius:20px; border:1.5px solid rgba(232,179,74,.75);
      background:linear-gradient(100deg,#fbbf58,#fcc55c); color:#0c2858; font-family:"Nunito",sans-serif;
      font-weight:800; font-size:11.5px; letter-spacing:.2px; line-height:1.5; cursor:pointer;
      white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .ccT-chip:hover{ filter:brightness(1.06); }
    .ccT-chip.waiting{ background:linear-gradient(100deg,#cfe8fb,#b3d9f6); border-color:rgba(70,150,230,.65); color:#0d3070; }
    .ccT-chip.done{ background:linear-gradient(100deg,#d8ecdc,#bfe0c8); border-color:rgba(10,157,99,.55); color:#08503a; }
    #ccT-waitbar{ display:flex; align-items:center; gap:12px; margin:2px 0 16px; padding:12px 16px;
      background:linear-gradient(90deg, rgba(46,140,240,.16), rgba(120,90,220,.14));
      border:1.5px solid rgba(60,150,240,.40); border-radius:16px; box-shadow:0 4px 16px rgba(30,99,184,.14);
      position:relative; z-index:5; flex-wrap:wrap; font-family:"Nunito",sans-serif; }
    #ccT-waitbar .ccT-wb-spin{ width:18px; height:18px; flex:none; border-radius:50%;
      border:3px solid rgba(46,140,240,.25); border-top-color:#2a7fe0; animation:ccT-spin .8s linear infinite; }
    @keyframes ccT-spin{ to{ transform:rotate(360deg); } }
    #ccT-waitbar.nospin .ccT-wb-spin{ display:none; }
    #ccT-waitbar .ccT-wb-txt{ flex:1; min-width:170px; font-weight:800; font-size:.98rem; color:#15539e; letter-spacing:.2px; }
    #ccT-waitbar .ccT-wb-sub{ display:block; font-weight:600; font-size:.8rem; color:#3a6aa5; margin-top:2px; }
    .ccT-wb-btn{ flex:none; border:1.5px solid rgba(60,150,240,.45); background:rgba(255,255,255,.9); color:#15539e;
      font-family:"Nunito",sans-serif; font-weight:800; font-size:.9rem; padding:8px 14px; border-radius:12px;
      cursor:pointer; transition:background .15s, transform .12s, box-shadow .15s; }
    .ccT-wb-btn:hover{ background:#fff; transform:translateY(-1px); box-shadow:0 4px 12px rgba(30,99,184,.2); }
    .ccT-wb-btn:active{ transform:translateY(0); }
    /* live-match picker (Spectate with more than one game running) */
    .ccT-watchrow{ display:flex; align-items:center; gap:10px; padding:10px 12px; border-radius:16px; margin:7px 0;
      background:var(--ccT-glass); border:1.5px solid rgba(140,200,240,.55); text-align:left; }
    .ccT-watchrow .ccT-wr-nm{ flex:1; min-width:0; }
    .ccT-watchrow .ccT-wr-title{ font-weight:800; color:#0c3472; }
    .ccT-watchrow .ccT-wr-meta{ font-size:.8rem; color:#3a6aa5; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }

    @media (prefers-reduced-motion: reduce){ .ccT-fmt,.ccT-flyer,.ccT-btn{ transition:none!important; }
      #ccT-waitbar .ccT-wb-spin{ animation:none; } }
    `;
    document.head.appendChild(s);
  }

  // =========================================================================
  // CREATE overlay
  // =========================================================================
  // mode: 'uniform' (pick a size + format) | 'sizes' (list each opening match's
  // size) | 'design' (the full canvas builder — window.__ccTourneyBuilder).
  // advance = how many finishers get out of every match. 1 is classic single
  // elimination; 2+ makes each match a group whose top N go through.
  let createState = { capacity: 8, ppm: 2, advance: 1, formats: [], summary: null, thirdPlace: false,
                      mode: "uniform", customSizes: [2, 2, 2, 2],
                      design: null, designSummary: null };

  /** The biggest match size that must still knock somebody out — the advancement
   *  ceiling is one less than the SMALLEST match in the bracket. */
  function advanceCeiling() {
    if (createState.mode === "sizes") {
      const sizes = createState.customSizes;
      return Math.max(1, (sizes.length ? Math.min.apply(null, sizes) : 2) - 1);
    }
    return Math.max(1, createState.ppm - 1);
  }

  function refreshAdvance() {
    const field = $("#ccT-adv-field"), sel = $("#ccT-adv"), hint = $("#ccT-adv-hint");
    if (!field || !sel) return;
    // A designed bracket sets advancement per match on the canvas, so a single
    // tournament-wide rule here would be a lie.
    field.style.display = createState.mode === "design" ? "none" : "";
    if (createState.mode === "design") return;
    const max = advanceCeiling();
    createState.advance = clamp(createState.advance, 1, max);
    sel.innerHTML = "";
    for (let a = 1; a <= max; a++) {
      const o = document.createElement("option");
      o.value = String(a);
      o.textContent = a === 1 ? "🥇 Winner only — single elimination"
                              : `🏅 Top ${a} advance to the next round`;
      if (a === createState.advance) o.selected = true;
      sel.appendChild(o);
    }
    sel.value = String(createState.advance);
    sel.disabled = max <= 1;
    if (hint) {
      hint.textContent = max <= 1
        ? "1 v 1 matches can only send the winner through — pick a bigger match size to advance more."
        : createState.advance === 1
          ? "Lose once and you're out."
          : `Each match plays as a group: its best ${createState.advance} carry on, the rest are knocked out.`;
    }
  }

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
          <div class="ccT-field"><label>Bracket</label>
            <div class="ccT-mode-grid" id="ccT-modes">
              <div class="ccT-fmt" data-mode="uniform"><div class="ccT-fmt-title">⚡ Quick</div>
                <div class="ccT-fmt-meta">Pick a size — we build the bracket.</div></div>
              <div class="ccT-fmt" data-mode="sizes"><div class="ccT-fmt-title">🧩 Match sizes</div>
                <div class="ccT-fmt-meta">Set each opening match's size yourself.</div></div>
              <div class="ccT-fmt" data-mode="design"><div class="ccT-fmt-title">🎨 Design it</div>
                <div class="ccT-fmt-meta">Build the whole bracket on a canvas.</div></div>
            </div></div>
          <div id="ccT-design" style="display:none">
            <div class="ccT-field">
              <div id="ccT-design-state" class="ccT-summary">Open the builder to design your bracket.</div>
              <button class="ccT-btn sm gold" id="ccT-design-open" type="button" style="margin-top:8px">🎨 Open the bracket builder</button>
              <button class="ccT-btn sm ghost" id="ccT-design-clear" type="button" style="margin-top:8px;display:none">Discard design</button>
            </div>
          </div>
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
          <div class="ccT-field" id="ccT-adv-field">
            <label>Who advances out of each match</label>
            <select class="ccT-select" id="ccT-adv"></select>
            <div class="ccT-hint" id="ccT-adv-hint"></div>
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
          <div class="ccT-field" id="ccT-third-field"><label class="ccT-toggle"><input type="checkbox" id="ccT-third"> Play a 3rd-place match for the bronze</label></div>
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
    $("#ccT-adv", modal).addEventListener("change", (e) => {
      createState.advance = clamp(+e.target.value || 1, 1, advanceCeiling());
      refreshAdvance();
      // Match sizes that can't manage this rule are re-labelled in the picker.
      if (createState.mode === "uniform") refreshFormats(); else refreshPreview();
    });
    modal.querySelectorAll("#ccT-modes .ccT-fmt").forEach(tile => {
      tile.addEventListener("click", () => { createState.mode = tile.dataset.mode; toggleCustomMode(modal); });
    });
    $("#ccT-design-open", modal).addEventListener("click", openDesigner);
    $("#ccT-design-clear", modal).addEventListener("click", () => {
      createState.design = null; createState.designSummary = null; toggleCustomMode(modal);
    });
    $("#ccT-custom-add", modal).addEventListener("click", addCustomMatch);
    $("#ccT-create-cancel", modal).addEventListener("click", () => modal.classList.remove("open"));
    $("#ccT-create-go", modal).addEventListener("click", submitCreate);
    // Sync the fresh DOM to any persisted createState (the modal is rebuilt each
    // open, but createState survives), then populate the uniform format tiles.
    toggleCustomMode(modal);
    refreshFormats();
  }

  // ── bracket mode: quick / match sizes / full design ───────────────────────
  function toggleCustomMode(modal) {
    modal = modal || $("#ccT-create");
    if (!modal) return;
    const mode = createState.mode;
    const show = (sel, on) => { const n = $(sel, modal); if (n) n.style.display = on ? "" : "none"; };
    show("#ccT-uniform", mode === "uniform");
    show("#ccT-custom", mode === "sizes");
    show("#ccT-design", mode === "design");
    modal.querySelectorAll("#ccT-modes .ccT-fmt").forEach(t =>
      t.classList.toggle("sel", t.dataset.mode === mode));
    // A designed bracket decides 3rd place by its own shape, and its size comes
    // from the canvas — those controls would be lying, so hide them.
    show("#ccT-third-field", mode !== "design");
    if (mode === "sizes") renderCustomBuilder();
    if (mode === "design") renderDesignState();
    refreshAdvance();
    refreshPreview();
  }

  function renderDesignState() {
    const box = $("#ccT-design-state"), clear = $("#ccT-design-clear"), open = $("#ccT-design-open");
    if (!box) return;
    const s = createState.designSummary, d = createState.design;
    if (!d || !d.matches || !d.matches.length) {
      box.innerHTML = "Open the builder to place match boxes, connect the winners, and set what kind of player each spot takes.";
      if (clear) clear.style.display = "none";
      if (open) open.textContent = "🎨 Open the bracket builder";
      return;
    }
    const kinds = s && s.slot_kinds ? s.slot_kinds : null;
    const extra = kinds
      ? ` <span style="opacity:.85">(${["open", "human", "ai", "invite"]
          .filter(k => kinds[k]).map(k => `${kinds[k]} ${{ open: "open", human: "player-only", ai: "AI", invite: "invited" }[k]}`)
          .join(" · ")})</span>` : "";
    box.innerHTML = s
      ? `✓ <b>${s.tournament_size}</b>-player designed bracket · <b>${s.num_rounds}</b> rounds ·
         <b>${d.matches.length}</b> matches${extra}. The winner of the Final is the champion.`
      : `✓ Designed bracket with <b>${d.matches.length}</b> matches.`;
    if (clear) clear.style.display = "";
    if (open) open.textContent = "🎨 Edit the design";
  }

  function openDesigner() {
    const b = window.__ccTourneyBuilder;
    if (!b) { toast("The bracket builder didn't load — refresh the page.", "err"); return; }
    // The builder sits above the create modal, so cancelling it simply reveals the
    // modal again exactly as the host left it.
    b.open(createState.design, (design, summary) => {
      createState.design = design;
      createState.designSummary = summary || null;
      createState.mode = "design";
      openCreate();                 // rebuild the create modal with the design in hand
    });
  }
  function customTotal() { return createState.customSizes.reduce((a, b) => a + b, 0); }
  function addCustomMatch() {
    if (createState.customSizes.length >= 16) return;      // 16 opening matches is plenty
    if (customTotal() + 2 > 32) { toast("A tournament holds at most 32 players.", "warn"); return; }
    createState.customSizes.push(2);
    renderCustomBuilder(); refreshAdvance(); refreshPreview();
  }
  function removeCustomMatch(i) {
    if (createState.customSizes.length <= 1) return;
    createState.customSizes.splice(i, 1);
    renderCustomBuilder(); refreshAdvance(); refreshPreview();
  }
  function changeCustomSize(i, delta) {
    const cur = createState.customSizes[i] || 2;
    const next = clamp(cur + delta, 2, 8);
    if (next === cur) return;
    if (delta > 0 && customTotal() + delta > 32) { toast("A tournament holds at most 32 players.", "warn"); return; }
    createState.customSizes[i] = next;
    renderCustomBuilder(); refreshAdvance(); refreshPreview();
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
    const want = createState.advance;
    let formats = [];
    try {
      const r = await get(`/api/tournament/formats?capacity=${cap}&advance=${want}`);
      formats = (r.data && r.data.formats) || [];
    } catch (_) {}
    createState.formats = formats;
    if (!formats.some(f => f.players_per_match === createState.ppm)) createState.ppm = formats.length ? formats[0].players_per_match : 2;
    const grid = $("#ccT-fmts"); if (!grid) return;
    grid.innerHTML = "";
    formats.forEach(f => {
      const c = el("div", "ccT-fmt" + (f.players_per_match === createState.ppm ? " sel" : ""));
      const dots = Array.from({ length: f.players_per_match }, () => `<span class="ccT-dot"></span>`).join("");
      // A match too small for the chosen "top N" says so rather than silently
      // running a different rule than the picker above it promises.
      const rule = f.advance_ok
        ? (f.advance_per_match > 1 ? `top ${f.advance_per_match} advance` : "winner advances")
        : `only top ${f.max_advance} can advance`;
      c.innerHTML = `<div class="ccT-fmt-title">${f.players_per_match} Player</div>
        <div class="ccT-fmt-mini">${dots}</div>
        <div class="ccT-fmt-meta">${f.num_rounds} rounds · ${f.num_opening_matches} opening${f.num_byes ? " · " + f.num_byes + " bye" + (f.num_byes > 1 ? "s" : "") : ""}<br>${rule}</div>`;
      c.addEventListener("click", () => {
        createState.ppm = f.players_per_match;
        createState.advance = clamp(createState.advance, 1, Math.max(1, f.players_per_match - 1));
        refreshAdvance();
        refreshFormats();
      });
      grid.appendChild(c);
    });
    refreshAdvance();
    refreshPreview();
  }

  async function refreshPreview() {
    const box = $("#ccT-preview"); if (!box) return;
    let url;
    if (createState.mode === "design") {
      const d = createState.design, s = createState.designSummary;
      if (!d || !d.matches || !d.matches.length) {
        box.innerHTML = "Design a bracket in the builder, then create the tournament.";
      } else if (s) {
        box.innerHTML = `Your design: <b>${s.tournament_size}</b> players ·
          <b>${s.num_rounds}</b> rounds · <b>${s.playable_matches}</b> games.
          Every spot fills exactly as you set it, and the Final decides the champion.`;
      } else {
        box.innerHTML = `Your design: <b>${d.matches.length}</b> matches.`;
      }
      return;
    }
    const adv = `&advance=${clamp(createState.advance, 1, advanceCeiling())}`;
    if (createState.mode === "sizes") {
      const total = customTotal();
      if (total < 4 || total > 32) { box.innerHTML = `Add matches until the total is <b>4–32</b> players (currently <b>${total}</b>).`; return; }
      url = `/api/tournament/preview?opening_sizes=${createState.customSizes.join(",")}&third_place=${createState.thirdPlace ? 1 : 0}${adv}`;
    } else {
      url = `/api/tournament/preview?capacity=${createState.capacity}&players_per_match=${createState.ppm}&third_place=${createState.thirdPlace ? 1 : 0}${adv}`;
    }
    try {
      const r = await get(url);
      const s = r.data && r.data.summary;
      if (!s) { box.textContent = (r.data && r.data.error) || "…"; return; }
      const shape = (s.is_custom && s.opening_sizes) ? `Opening matches: <b>${s.opening_sizes.join(" · ")}</b> players. ` : "";
      const rule = (s.advancing_per_match || 1) > 1
        ? `The <b>top ${s.advancing_per_match}</b> of every match advance`
        : "The <b>winner</b> of each match advances";
      const rounds = (s.round_sizes || []).map(r => r.length).join(" → ");
      box.innerHTML = `${shape}<b>${s.tournament_size}</b>-player bracket · <b>${s.num_rounds}</b> rounds ·
        <b>${s.num_opening_matches}</b> opening matches · <b>${s.playable_matches}</b> games total
        ${s.num_byes ? "· <b>" + s.num_byes + "</b> bye" + (s.num_byes > 1 ? "s" : "") : "· no byes"}.
        ${rule} until <b>1 champion</b> remains${rounds ? ` (${rounds} matches per round)` : ""}.
        ${s.third_place_match ? " A 3rd-place match decides the bronze." : ""}`;
    } catch (_) { box.textContent = "Preview unavailable."; }
  }

  async function submitCreate() {
    const modal = $("#ccT-create"); const errEl = $("#ccT-create-err", modal);
    errEl.textContent = "";
    if (createState.mode === "design") {
      const d = createState.design;
      if (!d || !d.matches || !d.matches.length) {
        errEl.textContent = "Design a bracket first — open the bracket builder.";
        return;
      }
    } else if (createState.mode === "sizes") {
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
    if (createState.mode === "design") {
      body.custom_graph = createState.design;
      body.third_place_match = false;   // a design decides 3rd place by its shape
    } else if (createState.mode === "sizes") {
      body.opening_sizes = createState.customSizes.slice();
      body.advance_per_match = clamp(createState.advance, 1, advanceCeiling());
    } else {
      body.total_capacity = createState.capacity;
      body.players_per_match = createState.ppm;
      body.advance_per_match = clamp(createState.advance, 1, advanceCeiling());
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
          <div class="ccT-lit-meta">${t.joined}/${t.capacity} players · ${t.players_per_match} per match ·
            ${esc(t.advance_label || "Winner advances")} · <b>${esc(t.tournament_id)}</b></div>
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
    // "Spectate" must actually spectate. It used to just switch to the bracket
    // tab, which is what the tab beside it already does — so a knocked-out player
    // pressing the one button labelled "watch a game" got a bracket and no game.
    $("#ccT-h-spectate", scr).addEventListener("click", () => spectatePick());
    $("#ccT-h-host", scr).addEventListener("click", openHostPanel);
    return scr;
  }

  function openScreen() {
    clearStatus();
    stopWatch();               // full SSE/poll sync owns state while the overlay is up
    const scr = ensureScreen();
    scr.classList.add("open");
    T.screenOpen = true;
    // Force the next state to re-render even if its version matches the last one
    // synced before the overlay was closed — otherwise applyState() early-returns
    // and the just-opened overlay stays blank.
    T.lastVersion = -1;
    startSync();
  }
  function closeScreen() {
    const scr = $("#ccT-screen"); if (scr) scr.classList.remove("open");
    T.screenOpen = false; stopSync(); stopWatch();
    if (T.panHandlers) {
      window.removeEventListener("mousemove", T.panHandlers.move);
      window.removeEventListener("mouseup", T.panHandlers.up);
      T.panHandlers = null;
    }
  }
  function setView(v) {
    T.view = v;
    document.querySelectorAll("#ccT-screen .ccT-tab").forEach(t => t.classList.toggle("on", t.dataset.view === v));
    render();
  }

  // Is my run over (knocked out, champion, or the whole bracket is done)? Then the
  // header's exit is a plain "go back", not a forfeit — see onLeave.
  function myRunIsOver(st) {
    if (!st || !st.viewer) return false;
    if (st.phase === "complete" || st.phase === "cancelled") return true;
    return st.viewer.status === "eliminated" || st.viewer.status === "champion"
        || !st.viewer.in_tournament;
  }

  async function onLeave() {
    const st = T.state;
    // Knocked out / finished: this button is just "back to Home". Leaving for real
    // would tell the server we FORFEITED — costing the no-quit XP of a run we
    // already completed — and drop us out of the results we're waiting on. Close
    // the overlay instead and keep following the tournament from the home screen.
    if (myRunIsOver(st)) {
      closeScreen();
      goHome();
      if (st && st.phase !== "cancelled") { persistActive(); startWatch(); }
      else clearActive();
      return;
    }
    if (st && st.viewer && st.viewer.in_tournament && st.phase === "running") {
      if (!window.confirm("Leaving now forfeits your spot in the tournament. Continue?")) return;
    }
    try { await post("/api/tournament/leave", { id: T.tid }); } catch (_) {}
    clearActive(); clearStatus(); closeScreen();
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
    // Only offer Spectate when there is genuinely a game to watch: the tournament
    // is under way, the host allows watching, and at least one match we're not
    // playing in is live right now.
    const specBtn = $("#ccT-h-spectate", scr);
    if (specBtn) {
      const nLive = st.phase === "running" && st.spectators_allowed !== false
        ? liveOtherMatches(st, currentRoomId()).length : 0;
      specBtn.style.display = nLive > 0 ? "" : "none";
      specBtn.textContent = nLive > 1 ? `👁 Spectate (${nLive})` : "👁 Spectate";
    }
    // "Leave" forfeits — so it must not be the word on the button once there is
    // nothing left to forfeit (see onLeave).
    const leaveBtn = $("#ccT-h-leave", scr);
    if (leaveBtn) {
      const done = myRunIsOver(st);
      leaveBtn.textContent = done ? "← Home" : "Leave";
      leaveBtn.classList.toggle("coral", !done);
      leaveBtn.classList.toggle("ghost", done);
      leaveBtn.title = done ? "Back to the home screen (you stay in the tournament)"
                            : "Leave the tournament (forfeits your spot)";
    }
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
        <h3>Players — ${st.joined}/${st.capacity}${
          st.human_capacity != null && st.human_capacity < st.capacity
            ? ` <span style="font-weight:500;font-size:.8rem;opacity:.8">(${st.human_capacity} people, ${st.capacity - st.human_capacity} AI)</span>` : ""}</h3>
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
    // How players get out of a match — the difference between single elimination and
    // a top-N format is the first thing a competitor needs to know.
    const advN = st.summary ? (st.summary.advancing_per_match || 1) : 1;
    const advTxt = st.summary && st.summary.mixed_advance
      ? " Each match advances the number of players its designer set."
      : advN > 1
        ? ` The <b>top ${advN}</b> of every match advance; everyone else is knocked out.`
        : " Win your match or you're out.";
    const shapeLine = st.summary
      ? (st.summary.is_graph
          ? `<br>Designed bracket: <b>${st.summary.total_matches}</b> matches over <b>${st.summary.num_rounds}</b> rounds, <b>${st.summary.tournament_size}</b> spots.${advTxt}`
        : st.summary.is_custom && st.summary.opening_sizes
          ? `<br>Custom bracket: opening matches <b>${st.summary.opening_sizes.join(" · ")}</b> players, ${st.summary.num_rounds} rounds.${advTxt}`
          : `<br>Format: <b>${st.config.players_per_match} Player</b> matches, ${st.summary.num_rounds} rounds.${advTxt}`)
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

  // A designed bracket carries the host's own match labels ("Losers Bracket",
  // "Play-in", …). Use them for the column header when a round agrees on one,
  // otherwise fall back to the generated name.
  function roundLabel(br, ri) {
    const row = br.rounds[ri] || [];
    const labels = Array.from(new Set(row.map(m => (m.label || "").trim()).filter(Boolean)));
    if (labels.length === 1) return labels[0];
    if (labels.length > 1) return labels.join(" · ");
    return ROUND_NAMES(br.n_rounds, ri);
  }

  // What an empty spot is waiting for, from the host's design. Feed spots name the
  // match they come from ("🏅 winner of M2"), which is the whole point of a design.
  let CUSTOM_MNUM = {};   // designed match id -> match number, rebuilt per render
  function indexCustomIds(br) {
    CUSTOM_MNUM = {};
    (br.rounds || []).forEach(row => row.forEach(m => {
      if (m.custom_id) CUSTOM_MNUM[m.custom_id] = m.match_number;
    }));
  }
  const ordinalShort = (n) => n === 1 ? "winner" : n === 2 ? "runner-up" : "#" + n;
  function emptySpotText(m, si) {
    const k = (m.slot_kinds || [])[si];
    if (!k) return "—";
    if (k.kind === "ai") return "🤖 AI";
    if (k.kind === "human") return "🧑 player";
    if (k.kind === "invite") return k.invite ? "✉️ " + k.invite : "✉️ invited";
    if (k.kind === "winner_from" || k.kind === "top_from") {
      const from = CUSTOM_MNUM[k.source];
      const who = ordinalShort(+k.rank || 1);
      return from ? `${k.rank > 1 ? "🎖" : "🏅"} ${who} of M${from}` : `🏅 ${who}`;
    }
    return "—";
  }

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
    indexCustomIds(br);
    const myPath = computeMyPath(br, st.viewer && st.viewer.pid);
    br.rounds.forEach((round, ri) => {
      const col = el("div", "ccT-round");
      const header = roundLabel(br, ri);
      col.appendChild(el("div", "ccT-round-label", header));
      // When one round mixes labels (e.g. a Semifinal beside a Losers Bracket
      // match), each card names itself — the shared header can't.
      const mixed = round.some(m => (m.label || "").trim() && (m.label || "").trim() !== header);
      round.forEach(m => col.appendChild(matchCard(m, st, myPath, hostArrange, mixed)));
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

  function matchCard(m, st, myPath, hostArrange, showLabel) {
    const mine = (m.players || []).some(p => p && st.viewer && p.pid === st.viewer.pid);
    const card = el("div", "ccT-match " + (m.status === "active" || m.status === "ready" ? "active " : "") + (m.status === "complete" ? "complete " : "") + (mine ? "mine " : ""));
    card.dataset.mnum = m.match_number;
    card.id = "ccT-m-" + m.match_number;
    let statusTxt = { pending: "", ready: "ready", active: "live", complete: "done", bye: "bye" }[m.status] || "";
    // Your own playable match reads as a call to action, not a passive label.
    if (mine && m.status === "ready") statusTxt = "start ▶";
    else if (mine && m.status === "active") statusTxt = "enter ▶";
    // With "top N advance" more than one finisher goes through, so every advancing
    // player is marked — and their finishing place shown, since 2nd advancing is
    // meaningfully different from winning the match.
    const advancing = m.advancing || (m.winner ? [{ pid: m.winner.pid, place: 1 }] : []);
    const placeOf = {};
    advancing.forEach(a => { if (a && a.pid) placeOf[a.pid] = a.place || 1; });
    const multiAdvance = (m.advance || 1) > 1;
    let slots = "";
    (m.players || []).forEach((p, si) => {
      const place = p ? placeOf[p.pid] : null;
      const sc = m.scores && p ? m.scores[p.pid] : null;
      slots += `<div class="ccT-slot ${p ? "" : "empty"} ${place ? "win" : ""}" data-slot="${si}">
        ${p ? `<img class="ccT-sav" src="${esc(bridge().avSrc(p.avatar) || "/avatars/mullet.png")}" onerror="this.src='/avatars/mullet.png'">` : `<span class="ccT-sav"></span>`}
        <span class="ccT-snm">${p ? esc(p.name) : esc(emptySpotText(m, si))}</span>
        ${place && multiAdvance ? `<span class="ccT-splace">#${place}</span>` : ""}
        ${sc != null ? `<span class="ccT-ssc">${sc}</span>` : ""}</div>`;
    });
    card.innerHTML = `<span class="ccT-mnum">${m.is_third_place ? "3rd" : "M" + m.match_number}</span>
      ${statusTxt ? `<span class="ccT-mstatus" style="background:${m.status === "active" || m.status === "ready" ? "rgba(47,140,224,.22)" : m.status === "complete" ? "rgba(243,167,18,.28)" : "rgba(120,150,190,.16)"}">${statusTxt}</span>` : ""}
      ${showLabel && (m.label || "").trim() ? `<div class="ccT-mlabel">${esc(m.label)}</div>` : ""}
      ${multiAdvance ? `<div class="ccT-madv">top ${m.advance} advance</div>` : ""}
      ${slots}`;
    // Host arranging: in the lobby PREVIEW, any spot a player was SEEDED into can
    // be dragged/tapped to swap two players' bracket positions. On a generated
    // bracket that's round 1; a designed bracket can seat players in any round, so
    // go by the spot's own type instead.
    if (hostArrange) {
      card.querySelectorAll(".ccT-slot[data-slot]").forEach(slotEl => {
        const si = +slotEl.dataset.slot;
        const k = (m.slot_kinds || [])[si];
        const seedable = k ? ["open", "human", "ai", "invite"].indexOf(k.kind) >= 0 : m.round_index === 0;
        if (!seedable) return;
        const p = (m.players || [])[si];
        if (p && p.pid) wireHostSwapTarget(slotEl, p.pid, p.name);
      });
    }
    // Your match: click to open its ready check (M_READY) or jump in (M_ACTIVE).
    // Others' live matches: click to spectate.
    if (mine && (m.status === "ready" || m.status === "active")) {
      card.style.cursor = "pointer";
      card.title = m.status === "active" ? "Enter your match" : "Ready up to start your match";
      card.addEventListener("click", (e) => {
        e.stopPropagation();
        // Act on the LIVE viewer state, not the snapshot captured when this card
        // was drawn — the match may have flipped ready→active since then, and a
        // stale "ready" read would pop the ready-check instead of taking you in.
        const mm = (T.state && T.state.viewer && T.state.viewer.my_match)
                || (st.viewer && st.viewer.my_match);
        if (!mm) return;
        T.readyLobbyDismissed[mm.match_number] = false;
        if (mm.status === "active" && mm.room_id) enterMatch(mm);
        else openReadyLobby(mm);
      });
    } else if (!mine && m.status === "active" && m.room_id && st.spectators_allowed !== false) {
      card.style.cursor = "pointer";
      card.title = "Watch this match";
      card.addEventListener("click", (e) => { e.stopPropagation(); spectateMatch(m.room_id); });
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
        // feeds[i] carries finisher #(i+1), so a line only lights once THAT place
        // has actually been decided — with top-N advancement the 1st-place line
        // lighting says nothing about the 2nd-place one.
        const advanced = (m.advancing || (m.winner ? [m.winner] : [])).length;
        (m.feeds || []).forEach((f, wi) => {
          if (!f) return; const nextNum = matchNumberAt(br, f[0], f[1]); if (nextNum == null) return;
          const a = rectOf(m.match_number), b = rectOf(nextNum); if (!a || !b) return;
          const x1 = a.x + a.w, y1 = a.y + a.h / 2, x2 = b.x, y2 = b.y + b.h / 2, mx = (x1 + x2) / 2;
          const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
          p.setAttribute("d", `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`);
          p.setAttribute("class", "ccT-line" + (wi < advanced ? " lit" : ""));
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
    // The bracket is re-rendered on every SSE tick, which recreates `wrap`. Its own
    // listeners die with the old node, but window listeners would pile up — tear the
    // previous pair down before wiring a fresh one so they can't accumulate.
    if (T.panHandlers) {
      window.removeEventListener("mousemove", T.panHandlers.move);
      window.removeEventListener("mouseup", T.panHandlers.up);
    }
    const onMove = (e) => { if (!dragging) return; T.panX = px + (e.clientX - sx); T.panY = py + (e.clientY - sy); apply(); };
    const onUp = () => { dragging = false; wrap.classList.remove("grabbing"); };
    T.panHandlers = { move: onMove, up: onUp };
    wrap.addEventListener("mousedown", (e) => { dragging = true; wrap.classList.add("grabbing"); sx = e.clientX; sy = e.clientY; px = T.panX; py = T.panY; });
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
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

  // ── match ready check + entry + end screens ────────────────────────────────
  let _lastMatchRoom = null, _lastEndShownFor = null;
  function maybeMatchPrompt(st) {
    const mm = st.viewer && st.viewer.my_match;
    if (!mm) { _lastMatchRoom = null; closeReadyLobby(); return; }
    if (mm.status === "ready") {
      // A match is waiting on its ready check. Auto-open it the first time; after
      // that keep it live-updated (unless the player closed it to view the bracket).
      if (T.readyLobbyMatch === mm.match_number) renderReadyLobby(mm);
      else if (!T.readyLobbyDismissed[mm.match_number]) openReadyLobby(mm);
    } else if (mm.status === "active" && mm.room_id) {
      // Everyone readied → the game launched → all players jump in together (once).
      if (mm.room_id !== _lastMatchRoom) {
        _lastMatchRoom = mm.room_id;
        closeReadyLobby();
        // If we're already sitting on this match's room page (viewing the bracket
        // over our own live game), don't auto-yank the bracket away — the player
        // opened it on purpose. Only auto-jump when the game is on another page.
        if (!sameRoomAsCurrent(mm.room_id)) enterMatch(mm);
      }
    }
  }

  function readyLobbyEl() {
    let m = $("#ccT-matchready");
    if (!m) { m = el("div", "ccT-modal"); m.id = "ccT-matchready"; document.body.appendChild(m); }
    return m;
  }
  function openReadyLobby(mm) {
    T.readyLobbyMatch = mm.match_number;
    renderReadyLobby(mm);
    readyLobbyEl().classList.add("open");
  }
  function closeReadyLobby() {
    const m = $("#ccT-matchready"); if (m) m.classList.remove("open");
    T.readyLobbyMatch = null;
  }
  function renderReadyLobby(mm) {
    const m = readyLobbyEl();
    const roster = mm.ready_roster || [];
    const iAmReady = !!mm.i_am_ready;
    const rc = mm.ready_count != null ? mm.ready_count : roster.filter(p => p.ready).length;
    const tc = mm.total_count != null ? mm.total_count : roster.length;
    const rows = roster.map(p => `
      <div class="ccT-mr-row ${p.ready ? "ready" : ""} ${p.me ? "me" : ""}">
        <img class="ccT-av" src="${esc(bridge().avSrc(p.avatar) || "/avatars/mullet.png")}" onerror="this.src='/avatars/mullet.png'">
        <span class="ccT-mr-nm">${esc(p.name)}${p.me ? " (you)" : ""}${p.is_bot ? " 🤖" : ""}</span>
        <span class="ccT-mr-badge ${p.quit ? "gone" : p.ready ? "on" : (p.connected ? "off" : "gone")}">${
          p.quit ? "Forfeited" : p.ready ? "✓ Ready" : (p.connected ? "Waiting…" : "Offline")}</span>
      </div>`).join("");
    const allReady = tc > 0 && rc >= tc;
    m.innerHTML = `<div class="ccT-card" style="width:min(440px,94vw)"><div class="ccT-body" style="text-align:center">
      <div style="font-size:2.2rem">🌊</div>
      <h2>Match ${mm.match_number} — Ready Check</h2>
      <p style="margin:2px 0 8px">Round ${mm.round_index + 1}. The game starts the instant <b>every player</b> is ready.</p>
      <div class="ccT-mr-count"><b>${rc}</b> / ${tc} ready</div>
      <div class="ccT-mr-list">${rows}</div>
      ${allReady
        ? `<div class="ccT-mr-go">🌊 Everyone's ready — starting your match…</div>`
        : `<button class="ccT-btn wide ${iAmReady ? "ghost" : "gold"}" id="ccT-mr-ready">${iAmReady ? "✓ You're Ready — tap to cancel" : "I'm Ready — Start Match"}</button>`}
      <button class="ccT-btn wide ghost" id="ccT-mr-later">View bracket</button>
    </div></div>`;
    const rb = $("#ccT-mr-ready", m);
    if (rb) rb.addEventListener("click", () => onMatchReadyToggle(!iAmReady));
    $("#ccT-mr-later", m).addEventListener("click", () => {
      m.classList.remove("open");
      T.readyLobbyDismissed[mm.match_number] = true;
      T.readyLobbyMatch = null;
      setView("bracket");
    });
  }
  async function onMatchReadyToggle(want) {
    const r = await post("/api/tournament/match_ready", { id: T.tid, ready: !!want });
    if (!(r.data && r.data.ok)) toast((r.data && r.data.error) || "Could not update ready", "err");
    // The SSE state refresh re-renders the lobby (and launches once all are ready).
  }
  function enterMatch(mm) {
    // The match runs as a normal private GameRoom. Each player has a pre-claimed
    // seat; the server gave us its seat_token so we reconnect straight into it.
    const roomId = (mm && mm.room_id) || mm;
    if (!roomId) return;
    const seatTok = mm && mm.seat_token;
    persistActive();
    // Tell the server we've arrived (may trigger immediate auto-start once all in).
    post("/api/tournament/entered", { id: T.tid }).catch(() => {});
    // Are we already sitting on THIS match's room page? That happens when the
    // bracket overlay was opened on top of our own live game (via the status chip):
    // re-navigating to the same URL is a no-op, so the overlay just stays put with
    // the game behind it — the "click my game and it keeps me on the bracket with
    // the game in the background" bug. Instead, close the overlay to reveal the
    // live game underneath, and keep the watch running for our next round.
    if (sameRoomAsCurrent(roomId)) {
      // Force the live game back to the front (it may be sitting behind the
      // overlay), THEN drop the bracket overlay so the game is actually visible —
      // instead of leaving it running in the background. This is the fix for
      // "click Enter Your Match → stuck on the bracket, game only appears when I
      // leave the tournament": leaving worked only because it, too, closed the
      // overlay; entering now does the same, reliably.
      try { const b = bridge(); if (b && typeof b.revealGame === "function") b.revealGame(); } catch (_) {}
      closeScreen();
      startWatch();
      return;
    }
    // Leaving a match we were only WATCHING → hand the spectator seat back before
    // the page unloads, so we don't linger in that room's spectator list.
    try { const b = bridge(); if (b && typeof b.stopSpectating === "function") b.stopSpectating(); } catch (_) {}
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
    $("#ccT-cancelled-home", m).addEventListener("click", () => { m.classList.remove("open"); clearActive(); clearStatus(); closeScreen(); goHome(); });
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
    $("#ccT-champ-home", m).addEventListener("click", () => { m.classList.remove("open"); clearActive(); clearStatus(); closeScreen(); goHome(); });
  }
  function showFinalPlacement(st) {
    let m = $("#ccT-final"); if (!m) { m = el("div", "ccT-modal"); m.id = "ccT-final"; document.body.appendChild(m); }
    const place = st.viewer.final_place; const xp = me_xp(st);
    // A bare "25th Place" is meaningless on its own — say what it's 25th OF, and
    // how far the run actually got, so a big (often AI-padded) bracket reads right.
    const field = (st.participants || []).length;
    const bots = (st.participants || []).filter(p => p && p.is_bot).length;
    const mine = (st.final_placements || []).find(e => e && e.player_id === st.viewer.pid);
    const rounds = (st.bracket && st.bracket.rounds ? st.bracket.rounds.length : 0)
      || (st.summary && st.summary.num_rounds) || 0;
    const reached = mine && typeof mine.round_reached === "number" ? mine.round_reached + 1 : 0;
    const sub = [
      field ? `out of ${field} player${field === 1 ? "" : "s"}` : "",
      bots ? `(${bots} AI)` : "",
    ].filter(Boolean).join(" ");
    const run = (reached && rounds)
      ? `You made it to round ${Math.min(reached, rounds)} of ${rounds}.` : "";
    m.innerHTML = `<div class="ccT-card ccT-champ"><div class="ccT-body">
      <div style="font-size:2.4rem">${place === 2 ? "🥈" : place === 3 ? "🥉" : "🌊"}</div>
      <h2>${ordinal(place)} Place</h2>
      ${sub ? `<p class="ccT-sub" style="margin:0">${esc(sub)}</p>` : ""}
      <p>${esc(st.name)} is complete. ${esc(run)}</p>
      ${podiumHtml(st)} ${xpHtml(xp)}
      <button class="ccT-btn wide gold" id="ccT-final-bracket">View Final Bracket</button>
      <button class="ccT-btn wide ghost" id="ccT-final-home">Back to Home</button></div></div>`;
    m.classList.add("open");
    $("#ccT-final-bracket", m).addEventListener("click", () => { m.classList.remove("open"); setView("bracket"); });
    $("#ccT-final-home", m).addEventListener("click", () => { m.classList.remove("open"); clearActive(); clearStatus(); closeScreen(); goHome(); });
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

  // ── End-of-a-tournament-match hooks (called by preview-app.js) ────────────
  // Is the room the game app currently has open one of MY matches in a live
  // tournament? The end-of-game screen asks this so a bracket match finishes
  // with "Spectate / Wait for next match", not "Play Again / Back to Lobby".
  // Returns null (→ the normal buttons) whenever we can't prove it is.
  window.__ccTourneyMatchCtx = function () {
    try {
      const st = T.bg || T.state;
      if (!T.tid || !st || !st.viewer) return null;
      if (!st.viewer.in_tournament) return null;
      const cur = currentRoomId();
      const m = matchInRoom(st, cur);
      if (!m) return null;
      // Are we a PLAYER in this match, or watching it? Either way it's a bracket
      // match and "Play Again / Back to Lobby" is wrong — a watcher has no seat to
      // play again with, so that button could only ever fail.
      const playing = (m.players || []).some(p => p && p.pid === st.viewer.pid);
      const finished = st.phase === "complete" || st.phase === "cancelled";
      const live = liveOtherMatches(st, cur).length;
      return {
        tid: T.tid,
        name: st.name || "",
        watching: !playing,
        // Knocked out / tournament over → there is no "next match" to wait for.
        over: finished || st.viewer.status === "eliminated",
        // …but "no next match" is NOT "nothing to do": knocked out with games
        // still running, Spectate is the button that matters.
        finished: finished,
        live: live,
        canSpectate: st.spectators_allowed !== false && live > 0,
      };
    } catch (_) { return null; }
  };
  window.__ccTourneySpectate = function () { if (T.tid) spectatePick(); };
  window.__ccTourneyOpenScreen = function () { if (T.tid) openScreen(); };
  // "Wait for next match in the lobby": the player is back on the Player Home,
  // so paint the waiting bar straight away (live:-1 = count not known yet) and
  // let the watch keep it honest + pull them into their next match.
  window.__ccTourneyWaitInLobby = function () {
    if (!T.tid) return;
    persistActive();
    renderStatus({ mode: "waiting", name: (T.bg && T.bg.name) || "", live: -1, canSpectate: true });
    startWatch();
  };

  // =========================================================================
  // STATUS SURFACE — "where do I stand in this tournament right now?"
  // =========================================================================
  // One status element, two homes, so it is never in the player's way:
  //   • inside a game → a compact chip DOCKED INTO the game's own notice bar.
  //     (It used to be a fat "🏆 Return to <name>" pill pinned to bottom-centre,
  //     sitting straight on top of the hand and the action bar.)
  //   • anywhere else → a waiting BAR on the Player Home, styled like the Quick
  //     Match "Finding players…" banner (spinner + status + Spectate / Bracket),
  //     so you can wait there and still browse your stats, friends or the menu.
  // s = { mode:"lobby"|"playing"|"waiting"|"out"|"done", name, live, canSpectate }
  //   live: how many OTHER matches are being played right now (-1 = not known yet).
  //   "out" = knocked out while the tournament is STILL RUNNING — you have no next
  //   match, but the rest of the bracket is live and you can watch any of it.
  function chipText(s) {
    if (s.mode === "done") return "🏆 " + (s.name || "Tournament") + " — results";
    if (s.mode === "out") {
      if (s.live > 0) return `👁 Knocked out — ${s.live} game${s.live === 1 ? "" : "s"} still live`;
      return "🏆 Knocked out — follow the bracket";
    }
    if (s.mode === "waiting") {
      if (s.live > 0) return `⏳ Waiting for ${s.live} other game${s.live === 1 ? "" : "s"} to finish`;
      if (s.live === 0) return "⏳ Waiting for the next round";
      return "⏳ Waiting for the other games to finish";
    }
    return "🏆 Return to " + (s.name || "tournament");
  }
  function barMain(s) {
    if (s.mode === "done") return "This tournament is finished.";
    if (s.mode === "lobby") return "Your tournament hasn't started yet.";
    if (s.mode === "playing") return "Your tournament match is in progress.";
    if (s.mode === "out") {
      if (s.live > 0) return `You're out — ${s.live} tournament game${s.live === 1 ? "" : "s"} still being played.`;
      return "You're out — the next round is starting.";
    }
    if (s.live > 0) return `Waiting for ${s.live} other tournament game${s.live === 1 ? "" : "s"} to finish…`;
    if (s.live === 0) return "Waiting for the next tournament round…";
    return "Waiting for the other tournament games to finish…";
  }
  function barSub(s) {
    const nm = s.name ? s.name + " · " : "";
    if (s.mode === "done") return nm + "open the bracket for the final standings.";
    if (s.mode === "lobby") return nm + "open it to ready up when the host starts.";
    if (s.mode === "out") {
      return nm + (s.canSpectate === false
        ? "follow the rest of the bracket to the final."
        : "watch a live match, or follow the bracket to the final.");
    }
    return nm + "we'll bring you into your next match automatically.";
  }

  function renderStatus(s) {
    if (T.screenOpen) { clearStatus(); return; }   // the bracket itself is on screen
    T.status = s;
    const notice = document.getElementById("pv-notice");
    // offsetParent is null whenever #pv-game (its ancestor) is display:none, so
    // this is a live "is the game table actually on screen?" test.
    if (notice && notice.offsetParent !== null) { hideWaitBar(); showChip(notice, s); }
    else { hideChip(); showWaitBar(s); }
  }
  function clearStatus() { hideChip(); hideWaitBar(); }

  function showChip(notice, s) {
    let chip = document.getElementById("ccT-chip");
    if (!chip || chip.parentNode !== notice) {
      if (chip) chip.remove();
      chip = el("button", "ccT-chip"); chip.id = "ccT-chip"; chip.type = "button";
      const anchor = notice.querySelector(".pv-menu-wrap");
      if (anchor) notice.insertBefore(chip, anchor); else notice.appendChild(chip);
    }
    const txt = chipText(s);
    if (chip.textContent !== txt) chip.textContent = txt;
    chip.title = s.mode === "waiting"
      ? "Your match is done — the other tournament games are still running. Tap for the bracket."
      : "Open the tournament bracket";
    chip.classList.toggle("waiting", s.mode === "waiting");
    chip.classList.toggle("done", s.mode === "done");
    chip.onclick = () => openScreen();
  }
  function hideChip() { const c = document.getElementById("ccT-chip"); if (c) c.remove(); }

  // The waiting bar lives inside the Player Home, immediately after the Quick
  // Match search bar, so it shows on every tab of the home screen (overview,
  // stats, friends…) exactly like matchmaking does — and disappears with the
  // home screen, because it's a child of it.
  function waitBarEl() {
    let bar = document.getElementById("ccT-waitbar");
    if (bar) return bar;
    const qm = document.getElementById("qm-search-bar");
    const home = document.getElementById("auth-stats-lobby");
    if (!qm && !home) return null;
    bar = el("div"); bar.id = "ccT-waitbar";
    bar.setAttribute("role", "status"); bar.setAttribute("aria-live", "polite");
    bar.innerHTML = `<span class="ccT-wb-spin" aria-hidden="true"></span>
      <span class="ccT-wb-txt"><span id="ccT-wb-main"></span><span class="ccT-wb-sub" id="ccT-wb-sub"></span></span>
      <button type="button" class="ccT-wb-btn" id="ccT-wb-spectate">👁 Spectate Game</button>
      <button type="button" class="ccT-wb-btn" id="ccT-wb-bracket">🏆 Bracket</button>`;
    if (qm && qm.parentNode) qm.parentNode.insertBefore(bar, qm.nextSibling);
    else home.appendChild(bar);
    $("#ccT-wb-spectate", bar).addEventListener("click", () => spectatePick());
    $("#ccT-wb-bracket", bar).addEventListener("click", () => openScreen());
    return bar;
  }
  function showWaitBar(s) {
    const bar = waitBarEl(); if (!bar) return;
    bar.style.display = "";
    // The spinner means "something is being waited on" — nothing is, in the
    // pre-start lobby or once the bracket is over.
    bar.classList.toggle("nospin", s.mode === "done" || s.mode === "lobby");
    const main = $("#ccT-wb-main", bar), sub = $("#ccT-wb-sub", bar);
    if (main) main.textContent = barMain(s);
    if (sub) sub.textContent = barSub(s);
    // Offer Spectate whenever there is (or might be) another game to watch —
    // between your own matches AND after you've been knocked out. The "out" case
    // is the one players actually ask for: your run is over, the final isn't.
    const spec = $("#ccT-wb-spectate", bar);
    const canWatch = (s.mode === "waiting" || s.mode === "out") && s.canSpectate !== false && s.live !== 0;
    if (spec) spec.style.display = canWatch ? "" : "none";
    const br = $("#ccT-wb-bracket", bar);
    if (br) br.textContent = s.mode === "done" ? "🏆 View results"
                           : s.mode === "lobby" ? "🏆 Open tournament" : "🏆 Bracket";
  }
  function hideWaitBar() { const b = document.getElementById("ccT-waitbar"); if (b) b.style.display = "none"; }

  // Are we currently sitting inside a game room page (e.g. /play/ROOM)? A
  // tournament match runs as a normal game room, so while the player is IN their
  // match we must NOT slap the full-screen bracket overlay on top of the game
  // (that was the "click your match → can't join" bug — the overlay covered the
  // game and every click just re-opened the bracket). We show the return pill
  // instead so the game stays visible and the player can hop back when ready.
  function onGameRoomPage() {
    // The game app's OWN state is authoritative: a match can be live inline while
    // the address bar still reads /game (dedicated game window, or a room entered
    // without a navigation). Ask it first, then fall back to the URL.
    try {
      const b = bridge();
      if (b && typeof b.inGameRoom === "function" && b.inGameRoom()) return true;
    } catch (_) {}
    try {
      const parts = location.pathname.split("/").filter(Boolean);
      if (parts[0] === "play" && parts[1]) return true;             // /play/ROOM
      const qp = new URLSearchParams(location.search);
      if (qp.get("room") || qp.get("roomId")) return true;          // ?room=ROOM
    } catch (_) {}
    return false;
  }
  // Which match room the player is ACTUALLY in, upper-cased to match the
  // tournament's room ids. Prefer the game app's live room over the address bar:
  // if a match is running inline the URL may not be /play/ROOM, and a URL-only
  // check would wrongly decide we're NOT in the room — so "Enter your match"
  // would fail to drop the overlay and the game stayed stuck in the background.
  // "" when not in any room.
  function currentRoomId() {
    try {
      const b = bridge();
      if (b && typeof b.currentRoom === "function") {
        const r = b.currentRoom();
        if (r) return String(r).toUpperCase();
      }
    } catch (_) {}
    try {
      const parts = location.pathname.split("/").filter(Boolean);
      if (parts[0] === "play" && parts[1]) return String(parts[1]).toUpperCase();
      const qp = new URLSearchParams(location.search);
      const r = qp.get("room") || qp.get("roomId");
      if (r) return String(r).toUpperCase();
    } catch (_) {}
    return "";
  }
  function sameRoomAsCurrent(roomId) {
    const cur = currentRoomId();
    return !!cur && String(roomId || "").toUpperCase() === cur;
  }
  function allBracketMatches(br) {
    if (!br || !br.rounds) return [];
    const out = [];
    br.rounds.forEach(round => round.forEach(m => out.push(m)));
    if (br.third_place) out.push(br.third_place);
    return out;
  }

  // Which match is running in the room the game app currently has open (if any)?
  function matchInRoom(st, roomUpper) {
    if (!roomUpper) return null;
    return allBracketMatches(st && st.bracket)
      .find(m => m.room_id && String(m.room_id).toUpperCase() === roomUpper) || null;
  }
  // Other matches being played right now (never counts the room we're sitting in).
  function liveOtherMatches(st, roomUpper) {
    return allBracketMatches(st && st.bracket).filter(m =>
      m.status === "active" && m.room_id && String(m.room_id).toUpperCase() !== roomUpper);
  }

  // Background tournament watch. Runs whenever the bracket overlay is CLOSED but
  // we're still in a live tournament — in our match, spectating someone else's,
  // or waiting it out on the Player Home. It does three things:
  //   1) Keeps us marked CONNECTED on the server (each state read refreshes our
  //      presence) so our NEXT round's match WAITS for us to ready up instead of
  //      launching without us while we're still on the previous game's end screen.
  //   2) Paints the status surface (in-game chip / home waiting bar) so the player
  //      always knows whether they're playing or waiting on the other games.
  //   3) The moment our next match is up it brings us there — straight into the
  //      game if it already launched, or to its ready check if it's still
  //      gathering ready-ups — so we never get stranded on a finished game.
  function startWatch() {
    stopWatch();
    if (!T.tid) return;
    T.watchTimer = setInterval(watchTick, 4000);
    watchTick();
  }
  function stopWatch() {
    if (T.watchTimer) { clearInterval(T.watchTimer); T.watchTimer = null; }
  }
  async function watchTick() {
    if (!T.tid || T.screenOpen) return;        // overlay open → full sync owns state
    let st;
    try {
      const r = await get(`/api/tournament/state?id=${encodeURIComponent(T.tid)}&pid=${encodeURIComponent(T.pid || "")}&host_token=${encodeURIComponent(T.hostToken || "")}`);
      st = r.data && r.data.ok && r.data.state;
    } catch (_) { return; }
    if (!st || !st.viewer) return;
    // NB: don't touch T.state / T.lastVersion here — the overlay is closed so
    // nothing renders from them, and leaving lastVersion alone means opening the
    // screen (below) still triggers a fresh applyState→render instead of an
    // early-return on a matching version (which would paint a blank overlay).
    // T.bg is the read-only snapshot the end-of-game screen + Spectate use.
    T.bg = st;
    // Out of the tournament (left / removed) → stop watching, drop the status.
    if (!st.viewer.in_tournament && st.viewer.status !== "spectating") {
      stopWatch(); clearActive(); clearStatus(); return;
    }
    const curRoom = currentRoomId();            // re-read every tick: it changes when we spectate
    const cur = matchInRoom(st, curRoom);
    // Am I a PLAYER in the match running in this room, or just watching it?
    const iPlayHere = !!(cur && (cur.players || []).some(p => p && p.pid === st.viewer.pid));
    const myGameLive = !!(cur && iPlayHere && cur.status !== "complete");
    const base = { name: st.name, live: liveOtherMatches(st, curRoom).length,
                   canSpectate: st.spectators_allowed !== false };

    // The whole tournament is over → stop pulling, but leave the status up so the
    // bracket + final standings stay one tap away.
    if (st.phase === "complete" || st.phase === "cancelled") {
      const wasOut = T.status && T.status.mode === "out";
      stopWatch();
      renderStatus(Object.assign({ mode: "done" }, base));
      // A player knocked out earlier followed the rest of the bracket from here,
      // so this is the moment their run actually ends: show the result (placement
      // + XP) instead of leaving them to notice that a bar changed wording. Only
      // from the home screen — never on top of a game table or its end screen.
      if (wasOut && !myGameLive && !T.screenOpen && !onGameRoomPage()) openScreen();
      return;
    }
    // KNOCKED OUT, but the tournament is still being played. This is NOT "done":
    // the rest of the bracket is live and watching it is the whole point of
    // sticking around. Lumping it in with "complete" stopped the watch (so the
    // live count froze and the final standings never arrived) and painted the
    // "done" bar, which hides the Spectate button — the "I lost and now I can't
    // watch anything" bug. Keep watching, and keep offering the other games.
    if (st.viewer.status === "eliminated") {
      renderStatus(Object.assign({ mode: "out" }, base));
      announceKnockedOut(st, base);
      return;
    }
    const mm = st.viewer.my_match;              // our next ready/active match, if any
    // Never interrupt a game we're actually PLAYING; being a spectator in this
    // room (or sitting on a finished game's end screen) must not hold us back.
    if (!myGameLive && mm && mm.room_id && String(mm.room_id).toUpperCase() !== curRoom) {
      stopWatch();
      clearStatus();
      closeEndScreen();                         // it stacks over the ready check otherwise
      if (mm.status === "active") enterMatch(mm);    // already launched → jump straight in
      else openScreen();                            // still gathering ready-ups → show its ready check
      return;
    }
    const mode = st.phase === "lobby" ? "lobby" : (myGameLive ? "playing" : "waiting");
    renderStatus(Object.assign({ mode: mode }, base));
  }
  // Knocked out, first time only: point the player at the games still running so
  // "my run is over" doesn't read as "the tournament is over for me". The status
  // bar/chip and the bracket's own live-match cards do the rest.
  function announceKnockedOut(st, base) {
    const key = st.tournament_id || T.tid;
    if (!key || T.outAnnounced === key) return;
    T.outAnnounced = key;
    if (base.canSpectate === false || !(base.live > 0)) return;
    toast(`You're out of ${st.name || "the tournament"} — ${base.live} game${base.live === 1 ? " is" : "s are"} still live. Tap Spectate to watch.`, "info");
  }

  // The end-of-game screen sits at z-index 9850 and would bury the ready check.
  function closeEndScreen() {
    try { const b = bridge(); if (b && typeof b.closeEndScreen === "function") b.closeEndScreen(); } catch (_) {}
  }

  // =========================================================================
  // SPECTATE — only ever offered when another match is genuinely being played
  // =========================================================================
  async function spectatePick() {
    let st = T.bg || T.state;
    if (T.tid) {
      try {
        const r = await get(`/api/tournament/state?id=${encodeURIComponent(T.tid)}&pid=${encodeURIComponent(T.pid || "")}&host_token=${encodeURIComponent(T.hostToken || "")}`);
        if (r.data && r.data.ok && r.data.state) { st = r.data.state; T.bg = st; }
      } catch (_) { /* fall back to the last snapshot */ }
    }
    if (!st) { toast("Could not reach the tournament, try again in a moment.", "err"); return; }
    if (st.spectators_allowed === false) {
      showNotice("🚫", "Spectating is off", "The host turned spectating off for <b>" + esc(st.name || "this tournament") + "</b>, so matches can't be watched.");
      return;
    }
    const cur = currentRoomId();
    const live = liveOtherMatches(st, cur);
    if (!live.length) {
      const out = st.viewer && st.viewer.status === "eliminated";
      // "All finished" is only true once the bracket is actually over. Between
      // rounds the next matches exist but haven't launched yet, and telling a
      // player everything is done when the semifinal is about to start is wrong.
      const pending = allBracketMatches(st.bracket).filter(m =>
        (m.status === "ready" || m.status === "pending") &&
        String(m.room_id || "").toUpperCase() !== cur).length;
      if (st.phase === "complete") {
        showNotice("🏆", "The tournament is over",
          "Every match in <b>" + esc(st.name || "this tournament") + "</b> has been played. Open the bracket for the final standings.");
      } else if (pending) {
        showNotice("🌊", "The next round hasn't started yet",
          "The other games in <b>" + esc(st.name || "this tournament") + "</b> are still gathering their players. Try again in a moment — "
          + (out ? "we'll keep the bracket updated as they play." : "we'll bring you into your match the moment it's ready."));
      } else {
        showNotice("🌊", "Nothing is being played right now",
          "No match in <b>" + esc(st.name || "this tournament") + "</b> is live at the moment. "
          + (out ? "Follow the bracket — the next round starts shortly." : "Hang tight, we'll bring you into your match the moment it's ready."));
      }
      return;
    }
    if (live.length === 1) { startSpectating(live[0].room_id); return; }
    showWatchPicker(live);
  }

  // Small "nothing to see here" card — reuses the tournament modal styling.
  function showNotice(icon, title, htmlBody) {
    let m = $("#ccT-notice");
    if (!m) { m = el("div", "ccT-modal"); m.id = "ccT-notice"; document.body.appendChild(m); }
    m.innerHTML = `<div class="ccT-card" style="width:min(430px,94vw)"><div class="ccT-body" style="text-align:center">
      <div style="font-size:2.2rem">${icon}</div><h2>${esc(title)}</h2>
      <p style="margin:6px 0 2px">${htmlBody}</p>
      <button class="ccT-btn wide gold" id="ccT-notice-ok">Got it</button></div></div>`;
    m.classList.add("open");
    $("#ccT-notice-ok", m).addEventListener("click", () => m.classList.remove("open"));
  }

  function showWatchPicker(live) {
    let m = $("#ccT-watchpick");
    if (!m) { m = el("div", "ccT-modal"); m.id = "ccT-watchpick"; document.body.appendChild(m); }
    const rows = live.map(mt => {
      const names = (mt.players || []).filter(Boolean).map(p => esc(p.name)).join(" vs ");
      return `<div class="ccT-watchrow">
        <div class="ccT-wr-nm"><div class="ccT-wr-title">${mt.is_third_place ? "3rd place match" : "Match " + mt.match_number}</div>
          <div class="ccT-wr-meta">${names || "In progress"}</div></div>
        <button class="ccT-btn sm gold" data-room="${esc(mt.room_id)}">Watch ▶</button></div>`;
    }).join("");
    m.innerHTML = `<div class="ccT-card" style="width:min(460px,94vw)"><div class="ccT-body">
      <h2 style="padding:6px 0 2px">Watch a live match</h2>
      <p class="ccT-sub" style="padding:0 0 6px">${live.length} games are still being played.</p>
      ${rows}
      <button class="ccT-btn wide ghost" id="ccT-watch-cancel">Cancel</button></div></div>`;
    m.classList.add("open");
    m.querySelectorAll("[data-room]").forEach(btn => btn.addEventListener("click", () => {
      m.classList.remove("open"); startSpectating(btn.dataset.room);
    }));
    $("#ccT-watch-cancel", m).addEventListener("click", () => m.classList.remove("open"));
  }

  // Watch a match IN PLACE (no page reload) so the watch keeps running and can
  // pull us straight out the moment our own next match is ready.
  async function startSpectating(rid) {
    if (!rid) return;
    persistActive();
    const b = bridge();
    if (b && typeof b.spectateRoom === "function") {
      let ok = false;
      try { ok = await b.spectateRoom(rid); } catch (_) { ok = false; }
      closeScreen();          // reveal the game behind the bracket (no-op if closed)
      clearStatus();
      if (!ok) { startWatch(); return; }   // joinAsSpectator already explained why
      startWatch();
      return;
    }
    // No bridge (older app shell) → fall back to the deep link.
    try { window.location.href = "/play/" + encodeURIComponent(rid) + "?t=" + encodeURIComponent(T.tid) + "&spectate=1"; }
    catch (_) { toast("Watch room " + rid, "info"); }
  }

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
      T.bg = st;
      const qp = new URLSearchParams(location.search);
      const forThisTourney = qp.get("t") === a.tid;
      // While inside the match room itself, never cover the game with the overlay —
      // the watch shows the status chip in the game's own notice bar instead, keeps
      // us connected (so the next round waits for us) and pulls us into our next
      // match's ready check when it's up.
      if (!onGameRoomPage() && forThisTourney && !T.screenOpen) {
        // Came back to the app shell from a match (e.g. left the room) → re-open.
        openScreen();
      } else {
        startWatch();
      }
    } catch (_) { /* leave the blob; try again next load */ }
  }

  function handleUrlAutoJoin() {
    const qp = new URLSearchParams(location.search);
    const code = (qp.get("tournament") || qp.get("join_tournament") || "").trim().toUpperCase();
    if (code && /^[A-Z0-9]{4,12}$/.test(code)) promptJoin(code);
  }

  // Tournament match rooms are full (all seats pre-claimed), so joining one
  // without a seat token yields the read-only spectator view (hands stripped).
  function spectateMatch(roomId) { startSpectating(roomId); }

  function boot() {
    injectStyles();
    injectMenuOption();
    handleUrlAutoJoin();     // ?tournament=CODE deep-link
    resumeActiveIfAny();     // status chip / waiting bar / auto-reopen after a match
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();
})();
