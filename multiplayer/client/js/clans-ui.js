/* Currents & Critters — Clan System UI (self-contained module).
 *
 * Renders the whole "Clans" Player-Home tab into #cc-clans-root. Talks only to
 * the server-authoritative /api/clan/* API through the window.__ccClans bridge
 * (set up in preview-app.js). The server owns every rule — points, caps,
 * cooldowns, permissions, seasons; this file renders state + sends intents.
 * Ocean-light styling reuses the Player-Home ph-* look (Nunito, pill buttons,
 * 18px cards, #2e8fe0 blues) so the tab feels native.
 */
(function () {
  "use strict";

  function bridge() { return window.__ccClans; }
  if (!bridge() || !bridge().ENABLED) return;

  const $  = (sel, root) => (root || document).querySelector(sel);
  const el = (tag, cls, html) => { const n = document.createElement(tag); if (cls) n.className = cls; if (html != null) n.innerHTML = html; return n; };
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const toast = (m, t) => bridge().toast(m, t);
  const avSrc = (u) => { try { return bridge().avSrc(u); } catch (_) { return u; } };

  const C = {
    view: "home",            // home | browse | create | clan | leaderboard | results
    home: null,              // last /home payload
    clan: null,              // last /get payload (open clan profile)
    clanTab: "overview",     // overview | members | chat | events | log | settings
    viewingClanId: null,     // which clan the "clan" view shows (mine or another)
    chatSince: 0, chatMsgs: [], chatTimer: null,
    countdownTimer: null,
    memberSort: "points",
    claimedRooms: {},        // roomId → true (end-game claim dedup, this session)
    busy: false,
  };

  // ── API ────────────────────────────────────────────────────────────────────
  async function post(action, extra) {
    const b = bridge();
    const body = Object.assign({}, extra || {});
    body.idToken = await b.idToken();
    if (!body.idToken) return { ok: false, error: "unauthorized" };
    return b.post("/api/clan/" + action, body);
  }

  const ERR = {
    unauthorized: "Sign in to use Clans.",
    firestore_unavailable: "Clans are temporarily unavailable — try again shortly.",
    bad_name: "That clan name can't be used.",
    name_taken: "That clan name is already taken.",
    already_in_clan: "You're already in a clan — leave it first.",
    already_member: "Already a member of that clan.",
    clan_full: "That clan is full (20 members max).",
    invite_required: "That clan is invite only.",
    request_required: "That clan requires a join request.",
    transfer_first: "Transfer ownership before leaving your clan.",
    no_permission: "You don't have permission to do that.",
    owner_only: "Only the clan owner can do that.",
    outranked: "You can't manage a member with an equal or higher role.",
    cooldown: "You must wait 24 hours after switching clans before earning Clan Points.",
    weekly_cap: "Weekly Clan Point cap reached (150).",
    already_claimed: "Already counted for this game.",
    muted: "You're muted in clan chat right now.",
    no_clan: "Clan not found.",
    server_error: "Something went wrong — try again.",
  };
  const errMsg = (e) => ERR[e] || "Something went wrong (" + esc(e || "unknown") + ").";

  // ── Formatting helpers ─────────────────────────────────────────────────────
  function fmtDate(ts) {
    if (!ts) return "—";
    try { return new Date(ts * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }); }
    catch (_) { return "—"; }
  }
  function fmtDateTime(ts) {
    if (!ts) return "—";
    try { return new Date(ts * 1000).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }); }
    catch (_) { return "—"; }
  }
  function fmtAgo(ts) {
    if (!ts) return "—";
    const s = Math.max(0, Math.floor(Date.now() / 1000 - ts));
    if (s < 90) return "just now";
    if (s < 3600) return Math.floor(s / 60) + "m ago";
    if (s < 86400) return Math.floor(s / 3600) + "h ago";
    return Math.floor(s / 86400) + "d ago";
  }
  function countdownParts(endTs) {
    let s = Math.max(0, endTs - Math.floor(Date.now() / 1000));
    const d = Math.floor(s / 86400); s -= d * 86400;
    const h = Math.floor(s / 3600);  s -= h * 3600;
    const m = Math.floor(s / 60);    s -= m * 60;
    return { d, h, m, s };
  }
  const roleLabel = (r) => ({ owner: "👑 Owner", captain: "⚓ Captain", recruiter: "📯 Recruiter", member: "🐟 Member" }[r] || "🐟 Member");
  const privacyLabel = (p) => ({ public: "🌊 Public — anyone can join", request: "✉️ Request to Join", invite: "🔒 Invite Only" }[p] || p);
  const privacyShort = (p) => ({ public: "🌊 Public", request: "✉️ Request", invite: "🔒 Invite" }[p] || p);

  // ── Styles (injected once; ccC- prefix, matches the ph-* light ocean look) ─
  const CSS = `
  #cc-clans-root { font-family: "Nunito", sans-serif; color: #23445f; }
  #cc-clans-root .ccC-topbtns { display:flex; gap:8px; flex-wrap:wrap; padding: 12px 16px 4px; }
  #cc-clans-root .ccC-btn {
    padding: 7px 16px; border-radius: 999px; font-size: 12.5px; font-weight: 800;
    border: 1.5px solid rgba(169,203,230,.8); background: #f5faff; color: #4d6587;
    cursor: pointer; font-family: "Nunito", sans-serif; transition: all .12s ease; line-height: 1.25;
  }
  #cc-clans-root .ccC-btn:hover { background:#eaf4ff; }
  #cc-clans-root .ccC-btn.pri { background: linear-gradient(180deg,#2e8fe0 0%,#1c70cc 100%); color:#fff; border-color:#1c70cc; }
  #cc-clans-root .ccC-btn.pri:hover { filter: brightness(1.06); }
  #cc-clans-root .ccC-btn.danger { color:#b2452f; border-color:#e8bdb2; background:#fff6f3; }
  #cc-clans-root .ccC-btn.tiny { padding: 3px 10px; font-size: 11px; }
  #cc-clans-root .ccC-btn[disabled] { opacity:.55; cursor: default; }
  .ccC-season {
    display:flex; align-items:center; justify-content:space-between; gap:10px; flex-wrap:wrap;
    margin: 10px 16px 0; padding: 12px 16px; border-radius: 14px;
    background: linear-gradient(135deg,#e8f4ff 0%, #d8ecfd 55%, #cfe6fb 100%);
    border: 1px solid rgba(169,203,230,.8);
  }
  .ccC-season-name { font-size: 15px; font-weight: 900; color:#1c5f9e; }
  .ccC-season-sub { font-size: 11.5px; color:#5b7fa3; font-weight:700; }
  .ccC-count { display:flex; gap:6px; }
  .ccC-count b { display:inline-block; min-width: 40px; text-align:center; background:#fff;
    border:1px solid rgba(169,203,230,.9); border-radius:10px; padding: 4px 4px 2px; font-size:15px; color:#1c5f9e; }
  .ccC-count b span { display:block; font-size:9px; font-weight:800; letter-spacing:.5px; color:#7a9db8; text-transform:uppercase; }
  .ccC-podium { display:flex; gap:10px; align-items:stretch; justify-content:center; flex-wrap:wrap; padding: 14px 16px 4px; }
  .ccC-pod {
    flex:1 1 150px; max-width: 210px; text-align:center; border-radius: 16px; padding: 14px 10px 12px;
    border: 1.5px solid; cursor:pointer; transition: transform .12s ease;
  }
  .ccC-pod:hover { transform: translateY(-2px); }
  .ccC-pod.g { order:2; border-color:#e6c14c; background: linear-gradient(180deg,#fff9e2,#fdf1c0); box-shadow: 0 6px 18px rgba(214,168,20,.18); }
  .ccC-pod.s { order:1; border-color:#c3cdd8; background: linear-gradient(180deg,#f7fafc,#e9eef4); }
  .ccC-pod.b { order:3; border-color:#d8a678; background: linear-gradient(180deg,#fdf3e9,#f7e2cd); }
  .ccC-pod .medal { font-size: 20px; }
  .ccC-pod img { width: 52px; height: 52px; border-radius: 50%; object-fit: cover; border: 2.5px solid #fff; box-shadow: 0 2px 8px rgba(35,100,165,.22); margin: 4px 0 2px; }
  .ccC-pod .nm { font-weight: 900; font-size: 13.5px; color:#23445f; }
  .ccC-pod .pts { font-size: 12px; font-weight:800; color:#1c5f9e; }
  .ccC-pod .rw { font-size: 10.5px; color:#7a9db8; font-weight:700; }
  .ccC-myclan { display:flex; align-items:center; gap:14px; margin: 12px 16px; padding: 14px 16px;
    border-radius: 16px; border: 1px solid rgba(169,203,230,.78); background:#fbfeff; cursor:pointer; }
  .ccC-myclan:hover { background:#f2f9ff; }
  .ccC-myclan img { width: 56px; height: 56px; border-radius: 50%; object-fit: cover; border: 2.5px solid #dcebf7; }
  .ccC-myclan .big { font-size: 16px; font-weight: 900; }
  .ccC-myclan .sub { font-size: 12px; color:#5b7fa3; font-weight:700; }
  .ccC-statgrid { display:grid; grid-template-columns: repeat(auto-fill,minmax(118px,1fr)); gap:8px; padding: 10px 16px; }
  .ccC-stat { border:1px solid #e3eef7; border-radius: 12px; padding: 8px 10px; background:#fbfeff; text-align:center; }
  .ccC-stat b { display:block; font-size: 16px; color:#1c5f9e; }
  .ccC-stat span { font-size: 10px; font-weight:800; letter-spacing:.4px; color:#7a9db8; text-transform:uppercase; }
  .ccC-sec { margin: 8px 16px 14px; border:1px solid #e3eef7; border-radius: 14px; background:#fdfeff; overflow:hidden; }
  .ccC-sec-h { padding: 9px 14px; font-size: 12px; font-weight: 900; letter-spacing:.4px; text-transform: uppercase;
    color:#4d6587; background:#f4f9fd; border-bottom:1px solid #e9f1f8; display:flex; justify-content:space-between; align-items:center; gap:8px;}
  .ccC-sec-b { padding: 10px 14px; font-size: 13px; }
  .ccC-tabs { display:flex; gap:6px; flex-wrap:wrap; padding: 10px 16px 0; }
  .ccC-goalbar { height: 9px; border-radius: 999px; background:#e4eef7; overflow:hidden; margin-top:6px; }
  .ccC-goalbar > i { display:block; height:100%; border-radius:999px; background: linear-gradient(90deg,#39b3e6,#1c70cc); transition: width .3s ease; }
  .ccC-member { display:flex; align-items:center; gap:10px; padding: 8px 12px; border-bottom: 1px solid #eef4fa; }
  .ccC-member:last-child { border-bottom:none; }
  .ccC-member img { width: 34px; height: 34px; border-radius: 50%; object-fit: cover; border:2px solid #e3eef7; }
  .ccC-member .who { flex:1 1 auto; min-width: 0; }
  .ccC-member .who .n { font-weight: 900; font-size: 13px; display:flex; align-items:center; gap:6px; flex-wrap:wrap; }
  .ccC-member .who .r { font-size: 10.5px; color:#7a9db8; font-weight:800; }
  .ccC-member .st { text-align:right; font-size: 11px; color:#5b7fa3; font-weight:700; white-space:nowrap; }
  .ccC-dot { width:8px; height:8px; border-radius:50%; display:inline-block; background:#c7d6e4; }
  .ccC-dot.on { background:#3fc26b; box-shadow: 0 0 0 3px rgba(63,194,107,.18); }
  .ccC-chip { display:inline-block; padding: 1px 8px; border-radius: 999px; font-size: 10px; font-weight: 900;
    background:#eaf4ff; color:#1c5f9e; border:1px solid #cfe4f7; }
  .ccC-chip.gold { background:#fdf3cd; color:#8a6a12; border-color:#ecd88f; }
  .ccC-chat { display:flex; flex-direction:column; height: 380px; }
  .ccC-chat-log { flex:1 1 auto; overflow-y:auto; padding: 10px 12px; display:flex; flex-direction:column; gap:7px; background:#f7fbfe; }
  .ccC-msg { max-width: 78%; padding: 7px 11px; border-radius: 14px; font-size: 13px; background:#fff;
    border:1px solid #e3eef7; align-self:flex-start; word-break:break-word; }
  .ccC-msg.me { align-self:flex-end; background:#dff0ff; border-color:#c8e2f8; }
  .ccC-msg .m-h { font-size: 10.5px; font-weight: 900; color:#5b7fa3; margin-bottom: 1px; display:flex; gap:6px; align-items:center;}
  .ccC-msg.sys { align-self:center; background:transparent; border:none; color:#7a9db8; font-size:11.5px; font-weight:700; font-style: italic; text-align:center; }
  .ccC-msg.ann { border-color:#ecd88f; background:#fffbea; }
  .ccC-chat-in { display:flex; gap:8px; padding: 9px 10px; border-top:1px solid #e9f1f8; background:#fff; }
  .ccC-chat-in input { flex:1 1 auto; border:1.5px solid rgba(169,203,230,.8); border-radius: 999px; padding: 8px 14px;
    font-family:"Nunito",sans-serif; font-size: 13px; outline:none; color:#23445f; background:#fbfeff; }
  .ccC-chat-in input:focus { border-color:#2e8fe0; }
  .ccC-inp, #cc-clans-root select, #cc-clans-root textarea {
    border:1.5px solid rgba(169,203,230,.8); border-radius: 12px; padding: 8px 12px;
    font-family:"Nunito",sans-serif; font-size: 13px; outline:none; color:#23445f; background:#fbfeff; }
  .ccC-inp:focus, #cc-clans-root textarea:focus { border-color:#2e8fe0; }
  .ccC-iconpick { display:grid; grid-template-columns: repeat(auto-fill,minmax(58px,1fr)); gap:8px; max-height: 240px;
    overflow-y:auto; padding: 10px; border:1px solid #e3eef7; border-radius: 12px; background:#fbfeff; }
  .ccC-iconpick .ic { text-align:center; cursor:pointer; border-radius: 12px; padding: 5px 2px; border:2px solid transparent; }
  .ccC-iconpick .ic:hover { background:#eef7ff; }
  .ccC-iconpick .ic.sel { border-color:#2e8fe0; background:#e4f2ff; }
  .ccC-iconpick .ic img { width: 42px; height: 42px; border-radius: 50%; object-fit:cover; }
  .ccC-iconpick .ic div { font-size: 9px; font-weight:800; color:#5b7fa3; margin-top:2px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .ccC-field { margin-bottom: 13px; }
  .ccC-field > label { display:block; font-size: 11px; font-weight: 900; letter-spacing:.4px; text-transform:uppercase; color:#4d6587; margin-bottom: 5px; }
  .ccC-hint { font-size: 11px; color:#7a9db8; font-weight: 700; margin-top: 4px; }
  .ccC-hint.bad { color:#b2452f; }
  .ccC-hint.good { color:#2e9e5b; }
  .ccC-pin { margin: 10px 16px 0; padding: 10px 14px; border-radius: 12px; background:#fffbea; border:1px solid #ecd88f;
    font-size: 12.5px; }
  .ccC-pin b { color:#8a6a12; }
  .ccC-empty { padding: 22px 10px; text-align: center; color:#7a9db8; font-weight: 700; font-size: 13px; }
  .ccC-activity { max-height: 320px; overflow-y:auto; }
  .ccC-activity .row { display:flex; gap:10px; padding: 7px 14px; border-bottom:1px solid #eef4fa; font-size: 12.5px; }
  .ccC-activity .row:last-child { border-bottom:none; }
  .ccC-activity .ts { color:#9db4c9; font-size: 11px; white-space:nowrap; font-weight:700; padding-top:1px; }
  .ccC-modal-bg { position:fixed; inset:0; background:rgba(12,40,66,.45); z-index: 9700; display:flex; align-items:center; justify-content:center; padding: 16px; }
  .ccC-modal { background:#fff; border-radius: 18px; max-width: 460px; width:100%; max-height: 86vh; overflow-y:auto;
    padding: 20px 22px; box-shadow: 0 18px 60px rgba(10,40,80,.35); font-family:"Nunito",sans-serif; color:#23445f; }
  .ccC-modal h3 { margin: 0 0 10px; font-size: 16px; color:#1c5f9e; }
  .ccC-lvl { display:flex; align-items:center; gap:10px; }
  .ccC-lvl .badge { min-width: 44px; height: 44px; border-radius: 50%; display:flex; align-items:center; justify-content:center;
    background: linear-gradient(180deg,#39b3e6,#1c70cc); color:#fff; font-weight: 900; font-size: 15px; box-shadow: 0 3px 10px rgba(28,112,204,.3); }
  .ccC-border-top10 { box-shadow: 0 0 0 3px rgba(230,193,76,.55), 0 6px 24px rgba(35,100,165,.1) !important; }
  .ccC-ev { border:1px solid #e3eef7; border-radius: 12px; padding: 10px 12px; margin-bottom: 9px; background:#fbfeff; }
  .ccC-ev .t { font-weight:900; font-size: 13.5px; display:flex; justify-content:space-between; gap:8px; }
  .ccC-ev .d { font-size: 11.5px; color:#5b7fa3; font-weight:700; margin: 2px 0 6px; }
  .ccC-table-wrap { overflow-x:auto; }
  .ccC-table { width:100%; border-collapse: collapse; font-size: 12.5px; min-width: 720px; }
  .ccC-table th { text-align:left; padding: 8px 10px; font-size: 10.5px; letter-spacing:.4px; text-transform: uppercase;
    color:#7a9db8; border-bottom: 1.5px solid #e3eef7; white-space:nowrap; }
  .ccC-table td { padding: 8px 10px; border-bottom: 1px solid #eef4fa; white-space:nowrap; }
  .ccC-table tr.me td { background: rgba(208,168,40,.08); }
  .ccC-table td img.mini { width: 26px; height: 26px; border-radius: 50%; object-fit:cover; vertical-align: middle; margin-right: 7px; border:1.5px solid #e3eef7; }
  @media (max-width: 640px) {
    .ccC-pod { max-width: none; }
    .ccC-chat { height: 320px; }
  }`;

  function injectCss() {
    if ($("#ccC-style")) return;
    const st = el("style"); st.id = "ccC-style"; st.textContent = CSS;
    document.head.appendChild(st);
  }

  // ── Root / navigation ──────────────────────────────────────────────────────
  function root() { return $("#cc-clans-root"); }

  function nav(view) {
    C.view = view;
    stopChatPoll();
    render();
  }

  async function refreshHome(force) {
    const res = await post("home", {});
    if (res && res.ok) { C.home = res; }
    else if (force) toast(errMsg(res && res.error), "error");
    return res;
  }

  window.__ccClansRender = async function () {
    injectCss();
    const r = root();
    if (!r) return;
    if (!bridge().authUser()) { r.innerHTML = ""; return; }   // guest gate handles the message
    if (!C.home) r.innerHTML = '<div class="ccC-empty">Loading clans…</div>';
    await refreshHome(false);
    // A season that ended since the player was last here opens straight onto
    // the Season Results screen — once. After that it's the 📜 button.
    C.view = (justEndedSid() ? "results" : "home");
    render();
  };

  // Returns the previous season's id when it has finalized standings the
  // player hasn't been shown yet (per-account localStorage marker).
  function justEndedSid() {
    try {
      const prev = C.home && C.home.prev_season;
      if (!prev || !prev.sid) return null;
      if (!((prev.standings || []).length)) return null;   // nothing was played
      const u = bridge().authUser();
      const key = "cc_clan_season_seen_" + ((u && u.uid) || "anon");
      if (localStorage.getItem(key) === String(prev.sid)) return null;
      localStorage.setItem(key, String(prev.sid));
      return prev.sid;
    } catch (_) { return null; }
  }

  // ── End-game claim hook (called from preview-app after stats save) ────────
  window.__ccClanClaimGame = async function (roomId) {
    try {
      const rid = String(roomId || "").toUpperCase();
      if (!rid || C.claimedRooms[rid]) return;
      if (!bridge().authUser()) return;
      C.claimedRooms[rid] = true;   // claim the slot first: never double-post
      const res = await post("claim-game", { room_id: rid });
      if (!res) {
        // No response at all (network blip) — the server never saw it, so let
        // a later attempt through instead of losing the points for good.
        delete C.claimedRooms[rid];
        return;
      }
      if (!res.ok) {
        if (res.error === "cooldown") toast("Clan Points: 24h clan-switch cooldown is active.", "info");
        return;   // silently skip no_clan / already_claimed / not_in_game etc.
      }
      const pts = Number(res.points || 0);
      if (pts > 0) toast(`🛡️ +${pts} Clan Point${pts === 1 ? "" : "s"} for ${res.clan_name || "your clan"}!`, "success");
      else if (res.opp_capped) toast("🛡️ Clan Points: daily limit vs the same opponent reached.", "info");
      if (res.goal_done) toast("🌞 Your clan finished today's Daily Goal! +25 Clan XP", "success");
      C.home = null;    // stale — refetch next time the tab opens
    } catch (_) {}
  };

  // Trade completion toast (server does the awarding; see /api/trade/confirm)
  window.__ccClanTradePoint = function (pts) {
    const n = Number(pts || 0);
    if (n > 0) toast(`🤝 +${n} Clan Point — daily clan trade complete!`, "success");
    C.home = null;
  };

  // "Invite to Clan" from player profiles / messages
  window.__ccClanInvite = async function (toUid, toName) {
    const res = await post("invite", { to_uid: String(toUid || ""), to_name: String(toName || "") });
    if (res && res.ok) toast(`🛡️ Clan invite sent to ${toName || "player"}!`, "success");
    else toast(errMsg(res && res.error), "error");
    return !!(res && res.ok);
  };
  window.__ccClanCanInvite = function () {
    try {
      if (!C.home) {
        // Not loaded yet (Clans tab never opened) — prime it in the background
        // so the NEXT profile open can show the invite button.
        if (bridge().authUser()) refreshHome(false);
        return false;
      }
      const my = C.home.my_clan_full && C.home.my_clan_full.my;
      return !!(my && my.perms && my.perms.invite);
    } catch (_) { return false; }
  };

  // ── Rendering ──────────────────────────────────────────────────────────────
  function render() {
    const r = root();
    if (!r) return;
    stopCountdown();
    if (C.view === "home") return renderHome(r);
    if (C.view === "browse") return renderBrowse(r);
    if (C.view === "create") return renderCreate(r);
    if (C.view === "leaderboard") return renderLeaderboard(r);
    if (C.view === "results") return renderResults(r);
    if (C.view === "clan") return renderClan(r);
  }

  function seasonBlock(season) {
    const wrap = el("div", "ccC-season");
    const left = el("div", "");
    left.appendChild(el("div", "ccC-season-name", `Season ${season.number} · ${esc(season.name)}`));
    left.appendChild(el("div", "ccC-season-sub", `Ends ${fmtDate(season.ends_ts)} · new season starts right after`));
    const cd = el("div", "ccC-count");
    wrap.appendChild(left); wrap.appendChild(cd);
    // Self-cancelling: leaving the Clans tab detaches this node without calling
    // render(), so the timer stops itself. It must only do that AFTER the block
    // has been on screen — callers build the whole card first and attach it
    // last, so the first tick legitimately runs detached.
    let everAttached = false;
    const tick = () => {
      if (document.body.contains(wrap)) everAttached = true;
      else if (everAttached) { stopCountdown(); return; }
      const p = countdownParts(season.ends_ts);
      cd.innerHTML = `<b>${p.d}<span>days</span></b><b>${p.h}<span>hrs</span></b><b>${p.m}<span>min</span></b><b>${p.s}<span>sec</span></b>`;
    };
    tick();
    C.countdownTimer = setInterval(tick, 1000);
    return wrap;
  }
  function stopCountdown() { if (C.countdownTimer) { clearInterval(C.countdownTimer); C.countdownTimer = null; } }

  function card(titleHtml) {
    const c = el("div", "ph-scard ph-full");
    c.style.marginBottom = "14px";
    if (titleHtml) {
      const h = el("div", "ph-sh");
      h.style.padding = "14px 16px 6px";
      h.appendChild(el("div", "ph-st", titleHtml));
      c.appendChild(h);
    }
    return c;
  }

  // ---- HOME ------------------------------------------------------------------
  async function renderHome(r) {
    r.innerHTML = "";
    if (!C.home) {
      r.innerHTML = '<div class="ccC-empty">Loading clans…</div>';
      const res = await refreshHome(true);
      if (!res || !res.ok) { r.innerHTML = '<div class="ccC-empty">Clans are unavailable right now — try again shortly.</div>'; return; }
      r.innerHTML = "";
    }
    const H = C.home;
    const c = card('🛡️ Clans');
    c.appendChild(seasonBlock(H.season));

    // Top 3 featured podium
    const rows = H.top3 || [];
    if (rows.length) {
      const pod = el("div", "ccC-podium");
      const cls = ["g", "s", "b"], medals = ["🥇", "🥈", "🥉"];
      const rewards = ["400 coins each", "300 coins each", "200 coins each"];
      rows.forEach((cl, i) => {
        const p = el("div", "ccC-pod " + cls[i]);
        p.innerHTML = `<div class="medal">${medals[i]}</div>
          <img src="${esc(avSrc(cl.icon))}" alt="">
          <div class="nm">${esc(cl.name)}</div>
          <div class="pts">${cl.points} pts</div>
          <div class="rw">${rewards[i]}</div>`;
        p.addEventListener("click", () => openClan(cl.id));
        pod.appendChild(p);
      });
      c.appendChild(pod);
    } else {
      c.appendChild(el("div", "ccC-empty", "No clans yet this season — found the first one! 🐚"));
    }

    // My clan (or join/create CTA)
    if (H.my_clan) {
      const m = el("div", "ccC-myclan" + (H.my_clan.season_border ? " ccC-border-top10" : ""));
      const contrib = H.my_contribution || {};
      m.innerHTML = `<img src="${esc(avSrc(H.my_clan.icon))}" alt="">
        <div style="flex:1 1 auto;min-width:0;">
          <div class="big">${esc(H.my_clan.name)} <span class="ccC-chip">#${H.my_clan.rank} this season</span></div>
          <div class="sub">${H.my_clan.points} Clan Points · your contribution: <b>${contrib.points || 0}</b>
          · this week ${contrib.weekly || 0}/${contrib.weekly_cap || 150}</div>
        </div>
        <button class="ccC-btn pri">Open</button>`;
      m.addEventListener("click", () => openClan(H.my_clan.id));
      c.appendChild(m);
    } else {
      const m = el("div", "ccC-myclan");
      m.style.cursor = "default";
      m.innerHTML = `<div style="font-size:30px;">🐚</div>
        <div style="flex:1 1 auto;">
          <div class="big">You're not in a clan yet</div>
          <div class="sub">Join a clan to earn Clan Points together and compete for seasonal rewards.</div>
        </div>`;
      const bb = el("div", "");
      bb.style.cssText = "display:flex;gap:8px;flex-wrap:wrap;";
      const bJoin = el("button", "ccC-btn pri", "🔍 Find a Clan");
      const bMake = el("button", "ccC-btn", "✨ Create a Clan");
      bJoin.addEventListener("click", () => nav("browse"));
      bMake.addEventListener("click", () => nav("create"));
      bb.appendChild(bJoin); bb.appendChild(bMake);
      m.appendChild(bb);
      c.appendChild(m);
    }
    if (H.cooldown_until) {
      c.appendChild(el("div", "ccC-pin", `⏳ <b>Clan-switch cooldown:</b> you can earn Clan Points again ${fmtDateTime(H.cooldown_until)}.`));
    }

    // Pending invites
    const invites = H.invites || [];
    if (invites.length) {
      const sec = el("div", "ccC-sec");
      sec.appendChild(el("div", "ccC-sec-h", "✉️ Clan invitations"));
      const b = el("div", "ccC-sec-b");
      invites.forEach(inv => {
        const row = el("div", "");
        row.style.cssText = "display:flex;align-items:center;gap:10px;margin-bottom:8px;";
        row.innerHTML = `<img src="${esc(avSrc(inv.icon))}" style="width:34px;height:34px;border-radius:50%;object-fit:cover;border:2px solid #e3eef7;">
          <div style="flex:1;"><b>${esc(inv.name)}</b><div class="ccC-hint">invited by ${esc(inv.by)} · ${fmtAgo(inv.ts)}</div></div>`;
        const acc = el("button", "ccC-btn pri tiny", "Accept");
        const dec = el("button", "ccC-btn tiny", "Decline");
        acc.addEventListener("click", async () => {
          const res = await post("join", { clan_id: inv.clan_id });
          if (res && res.ok) { toast("Welcome to " + inv.name + "! 🛡️", "success"); C.home = null; await refreshHome(true); openClan(inv.clan_id); }
          else toast(errMsg(res && res.error), "error");
        });
        dec.addEventListener("click", async () => {
          await post("invite-decline", { clan_id: inv.clan_id });
          C.home = null; await refreshHome(true); render();
        });
        row.appendChild(acc); row.appendChild(dec);
        b.appendChild(row);
      });
      sec.appendChild(b);
      c.appendChild(sec);
    }

    // My badges shelf
    const badges = H.badges || [];
    if (badges.length) {
      const sec = el("div", "ccC-sec");
      sec.appendChild(el("div", "ccC-sec-h", "🎖 Your clan badges"));
      const b = el("div", "ccC-sec-b");
      b.style.cssText = "display:flex;gap:6px;flex-wrap:wrap;";
      badges.slice().reverse().forEach(bd => {
        const medal = bd.type === "mvp" ? "🎖" : (bd.place === 1 ? "🥇" : bd.place === 2 ? "🥈" : "🥉");
        const label = bd.type === "mvp" ? `Season ${bd.season} Clan MVP` : `Season ${bd.season} · #${bd.place} with ${esc(bd.clan || "")}`;
        const chip = el("span", "ccC-chip" + (bd.type === "mvp" || bd.place === 1 ? " gold" : ""), `${medal} ${label}`);
        chip.style.padding = "5px 12px"; chip.style.fontSize = "11.5px";
        b.appendChild(chip);
      });
      sec.appendChild(b);
      c.appendChild(sec);
    }

    // Buttons
    const btns = el("div", "ccC-topbtns");
    btns.style.paddingBottom = "16px";
    const bLb = el("button", "ccC-btn pri", "🏆 Full Clan Leaderboard");
    bLb.addEventListener("click", () => nav("leaderboard"));
    btns.appendChild(bLb);
    const bBrowse = el("button", "ccC-btn", "🔍 Browse Clans");
    bBrowse.addEventListener("click", () => nav("browse"));
    btns.appendChild(bBrowse);
    if (!H.my_clan) {
      const bMk = el("button", "ccC-btn", "✨ Create a Clan");
      bMk.addEventListener("click", () => nav("create"));
      btns.appendChild(bMk);
    }
    const bPrev = el("button", "ccC-btn", `📜 Season ${(H.prev_season && H.prev_season.number) || ""} Results`);
    bPrev.addEventListener("click", () => nav("results"));
    btns.appendChild(bPrev);
    c.appendChild(btns);
    r.appendChild(c);
  }

  function openClan(clanId) {
    C.viewingClanId = clanId;
    C.clanTab = "overview";
    C.clan = null;
    nav("clan");
  }

  // ---- BROWSE ----------------------------------------------------------------
  async function renderBrowse(r) {
    r.innerHTML = "";
    const c = card("🔍 Find a Clan");
    const bar = el("div", "ccC-topbtns");
    const back = el("button", "ccC-btn", "← Back");
    back.addEventListener("click", () => nav("home"));
    bar.appendChild(back);
    const inp = el("input", "ccC-inp");
    inp.placeholder = "Search clans by name…";
    inp.style.cssText = "flex:1 1 200px;border-radius:999px;";
    bar.appendChild(inp);
    const bMk = el("button", "ccC-btn pri", "✨ Create a Clan");
    bMk.addEventListener("click", () => nav("create"));
    bar.appendChild(bMk);
    c.appendChild(bar);
    const listWrap = el("div", "");
    listWrap.innerHTML = '<div class="ccC-empty">Loading clans…</div>';
    c.appendChild(listWrap);
    r.appendChild(c);

    let t = null;
    const load = async () => {
      const res = await post("browse", { query: inp.value.trim() });
      if (!res || !res.ok) { listWrap.innerHTML = `<div class="ccC-empty">${errMsg(res && res.error)}</div>`; return; }
      listWrap.innerHTML = "";
      const inClan = !!(C.home && C.home.my_clan);
      if ((res.recommended || []).length && !inp.value.trim()) {
        const sec = el("div", "ccC-sec");
        sec.appendChild(el("div", "ccC-sec-h", "⭐ Recommended for you"));
        const b = el("div", "ccC-sec-b");
        b.style.cssText = "display:flex;gap:8px;flex-wrap:wrap;";
        res.recommended.forEach(cl => {
          const chip = el("button", "ccC-btn", `<img src="${esc(avSrc(cl.icon))}" style="width:20px;height:20px;border-radius:50%;vertical-align:-5px;object-fit:cover;"> ${esc(cl.name)} · #${cl.rank}`);
          chip.addEventListener("click", () => openClan(cl.id));
          b.appendChild(chip);
        });
        sec.appendChild(b);
        listWrap.appendChild(sec);
      }
      if (!(res.rows || []).length) {
        listWrap.appendChild(el("div", "ccC-empty", "No clans found — try another name, or create your own!"));
        return;
      }
      const sec = el("div", "ccC-sec");
      res.rows.forEach(cl => {
        const row = el("div", "ccC-member");
        row.style.cursor = "pointer";
        const full = cl.member_count >= cl.max_members;
        row.innerHTML = `<img src="${esc(avSrc(cl.icon))}" alt="">
          <div class="who">
            <div class="n">${esc(cl.name)} <span class="ccC-chip">#${cl.rank}</span> <span class="ccC-chip">${privacyShort(cl.privacy)}</span></div>
            <div class="r">${esc(cl.description || "")}</div>
            <div class="r">${cl.member_count}/${cl.max_members} members · ${cl.points} pts this season · Lv ${cl.level}</div>
          </div>`;
        const bt = el("button", "ccC-btn tiny" + (cl.privacy === "public" && !full && !inClan ? " pri" : ""),
          full ? "Full" : cl.privacy === "public" ? "Join" : cl.privacy === "request" ? "Request" : "Invite Only");
        if (full || inClan || cl.privacy === "invite") bt.disabled = true;
        if (inClan) bt.title = "You're already in a clan";
        bt.addEventListener("click", async (e) => {
          e.stopPropagation();
          if (cl.privacy === "public") {
            const res2 = await post("join", { clan_id: cl.id });
            if (res2 && res2.ok) { toast("Welcome to " + cl.name + "! 🛡️", "success"); C.home = null; await refreshHome(true); openClan(cl.id); }
            else toast(errMsg(res2 && res2.error), "error");
          } else if (cl.privacy === "request") {
            const res2 = await post("request", { clan_id: cl.id });
            if (res2 && res2.ok) { toast("Join request sent to " + cl.name + " ✉️", "success"); bt.textContent = "Requested"; bt.disabled = true; }
            else toast(errMsg(res2 && res2.error), "error");
          }
        });
        row.appendChild(bt);
        row.addEventListener("click", () => openClan(cl.id));
        sec.appendChild(row);
      });
      listWrap.appendChild(sec);
    };
    inp.addEventListener("input", () => { clearTimeout(t); t = setTimeout(load, 350); });
    load();
  }

  // ---- CREATE ----------------------------------------------------------------
  function renderCreate(r) {
    r.innerHTML = "";
    const c = card("✨ Create a Clan");
    const bar = el("div", "ccC-topbtns");
    const back = el("button", "ccC-btn", "← Back");
    back.addEventListener("click", () => nav("home"));
    bar.appendChild(back);
    c.appendChild(bar);
    const body = el("div", "");
    body.style.padding = "8px 16px 18px";

    // Name
    const fName = el("div", "ccC-field");
    fName.innerHTML = '<label>Clan name</label>';
    const inName = el("input", "ccC-inp");
    inName.maxLength = 30; inName.placeholder = "e.g. Reef Riders";
    inName.style.width = "100%"; inName.style.boxSizing = "border-box";
    const hName = el("div", "ccC-hint", "3–30 characters. Names are checked automatically — inappropriate or disguised names can't be used.");
    fName.appendChild(inName); fName.appendChild(hName);
    body.appendChild(fName);
    let nameOk = false, nameTimer = null;
    inName.addEventListener("input", () => {
      clearTimeout(nameTimer);
      nameOk = false;
      hName.className = "ccC-hint";
      hName.textContent = "Checking…";
      nameTimer = setTimeout(async () => {
        const v = inName.value.trim();
        if (v.length < 3) { hName.textContent = "At least 3 characters."; return; }
        const res = await post("check-name", { name: v });
        if (!res || !res.ok) { hName.textContent = "Couldn't check the name — try again."; return; }
        if (!res.clean) { hName.className = "ccC-hint bad"; hName.textContent = res.reason === "inappropriate" ? "That name isn't allowed." : res.reason === "charset" ? "Letters, numbers and simple punctuation only." : "Name must be 3–30 characters."; return; }
        if (!res.available) { hName.className = "ccC-hint bad"; hName.textContent = "That name is already taken."; return; }
        nameOk = true; hName.className = "ccC-hint good"; hName.textContent = "✓ Available!";
      }, 350);
    });

    // Icon picker — every critter in the game, previewable
    const fIcon = el("div", "ccC-field");
    fIcon.innerHTML = '<label>Clan critter icon</label>';
    const pick = el("div", "ccC-iconpick");
    let selIcon = null, selIconName = "";
    const avatars = (bridge().animalAvatars() || []);
    avatars.forEach(a => {
      const ic = el("div", "ic");
      ic.innerHTML = `<img loading="lazy" src="${esc(avSrc(a.img))}" alt="${esc(a.name)}"><div>${esc(a.name)}</div>`;
      ic.title = a.name;
      ic.addEventListener("click", () => {
        pick.querySelectorAll(".ic.sel").forEach(x => x.classList.remove("sel"));
        ic.classList.add("sel");
        selIcon = a.img; selIconName = a.name;
      });
      pick.appendChild(ic);
    });
    fIcon.appendChild(pick);
    fIcon.appendChild(el("div", "ccC-hint", "Your critter appears beside the clan name, on the leaderboard, in invites, chat and next to members' names."));
    body.appendChild(fIcon);

    // Description
    const fDesc = el("div", "ccC-field");
    fDesc.innerHTML = '<label>Description (optional)</label>';
    const inDesc = el("textarea", "");
    inDesc.maxLength = 240; inDesc.rows = 2;
    inDesc.placeholder = "What's your clan about?";
    inDesc.style.cssText = "width:100%;box-sizing:border-box;resize:vertical;";
    fDesc.appendChild(inDesc);
    body.appendChild(fDesc);

    // Privacy
    const fPriv = el("div", "ccC-field");
    fPriv.innerHTML = '<label>Membership setting</label>';
    let selPriv = "public";
    const privRow = el("div", "");
    privRow.style.cssText = "display:flex;gap:8px;flex-wrap:wrap;";
    [["public", "🌊 Public", "Anyone can join instantly"],
     ["request", "✉️ Request to Join", "You approve who joins"],
     ["invite", "🔒 Invite Only", "Join by invitation only"]].forEach(([v, lab, hint]) => {
      const b = el("button", "ccC-btn" + (v === "public" ? " pri" : ""), lab);
      b.title = hint;
      b.addEventListener("click", () => {
        selPriv = v;
        privRow.querySelectorAll(".ccC-btn").forEach(x => x.classList.remove("pri"));
        b.classList.add("pri");
      });
      privRow.appendChild(b);
    });
    fPriv.appendChild(privRow);
    body.appendChild(fPriv);

    const go = el("button", "ccC-btn pri", "🛡️ Found this Clan");
    go.style.cssText = "font-size:14px;padding:10px 26px;";
    go.addEventListener("click", async () => {
      const name = inName.value.trim();
      if (name.length < 3) { toast("Enter a clan name (3–30 characters).", "error"); return; }
      if (!selIcon) { toast("Choose a critter icon for your clan.", "error"); return; }
      go.disabled = true;
      const res = await post("create", { name, icon: selIcon, icon_name: selIconName,
                                         description: inDesc.value.trim(), privacy: selPriv });
      go.disabled = false;
      if (res && res.ok) {
        toast("Clan founded — welcome, Owner! 👑", "success");
        C.home = null; await refreshHome(true);
        openClan(res.clan_id);
      } else if (res && res.error === "bad_name") {
        toast("That clan name can't be used — please pick another.", "error");
      } else toast(errMsg(res && res.error), "error");
    });
    body.appendChild(go);
    c.appendChild(body);
    r.appendChild(c);
  }

  // ---- LEADERBOARD -------------------------------------------------------------
  async function renderLeaderboard(r) {
    r.innerHTML = "";
    const c = card("🏆 Clan Leaderboard");
    const bar = el("div", "ccC-topbtns");
    const back = el("button", "ccC-btn", "← Back");
    back.addEventListener("click", () => nav("home"));
    bar.appendChild(back);
    c.appendChild(bar);
    const holder = el("div", "");
    holder.innerHTML = '<div class="ccC-empty">Loading…</div>';
    c.appendChild(holder);
    r.appendChild(c);
    const res = await post("leaderboard", {});
    if (!res || !res.ok) { holder.innerHTML = `<div class="ccC-empty">${errMsg(res && res.error)}</div>`; return; }
    holder.innerHTML = "";
    c.insertBefore(seasonBlock(res.season), holder);
    const rows = res.rows || [];
    if (!rows.length) { holder.appendChild(el("div", "ccC-empty", "No clans yet — create the first one!")); return; }

    // Featured top three
    const pod = el("div", "ccC-podium");
    const cls = ["g", "s", "b"], medals = ["🥇", "🥈", "🥉"];
    const rewards = ["🏅 Gold badge + 400 coins/member", "🏅 Silver badge + 300 coins/member", "🏅 Bronze badge + 200 coins/member"];
    rows.slice(0, 3).forEach((cl, i) => {
      const p = el("div", "ccC-pod " + cls[i]);
      p.innerHTML = `<div class="medal">${medals[i]}</div><img src="${esc(avSrc(cl.icon))}" alt="">
        <div class="nm">${esc(cl.name)}</div><div class="pts">${cl.points} pts</div><div class="rw">${rewards[i]}</div>`;
      p.addEventListener("click", () => openClan(cl.id));
      pod.appendChild(p);
    });
    holder.appendChild(pod);

    const myId = C.home && C.home.my_clan ? C.home.my_clan.id : null;
    const wrap = el("div", "ccC-table-wrap");
    wrap.style.padding = "6px 16px 16px";
    const tb = el("table", "ccC-table");
    tb.innerHTML = `<thead><tr><th>Rank</th><th>Clan</th><th>Members</th><th>Points</th>
      <th>Comp Wins</th><th>Casual Wins</th><th>Challenge Pts</th><th>Trade Pts</th>
      <th>Games</th><th>Record</th></tr></thead>`;
    const tbody = el("tbody");
    rows.forEach(cl => {
      const tr = el("tr", cl.id === myId ? "me" : "");
      tr.style.cursor = "pointer";
      const medal = cl.rank === 1 ? "🥇" : cl.rank === 2 ? "🥈" : cl.rank === 3 ? "🥉" : "#" + cl.rank;
      tr.innerHTML = `<td><b>${medal}</b></td>
        <td><img class="mini" src="${esc(avSrc(cl.icon))}">${esc(cl.name)}${cl.season_border ? ' <span class="ccC-chip gold">Top 10</span>' : ""}</td>
        <td>${cl.member_count}/${cl.max_members}</td><td><b>${cl.points}</b></td>
        <td>${cl.comp_wins}</td><td>${cl.casual_wins}</td><td>${cl.challenge_points}</td>
        <td>${cl.trade_points}</td><td>${cl.games}</td><td>${esc(cl.record || "0-0")}</td>`;
      tr.addEventListener("click", () => openClan(cl.id));
      tbody.appendChild(tr);
    });
    tb.appendChild(tbody);
    wrap.appendChild(tb);
    holder.appendChild(wrap);
  }

  // ---- SEASON RESULTS -----------------------------------------------------------
  async function renderResults(r) {
    r.innerHTML = "";
    const c = card("📜 Clan Season Results");
    const bar = el("div", "ccC-topbtns");
    const back = el("button", "ccC-btn", "← Back");
    back.addEventListener("click", () => nav("home"));
    bar.appendChild(back);
    c.appendChild(bar);
    const holder = el("div", "");
    holder.innerHTML = '<div class="ccC-empty">Loading…</div>';
    c.appendChild(holder);
    r.appendChild(c);
    const res = await post("season-results", {});
    if (!res || !res.ok) { holder.innerHTML = `<div class="ccC-empty">${errMsg(res && res.error)}</div>`; return; }
    holder.innerHTML = "";
    const meta = res.meta || {};
    const standings = meta.standings || [];
    holder.appendChild(el("div", "ccC-season",
      `<div><div class="ccC-season-name">Season ${res.number} · ${esc(res.name)}</div>
       <div class="ccC-season-sub">${standings.length ? "Final results" : "No clan results were recorded for this season."}</div></div>
       <div class="ccC-season-sub">Next: Season ${res.next_season.number} · ${esc(res.next_season.name)} — started ${fmtDate(res.next_season.starts_ts)}</div>`));
    if (standings.length) {
      const pod = el("div", "ccC-podium");
      const cls = ["g", "s", "b"], medals = ["🥇", "🥈", "🥉"];
      standings.slice(0, 3).forEach((cl, i) => {
        const p = el("div", "ccC-pod " + cls[i]);
        p.innerHTML = `<div class="medal">${medals[i]}</div><img src="${esc(avSrc(cl.icon))}" alt="">
          <div class="nm">${esc(cl.name)}</div><div class="pts">${cl.points} pts</div>
          <div class="rw">${cl.coins_per_member ? cl.coins_per_member + " coins/member" : ""}</div>
          ${cl.mvp ? `<div class="rw">MVP: ${esc(cl.mvp.name)} 🎖</div>` : ""}`;
        pod.appendChild(p);
      });
      holder.appendChild(pod);

      // My clan's final placement + my breakdown. Uses the clan I was in for
      // THAT season (server-resolved), not whatever clan I'm in today.
      const myId = res.my_clan_id || (C.home && C.home.my_clan ? C.home.my_clan.id : null);
      const mine = standings.find(s => s.clan_id === myId);
      if (mine) {
        const sec = el("div", "ccC-sec");
        sec.appendChild(el("div", "ccC-sec-h", `🛡️ ${esc(mine.name)} — final placement #${mine.rank}`));
        const b = el("div", "ccC-sec-b");
        const my = res.my_contribution || {};
        b.innerHTML = `
          <div class="ccC-statgrid" style="padding:0 0 8px;">
            <div class="ccC-stat"><b>${mine.points}</b><span>Clan Points</span></div>
            <div class="ccC-stat"><b>${mine.comp_wins}</b><span>Comp wins</span></div>
            <div class="ccC-stat"><b>${mine.casual_wins}</b><span>Casual wins</span></div>
            <div class="ccC-stat"><b>${mine.challenges_completed}</b><span>Challenges</span></div>
            <div class="ccC-stat"><b>${esc(mine.record)}</b><span>Record</span></div>
          </div>
          <div style="font-size:12.5px;">
            <div>🎖 <b>Clan MVP:</b> ${mine.mvp ? esc(mine.mvp.name) + " (" + mine.mvp.points + " pts)" : "—"}</div>
            <div>⚔️ <b>Most competitive wins:</b> ${mine.top_comp ? esc(mine.top_comp.name) + " (" + mine.top_comp.wins + ")" : "—"}</div>
            <div>🌊 <b>Most active member:</b> ${mine.most_active ? esc(mine.most_active.name) + " (" + mine.most_active.days + " days)" : "—"}</div>
            ${my ? `<div style="margin-top:7px;">📊 <b>Your contribution:</b> ${my.points || 0} pts
              (games ${my.game_points || 0} · trades ${my.trade_points || 0} · challenges ${my.challenge_points || 0})</div>` : ""}
            ${mine.coins_per_member ? `<div>🪙 <b>Clan reward:</b> ${mine.coins_per_member} Critter Coins per eligible member (10+ pts)</div>` : ""}
            <div>🪙 <b>You earned:</b> ${Number(res.my_coins || 0).toLocaleString()} Critter Coins</div>
            <div>🎖 <b>You unlocked:</b> ${(res.my_badges || []).length
              ? (res.my_badges || []).map(b => `<span class="ccC-chip gold">${b.type === "mvp"
                  ? "Season " + b.season + " Clan MVP"
                  : (b.place === 1 ? "🥇" : b.place === 2 ? "🥈" : "🥉") + " Season " + b.season + " badge"}</span>`).join(" ")
              : "—"}</div>
          </div>`;
        sec.appendChild(b);
        holder.appendChild(sec);
      }
      // Full final table
      const wrap = el("div", "ccC-table-wrap");
      wrap.style.padding = "6px 16px 16px";
      const tb = el("table", "ccC-table");
      tb.innerHTML = `<thead><tr><th>Rank</th><th>Clan</th><th>Points</th><th>Record</th><th>MVP</th><th>Reward</th></tr></thead>`;
      const tbody = el("tbody");
      standings.forEach(cl => {
        const tr = el("tr", cl.clan_id === myId ? "me" : "");
        tr.innerHTML = `<td><b>${cl.rank <= 3 ? ["🥇","🥈","🥉"][cl.rank-1] : "#" + cl.rank}</b></td>
          <td><img class="mini" src="${esc(avSrc(cl.icon))}">${esc(cl.name)}</td>
          <td><b>${cl.points}</b></td><td>${esc(cl.record)}</td>
          <td>${cl.mvp ? "🎖 " + esc(cl.mvp.name) : "—"}</td>
          <td>${cl.coins_per_member ? cl.coins_per_member + " coins/member" : cl.rank <= SEASON_BORDER_TOP_N_UI ? "Seasonal border" : "—"}</td>`;
        tbody.appendChild(tr);
      });
      tb.appendChild(tbody);
      wrap.appendChild(tb);
      holder.appendChild(wrap);
    }
  }
  const SEASON_BORDER_TOP_N_UI = 10;

  // ---- CLAN PROFILE --------------------------------------------------------------
  async function loadClan() {
    const res = await post("get", { clan_id: C.viewingClanId });
    if (res && res.ok) C.clan = res.clan;
    return res;
  }

  async function renderClan(r) {
    r.innerHTML = "";
    if (!C.clan) {
      r.innerHTML = '<div class="ccC-empty">Loading clan…</div>';
      const res = await loadClan();
      if (!res || !res.ok) { r.innerHTML = `<div class="ccC-empty">${errMsg(res && res.error)}</div>`; return; }
      r.innerHTML = "";
    }
    const cl = C.clan;
    const my = cl.my || null;
    const isMine = !!my;
    const c = card(null);
    if (cl.season_border) c.classList.add("ccC-border-top10");

    // Header
    const bar = el("div", "ccC-topbtns");
    const back = el("button", "ccC-btn", "← Back");
    back.addEventListener("click", async () => { C.clan = null; C.home = null; await refreshHome(false); nav("home"); });
    bar.appendChild(back);
    if (!isMine) {
      const flagBtn = el("button", "ccC-btn tiny", "⚑ Report name");
      flagBtn.style.marginLeft = "auto";
      flagBtn.addEventListener("click", () => reportNameModal(cl));
      bar.appendChild(flagBtn);
    }
    c.appendChild(bar);

    const head = el("div", "ccC-myclan");
    head.style.cursor = "default";
    const levelInfo = cl.level_info || { level: 1, into: 0, next: 100 };
    head.innerHTML = `<img src="${esc(avSrc(cl.icon))}" alt="">
      <div style="flex:1 1 auto;min-width:0;">
        <div class="big">${esc(cl.name)}
          ${cl.rank ? `<span class="ccC-chip">#${cl.rank} this season</span>` : ""}
          <span class="ccC-chip">${privacyShort(cl.privacy)}</span>
          ${cl.season_border ? '<span class="ccC-chip gold">Top 10 last season</span>' : ""}
          ${cl.favorite_critter ? `<span class="ccC-chip">💙 <img src="${esc(avSrc(cl.favorite_critter))}" style="width:15px;height:15px;border-radius:50%;vertical-align:-3px;object-fit:cover;"> season favorite</span>` : ""}
        </div>
        <div class="sub">${esc(cl.description || "")}</div>
        <div class="sub">${cl.member_count}/${cl.max_members} members · founded ${fmtDate(cl.created_ts)}</div>
      </div>
      <div class="ccC-lvl"><div class="badge">Lv ${levelInfo.level}</div>
        <div style="min-width:110px;">
          <div class="ccC-hint" style="margin:0 0 3px;">Clan XP ${levelInfo.into}/${levelInfo.next}</div>
          <div class="ccC-goalbar" style="width:110px;"><i style="width:${Math.min(100, Math.round(100 * levelInfo.into / Math.max(1, levelInfo.next)))}%"></i></div>
        </div></div>`;
    c.appendChild(head);

    if (cl.pinned_announcement) {
      c.appendChild(el("div", "ccC-pin",
        `📌 <b>${esc(cl.pinned_announcement.by || "")}</b>: ${esc(cl.pinned_announcement.text)} <span class="ccC-hint" style="display:inline;">· ${fmtAgo(cl.pinned_announcement.ts)}</span>`));
    }

    // Sub-tabs
    const tabs = el("div", "ccC-tabs");
    const tabList = [["overview", "Overview"], ["members", "Members"], ["chat", "Chat"], ["events", "Events"], ["log", "Activity Log"]];
    if (isMine && (my.is_owner || my.perms.edit_custom_roles)) tabList.push(["settings", "Settings"]);
    tabList.forEach(([id, label]) => {
      const b = el("button", "ph-lb-mode-btn" + (C.clanTab === id ? " active" : ""), label);
      if (id === "chat" && !isMine) b.disabled = true;
      b.addEventListener("click", () => { C.clanTab = id; stopChatPoll(); render(); });
      tabs.appendChild(b);
    });
    c.appendChild(tabs);

    const pane = el("div", "");
    pane.style.paddingBottom = "10px";
    c.appendChild(pane);
    r.appendChild(c);

    if (C.clanTab === "overview") paneOverview(pane, cl, my);
    else if (C.clanTab === "members") paneMembers(pane, cl, my);
    else if (C.clanTab === "chat") paneChat(pane, cl, my);
    else if (C.clanTab === "events") paneEvents(pane, cl, my);
    else if (C.clanTab === "log") paneLog(pane, cl);
    else if (C.clanTab === "settings") paneSettings(pane, cl, my);
  }

  // ---- Overview pane
  function paneOverview(pane, cl, my) {
    const grid = el("div", "ccC-statgrid");
    [["Clan Points", cl.points], ["Comp wins", cl.comp_wins], ["Casual wins", cl.casual_wins],
     ["Challenges", cl.challenges_completed], ["Trade pts", cl.trade_points],
     ["Games", cl.games], ["Record", cl.record || "0-0"], ["Win streak", cl.win_streak || 0]]
      .forEach(([lab, v]) => {
        const s = el("div", "ccC-stat");
        s.innerHTML = `<b>${esc(String(v))}</b><span>${lab}</span>`;
        grid.appendChild(s);
      });
    pane.appendChild(grid);

    // Daily goal
    const dg = cl.daily_goal || {};
    const goal = dg.goal || {};
    const sec = el("div", "ccC-sec");
    sec.appendChild(el("div", "ccC-sec-h", `🌞 Daily clan goal <span class="ccC-chip${dg.done ? " gold" : ""}">${dg.done ? "Complete! +25 Clan XP" : "in progress"}</span>`));
    const b = el("div", "ccC-sec-b");
    const prog = Math.min(Number(dg.progress || 0), Number(goal.target || 1));
    b.innerHTML = `<b>${esc(goal.label || "")}</b>
      <div class="ccC-goalbar"><i style="width:${Math.round(100 * prog / Math.max(1, Number(goal.target || 1)))}%"></i></div>
      <div class="ccC-hint">${prog}/${goal.target || 0} · resets daily (UTC)</div>`;
    sec.appendChild(b);
    pane.appendChild(sec);

    // Weekly challenges
    const secC = el("div", "ccC-sec");
    secC.appendChild(el("div", "ccC-sec-h", "🏁 Weekly clan challenges"));
    const bc = el("div", "ccC-sec-b");
    const chs = cl.challenges || [];
    if (!chs.length) {
      bc.innerHTML = '<div class="ccC-empty" style="padding:8px;">New clan challenges are being finalized — coming soon! 🐙</div>';
    } else {
      chs.forEach(ch => {
        const row = el("div", "");
        row.style.marginBottom = "12px";
        const pct = Math.min(100, Math.round(100 * Number(ch.progress || 0) / Math.max(1, ch.target)));
        const left = countdownParts(ch.ends_ts || cl.week_ends_ts || 0);
        const contribs = (ch.contributors || []).filter(x => x.points > 0);
        row.innerHTML = `<div style="display:flex;justify-content:space-between;gap:8px;">
            <b>${ch.done ? "✅ " : ""}${esc(ch.name)}</b>
            <span class="ccC-chip">+${ch.clan_points} pts · +${ch.member_xp} XP</span></div>
          <div class="ccC-hint">${esc(ch.desc || "")}</div>
          <div class="ccC-goalbar"><i style="width:${pct}%"></i></div>
          <div class="ccC-hint">${Math.min(ch.progress, ch.target)}/${ch.target}
            · ⏳ ${left.d}d ${left.h}h left
            · needs ${ch.min_contribution}+ of your own points for the XP</div>
          <div class="ccC-hint" style="margin-top:3px;">${contribs.length
            ? "👥 Contributed: " + contribs.map(x =>
                `<span class="ccC-chip${x.qualifies ? " gold" : ""}">${esc(x.name)} ${x.points}</span>`).join(" ")
            : "👥 No one has contributed yet this week."}</div>`;
        bc.appendChild(row);
      });
    }
    secC.appendChild(bc);
    pane.appendChild(secC);

    // Favorite critter vote (members only)
    if (my) {
      const secV = el("div", "ccC-sec");
      secV.appendChild(el("div", "ccC-sec-h", "💙 Favorite clan critter — season vote"));
      const bv = el("div", "ccC-sec-b");
      bv.innerHTML = `<div class="ccC-hint" style="margin:0 0 8px;">Vote for this season's featured critter — the winner decorates the clan page.
        ${cl.favorite_critter ? `Current favorite: <img src="${esc(avSrc(cl.favorite_critter))}" style="width:18px;height:18px;border-radius:50%;vertical-align:-4px;object-fit:cover;">` : ""}</div>`;
      const pick = el("div", "ccC-iconpick");
      pick.style.maxHeight = "140px";
      (bridge().animalAvatars() || []).forEach(a => {
        const ic = el("div", "ic" + (cl.my_vote === a.img ? " sel" : ""));
        ic.innerHTML = `<img loading="lazy" src="${esc(avSrc(a.img))}" alt="${esc(a.name)}"><div>${esc(a.name)}</div>`;
        ic.addEventListener("click", async () => {
          const res = await post("vote-critter", { icon: a.img });
          if (res && res.ok) { toast(`Voted for ${a.name} 💙`, "success"); C.clan = null; render(); }
          else toast(errMsg(res && res.error), "error");
        });
        pick.appendChild(ic);
      });
      bv.appendChild(pick);
      secV.appendChild(bv);
      pane.appendChild(secV);
    }

    // Friendly rival (no rewards ride on it — bragging rights only)
    const canRival = !!(my && (my.is_owner || my.perms.post_announcements));
    if (cl.rival || canRival) {
      const secR = el("div", "ccC-sec");
      const hR = el("div", "ccC-sec-h");
      hR.innerHTML = "⚔️ Friendly rivalry";
      if (canRival) {
        const bR = el("button", "ccC-btn tiny", cl.rival ? "Change" : "Pick a rival");
        bR.addEventListener("click", () => rivalModal(cl));
        hR.appendChild(bR);
        if (cl.rival) {
          const bC = el("button", "ccC-btn tiny", "Clear");
          bC.addEventListener("click", async () => {
            const res = await post("rival", { op: "clear" });
            if (res && res.ok) { C.clan = null; render(); }
          });
          hR.appendChild(bC);
        }
      }
      secR.appendChild(hR);
      const bb = el("div", "ccC-sec-b");
      if (cl.rival) {
        const rows = [["Clan Points", cl.points, cl.rival.points],
                      ["Competitive wins", cl.comp_wins, cl.rival.comp_wins],
                      ["Casual wins", cl.casual_wins, cl.rival.casual_wins],
                      ["Challenges", cl.challenges_completed, cl.rival.challenges_completed]];
        bb.innerHTML = `<div style="display:flex;align-items:center;gap:10px;justify-content:center;margin-bottom:8px;">
            <b>${esc(cl.name)}</b>
            <img src="${esc(avSrc(cl.icon))}" style="width:30px;height:30px;border-radius:50%;object-fit:cover;">
            <span class="ccC-hint" style="margin:0;">vs</span>
            <img src="${esc(avSrc(cl.rival.icon))}" style="width:30px;height:30px;border-radius:50%;object-fit:cover;">
            <b>${esc(cl.rival.name)}</b></div>`
          + rows.map(([lab, a, b2]) => `<div style="display:flex;justify-content:space-between;gap:10px;font-size:12.5px;padding:3px 0;border-bottom:1px solid #eef4fa;">
              <b style="color:${a >= b2 ? "#1c5f9e" : "#8aa4bb"};min-width:40px;">${a}</b>
              <span class="ccC-hint" style="margin:0;">${lab}</span>
              <b style="color:${b2 >= a ? "#1c5f9e" : "#8aa4bb"};min-width:40px;text-align:right;">${b2}</b></div>`).join("")
          + '<div class="ccC-hint" style="margin-top:7px;">Rivalries are for bragging rights only — no extra points or rewards.</div>';
      } else {
        bb.innerHTML = '<div class="ccC-hint">Pick one friendly rival clan for the season and compare stats head-to-head. No rewards attached.</div>';
      }
      secR.appendChild(bb);
      pane.appendChild(secR);
    }

    // Previous season placements
    const prevs = Object.entries(cl.prev_results || {}).sort((a, b) => (a[0] < b[0] ? 1 : -1));
    if (prevs.length) {
      const secP = el("div", "ccC-sec");
      secP.appendChild(el("div", "ccC-sec-h", "📜 Season history"));
      const bp = el("div", "ccC-sec-b");
      prevs.forEach(([sid, resu]) => {
        bp.appendChild(el("div", "", `<b>Season ${seasonNumFromSid(sid)}</b> — #${resu.rank} · ${resu.points} pts
          ${resu.mvp ? " · MVP: " + esc(resu.mvp.name) + " 🎖" : ""}`));
      });
      secP.appendChild(bp);
      pane.appendChild(secP);
    }

    // My membership actions
    if (my && !my.is_owner) {
      const row = el("div", "ccC-topbtns");
      row.style.paddingBottom = "14px";
      const leave = el("button", "ccC-btn danger", "🚪 Leave clan");
      leave.addEventListener("click", () => leaveModal(cl));
      row.appendChild(leave);
      pane.appendChild(row);
    }
  }
  async function rivalModal(cl) {
    const bg = el("div", "ccC-modal-bg");
    const md = el("div", "ccC-modal");
    md.innerHTML = "<h3>⚔️ Pick a friendly rival</h3>"
      + '<div class="ccC-hint" style="margin-bottom:8px;">One rival per season. Purely for bragging rights — no points or rewards change hands.</div>';
    const list = el("div", "");
    list.innerHTML = '<div class="ccC-empty">Loading clans…</div>';
    md.appendChild(list);
    const cancel = el("button", "ccC-btn", "Cancel");
    cancel.style.marginTop = "10px";
    cancel.addEventListener("click", () => document.body.removeChild(bg));
    md.appendChild(cancel);
    bg.appendChild(md);
    document.body.appendChild(bg);
    const res = await post("leaderboard", {});
    list.innerHTML = "";
    ((res && res.rows) || []).filter(r => r.id !== cl.id).slice(0, 40).forEach(r => {
      const row = el("button", "ccC-btn", `<img src="${esc(avSrc(r.icon))}" style="width:20px;height:20px;border-radius:50%;vertical-align:-5px;object-fit:cover;"> ${esc(r.name)} · #${r.rank} · ${r.points} pts`);
      row.style.cssText = "display:block;width:100%;text-align:left;margin-bottom:6px;";
      row.addEventListener("click", async () => {
        const r2 = await post("rival", { op: "set", clan_id: r.id });
        document.body.removeChild(bg);
        if (r2 && r2.ok) { toast("⚔️ Rivalry declared!", "success"); C.clan = null; render(); }
        else toast(errMsg(r2 && r2.error), "error");
      });
      list.appendChild(row);
    });
    if (!list.children.length) list.appendChild(el("div", "ccC-empty", "No other clans yet."));
  }

  function seasonNumFromSid(sid) {
    const m = /^(\d{4})-Q([1-4])$/.exec(String(sid || ""));
    if (!m) return "?";
    return Math.max(1, (Number(m[1]) - 2026) * 4 + (Number(m[2]) - 3) + 1);
  }

  // ---- Members pane
  function paneMembers(pane, cl, my) {
    const canReview = !!(my && my.perms.review_requests);
    // Pending join requests (reviewers only)
    if (canReview && (cl.join_requests || []).length) {
      const sec = el("div", "ccC-sec");
      sec.appendChild(el("div", "ccC-sec-h", "✉️ Join requests"));
      const b = el("div", "ccC-sec-b");
      cl.join_requests.forEach(rq => {
        const row = el("div", "");
        row.style.cssText = "display:flex;align-items:center;gap:8px;margin-bottom:7px;";
        row.innerHTML = `<b style="flex:1;">${esc(rq.name)}</b><span class="ccC-hint">${fmtAgo(rq.ts)}</span>`;
        const acc = el("button", "ccC-btn pri tiny", "Accept");
        const rej = el("button", "ccC-btn tiny", "Reject");
        acc.addEventListener("click", async () => {
          const res = await post("request-act", { uid: rq.uid, accept: true });
          if (res && res.ok) { toast(rq.name + " joined! 🌊", "success"); C.clan = null; render(); }
          else toast(errMsg(res && res.error), "error");
        });
        rej.addEventListener("click", async () => {
          await post("request-act", { uid: rq.uid, accept: false });
          C.clan = null; render();
        });
        row.appendChild(acc); row.appendChild(rej);
        b.appendChild(row);
      });
      sec.appendChild(b);
      pane.appendChild(sec);
    }

    // Invite box
    if (my && my.perms.invite) {
      const sec = el("div", "ccC-sec");
      sec.appendChild(el("div", "ccC-sec-h", "📯 Invite a player"));
      const b = el("div", "ccC-sec-b");
      b.style.cssText = "display:flex;gap:8px;flex-wrap:wrap;align-items:center;";
      const inp = el("input", "ccC-inp");
      inp.placeholder = "Exact username…";
      inp.style.flex = "1 1 160px";
      const bt = el("button", "ccC-btn pri tiny", "Send invite");
      bt.addEventListener("click", async () => {
        const n = inp.value.trim();
        if (!n) return;
        const ok = await window.__ccClanInvite("", n);
        if (ok) inp.value = "";
      });
      b.appendChild(inp); b.appendChild(bt);
      b.appendChild(el("div", "ccC-hint", "You can also invite from a player's profile or from Messages."));
      sec.appendChild(b);
      pane.appendChild(sec);
    }

    // Sort control
    const sec = el("div", "ccC-sec");
    const head = el("div", "ccC-sec-h");
    head.innerHTML = `👥 Members (${cl.member_count}/${cl.max_members})`;
    const sortSel = el("select", "");
    [["points", "Season contribution"], ["weekly_points", "Weekly contribution"], ["role", "Role"],
     ["last_seen", "Activity"], ["joined_ts", "Join date"], ["comp_wins", "Competitive wins"]]
      .forEach(([v, lab]) => {
        const o = el("option", "", esc(lab)); o.value = v;
        if (v === C.memberSort) o.selected = true;
        sortSel.appendChild(o);
      });
    sortSel.addEventListener("change", () => { C.memberSort = sortSel.value; render(); });
    head.appendChild(sortSel);
    sec.appendChild(head);

    const roleW = { owner: 4, captain: 3, recruiter: 2, member: 1 };
    const members = (cl.members || []).slice().sort((a, b) => {
      if (C.memberSort === "role") return (roleW[b.role] || 0) - (roleW[a.role] || 0) || b.points - a.points;
      if (C.memberSort === "joined_ts") return a.joined_ts - b.joined_ts;
      return Number(b[C.memberSort] || 0) - Number(a[C.memberSort] || 0);
    });
    const myUid = uidOf();
    members.forEach(m => {
      const row = el("div", "ccC-member");
      const customRole = (cl.custom_roles || []).find(rr => rr.id === m.custom_role_id);
      row.innerHTML = `<span class="ccC-dot${m.online ? " on" : ""}" title="${m.online ? "Online" : "Last seen " + fmtAgo(m.last_seen)}"></span>
        <img src="${esc(avSrc(m.avatar || cl.icon))}" alt="">
        <div class="who">
          <div class="n">${esc(m.name)}
            ${m.uid === myUid ? '<span class="ccC-chip">you</span>' : ""}
            ${m.is_mvp_chip ? '<span class="ccC-chip gold">🎖 MVP</span>' : ""}
            ${m.trade_point_today ? '<span class="ccC-chip" title="Daily clan trade point earned">🤝✓</span>' : ""}
          </div>
          <div class="r">${roleLabel(m.role)}${customRole ? " · 🧩 " + esc(customRole.name) : ""} · joined ${fmtDate(m.joined_ts)} · ${m.online ? "online now" : "active " + fmtAgo(m.last_seen)}</div>
        </div>
        <div class="st">${m.points} pts season<br>${m.weekly_points} this week · ⚔️${m.comp_wins} 🌊${m.casual_wins}${(cl.challenges || []).length ? " 🏁" + m.challenges_done : ""}</div>`;
      if (my && m.uid !== myUid && (my.perms.change_roles || my.perms.remove_members || my.is_owner || my.perms.moderate_chat)) {
        const mg = el("button", "ccC-btn tiny", "⋯");
        mg.title = "Manage member";
        mg.addEventListener("click", () => manageMemberModal(cl, my, m));
        row.appendChild(mg);
      }
      sec.appendChild(row);
    });
    pane.appendChild(sec);

    // Former contributors (season history)
    const former = cl.former_contributors || [];
    if (former.length) {
      const secF = el("div", "ccC-sec");
      secF.appendChild(el("div", "ccC-sec-h", "🫧 Former members — points stay with the clan"));
      const b = el("div", "ccC-sec-b");
      b.innerHTML = former.map(f => `<span class="ccC-chip" style="margin:0 4px 4px 0;">${esc(f.name || "Player")} · ${f.points} pts</span>`).join("");
      secF.appendChild(b);
      pane.appendChild(secF);
    }
  }

  function uidOf() {
    try { const u = bridge().authUser(); return u ? u.uid : ""; } catch (_) { return ""; }
  }

  function manageMemberModal(cl, my, m) {
    const bg = el("div", "ccC-modal-bg");
    const md = el("div", "ccC-modal");
    md.appendChild(el("h3", "", `Manage ${esc(m.name)}`));
    md.appendChild(el("div", "ccC-hint", `${roleLabel(m.role)} · ${m.points} pts this season`));
    const btns = el("div", "");
    btns.style.cssText = "display:flex;flex-direction:column;gap:8px;margin-top:12px;";
    const add = (label, cls, fn) => {
      const b = el("button", "ccC-btn " + (cls || ""), label);
      b.addEventListener("click", async () => { await fn(); document.body.removeChild(bg); });
      btns.appendChild(b);
    };
    const setRole = (role) => async () => {
      const res = await post("role", { uid: m.uid, role });
      if (res && res.ok) { toast("Role updated 🎖", "success"); C.clan = null; render(); }
      else toast(errMsg(res && res.error), "error");
    };
    const isOwner = my.is_owner;
    if (isOwner && m.role !== "captain") add("⚓ Promote to Captain", "", setRole("captain"));
    if ((isOwner || my.perms.change_roles) && m.role !== "recruiter") add("📯 Make Recruiter", "", setRole("recruiter"));
    if ((isOwner || my.perms.change_roles) && m.role !== "member") add("🐟 Set as Member", "", setRole("member"));
    (cl.custom_roles || []).forEach(rr => {
      if ((isOwner || my.perms.change_roles) && m.custom_role_id !== rr.id) {
        add(`🧩 Assign role: ${esc(rr.name)}`, "", async () => {
          const res = await post("role", { uid: m.uid, role: m.role === "owner" ? "member" : (m.role === "captain" && !isOwner ? "member" : m.role === "captain" ? "captain" : m.role), custom_role_id: rr.id });
          if (res && res.ok) { toast("Role assigned 🧩", "success"); C.clan = null; render(); }
          else toast(errMsg(res && res.error), "error");
        });
      }
    });
    if (my.perms.moderate_chat) {
      add("🔇 Mute in chat (30 min)", "", async () => {
        const res = await post("chat-mod", { op: "mute", uid: m.uid, minutes: 30 });
        if (res && res.ok) toast("Muted for 30 minutes 🔇", "info");
        else toast(errMsg(res && res.error), "error");
      });
    }
    if (isOwner) {
      add("👑 Transfer ownership", "danger", async () => {
        if (!confirm(`Make ${m.name} the clan owner? You'll become a Captain.`)) return;
        const res = await post("transfer", { uid: m.uid });
        if (res && res.ok) { toast("👑 Ownership transferred.", "success"); C.clan = null; render(); }
        else toast(errMsg(res && res.error), "error");
      });
    }
    if (isOwner || my.perms.remove_members) {
      add("❌ Remove from clan", "danger", async () => {
        if (!confirm(`Remove ${m.name} from the clan? They keep no seat and must wait 24h before earning points elsewhere. Their earned points stay with the clan.`)) return;
        const res = await post("kick", { uid: m.uid });
        if (res && res.ok) { toast(m.name + " was removed.", "info"); C.clan = null; render(); }
        else toast(errMsg(res && res.error), "error");
      });
    }
    const close = el("button", "ccC-btn", "Cancel");
    close.addEventListener("click", () => document.body.removeChild(bg));
    btns.appendChild(close);
    md.appendChild(btns);
    bg.appendChild(md);
    bg.addEventListener("click", (e) => { if (e.target === bg) document.body.removeChild(bg); });
    document.body.appendChild(bg);
  }

  function leaveModal(cl) {
    const bg = el("div", "ccC-modal-bg");
    const md = el("div", "ccC-modal");
    md.innerHTML = `<h3>Leave ${esc(cl.name)}?</h3>
      <div style="font-size:13px;line-height:1.55;">
        Before you go, here's what happens:
        <ul style="margin:8px 0 8px 18px;padding:0;">
          <li>Your earned Clan Points <b>stay with ${esc(cl.name)}</b>.</li>
          <li>Your contribution remains visible in the season history.</li>
          <li>You must wait <b>24 hours</b> before earning points for another clan.</li>
          <li>You won't receive this clan's seasonal rewards unless you return before the season ends and still qualify.</li>
        </ul>
      </div>`;
    const row = el("div", "");
    row.style.cssText = "display:flex;gap:8px;justify-content:flex-end;margin-top:12px;";
    const cancel = el("button", "ccC-btn", "Stay");
    const go = el("button", "ccC-btn danger", "Leave clan");
    cancel.addEventListener("click", () => document.body.removeChild(bg));
    go.addEventListener("click", async () => {
      const res = await post("leave", {});
      document.body.removeChild(bg);
      if (res && res.ok) { toast("You left the clan. 👋", "info"); C.clan = null; C.home = null; await refreshHome(true); nav("home"); }
      else toast(errMsg(res && res.error), "error");
    });
    row.appendChild(cancel); row.appendChild(go);
    md.appendChild(row);
    bg.appendChild(md);
    document.body.appendChild(bg);
  }

  function reportNameModal(cl) {
    const bg = el("div", "ccC-modal-bg");
    const md = el("div", "ccC-modal");
    md.innerHTML = `<h3>Report clan name</h3>
      <div class="ccC-hint" style="margin-bottom:8px;">Report “${esc(cl.name)}” if it slipped past the automatic filter. An admin will review it.</div>`;
    const ta = el("textarea", "");
    ta.rows = 2; ta.placeholder = "Why is this name inappropriate?";
    ta.style.cssText = "width:100%;box-sizing:border-box;";
    md.appendChild(ta);
    const row = el("div", "");
    row.style.cssText = "display:flex;gap:8px;justify-content:flex-end;margin-top:12px;";
    const cancel = el("button", "ccC-btn", "Cancel");
    const go = el("button", "ccC-btn pri", "Send report");
    cancel.addEventListener("click", () => document.body.removeChild(bg));
    go.addEventListener("click", async () => {
      const res = await post("report", { kind: "name", clan_id: cl.id, reason: ta.value.trim() });
      document.body.removeChild(bg);
      if (res && res.ok) toast("Report sent — thanks for keeping the reef clean. 🪸", "success");
      else toast(errMsg(res && res.error), "error");
    });
    row.appendChild(cancel); row.appendChild(go);
    md.appendChild(row);
    bg.appendChild(md);
    document.body.appendChild(bg);
  }

  // ---- Chat pane
  function stopChatPoll() {
    if (C.chatTimer) { clearInterval(C.chatTimer); C.chatTimer = null; }
    C.chatSince = 0; C.chatMsgs = [];
  }

  function paneChat(pane, cl, my) {
    if (!my) { pane.appendChild(el("div", "ccC-empty", "Clan chat is members-only.")); return; }
    const sec = el("div", "ccC-sec");
    sec.style.margin = "10px 16px 14px";
    const headEl = el("div", "ccC-sec-h");
    const p = countdownParts(cl.season.ends_ts);
    headEl.innerHTML = `💬 Clan chat <span class="ccC-chip">⏳ season ends in ${p.d}d ${p.h}h</span>`;
    if (my.perms.post_announcements) {
      const annBtn = el("button", "ccC-btn tiny", "📣 Announce");
      annBtn.addEventListener("click", () => announceModal(my));
      headEl.appendChild(annBtn);
    }
    // Share the game/tournament I'm in right now, so clanmates can jump in.
    const liveRoom = (() => { try { return bridge().currentRoom ? String(bridge().currentRoom() || "") : ""; } catch (_) { return ""; } })();
    if (liveRoom) {
      const invBtn = el("button", "ccC-btn tiny pri", "🎮 Invite to my game");
      invBtn.addEventListener("click", async () => {
        const res = await post("chat-send", {
          kind: "game_invite", room_id: liveRoom,
          text: `🎮 Come play! Room ${liveRoom}`,
        });
        if (res && res.ok) { toast("Game invite sent to the clan 🎮", "success"); poll(true); }
        else toast(errMsg(res && res.error), "error");
      });
      headEl.appendChild(invBtn);
    }
    sec.appendChild(headEl);
    const chat = el("div", "ccC-chat");
    const log = el("div", "ccC-chat-log");
    log.innerHTML = '<div class="ccC-empty">Loading chat…</div>';
    chat.appendChild(log);
    const inRow = el("div", "ccC-chat-in");
    const inp = el("input", "");
    inp.placeholder = "Message your clan…";
    inp.maxLength = 500;
    const send = el("button", "ccC-btn pri", "Send");
    const doSend = async () => {
      const text = inp.value.trim();
      if (!text) return;
      inp.value = "";
      const res = await post("chat-send", { text });
      if (res && res.ok) poll(true);
      else if (res && res.error === "muted") toast("You're muted in clan chat until " + fmtDateTime(res.until) + ".", "error");
      else toast(errMsg(res && res.error), "error");
    };
    send.addEventListener("click", doSend);
    inp.addEventListener("keydown", (e) => { if (e.key === "Enter") doSend(); });
    inRow.appendChild(inp); inRow.appendChild(send);
    chat.appendChild(inRow);
    sec.appendChild(chat);
    pane.appendChild(sec);

    const myUid = uidOf();
    function draw() {
      log.innerHTML = "";
      if (!C.chatMsgs.length) {
        log.innerHTML = '<div class="ccC-empty">No messages yet — say hi! 🐠</div>';
        return;
      }
      C.chatMsgs.forEach(m => {
        if (m.kind === "system") { log.appendChild(el("div", "ccC-msg sys", esc(m.text))); return; }
        const mine = m.uid === myUid;
        const d = el("div", "ccC-msg" + (mine ? " me" : "") + (m.kind === "announce" ? " ann" : ""));
        let extra = "";
        if (m.kind === "game_invite" && m.room_id) extra = ` <button class="ccC-btn pri tiny" data-room="${esc(m.room_id)}">Join game</button>`;
        d.innerHTML = `<div class="m-h">${m.kind === "announce" ? "📣 " : ""}${esc(m.name || "?")} <span style="font-weight:700;color:#9db4c9;">${fmtAgo(m.ts)}</span></div>${esc(m.text)}${extra}`;
        const jb = d.querySelector("[data-room]");
        if (jb) jb.addEventListener("click", () => { location.search = "?room=" + encodeURIComponent(jb.getAttribute("data-room")); });
        if (my.perms.moderate_chat && !mine) {
          const del = el("button", "ccC-btn tiny", "🗑");
          del.title = "Remove message";
          del.style.cssText = "margin-left:6px;padding:0 7px;";
          del.addEventListener("click", async () => {
            await post("chat-mod", { op: "delete", id: m.id });
            C.chatMsgs = C.chatMsgs.filter(x => x.id !== m.id);
            draw();
          });
          d.appendChild(del);
        } else if (!mine) {
          const rep = el("button", "ccC-btn tiny", "⚑");
          rep.title = "Report message";
          rep.style.cssText = "margin-left:6px;padding:0 7px;";
          rep.addEventListener("click", async () => {
            const res = await post("report", { kind: "message", msg_id: m.id, msg_text: m.text, reason: "chat report" });
            if (res && res.ok) toast("Message reported. 🪸", "success");
          });
          d.appendChild(rep);
        }
        log.appendChild(d);
      });
      log.scrollTop = log.scrollHeight;
    }
    async function poll(scroll) {
      const res = await post("chat-get", { since: C.chatSince });
      if (!res || !res.ok) return;
      const fresh = res.messages || [];
      if (fresh.length) {
        C.chatMsgs = C.chatMsgs.concat(fresh).slice(-120);
        C.chatSince = Math.max(C.chatSince, ...fresh.map(m => Number(m.ts || 0)));
        draw();
      } else if (scroll) draw();
      if (res.muted_until) inp.placeholder = "You're muted until " + fmtDateTime(res.muted_until);
    }
    poll(true);
    C.chatTimer = setInterval(() => {
      // stop polling when the pane is no longer in the document
      if (!document.body.contains(log)) { stopChatPoll(); return; }
      poll(false);
    }, 3500);
  }

  function announceModal(my) {
    const bg = el("div", "ccC-modal-bg");
    const md = el("div", "ccC-modal");
    md.innerHTML = "<h3>📣 Clan announcement</h3>";
    const ta = el("textarea", "");
    ta.rows = 3; ta.maxLength = 500;
    ta.placeholder = "Announcement for the whole clan…";
    ta.style.cssText = "width:100%;box-sizing:border-box;";
    md.appendChild(ta);
    let pin = false;
    if (my.perms.pin_announcements) {
      const lb = el("label", "");
      lb.style.cssText = "display:flex;align-items:center;gap:7px;margin-top:9px;font-size:13px;font-weight:700;cursor:pointer;";
      const cb = el("input", ""); cb.type = "checkbox";
      cb.addEventListener("change", () => { pin = cb.checked; });
      lb.appendChild(cb);
      lb.appendChild(document.createTextNode("📌 Pin to the top of the clan page"));
      md.appendChild(lb);
    }
    const row = el("div", "");
    row.style.cssText = "display:flex;gap:8px;justify-content:flex-end;margin-top:12px;";
    const cancel = el("button", "ccC-btn", "Cancel");
    const go = el("button", "ccC-btn pri", "Post");
    cancel.addEventListener("click", () => document.body.removeChild(bg));
    go.addEventListener("click", async () => {
      const text = ta.value.trim();
      if (!text) return;
      const res = await post("announce", { text, pin });
      document.body.removeChild(bg);
      if (res && res.ok) { toast("Announcement posted 📣", "success"); C.clan = null; render(); }
      else toast(errMsg(res && res.error), "error");
    });
    row.appendChild(cancel); row.appendChild(go);
    md.appendChild(row);
    bg.appendChild(md);
    document.body.appendChild(bg);
  }

  // ---- Events pane
  function paneEvents(pane, cl, my) {
    const sec = el("div", "ccC-sec");
    const head = el("div", "ccC-sec-h");
    head.innerHTML = "📅 Clan events";
    if (my && my.perms.create_events) {
      const nb = el("button", "ccC-btn tiny pri", "+ New event");
      nb.addEventListener("click", () => eventModal());
      head.appendChild(nb);
    }
    sec.appendChild(head);
    const b = el("div", "ccC-sec-b");
    const evs = (cl.events || []).slice().sort((a, b2) => a.ts - b2.ts);
    if (!evs.length) {
      b.appendChild(el("div", "ccC-empty", "No events scheduled. Game night, anyone? 🎲"));
    }
    const myUid = uidOf();
    evs.forEach(ev => {
      const going = (ev.attending || []).includes(myUid);
      const reminded = (ev.reminders || []).includes(myUid);
      const soon = ev.ts - Date.now() / 1000 < 3600 && ev.ts > Date.now() / 1000 - 3600;
      const d = el("div", "ccC-ev");
      d.innerHTML = `<div class="t"><span>${soon ? "🔔 " : ""}${esc(ev.name)}</span><span class="ccC-chip">${fmtDateTime(ev.ts)}</span></div>
        <div class="d">${esc(ev.desc || "")} · host: <b>${esc(ev.host_name || "?")}</b> · ${((ev.attending || []).length)} attending</div>`;
      const row = el("div", "");
      row.style.cssText = "display:flex;gap:6px;flex-wrap:wrap;";
      if (my) {
        const jb = el("button", "ccC-btn tiny" + (going ? "" : " pri"), going ? "Leave" : "Join");
        jb.addEventListener("click", async () => {
          const res = await post("events", { op: going ? "leave" : "join", id: ev.id });
          if (res && res.ok) { C.clan = null; render(); }
        });
        row.appendChild(jb);
        if (going && !reminded) {
          const rb = el("button", "ccC-btn tiny", "🔔 Remind me");
          rb.addEventListener("click", async () => {
            const res = await post("events", { op: "remind", id: ev.id });
            if (res && res.ok) { toast("You'll see a reminder when it's about to start. 🔔", "info"); C.clan = null; render(); }
          });
          row.appendChild(rb);
        }
        if (ev.host_uid === myUid || (my.perms.create_events && my.is_owner)) {
          const db2 = el("button", "ccC-btn tiny danger", "Delete");
          db2.addEventListener("click", async () => {
            const res = await post("events", { op: "delete", id: ev.id });
            if (res && res.ok) { C.clan = null; render(); }
          });
          row.appendChild(db2);
        }
      }
      d.appendChild(row);
      b.appendChild(d);
    });
    b.appendChild(el("div", "ccC-hint", "Events don't award Clan Points by themselves — only the games, trades and challenges played during them do."));
    sec.appendChild(b);
    pane.appendChild(sec);
  }

  function eventModal() {
    const bg = el("div", "ccC-modal-bg");
    const md = el("div", "ccC-modal");
    md.innerHTML = "<h3>📅 New clan event</h3>";
    const nm = el("input", "ccC-inp"); nm.placeholder = "Event name (e.g. Clan Game Night)"; nm.maxLength = 60;
    nm.style.cssText = "width:100%;box-sizing:border-box;margin-bottom:9px;";
    const dt = el("input", "ccC-inp"); dt.type = "datetime-local";
    dt.style.cssText = "width:100%;box-sizing:border-box;margin-bottom:9px;";
    const ds = el("textarea", ""); ds.rows = 2; ds.maxLength = 200; ds.placeholder = "Description (optional)";
    ds.style.cssText = "width:100%;box-sizing:border-box;";
    md.appendChild(nm); md.appendChild(dt); md.appendChild(ds);
    const row = el("div", "");
    row.style.cssText = "display:flex;gap:8px;justify-content:flex-end;margin-top:12px;";
    const cancel = el("button", "ccC-btn", "Cancel");
    const go = el("button", "ccC-btn pri", "Create");
    cancel.addEventListener("click", () => document.body.removeChild(bg));
    go.addEventListener("click", async () => {
      const name = nm.value.trim();
      const ts = dt.value ? Math.floor(new Date(dt.value).getTime() / 1000) : 0;
      if (!name || !ts) { toast("Give the event a name and a time.", "error"); return; }
      const res = await post("events", { op: "create", name, ts, desc: ds.value.trim() });
      document.body.removeChild(bg);
      if (res && res.ok) { toast("Event created 📅", "success"); C.clan = null; render(); }
      else toast(errMsg(res && res.error), "error");
    });
    row.appendChild(cancel); row.appendChild(go);
    md.appendChild(row);
    bg.appendChild(md);
    document.body.appendChild(bg);
  }

  // ---- Activity log pane
  function paneLog(pane, cl) {
    const sec = el("div", "ccC-sec ccC-activity");
    sec.style.margin = "10px 16px 14px";
    const rows = cl.activity || [];
    if (!rows.length) {
      sec.appendChild(el("div", "ccC-empty", "Nothing here yet — go make some waves! 🌊"));
    }
    rows.forEach(a => {
      const d = el("div", "row");
      d.innerHTML = `<span class="ts">${fmtAgo(a.ts)}</span><span>${esc(a.text)}</span>`;
      sec.appendChild(d);
    });
    pane.appendChild(sec);
  }

  // ---- Settings pane (owner + role editors)
  function paneSettings(pane, cl, my) {
    if (!my) return;
    const isOwner = my.is_owner;

    if (isOwner) {
      // Identity / privacy
      const sec = el("div", "ccC-sec");
      sec.appendChild(el("div", "ccC-sec-h", "⚙️ Clan settings"));
      const b = el("div", "ccC-sec-b");
      const ds = el("textarea", ""); ds.rows = 2; ds.maxLength = 240; ds.value = cl.description || "";
      ds.placeholder = "Clan description…";
      ds.style.cssText = "width:100%;box-sizing:border-box;margin-bottom:9px;";
      b.appendChild(el("div", "ccC-field", "<label>Description</label>"));
      b.lastChild.appendChild(ds);
      const fp = el("div", "ccC-field");
      fp.innerHTML = "<label>Membership setting</label>";
      const privRow = el("div", "");
      privRow.style.cssText = "display:flex;gap:8px;flex-wrap:wrap;";
      let selPriv = cl.privacy;
      [["public", "🌊 Public"], ["request", "✉️ Request to Join"], ["invite", "🔒 Invite Only"]].forEach(([v, lab]) => {
        const bt = el("button", "ccC-btn" + (v === selPriv ? " pri" : ""), lab);
        bt.addEventListener("click", () => {
          selPriv = v;
          privRow.querySelectorAll(".ccC-btn").forEach(x => x.classList.remove("pri"));
          bt.classList.add("pri");
        });
        privRow.appendChild(bt);
      });
      fp.appendChild(privRow);
      b.appendChild(fp);
      const lb = el("label", "");
      lb.style.cssText = "display:flex;align-items:center;gap:7px;font-size:13px;font-weight:700;cursor:pointer;margin-bottom:10px;";
      const cb = el("input", ""); cb.type = "checkbox"; cb.checked = !!cl.captains_can_edit_roles;
      lb.appendChild(cb);
      lb.appendChild(document.createTextNode("Allow Captains to create & edit custom roles"));
      b.appendChild(lb);

      // Icon change
      const fi = el("div", "ccC-field");
      fi.innerHTML = "<label>Clan critter icon</label>";
      const pick = el("div", "ccC-iconpick");
      pick.style.maxHeight = "150px";
      let selIcon = cl.icon, selIconName = cl.icon_name || "";
      (bridge().animalAvatars() || []).forEach(a => {
        const ic = el("div", "ic" + (a.img === cl.icon ? " sel" : ""));
        ic.innerHTML = `<img loading="lazy" src="${esc(avSrc(a.img))}" alt="${esc(a.name)}"><div>${esc(a.name)}</div>`;
        ic.addEventListener("click", () => {
          pick.querySelectorAll(".ic.sel").forEach(x => x.classList.remove("sel"));
          ic.classList.add("sel");
          selIcon = a.img; selIconName = a.name;
        });
        pick.appendChild(ic);
      });
      fi.appendChild(pick);
      b.appendChild(fi);

      const save = el("button", "ccC-btn pri", "💾 Save settings");
      save.addEventListener("click", async () => {
        const res = await post("settings", {
          description: ds.value.trim(), privacy: selPriv,
          captains_can_edit_roles: cb.checked,
          icon: selIcon, icon_name: selIconName,
        });
        if (res && res.ok) { toast("Settings saved ⚙️", "success"); C.clan = null; C.home = null; render(); }
        else toast(errMsg(res && res.error), "error");
      });
      b.appendChild(save);
      sec.appendChild(b);
      pane.appendChild(sec);
    }

    // Custom roles editor (owner, or captain when allowed)
    if (isOwner || my.perms.edit_custom_roles) {
      const sec = el("div", "ccC-sec");
      sec.appendChild(el("div", "ccC-sec-h", "🧩 Custom roles"));
      const b = el("div", "ccC-sec-b");
      (cl.custom_roles || []).forEach(rr => {
        const row = el("div", "");
        row.style.cssText = "display:flex;align-items:center;gap:8px;margin-bottom:7px;flex-wrap:wrap;";
        const on = Object.entries(rr.perms || {}).filter(([, v]) => v).map(([k]) => permLabel(k)).join(", ") || "no permissions";
        row.innerHTML = `<b>${esc(rr.name)}</b><span class="ccC-hint" style="flex:1;">${esc(on)}</span>`;
        const eb = el("button", "ccC-btn tiny", "Edit");
        eb.addEventListener("click", () => roleModal(rr));
        const db2 = el("button", "ccC-btn tiny danger", "Delete");
        db2.addEventListener("click", async () => {
          const res = await post("custom-role", { op: "delete", id: rr.id });
          if (res && res.ok) { C.clan = null; render(); }
          else toast(errMsg(res && res.error), "error");
        });
        row.appendChild(eb); row.appendChild(db2);
        b.appendChild(row);
      });
      const nb = el("button", "ccC-btn", "+ New custom role");
      nb.addEventListener("click", () => roleModal(null));
      b.appendChild(nb);
      b.appendChild(el("div", "ccC-hint", "Custom roles can never delete the clan, transfer ownership, change the owner, or change membership settings."));
      sec.appendChild(b);
      pane.appendChild(sec);
    }

    // Danger zone (owner)
    if (isOwner) {
      const sec = el("div", "ccC-sec");
      sec.appendChild(el("div", "ccC-sec-h", "🌋 Danger zone"));
      const b = el("div", "ccC-sec-b");
      b.appendChild(el("div", "ccC-hint", "To leave the clan, transfer ownership to another member first (Members tab → ⋯ → Transfer ownership)."));
      const del = el("button", "ccC-btn danger", "🗑 Disband clan");
      del.style.marginTop = "8px";
      del.addEventListener("click", async () => {
        if (!confirm(`Disband ${cl.name}? This permanently deletes the clan for all ${cl.member_count} member(s). Season history in the archive is kept, but the clan itself is gone.`)) return;
        if (!confirm("Really disband? This cannot be undone.")) return;
        const res = await post("disband", {});
        if (res && res.ok) { toast("Clan disbanded.", "info"); C.clan = null; C.home = null; await refreshHome(true); nav("home"); }
        else toast(errMsg(res && res.error), "error");
      });
      b.appendChild(del);
      sec.appendChild(b);
      pane.appendChild(sec);
    }
  }

  const PERM_LABELS = {
    invite: "Invite players", review_requests: "Review join requests",
    remove_members: "Remove regular members", post_announcements: "Post announcements",
    pin_announcements: "Pin announcements", moderate_chat: "Moderate clan chat",
    create_events: "Create clan events", manage_challenges: "Manage clan challenges",
    change_roles: "Change a member's role",
  };
  const permLabel = (k) => PERM_LABELS[k] || k;

  function roleModal(existing) {
    const bg = el("div", "ccC-modal-bg");
    const md = el("div", "ccC-modal");
    md.innerHTML = `<h3>🧩 ${existing ? "Edit" : "New"} custom role</h3>`;
    const nm = el("input", "ccC-inp");
    nm.placeholder = "Role name (e.g. Reef Keeper)"; nm.maxLength = 24;
    nm.value = existing ? existing.name : "";
    nm.style.cssText = "width:100%;box-sizing:border-box;margin-bottom:10px;";
    md.appendChild(nm);
    const perms = {};
    Object.keys(PERM_LABELS).forEach(k => {
      perms[k] = !!(existing && existing.perms && existing.perms[k]);
      const lb = el("label", "");
      lb.style.cssText = "display:flex;align-items:center;gap:7px;font-size:13px;font-weight:700;cursor:pointer;margin-bottom:5px;";
      const cb = el("input", ""); cb.type = "checkbox"; cb.checked = perms[k];
      cb.addEventListener("change", () => { perms[k] = cb.checked; });
      lb.appendChild(cb);
      lb.appendChild(document.createTextNode(PERM_LABELS[k]));
      md.appendChild(lb);
    });
    const row = el("div", "");
    row.style.cssText = "display:flex;gap:8px;justify-content:flex-end;margin-top:12px;";
    const cancel = el("button", "ccC-btn", "Cancel");
    const go = el("button", "ccC-btn pri", existing ? "Save" : "Create");
    cancel.addEventListener("click", () => document.body.removeChild(bg));
    go.addEventListener("click", async () => {
      const name = nm.value.trim();
      if (name.length < 2) { toast("Give the role a name.", "error"); return; }
      const res = await post("custom-role", {
        op: existing ? "edit" : "create",
        role: { id: existing ? existing.id : undefined, name, perms },
      });
      document.body.removeChild(bg);
      if (res && res.ok) { toast("Role saved 🧩", "success"); C.clan = null; render(); }
      else toast(errMsg(res && res.error), "error");
    });
    row.appendChild(cancel); row.appendChild(go);
    md.appendChild(row);
    bg.appendChild(md);
    document.body.appendChild(bg);
  }
})();
