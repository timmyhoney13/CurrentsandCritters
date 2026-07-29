/* Currents & Critters — Custom Tournament Bracket BUILDER.
 *
 * A blank canvas the host designs a tournament on: drop match boxes anywhere,
 * set how many player spots each holds (2–8), drag connections from a match's
 * advancing finishers into the exact spots they fill later, type each spot
 * (open / player only / AI / invite / fed by a match), and label matches
 * ("Round 1", "Semifinal", "Final", "Losers Bracket", or anything).
 *
 * The design is validated live — the same rules the server enforces — and the
 * host cannot open a tournament until every check passes. The server re-validates
 * on create; this module only produces the design and shows what's wrong.
 *
 * Exposes window.__ccTourneyBuilder.open(startingDesign, onSave). Loaded on every
 * page but inert until opened, and only reachable when tournaments are enabled.
 */
(function () {
  "use strict";

  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));
  const el = (tag, cls, html) => { const n = document.createElement(tag); if (cls) n.className = cls; if (html != null) n.innerHTML = html; return n; };
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

  // Must mirror tournament_engine.py.
  const K_OPEN = "open", K_HUMAN = "human", K_AI = "ai", K_INVITE = "invite",
        K_WINNER = "winner_from", K_TOP = "top_from";
  const ENTRY_KINDS = [K_OPEN, K_HUMAN, K_AI, K_INVITE];
  const MIN_SPOTS = 2, MAX_SPOTS = 8, MAX_MATCHES = 32, MIN_FIELD = 4, MAX_FIELD = 32;
  const BOX_W = 210, GRID = 10;

  const KIND_META = {
    [K_OPEN]:   { icon: "🌊", name: "Open",             hint: "A person or an AI can take this spot." },
    [K_HUMAN]:  { icon: "🧑", name: "Player only",      hint: "Only a real player can take this spot." },
    [K_AI]:     { icon: "🤖", name: "AI only",          hint: "Always filled by a bot." },
    [K_INVITE]: { icon: "✉️", name: "Invite only",      hint: "Reserved for one named player." },
    [K_WINNER]: { icon: "🏅", name: "Winner from…",     hint: "Filled by the winner of another match." },
    [K_TOP]:    { icon: "🎖", name: "Top player from…", hint: "Filled by an advancing player of another match." },
  };
  const LABEL_PRESETS = ["Round 1", "Round 2", "Quarterfinal", "Semifinal", "Final",
                         "Losers Bracket", "Play-in", "Group Stage", "Grand Final"];

  // ── design state ─────────────────────────────────────────────────────────
  const B = {
    matches: [],          // {id,label,x,y,advance,slots:[{kind,source,rank,invite}]}
    seq: 0,
    onSave: null,
    zoom: 1, panX: 0, panY: 0,
    drag: null,           // {id, dx, dy} while a box is being moved
    link: null,           // {from, rank, x, y} while a connection is being drawn
    sel: null,            // selected match id
    errors: [], summary: null,
    checkTimer: null, checking: false,
    panDrag: null,
  };

  function bridge() { return window.__ccTourney; }
  function toast(m, t) { try { bridge().toast(m, t); } catch (_) {} }
  const byId = (id) => B.matches.find(m => m.id === id);
  const indexOf = (id) => B.matches.findIndex(m => m.id === id);
  // Several matches legitimately share a label ("Round 1" ×4), so a bare label makes
  // "Round 1, spot 2 isn't connected" impossible to act on. Disambiguate with the
  // M-number badge shown on the box itself whenever the label isn't unique.
  function display(id) {
    const i = indexOf(id);
    if (i < 0) return String(id);
    const label = B.matches[i].label.trim();
    if (!label) return "Match " + (i + 1);
    const shared = B.matches.filter(m => m.label.trim() === label).length > 1;
    return shared ? `${label} (M${i + 1})` : label;
  }

  // =========================================================================
  // Design mutations
  // =========================================================================
  function newId() { B.seq += 1; return "m" + B.seq + "_" + Math.random().toString(36).slice(2, 6); }

  function freeSpot() {
    // Drop new boxes in a readable column-major grid, skipping occupied cells.
    const cols = 4;
    for (let i = 0; i < 64; i++) {
      const x = 40 + (i % cols) * (BOX_W + 60), y = 40 + Math.floor(i / cols) * 250;
      if (!B.matches.some(m => Math.abs(m.x - x) < 30 && Math.abs(m.y - y) < 60)) return { x, y };
    }
    return { x: 40, y: 40 };
  }

  function addMatch(spots, label) {
    if (B.matches.length >= MAX_MATCHES) { toast(`A bracket holds at most ${MAX_MATCHES} matches.`, "warn"); return null; }
    const n = clamp(spots || 2, MIN_SPOTS, MAX_SPOTS);
    const at = freeSpot();
    const m = {
      id: newId(), label: label || "", x: at.x, y: at.y, advance: 1,
      slots: Array.from({ length: n }, () => ({ kind: K_OPEN, source: "", rank: 1, invite: "" })),
    };
    B.matches.push(m);
    B.sel = m.id;
    render();
    return m;
  }

  function removeMatch(id) {
    B.matches = B.matches.filter(m => m.id !== id);
    // Any connection that pointed at the deleted match falls back to an open spot.
    B.matches.forEach(m => m.slots.forEach(s => {
      if ((s.kind === K_WINNER || s.kind === K_TOP) && s.source === id) {
        s.kind = K_OPEN; s.source = ""; s.rank = 1;
      }
    }));
    if (B.sel === id) B.sel = null;
    render();
  }

  function setSpots(id, n) {
    const m = byId(id); if (!m) return;
    n = clamp(n, MIN_SPOTS, MAX_SPOTS);
    while (m.slots.length < n) m.slots.push({ kind: K_OPEN, source: "", rank: 1, invite: "" });
    while (m.slots.length > n) {
      m.slots.pop();
      // Dropping a spot can orphan a connection that pointed into it — the
      // validator will say so, and the source's advance count is clamped here.
    }
    m.advance = clamp(m.advance, 1, Math.max(1, m.slots.length - 1));
    render();
  }

  function setAdvance(id, n) {
    const m = byId(id); if (!m) return;
    const max = Math.max(1, m.slots.length - 1);
    const next = clamp(n, 1, max);
    if (next < m.advance) {
      // Drop any connection that took a finisher who no longer advances.
      B.matches.forEach(t => t.slots.forEach(s => {
        if (s.source === id && s.rank > next) { s.kind = K_OPEN; s.source = ""; s.rank = 1; }
      }));
    }
    m.advance = next;
    render();
  }

  function setSlotKind(mid, si, kind) {
    const m = byId(mid); if (!m) return;
    const s = m.slots[si]; if (!s) return;
    s.kind = kind;
    if (kind !== K_INVITE) s.invite = "";
    if (kind !== K_WINNER && kind !== K_TOP) { s.source = ""; s.rank = 1; }
    if (kind === K_WINNER) s.rank = 1;
    render();
  }

  // A connection: finisher #rank of `from` fills spot `si` of `to`.
  function connect(fromId, rank, toId, si) {
    const to = byId(toId), from = byId(fromId);
    if (!to || !from) return;
    if (fromId === toId) { toast("A match can't feed itself.", "warn"); return; }
    const s = to.slots[si]; if (!s) return;
    // Clear any other spot already taking this finisher — one player, one seat.
    B.matches.forEach(m => m.slots.forEach(x => {
      if (x.source === fromId && x.rank === rank && !(m.id === toId && x === s)) {
        x.kind = K_OPEN; x.source = ""; x.rank = 1;
      }
    }));
    s.kind = rank === 1 ? K_WINNER : K_TOP;
    s.source = fromId;
    s.rank = rank;
    render();
  }

  function disconnect(mid, si) {
    const m = byId(mid); if (!m || !m.slots[si]) return;
    m.slots[si].kind = K_OPEN; m.slots[si].source = ""; m.slots[si].rank = 1;
    render();
  }

  function toDesign() {
    return {
      matches: B.matches.map(m => ({
        id: m.id, label: m.label || "", x: Math.round(m.x), y: Math.round(m.y),
        advance: m.advance,
        slots: m.slots.map(s => {
          const o = { kind: s.kind };
          if (s.kind === K_WINNER || s.kind === K_TOP) { o.source = s.source; o.rank = s.rank; }
          if (s.kind === K_INVITE && s.invite) o.invite = s.invite;
          return o;
        }),
      })),
    };
  }

  function loadDesign(d) {
    B.matches = []; B.seq = 0;
    const rows = (d && d.matches) || [];
    rows.forEach((m, i) => {
      B.matches.push({
        id: String(m.id || ("m" + (i + 1))),
        label: String(m.label || ""),
        x: Number(m.x) || (40 + (i % 4) * (BOX_W + 60)),
        y: Number(m.y) || (40 + Math.floor(i / 4) * 250),
        advance: Math.max(1, Number(m.advance) || 1),
        slots: (m.slots || []).map(s => ({
          kind: String((s && s.kind) || K_OPEN),
          source: String((s && s.source) || ""),
          rank: Math.max(1, Number(s && s.rank) || 1),
          invite: String((s && s.invite) || ""),
        })),
      });
    });
    B.seq = B.matches.length;
  }

  // =========================================================================
  // Rounds — the fast way to grow a bracket.
  //
  // "Add Round" takes every finisher who is still advancing to nowhere, packs them
  // into a new column of matches and connects all of them in one go, so a whole
  // round costs one click instead of one drag per player.
  // =========================================================================

  /** Advancing finishers that no spot is waiting for yet, in bracket order. */
  function pendingFeeds() {
    const taken = new Set();
    B.matches.forEach(m => m.slots.forEach(s => { if (s.source) taken.add(s.source + "|" + s.rank); }));
    const out = [];
    layers().forEach(row => row.forEach(m => {
      for (let r = 1; r <= m.advance; r++) {
        if (!taken.has(m.id + "|" + r)) out.push({ mid: m.id, rank: r });
      }
    }));
    return out;
  }

  /** Split ``n`` players into matches of at most ``per`` (0 = auto), as evenly as
   *  possible and never below MIN_SPOTS — a designed bracket has no byes, so a
   *  leftover single player is folded into a bigger match instead. Mirrors
   *  _round_match_sizes() in tournament_engine.py. */
  function packRound(n, per) {
    if (n < MIN_SPOTS) return null;
    per = clamp(per || 0, 0, MAX_SPOTS);
    if (!per) per = (n <= MAX_SPOTS) ? n : MIN_SPOTS;   // auto: one Final if they all fit
    let k = Math.ceil(n / per);
    while (k > 1 && Math.floor(n / k) < MIN_SPOTS) k -= 1;
    const base = Math.floor(n / k), extra = n % k;
    if (base < MIN_SPOTS) return null;
    const sizes = Array.from({ length: k }, (_, i) => base + (i < extra ? 1 : 0));
    return sizes.some(s => s > MAX_SPOTS) ? null : sizes;
  }

  // Labels the builder assigns itself. A host's own name ("Losers Bracket") is
  // never overwritten, but these get re-derived as the bracket grows — otherwise
  // yesterday's "Final" keeps that name after a round is added behind it.
  const AUTO_LABEL = /^(round \d+|final|grand final)$/i;
  function relabelAuto() {
    const rows = layers();
    rows.forEach((row, r) => row.forEach(m => {
      const cur = m.label.trim();
      if (cur && !AUTO_LABEL.test(cur)) return;
      m.label = (r === rows.length - 1 && row.length === 1 && rows.length > 1)
        ? "Final" : "Round " + (r + 1);
    }));
  }

  function addRound(per) {
    const pending = pendingFeeds();
    if (!pending.length) {
      toast("Add some matches first — a round connects the players advancing out of them.", "warn");
      return;
    }
    if (pending.length < MIN_SPOTS) {
      toast("Only one player is still advancing, so that match is already the Final.", "warn");
      return;
    }
    const sizes = packRound(pending.length, per);
    if (!sizes) {
      toast(`${pending.length} players can't be split into ${MIN_SPOTS}–${MAX_SPOTS}-player matches that way — try another size.`, "warn");
      return;
    }
    if (B.matches.length + sizes.length > MAX_MATCHES) {
      toast(`That round would take the bracket past ${MAX_MATCHES} matches.`, "warn");
      return;
    }
    const srcX = pending.map(p => (byId(p.mid) || {}).x || 0);
    const colX = Math.max(...srcX) + BOX_W + 110;
    let q = 0;
    sizes.forEach((cap, i) => {
      const m = {
        id: newId(), label: "", x: colX, y: 40 + i * 230,
        advance: 1,
        slots: Array.from({ length: cap }, () => {
          const p = pending[q++];
          return { kind: p.rank === 1 ? K_WINNER : K_TOP, source: p.mid, rank: p.rank, invite: "" };
        }),
      };
      B.matches.push(m);
    });
    relabelAuto();
    B.sel = null;
    autoArrange();      // renders + refits, so the new column reads cleanly
    const n = sizes.length;
    toast(n === 1 ? "Added the Final — every advancing player now has a seat."
                  : `Added a round of ${n} matches and connected ${pending.length} players.`, "ok");
  }

  /** Connect whatever is still dangling into the existing bracket, without adding
   *  matches: leftover finishers drop into empty entry spots of later matches. */
  function autoConnect() {
    const pending = pendingFeeds();
    if (!pending.length) { toast("Every advancing player already has a seat.", "ok"); return; }
    const depth = new Map();
    layers().forEach((row, r) => row.forEach(m => depth.set(m.id, r)));
    let placed = 0;
    for (const p of pending) {
      const from = depth.get(p.mid) || 0;
      // Only ever feed FORWARD (a strictly later round) so we can't build a loop.
      const target = B.matches.find(m => (depth.get(m.id) || 0) > from
        && m.slots.some(s => ENTRY_KINDS.indexOf(s.kind) >= 0));
      if (!target) continue;
      const si = target.slots.findIndex(s => ENTRY_KINDS.indexOf(s.kind) >= 0);
      target.slots[si] = { kind: p.rank === 1 ? K_WINNER : K_TOP, source: p.mid, rank: p.rank, invite: "" };
      placed += 1;
    }
    render();
    toast(placed ? `Connected ${placed} advancing player${placed === 1 ? "" : "s"}.`
                 : "No empty spots in a later match — add a round instead.", placed ? "ok" : "warn");
  }

  /** Build a WHOLE bracket from a format: N players, matches of ``per``, top
   *  ``adv`` of each advance. adv = 1 is classic single elimination; 2+ makes it a
   *  group-stage/top-N tournament. Mirrors plan_round_sizes() server-side, except
   *  a designed bracket has no byes so rounds are packed to a 2-player minimum. */
  function buildTemplate(players, per, adv) {
    players = clamp(players || 8, MIN_FIELD, MAX_FIELD);
    per = clamp(per || 2, MIN_SPOTS, MAX_SPOTS);
    adv = clamp(adv || 1, 1, per - 1);
    const eff = (cap) => Math.max(1, Math.min(adv, cap - 1));
    const grid = [];
    let field = players, guard = 0;
    while (field > 1 && guard++ < 64) {
      const sizes = packRound(field, per);
      if (!sizes) break;
      grid.push(sizes);
      // A round holding one match IS the Final — it crowns a champion, not a top N.
      field = sizes.length <= 1 ? 1 : sizes.reduce((a, s) => a + eff(s), 0);
    }
    if (!grid.length) return null;
    const total = grid.reduce((a, row) => a + row.length, 0);
    if (total > MAX_MATCHES) return null;

    B.matches = []; B.seq = 0; B.sel = null;
    let prev = [];                       // [{mid, rank}] advancing out of the last round
    grid.forEach((sizes, r) => {
      const isFinalRound = (sizes.length === 1 && r === grid.length - 1);
      const queue = prev.slice();
      const ids = [];
      sizes.forEach((cap, i) => {
        const slots = Array.from({ length: cap }, () => {
          if (r === 0) return { kind: K_OPEN, source: "", rank: 1, invite: "" };
          const p = queue.shift();
          return p ? { kind: p.rank === 1 ? K_WINNER : K_TOP, source: p.mid, rank: p.rank, invite: "" }
                   : { kind: K_OPEN, source: "", rank: 1, invite: "" };
        });
        const m = {
          id: newId(), label: isFinalRound ? "Final" : "Round " + (r + 1),
          x: 40 + r * (BOX_W + 110), y: 40 + i * 230,
          advance: isFinalRound ? 1 : eff(cap), slots,
        };
        B.matches.push(m);
        ids.push(m);
      });
      prev = [];
      ids.forEach(m => { for (let k = 1; k <= m.advance; k++) prev.push({ mid: m.id, rank: k }); });
    });
    return { rounds: grid.length, matches: total };
  }

  // A sensible starting point so the canvas is never intimidatingly blank.
  function starterDesign() {
    B.matches = []; B.seq = 0;
    buildTemplate(8, 2, 1);
    B.sel = null;
  }

  // =========================================================================
  // Validation — mirrors validate_custom_bracket() in tournament_engine.py so the
  // host sees problems instantly. The server is still the authority on create.
  // =========================================================================
  function placeWord(rank) { return rank === 1 ? "The winner" : rank === 2 ? "The runner-up" : "Finisher #" + rank; }

  function validate() {
    const errs = [];
    const ms = B.matches;
    if (!ms.length) return ["Add at least one match to the bracket."];
    if (ms.length > MAX_MATCHES) return [`A bracket can hold at most ${MAX_MATCHES} matches.`];

    ms.forEach((m, i) => {
      const who = display(m.id);
      if (m.slots.length < MIN_SPOTS || m.slots.length > MAX_SPOTS)
        errs.push(`${who} has ${m.slots.length} player spot(s) — each match holds ${MIN_SPOTS}–${MAX_SPOTS}.`);
      else if (m.advance >= m.slots.length)
        errs.push(`${who} advances ${m.advance} of ${m.slots.length} players — a match must knock at least one player out.`);
      m.slots.forEach((s, si) => {
        if (s.kind !== K_WINNER && s.kind !== K_TOP) return;
        if (!s.source) errs.push(`${who}, spot ${si + 1} waits on a match result but no match is connected.`);
        else if (s.source === m.id) errs.push(`${who}, spot ${si + 1} is fed by ${who} itself.`);
        else if (!byId(s.source)) errs.push(`${who}, spot ${si + 1} is fed by a match that no longer exists.`);
        else if (s.rank > byId(s.source).advance)
          errs.push(`${who}, spot ${si + 1} takes finisher #${s.rank} of ${display(s.source)}, but only ${byId(s.source).advance} advance${byId(s.source).advance !== 1 ? "" : "s"} from there.`);
      });
    });
    if (errs.length) return errs;

    // One finisher, one destination.
    const consumed = new Map();   // "src|rank" -> [{mid,si}]
    ms.forEach(m => m.slots.forEach((s, si) => {
      if (s.kind === K_WINNER || s.kind === K_TOP) {
        const k = s.source + "|" + s.rank;
        if (!consumed.has(k)) consumed.set(k, []);
        consumed.get(k).push({ mid: m.id, si });
      }
    }));
    consumed.forEach((targets, k) => {
      if (targets.length > 1) {
        const [src, rank] = k.split("|");
        const where = targets.map(t => `${display(t.mid)} spot ${t.si + 1}`).join(", ");
        errs.push(`${placeWord(+rank)} of ${display(src)} is sent to ${targets.length} spots (${where}) — a player can only advance to one.`);
      }
    });
    if (errs.length) return errs;

    const cyc = findCycle();
    if (cyc) return ["These matches feed each other in a loop: " + cyc.map(display).join(" → ") + "."];

    const fedFrom = new Set();
    ms.forEach(m => m.slots.forEach(s => { if (s.source) fedFrom.add(s.source); }));
    const terminals = ms.filter(m => !fedFrom.has(m.id));
    if (!terminals.length) return ["No Final: every match advances into another one, so no champion is decided."];
    if (terminals.length > 1)
      return [`The bracket has ${terminals.length} matches that lead nowhere (${terminals.map(m => display(m.id)).join(", ")}) — connect them so exactly one Final decides the champion.`];
    const final = terminals[0];
    if (final.advance !== 1)
      errs.push(`${display(final.id)} is the Final — exactly 1 player can win it (it advances ${final.advance}).`);

    ms.forEach(m => {
      if (m.id === final.id) return;
      for (let rank = 1; rank <= m.advance; rank++) {
        if (!consumed.has(m.id + "|" + rank))
          errs.push(`${placeWord(rank)} of ${display(m.id)} has nowhere to go — connect it to a spot in a later match.`);
      }
    });

    const entries = ms.reduce((a, m) => a + m.slots.filter(s => ENTRY_KINDS.indexOf(s.kind) >= 0).length, 0);
    if (entries < MIN_FIELD || entries > MAX_FIELD)
      errs.push(`A tournament needs ${MIN_FIELD}–${MAX_FIELD} starting player spots (this bracket has ${entries}).`);
    const humanSpots = ms.reduce((a, m) => a + m.slots.filter(s => ENTRY_KINDS.indexOf(s.kind) >= 0 && s.kind !== K_AI).length, 0);
    if (!humanSpots) errs.push("Every spot is AI-only — leave at least one spot a person can take.");
    return errs;
  }

  function findCycle() {
    const edges = new Map();
    B.matches.forEach(m => edges.set(m.id, []));
    B.matches.forEach(m => m.slots.forEach(s => {
      if (s.source && edges.has(s.source)) edges.get(s.source).push(m.id);
    }));
    const color = new Map(), stack = [];
    let found = null;
    function visit(n) {
      if (found) return;
      color.set(n, 1); stack.push(n);
      (edges.get(n) || []).forEach(nx => {
        if (found) return;
        if (color.get(nx) === 1) { found = stack.slice(stack.indexOf(nx)).concat([nx]); return; }
        if (!color.get(nx)) visit(nx);
      });
      stack.pop(); color.set(n, 2);
    }
    B.matches.forEach(m => { if (!color.get(m.id) && !found) visit(m.id); });
    return found;
  }

  function entryCount() {
    return B.matches.reduce((a, m) => a + m.slots.filter(s => ENTRY_KINDS.indexOf(s.kind) >= 0).length, 0);
  }

  // Layer matches by longest path (same as the server) to describe rounds.
  function layers() {
    const depth = new Map();
    const resolve = (id, guard) => {
      if (depth.has(id)) return depth.get(id);
      if (guard > MAX_MATCHES + 1) return 0;
      const m = byId(id); if (!m) return 0;
      const srcs = m.slots.filter(s => s.source && byId(s.source)).map(s => s.source);
      const d = srcs.length ? 1 + Math.max(...srcs.map(s => resolve(s, guard + 1))) : 0;
      depth.set(id, d);
      return d;
    };
    B.matches.forEach(m => resolve(m.id, 0));
    const out = [];
    B.matches.forEach(m => {
      const d = depth.get(m.id) || 0;
      (out[d] = out[d] || []).push(m);
    });
    return out.filter(Boolean);
  }

  // =========================================================================
  // Rendering
  // =========================================================================
  function injectStyles() {
    if ($("#ccTB-style")) return;
    const s = el("style"); s.id = "ccTB-style";
    s.textContent = `
    #ccTB{ position:fixed; inset:0; z-index:9200; display:none; flex-direction:column;
      background:radial-gradient(120% 90% at 50% -10%, #e2f2fc 0%, #bfdcf2 55%, #a3cbe7 100%);
      color:#0d3070; font-family:"Nunito",sans-serif; }
    #ccTB.open{ display:flex; }
    /* position+z-index are load-bearing: backdrop-filter makes the header its own
       stacking context, so without them the "Add Player Group" menu paints BEHIND
       the canvas and can't be clicked. */
    .ccTB-head{ position:relative; z-index:5; display:flex; align-items:center; gap:10px; padding:10px 14px; flex-wrap:wrap;
      border-bottom:1.5px solid rgba(120,185,235,.45); background:rgba(255,255,255,.6); backdrop-filter:blur(6px); }
    .ccTB-head h2{ margin:0; font-family:"Cinzel",serif; font-size:1.1rem; color:#0c3472; letter-spacing:.5px; }
    .ccTB-head .ccTB-sp{ flex:1; }
    .ccTB-b{ cursor:pointer; border:none; border-radius:13px; padding:9px 14px; font-size:.9rem; font-weight:800;
      color:#0c2858; font-family:"Nunito",sans-serif; background:linear-gradient(100deg,#fbbf58,#fcc55c);
      box-shadow:0 5px 16px rgba(200,130,10,.26); transition:filter .15s, transform .12s; }
    .ccTB-b:hover:not(:disabled){ filter:brightness(1.05); transform:translateY(-1px); }
    .ccTB-b:disabled{ opacity:.5; cursor:not-allowed; box-shadow:none; }
    .ccTB-b.ghost{ background:rgba(255,255,255,.75); color:#1c4f92; border:1.5px solid rgba(120,185,235,.7); box-shadow:none; }
    .ccTB-b.ghost:hover:not(:disabled){ background:#fff; }
    .ccTB-b.sm{ padding:6px 11px; font-size:.8rem; border-radius:10px; }
    .ccTB-b.danger{ background:rgba(255,255,255,.8); color:#c62a4e; border:1.5px solid rgba(198,42,78,.4); box-shadow:none; }
    .ccTB-menu{ position:relative; display:inline-block; }
    .ccTB-menu-pop{ position:absolute; top:calc(100% + 6px); left:0; z-index:30; display:none; flex-direction:column; gap:4px;
      padding:7px; border-radius:14px; background:#fff; border:1.5px solid rgba(140,200,240,.6); box-shadow:0 10px 30px rgba(8,50,130,.18); min-width:190px; }
    .ccTB-menu.open .ccTB-menu-pop{ display:flex; }
    .ccTB-menu-pop.wide{ min-width:260px; }
    .ccTB-menu-pop button{ text-align:left; background:none; border:none; padding:8px 10px; border-radius:9px; cursor:pointer;
      font-family:"Nunito",sans-serif; font-weight:700; color:#123a70; font-size:.88rem; }
    .ccTB-menu-pop button:hover:not(:disabled){ background:#eaf4ff; }
    .ccTB-menu-pop button:disabled{ opacity:.4; cursor:not-allowed; }
    .ccTB-menu-note{ padding:4px 10px 6px; font-size:.76rem; line-height:1.4; color:#3a6aa5; font-weight:600; }
    .ccTB-fld{ display:flex; align-items:center; gap:7px; padding:5px 10px; font-size:.8rem; font-weight:800; color:#123a70; }
    .ccTB-fld input[type=range]{ flex:1; min-width:0; accent-color:#2f8ce0; }
    .ccTB-fld select{ flex:1; min-width:0; font-family:"Nunito",sans-serif; font-weight:700; font-size:.8rem;
      color:#123a70; background:#fff; border:1px solid rgba(140,200,240,.7); border-radius:8px; padding:4px 5px; cursor:pointer; }

    .ccTB-body{ flex:1; display:flex; min-height:0; }
    .ccTB-canvas-wrap{ flex:1; position:relative; overflow:hidden; cursor:grab; touch-action:none; }
    .ccTB-canvas-wrap.grabbing{ cursor:grabbing; }
    .ccTB-canvas{ position:absolute; top:0; left:0; width:4000px; height:3000px; transform-origin:0 0;
      background-image:radial-gradient(rgba(90,150,215,.28) 1px, transparent 1px); background-size:24px 24px; }
    /* transform-origin MUST match the canvas. Without it the SVG defaults to
       50% 50% of its 4000x3000 box, so every zoom slid the connection lines
       ~(2000,1500)*(1-zoom) away from the boxes they connect. */
    svg.ccTB-links{ position:absolute; top:0; left:0; width:4000px; height:3000px; transform-origin:0 0;
      pointer-events:none; overflow:visible; }
    .ccTB-link{ stroke:rgba(60,130,205,.65); stroke-width:2.5; fill:none; }
    .ccTB-link.ghost{ stroke:var(--ccT-gold,#f3a712); stroke-dasharray:6 5; stroke-width:3; }

    .ccTB-box{ position:absolute; width:${BOX_W}px; border-radius:16px; background:rgba(255,255,255,.94);
      border:1.5px solid rgba(140,200,240,.7); box-shadow:0 5px 18px rgba(8,50,130,.13); user-select:none; cursor:grab; }
    .ccTB-box:active{ cursor:grabbing; }
    .ccTB-grip{ color:#5a8cc0; font-size:.8rem; letter-spacing:-1px; cursor:grab; }
    .ccTB-box.sel{ border-color:#f3a712; box-shadow:0 0 0 3px rgba(243,167,18,.25), 0 6px 20px rgba(8,50,130,.16); }
    .ccTB-box.bad{ border-color:#e0607f; box-shadow:0 0 0 3px rgba(224,96,127,.2); }
    .ccTB-bh{ display:flex; align-items:center; gap:6px; padding:7px 9px; border-radius:15px 15px 0 0; cursor:grab;
      background:linear-gradient(180deg,rgba(206,232,250,.95),rgba(184,216,242,.95)); border-bottom:1px solid rgba(140,200,240,.6); }
    .ccTB-bh:active{ cursor:grabbing; }
    .ccTB-bh .ccTB-lab{ flex:1; min-width:0; font-weight:800; font-size:.84rem; color:#0c3472; background:transparent;
      border:none; outline:none; font-family:"Nunito",sans-serif; padding:2px 3px; border-radius:6px; }
    .ccTB-bh .ccTB-lab:focus{ background:#fff; box-shadow:0 0 0 2px rgba(50,140,240,.3); }
    .ccTB-bh .ccTB-num{ font-size:.66rem; font-weight:800; color:#fff; background:#2f8ce0; padding:1px 7px; border-radius:8px; }
    .ccTB-bx{ border:none; background:transparent; color:#c62a4e; cursor:pointer; font-weight:800; font-size:.9rem; padding:0 2px; }
    .ccTB-rows{ padding:6px 8px 8px; }
    .ccTB-row{ display:flex; align-items:center; gap:5px; padding:3px 4px; margin:3px 0; border-radius:9px;
      background:rgba(230,242,252,.9); border:1.5px solid transparent; }
    .ccTB-row.drop{ border-color:#f3a712; background:rgba(243,167,18,.16); }
    .ccTB-row.fed{ background:linear-gradient(90deg,rgba(47,140,224,.16),rgba(230,242,252,.9)); }
    .ccTB-row .ccTB-ic{ font-size:.85rem; width:16px; text-align:center; }
    .ccTB-row select{ flex:1; min-width:0; font-size:.74rem; font-weight:700; font-family:"Nunito",sans-serif;
      color:#123a70; background:#fff; border:1px solid rgba(140,200,240,.7); border-radius:8px; padding:3px 4px; cursor:pointer; }
    .ccTB-row input.ccTB-inv{ flex:1; min-width:0; width:100%; font-size:.72rem; font-weight:700; font-family:"Nunito",sans-serif;
      color:#123a70; background:#fff; border:1px solid rgba(140,200,240,.7); border-radius:8px; padding:3px 5px; }
    .ccTB-row .ccTB-src{ flex:1; font-size:.72rem; font-weight:700; color:#1c6fc0; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .ccTB-row .ccTB-cut{ border:none; background:transparent; color:#5a7bb0; cursor:pointer; font-size:.8rem; }
    .ccTB-foot{ display:flex; align-items:center; gap:6px; padding:5px 8px 8px; flex-wrap:wrap; }
    .ccTB-step{ display:flex; align-items:center; gap:3px; font-size:.7rem; font-weight:800; color:#2050a0; }
    .ccTB-step button{ width:20px; height:20px; border-radius:6px; border:1px solid rgba(50,140,240,.5); background:#fff;
      color:#0c3472; font-weight:800; cursor:pointer; line-height:1; padding:0; }
    .ccTB-step button:disabled{ opacity:.35; cursor:not-allowed; }
    .ccTB-ports{ position:absolute; right:-11px; top:0; bottom:0; display:flex; flex-direction:column; justify-content:center; gap:6px; }
    .ccTB-port{ width:22px; height:22px; border-radius:50%; border:2px solid #fff; cursor:crosshair;
      background:linear-gradient(140deg,#f7b733,#fbbf58); box-shadow:0 2px 7px rgba(200,130,10,.4);
      font-size:.6rem; font-weight:900; color:#5a3800; display:flex; align-items:center; justify-content:center; }
    .ccTB-port.linking{ transform:scale(1.2); }
    .ccTB-port.used{ background:linear-gradient(140deg,#8fc4ee,#b6d9f4); box-shadow:0 2px 6px rgba(30,90,160,.25); color:#134b86; }

    .ccTB-side{ width:290px; flex:none; border-left:1.5px solid rgba(120,185,235,.45); background:rgba(255,255,255,.62);
      padding:12px; overflow:auto; }
    @media(max-width:860px){ .ccTB-side{ position:absolute; right:0; top:0; bottom:0; z-index:25; width:min(290px,86vw);
      box-shadow:-8px 0 24px rgba(8,50,130,.18); transform:translateX(100%); transition:transform .2s; }
      .ccTB-side.open{ transform:none; } }
    .ccTB-side h3{ margin:0 0 8px; font-family:"Cinzel",serif; font-size:.95rem; color:#0c3472; }
    .ccTB-stat{ display:flex; justify-content:space-between; padding:4px 0; font-size:.83rem; border-bottom:1px dashed rgba(120,160,210,.35); }
    .ccTB-stat b{ color:#0c6ab8; }
    .ccTB-err{ margin:6px 0; padding:8px 10px; border-radius:11px; font-size:.8rem; line-height:1.4;
      background:rgba(224,96,127,.13); border-left:3px solid #e0607f; color:#8c2340; }
    .ccTB-ok{ margin:8px 0; padding:9px 11px; border-radius:11px; font-size:.85rem; font-weight:700;
      background:rgba(10,157,99,.13); border-left:3px solid #0a9d63; color:#0a6a45; }
    .ccTB-tip{ font-size:.76rem; color:#3a6aa5; line-height:1.5; margin-top:10px; }
    .ccTB-zoom{ position:absolute; left:12px; bottom:12px; display:flex; gap:6px; z-index:20; }
    .ccTB-zoom button{ width:36px; height:36px; border-radius:11px; border:1.5px solid rgba(120,185,235,.7);
      background:rgba(255,255,255,.9); color:#1c4f92; font-size:1rem; font-weight:800; cursor:pointer; }
    @media (prefers-reduced-motion: reduce){ .ccTB-b,.ccTB-box{ transition:none!important; } }
    `;
    document.head.appendChild(s);
  }

  function ensureScreen() {
    let s = $("#ccTB");
    if (s) return s;
    injectStyles();
    s = el("div"); s.id = "ccTB";
    s.innerHTML = `
      <div class="ccTB-head">
        <h2>🎨 Bracket Builder</h2>
        <div class="ccTB-menu" id="ccTB-addm">
          <button class="ccTB-b sm" id="ccTB-add">＋ Add Match ▾</button>
          <div class="ccTB-menu-pop">
            <button data-n="2">2 players in this match</button>
            <button data-n="3">3 players in this match</button>
            <button data-n="4">4 players in this match</button>
            <button data-n="6">6 players in this match</button>
            <button data-n="8">8 players in this match</button>
          </div>
        </div>
        <div class="ccTB-menu" id="ccTB-roundm">
          <button class="ccTB-b sm" id="ccTB-round">＋ Add Round ▾</button>
          <div class="ccTB-menu-pop">
            <div class="ccTB-menu-note" id="ccTB-round-note"></div>
            <button data-per="0">Auto — fit everyone advancing</button>
            <button data-per="2">1 v 1 matches</button>
            <button data-per="3">3-player matches</button>
            <button data-per="4">4-player matches</button>
            <button data-per="6">6-player matches</button>
            <button data-per="8">8-player matches</button>
            <button data-act="connect">🔗 Connect leftovers to existing spots</button>
          </div>
        </div>
        <div class="ccTB-menu" id="ccTB-tplm">
          <button class="ccTB-b sm ghost" id="ccTB-tpl">📐 Quick start ▾</button>
          <div class="ccTB-menu-pop wide">
            <div class="ccTB-menu-note">Build a whole bracket, then edit anything.</div>
            <label class="ccTB-fld">Players
              <input type="range" id="ccTB-tpl-n" min="${MIN_FIELD}" max="${MAX_FIELD}" value="8">
              <b id="ccTB-tpl-n-v">8</b></label>
            <label class="ccTB-fld">Players per match
              <select id="ccTB-tpl-per"></select></label>
            <label class="ccTB-fld">Who advances
              <select id="ccTB-tpl-adv"></select></label>
            <div class="ccTB-menu-note" id="ccTB-tpl-note"></div>
            <button class="ccTB-b sm" id="ccTB-tpl-go" style="width:100%">Build this bracket</button>
          </div>
        </div>
        <button class="ccTB-b sm ghost" id="ccTB-tidy">⇄ Auto-arrange</button>
        <button class="ccTB-b sm ghost" id="ccTB-clear">✕ Clear</button>
        <div class="ccTB-sp"></div>
        <button class="ccTB-b sm ghost" id="ccTB-checks">✓ Checks</button>
        <button class="ccTB-b sm ghost" id="ccTB-cancel">Cancel</button>
        <button class="ccTB-b" id="ccTB-save" disabled>Use this bracket →</button>
      </div>
      <div class="ccTB-body">
        <div class="ccTB-canvas-wrap" id="ccTB-wrap">
          <svg class="ccTB-links" id="ccTB-links"></svg>
          <div class="ccTB-canvas" id="ccTB-canvas"></div>
          <div class="ccTB-zoom">
            <button id="ccTB-zin" title="Zoom in">＋</button>
            <button id="ccTB-zout" title="Zoom out">－</button>
            <button id="ccTB-zfit" title="Fit">⤢</button>
          </div>
        </div>
        <div class="ccTB-side" id="ccTB-side"></div>
      </div>`;
    document.body.appendChild(s);

    // One click-away handler closes every dropdown; each button toggles its own.
    const menus = $$(".ccTB-menu", s);
    const closeMenus = (except) => menus.forEach(m => { if (m !== except) m.classList.remove("open"); });
    menus.forEach(menu => {
      const btn = menu.querySelector(".ccTB-b");
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const open = !menu.classList.contains("open");
        closeMenus();
        menu.classList.toggle("open", open);
        if (open && menu.id === "ccTB-roundm") refreshRoundMenu();
        if (open && menu.id === "ccTB-tplm") refreshTemplateForm();
      });
      // Clicks INSIDE the popup must not bubble to the close-everything handler,
      // or the quick-start form would slam shut on its own sliders.
      const pop = menu.querySelector(".ccTB-menu-pop");
      if (pop) pop.addEventListener("click", (e) => e.stopPropagation());
    });
    document.addEventListener("click", () => closeMenus());

    const am = $("#ccTB-addm", s);
    $$(".ccTB-menu-pop button", am).forEach(b => b.addEventListener("click", () => {
      closeMenus(); addMatch(+b.dataset.n, "");
    }));
    const rm = $("#ccTB-roundm", s);
    $$(".ccTB-menu-pop button", rm).forEach(b => b.addEventListener("click", () => {
      closeMenus();
      if (b.dataset.act === "connect") autoConnect();
      else addRound(+b.dataset.per);
    }));
    wireTemplateForm(s, closeMenus);
    $("#ccTB-tidy", s).addEventListener("click", autoArrange);
    $("#ccTB-clear", s).addEventListener("click", () => {
      if (!B.matches.length || window.confirm("Clear the whole bracket and start over?")) { starterDesign(); render(); }
    });
    $("#ccTB-checks", s).addEventListener("click", () => $("#ccTB-side", s).classList.toggle("open"));
    $("#ccTB-cancel", s).addEventListener("click", close);
    $("#ccTB-save", s).addEventListener("click", save);
    $("#ccTB-zin", s).addEventListener("click", () => setZoom(B.zoom * 1.15));
    $("#ccTB-zout", s).addEventListener("click", () => setZoom(B.zoom / 1.15));
    $("#ccTB-zfit", s).addEventListener("click", fit);
    wireCanvas(s);
    return s;
  }

  // ── Add-Round menu: say exactly what the round will do before it's clicked ──
  function refreshRoundMenu() {
    const note = $("#ccTB-round-note"); if (!note) return;
    const pending = pendingFeeds();
    const n = pending.length;
    note.textContent = !n
      ? "Nothing is advancing yet — add some matches first."
      : n < MIN_SPOTS
        ? "One player is still advancing, so that match is already the Final."
        : `${n} players are advancing with nowhere to go. Pick a match size and they'll all be seated and connected.`;
    $$("#ccTB-roundm .ccTB-menu-pop button[data-per]").forEach(b => {
      const per = +b.dataset.per;
      const sizes = packRound(n, per);
      b.disabled = !sizes;
      const tail = sizes
        ? ` → ${sizes.length === 1 ? "the Final" : sizes.length + " matches"} (${sizes.join("·")})`
        : " — doesn't divide";
      b.textContent = (b.dataset.baseLabel || (b.dataset.baseLabel = b.textContent)) + tail;
    });
  }

  // ── Quick start: a whole bracket from players / match size / who advances ──
  const TPL = { n: 8, per: 2, adv: 1 };
  function wireTemplateForm(s, closeMenus) {
    const nEl = $("#ccTB-tpl-n", s), perEl = $("#ccTB-tpl-per", s), advEl = $("#ccTB-tpl-adv", s);
    nEl.addEventListener("input", () => { TPL.n = +nEl.value; refreshTemplateForm(); });
    perEl.addEventListener("change", () => { TPL.per = +perEl.value; TPL.adv = Math.min(TPL.adv, TPL.per - 1); refreshTemplateForm(); });
    advEl.addEventListener("change", () => { TPL.adv = +advEl.value; refreshTemplateForm(); });
    $("#ccTB-tpl-go", s).addEventListener("click", () => {
      if (B.matches.length && !window.confirm("Replace the bracket on the canvas with this one?")) return;
      const built = buildTemplate(TPL.n, TPL.per, TPL.adv);
      closeMenus();
      if (!built) { toast("That combination doesn't make a bracket — try a different match size.", "warn"); return; }
      autoArrange();
      toast(`Built a ${TPL.n}-player bracket: ${built.rounds} rounds, ${built.matches} matches.`, "ok");
    });
  }

  function refreshTemplateForm() {
    const nEl = $("#ccTB-tpl-n"), perEl = $("#ccTB-tpl-per"), advEl = $("#ccTB-tpl-adv");
    if (!nEl) return;
    nEl.value = TPL.n;
    $("#ccTB-tpl-n-v").textContent = TPL.n;
    perEl.innerHTML = "";
    for (let p = MIN_SPOTS; p <= Math.min(MAX_SPOTS, TPL.n); p++) {
      const o = el("option"); o.value = p; o.textContent = p === 2 ? "1 v 1" : p + " players";
      if (p === TPL.per) o.selected = true;
      perEl.appendChild(o);
    }
    TPL.per = clamp(TPL.per, MIN_SPOTS, Math.min(MAX_SPOTS, TPL.n));
    perEl.value = String(TPL.per);
    // "Top N advance" — 1 is single elimination, more makes each match a group.
    advEl.innerHTML = "";
    TPL.adv = clamp(TPL.adv, 1, Math.max(1, TPL.per - 1));
    for (let a = 1; a <= TPL.per - 1; a++) {
      const o = el("option"); o.value = a;
      o.textContent = a === 1 ? "Winner only (single elimination)" : `Top ${a} advance`;
      if (a === TPL.adv) o.selected = true;
      advEl.appendChild(o);
    }
    advEl.value = String(TPL.adv);
    // Dry-run the shape without touching the canvas.
    const saved = B.matches, savedSeq = B.seq;
    const built = buildTemplate(TPL.n, TPL.per, TPL.adv);
    B.matches = saved; B.seq = savedSeq;
    const note = $("#ccTB-tpl-note");
    if (note) {
      note.textContent = built
        ? `${TPL.n} players · ${built.rounds} rounds · ${built.matches} matches` +
          (TPL.adv > 1 ? ` · top ${TPL.adv} out of every match` : " · winner takes each match")
        : "That combination doesn't make a bracket.";
    }
  }

  function render() {
    const s = $("#ccTB"); if (!s) return;
    const canvas = $("#ccTB-canvas", s);
    canvas.innerHTML = "";
    B.errors = validate();
    const badIds = new Set();
    B.errors.forEach(e => B.matches.forEach(m => { if (e.indexOf(display(m.id)) >= 0) badIds.add(m.id); }));

    B.matches.forEach((m, i) => canvas.appendChild(matchBox(m, i, badIds.has(m.id))));
    // Transform FIRST: drawLinks measures the live DOM and divides by B.zoom, so the
    // canvas must already be showing that zoom or every line is scaled wrong.
    applyTransform();
    drawLinks();
    renderSide();
    $("#ccTB-save", s).disabled = B.errors.length > 0;
  }

  function matchBox(m, i, bad) {
    const box = el("div", "ccTB-box" + (B.sel === m.id ? " sel" : "") + (bad ? " bad" : ""));
    box.id = "ccTB-box-" + m.id;
    box.style.left = m.x + "px";
    box.style.top = m.y + "px";
    box.dataset.mid = m.id;

    const head = el("div", "ccTB-bh");
    head.innerHTML = `<span class="ccTB-grip" title="Drag to move">⠿</span><span class="ccTB-num">M${i + 1}</span>`;
    const lab = el("input", "ccTB-lab");
    lab.value = m.label; lab.placeholder = "Round 1"; lab.maxLength = 28;
    lab.setAttribute("list", "ccTB-labels");
    lab.addEventListener("input", () => { m.label = lab.value; scheduleSide(); });
    lab.addEventListener("mousedown", e => e.stopPropagation());
    lab.addEventListener("touchstart", e => e.stopPropagation(), { passive: true });
    head.appendChild(lab);
    const x = el("button", "ccTB-bx", "✕");
    x.title = "Delete this match";
    x.addEventListener("click", (e) => { e.stopPropagation(); removeMatch(m.id); });
    x.addEventListener("mousedown", e => e.stopPropagation());
    head.appendChild(x);
    box.appendChild(head);

    const rows = el("div", "ccTB-rows");
    m.slots.forEach((s, si) => rows.appendChild(slotRow(m, s, si)));
    box.appendChild(rows);

    const foot = el("div", "ccTB-foot");
    foot.appendChild(stepper("Players", m.slots.length, MIN_SPOTS, MAX_SPOTS, (v) => setSpots(m.id, v)));
    foot.appendChild(stepper("Advance", m.advance, 1, Math.max(1, m.slots.length - 1), (v) => setAdvance(m.id, v)));
    box.appendChild(foot);

    // One output port per advancing finisher — drag it onto a spot to connect.
    const ports = el("div", "ccTB-ports");
    for (let r = 1; r <= m.advance; r++) {
      const used = B.matches.some(t => t.slots.some(sl => sl.source === m.id && sl.rank === r));
      const p = el("div", "ccTB-port" + (used ? " used" : ""), r === 1 ? "🏅" : String(r));
      p.title = `${placeWord(r)} of ${display(m.id)} — drag onto a spot in a later match`;
      p.dataset.mid = m.id; p.dataset.rank = String(r);
      p.addEventListener("mousedown", (e) => startLink(e, m.id, r));
      p.addEventListener("touchstart", (e) => startLink(e, m.id, r), { passive: false });
      ports.appendChild(p);
    }
    box.appendChild(ports);
    // Drag from ANYWHERE on the box except its controls. The title bar alone is
    // mostly the label input, which would leave only a few draggable pixels.
    const grab = (e) => {
      B.sel = m.id;
      if (e.target.closest && e.target.closest("input, select, button, .ccTB-port")) return;
      startDrag(e, m);
    };
    box.addEventListener("mousedown", grab);
    box.addEventListener("touchstart", grab, { passive: false });
    return box;
  }

  function slotRow(m, s, si) {
    const fed = (s.kind === K_WINNER || s.kind === K_TOP);
    const row = el("div", "ccTB-row" + (fed ? " fed" : ""));
    row.dataset.mid = m.id; row.dataset.si = String(si);
    row.appendChild(el("span", "ccTB-ic", (KIND_META[s.kind] || KIND_META[K_OPEN]).icon));
    if (fed) {
      const src = byId(s.source);
      const txt = src ? `${placeWord(s.rank)} of ${esc(display(s.source))}` : "not connected";
      row.appendChild(el("span", "ccTB-src", txt));
      const cut = el("button", "ccTB-cut", "✕");
      cut.title = "Disconnect";
      cut.addEventListener("click", (e) => { e.stopPropagation(); disconnect(m.id, si); });
      cut.addEventListener("mousedown", e => e.stopPropagation());
      row.appendChild(cut);
    } else {
      const sel = el("select");
      ENTRY_KINDS.forEach(k => {
        const o = el("option"); o.value = k; o.textContent = KIND_META[k].name;
        if (k === s.kind) o.selected = true;
        sel.appendChild(o);
      });
      sel.title = (KIND_META[s.kind] || {}).hint || "";
      sel.addEventListener("change", () => setSlotKind(m.id, si, sel.value));
      sel.addEventListener("mousedown", e => e.stopPropagation());
      row.appendChild(sel);
      if (s.kind === K_INVITE) {
        const inv = el("input", "ccTB-inv");
        inv.value = s.invite; inv.placeholder = "player name"; inv.maxLength = 40;
        inv.addEventListener("input", () => { s.invite = inv.value; });
        inv.addEventListener("mousedown", e => e.stopPropagation());
        row.appendChild(inv);
      }
    }
    return row;
  }

  function stepper(label, val, lo, hi, onSet) {
    const wrap = el("div", "ccTB-step");
    const dec = el("button", "", "－"); dec.type = "button"; dec.disabled = val <= lo;
    const num = el("span", "", `${label}: ${val}`);
    const inc = el("button", "", "＋"); inc.type = "button"; inc.disabled = val >= hi;
    dec.addEventListener("click", (e) => { e.stopPropagation(); onSet(val - 1); });
    inc.addEventListener("click", (e) => { e.stopPropagation(); onSet(val + 1); });
    [dec, inc].forEach(b => b.addEventListener("mousedown", e => e.stopPropagation()));
    wrap.appendChild(dec); wrap.appendChild(num); wrap.appendChild(inc);
    return wrap;
  }

  function renderSide() {
    const side = $("#ccTB-side"); if (!side) return;
    const rows = layers();
    const entries = entryCount();
    const kinds = {};
    B.matches.forEach(m => m.slots.forEach(s => { if (ENTRY_KINDS.indexOf(s.kind) >= 0) kinds[s.kind] = (kinds[s.kind] || 0) + 1; }));
    const advs = Array.from(new Set(B.matches.map(m => m.advance))).sort((a, b) => a - b);
    const advTxt = !advs.length ? "—"
      : advs.length > 1 ? `Top ${advs[0]}–${advs[advs.length - 1]}`
      : advs[0] === 1 ? "Winner only" : `Top ${advs[0]}`;
    let html = `<h3>Your bracket</h3>
      <div class="ccTB-stat"><span>Matches</span><b>${B.matches.length}</b></div>
      <div class="ccTB-stat"><span>Starting player spots</span><b>${entries}</b></div>
      <div class="ccTB-stat"><span>Rounds</span><b>${rows.length}</b></div>
      <div class="ccTB-stat"><span>Advancing per match</span><b>${advTxt}</b></div>
      <div class="ccTB-stat"><span>Round shape</span><b>${rows.map(r => r.length).join(" → ") || "—"}</b></div>`;
    ENTRY_KINDS.forEach(k => {
      if (kinds[k]) html += `<div class="ccTB-stat"><span>${KIND_META[k].icon} ${KIND_META[k].name}</span><b>${kinds[k]}</b></div>`;
    });
    if (B.errors.length) {
      html += `<h3 style="margin-top:14px">Before you can open it</h3>`;
      B.errors.slice(0, 12).forEach(e => { html += `<div class="ccTB-err">${esc(e)}</div>`; });
      if (B.errors.length > 12) html += `<div class="ccTB-tip">…and ${B.errors.length - 12} more.</div>`;
    } else {
      html += `<div class="ccTB-ok">✓ Every check passed — this bracket is ready to open.</div>`;
      if (B.summary) {
        html += `<div class="ccTB-tip">${B.summary.tournament_size} players · ${B.summary.num_rounds} rounds ·
          ${B.summary.playable_matches} games. The winner of the Final is the champion.</div>`;
      }
    }
    const pending = pendingFeeds().length;
    if (pending >= MIN_SPOTS) {
      html += `<div class="ccTB-tip" style="color:#8a5a00"><b>${pending}</b> advancing player${pending === 1 ? "" : "s"}
        still need a seat — <b>＋ Add Round</b> seats and connects them all at once.</div>`;
    }
    html += `<div class="ccTB-tip"><b>How to build:</b><br>
      • <b>📐 Quick start</b> builds a whole bracket — players, match size, and who advances.<br>
      • <b>＋ Add Round</b> takes everyone still advancing and connects them into a new round in one click.<br>
      • <b>＋ Add Match</b> drops a single box — set <i>Players</i> (2–8) inside it.<br>
      • Drag a box anywhere (or by its ⠿ grip) to move it.<br>
      • Drag the gold 🏅 handle on a match's right edge onto a spot in another match to send its winner there.<br>
      • <b>Advance</b> sets how many players get out of a match (top 1, top 2, top 3…); each one gets its own handle.<br>
      • Every spot can be Open, Player only, AI only, or Invite only.</div>`;
    side.innerHTML = html;
  }

  function scheduleSide() {
    clearTimeout(B.checkTimer);
    B.checkTimer = setTimeout(() => { B.errors = validate(); renderSide(); const s = $("#ccTB-save"); if (s) s.disabled = B.errors.length > 0; }, 220);
  }

  // ── connection lines ──────────────────────────────────────────────────────
  function drawLinks() {
    const svg = $("#ccTB-links"); if (!svg) return;
    svg.innerHTML = "";
    const line = (x1, y1, x2, y2, cls) => {
      const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
      const mx = (x1 + x2) / 2;
      p.setAttribute("d", `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`);
      p.setAttribute("class", cls || "ccTB-link");
      svg.appendChild(p);
    };
    B.matches.forEach(m => m.slots.forEach((s, si) => {
      if (!s.source) return;
      const src = byId(s.source); if (!src) return;
      const from = portPoint(src, s.rank);
      const to = spotPoint(m, si);
      if (from && to) line(from.x, from.y, to.x, to.y);
    }));
    if (B.link && B.link.x != null) {
      const src = byId(B.link.from);
      const from = src && portPoint(src, B.link.rank);
      if (from) line(from.x, from.y, B.link.x, B.link.y, "ccTB-link ghost");
    }
  }

  // Geometry is MEASURED off the live DOM and converted back into canvas space, so
  // a line always lands on the exact pixel of the handle/spot it belongs to — at
  // any zoom, and whatever the CSS box metrics happen to be. (Hand-computing this
  // from the design is what put the lines in the wrong places: it assumed a port
  // pitch and a row height that the stylesheet doesn't actually produce.)
  function boxNode(m) { return $("#ccTB-box-" + m.id); }
  // A node's rect in CANVAS coordinates. The canvas has transform-origin 0 0, so
  // its own rect's top-left is canvas (0,0) and dividing by the zoom undoes scale.
  function rectIn(node) {
    const canvas = $("#ccTB-canvas");
    if (!canvas || !node || !node.isConnected) return null;
    const cr = canvas.getBoundingClientRect(), nr = node.getBoundingClientRect();
    const z = B.zoom || 1;
    return { left: (nr.left - cr.left) / z, top: (nr.top - cr.top) / z,
             width: nr.width / z, height: nr.height / z };
  }
  function boxHeight(m) { const n = boxNode(m); return n ? n.offsetHeight : 44 + m.slots.length * 30 + 36; }
  function portPoint(m, rank) {
    const node = boxNode(m);
    const p = node && node.querySelector(`.ccTB-port[data-rank="${rank}"]`);
    const r = rectIn(p);
    if (r) return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
    // Pre-render fallback: centred stack of ports on the right edge.
    const h = boxHeight(m), n = Math.max(1, m.advance), step = 32;
    return { x: m.x + BOX_W, y: m.y + h / 2 - ((n - 1) * step) / 2 + (rank - 1) * step };
  }
  function spotPoint(m, si) {
    const node = boxNode(m);
    const row = node && node.querySelector(`.ccTB-row[data-si="${si}"]`);
    const r = rectIn(row);
    if (r) return { x: r.left, y: r.top + r.height / 2 };
    return { x: m.x, y: m.y + 44 + si * 30 };
  }

  // ── canvas interaction ────────────────────────────────────────────────────
  function pt(e) {
    const t = (e.touches && e.touches[0]) || (e.changedTouches && e.changedTouches[0]) || e;
    return { x: t.clientX, y: t.clientY };
  }
  function toCanvas(p) {
    const wrap = $("#ccTB-wrap"); const r = wrap.getBoundingClientRect();
    return { x: (p.x - r.left - B.panX) / B.zoom, y: (p.y - r.top - B.panY) / B.zoom };
  }

  function startDrag(e, m) {
    if (e.button != null && e.button !== 0) return;
    e.preventDefault(); e.stopPropagation();
    const c = toCanvas(pt(e));
    B.sel = m.id;
    B.drag = { id: m.id, dx: c.x - m.x, dy: c.y - m.y, moved: false };
    document.body.style.userSelect = "none";
  }

  function startLink(e, mid, rank) {
    e.preventDefault(); e.stopPropagation();
    const c = toCanvas(pt(e));
    B.link = { from: mid, rank, x: c.x, y: c.y };
    e.currentTarget.classList.add("linking");
  }

  function wireCanvas(s) {
    const wrap = $("#ccTB-wrap", s);
    wrap.addEventListener("mousedown", (e) => {
      if (e.target.closest(".ccTB-box")) return;
      B.panDrag = { sx: e.clientX, sy: e.clientY, px: B.panX, py: B.panY };
      wrap.classList.add("grabbing");
    });
    wrap.addEventListener("touchstart", (e) => {
      if (e.target.closest(".ccTB-box") || e.touches.length !== 1) return;
      B.panDrag = { sx: e.touches[0].clientX, sy: e.touches[0].clientY, px: B.panX, py: B.panY };
    }, { passive: true });
    wrap.addEventListener("wheel", (e) => {
      e.preventDefault();
      setZoom(B.zoom * (e.deltaY < 0 ? 1.08 : 1 / 1.08));
    }, { passive: false });

    const move = (e) => {
      const p = pt(e);
      if (B.drag) {
        const c = toCanvas(p);
        const m = byId(B.drag.id); if (!m) return;
        m.x = Math.max(0, Math.round((c.x - B.drag.dx) / GRID) * GRID);
        m.y = Math.max(0, Math.round((c.y - B.drag.dy) / GRID) * GRID);
        B.drag.moved = true;
        const node = $("#ccTB-box-" + m.id);
        if (node) { node.style.left = m.x + "px"; node.style.top = m.y + "px"; }
        drawLinks();
        if (e.cancelable) e.preventDefault();
        return;
      }
      if (B.link) {
        const c = toCanvas(p);
        B.link.x = c.x; B.link.y = c.y;
        drawLinks();
        highlightDrop(p);
        if (e.cancelable) e.preventDefault();
        return;
      }
      if (B.panDrag) {
        const t = (e.touches && e.touches[0]) || e;
        B.panX = B.panDrag.px + (t.clientX - B.panDrag.sx);
        B.panY = B.panDrag.py + (t.clientY - B.panDrag.sy);
        applyTransform();
      }
    };
    const up = (e) => {
      if (B.link) {
        const target = dropTarget(pt(e));
        const link = B.link;
        B.link = null;
        $$(".ccTB-port.linking").forEach(n => n.classList.remove("linking"));
        $$(".ccTB-row.drop").forEach(n => n.classList.remove("drop"));
        if (target) connect(link.from, link.rank, target.mid, target.si);
        else { drawLinks(); toast("Drop the handle on a player spot in another match.", "info"); }
      }
      if (B.drag) { const moved = B.drag.moved; B.drag = null; document.body.style.userSelect = ""; if (moved) render(); }
      if (B.panDrag) { B.panDrag = null; const w = $("#ccTB-wrap"); if (w) w.classList.remove("grabbing"); }
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
    window.addEventListener("touchmove", move, { passive: false });
    window.addEventListener("touchend", up);
    window.addEventListener("touchcancel", up);
  }

  function dropTarget(p) {
    const node = document.elementFromPoint(p.x, p.y);
    const row = node && node.closest && node.closest(".ccTB-row");
    if (!row || !row.dataset.mid) return null;
    if (row.dataset.mid === (B.link && B.link.from)) return null;
    return { mid: row.dataset.mid, si: +row.dataset.si };
  }

  function highlightDrop(p) {
    $$(".ccTB-row.drop").forEach(n => n.classList.remove("drop"));
    const t = dropTarget(p);
    if (!t) return;
    const row = document.querySelector(`.ccTB-row[data-mid="${t.mid}"][data-si="${t.si}"]`);
    if (row) row.classList.add("drop");
  }

  function applyTransform() {
    const c = $("#ccTB-canvas"), svg = $("#ccTB-links");
    const tf = `translate(${B.panX}px,${B.panY}px) scale(${B.zoom})`;
    if (c) c.style.transform = tf;
    if (svg) svg.style.transform = tf;
  }
  function setZoom(z) { B.zoom = clamp(z, 0.35, 2); applyTransform(); }

  function fit() {
    const wrap = $("#ccTB-wrap"); if (!wrap || !B.matches.length) { B.zoom = 1; B.panX = B.panY = 0; applyTransform(); return; }
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    B.matches.forEach(m => {
      x0 = Math.min(x0, m.x); y0 = Math.min(y0, m.y);
      x1 = Math.max(x1, m.x + BOX_W); y1 = Math.max(y1, m.y + boxHeight(m));
    });
    const pad = 40, w = wrap.clientWidth, h = wrap.clientHeight;
    B.zoom = clamp(Math.min((w - pad * 2) / Math.max(1, x1 - x0), (h - pad * 2) / Math.max(1, y1 - y0)), 0.35, 1.15);
    B.panX = pad - x0 * B.zoom;
    B.panY = (h - (y1 - y0) * B.zoom) / 2 - y0 * B.zoom;
    applyTransform();
  }

  // Lay the design out left-to-right by round, so a hand-drawn tangle reads clean.
  function autoArrange() {
    const rows = layers();
    rows.forEach((row, r) => {
      row.forEach((m, i) => {
        m.x = 40 + r * (BOX_W + 110);
        m.y = 40 + i * 230;
      });
    });
    render();
    setTimeout(fit, 30);
  }

  // =========================================================================
  // Open / save
  // =========================================================================
  async function serverCheck() {
    // The client mirrors the rules, but the server is the authority — ask it, so a
    // design can never be "valid here, rejected there".
    if (B.checking) return;
    B.checking = true;
    try {
      const r = await bridge().post("/api/tournament/validate", { custom_graph: toDesign() });
      const d = r && r.data;
      if (d && Array.isArray(d.errors)) { B.errors = d.errors; B.summary = d.summary || null; }
      renderSide();
      const s = $("#ccTB-save"); if (s) s.disabled = B.errors.length > 0;
    } catch (_) { /* offline: the local check already gated the button */ }
    B.checking = false;
  }

  function open(existing, onSave) {
    injectStyles();
    const s = ensureScreen();
    B.onSave = onSave || null;
    if (existing && existing.matches && existing.matches.length) loadDesign(existing);
    else if (!B.matches.length) starterDesign();
    if (!$("#ccTB-labels")) {
      const dl = el("datalist"); dl.id = "ccTB-labels";
      LABEL_PRESETS.forEach(p => { const o = el("option"); o.value = p; dl.appendChild(o); });
      document.body.appendChild(dl);
    }
    s.classList.add("open");
    render();
    setTimeout(fit, 30);
    serverCheck();
  }

  function close() {
    const s = $("#ccTB"); if (s) s.classList.remove("open");
  }

  async function save() {
    B.errors = validate();
    if (B.errors.length) { renderSide(); toast(B.errors[0], "warn"); return; }
    const btn = $("#ccTB-save");
    if (btn) { btn.disabled = true; btn.textContent = "Checking…"; }
    let ok = true;
    try {
      const r = await bridge().post("/api/tournament/validate", { custom_graph: toDesign() });
      const d = r && r.data;
      if (d && Array.isArray(d.errors) && d.errors.length) {
        B.errors = d.errors; B.summary = null; ok = false; renderSide();
        toast(d.errors[0], "warn");
      } else if (d && d.summary) { B.summary = d.summary; }
    } catch (_) { /* offline: trust the local check */ }
    if (btn) { btn.disabled = false; btn.textContent = "Use this bracket →"; }
    if (!ok) return;
    close();
    if (B.onSave) B.onSave(toDesign(), B.summary);
  }

  window.__ccTourneyBuilder = {
    open,
    close,
    design: toDesign,
    validate: () => validate(),          // exposed for tests
    _state: B,
    // Pure design logic, exported so the node test can check it against the Python
    // validator (test_tournament_builder.js) without a browser.
    _test: { buildTemplate, packRound, pendingFeeds, loadDesign, toDesign, layers, relabelAuto },
  };
})();
