(function () {
  "use strict";
  const ADMIN_EMAIL = "currentsandcritters@gmail.com";

  // ── Access control ───────────────────────────────────────────────
  // TWO ways to hold the Controller, and they are not the same thing.
  //
  //   isAdmin()      the developer account. Works anywhere, any room, and is
  //                  gated on the server by ADMIN_MOD_KEY.
  //   tableArmed()   a table voted yes in the lobby (see the Current
  //                  Controller row there). No key, casual rooms only, only
  //                  for the seat that asked, and only for that one game.
  //
  // Both are re-checked on EVERY action rather than cached: a vote belongs to
  // one game, so the panel has to close itself when that game ends.
  function isAdmin() {
    try {
      const u = window.__fishAuthUser && window.__fishAuthUser();
      return !!(u && u.email && String(u.email).toLowerCase() === ADMIN_EMAIL);
    } catch (_) { return false; }
  }
  // The server's own answer, mirrored into every state payload. `is_mine` is
  // what stops one armed table handing the panel to everybody at it.
  function tableArmed() {
    try {
      const cc = (payload() || {}).controller;
      return !!(cc && cc.armed && cc.is_mine);
    } catch (_) { return false; }
  }
  function mayControl() { return isAdmin() || tableArmed(); }

  // ── Tool registry ────────────────────────────────────────────────
  // phase "live"  = fully working client-side now.
  // phase "server"= UI present; mutations land in the server pass.
  const TOOLS = [
    { id:"bot_brain",    icon:"🧠", name:"Bot Brain Viewer",          phase:"server", desc:"See each bot's strategy, intended move, reasoning and top scored alternatives." },
    { id:"bot_override", icon:"🎮", name:"Bot Move Override",         phase:"server", desc:"Force a bot's next move from its legal actions; play continues normally." },
    { id:"enemy_hands",  icon:"🃏", name:"Enemy Hand Viewer",         phase:"server", desc:"Reveal every hand; add / flood ANY card (both faces, many copies) / remove / clear / copy." },
    { id:"deck_picker",  icon:"🗂️", name:"Full Deck Picker",          phase:"server", desc:"Browse EVERY card (both faces) and drop fresh copies into a hand (flood), pool, deck or discard." },
    { id:"force_pool",   icon:"🌊", name:"Force Pool Cards",          phase:"server", desc:"Clear, refill, remove, or force exact cards into the pool." },
    { id:"what_if",      icon:"🔮", name:"What If Menu",              phase:"live",   desc:"Preview move estimates, then commit a real legal move with Apply This Move." },
    { id:"bug_log",      icon:"🐞", name:"Recent Bug Log",            phase:"live",   desc:"Track errors and odd game-state issues this session with full context." },
    { id:"state_viewer", icon:"📊", name:"Game State Viewer",         phase:"live",   desc:"Live snapshot of turn, deck, pool, scores, hands, oceans, end-game and sync." },
  ];
  const TOOL_BY_ID = {};
  TOOLS.forEach(t => { TOOL_BY_ID[t.id] = t; });
  // The How to Play tab lists these rather than keeping its own copy, so a tool
  // added here cannot end up undocumented there. Read only: the panel is still
  // built from TOOLS itself.
  window.CC_CONTROLLER_TOOLS = TOOLS.map(t => ({ id: t.id, icon: t.icon, name: t.name, desc: t.desc }));

  // ── Toggle persistence ───────────────────────────────────────────
  const LS_KEY = "cc_toggles_v1";
  let toggles = {};
  try { toggles = JSON.parse(localStorage.getItem(LS_KEY) || "{}") || {}; } catch (_) { toggles = {}; }
  function saveToggles() { try { localStorage.setItem(LS_KEY, JSON.stringify(toggles)); } catch (_) {} }
  const isOn = (id) => !!toggles[id];

  // ── Helpers ──────────────────────────────────────────────────────
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;" }[c]));
  }
  function payload() { try { return (window.__ccGetPayload && window.__ccGetPayload()) || null; } catch (_) { return null; } }
  function myIndex(p) { const v = p && p.viewer; return v && Number.isInteger(v.seat_index) ? v.seat_index : null; }

  // Visual card tile cropped to the correct face of the sprite sheet.
  function cardTile(face, fallbackLabel) {
    const uid = Number(face && face.uid) || 0;
    const info = (window.__ccCardImg && uid) ? window.__ccCardImg(uid) : { url:"", pos:"center" };
    const name = (face && (face.name || face.species)) || fallbackLabel || ("#" + uid);
    const pts  = (face && face.points != null) ? face.points : "";
    let bg = "";
    if (info.url) {
      let size = "cover", pos = "center";
      if (info.pos === "top")    { size = "100% 200%"; pos = "center top"; }
      else if (info.pos === "bottom") { size = "100% 200%"; pos = "center bottom"; }
      else if (info.pos === "left")   { size = "200% 100%"; pos = "left center"; }
      else if (info.pos === "right")  { size = "200% 100%"; pos = "right center"; }
      bg = `background-image:url('${info.url}');background-size:${size};background-position:${pos};`;
    }
    return `<div class="cc-card" title="${esc(name)}"><div class="cc-card-img" style="${bg}"></div><div class="cc-card-name">${esc(name)}${pts !== "" ? " · " + esc(pts) : ""}</div></div>`;
  }
  function cardBack(n) {
    let out = "";
    for (let i = 0; i < n; i++) out += `<div class="cc-card cc-card-back"><div class="cc-card-img"></div><div class="cc-card-name">hidden</div></div>`;
    return out;
  }
  const firstFace = (entry) => (entry && Array.isArray(entry.faces) && entry.faces[0]) ? entry.faces[0] : null;

  // ── Bug log (records only while the toggle is ON) ─────────────────
  const bugLog = [];
  function pushBug(message, explanation) {
    if (!isOn("bug_log")) return;
    const p = payload() || {};
    const st = p.state || {};
    const la = window.__ccLastAction;
    bugLog.unshift({
      message: String(message || "").slice(0, 600),
      turn: st.round_count != null ? st.round_count : "-",
      currentPlayer: st.current_player || "-",
      affectedPlayer: st.current_player || "-",
      action: la ? (la.kind || (la.action && la.action.kind) || "action") : "-",
      ts: new Date().toISOString(),
      explanation: explanation || ""
    });
    if (bugLog.length > 100) bugLog.length = 100;
    if (panelOpen() && isOn("bug_log")) renderBody("bug_log");
  }
  window.addEventListener("error", (e) => { try { pushBug((e.message || "error") + (e.filename ? " @ " + e.filename + ":" + e.lineno : ""), "Uncaught error"); } catch (_) {} });
  window.addEventListener("unhandledrejection", (e) => { try { pushBug("Unhandled rejection: " + (e.reason && (e.reason.message || e.reason)), "Promise rejected"); } catch (_) {} });
  const _origErr = console.error.bind(console);
  console.error = function (...a) {
    try { pushBug(a.map(x => typeof x === "string" ? x : (x && x.message) || (function(){ try { return JSON.stringify(x); } catch(_) { return String(x); } })()).join(" "), "console.error"); } catch (_) {}
    return _origErr(...a);
  };


  // ════════════════════════════════════════════════════════════════
  //  STYLES
  // ════════════════════════════════════════════════════════════════
  const css = `
  #cc-overlay { position:fixed; inset:0; z-index:100000; display:none; background:rgba(2,18,38,.62); backdrop-filter:blur(3px); -webkit-backdrop-filter:blur(3px); }
  #cc-overlay.open { display:flex; align-items:flex-start; justify-content:center; padding:24px 14px; overflow:auto; }
  #cc-panel { width:100%; max-width:1000px; margin:auto; background:linear-gradient(160deg,#0a2742 0%,#0d3a5e 48%,#0a2c4a 100%); border:1px solid rgba(80,200,235,.45); border-radius:20px; box-shadow:0 24px 80px rgba(0,0,0,.6),0 0 0 1px rgba(60,170,220,.18) inset; color:#dff1fb; font-family:"Nunito",system-ui,sans-serif; overflow:hidden; }
  #cc-head { display:flex; align-items:center; gap:12px; padding:16px 20px; border-bottom:1px solid rgba(80,200,235,.25); background:linear-gradient(90deg,rgba(20,90,140,.55),rgba(12,60,100,.2)); }
  #cc-head .cc-logo { font-family:"Cinzel",serif; font-size:1.2rem; font-weight:900; letter-spacing:.5px; color:#7fe3ff; text-shadow:0 1px 8px rgba(40,180,230,.5); flex:1; }
  #cc-head .cc-tag { font-size:.66rem; font-weight:800; text-transform:uppercase; letter-spacing:1px; color:#0a2742; background:#5fd0e8; padding:3px 8px; border-radius:6px; }
  #cc-close { background:rgba(255,255,255,.08); border:1px solid rgba(120,205,235,.4); color:#cfeeff; width:32px; height:32px; border-radius:9px; font-size:18px; cursor:pointer; line-height:1; }
  #cc-close:hover { background:rgba(232,64,87,.25); border-color:#e84057; color:#fff; }
  #cc-key { background:rgba(255,255,255,.08); border:1px solid rgba(120,205,235,.4); color:#cfeeff; width:32px; height:32px; border-radius:9px; font-size:15px; cursor:pointer; line-height:1; }
  #cc-key:hover { background:rgba(95,208,232,.22); border-color:#5fd0e8; color:#fff; }
  #cc-body { padding:14px 18px 22px; max-height:calc(100vh - 130px); overflow:auto; }
  .cc-tool { border:1px solid rgba(80,170,215,.28); border-radius:14px; margin-bottom:12px; background:rgba(8,40,68,.55); overflow:hidden; }
  .cc-tool.cc-active { border-color:rgba(95,208,232,.7); box-shadow:0 0 0 1px rgba(95,208,232,.25) inset; }
  .cc-row { display:flex; align-items:center; gap:14px; padding:13px 16px; }
  .cc-ico { font-size:1.4rem; width:30px; text-align:center; flex-shrink:0; }
  .cc-meta { flex:1; min-width:0; }
  .cc-name { font-weight:800; font-size:1rem; color:#eaf8ff; display:flex; align-items:center; gap:8px; }
  .cc-badge { font-size:.58rem; font-weight:800; text-transform:uppercase; letter-spacing:.6px; padding:2px 6px; border-radius:5px; }
  .cc-badge.live { background:rgba(31,187,138,.22); color:#5ff0bf; border:1px solid rgba(31,187,138,.5); }
  .cc-badge.server { background:rgba(240,180,60,.18); color:#ffd574; border:1px solid rgba(240,180,60,.45); }
  .cc-desc { font-size:.8rem; color:#9fc6e0; margin-top:3px; line-height:1.4; }
  .cc-switch { position:relative; width:50px; height:28px; flex-shrink:0; border-radius:999px; background:rgba(120,150,175,.35); border:1px solid rgba(150,190,215,.4); cursor:pointer; transition:background .18s; }
  .cc-switch::after { content:""; position:absolute; top:2px; left:2px; width:22px; height:22px; border-radius:50%; background:#dceffb; transition:transform .18s; box-shadow:0 1px 4px rgba(0,0,0,.4); }
  .cc-switch.on { background:linear-gradient(90deg,#1f9ad7,#22d8c8); border-color:#3de0e0; }
  .cc-switch.on::after { transform:translateX(22px); }
  .cc-toolbody { display:none; padding:0 16px 16px; border-top:1px solid rgba(80,170,215,.2); }
  .cc-tool.cc-active .cc-toolbody { display:block; }
  .cc-sub { font-size:.72rem; color:#8fb6d2; margin:12px 0 6px; text-transform:uppercase; letter-spacing:.8px; font-weight:800; }
  .cc-btn { background:rgba(40,150,210,.28); border:1px solid rgba(95,208,232,.5); color:#dff1fb; padding:7px 13px; border-radius:9px; font-weight:700; font-size:.82rem; cursor:pointer; font-family:inherit; }
  .cc-btn:hover { background:rgba(40,150,210,.5); }
  .cc-btn:disabled { opacity:.4; cursor:not-allowed; }
  .cc-btn.cc-danger { background:rgba(232,64,87,.22); border-color:rgba(232,64,87,.55); }
  .cc-btn.cc-danger:hover { background:rgba(232,64,87,.42); }
  .cc-btnrow { display:flex; flex-wrap:wrap; gap:8px; margin:8px 0; }
  .cc-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(160px,1fr)); gap:10px; }
  .cc-kv { display:flex; justify-content:space-between; gap:10px; padding:6px 10px; background:rgba(8,40,68,.5); border:1px solid rgba(80,170,215,.2); border-radius:8px; font-size:.82rem; }
  .cc-kv b { color:#7fe3ff; font-weight:800; }
  .cc-cards { display:flex; flex-wrap:wrap; gap:8px; margin:6px 0 4px; }
  .cc-card { width:64px; }
  .cc-card-img { width:64px; height:64px; border-radius:8px; border:1px solid rgba(95,208,232,.4); background:#0a2742 center/cover no-repeat; overflow:hidden; }
  .cc-card-back .cc-card-img { background:var(--cc-card-back) center/cover no-repeat; }
  .cc-card-name { font-size:.62rem; color:#bfe0f2; text-align:center; margin-top:2px; line-height:1.2; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .cc-seat { border:1px solid rgba(80,170,215,.25); border-radius:11px; padding:10px 12px; margin-bottom:10px; background:rgba(6,32,56,.5); }
  .cc-seat-hd { font-weight:800; color:#eaf8ff; margin-bottom:6px; display:flex; align-items:center; gap:8px; }
  .cc-note { font-size:.76rem; color:#ffd574; background:rgba(240,180,60,.1); border:1px dashed rgba(240,180,60,.45); border-radius:9px; padding:9px 12px; margin:8px 0; line-height:1.5; }
  .cc-pending { font-size:.82rem; color:#cfe6f4; line-height:1.6; }
  .cc-pending ul { margin:6px 0 0; padding-left:18px; }
  .cc-loglist { max-height:280px; overflow:auto; font-family:ui-monospace,monospace; font-size:.74rem; }
  .cc-logitem { border:1px solid rgba(232,64,87,.3); background:rgba(232,64,87,.07); border-radius:8px; padding:8px 10px; margin-bottom:7px; }
  .cc-logitem .m { color:#ff9aa8; font-weight:700; word-break:break-word; }
  .cc-logitem .meta { color:#9fc6e0; margin-top:4px; }
  .cc-input { background:rgba(8,40,68,.7); border:1px solid rgba(95,208,232,.4); color:#eaf8ff; border-radius:9px; padding:7px 11px; font-family:inherit; font-size:.85rem; width:100%; box-sizing:border-box; }
  .cc-achrow { display:flex; align-items:center; gap:10px; padding:7px 10px; border:1px solid rgba(80,170,215,.2); border-radius:9px; margin-bottom:6px; background:rgba(8,40,68,.45); }
  .cc-achrow .ai { font-size:1.1rem; }
  .cc-achrow .an { flex:1; min-width:0; font-size:.84rem; }
  .cc-achrow .an small { display:block; color:#8fb6d2; font-size:.7rem; }
  .cc-achrow.done { border-color:rgba(31,187,138,.5); }
  #cc-menu-item { color:#5fd0e8 !important; font-weight:800; }
  .cc-keybar { display:flex; align-items:center; gap:10px; flex-wrap:wrap; padding:9px 12px; margin-bottom:10px; border:1px solid rgba(95,208,232,.35); border-radius:11px; background:rgba(8,40,68,.6); }
  .cc-pick { position:fixed; inset:0; z-index:100002; background:rgba(2,18,38,.7); display:flex; align-items:flex-start; justify-content:center; padding:30px 14px; overflow:auto; }
  .cc-pick-inner { width:100%; max-width:720px; background:linear-gradient(160deg,#0a2742,#0d3a5e); border:1px solid rgba(95,208,232,.5); border-radius:16px; padding:14px 16px; color:#dff1fb; box-shadow:0 20px 60px rgba(0,0,0,.6); }
  .cc-pick-hd { display:flex; align-items:center; justify-content:space-between; gap:10px; }
  .cc-pick-grid { display:flex; flex-wrap:wrap; gap:8px; max-height:60vh; overflow:auto; }
  .cc-pick-cell { cursor:pointer; border-radius:9px; transition:transform .1s; }
  .cc-pick-cell:hover { transform:scale(1.06); }
  @media (max-width:640px){ .cc-card{width:54px} .cc-card-img{width:54px;height:54px} }
  `;
  const styleEl = document.createElement("style");
  styleEl.id = "cc-styles";
  styleEl.textContent = css;
  document.head.appendChild(styleEl);

  // ════════════════════════════════════════════════════════════════
  //  PANEL DOM
  // ════════════════════════════════════════════════════════════════
  let overlay = null;
  const panelOpen = () => !!(overlay && overlay.classList.contains("open"));

  function buildPanel() {
    if (overlay) return overlay;
    overlay = document.createElement("div");
    overlay.id = "cc-overlay";
    overlay.innerHTML =
      `<div id="cc-panel" role="dialog" aria-label="Current Controller">
        <div id="cc-head">
          <span class="cc-logo">🌊 Current Controller</span>
          <span class="cc-tag">Admin</span>
          <button id="cc-key" title="Change admin key">🔑</button>
          <button id="cc-close" title="Close">✕</button>
        </div>
        <div id="cc-body">${TOOLS.map(toolRowHtml).join("")}</div>
      </div>`;
    document.body.appendChild(overlay);

    overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });
    overlay.querySelector("#cc-close").addEventListener("click", close);
    overlay.querySelector("#cc-key").addEventListener("click", () => {
      promptKey();
      // Re-run any open server-backed tool with the new key.
      TOOLS.forEach(t => { if (t.phase === "server" && isOn(t.id)) renderBody(t.id); });
    });
    TOOLS.forEach(t => {
      const sw = overlay.querySelector(`#cc-sw-${t.id}`);
      if (sw) sw.addEventListener("click", () => toggleTool(t.id));
    });
    return overlay;
  }

  function toolRowHtml(t) {
    const on = isOn(t.id);
    return `<div class="cc-tool ${on ? "cc-active" : ""}" id="cc-tool-${t.id}">
      <div class="cc-row">
        <div class="cc-ico">${t.icon}</div>
        <div class="cc-meta">
          <div class="cc-name">${esc(t.name)} <span class="cc-badge ${t.phase}">${t.phase === "live" ? "Live" : "Server"}</span></div>
          <div class="cc-desc">${esc(t.desc)}</div>
        </div>
        <div class="cc-switch ${on ? "on" : ""}" id="cc-sw-${t.id}" role="switch" aria-checked="${on}" tabindex="0"></div>
      </div>
      <div class="cc-toolbody" id="cc-body-${t.id}"></div>
    </div>`;
  }

  function toggleTool(id) {
    if (!mayControl()) { close(); return; }   // hard re-check on every action
    toggles[id] = !toggles[id];
    saveToggles();
    const tool = overlay && overlay.querySelector(`#cc-tool-${id}`);
    const sw = overlay && overlay.querySelector(`#cc-sw-${id}`);
    if (tool) tool.classList.toggle("cc-active", isOn(id));
    if (sw) { sw.classList.toggle("on", isOn(id)); sw.setAttribute("aria-checked", String(isOn(id))); }
    onToolStateChange(id);
    renderBody(id);
  }

  function onToolStateChange(_id) {}

  function open() {
    if (!mayControl()) return;
    // The admin key is the DEVELOPER's way in. A table that voted yes needs no
    // key at all (the server authorizes that seat from the vote it watched),
    // so a Supporter must never be asked for a secret they do not have.
    if (!tableArmed() && !ccAdminKey()) {
      const v = prompt("Enter the Current Controller admin key:", "");
      if (v === null) return;            // cancelled, leave the panel closed
      ccSetAdminKey(v.trim());
    }
    buildPanel();
    TOOLS.forEach(t => renderBody(t.id));
    overlay.classList.add("open");
  }
  function close() { if (overlay) overlay.classList.remove("open"); }

  // ════════════════════════════════════════════════════════════════
  //  TOOL BODIES
  // ════════════════════════════════════════════════════════════════
  function renderBody(id) {
    const el = overlay && overlay.querySelector(`#cc-body-${id}`);
    if (!el) return;
    if (!isOn(id)) { el.innerHTML = ""; return; }
    try { (BODY[id] || (() => { el.innerHTML = ""; }))(el); }
    catch (e) { el.innerHTML = `<div class="cc-note">Tool render error: ${esc(e && e.message)}</div>`; }
  }

  // ── Server-mod bridge (hard-gated by ADMIN_MOD_KEY on the server) ──
  const ADMIN_KEY_LS = "cc_admin_key";
  function ccAdminKey() { try { return localStorage.getItem(ADMIN_KEY_LS) || ""; } catch (_) { return ""; } }
  function ccSetAdminKey(v) { try { v ? localStorage.setItem(ADMIN_KEY_LS, v) : localStorage.removeItem(ADMIN_KEY_LS); } catch (_) {} }
  async function ccAdminFetch(op, params) {
    const room = window.__ccRoomId && window.__ccRoomId();
    const token = window.__ccSeatToken && window.__ccSeatToken();
    const key = ccAdminKey();
    const armed = tableArmed();
    if (!room) return { ok: false, error: "no active game, join a game first" };
    if (!key && !armed) return { ok: false, error: "no admin key set" };
    const url = window.__ccApiUrl ? window.__ccApiUrl("/api/rooms/" + room + "/admin_mod") : ("/api/rooms/" + room + "/admin_mod");
    try {
      const res = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ admin_key: key, seat_token: token, op, params: params || {} }) });
      return await res.json();
    } catch (e) {
      try {
        window.CCErrorLog?.report("api_fetch_failed", {
          path: "/api/rooms/" + room + "/admin_mod",
          method: "POST",
          op,
          error: e && (e.name || e.message || e)
        }, "warn");
      } catch (_) {}
      return { ok: false, error: String((e && e.message) || e) };
    }
  }
  const entryTiles = (entries) => (Array.isArray(entries) && entries.length) ? entries.map(e => cardTile(firstFace(e), e.label)).join("") : '<span class="cc-desc">none</span>';
  const speciesOf = (e) => { const f = firstFace(e); return (f && f.species) || ""; };
  const pointsOf  = (e) => { const f = firstFace(e); return (f && f.points != null) ? f.points : ""; };

  // One-time key prompt. The panel only opens for the admin account; the key is
  // asked once (on open, or via the 🔑 header button) and cached for the rest of
  // the session, so every server-backed tool then runs silently.
  function promptKey() {
    const v = prompt("Enter the Current Controller admin key:", ccAdminKey() || "");
    if (v !== null) ccSetAdminKey(v.trim());
    return ccAdminKey();
  }
  // Render a server-tool error; offer a one-click re-enter on auth failures.
  function serverErr(el, id, error) {
    const auth = /authoriz|admin key/i.test(String(error || ""));
    el.innerHTML = `<div class="cc-note">${esc(error || "request failed")}${auth ? ` <button class="cc-btn" data-cc-rekey style="margin-left:8px">Re-enter key</button>` : ""}</div>`;
    if (auth) el.querySelector("[data-cc-rekey]")?.addEventListener("click", () => { promptKey(); renderBody(id); });
  }

  // Inline searchable card picker (used to choose a card from the deck / a hand).
  function ccPick(title, entries, onPick) {
    const modal = document.createElement("div");
    modal.className = "cc-pick";
    modal.innerHTML = `<div class="cc-pick-inner">
      <div class="cc-pick-hd"><b>${esc(title)}</b><button class="cc-btn" data-cc-pickclose>✕</button></div>
      <input class="cc-input" data-cc-picksearch placeholder="Search by name, species, points…" style="margin:8px 0">
      <div class="cc-pick-grid"></div>
    </div>`;
    (overlay || document.body).appendChild(modal);
    const grid = modal.querySelector(".cc-pick-grid");
    const search = modal.querySelector("[data-cc-picksearch]");
    function paint(f) {
      f = (f || "").toLowerCase();
      const list = (entries || []).filter(e => {
        if (!f) return true;
        const fc = firstFace(e) || {};
        return ((fc.name || "") + " " + (fc.species || "") + " " + (e.label || "") + " " + (fc.points != null ? fc.points : "")).toLowerCase().includes(f);
      });
      grid.innerHTML = list.length ? list.map((e, i) =>
        `<div class="cc-pick-cell" data-uid="${e.entry_uid}">${cardTile(firstFace(e), e.label)}</div>`).join("")
        : `<div class="cc-desc">No matches.</div>`;
      grid.querySelectorAll(".cc-pick-cell").forEach(c => c.addEventListener("click", () => {
        const uid = Number(c.getAttribute("data-uid"));
        document.body.contains(modal) && modal.remove();
        onPick(uid);
      }));
    }
    search.addEventListener("input", () => paint(search.value));
    modal.querySelector("[data-cc-pickclose]").addEventListener("click", () => modal.remove());
    modal.addEventListener("click", (e) => { if (e.target === modal) modal.remove(); });
    paint("");
  }

  // Count remaining copies of each species in a list of deck entries.
  function copiesBySpecies(deck) {
    const m = {};
    (deck || []).forEach(e => { const s = speciesOf(e) || (firstFace(e) || {}).name || "?"; m[s] = (m[s] || 0) + 1; });
    return m;
  }

  // ── Full card catalog, every card, BOTH faces (for the add / mint pickers) ──
  // Fixes "I only see the lefts not the rights / ups not the downs": the old
  // pickers drew from the live DECK (one face per card, only remaining copies).
  // The catalog is the whole game: every card, both orientations.
  let _ccCatalog = null;
  async function ccLoadCatalog() {
    if (_ccCatalog) return _ccCatalog;
    const r = await ccAdminFetch("catalog", {});
    _ccCatalog = (r && r.ok && Array.isArray(r.catalog)) ? r.catalog : [];
    return _ccCatalog;
  }
  // Flatten the catalog to one tile per FACE so BOTH orientations (left+right,
  // up+down) of every card are visible & selectable. Each tile carries the
  // card's canonical entry_uid, that's what gets minted.
  function ccCatalogFaceTiles(catalog) {
    const tiles = [];
    (catalog || []).forEach(e => {
      const faces = Array.isArray(e.faces) ? e.faces : [];
      if (!faces.length) { tiles.push({ entry_uid: e.entry_uid, face: null, label: e.label }); return; }
      faces.forEach(f => tiles.push({ entry_uid: e.entry_uid, face: f, label: e.label }));
    });
    return tiles;
  }
  // Searchable picker over the FULL catalog (both faces). Calls onPick(entryUid).
  async function ccPickCatalog(title, onPick) {
    const tiles = ccCatalogFaceTiles(await ccLoadCatalog());
    const modal = document.createElement("div");
    modal.className = "cc-pick";
    modal.innerHTML = `<div class="cc-pick-inner">
      <div class="cc-pick-hd"><b>${esc(title)}</b><button class="cc-btn" data-cc-pickclose>✕</button></div>
      <div class="cc-desc">Every card, both faces (← → and ↑ ↓). ${tiles.length} faces.</div>
      <input class="cc-input" data-cc-picksearch placeholder="Search by name, species…" style="margin:8px 0">
      <div class="cc-pick-grid"></div>
    </div>`;
    (overlay || document.body).appendChild(modal);
    const grid = modal.querySelector(".cc-pick-grid");
    const search = modal.querySelector("[data-cc-picksearch]");
    function paint(f) {
      f = (f || "").toLowerCase();
      const list = tiles.filter(t => {
        if (!f) return true;
        const fc = t.face || {};
        return ((fc.name || "") + " " + (fc.species || "") + " " + (t.label || "")).toLowerCase().includes(f);
      });
      grid.innerHTML = list.length ? list.map((t, i) =>
        `<div class="cc-pick-cell" data-i="${i}">${cardTile(t.face, t.label)}</div>`).join("")
        : `<div class="cc-desc">No matches.</div>`;
      grid.querySelectorAll(".cc-pick-cell").forEach(c => c.addEventListener("click", () => {
        const t = list[Number(c.getAttribute("data-i"))];
        document.body.contains(modal) && modal.remove();
        onPick(t.entry_uid);
      }));
    }
    search.addEventListener("input", () => paint(search.value));
    modal.querySelector("[data-cc-pickclose]").addEventListener("click", () => modal.remove());
    modal.addEventListener("click", (e) => { if (e.target === modal) modal.remove(); });
    paint("");
  }
  // Ask how many copies to mint, the "flood" control. Returns a clamped int in
  // 1..50, or 0 if the admin cancelled.
  function ccAskCount(promptText) {
    const v = prompt(promptText || "How many copies? (1–50)", "1");
    if (v == null) return 0;
    let n = parseInt(v, 10);
    if (!Number.isFinite(n) || n < 1) n = 1;
    return Math.min(n, 50);
  }

  const BODY = {
    // ── Game State Viewer ─────────────────────────────────────────
    state_viewer(el) {
      const p = payload();
      if (!p) { el.innerHTML = `<div class="cc-note">No active game state. Join a game to populate this.</div>`; return; }
      const st = p.state || {};
      const conn = (window.__ccConnInfo && window.__ccConnInfo()) || {};
      const players = Array.isArray(st.players) ? st.players : [];
      const mi = myIndex(p);
      const deck = st.deck_remaining ?? st.deck_size ?? st.deck_count ?? (Array.isArray(st.deck) ? st.deck.length : "-");
      const eg = st.end_game || {};
      const pool = Array.isArray(st.pool) ? st.pool : [];
      let h = "";
      h += `<div class="cc-sub">Turn & Sync</div><div class="cc-grid">
        <div class="cc-kv"><span>Current player</span><b>${esc(st.current_player || "-")}</b></div>
        <div class="cc-kv"><span>Round</span><b>${esc(st.round_count ?? "-")}</b></div>
        <div class="cc-kv"><span>Deck remaining</span><b>${esc(deck)}</b></div>
        <div class="cc-kv"><span>Pool size</span><b>${pool.length}</b></div>
        <div class="cc-kv"><span>State version</span><b>${esc(conn.version ?? "-")}</b></div>
        <div class="cc-kv"><span>Connection</span><b>${conn.sse ? "SSE" : (conn.polling ? "Polling" : "-")}</b></div>
        <div class="cc-kv"><span>End Game card</span><b>${esc(eg.end_game_uid ?? eg.location ?? (eg.triggered ? "triggered" : "in deck"))}</b></div>
        <div class="cc-kv"><span>Room</span><b>${esc(conn.room || p.room?.room_id || "-")}</b></div>
      </div>`;
      h += `<div class="cc-sub">Pool (${pool.length})</div><div class="cc-cards">${pool.length ? pool.map(e => cardTile(firstFace(e), e.label)).join("") : '<span class="cc-desc">empty</span>'}</div>`;
      h += `<div class="cc-sub">Players</div>`;
      players.forEach(pl => {
        const mine = pl.index === mi;
        const hc = pl.hand_count ?? (Array.isArray(pl.hand) ? pl.hand.length : 0);
        const oceans = Array.isArray(pl.board) ? pl.board.length : (pl.board_ocean_count ?? "-");
        h += `<div class="cc-kv"><span>P${(pl.index ?? 0) + 1} · ${esc(pl.name)}${mine ? " (you)" : ""}</span><b>${esc(pl.score ?? 0)} pts · ${esc(hc)} cards · ${esc(oceans)} oceans${pl.strategy ? " · " + esc(pl.strategy) : ""}</b></div>`;
      });
      const me = players.find(pl => pl.index === mi);
      if (me && Array.isArray(me.hand) && me.hand.length) {
        h += `<div class="cc-sub">Your hand</div><div class="cc-cards">${me.hand.map(e => cardTile(firstFace(e), e.label)).join("")}</div>`;
      }
      h += `<div class="cc-btnrow"><button class="cc-btn" id="cc-sv-copy">📋 Copy Debug Report</button></div>`;
      el.innerHTML = h;
      const cp = el.querySelector("#cc-sv-copy");
      if (cp) cp.addEventListener("click", () => {
        const report = buildDebugReport(p, conn);
        copyText(report, cp);
      });
    },

    // ── Enemy Hand Viewer (Phase 1: view-only) ────────────────────
    // ── Enemy Hand Viewer (full reveal + edit) ────────────────────
    async enemy_hands(el) {
      el.innerHTML = `<div class="cc-desc">Revealing hands…</div>`;
      const r = await ccAdminFetch("reveal", {});
      if (!r.ok) { serverErr(el, "enemy_hands", r.error || "reveal failed"); return; }
      const mi = myIndex(payload());
      let h = `<div class="cc-note">Full reveal, every player's real hand. <b>Add / flood</b> mints fresh copies of ANY card (both faces, left/right and up/down) and can drop many copies at once. Edits apply on the match thread (works best during a human turn).</div>`;
      (r.players || []).forEach(pl => {
        const mine = pl.index === mi;
        h += `<div class="cc-seat"><div class="cc-seat-hd">P${(pl.index ?? 0) + 1} · ${esc(pl.name)}${mine ? " (you)" : ""} <span class="cc-desc">${(pl.hand || []).length} cards · ${esc(pl.score)} pts</span></div>`;
        h += `<div class="cc-cards">${entryTiles(pl.hand)}</div>`;
        h += `<div class="cc-btnrow">
          <button class="cc-btn" data-act="add" data-seat="${pl.index}">＋ Add / flood card…</button>
          <button class="cc-btn" data-act="remove" data-seat="${pl.index}">－ Remove card…</button>
          <button class="cc-btn cc-danger" data-act="clear" data-seat="${pl.index}">Clear hand</button>
          ${mine ? "" : `<button class="cc-btn" data-act="copy" data-seat="${pl.index}" data-myseat="${mi}">Copy to my hand</button>`}
        </div></div>`;
      });
      el.innerHTML = h;
      const refresh = () => renderBody("enemy_hands");
      el.querySelectorAll("[data-act]").forEach(b => b.addEventListener("click", async () => {
        const seat = Number(b.getAttribute("data-seat"));
        const act = b.getAttribute("data-act");
        if (act === "add") {
          ccPickCatalog("Add which card to P" + (seat + 1) + "? (any card · both faces)", async (uid) => {
            const count = ccAskCount("How many copies to give P" + (seat + 1) + "?  (flood: 1–50)");
            if (!count) return;
            const res = await ccAdminFetch("mint", { seat, uid, dest: "hand", count });
            if (!res || !res.ok) alert("Add failed: " + (res && res.error));
            else if (count > 1) alert("Added " + (res.minted || count) + " copies to P" + (seat + 1) + "'s hand.");
            refresh();
          });
        } else if (act === "remove") {
          const pl = (r.players || []).find(x => x.index === seat);
          ccPick("Remove which card from P" + (seat + 1) + "?", pl ? pl.hand : [], async (uid) => { await ccAdminFetch("hand_remove", { seat, uid }); refresh(); });
        } else if (act === "clear") {
          if (confirm("Clear P" + (seat + 1) + "'s entire hand to discard?")) { await ccAdminFetch("hand_clear", { seat }); refresh(); }
        } else if (act === "copy") {
          const myseat = Number(b.getAttribute("data-myseat"));
          const res = await ccAdminFetch("hand_copy_to_me", { seat, my_seat: myseat });
          if (res && res.ok) alert(`Copied ${res.copied} card(s) from the deck` + (res.unavailable ? `, ${res.unavailable} unavailable (no deck copy).` : "."));
          refresh();
        }
      }));
    },

    // ── What If Menu (preview estimates + real Apply) ─────────────
    what_if(el) {
      const p = payload();
      if (!p) { el.innerHTML = `<div class="cc-note">No active game.</div>`; return; }
      const st = p.state || {};
      const mi = myIndex(p);
      const me = (st.players || []).find(pl => pl.index === mi);
      const pool = Array.isArray(st.pool) ? st.pool : [];
      const legal = (window.__ccLegalActions && window.__ccLegalActions()) || [];
      let h = `<div class="cc-note">Estimates are quick previews. <b>Apply This Move</b> commits a <i>real</i>, legal move on your turn through the normal game pipeline.</div>`;
      h += `<div class="cc-sub">What if I play…</div><div class="cc-grid">`;
      const hand = (me && Array.isArray(me.hand)) ? me.hand : [];
      if (hand.length) hand.forEach(e => {
        const f = firstFace(e); const pts = f && f.points != null ? f.points : "?";
        h += `<div class="cc-kv"><span>${esc((f && f.name) || e.label)}</span><b>≈ +${esc(pts)} pts</b></div>`;
      });
      else h += `<span class="cc-desc">No cards in hand.</span>`;
      h += `</div>`;
      h += `<div class="cc-sub">What if I take from the pool…</div><div class="cc-grid">`;
      if (pool.length) pool.forEach(e => { const f = firstFace(e); const pts = f && f.points != null ? f.points : "?"; h += `<div class="cc-kv"><span>${esc((f && f.name) || e.label)}</span><b>+1 card · ${esc(pts)} pts</b></div>`; });
      else h += `<span class="cc-desc">Pool empty.</span>`;
      h += `</div>`;
      h += `<div class="cc-sub">Apply a real move (your legal moves right now)</div>`;
      if (legal.length) {
        h += `<div id="cc-wi-legal">` + legal.map((a, i) =>
          `<div class="cc-kv"><span>${esc(ccActionLabel(a))}</span><button class="cc-btn" data-apply="${i}">✓ Apply This Move</button></div>`).join("") + `</div>`;
      } else {
        h += `<div class="cc-desc">No legal moves right now, it's not your turn, or you're mid-resolution.</div>`;
      }
      el.innerHTML = h;
      el.querySelectorAll("[data-apply]").forEach(b => b.addEventListener("click", () => {
        const i = Number(b.getAttribute("data-apply"));
        if (confirm("Apply this real move now?")) { window.__ccApplyAction && window.__ccApplyAction(i); close(); }
      }));
    },

    // ── Recent Bug Log ────────────────────────────────────────────
    bug_log(el) {
      let h = `<div class="cc-btnrow"><button class="cc-btn" id="cc-bug-copy">📋 Copy Bug Report</button><button class="cc-btn cc-danger" id="cc-bug-clear">Clear Bug Log</button></div>`;
      if (!bugLog.length) h += `<div class="cc-desc">No issues recorded yet this session. Errors, console.error calls and rejected promises will appear here while this is ON.</div>`;
      else h += `<div class="cc-loglist">` + bugLog.map(b =>
        `<div class="cc-logitem"><div class="m">${esc(b.message)}</div><div class="meta">turn ${esc(b.turn)} · current: ${esc(b.currentPlayer)} · affected: ${esc(b.affectedPlayer)} · action: ${esc(b.action)} · ${esc(b.ts)}${b.explanation ? " · " + esc(b.explanation) : ""}</div></div>`
      ).join("") + `</div>`;
      el.innerHTML = h;
      el.querySelector("#cc-bug-copy")?.addEventListener("click", (e) => copyText(JSON.stringify(bugLog, null, 2), e.target));
      el.querySelector("#cc-bug-clear")?.addEventListener("click", () => { bugLog.length = 0; renderBody("bug_log"); });
    },

    // ── Bot Brain Viewer (reads recorded bot decisions) ───────────
    async bot_brain(el) {
      el.innerHTML = `<div class="cc-desc">Reading bot brain…</div>`;
      const r = await ccAdminFetch("bot_brain", {});
      if (!r.ok) { serverErr(el, "bot_brain", r.error || "failed"); return; }
      const brain = r.brain || {};
      const seats = Object.keys(brain);
      let h = "";
      if (!seats.length) { el.innerHTML = `<div class="cc-desc">No bot decisions recorded yet. This fills in as bots take turns.</div>`; return; }
      seats.forEach(sk => {
        const b = brain[sk];
        h += `<div class="cc-seat"><div class="cc-seat-hd">🧠 P${(Number(sk) + 1)} · ${esc(b.name)} <span class="cc-desc">${esc(b.strategy || "")}</span></div>`;
        h += `<div class="cc-kv"><span>Wants to play</span><b>${esc(b.chosen ? b.chosen.label : "-")}</b></div>`;
        if (b.reason) h += `<div class="cc-desc" style="margin:4px 0">${esc(b.reason)}</div>`;
        h += `<div class="cc-sub">Top scored alternatives</div>`;
        (b.candidates || []).forEach((c, i) => {
          h += `<div class="cc-kv"><span>${i + 1}. ${esc(c.label)}</span><b>${esc(c.score)}</b></div>`;
        });
        h += `<div class="cc-desc" style="margin-top:6px">Use <b>Bot Move Override</b> to force a different move for this seat.</div>`;
        h += `</div>`;
      });
      el.innerHTML = h;
    },

    // ── Bot Move Override (arm a forced move for a bot seat) ───────
    async bot_override(el) {
      el.innerHTML = `<div class="cc-desc">Loading bot seats…</div>`;
      const r = await ccAdminFetch("bot_brain", {});
      if (!r.ok) { serverErr(el, "bot_override", r.error || "failed"); return; }
      let h = `<div class="cc-note">Pick a bot's next move from its current legal actions. The bot uses it on its next turn, then play continues normally.</div>`;
      const brain = r.brain || {};
      const seats = Object.keys(brain);
      if (!seats.length) { el.innerHTML = h + `<div class="cc-desc">No bot turns observed yet, let a bot reach its turn, then return here.</div>`; return; }
      seats.forEach(sk => {
        const b = brain[sk];
        h += `<div class="cc-seat" data-seat="${sk}"><div class="cc-seat-hd">🎮 P${(Number(sk) + 1)} · ${esc(b.name)}</div>`;
        h += `<div class="cc-desc">Currently wants: ${esc(b.chosen ? b.chosen.label : "-")}</div>`;
        h += `<div class="cc-sub">Force this seat to:</div><div id="cc-ovr-${sk}">`;
        (b.legal_actions || []).forEach((a, i) => {
          h += `<div class="cc-kv"><span>${esc(a.label)}</span><button class="cc-btn" data-ovr-seat="${sk}" data-ovr-idx="${i}">Force</button></div>`;
        });
        h += `</div><div class="cc-btnrow"><button class="cc-btn cc-danger" data-ovr-clear="${sk}">Clear override</button></div></div>`;
      });
      el.innerHTML = h;
      el.querySelectorAll("[data-ovr-idx]").forEach(btn => btn.addEventListener("click", async () => {
        const seat = Number(btn.getAttribute("data-ovr-seat"));
        const idx = Number(btn.getAttribute("data-ovr-idx"));
        const desc = ((brain[String(seat)] || {}).legal_actions || [])[idx] || null;
        const res = await ccAdminFetch("bot_override_arm", { seat, action_index: idx, action: desc });
        btn.textContent = res && res.ok ? "Armed ✓" : "Failed";
        setTimeout(() => { btn.textContent = "Force"; }, 1200);
      }));
      el.querySelectorAll("[data-ovr-clear]").forEach(btn => btn.addEventListener("click", async () => {
        const seat = Number(btn.getAttribute("data-ovr-clear"));
        await ccAdminFetch("bot_override_arm", { seat, action_index: null });
        btn.textContent = "Cleared";
        setTimeout(() => { btn.textContent = "Clear override"; }, 1200);
      }));
    },

    // ── Full Deck Picker (browse the real deck + place any card) ───
    async deck_picker(el) {
      el.innerHTML = `<div class="cc-desc">Loading every card…</div>`;
      const r = await ccAdminFetch("reveal", {});
      if (!r.ok) { serverErr(el, "deck_picker", r.error || "failed"); return; }
      const liveDeck = r.deck || [];
      const copies = copiesBySpecies(liveDeck);
      const tiles = ccCatalogFaceTiles(await ccLoadCatalog());
      const mi = myIndex(payload());
      let h = `<div class="cc-note">Every card in the game, both faces (← → and ↑ ↓). Click any card, then choose where to drop a <b>fresh copy</b>. Pick <b>hand</b> to flood with many copies. (${liveDeck.length} cards left in the live deck.)</div>`;
      h += `<input class="cc-input" id="cc-dp-search" placeholder="Search by name, species…" style="margin-bottom:10px">`;
      h += `<div class="cc-sub">Remaining copies in the live deck</div><div class="cc-grid">` +
        Object.keys(copies).sort().map(s => `<div class="cc-kv"><span>${esc(s)}</span><b>${copies[s]}</b></div>`).join("") + `</div>`;
      h += `<div class="cc-sub">All cards (${tiles.length} faces)</div><div class="cc-cards" id="cc-dp-grid"></div>`;
      el.innerHTML = h;
      const grid = el.querySelector("#cc-dp-grid");
      const search = el.querySelector("#cc-dp-search");
      function paint(f) {
        f = (f || "").toLowerCase();
        const list = tiles.filter(t => { const fc = t.face || {}; return !f || ((fc.name || "") + " " + (fc.species || "") + " " + (t.label || "")).toLowerCase().includes(f); });
        grid.innerHTML = list.length ? list.map((t, i) => `<div data-i="${i}" style="cursor:pointer">${cardTile(t.face, t.label)}</div>`).join("") : `<span class="cc-desc">No matches.</span>`;
        grid.querySelectorAll("[data-i]").forEach(c => c.addEventListener("click", async () => {
          const t = list[Number(c.getAttribute("data-i"))];
          const dest = prompt("Drop a fresh copy where? Type one of:\nhand · pool · deck_top · deck_bottom · discard", "hand");
          if (!dest) return;
          const params = { uid: t.entry_uid, dest: dest.trim() };
          if (params.dest === "hand") {
            params.seat = mi;
            params.count = ccAskCount("How many copies? (flood: 1–50)");
            if (!params.count) return;
          }
          const res = await ccAdminFetch("mint", params);
          if (res && res.ok) renderBody("deck_picker");
          else alert("Place failed: " + (res && res.error));
        }));
      }
      search.addEventListener("input", () => paint(search.value));
      paint("");
    },

    // ── Force Pool Cards (clear / refill / replace / force) ────────
    async force_pool(el) {
      el.innerHTML = `<div class="cc-desc">Loading pool…</div>`;
      const r = await ccAdminFetch("reveal", {});
      if (!r.ok) { serverErr(el, "force_pool", r.error || "failed"); return; }
      const pool = r.pool || [];
      const deck = r.deck || [];
      let h = `<div class="cc-note">Shape the pool directly. Removed pool cards go to the discard pile; added cards are pulled from the deck.</div>`;
      h += `<div class="cc-sub">Current pool (${pool.length})</div><div class="cc-cards">`;
      pool.forEach(e => { h += `<div data-poolrm="${e.entry_uid}" title="Click to remove" style="cursor:pointer">${cardTile(firstFace(e), e.label)}</div>`; });
      if (!pool.length) h += `<span class="cc-desc">empty</span>`;
      h += `</div>`;
      h += `<div class="cc-btnrow">
        <button class="cc-btn cc-danger" data-fp="clear">Clear pool</button>
        <button class="cc-btn" data-fp="refill">Refill to 6</button>
        <button class="cc-btn" data-fp="add">＋ Force a card in…</button>
      </div>`;
      el.innerHTML = h;
      const refresh = () => renderBody("force_pool");
      el.querySelectorAll("[data-poolrm]").forEach(c => c.addEventListener("click", async () => {
        await ccAdminFetch("pool_remove", { uid: Number(c.getAttribute("data-poolrm")) }); refresh();
      }));
      el.querySelector('[data-fp="clear"]')?.addEventListener("click", async () => { if (confirm("Clear the entire pool?")) { await ccAdminFetch("pool_clear", {}); refresh(); } });
      el.querySelector('[data-fp="refill"]')?.addEventListener("click", async () => { await ccAdminFetch("pool_refill", { count: 6 }); refresh(); });
      el.querySelector('[data-fp="add"]')?.addEventListener("click", () => {
        ccPickCatalog("Force which card into the pool? (any card · both faces)", async (uid) => { await ccAdminFetch("mint", { uid, dest: "pool", count: 1 }); refresh(); });
      });
    },
  };

  // Best-effort label for a client-side legal action object (What If apply list).
  function ccActionLabel(a) {
    if (!a) return "move";
    const k = a.kind || "move";
    if (k === "draw") return a.draw_from_pool ? "Draw from pool" : "Draw from deck";
    if (k === "end_turn") return "End turn";
    const name = a.face_name || a.card_name || (a.face_uid != null ? "#" + a.face_uid : "");
    return [k, name].filter(Boolean).join(" ");
  }

  function buildDebugReport(p, conn) {
    const st = p.state || {};
    const lines = [];
    lines.push("=== Currents and Critters, Debug Report ===");
    lines.push("time: " + new Date().toISOString());
    lines.push("room: " + (conn.room || p.room?.room_id || "-") + "  version: " + (conn.version ?? "-") + "  conn: " + (conn.sse ? "SSE" : conn.polling ? "Polling" : "-"));
    lines.push("current_player: " + (st.current_player || "-") + "  round: " + (st.round_count ?? "-"));
    lines.push("deck_remaining: " + (st.deck_remaining ?? st.deck_size ?? st.deck_count ?? "-"));
    lines.push("end_game: " + JSON.stringify(st.end_game || {}));
    lines.push("pool: " + (Array.isArray(st.pool) ? st.pool.map(e => e.label).join(" | ") : "-"));
    (st.players || []).forEach(pl => {
      lines.push(`P${(pl.index ?? 0) + 1} ${pl.name}: ${pl.score ?? 0} pts, ${pl.hand_count ?? (pl.hand ? pl.hand.length : 0)} cards${pl.strategy ? ", " + pl.strategy : ""}`);
    });
    lines.push("--- raw state ---");
    try { lines.push(JSON.stringify(p, null, 2)); } catch (_) { lines.push("(unserializable)"); }
    return lines.join("\n");
  }

  function copyText(text, btn) {
    const done = () => { if (btn) { const o = btn.textContent; btn.textContent = "✓ Copied"; setTimeout(() => { btn.textContent = o; }, 1400); } };
    try {
      navigator.clipboard.writeText(text).then(done, () => fallback());
    } catch (_) { fallback(); }
    function fallback() {
      const ta = document.createElement("textarea");
      ta.value = text; document.body.appendChild(ta); ta.select();
      try { document.execCommand("copy"); done(); } catch (_) {}
      document.body.removeChild(ta);
    }
  }

  // ════════════════════════════════════════════════════════════════
  //  MENU INTEGRATION + ADMIN VISIBILITY
  // ════════════════════════════════════════════════════════════════
  function ensureMenuButton() {
    const drop = document.getElementById("pv-menu-drop");
    if (!drop) return;
    let item = document.getElementById("cc-menu-item");
    const admin = mayControl();
    if (admin && !item) {
      const div = document.createElement("div");
      div.className = "pv-menu-divider";
      div.id = "cc-menu-divider";
      item = document.createElement("button");
      item.className = "pv-menu-item";
      item.id = "cc-menu-item";
      item.innerHTML = "🌊 Current Controller";
      item.addEventListener("click", () => { document.getElementById("pv-menu-drop")?.classList.remove("open"); open(); });
      const back = document.getElementById("pv-back-btn");
      if (back) { drop.insertBefore(div, back); drop.insertBefore(item, back); }
      else { drop.appendChild(div); drop.appendChild(item); }
    }
    // Hard hide / show based on current auth.
    if (item) item.style.display = admin ? "" : "none";
    const div = document.getElementById("cc-menu-divider");
    if (div) div.style.display = admin ? "" : "none";
    if (!admin) {
      close();
    }
  }


  // Re-evaluate admin visibility continuously (auth can resolve after load,
  // and a sign-out must immediately strip the button + any open panel).
  setInterval(ensureMenuButton, 1200);
  ensureMenuButton();

  // Live-refresh the cheap client-side tools while the panel is visible.
  // Server-backed tools (enemy_hands, deck_picker, force_pool, bot_*) refresh
  // on demand / after each action, not on a timer, to avoid network spam.
  setInterval(() => {
    if (!panelOpen()) return;
    if (isOn("state_viewer")) renderBody("state_viewer");
  }, 1000);

  // ESC closes the panel.
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && panelOpen()) close(); });

})();
