/* Currents and Critters · plush pre-order reservations
 * ─────────────────────────────────────────────────────────────────────────
 * ONE script for BOTH websites:
 *   currentsandcritters.com/preorder   (and the Store tab, compact)
 *   beardedsealstudios.com/preorder    (Squarespace code block loads this file
 *                                       from currentsandcritters.com)
 *
 * Everything priced comes from plush/catalog.json, the same file the game
 * server recomputes every reservation from (plush_reserve.py). Nothing on this
 * page decides a price that gets stored.
 *
 * Mounts:
 *   <div data-cc-preorder="full"    data-site="cc|bss"></div>  the whole page
 *   <div data-cc-preorder="compact" data-site="cc"></div>       bar + summary + link
 *   <span data-ccpo-qty="whale-shark"></span>                   a stepper, anywhere
 *
 * The rules shown to the customer, in one place (and mirrored server-side):
 *   $39.99 each + shipping and applicable sales tax.
 *   3 or more plushies (any mix) = free standard shipping to U.S. addresses.
 *   1 or 2 = shipping calculated at checkout. Tax always calculated at checkout.
 *   A reservation is free: nothing is charged today.
 */
(function () {
  "use strict";
  if (window.__ccPreorderLoaded) return;
  window.__ccPreorderLoaded = true;

  var SCRIPT = document.currentScript ||
    (function () { var s = document.querySelectorAll("script[src*='preorder.js']"); return s[s.length - 1]; })();
  var BASE = (function () {
    try { return new URL(".", SCRIPT.src).href; } catch (e) { return "https://www.currentsandcritters.com/plush/"; }
  })();
  var API = (window.CC_PREORDER_API || "https://play.currentsandcritters.com").replace(/\/$/, "");
  var STORE_KEY = "ccPlushCart.v1";
  var COUNTRIES = [["US", "United States"], ["CA", "Canada"], ["GB", "United Kingdom"],
                   ["AU", "Australia"], ["OTHER", "Another country"]];

  /* ── tiny helpers ─────────────────────────────────────────────────────── */
  function h(tag, attrs, kids) {
    var el = document.createElement(tag);
    if (attrs) for (var k in attrs) {
      if (!Object.prototype.hasOwnProperty.call(attrs, k) || attrs[k] == null || attrs[k] === false) continue;
      if (k === "class") el.className = attrs[k];
      else if (k === "text") el.textContent = attrs[k];
      else if (k === "html") el.innerHTML = attrs[k];
      else if (k.slice(0, 2) === "on") el.addEventListener(k.slice(2), attrs[k]);
      else el.setAttribute(k, attrs[k] === true ? "" : attrs[k]);
    }
    (kids || []).forEach(function (c) { if (c != null) el.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
    return el;
  }
  function money(cents) { cents = Math.round(cents); return "$" + Math.floor(cents / 100).toLocaleString("en-US") + "." + ("0" + (cents % 100)).slice(-2); }
  function plural(n, one, many) { return n === 1 ? one : many; }
  function icon(name) {
    var P = {
      check: '<path d="M5 12.5l4.2 4.2L19 7" />',
      truck: '<path d="M3 7h11v8H3z"/><path d="M14 10h4l3 3v2h-7z"/><circle cx="7" cy="17" r="1.7"/><circle cx="17" cy="17" r="1.7"/>',
      lock: '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
      heart: '<path d="M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10z"/>',
      wave: '<path d="M2 15c2.5 0 2.5-2 5-2s2.5 2 5 2 2.5-2 5-2 2.5 2 5 2"/><path d="M2 10c2.5 0 2.5-2 5-2s2.5 2 5 2 2.5-2 5-2 2.5 2 5 2"/>',
      feather: '<path d="M20 4c-7 0-12 5-12 12v4"/><path d="M8 16c4 0 8-2 10-6"/>',
      weight: '<path d="M6 9h12l2 11H4z"/><circle cx="12" cy="6" r="2.5"/>',
      spark: '<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M18 6l-2.5 2.5M8.5 15.5 6 18"/>',
      gift: '<rect x="4" y="9" width="16" height="11" rx="1.5"/><path d="M4 13h16M12 9v11"/><path d="M12 9c-3 0-5-1.2-5-3a2 2 0 0 1 4-.5L12 9zm0 0c3 0 5-1.2 5-3a2 2 0 0 0-4-.5L12 9z"/>',
      game: '<rect x="3" y="7" width="18" height="11" rx="4"/><path d="M8 11v3M6.5 12.5h3"/><circle cx="16" cy="11.5" r="1"/><circle cx="18" cy="13.5" r="1"/>',
      calendar: '<rect x="4" y="5" width="16" height="15" rx="2"/><path d="M4 10h16M9 3v4M15 3v4"/>',
      mail: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m4 7 8 6 8-6"/>',
      card: '<rect x="3" y="6" width="18" height="13" rx="2"/><path d="M3 10h18"/>',
      spin: '<path d="M4 12a8 8 0 0 1 14-5.3M20 12a8 8 0 0 1-14 5.3"/><path d="M18 3v4h-4M6 21v-4h4"/>',
      plus: '<path d="M12 5v14M5 12h14"/>', minus: '<path d="M5 12h14"/>',
      arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>', left: '<path d="M15 6l-6 6 6 6"/>', right: '<path d="M9 6l6 6-6 6"/>'
    };
    var s = document.createElement("span");
    s.className = "ccpo-ico";
    s.setAttribute("aria-hidden", "true");
    s.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' + (P[name] || "") + "</svg>";
    return s;
  }
  function storeGet() { try { return JSON.parse(localStorage.getItem(STORE_KEY) || "null"); } catch (e) { return null; } }
  function storeSet(v) { try { localStorage.setItem(STORE_KEY, JSON.stringify(v)); } catch (e) { /* private mode */ } }
  function daysTo(iso) {
    var p = String(iso).split("-");
    var launch = new Date(+p[0], +p[1] - 1, +p[2]);
    var now = new Date(); now.setHours(0, 0, 0, 0);
    return Math.max(0, Math.round((launch - now) / 864e5));
  }
  function reduceMotion() { return window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches; }

  /* ── the catalog ──────────────────────────────────────────────────────── */
  var CAT = null;
  var catalogReady = (window.CC_PLUSH_CATALOG ? Promise.resolve(window.CC_PLUSH_CATALOG)
    : fetch(BASE + "catalog.json", { credentials: "omit" }).then(function (r) {
        if (!r.ok) throw new Error("catalog " + r.status); return r.json();
      })).then(function (c) { CAT = c; return c; });

  /* ── the pricing rules (mirror of plush_reserve.quote) ────────────────── */
  function quote(items, country) {
    var count = 0, subtotal = 0, lines = [];
    CAT.products.forEach(function (p) {
      var q = Math.max(0, Math.min(CAT.maxPerItem, items[p.id] | 0));
      if (q > 0) { lines.push({ p: p, qty: q, cents: q * p.priceCents }); count += q; subtotal += q * p.priceCents; }
    });
    var min = CAT.freeShippingMin;
    var qualifies = count >= min;
    var covered = CAT.freeShippingCountries.indexOf((country || "US").toUpperCase()) !== -1;
    return { lines: lines, count: count, subtotal: subtotal, qualifies: qualifies,
             free: qualifies && covered, covered: covered, remaining: Math.max(0, min - count), min: min };
  }
  function shipMessage(q) {
    if (q.qualifies && !q.covered) return "Free standard shipping is for U.S. addresses. We'll quote your shipping at checkout.";
    if (q.count >= q.min) return "You’ve unlocked free shipping!";
    if (q.count === 0) return "Add " + q.min + " plushies to unlock free shipping.";
    if (q.remaining === 1) return "Add 1 more plushie to unlock free shipping.";
    return "You’re " + q.remaining + " plushies away from free shipping.";
  }

  /* ── one shared cart for every mount on the page ──────────────────────── */
  var state = { items: {}, country: "US" };
  var listeners = [];
  function emit() { var q = quote(state.items, state.country); listeners.forEach(function (fn) { fn(q); }); storeSet(state); }
  function setQty(id, n) {
    n = Math.max(0, Math.min(CAT.maxPerItem, Math.floor(+n || 0)));
    var others = 0;
    for (var k in state.items) if (k !== id) others += state.items[k] | 0;
    n = Math.min(n, Math.max(0, CAT.maxPerOrder - others));
    state.items[id] = n; emit();
  }
  function loadState() {
    var saved = storeGet();
    if (saved && saved.items) { state.items = saved.items; state.country = saved.country || "US"; }
    var known = {}; CAT.products.forEach(function (p) { known[p.id] = 1; if (state.items[p.id] == null) state.items[p.id] = 0; });
    for (var k in state.items) if (!known[k]) delete state.items[k];
    // ?whale-shark=2&loggerhead-sea-turtle=1 (links from the Store tab and the shop) win over a saved cart.
    try {
      var qs = new URLSearchParams(location.search), fromLink = false;
      CAT.products.forEach(function (p) {
        if (qs.has(p.id)) { state.items[p.id] = Math.max(0, Math.min(CAT.maxPerItem, parseInt(qs.get(p.id), 10) || 0)); fromLink = true; }
      });
      var add = qs.get("add");
      if (add && known[add] && !fromLink && !(state.items[add] > 0)) state.items[add] = 1;
    } catch (e) { /* old browser: keep the saved cart */ }
  }

  /* ── components ───────────────────────────────────────────────────────── */
  function stepper(p, opts) {
    opts = opts || {};
    var input = h("input", { type: "text", inputmode: "numeric", pattern: "[0-9]*", maxlength: "2",
      class: "ccpo-qty-n", "aria-label": p.name + " quantity", value: "0" });
    var minus = h("button", { type: "button", class: "ccpo-qty-btn", "aria-label": "Remove one " + p.name }, [icon("minus")]);
    var plus = h("button", { type: "button", class: "ccpo-qty-btn", "aria-label": "Add one " + p.name }, [icon("plus")]);
    var wrap = h("div", { class: "ccpo-qty" + (opts.small ? " is-small" : ""), role: "group", "aria-label": p.name + " quantity" }, [minus, input, plus]);
    minus.addEventListener("click", function () { setQty(p.id, (state.items[p.id] | 0) - 1); });
    plus.addEventListener("click", function () { setQty(p.id, (state.items[p.id] | 0) + 1); bump(wrap); });
    input.addEventListener("input", function () { input.value = input.value.replace(/\D/g, "").slice(0, 2); });
    input.addEventListener("change", function () { setQty(p.id, parseInt(input.value, 10) || 0); input.value = String(state.items[p.id] | 0); });
    input.addEventListener("blur", function () { input.value = String(state.items[p.id] | 0); });
    input.addEventListener("keydown", function (e) {
      if (e.key === "ArrowUp") { e.preventDefault(); setQty(p.id, (state.items[p.id] | 0) + 1); }
      if (e.key === "ArrowDown") { e.preventDefault(); setQty(p.id, (state.items[p.id] | 0) - 1); }
      if (e.key === "Enter") { e.preventDefault(); setQty(p.id, parseInt(input.value, 10) || 0); input.value = String(state.items[p.id] | 0); }
    });
    listeners.push(function () {
      var n = state.items[p.id] | 0;
      if (document.activeElement !== input) input.value = String(n);
      minus.disabled = n <= 0;
      var total = 0; for (var k in state.items) total += state.items[k] | 0;
      plus.disabled = n >= CAT.maxPerItem || total >= CAT.maxPerOrder;
      wrap.classList.toggle("has-qty", n > 0);
    });
    return wrap;
  }
  function bump(el) {
    if (reduceMotion()) return;
    el.classList.remove("is-bump"); void el.offsetWidth; el.classList.add("is-bump");
  }

  function shipBar(opts) {
    opts = opts || {};
    var msg = h("p", { class: "ccpo-ship-msg", "aria-live": "polite" });
    var fill = h("span", { class: "ccpo-ship-fill" });
    var steps = [];
    var track = h("div", { class: "ccpo-ship-track", role: "progressbar", "aria-valuemin": "0", "aria-valuemax": String(CAT.freeShippingMin) }, [fill]);
    for (var i = 1; i <= CAT.freeShippingMin; i++) {
      var s = h("span", { class: "ccpo-ship-step", style: "left:" + (i / CAT.freeShippingMin * 100) + "%" }, [i === CAT.freeShippingMin ? icon("truck") : h("b", { text: String(i) })]);
      steps.push(s); track.appendChild(s);
    }
    var note = h("p", { class: "ccpo-ship-note", text: "Any mix of plushies counts. Free standard shipping to U.S. addresses; sales tax still applies." });
    var bar = h("div", { class: "ccpo-ship" + (opts.compact ? " is-compact" : "") }, [
      h("div", { class: "ccpo-ship-head" }, [h("span", { class: "ccpo-ship-badge" }, [icon("truck")]), msg]), track, opts.compact ? null : note]);
    var was = false;
    listeners.push(function (q) {
      var n = Math.min(q.count, q.min);
      fill.style.width = (n / q.min * 100) + "%";
      track.setAttribute("aria-valuenow", String(n));
      track.setAttribute("aria-valuetext", q.count + " of " + q.min + " plushies toward free shipping");
      msg.textContent = shipMessage(q);
      steps.forEach(function (s, i) { s.classList.toggle("is-on", q.count >= i + 1); });
      var on = q.count >= q.min;
      bar.classList.toggle("is-unlocked", on);
      bar.classList.toggle("is-uncovered", on && !q.covered);
      if (on && !was) bump(bar);
      was = on;
    });
    return bar;
  }

  function spinner(p) {
    var idx = 0, frames = [], chips = [];
    var stage = h("div", { class: "ccpo-spin-stage", style: "--ccpo-scale:" + (p.scale || 1) });
    p.frames.forEach(function (f, i) {
      var img = h("img", { class: "ccpo-spin-frame" + (i === 0 ? " is-on" : ""), src: f.src + "?format=750w",
        alt: p.name + " plush, " + f.label.toLowerCase() + " view", draggable: "false", decoding: "async",
        loading: i === 0 ? "eager" : "lazy", width: "750", height: "750" });
      frames.push(img); stage.appendChild(img);
    });
    var box = h("div", { class: "ccpo-spin", tabindex: "0", role: "group",
      "aria-label": p.name + ", 360 degree view. Drag, or use the left and right arrow keys, to turn it." }, [
      h("span", { class: "ccpo-spin-tag" }, [icon("spin"), "360° view"]), stage,
      h("button", { type: "button", class: "ccpo-spin-arrow is-prev", "aria-label": "Turn left", onclick: function () { go(idx - 1); } }, [icon("left")]),
      h("button", { type: "button", class: "ccpo-spin-arrow is-next", "aria-label": "Turn right", onclick: function () { go(idx + 1); } }, [icon("right")]),
      h("span", { class: "ccpo-spin-hint", "aria-hidden": "true", text: "⟷ Drag to spin" })]);
    var chipRow = h("div", { class: "ccpo-spin-chips", role: "group", "aria-label": "Pick a view" });
    p.frames.forEach(function (f, i) {
      var c = h("button", { type: "button", class: "ccpo-spin-chip" + (i === 0 ? " is-on" : ""), "aria-pressed": i === 0 ? "true" : "false", text: f.label, onclick: function () { go(i); } });
      chips.push(c); chipRow.appendChild(c);
    });
    function go(i) {
      var n = frames.length; idx = ((i % n) + n) % n;
      frames.forEach(function (f, j) { f.classList.toggle("is-on", j === idx); });
      chips.forEach(function (c, j) { c.classList.toggle("is-on", j === idx); c.setAttribute("aria-pressed", j === idx ? "true" : "false"); });
      box.classList.add("was-used");
    }
    var startX = null, startIdx = 0;
    box.addEventListener("pointerdown", function (e) {
      if (e.target.closest("button")) return;
      startX = e.clientX; startIdx = idx; box.classList.add("is-dragging");
      try { box.setPointerCapture(e.pointerId); } catch (er) { /* fine */ }
    });
    box.addEventListener("pointermove", function (e) {
      if (startX == null) return;
      var step = Math.round((e.clientX - startX) / 48);
      if (startIdx - step !== idx) go(startIdx - step);
    });
    function end() { startX = null; box.classList.remove("is-dragging"); }
    box.addEventListener("pointerup", end); box.addEventListener("pointercancel", end);
    box.addEventListener("keydown", function (e) {
      if (e.key === "ArrowLeft") { e.preventDefault(); go(idx - 1); }
      if (e.key === "ArrowRight") { e.preventDefault(); go(idx + 1); }
    });
    return h("div", { class: "ccpo-spinwrap" }, [box, chipRow]);
  }

  function productCard(p) {
    var line = h("p", { class: "ccpo-line", "aria-live": "polite" });
    listeners.push(function () {
      var n = state.items[p.id] | 0;
      line.textContent = n ? n + " × " + money(p.priceCents) + " = " + money(n * p.priceCents) : "Choose how many you'd like";
      card.classList.toggle("is-picked", n > 0);
    });
    var inches = Math.round(p.sizeCm / 2.54 * 10) / 10;
    var card = h("article", { class: "ccpo-card", id: "ccpo-" + p.id }, [
      spinner(p),
      h("div", { class: "ccpo-card-body" }, [
        h("p", { class: "ccpo-kicker" }, [h("span", { class: "ccpo-dot" }), p.family + " · First run"]),
        h("h3", { class: "ccpo-card-title", text: p.name }),
        h("p", { class: "ccpo-price" }, [h("b", { text: money(p.priceCents) }), h("span", { text: " " + CAT.priceNote })]),
        h("p", { class: "ccpo-blurb", text: p.blurb }),
        h("ul", { class: "ccpo-specs", "aria-label": "Size and weight" }, [
          h("li", null, [icon("spin"), h("b", { text: p.sizeCm + " cm" }), h("span", { text: " (about " + inches + " in)" })]),
          h("li", null, [icon("weight"), h("b", { text: p.weightLb + " lbs" }), h("span", { text: " weighted" })])]),
        h("ul", { class: "ccpo-feats" }, CAT.features.map(function (f) { return h("li", null, [icon("check"), f]); })),
        h("div", { class: "ccpo-buyrow" }, [stepper(p), line])
      ])
    ]);
    return card;
  }

  function summary(opts) {
    opts = opts || {};
    var list = h("ul", { class: "ccpo-lines" });
    var rows = {
      subtotal: h("dd", null), shipping: h("dd", null), tax: h("dd", { text: "Calculated at checkout" }), total: h("dd", null)
    };
    var totalLabel = h("dt", null);
    var dl = h("dl", { class: "ccpo-totals" }, [
      h("dt", { text: "Merchandise subtotal" }), rows.subtotal,
      h("dt", { text: "Shipping" }), rows.shipping,
      h("dt", { text: "Sales tax" }), rows.tax,
      totalLabel, rows.total]);
    var due = h("div", { class: "ccpo-due" }, [icon("lock"), h("span", null, [h("b", { text: "Due today: $0.00." }), " Reserving is free. You'll confirm and pay only when pre-orders open."])]);
    listeners.push(function (q) {
      list.innerHTML = "";
      if (!q.lines.length) {
        list.appendChild(h("li", { class: "ccpo-empty", text: "No plushies yet. Add a Whale Shark, a Loggerhead, or both." }));
      }
      q.lines.forEach(function (l) {
        list.appendChild(h("li", null, [
          h("img", { src: l.p.frames[0].src + "?format=100w", alt: "", width: "44", height: "44", loading: "lazy" }),
          h("span", { class: "ccpo-lname" }, [h("b", { text: l.p.name }), h("small", { text: l.qty + " × " + money(l.p.priceCents) })]),
          h("span", { class: "ccpo-lamt", text: money(l.cents) })]));
      });
      rows.subtotal.textContent = money(q.subtotal);
      rows.shipping.innerHTML = "";
      if (q.free) rows.shipping.appendChild(h("span", { class: "ccpo-free" }, [icon("check"), "Free standard shipping"]));
      else rows.shipping.textContent = "Calculated at checkout";
      totalLabel.textContent = q.free ? "Total before tax" : "Total before shipping & tax";
      rows.total.textContent = money(q.subtotal);
    });
    return h("div", { class: "ccpo-summary-body" }, [list, dl, opts.noDue ? null : due]);
  }

  /* ── the reservation form ─────────────────────────────────────────────── */
  var serverStatus = null;
  function wake() {
    if (wake.p) return wake.p;
    wake.p = fetch(API + "/api/plush/reserve", { credentials: "omit", cache: "no-store" })
      .then(function (r) { return r.json(); }).then(function (d) { serverStatus = d; return d; })
      .catch(function () { wake.p = null; return null; });
    return wake.p;
  }

  function field(label, input, hint) {
    var id = input.id;
    var err = h("p", { class: "ccpo-err", id: id + "-err", "aria-live": "polite" });
    input.setAttribute("aria-describedby", id + "-err");
    return h("div", { class: "ccpo-field" }, [h("label", { for: id }, [label, hint ? h("span", { class: "ccpo-opt", text: " " + hint }) : null]), input, err]);
  }

  function form(site) {
    var inbox = site === "bss" ? "timothy.honey@beardedsealstudios.com" : "currentsandcritters@gmail.com";
    var privacy = site === "bss" ? "https://www.currentsandcritters.com/privacy" : "/privacy";
    var f = {
      first: h("input", { id: "ccpo-first", name: "given-name", autocomplete: "given-name", required: true, maxlength: "60" }),
      last: h("input", { id: "ccpo-last", name: "family-name", autocomplete: "family-name", required: true, maxlength: "60" }),
      email: h("input", { id: "ccpo-email", name: "email", type: "email", autocomplete: "email", required: true, maxlength: "254", inputmode: "email" }),
      country: h("select", { id: "ccpo-country", name: "country", autocomplete: "country" },
        COUNTRIES.map(function (c) { return h("option", { value: c[0], text: c[1] }); })),
      zip: h("input", { id: "ccpo-zip", name: "postal-code", autocomplete: "postal-code", maxlength: "12", inputmode: "text" }),
      news: h("input", { id: "ccpo-news", type: "checkbox" }),
      trap: h("input", { name: "company_site", tabindex: "-1", autocomplete: "off" })
    };
    f.country.value = state.country || "US";
    f.country.addEventListener("change", function () { state.country = f.country.value; emit(); });
    var btnLabel = h("span", { text: "Reserve my plushies" });
    var btn = h("button", { type: "submit", class: "ccpo-btn ccpo-btn-primary ccpo-submit" }, [btnLabel, icon("arrow")]);
    var status = h("div", { class: "ccpo-status", role: "status", "aria-live": "polite" });
    var errBox = h("div", { class: "ccpo-errbox", role: "alert", tabindex: "-1", hidden: true });

    var el = h("form", { class: "ccpo-form", novalidate: true }, [
      h("h3", { class: "ccpo-form-title" }, ["Your details"]),
      h("p", { class: "ccpo-form-sub", text: "Just what we need to save your spot and email you when pre-orders open." }),
      errBox,
      h("div", { class: "ccpo-grid2" }, [field("First name", f.first), field("Last name", f.last)]),
      field("Email", f.email),
      h("div", { class: "ccpo-grid2" }, [field("Ships to", f.country), field("ZIP / postal code", f.zip, "(optional)")]),
      h("label", { class: "ccpo-check" }, [f.news, h("span", { class: "ccpo-box", "aria-hidden": "true" }, [icon("check")]),
        h("span", { text: "Send me Currents and Critters news too (optional)." })]),
      h("div", { class: "ccpo-trap", "aria-hidden": "true" }, [h("label", null, ["Company website", f.trap])]),
      btn, status,
      h("p", { class: "ccpo-fine", html: "Free and non-binding: no card needed and nothing is charged today. When pre-orders open we'll email you to confirm, and shipping and sales tax are calculated at checkout. We only use your details for this reservation. <a href=\"" + privacy + "\" target=\"_blank\" rel=\"noopener\">Privacy Policy</a>" })
    ]);

    function setErr(input, msg) {
      var e = document.getElementById(input.id + "-err");
      if (e) e.textContent = msg || "";
      input.setAttribute("aria-invalid", msg ? "true" : "false");
      input.closest(".ccpo-field").classList.toggle("is-bad", !!msg);
    }
    [f.first, f.last, f.email, f.zip].forEach(function (i) { i.addEventListener("input", function () { setErr(i, ""); }); });

    function check() {
      var bad = [];
      var first = f.first.value.trim(), last = f.last.value.trim(), email = f.email.value.trim(), zip = f.zip.value.trim();
      setErr(f.first, first ? "" : "Please add your first name."); if (!first) bad.push(f.first);
      setErr(f.last, last ? "" : "Please add your last name."); if (!last) bad.push(f.last);
      var okEmail = /^[^\s@,;<>]+@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)*\.[A-Za-z]{2,}$/.test(email);
      setErr(f.email, okEmail ? "" : (email ? "That email address doesn't look right." : "Please add your email.")); if (!okEmail) bad.push(f.email);
      var zipOk = !zip || (f.country.value === "US" ? /^\d{5}(-\d{4})?$/.test(zip) : /^[A-Za-z0-9][A-Za-z0-9 \-]{1,10}[A-Za-z0-9]$/.test(zip));
      setErr(f.zip, zipOk ? "" : (f.country.value === "US" ? "Use a 5-digit ZIP, like 37203." : "That postal code doesn't look right.")); if (!zipOk) bad.push(f.zip);
      return bad;
    }

    var busy = false;
    el.addEventListener("submit", function (e) {
      e.preventDefault();
      if (busy) return;                       // one submission at a time
      errBox.hidden = true; status.textContent = "";
      var q = quote(state.items, state.country);
      var bad = check();
      if (!q.count) {
        errBox.hidden = false; errBox.textContent = "Choose at least one plushie first.";
        errBox.focus(); return;
      }
      if (bad.length) { bad[0].focus(); return; }
      busy = true; btn.disabled = true; el.classList.add("is-busy"); btnLabel.textContent = "Reserving…";
      var slow = setTimeout(function () { status.textContent = "Waking our server. The first request can take up to a minute; please keep this page open."; }, 5000);
      var ctrl = window.AbortController ? new AbortController() : null;
      var kill = setTimeout(function () { if (ctrl) ctrl.abort(); }, 90000);
      var items = {}; CAT.products.forEach(function (p) { items[p.id] = state.items[p.id] | 0; });
      var payload = { first: f.first.value.trim(), last: f.last.value.trim(), email: f.email.value.trim(),
        country: f.country.value, zip: f.zip.value.trim(), items: items, newsletter: f.news.checked,
        site: site, company_site: f.trap.value, catalogVersion: CAT.version };
      fetch(API + "/api/plush/reserve", { method: "POST", credentials: "omit", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload), signal: ctrl ? ctrl.signal : undefined })
        .then(function (r) { return r.json().catch(function () { return { ok: false, message: "" }; }); })
        .then(function (d) {
          if (!d || !d.ok) throw new Error((d && d.message) || "");
          done(d, payload);
        })
        .catch(function (err) {
          var msg = (err && err.message && err.message !== "Failed to fetch" && err.name !== "AbortError") ? err.message
            : "We couldn't reach our reservation server just now.";
          var body = "Hi! I'd like to reserve:\n" + q.lines.map(function (l) { return "- " + l.qty + " x " + l.p.name; }).join("\n") +
            "\n\nName: " + payload.first + " " + payload.last + "\nShips to: " + payload.country + " " + payload.zip + "\n";
          errBox.hidden = false; errBox.innerHTML = "";
          errBox.appendChild(document.createTextNode(msg + " "));
          errBox.appendChild(h("a", { href: "mailto:" + inbox + "?subject=" + encodeURIComponent("Plush reservation") + "&body=" + encodeURIComponent(body), text: "Email your reservation to us instead →" }));
          errBox.focus();
        })
        .then(function () {
          clearTimeout(slow); clearTimeout(kill); busy = false; btn.disabled = false; el.classList.remove("is-busy");
          btnLabel.textContent = "Reserve my plushies"; if (!errBox.hidden) status.textContent = "";
        });
    });

    function done(d, payload) {
      var wrap = el.parentNode;
      var lines = (d.lines || []).map(function (l) { return h("li", null, [h("span", { text: l.qty + " × " + l.name }), h("b", { text: money(l.lineCents) })]); });
      var ok = h("div", { class: "ccpo-done", tabindex: "-1" }, [
        h("div", { class: "ccpo-done-badge" }, [icon("check")]),
        h("h3", { text: d.updated ? "Reservation updated!" : "You’re on the list, " + payload.first + "!" }),
        h("p", { text: "We'll email " + payload.email + " the moment pre-orders open, so you can confirm and pay. Nothing has been charged." }),
        h("ul", { class: "ccpo-done-lines" }, lines),
        h("p", { class: "ccpo-done-meta" }, [
          "Subtotal ", h("b", { text: money(d.subtotalCents) }), d.freeShipping ? " · Free standard shipping" : " · Shipping calculated at checkout",
          " · Sales tax calculated at checkout"]),
        h("p", { class: "ccpo-ref" }, ["Reference ", h("b", { text: d.reference })]),
        d.newsletterError ? h("p", { class: "ccpo-fine", text: d.newsletterError }) : null,
        h("button", { type: "button", class: "ccpo-btn ccpo-btn-ghost", text: "Change my reservation", onclick: function () { ok.remove(); el.hidden = false; f.first.focus(); } })
      ]);
      el.hidden = true;
      wrap.insertBefore(ok, el.nextSibling);
      ok.focus();
    }

    // Wake the server as soon as somebody starts filling the form in.
    el.addEventListener("focusin", function () { wake(); }, { once: true });
    return el;
  }

  /* ── the mobile dock ──────────────────────────────────────────────────── */
  function dock(target) {
    var txt = h("span", { class: "ccpo-dock-txt" });
    var fill = h("span", { class: "ccpo-dock-fill" });
    var d = h("div", { class: "ccpo-dock", "aria-hidden": "true" }, [
      h("div", { class: "ccpo-dock-in" }, [
        h("div", { class: "ccpo-dock-main" }, [txt, h("span", { class: "ccpo-dock-track" }, [fill])]),
        h("button", { type: "button", class: "ccpo-btn ccpo-btn-primary ccpo-dock-btn", tabindex: "-1", onclick: function () {
          target.scrollIntoView({ behavior: reduceMotion() ? "auto" : "smooth", block: "start" });
          var i = target.querySelector("input"); if (i) setTimeout(function () { i.focus({ preventScroll: true }); }, 450);
        } }, ["Reserve", icon("arrow")])])]);
    var visible = false, inView = false, count = 0;
    function sync() { var show = count > 0 && !inView; if (show !== visible) { visible = show; d.classList.toggle("is-on", show); } }
    listeners.push(function (q) {
      count = q.count;
      txt.textContent = q.count ? q.count + " " + plural(q.count, "plushie", "plushies") + " · " + money(q.subtotal) + (q.free ? " · Free shipping" : q.covered ? " · " + q.remaining + " to free shipping" : "") : "";
      fill.style.width = Math.min(100, q.count / q.min * 100) + "%";
      d.classList.toggle("is-unlocked", q.count >= q.min);
      sync();
    });
    if ("IntersectionObserver" in window) {
      new IntersectionObserver(function (es) { inView = es[0].isIntersecting; sync(); }, { threshold: 0.15 }).observe(target);
    }
    return d;
  }

  /* ── page sections ────────────────────────────────────────────────────── */
  function hero() {
    var days = daysTo(CAT.launchDate);
    return h("header", { class: "ccpo-hero" }, [
      h("p", { class: "ccpo-pill" }, [h("span", { class: "ccpo-dot is-live" }), "Pre-order reservations are open"]),
      h("h1", { class: "ccpo-h1", html: "Critters you can <em>hold.</em>" }),
      h("p", { class: "ccpo-lead", text: "The Whale Shark and the Loggerhead Sea Turtle are swimming off the Currents and Critters cards and into your arms. Reserve yours today. It's free, and we'll email you the moment pre-orders open." }),
      h("ul", { class: "ccpo-chips" }, [
        h("li", { class: "is-price" }, [h("b", { text: money(CAT.products[0].priceCents) }), " each " + CAT.priceNote]),
        h("li", { class: "is-ship" }, [icon("truck"), "Free standard shipping on " + CAT.freeShippingMin + "+ plushies"]),
        h("li", null, [icon("heart"), CAT.pledge])]),
      days > 0 ? h("p", { class: "ccpo-count" }, [h("b", { text: String(days) }), " " + plural(days, "day", "days") + " until launch · " + CAT.launchLabel]) : null
    ]);
  }
  function steps() {
    var S = [["calendar", "Reserve today", "Pick your plushies. Free, no card needed."],
             ["mail", "We email you", "The moment pre-orders open, you hear first."],
             ["card", "Confirm & pay", "Checkout calculates shipping and sales tax."],
             ["gift", "Launch day", "Plushies launch " + CAT.launchLabel + "."]];
    return h("ol", { class: "ccpo-steps", "aria-label": "How reserving works" }, S.map(function (s, i) {
      return h("li", null, [h("span", { class: "ccpo-step-ico" }, [icon(s[0])]), h("div", null, [h("b", { text: (i + 1) + ". " + s[1] }), h("span", { text: s[2] })])]);
    }));
  }
  function perks() {
    var P = [["feather", "Ultra-soft", "Premium ultra-soft plush fabric that begs to be hugged."],
             ["weight", "Calming weight", "2.5 lbs of securely enclosed weighted filling. No heating components."],
             ["game", "In-game unlock", CAT.unlock],
             ["wave", "Part of the deck", "Both are Crosscurrent critters from the Currents and Critters card game."]];
    return h("section", { class: "ccpo-perks", "aria-label": "Why you'll love them" }, P.map(function (p) {
      return h("div", { class: "ccpo-perk" }, [h("span", { class: "ccpo-perk-ico" }, [icon(p[0])]), h("h3", { text: p[1] }), h("p", { text: p[2] })]);
    }));
  }
  function mission(site) {
    var link = site === "bss" ? "https://www.currentsandcritters.com/legal" : "/legal";
    return h("section", { class: "ccpo-mission" }, [
      h("div", { class: "ccpo-mission-in" }, [
        h("span", { class: "ccpo-mission-ico" }, [icon("heart")]),
        h("div", null, [
          h("p", { class: "ccpo-eyebrow", text: "Our commitment" }),
          h("h2", { class: "ccpo-h2", text: CAT.pledge + "." }),
          h("p", { text: "Bearded Seal Studios pledges 5% to ocean conservation, out of what it earns. It's a commitment the studio makes on its own behalf, and it never changes the price you pay. Real whale sharks and loggerheads need healthy oceans, and so do the ones in our deck." }),
          h("a", { class: "ccpo-link", href: link, target: site === "bss" ? "_blank" : null, rel: site === "bss" ? "noopener" : null, text: "How the pledge works →" })])])]);
  }
  function faq(site) {
    var inbox = site === "bss" ? "timothy.honey@beardedsealstudios.com" : "currentsandcritters@gmail.com";
    var Q = [
      ["Am I paying anything today?", "No. A reservation is free and non-binding, and we never ask for a card here. When pre-orders open, we email you a link to confirm your plushies and pay at checkout."],
      ["How much are the plushies?", "Each plushie is " + money(CAT.products[0].priceCents) + " " + CAT.priceNote + "."],
      ["How does free shipping work?", "Reserve " + CAT.freeShippingMin + " or more plushies, in any mix of Whale Sharks and Loggerheads, and standard shipping is free to U.S. addresses. With 1 or 2 plushies, shipping is calculated at checkout. Sales tax still applies, and free shipping doesn't cover expedited or international shipping."],
      ["How big are they, and what are they made of?", "Each one is " + CAT.products[0].sizeCm + " cm (about " + (Math.round(CAT.products[0].sizeCm / 2.54 * 10) / 10) + " in) and weighs " + CAT.products[0].weightLb + " lbs. They're made with premium ultra-soft plush fabric and securely enclosed weighted filling, with no heating components."],
      ["What's the in-game unlock?", CAT.unlock],
      ["When do they launch?", "The first plushies launch on Groundhog Day, " + CAT.launchLabel + "."],
      ["Can I change or cancel my reservation?", "Yes. Reserve again with the same email and we'll update your quantities instead of adding a second reservation. To cancel, just email " + inbox + "."],
      ["What do you do with my details?", "We use them only for your reservation: to email you when pre-orders open and to estimate shipping. You're only added to our newsletter if you tick that box."]
    ];
    return h("section", { class: "ccpo-faq", "aria-labelledby": "ccpo-faq-h" }, [
      h("p", { class: "ccpo-eyebrow", text: "Questions" }),
      h("h2", { class: "ccpo-h2", id: "ccpo-faq-h", text: "Good to know" }),
      h("div", { class: "ccpo-faq-list" }, Q.map(function (q, i) {
        return h("details", { class: "ccpo-q", open: i === 0 ? true : null }, [h("summary", null, [h("span", { text: q[0] }), icon("plus")]), h("p", { text: q[1] })]);
      }))]);
  }
  function finalCta(target) {
    return h("section", { class: "ccpo-final" }, [
      h("h2", { class: "ccpo-h2", html: "Bring a critter <em>home.</em>" }),
      h("p", { text: money(CAT.products[0].priceCents) + " each " + CAT.priceNote + ". Free standard shipping when you reserve " + CAT.freeShippingMin + " or more." }),
      h("button", { type: "button", class: "ccpo-btn ccpo-btn-primary ccpo-btn-lg", onclick: function () {
        target.scrollIntoView({ behavior: reduceMotion() ? "auto" : "smooth", block: "start" });
      } }, ["Reserve yours now", icon("arrow")])]);
  }

  /* ── mounts ───────────────────────────────────────────────────────────── */
  function mountFull(root) {
    var site = root.getAttribute("data-site") === "bss" ? "bss" : "cc";
    root.classList.add("ccpo", "ccpo-full", "ccpo-site-" + site);
    root.innerHTML = "";
    var products = h("div", { class: "ccpo-products" }, CAT.products.map(productCard));
    var checkout = h("aside", { class: "ccpo-checkout", id: "ccpo-reserve", "aria-labelledby": "ccpo-sum-h" }, [
      h("div", { class: "ccpo-panel" }, [
        h("h2", { class: "ccpo-panel-title", id: "ccpo-sum-h", text: "Your reservation" }),
        shipBar(), summary(), form(site)])]);
    var shop = h("section", { class: "ccpo-shop", id: "ccpo-shop", "aria-label": "Choose your plushies" }, [
      h("div", { class: "ccpo-shop-head" }, [h("p", { class: "ccpo-eyebrow", text: "Step 1" }), h("h2", { class: "ccpo-h2", text: "Choose your plushies" }),
        h("p", { class: "ccpo-sub", text: "Mix and match. Any " + CAT.freeShippingMin + " plushies unlock free standard shipping." })]),
      h("div", { class: "ccpo-layout" }, [products, checkout])]);
    root.appendChild(hero());
    root.appendChild(steps());
    root.appendChild(shop);
    root.appendChild(perks());
    root.appendChild(mission(site));
    root.appendChild(faq(site));
    root.appendChild(finalCta(shop));
    document.body.appendChild(dock(checkout));
  }

  function mountCompact(root) {
    var site = root.getAttribute("data-site") === "bss" ? "bss" : "cc";
    var href = root.getAttribute("data-href") || (site === "bss" ? "/preorder" : "/preorder");
    root.classList.add("ccpo", "ccpo-compact", "ccpo-site-" + site);
    root.innerHTML = "";
    var count = h("span", { class: "ccpo-compact-count" });
    var go = h("a", { class: "ccpo-btn ccpo-btn-primary", href: href }, [h("span", { text: "Reserve now" }), icon("arrow")]);
    listeners.push(function (q) {
      count.textContent = q.count ? q.count + " " + plural(q.count, "plushie", "plushies") + " · " + money(q.subtotal) + " + " + (q.free ? "tax" : "shipping & tax") : "Reserving is free. Nothing is charged today.";
      var qs = CAT.products.map(function (p) { return p.id + "=" + (state.items[p.id] | 0); }).join("&");
      go.setAttribute("href", href + (href.indexOf("?") === -1 ? "?" : "&") + qs);
    });
    root.appendChild(h("div", { class: "ccpo-compact-in" }, [shipBar({ compact: true }), h("div", { class: "ccpo-compact-row" }, [count, go])]));
  }

  function boot() {
    var fulls = document.querySelectorAll("[data-cc-preorder='full']");
    var compacts = document.querySelectorAll("[data-cc-preorder='compact']");
    var qtys = document.querySelectorAll("[data-ccpo-qty]");
    if (!fulls.length && !compacts.length && !qtys.length) return;
    catalogReady.then(function () {
      loadState();
      Array.prototype.forEach.call(fulls, mountFull);
      Array.prototype.forEach.call(compacts, mountCompact);
      Array.prototype.forEach.call(qtys, function (slot) {
        var p = CAT.products.filter(function (x) { return x.id === slot.getAttribute("data-ccpo-qty"); })[0];
        if (!p) return;
        slot.classList.add("ccpo", "ccpo-slot");
        slot.innerHTML = "";
        slot.appendChild(stepper(p, { small: true }));
      });
      emit();
      if (fulls.length) {
        if ("requestIdleCallback" in window) requestIdleCallback(wake); else setTimeout(wake, 1200);
      }
    }).catch(function (e) {
      Array.prototype.forEach.call(fulls, function (root) {
        root.classList.add("ccpo");
        root.innerHTML = '<p class="ccpo-loaderr">The pre-order page didn’t load. Please refresh, or email <a href="mailto:currentsandcritters@gmail.com">currentsandcritters@gmail.com</a>.</p>';
      });
      if (window.console) console.warn("[preorder]", e);
    });
  }

  window.CCPreorder = { quote: function (items, country) { return CAT ? quote(items || {}, country) : null; },
                        shipMessage: function (q) { return shipMessage(q); }, ready: catalogReady, state: state,
                        setQty: function (id, n) { if (CAT) setQty(id, n); } };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();
})();
