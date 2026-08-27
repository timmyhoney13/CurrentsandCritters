/* ================================================================
 * Currents and Critters: Newsletter admin (/admin/newsletter).
 *
 * WHAT THIS FILE IS ALLOWED TO DECIDE
 * Layout, and nothing else. Every authorisation answer, every count, every
 * sanitising pass and every send happens on the server. The `isAdmin` flag
 * below exists ONLY to decide whether to draw the app or the sign-in card:
 * flipping it in devtools gets you an empty shell whose every request comes
 * back 403, because each endpoint verifies the Firebase ID token itself.
 *
 * WHY THERE IS NO CSRF TOKEN
 * Authorisation here is a bearer ID token placed in the request BODY by this
 * script. It is not ambient: a form posted from evil.com carries no token, so
 * the request is simply unauthorised. Cookies are what need CSRF protection,
 * and this page sets none. (Adding a cookie session plus a CSRF token would be
 * strictly weaker than what is here, it would create the ambient authority
 * the token scheme avoids.)
 *
 * ESCAPING
 * Every subscriber address, campaign subject and audit line is untrusted text.
 * It reaches the DOM through esc() or textContent, never through raw innerHTML.
 * The one place server HTML is rendered, the email preview: goes into a
 * SANDBOXED iframe via srcdoc, so even if the sanitiser were bypassed the
 * markup would run with no origin, no cookies and no access to this page.
 * ================================================================ */
(function () {
  "use strict";

  var API = "/api/newsletter/";
  var auth = null, currentUser = null, idTokenCache = { token: "", at: 0 };
  var isAdmin = false;
  var state = {
    section: "dashboard",
    subs: { page: 1, perPage: 50, query: "", status: "all", sort: "recent", data: null },
    draft: { id: "", subject: "", previewText: "", dirty: false },
    campaigns: [],
    progressId: "",
    progressTimer: null,
    settings: null,
    dashboard: null
  };

  /* ── tiny helpers ───────────────────────────────────────────── */
  function $(id) { return document.getElementById(id); }
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function num(n) { return (Number(n) || 0).toLocaleString(); }

  function toast(msg, kind) {
    var t = el("div", "n-toast" + (kind ? " " + kind : ""), msg);
    $("toasts").appendChild(t);
    setTimeout(function () {
      t.style.transition = "opacity .3s"; t.style.opacity = "0";
      setTimeout(function () { if (t.parentNode) t.parentNode.removeChild(t); }, 320);
    }, kind === "bad" ? 6500 : 3600);
  }

  /* ── API ─────────────────────────────────────────────────────── */
  function getToken(force) {
    if (!currentUser) return Promise.reject(new Error("signed out"));
    // Firebase refreshes on its own; caching for 5 min keeps a busy dashboard
    // from asking for a token on every poll tick.
    var now = Date.now();
    if (!force && idTokenCache.token && (now - idTokenCache.at) < 300000) {
      return Promise.resolve(idTokenCache.token);
    }
    return currentUser.getIdToken(!!force).then(function (t) {
      idTokenCache = { token: t, at: Date.now() };
      return t;
    });
  }

  function api(action, payload) {
    return getToken(false).then(function (token) {
      return fetch(API + action, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          // Not a security control on its own, the ID token is. It makes the
          // request non-simple so a cross-origin attempt must pass preflight.
          "X-CC-Newsletter": "1"
        },
        body: JSON.stringify(Object.assign({ idToken: token }, payload || {}))
      });
    }).then(function (r) {
      if (r.status === 403 || r.status === 401) {
        isAdmin = false; showGate("This Google account is not authorised for the newsletter admin.");
        return { ok: false, error: "unauthorized" };
      }
      return r.json().catch(function () { return { ok: false, error: "Bad response from the server." }; });
    }).catch(function () {
      return { ok: false, error: "Could not reach the server." };
    });
  }

  /* ── sign-in gate ────────────────────────────────────────────── */
  function showGate(message) {
    $("app").classList.add("n-hidden");
    $("gate").classList.remove("n-hidden");
    var m = $("gateMsg");
    m.textContent = message || "";
    m.className = "n-gate-msg" + (message ? " err" : "");
    $("gateSignout").classList.toggle("n-hidden", !currentUser);
    $("signinBtn").classList.toggle("n-hidden", !!currentUser);
  }
  function showApp() {
    $("gate").classList.add("n-hidden");
    $("app").classList.remove("n-hidden");
  }

  /* ══════════════════════════════════════════════════════════════
     NAVIGATION
     ══════════════════════════════════════════════════════════════ */
  var SECTIONS = [
    { id: "dashboard",   icon: "◎", label: "Dashboard" },
    { id: "subscribers", icon: "✉", label: "Subscribers" },
    { id: "compose",     icon: "✎", label: "Compose" },
    { id: "drafts",      icon: "▤", label: "Drafts" },
    { id: "sent",        icon: "✓", label: "Sent" },
    { id: "progress",    icon: "▶", label: "Sending Progress" },
    { id: "audit",       icon: "⌚", label: "Audit History" },
    { id: "settings",    icon: "⚙", label: "Connections" }
  ];

  function buildNav() {
    var nav = $("nav"); nav.innerHTML = "";
    var sel = $("navMobile"); sel.innerHTML = "";
    SECTIONS.forEach(function (s, i) {
      var b = el("button", "n-nav-btn" + (s.id === state.section ? " active" : ""));
      b.appendChild(el("span", "n-nav-ico", s.icon));
      b.appendChild(el("span", null, s.label));
      if (s.id === "drafts") {
        var n = state.campaigns.filter(function (c) { return c.status === "draft"; }).length;
        if (n) b.appendChild(el("span", "n-nav-badge", String(n)));
      }
      b.addEventListener("click", function () { go(s.id); });
      nav.appendChild(b);
      if (i === 4) nav.appendChild(el("div", "n-nav-sep"));

      var o = document.createElement("option");
      o.value = s.id; o.textContent = s.label;
      if (s.id === state.section) o.selected = true;
      sel.appendChild(o);
    });
    sel.onchange = function () { go(sel.value); };
  }

  function go(id) {
    if (state.progressTimer) { clearInterval(state.progressTimer); state.progressTimer = null; }
    state.section = id;
    buildNav();
    render();
  }

  function sectionHead(title, sub) {
    var h = el("div", "n-section-head");
    h.appendChild(el("h1", "n-h1", title));
    if (sub) h.appendChild(el("div", "n-h1-sub", sub));
    return h;
  }

  function card(label, value, foot, cls) {
    var c = el("div", "n-card");
    c.appendChild(el("div", "n-card-lbl", label));
    c.appendChild(el("div", "n-card-val" + (cls ? " " + cls : ""), value));
    c.appendChild(el("div", "n-card-foot", foot || ""));
    return c;
  }

  function loading(view) {
    view.appendChild(el("div", "n-empty", "Loading…"));
  }

  /* insertBefore, but it cannot throw. `ref` is only a positioning hint, and
     a detached one is not worth losing the message over: the whole reason
     these callers exist is to show that something is wrong, so failing to
     place one perfectly must never be what stops it being shown at all. */
  function putAbove(parent, node, ref) {
    if (ref && ref.parentNode === parent) parent.insertBefore(node, ref);
    else parent.appendChild(node);
  }

  /* Every render throws away the previous view's DOM (`v.innerHTML = ""`),
     but the request that view is waiting on carries on and then writes into
     nodes that are no longer attached to anything. That is a real crash, not
     a cosmetic one: "NotFoundError: the node before which the new node is to
     be inserted is not a child of this node", thrown out of a .then() where
     nothing catches it, leaving the page half-drawn with no message. It shows
     up when a response is slow or failing and the reader clicks another tab
     while waiting, which is exactly what people do when the server is
     struggling: the moment the dashboard is worth watching is the moment this
     breaks it.

     So each render takes a ticket, and a continuation checks its ticket is
     still the current one before touching the DOM. */
  var renderGen = 0;
  function stale(gen) { return gen !== renderGen; }

  function render() {
    renderGen++;
    var v = $("view"); v.innerHTML = "";
    ({
      dashboard: viewDashboard, subscribers: viewSubscribers, compose: viewCompose,
      drafts: viewDrafts, sent: viewSent, progress: viewProgress,
      audit: viewAudit, settings: viewSettings
    }[state.section] || viewDashboard)(v);
  }

  /* ══════════════════════════════════════════════════════════════
     DASHBOARD
     ══════════════════════════════════════════════════════════════ */
  function viewDashboard(v) {
    var gen = renderGen;
    v.appendChild(sectionHead("Dashboard", "Everything at a glance."));
    var cards = el("div", "n-cards");
    v.appendChild(cards);
    loading(v);

    Promise.all([api("dashboard"), api("settings")]).then(function (res) {
      if (stale(gen)) return;          // the reader moved on; this view is gone
      var d = res[0], s = res[1];
      v.querySelectorAll(".n-empty").forEach(function (n) { n.remove(); });
      if (!d.ok) { v.appendChild(el("div", "n-empty", d.error || "Could not load the dashboard.")); return; }
      state.dashboard = d;
      state.settings = s.ok ? s : null;

      /* ── "IS MAIL ACTUALLY GOING OUT?" ────────────────────────────────
         Top of the page, above the numbers, because this is the question
         the numbers cannot answer. The whole system can be dead while every
         count on this page looks healthy: subscribers still arrive, the
         dashboard still fills in, and nothing says the outbound side has
         been failing for a week. So when it is broken this shouts, and it
         carries the provider's own error text plus the one button that
         proves the fix worked. */
      var hz = d.sendHealth || {};
      if (hz.healthy === false) {
        var alert = el("div", "n-warn-box");
        alert.style.marginBottom = "18px";
        var t = el("div");
        t.style.fontWeight = "800";
        t.style.fontSize = "16px";
        t.textContent = hz.configured ? "Email is not being delivered"
                                      : "Email sending is not set up";
        alert.appendChild(t);
        alert.appendChild(el("div", null, hz.summary || ""));
        if (hz.fromPreviousRun) {
          alert.appendChild(el("div", "n-hint",
            "This is the last result from before the server restarted."));
        }
        alert.appendChild(selfTestButton());
        putAbove(v, alert, cards);
      } else if (hz.summary) {
        var okline = el("div", "n-note");
        okline.style.marginBottom = "18px";
        okline.appendChild(el("span", null, hz.summary + " "));
        okline.appendChild(selfTestButton());
        putAbove(v, okline, cards);
      }

      /* People who signed up while sending was broken. Their welcome was
         marked failed and, before the repair pass existed, nothing would ever
         have looked at it again. Shown separately from the health box because
         it survives the fix: sending can be healthy again while a backlog of
         people are still owed the email they were promised. */
      if (d.owedWelcome > 0) {
        var owed = el("div", "n-warn-box");
        owed.style.marginBottom = "18px";
        var ot = el("div");
        ot.style.fontWeight = "800";
        ot.textContent = d.owedWelcome === 1
          ? "1 person is still owed their welcome email"
          : d.owedWelcome + " people are still owed their welcome email";
        owed.appendChild(ot);
        owed.appendChild(el("div", null,
          "They signed up while email delivery was down, so their welcome was "
          + "written off as failed. It is re-sent automatically whenever the "
          + "server restarts with working email, or you can do it now."));
        var rb = el("button", "n-btn primary", "Send the ones we missed");
        rb.addEventListener("click", function () {
          rb.disabled = true; rb.textContent = "Re-queueing…";
          api("resend-missed").then(function (r) {
            rb.textContent = r.ok
              ? "Re-queued " + r.requeued + "; they send within a minute"
              : (r.error || "Could not re-queue.");
            if (r.ok) setTimeout(render, 4000);
          });
        });
        owed.appendChild(rb);
        putAbove(v, owed, cards);
      }

      cards.appendChild(card("Active subscribers", num(d.activeCount),
        d.truncated ? "List truncated: see Subscribers" : "Receiving newsletters"));
      cards.appendChild(card("Stranded (old signups)", num(d.pendingCount),
        d.pendingCount ? "Old two-step signups, confirm by hand"
                       : "Signups now join the list at once"));
      cards.appendChild(card("Unsubscribed", num(d.unsubscribedCount), "Kept on record, never emailed"));
      cards.appendChild(card("Newsletters sent", num(d.newslettersSent), "Completed campaigns"));
      cards.appendChild(card("Most recent signup",
        d.mostRecentSignup || "-", d.mostRecentSignupAtIso || "No signups yet",
        d.mostRecentSignup ? "small" : "empty"));
      cards.appendChild(card("Sent today", num(d.sendsUsedToday) + " / " + num(d.dailyCap),
        "Gmail daily cap for this process"));

      // Current campaign
      var blk = el("div", "n-block");
      blk.appendChild(headRow("Current campaign"));
      var p = el("div", "n-panel");
      if (d.currentCampaign) {
        p.appendChild(el("h3", null, d.currentCampaign.subject));
        p.appendChild(progressBlock(d.currentCampaign));
        var b = el("button", "n-btn primary", "Open sending progress");
        b.style.marginTop = "14px";
        b.addEventListener("click", function () { state.progressId = d.currentCampaign.id; go("progress"); });
        p.appendChild(b);
      } else {
        p.appendChild(el("p", "n-note", "No campaign is sending right now."));
      }
      blk.appendChild(p);
      v.appendChild(blk);

      // Connection status
      var st = el("div", "n-block");
      st.appendChild(headRow("Connections"));
      var sp = el("div", "n-panel");
      if (s.ok) {
        sp.appendChild(statusRow("Email sending", gmailChip(s.gmail),
          s.gmail.connected
            ? ((s.gmail.transportLabel || "") + ": signed in as " + (s.gmail.authorizedAs || "?"))
            : (s.gmail.error || "")));
        sp.appendChild(statusRow("Stripe webhook secret",
          chip(s.stripe.webhookSecretSet ? "good" : "bad", s.stripe.webhookSecretSet ? "Set" : "Not set"),
          s.stripe.webhookSecretSet
            ? ("Newsletter signups recorded from Stripe: " + num(s.stripe.signupsFromStripe))
            : "Signups from checkout cannot be processed until this is set."));
        sp.appendChild(statusRow("Unsubscribe links",
          chip(s.unsubscribeSecretSet ? "good" : "bad", s.unsubscribeSecretSet ? "Ready" : "Not configured"),
          s.unsubscribeSecretSet ? "" : "Set NEWSLETTER_UNSUBSCRIBE_SECRET: sending is blocked without it."));
        if (s.ok && s.gmail && s.gmail.capWarning) {
          sp.appendChild(statusRow("Daily limit",
            chip("warn", num(s.gmail.dailyCap) + " / day"), s.gmail.capWarning));
        }
        if (d.pendingWelcome || d.failedWelcome || d.stuckWelcome) {
          var bits = num(d.pendingWelcome) + " pending, " + num(d.failedWelcome) + " failed";
          if (d.stuckWelcome) bits += ", " + num(d.stuckWelcome) + " mid-send";
          sp.appendChild(statusRow("Welcome emails",
            chip((d.failedWelcome || d.stuckWelcome) ? "warn" : "info", bits),
            d.stuckWelcome
              ? "Mid-send welcomes are re-queued automatically after a restart."
              : "Pending welcomes send automatically within a minute."));
        }
      } else {
        sp.appendChild(el("p", "n-note", "Could not read connection status."));
      }
      st.appendChild(sp);
      v.appendChild(st);
    });
  }

  /* One button that sends a REAL email through the real transport and reports
     exactly what came back. It exists because the only previous way to answer
     "why is no mail arriving" was to read a Render log. */
  function selfTestButton() {
    var b = el("button", "n-btn small", "Send me a test email now");
    b.addEventListener("click", function () {
      b.disabled = true;
      b.textContent = "Testing…";
      api("self-test").then(function (r) {
        b.disabled = false;
        b.textContent = "Send me a test email now";
        showSelfTest(r);
      });
    });
    return b;
  }

  function showSelfTest(r) {
    var body = el("div");
    (r.steps || []).forEach(function (st) {
      var row = el("div", "n-status-row");
      row.appendChild(el("div", "n-status-lbl", st.name));
      row.appendChild(chip(st.ok ? "good" : "bad", st.ok ? "OK" : "Failed"));
      row.appendChild(el("div", "n-status-val", st.detail || ""));
      body.appendChild(row);
    });
    if (!r.ok && r.error) {
      var e = el("div", "n-warn-box");
      e.style.marginTop = "12px";
      e.appendChild(el("div", null, r.error));
      body.appendChild(e);
    }
    confirmModal({
      title: r.ok ? "Sending works" : "Sending is broken",
      body: r.ok
        ? ("A real email was just delivered to " + (r.sentTo || "your admin address") +
           ". If it is not in your inbox in a minute, check spam: that would mean " +
           "delivery works and reputation is the problem, not configuration.")
        : "This is the exact point where it fails, and the message underneath is "
          + "the mail provider's own, not a summary.",
      confirmLabel: "Close",
      hideCancel: true,
      content: body,
      onConfirm: function (done, close) { done(); close(); }
    });
  }

  function headRow(title, extra) {
    var h = el("div", "n-block-head");
    h.appendChild(el("h2", "n-h2", title));
    h.appendChild(el("div", "n-spacer"));
    if (extra) h.appendChild(extra);
    return h;
  }
  function chip(kind, text) { return el("span", "n-chip " + kind, text); }
  // Three states, and the third one is a leftover that still matters.
  // Signing up is one step now, but records created under the old
  // confirm-your-email flow are still sitting in "pending", and they are real
  // people who filled in the form and never got in. Folding them into
  // "Unsubscribed" (anything-not-active) would report somebody who joined as
  // having opted out, and would hide the ones still owed a fix.
  function statusChip(status) {
    if (status === "active") return chip("good", "Active");
    if (status === "pending") return chip("warn", "Never confirmed");
    return chip("neutral", "Unsubscribed");
  }
  function gmailChip(g) {
    if (!g.configured) return chip("bad", "Not set up");
    if (!g.connected) return chip("bad", "Connection failed");
    if (!g.canSendAsSender) return chip("warn", "Wrong account");
    return chip("good", "Connected");
  }
  function statusRow(label, chipNode, detail) {
    var r = el("div", "n-status-row");
    r.appendChild(el("div", "n-status-lbl", label));
    r.appendChild(chipNode);
    r.appendChild(el("div", "n-status-val", detail || ""));
    return r;
  }

  function progressBlock(c) {
    var wrap = el("div");
    var bar = el("div", "n-bar");
    var fill = el("div", "n-bar-fill");
    fill.style.width = Math.max(0, Math.min(100, Number(c.percent) || 0)) + "%";
    bar.appendChild(fill);
    wrap.appendChild(bar);
    var t = el("div", "n-tally");
    t.appendChild(chip("info", (Number(c.percent) || 0) + "% complete"));
    t.appendChild(chip("neutral", num(c.intendedRecipients) + " intended"));
    t.appendChild(chip("good", num(c.sentCount) + " sent"));
    if (c.failedCount) t.appendChild(chip("bad", num(c.failedCount) + " failed"));
    if (c.skippedCount) t.appendChild(chip("warn", num(c.skippedCount) + " skipped"));
    if (c.interruptedCount) t.appendChild(chip("warn", num(c.interruptedCount) + " interrupted"));
    if (c.pendingCount) t.appendChild(chip("neutral", num(c.pendingCount) + " pending"));
    wrap.appendChild(t);
    return wrap;
  }

  /* ══════════════════════════════════════════════════════════════
     SUBSCRIBERS
     ══════════════════════════════════════════════════════════════ */
  function viewSubscribers(v) {
    v.appendChild(sectionHead("Subscribers", "Search, filter and manage the email list."));

    var counts = el("div", "n-cards");
    counts.style.marginBottom = "18px";
    v.appendChild(counts);

    var bar = el("div", "n-block-head");
    var q = el("input", "n-input"); q.type = "search";
    q.placeholder = "Search by email address…";
    q.value = state.subs.query; q.style.maxWidth = "300px";
    var status = el("select", "n-select"); status.style.maxWidth = "180px";
    [["all", "All statuses"], ["active", "Active only"],
     ["pending", "Never confirmed"], ["unsubscribed", "Unsubscribed only"]]
      .forEach(function (o) {
        var op = document.createElement("option");
        op.value = o[0]; op.textContent = o[1];
        if (state.subs.status === o[0]) op.selected = true;
        status.appendChild(op);
      });
    var sort = el("select", "n-select"); sort.style.maxWidth = "220px";
    [["recent", "Newest first"], ["oldest", "Oldest first"],
     ["recent_activity", "Recently (re)subscribed"], ["email", "Email A–Z"]]
      .forEach(function (o) {
        var op = document.createElement("option");
        op.value = o[0]; op.textContent = o[1];
        if (state.subs.sort === o[0]) op.selected = true;
        sort.appendChild(op);
      });

    var addBtn = el("button", "n-btn primary", "+ Add subscriber");
    var expBtn = el("button", "n-btn", "Export CSV");

    bar.appendChild(q); bar.appendChild(status); bar.appendChild(sort);
    bar.appendChild(el("div", "n-spacer"));
    bar.appendChild(expBtn); bar.appendChild(addBtn);
    v.appendChild(bar);

    var wrap = el("div", "n-table-wrap");
    v.appendChild(wrap);
    wrap.appendChild(el("div", "n-empty", "Loading…"));

    var debounce = null;
    q.addEventListener("input", function () {
      clearTimeout(debounce);
      debounce = setTimeout(function () {
        state.subs.query = q.value.trim(); state.subs.page = 1; load();
      }, 260);
    });
    status.addEventListener("change", function () { state.subs.status = status.value; state.subs.page = 1; load(); });
    sort.addEventListener("change", function () { state.subs.sort = sort.value; state.subs.page = 1; load(); });
    addBtn.addEventListener("click", openAddModal);
    expBtn.addEventListener("click", function () { doExport(expBtn); });

    function load() {
      api("subscribers", {
        query: state.subs.query, status: state.subs.status, sort: state.subs.sort,
        page: state.subs.page, perPage: state.subs.perPage
      }).then(function (d) {
        wrap.innerHTML = ""; counts.innerHTML = "";
        if (!d.ok) { wrap.appendChild(el("div", "n-empty", d.error || "Could not load subscribers.")); return; }
        state.subs.data = d;

        counts.appendChild(card("Active", num(d.counts.active), "Will receive newsletters"));
        counts.appendChild(card("Never confirmed", num(d.counts.pending),
          "Signed up under the old two-step flow and never clicked the link"));
        counts.appendChild(card("Unsubscribed", num(d.counts.unsubscribed), "Excluded from every send"));
        counts.appendChild(card("Total on record", num(d.counts.total),
          d.truncated ? "Showing the first " + num(d.counts.total) : "All subscriber records"));

        if (!d.rows.length) {
          wrap.appendChild(el("div", "n-empty",
            state.subs.query ? "No subscribers match that search." : "No subscribers yet."));
          return;
        }

        var scroll = el("div", "n-scroll");
        var table = el("table", "n-table");
        table.innerHTML =
          "<thead><tr><th>Email</th><th>Status</th><th>Source</th>" +
          "<th>Subscribed</th><th>Last (re)subscribed</th><th>Unsubscribed</th>" +
          "<th style='text-align:right'>Actions</th></tr></thead>";
        var tb = document.createElement("tbody");
        d.rows.forEach(function (r) { tb.appendChild(subRow(r, load)); });
        table.appendChild(tb);
        scroll.appendChild(table);
        wrap.appendChild(scroll);

        var pager = el("div", "n-pager");
        var prev = el("button", "n-btn small", "← Previous");
        var next = el("button", "n-btn small", "Next →");
        prev.disabled = d.page <= 1;
        next.disabled = d.page >= d.pages;
        prev.addEventListener("click", function () { state.subs.page--; load(); });
        next.addEventListener("click", function () { state.subs.page++; load(); });
        pager.appendChild(prev); pager.appendChild(next);
        pager.appendChild(el("div", "n-count",
          "Page " + d.page + " of " + d.pages + " · " + num(d.total) + " matching"));
        wrap.appendChild(pager);
      });
    }

    function subRow(r, reload) {
      var tr = document.createElement("tr");

      var tdE = document.createElement("td"); tdE.className = "n-email";
      tdE.textContent = r.email; tr.appendChild(tdE);

      var tdS = document.createElement("td");
      tdS.appendChild(statusChip(r.status));
      tr.appendChild(tdS);

      var tdSrc = document.createElement("td"); tdSrc.className = "n-when";
      tdSrc.textContent = r.source || "-"; tr.appendChild(tdSrc);

      ["subscribedAtIso", "resubscribedAtIso", "unsubscribedAtIso"].forEach(function (k) {
        var td = document.createElement("td"); td.className = "n-when";
        td.textContent = r[k] || "-"; tr.appendChild(td);
      });

      var tdA = document.createElement("td"); tdA.className = "n-acts";
      if (r.status === "active") {
        var u = el("button", "n-btn small danger", "Unsubscribe");
        u.addEventListener("click", function () {
          confirmModal({
            title: "Unsubscribe this person?",
            body: "They will stop receiving newsletters immediately. Their record is kept " +
                  "(nothing is deleted) so they are never accidentally re-added.",
            facts: [["Email", r.email], ["Subscribed", r.subscribedAtIso || "-"]],
            confirmLabel: "Unsubscribe",
            danger: true,
            onConfirm: function (done) {
              api("subscriber-unsubscribe", { id: r.id }).then(function (d) {
                done();
                if (d.ok) { toast("Unsubscribed " + r.email, "good"); reload(); }
                else toast(d.error || "Could not unsubscribe.", "bad");
              });
            }
          });
        });
        tdA.appendChild(u);
      } else if (r.status === "pending") {
        // Nothing here is a punishment, this person is mid-signup. Give the
        // two actions that finish it: send the link again, or vouch for the
        // address by hand when the mail never arrived.
        var again = el("button", "n-btn small", "Resend link");
        again.addEventListener("click", function () {
          again.disabled = true;
          api("subscriber-resend-confirmation", { id: r.id }).then(function (d) {
            again.disabled = false;
            if (d.ok) toast("Confirmation email re-sent to " + r.email + ".", "good");
            else toast(d.error || "Could not re-send.", "bad");
          });
        });
        tdA.appendChild(again);
        var ok = el("button", "n-btn small", "Confirm by hand");
        ok.addEventListener("click", function () { openConfirmModal(r, reload); });
        tdA.appendChild(ok);
      } else {
        var re = el("button", "n-btn small", "Reactivate");
        re.addEventListener("click", function () { openReactivateModal(r, reload); });
        tdA.appendChild(re);
      }
      tr.appendChild(tdA);
      return tr;
    }

    function doExport(btn) {
      btn.disabled = true; btn.textContent = "Exporting…";
      api("export", { status: state.subs.status, query: state.subs.query }).then(function (d) {
        btn.disabled = false; btn.textContent = "Export CSV";
        if (!d.ok) { toast(d.error || "Export failed.", "bad"); return; }
        var blob = new Blob([d.csv], { type: "text/csv;charset=utf-8" });
        var a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = d.filename || "subscribers.csv";
        document.body.appendChild(a); a.click();
        setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
        toast("Exported " + num(d.rows) + " row(s).", "good");
      });
    }

    function openAddModal() {
      var email = el("input", "n-input"); email.type = "email"; email.placeholder = "person@example.com";
      var note = el("input", "n-input"); note.placeholder = "e.g. asked to join at a convention";
      var consent = document.createElement("input"); consent.type = "checkbox";

      var form = el("div");
      var f1 = el("label", "n-field");
      f1.appendChild(el("span", "n-label", "Email address"));
      f1.appendChild(email);
      form.appendChild(f1);

      var f2 = el("label", "n-field");
      f2.appendChild(el("span", "n-label", "How did they give permission? (recorded in the audit log)"));
      f2.appendChild(note);
      form.appendChild(f2);

      var f3 = el("label", "n-check");
      f3.appendChild(consent);
      f3.appendChild(el("span", null,
        "I confirm this person gave me permission to send them Currents & Critters updates. " +
        "Adding someone who did not ask is what gets a sending domain blocked."));
      form.appendChild(f3);

      confirmModal({
        title: "Add a subscriber",
        body: "Use this only for someone who asked you directly. Everyone else joins " +
              "through the optional newsletter field at checkout.",
        content: form,
        confirmLabel: "Add subscriber",
        onConfirm: function (done, close) {
          if (!consent.checked) { done(); toast("Tick the permission box first.", "bad"); return; }
          api("subscriber-add", {
            email: email.value, consent: true, consentNote: note.value
          }).then(function (d) {
            done();
            if (d.ok) {
              toast("Added " + email.value.trim().toLowerCase() +
                    (d.result === "reactivated" ? " (reactivated)" : "") + ".", "good");
              close(); load();
            } else toast(d.error || "Could not add that subscriber.", "bad");
          });
        }
      });
      setTimeout(function () { email.focus(); }, 60);
    }

    function openConfirmModal(r, reload) {
      var reason = el("input", "n-input");
      reason.placeholder = "e.g. this is my own address; the mail went to spam";
      var form = el("label", "n-field");
      form.appendChild(el("span", "n-label", "Why are you confirming this by hand?"));
      form.appendChild(reason);

      confirmModal({
        title: "Add this address to the list by hand?",
        body: "They signed up back when joining took a confirmation click, and never " +
              "clicked it. Signups are one step now, so nothing will ever confirm this " +
              "record on its own. Confirming " +
              "here makes them an active subscriber and sends the welcome email.",
        warn: "The email click is what proves somebody owns an address. Only skip it when " +
              "you know they do, your account and reason are written to the audit log.",
        facts: [["Email", r.email], ["Signed up", r.subscribedAtIso || "-"]],
        content: form,
        confirmLabel: "Confirm subscriber",
        onConfirm: function (done, close) {
          if (!reason.value.trim()) { done(); toast("Give a reason first.", "bad"); return; }
          api("subscriber-confirm", { id: r.id, reason: reason.value }).then(function (d) {
            done();
            if (d.ok) { toast(r.email + " is now an active subscriber.", "good"); close(); reload(); }
            else toast(d.error || "Could not confirm.", "bad");
          });
        }
      });
    }

    function openReactivateModal(r, reload) {
      var reason = el("input", "n-input");
      reason.placeholder = "e.g. emailed asking to rejoin on 6 Aug";
      var form = el("label", "n-field");
      form.appendChild(el("span", "n-label", "Why are you reactivating them?"));
      form.appendChild(reason);

      confirmModal({
        title: "Reactivate this subscriber?",
        body: "Only do this when they have asked to rejoin. They will start receiving " +
              "newsletters again and will be sent the welcome email.",
        warn: "Reactivating someone who did not ask is a spam complaint waiting to happen.",
        facts: [["Email", r.email], ["Unsubscribed", r.unsubscribedAtIso || "-"]],
        content: form,
        confirmLabel: "Reactivate",
        onConfirm: function (done, close) {
          if (!reason.value.trim()) { done(); toast("Give a reason first.", "bad"); return; }
          api("subscriber-reactivate", { id: r.id, reason: reason.value }).then(function (d) {
            done();
            if (d.ok) { toast("Reactivated " + r.email + ".", "good"); close(); reload(); }
            else toast(d.error || "Could not reactivate.", "bad");
          });
        }
      });
    }

    load();
  }

  /* ══════════════════════════════════════════════════════════════
     COMPOSER
     ══════════════════════════════════════════════════════════════ */
  function viewCompose(v) {
    v.appendChild(sectionHead(
      state.draft.id ? "Edit newsletter" : "Compose newsletter",
      "The footer, business address, Privacy Policy link and each reader's own " +
      "unsubscribe link are added automatically: don't type them."));

    var grid = el("div", "n-compose");

    /* ---- left: editor ---- */
    var left = el("div");

    var subj = el("input", "n-input");
    subj.placeholder = "Subject line"; subj.maxLength = 200; subj.value = state.draft.subject || "";
    var f1 = el("label", "n-field");
    f1.appendChild(el("span", "n-label", "Email subject"));
    f1.appendChild(subj);
    left.appendChild(f1);

    var prev = el("input", "n-input");
    prev.placeholder = "Optional, the grey line shown next to the subject in the inbox";
    prev.maxLength = 200; prev.value = state.draft.previewText || "";
    var f2 = el("label", "n-field");
    f2.appendChild(el("span", "n-label", "Preview text"));
    f2.appendChild(prev);
    left.appendChild(f2);

    left.appendChild(el("span", "n-label", "Message"));
    left.appendChild(buildToolbar());
    var ed = el("div", "n-editor");
    ed.id = "editor";
    ed.contentEditable = "true";
    ed.setAttribute("data-placeholder", "Write your newsletter here…");
    ed.spellcheck = true;
    left.appendChild(ed);

    var actions = el("div", "n-block-head");
    actions.style.marginTop = "16px";
    var saveBtn = el("button", "n-btn primary", "Save draft");
    var testBtn = el("button", "n-btn", "Send Test Email");
    var sendBtn = el("button", "n-btn send", "Send to all subscribers");
    var newBtn = el("button", "n-btn", "New blank draft");
    actions.appendChild(saveBtn); actions.appendChild(testBtn);
    actions.appendChild(el("div", "n-spacer"));
    actions.appendChild(newBtn); actions.appendChild(sendBtn);
    left.appendChild(actions);
    var msg = el("div", "n-msg"); left.appendChild(msg);
    grid.appendChild(left);

    /* ---- right: live preview ---- */
    var right = el("div");
    right.appendChild(el("span", "n-label", "Preview"));
    var tabs = el("div", "n-preview-tabs");
    var stage = el("div", "n-preview-stage");
    var frame = document.createElement("iframe");
    frame.className = "n-preview-frame";
    frame.setAttribute("title", "Email preview");
    // Sandboxed with NO allow-scripts and NO allow-same-origin: the preview is
    // rendered markup, never a live page. Even a sanitiser bypass would run
    // inside an origin-less frame with nothing to reach.
    frame.setAttribute("sandbox", "");
    var textPane = el("pre", "n-preview-text n-hidden");
    stage.appendChild(frame); stage.appendChild(textPane);

    var mode = "desktop";
    [["desktop", "Desktop"], ["mobile", "Mobile"], ["text", "Plain text"]].forEach(function (t, i) {
      var b = el("button", "n-preview-tab" + (i === 0 ? " active" : ""), t[1]);
      b.addEventListener("click", function () {
        mode = t[0];
        tabs.querySelectorAll(".n-preview-tab").forEach(function (x) { x.classList.remove("active"); });
        b.classList.add("active");
        stage.classList.toggle("mobile", mode === "mobile");
        frame.classList.toggle("n-hidden", mode === "text");
        textPane.classList.toggle("n-hidden", mode !== "text");
        refreshPreview();
      });
      tabs.appendChild(b);
    });
    right.appendChild(tabs); right.appendChild(stage);
    right.appendChild(el("div", "n-hint",
      "The preview shows an example unsubscribe link. Every real recipient gets their own."));
    grid.appendChild(right);
    v.appendChild(grid);

    /* ---- behaviour ---- */
    var previewTimer = null, lastPreviewKey = "";
    function refreshPreview() {
      var key = subj.value + "\u0000" + prev.value + "\u0000" + ed.innerHTML + "\u0000" + mode;
      if (key === lastPreviewKey) return;
      lastPreviewKey = key;
      clearTimeout(previewTimer);
      previewTimer = setTimeout(function () {
        api("campaign-preview", {
          subject: subj.value, previewText: prev.value, contentHtml: ed.innerHTML
        }).then(function (d) {
          if (!d.ok) return;
          if (mode === "text") textPane.textContent = d.text;
          else frame.srcdoc = d.html;
        });
      }, 420);
    }

    function markDirty() { state.draft.dirty = true; refreshPreview(); }
    ed.addEventListener("input", markDirty);
    subj.addEventListener("input", markDirty);
    prev.addEventListener("input", markDirty);

    // Paste as PLAIN TEXT. Pasting from Word or a webpage otherwise drags in
    // style attributes, font tags and tracking markup that the server would
    // strip anyway, this way what you see in the editor is what survives.
    ed.addEventListener("paste", function (e) {
      e.preventDefault();
      var text = (e.clipboardData || window.clipboardData).getData("text/plain");
      document.execCommand("insertText", false, text);
    });

    function collect() {
      return { subject: subj.value, previewText: prev.value, contentHtml: ed.innerHTML };
    }

    function save(then) {
      if (!subj.value.trim()) { toast("Add a subject first.", "bad"); return; }
      saveBtn.disabled = true; saveBtn.textContent = "Saving…";
      api("campaign-save", Object.assign({ id: state.draft.id }, collect())).then(function (d) {
        saveBtn.disabled = false; saveBtn.textContent = "Save draft";
        if (!d.ok) { toast(d.error || "Could not save.", "bad"); return; }
        state.draft.id = d.id; state.draft.dirty = false;
        toast(d.created ? "Draft created." : "Draft saved.", "good");
        loadCampaigns();
        if (then) then(d.id);
      });
    }

    saveBtn.addEventListener("click", function () { save(null); });

    newBtn.addEventListener("click", function () {
      if (state.draft.dirty && !window.confirm("Discard unsaved changes and start a new draft?")) return;
      state.draft = { id: "", subject: "", previewText: "", dirty: false };
      render();
    });

    // Double-click / double-tap guard: the button disables itself for the whole
    // round trip AND for a moment after, and the server rate-limits test sends
    // besides. Two independent guards because "I clicked twice" is the single
    // most common way to send something twice.
    var testBusy = false;
    testBtn.addEventListener("click", function () {
      if (testBusy) return;
      if (!subj.value.trim()) { toast("Add a subject first.", "bad"); return; }
      testBusy = true; testBtn.disabled = true; testBtn.textContent = "Sending test…";
      api("test-send", Object.assign({ id: state.draft.id }, collect())).then(function (d) {
        testBtn.textContent = "Send Test Email";
        if (d.ok) { msg.className = "n-msg ok"; msg.textContent = "Test email sent to " + d.sentTo + "."; toast("Test email sent.", "good"); }
        else { msg.className = "n-msg err"; msg.textContent = d.error || "Test failed."; toast(d.error || "Test failed.", "bad"); }
        setTimeout(function () { testBusy = false; testBtn.disabled = false; }, 2500);
      });
    });

    sendBtn.addEventListener("click", function () {
      if (!subj.value.trim()) { toast("Add a subject first.", "bad"); return; }
      // Always save first: sending an unsaved composer would send content the
      // history has no record of.
      save(function (cid) { openSendModal(cid, subj.value); });
    });

    // Restore an existing draft.
    if (state.draft.id) {
      api("campaign-get", { id: state.draft.id }).then(function (d) {
        if (d.ok) {
          subj.value = d.campaign.subject || "";
          prev.value = d.campaign.previewText || "";
          ed.innerHTML = d.campaign.contentHtml || "";
          state.draft.subject = subj.value; state.draft.previewText = prev.value;
        }
        refreshPreview();
      });
    } else {
      refreshPreview();
    }
  }

  function buildToolbar() {
    var bar = el("div", "n-toolbar");
    function tool(label, title, fn) {
      var b = el("button", "n-tool", label);
      b.type = "button"; b.title = title; b.setAttribute("aria-label", title);
      // mousedown + preventDefault keeps the editor's selection alive; a plain
      // click moves focus to the button first and the command applies to nothing.
      b.addEventListener("mousedown", function (e) { e.preventDefault(); });
      b.addEventListener("click", function (e) { e.preventDefault(); fn(); });
      bar.appendChild(b);
      return b;
    }
    function cmd(name, arg) { document.execCommand(name, false, arg || null); $("editor").focus(); }
    function sep() { bar.appendChild(el("div", "n-tool-sep")); }

    tool("H1", "Heading 1", function () { cmd("formatBlock", "<h1>"); });
    tool("H2", "Heading 2", function () { cmd("formatBlock", "<h2>"); });
    tool("H3", "Heading 3", function () { cmd("formatBlock", "<h3>"); });
    tool("¶", "Paragraph", function () { cmd("formatBlock", "<p>"); });
    sep();
    tool("B", "Bold", function () { cmd("bold"); });
    tool("I", "Italic", function () { cmd("italic"); });
    tool("U", "Underline", function () { cmd("underline"); });
    sep();
    tool("• List", "Bulleted list", function () { cmd("insertUnorderedList"); });
    tool("1. List", "Numbered list", function () { cmd("insertOrderedList"); });
    tool("❝", "Quote", function () { cmd("formatBlock", "<blockquote>"); });
    sep();
    tool("🔗 Link", "Insert link", function () {
      var url = window.prompt("Link URL (must start with https:// or mailto:)", "https://");
      if (!url) return;
      if (!/^(https?:\/\/|mailto:)/i.test(url.trim())) {
        toast("Links must start with https://, http:// or mailto:", "bad"); return;
      }
      cmd("createLink", url.trim());
    });
    tool("🖼 Image", "Insert image", function () {
      var url = window.prompt(
        "Image URL. It must be a public https:// address: email clients " +
        "cannot load an image from your computer.", "https://");
      if (!url) return;
      url = url.trim();
      if (!/^https:\/\//i.test(url)) { toast("Images must be a public https:// URL.", "bad"); return; }
      if (!/\.(png|jpe?g|gif|webp)(\?|#|$)/i.test(url)) {
        if (!window.confirm("That URL doesn't look like an image file. Insert it anyway?")) return;
      }
      cmd("insertHTML", '<img src="' + esc(url) + '" alt="" />');
    });
    tool("Button", "Insert a styled button", function () {
      var text = window.prompt("Button text", "Play Currents & Critters");
      if (!text) return;
      var url = window.prompt("Button link", "https://currentsandcritters.com");
      if (!url || !/^(https?:\/\/|mailto:)/i.test(url.trim())) {
        toast("Buttons need an https:// or mailto: link.", "bad"); return;
      }
      cmd("insertHTML",
        '<p><a class="cc-btn" href="' + esc(url.trim()) + '">' + esc(text) + "</a></p><p><br></p>");
    });
    sep();
    tool("-", "Divider", function () { cmd("insertHorizontalRule"); });
    tool("Clear", "Remove formatting", function () { cmd("removeFormat"); });
    return bar;
  }

  /* ══════════════════════════════════════════════════════════════
     SEND CONFIRMATION, the one irreversible action
     ══════════════════════════════════════════════════════════════ */
  function openSendModal(cid, subject) {
    api("campaign-progress", { id: cid }).then(function (pd) {
      var activeCount = state.dashboard ? state.dashboard.activeCount : null;
      api("dashboard").then(function (d) {
        if (d.ok) { state.dashboard = d; activeCount = d.activeCount; }

        var phrase = el("input", "n-input");
        phrase.placeholder = "Type SEND to confirm";
        phrase.autocomplete = "off"; phrase.spellcheck = false;

        var previewBtn = el("button", "n-btn small", "View the final preview");
        previewBtn.addEventListener("click", function () {
          api("campaign-preview", { id: cid }).then(function (p) {
            if (!p.ok) { toast("Preview failed.", "bad"); return; }
            var w = window.open("", "_blank", "width=680,height=860");
            if (w) { w.document.write(p.html); w.document.close(); }
          });
        });

        var form = el("div");
        form.appendChild(previewBtn);

        /* ── The spam preflight ────────────────────────────────────────
           Runs the draft past the checks a filter applies, in the one place
           it can still change the outcome: the moment before an irreversible
           send. It is advice and never a gate, the Send button is not touched
           by anything here, because a false positive must never be able to
           block Tim's own newsletter. */
        var spam = el("div"); spam.style.marginTop = "14px";
        spam.appendChild(el("div", "n-hint", "Checking for spam-filter triggers…"));
        form.appendChild(spam);
        api("spam-check", { id: cid }).then(function (sc) {
          spam.innerHTML = "";
          if (!sc || !sc.ok) return;                 // never block on the check
          var warns = (sc.findings || []).filter(function (x) { return x.level === "warn"; });
          var notes = (sc.findings || []).filter(function (x) { return x.level === "note"; });
          if (!warns.length && !notes.length) {
            var okBox = el("div", "n-note", "Spam check: nothing to flag. "
              + num(sc.stats.words) + " words, " + num(sc.stats.links) + " links, "
              + num(sc.stats.images) + " images.");
            spam.appendChild(okBox);
            return;
          }
          var box = el("div", warns.length ? "n-warn-box" : "n-note");
          box.appendChild(el("div", null, warns.length
            ? ("Spam check found " + warns.length + " thing"
               + (warns.length === 1 ? "" : "s") + " worth fixing first. You can still send.")
            : "Spam check: a few small suggestions. Nothing here will get you filtered."));
          var ul = el("ul");
          ul.style.margin = "8px 0 0";
          ul.style.paddingLeft = "18px";
          warns.concat(notes).forEach(function (fnd) {
            var li = el("li");
            li.style.marginBottom = "5px";
            li.appendChild(el("strong", null, fnd.title));
            li.appendChild(el("div", "n-hint", fnd.detail));
            ul.appendChild(li);
          });
          box.appendChild(ul);
          spam.appendChild(box);
        });

        var f = el("label", "n-field"); f.style.marginTop = "14px";
        f.appendChild(el("span", "n-label", "Type SEND to confirm"));
        f.appendChild(phrase);
        form.appendChild(f);

        confirmModal({
          title: "Send this newsletter to every active subscriber?",
          body: "This cannot be undone. Each person gets their own individual email " +
                "with their own unsubscribe link, no subscriber can see another's address.",
          warn: "Sending happens on the server in batches. You can close this page; " +
                "progress continues and is shown under Sending Progress.",
          facts: [
            ["Subject", subject],
            ["Active subscribers", activeCount == null ? "-" : num(activeCount)],
            ["From", state.settings && state.settings.gmail ? state.settings.gmail.senderEmail : ""]
          ],
          content: form,
          confirmLabel: "Send now",
          send: true,
          onConfirm: function (done, close) {
            if (phrase.value.trim() !== "SEND") {
              done(); toast("Type SEND exactly to confirm.", "bad"); return;
            }
            api("campaign-start", { id: cid, confirm: "SEND" }).then(function (r) {
              done();
              if (r.ok) {
                toast("Sending started to " + num(r.recipients) + " subscribers.", "good");
                close();
                state.progressId = cid;
                state.draft = { id: "", subject: "", previewText: "", dirty: false };
                loadCampaigns();
                go("progress");
              } else toast(r.error || "Could not start the send.", "bad");
            });
          }
        });
        setTimeout(function () { phrase.focus(); }, 60);
      });
    });
  }

  /* ══════════════════════════════════════════════════════════════
     DRAFTS / SENT
     ══════════════════════════════════════════════════════════════ */
  function campaignTable(v, list, kind) {
    if (!list.length) {
      v.appendChild(el("div", "n-empty",
        kind === "draft" ? "No drafts yet. Head to Compose to write one."
                         : "No newsletters have been sent yet."));
      return;
    }
    var wrap = el("div", "n-table-wrap");
    var scroll = el("div", "n-scroll");
    var t = el("table", "n-table");
    t.innerHTML = kind === "draft"
      ? "<thead><tr><th>Subject</th><th>Created</th><th>Last edited</th><th style='text-align:right'>Actions</th></tr></thead>"
      : "<thead><tr><th>Subject</th><th>Sent</th><th>Intended</th><th>Sent</th><th>Failed</th><th>Skipped</th><th style='text-align:right'>Actions</th></tr></thead>";
    var tb = document.createElement("tbody");

    list.forEach(function (c) {
      var tr = document.createElement("tr");
      var s = document.createElement("td"); s.className = "n-email";
      s.textContent = c.subject || "(no subject)"; tr.appendChild(s);

      if (kind === "draft") {
        [c.createdAtIso, c.updatedAtIso].forEach(function (x) {
          var td = document.createElement("td"); td.className = "n-when";
          td.textContent = x || "-"; tr.appendChild(td);
        });
      } else {
        var d1 = document.createElement("td"); d1.className = "n-when";
        d1.textContent = c.sentAtIso || c.startedAtIso || "-"; tr.appendChild(d1);
        [c.intendedRecipients, c.sentCount, c.failedCount, c.skippedCount].forEach(function (n) {
          var td = document.createElement("td"); td.textContent = num(n); tr.appendChild(td);
        });
      }

      var a = document.createElement("td"); a.className = "n-acts";
      if (kind === "draft") {
        var edit = el("button", "n-btn small", "Edit");
        edit.addEventListener("click", function () {
          state.draft = { id: c.id, subject: c.subject, previewText: c.previewText, dirty: false };
          go("compose");
        });
        a.appendChild(edit);
      } else {
        var view = el("button", "n-btn small", "Results");
        view.addEventListener("click", function () { state.progressId = c.id; go("progress"); });
        a.appendChild(view);
      }
      var dup = el("button", "n-btn small", "Duplicate");
      dup.style.marginLeft = "6px";
      dup.addEventListener("click", function () {
        api("campaign-duplicate", { id: c.id }).then(function (d) {
          if (!d.ok) { toast(d.error || "Could not duplicate.", "bad"); return; }
          toast("Duplicated as a new draft.", "good");
          state.draft = { id: d.id, subject: "", previewText: "", dirty: false };
          loadCampaigns(); go("compose");
        });
      });
      a.appendChild(dup);
      tr.appendChild(a);
      tb.appendChild(tr);
    });
    t.appendChild(tb); scroll.appendChild(t); wrap.appendChild(scroll);
    v.appendChild(wrap);
  }

  function viewDrafts(v) {
    v.appendChild(sectionHead("Drafts", "Saved newsletters that have not been sent."));
    var host = el("div"); v.appendChild(host);
    host.appendChild(el("div", "n-empty", "Loading…"));
    loadCampaigns().then(function () {
      host.innerHTML = "";
      campaignTable(host, state.campaigns.filter(function (c) { return c.status === "draft"; }), "draft");
    });
  }

  function viewSent(v) {
    v.appendChild(sectionHead("Sent newsletters", "The permanent history. Open one to see its results."));
    var host = el("div"); v.appendChild(host);
    host.appendChild(el("div", "n-empty", "Loading…"));
    loadCampaigns().then(function () {
      host.innerHTML = "";
      campaignTable(host, state.campaigns.filter(function (c) {
        return c.status === "sent" || c.status === "sending";
      }), "sent");
    });
  }

  function loadCampaigns() {
    return api("campaign-list").then(function (d) {
      state.campaigns = d.ok ? (d.campaigns || []) : [];
      buildNav();
      return state.campaigns;
    });
  }

  /* ══════════════════════════════════════════════════════════════
     SENDING PROGRESS
     ══════════════════════════════════════════════════════════════ */
  function viewProgress(v) {
    v.appendChild(sectionHead("Sending progress", "Live status of a campaign send."));

    var picker = el("div", "n-block-head");
    var sel = el("select", "n-select"); sel.style.maxWidth = "420px";
    picker.appendChild(sel);
    v.appendChild(picker);
    var host = el("div"); v.appendChild(host);
    host.appendChild(el("div", "n-empty", "Loading…"));

    loadCampaigns().then(function (list) {
      var sendable = list.filter(function (c) { return c.status !== "draft"; });
      if (!sendable.length) {
        host.innerHTML = "";
        host.appendChild(el("div", "n-empty", "No newsletter has been sent yet."));
        return;
      }
      if (!state.progressId || !sendable.some(function (c) { return c.id === state.progressId; })) {
        state.progressId = sendable[0].id;
      }
      sendable.forEach(function (c) {
        var o = document.createElement("option");
        o.value = c.id;
        o.textContent = (c.subject || "(no subject)") + ": " + (c.sentAtIso || c.startedAtIso || c.status);
        if (c.id === state.progressId) o.selected = true;
        sel.appendChild(o);
      });
      sel.onchange = function () { state.progressId = sel.value; tick(); };
      tick();
      // The campaign list arrives asynchronously, so by the time we get here
      // the user may already have navigated away, and go()'s clearInterval
      // has therefore ALREADY run. Starting the poller now would leave a timer
      // nothing ever clears, hammering the server from a section that is no
      // longer on screen. Only arm it if this section is still the live one.
      if (state.section !== "progress") return;
      if (state.progressTimer) clearInterval(state.progressTimer);
      state.progressTimer = setInterval(function () {
        if (state.section !== "progress") { clearInterval(state.progressTimer); state.progressTimer = null; return; }
        tick();
      }, 5000);
    });

    function tick() {
      api("campaign-progress", { id: state.progressId }).then(function (d) {
        host.innerHTML = "";
        if (!d.ok) { host.appendChild(el("div", "n-empty", "Could not load that campaign.")); return; }
        var c = d.campaign;

        var cards = el("div", "n-cards");
        cards.appendChild(card("Intended recipients", num(c.intendedRecipients), "Active at send time"));
        cards.appendChild(card("Sent", num(c.tally.sent), "Delivered to Gmail"));
        cards.appendChild(card("Pending", num(c.tally.pending), "Waiting in the queue"));
        cards.appendChild(card("Sending now", num(c.tally.sending), "In flight"));
        cards.appendChild(card("Failed", num(c.tally.failed), "Gave up after retries"));
        cards.appendChild(card("Skipped", num(c.tally.skipped_unsubscribed), "Unsubscribed before their turn"));
        host.appendChild(cards);

        var p = el("div", "n-panel"); p.style.marginTop = "18px";
        p.appendChild(el("h3", null, c.subject || "(no subject)"));
        var meta = el("p", "n-note");
        meta.textContent =
          "Status: " + c.status +
          " · Started: " + (c.startedAtIso || "-") +
          " · Finished: " + (c.sentAtIso || "-") +
          " · Started by: " + (c.startedBy || "-");
        p.appendChild(meta);
        p.appendChild(progressBlock({
          percent: c.percent, intendedRecipients: c.intendedRecipients,
          sentCount: c.tally.sent, failedCount: c.tally.failed,
          skippedCount: c.tally.skipped_unsubscribed,
          interruptedCount: c.tally.interrupted, pendingCount: c.tally.pending
        }));

        if (c.canRetry) {
          var rb = el("button", "n-btn", "Retry failed / interrupted");
          rb.style.marginTop = "16px";
          rb.addEventListener("click", function () {
            confirmModal({
              title: "Retry the recipients that didn't go out?",
              body: "Only recipients marked failed or interrupted are queued again. " +
                    "Anyone already marked sent is never re-sent, so nobody receives a second copy.",
              confirmLabel: "Retry them",
              onConfirm: function (done, close) {
                api("campaign-retry", { id: c.id }).then(function (r) {
                  done();
                  if (r.ok) { toast("Requeued " + num(r.requeued) + " recipient(s).", "good"); close(); tick(); }
                  else toast(r.error || "Could not retry.", "bad");
                });
              }
            });
          });
          p.appendChild(rb);
        }
        host.appendChild(p);

        if (c.failures && c.failures.length) {
          var fb = el("div", "n-block");
          fb.appendChild(headRow("Recipients that need attention"));
          var wrap = el("div", "n-table-wrap");
          var scroll = el("div", "n-scroll");
          var t = el("table", "n-table");
          t.innerHTML = "<thead><tr><th>Email</th><th>Status</th><th>Attempts</th><th>Reason</th></tr></thead>";
          var tb = document.createElement("tbody");
          c.failures.forEach(function (f) {
            var tr = document.createElement("tr");
            [f.email, f.status, String(f.attempts), f.error || "-"].forEach(function (x) {
              var td = document.createElement("td"); td.textContent = x; tr.appendChild(td);
            });
            tb.appendChild(tr);
          });
          t.appendChild(tb); scroll.appendChild(t); wrap.appendChild(scroll);
          fb.appendChild(wrap);
          host.appendChild(fb);
        }
      });
    }
  }

  /* ══════════════════════════════════════════════════════════════
     AUDIT
     ══════════════════════════════════════════════════════════════ */
  var AUDIT_LABEL = {
    subscriber_added_stripe: "Subscriber added (Stripe)",
    subscriber_added_manual: "Subscriber added (manual)",
    subscriber_unsubscribed: "Subscriber unsubscribed",
    subscriber_reactivated: "Subscriber reactivated",
    welcome_email_sent: "Welcome email sent",
    welcome_email_failed: "Welcome email failed",
    test_email_sent: "Test email sent",
    draft_created: "Draft created",
    draft_updated: "Draft edited",
    campaign_approved: "Campaign approved",
    campaign_started: "Campaign started",
    campaign_completed: "Campaign completed",
    campaign_failed: "Campaign failed",
    csv_exported: "CSV exported",
    unauthorized_admin_access: "Unauthorised access attempt",
    gmail_check: "Gmail connection checked"
  };
  var AUDIT_TONE = {
    welcome_email_failed: "bad", campaign_failed: "bad", unauthorized_admin_access: "bad",
    campaign_approved: "warn", campaign_started: "warn", csv_exported: "warn",
    campaign_completed: "good", welcome_email_sent: "good", subscriber_added_stripe: "good",
    subscriber_added_manual: "good"
  };

  function viewAudit(v) {
    v.appendChild(sectionHead("Audit history",
      "Every important action, kept permanently. Email bodies, tokens and secrets are never recorded here."));
    var host = el("div"); v.appendChild(host);
    host.appendChild(el("div", "n-empty", "Loading…"));
    api("audit", { limit: 200 }).then(function (d) {
      host.innerHTML = "";
      if (!d.ok || !d.rows.length) { host.appendChild(el("div", "n-empty", "Nothing recorded yet.")); return; }
      var wrap = el("div", "n-table-wrap");
      var scroll = el("div", "n-scroll");
      var t = el("table", "n-table");
      t.innerHTML = "<thead><tr><th>When</th><th>Action</th><th>Who</th><th>Details</th></tr></thead>";
      var tb = document.createElement("tbody");
      d.rows.forEach(function (r) {
        var tr = document.createElement("tr");
        var w = document.createElement("td"); w.className = "n-when"; w.textContent = r.atIso || "-";
        tr.appendChild(w);
        var a = document.createElement("td");
        a.appendChild(chip(AUDIT_TONE[r.action] || "neutral", AUDIT_LABEL[r.action] || r.action));
        tr.appendChild(a);
        var who = document.createElement("td"); who.className = "n-when";
        who.textContent = r.admin || "system"; tr.appendChild(who);
        var s = document.createElement("td"); s.textContent = r.summary || "-"; tr.appendChild(s);
        tb.appendChild(tr);
      });
      t.appendChild(tb); scroll.appendChild(t); wrap.appendChild(scroll);
      host.appendChild(wrap);
    });
  }

  /* ══════════════════════════════════════════════════════════════
     SETTINGS / CONNECTIONS
     ══════════════════════════════════════════════════════════════ */
  function viewSettings(v) {
    v.appendChild(sectionHead("Connections",
      "What this server can actually verify right now. Nothing here is assumed."));
    var host = el("div"); v.appendChild(host);
    host.appendChild(el("div", "n-empty", "Checking…"));

    api("settings").then(function (d) {
      host.innerHTML = "";
      if (!d.ok) { host.appendChild(el("div", "n-empty", "Could not read settings.")); return; }
      state.settings = d;
      var g = d.gmail, s = d.stripe;

      var p1 = el("div", "n-panel");
      p1.appendChild(el("h3", null, "Email sending"));
      p1.appendChild(el("p", "n-note",
        "Credentials live only in Render environment variables. No password, API key " +
        "or token is ever sent to this page."));
      p1.appendChild(statusRow("Method", chip(g.transport ? "info" : "bad",
        g.transportLabel || "not configured"),
        g.transport === "smtp" ? "Standard SMTP: works with any mail provider."
          : g.transport === "http" ? "HTTPS email API."
          : g.transport === "gmail_api" ? "Gmail API (OAuth)."
          : "Set SMTP_HOST / SMTP_USERNAME / SMTP_PASSWORD, or NEWSLETTER_API_KEY."));
      p1.appendChild(statusRow("Connection", gmailChip(g), g.error || "Ready to send."));
      p1.appendChild(statusRow("Signed in as", chip(g.authorizedAs ? "info" : "neutral",
        g.authorizedAs || "unknown"), ""));
      p1.appendChild(statusRow("From address", chip("neutral", g.senderEmail),
        g.senderVerified
          ? "Confirmed: this address matches the account we authenticated as."
          : "Not independently verifiable with this method, a test email is the real proof."));
      p1.appendChild(statusRow("Reply-To", chip("neutral", g.replyTo), ""));
      p1.appendChild(statusRow("Endpoint", chip("neutral", (g.scopes || []).join(", ") || "-"), ""));
      p1.appendChild(statusRow("Daily cap",
        chip(g.capWarning ? "warn" : "neutral", num(g.dailyCap) + " / day"),
        g.capWarning || "Matches what this account can actually send."));
      if (g.setupHint) {
        var hint = el("div", "n-warn-box", g.setupHint);
        hint.style.marginTop = "14px";
        p1.appendChild(hint);
      }
      var recheck = el("button", "n-btn", "Re-check connection");
      recheck.style.marginTop = "14px";
      recheck.addEventListener("click", function () { go("settings"); });
      p1.appendChild(recheck);
      host.appendChild(p1);

      /* ── Domain authentication ──────────────────────────────────────
         The panel that answers "why does my email go to spam". Everything
         above is about whether we can SEND; this is about whether anyone
         believes the message when it arrives. It is a live DNS read, so it
         reports what the internet currently sees, not what was intended. */
      var da = d.domainAuth || {};
      var pDns = el("div", "n-panel");
      pDns.appendChild(el("h3", null, "Domain authentication (spam folder)"));
      pDns.appendChild(el("p", "n-note",
        "Gmail and Yahoo require bulk senders to prove mail really comes from " +
        "their domain. Fail that and the message is not bounced, it is filed as " +
        "spam. These are live DNS lookups for " + (da.domain || "your domain") + "."));

      if (da.dnsReachable === false) {
        pDns.appendChild(el("div", "n-warn-box", da.summary ||
          "This server could not reach DNS, so none of this could be checked."));
      } else {
        var authRow = function (label, part, good, unknownIsFine) {
          var okv = part && part.ok;
          var kind = okv ? "good" : (unknownIsFine ? "warn" : "bad");
          pDns.appendChild(statusRow(label,
            chip(kind, okv ? good : (unknownIsFine ? "Not confirmed" : "Missing")),
            (part && (part.detail || part.record)) || ""));
        };
        authRow("SPF", da.spf, "Published");
        /* DKIM selectors cannot be enumerated from outside, so a miss here is
           reported as unknown, never as a failure. Saying "missing" would send
           Tim to fix something that may already be correct. */
        authRow("DKIM", da.dkim,
          "Published" + (da.dkim && da.dkim.selector ? " (" + da.dkim.selector + ")" : ""),
          true);
        authRow("DMARC", da.dmarc,
          "Published" + (da.dmarc && da.dmarc.policy ? " (p=" + da.dmarc.policy + ")" : ""));

        var box = el("div", da.ready ? "n-note" : "n-warn-box", da.summary || "");
        box.style.marginTop = "14px";
        pDns.appendChild(box);

        if (da.dmarc && !da.dmarc.ok && !da.consumerGmail) {
          var fix = el("div", "n-note");
          fix.style.marginTop = "10px";
          fix.appendChild(el("div", null, "Add this one DNS record at your domain host:"));
          var rec = el("div", "n-code",
            "TXT   _dmarc." + (da.domain || "") +
            "   v=DMARC1; p=none; rua=mailto:" + (d.adminEmail || "") + "; fo=1");
          rec.style.display = "block";
          rec.style.marginTop = "6px";
          rec.style.wordBreak = "break-all";
          fix.appendChild(rec);
          pDns.appendChild(fix);
        }
      }

      var reDns = el("button", "n-btn", "Re-check DNS now");
      reDns.style.marginTop = "14px";
      reDns.addEventListener("click", function () {
        reDns.disabled = true; reDns.textContent = "Checking DNS…";
        /* The server caches these lookups for ten minutes; this is the flag
           that says "I just edited DNS, look again". */
        api("settings", { recheckDns: true }).then(function (fresh) {
          if (fresh && fresh.ok) { state.settings = fresh; go("settings"); }
          else { reDns.disabled = false; reDns.textContent = "Re-check DNS now"; }
        });
      });
      pDns.appendChild(reDns);
      host.appendChild(pDns);

      var p2 = el("div", "n-panel");
      p2.appendChild(el("h3", null, "Stripe newsletter signup"));
      p2.appendChild(el("p", "n-note",
        "Signups arrive on the checkout.session.completed webhook. We can verify that a " +
        "signing secret is set and show which question labels Stripe last sent, we " +
        "cannot verify the Stripe Dashboard from here, so nothing below is guessed."));
      p2.appendChild(statusRow("Webhook signing secret",
        chip(s.webhookSecretSet ? "good" : "bad", s.webhookSecretSet ? "Set" : "Not set"),
        s.webhookSecretSet ? "Webhook events are verified." : "Set STRIPE_WEBHOOK_SECRET in Render."));
      p2.appendChild(statusRow("Signups from Stripe", chip("info", num(s.signupsFromStripe)),
        "Subscribers whose source is Stripe Checkout."));

      var lastRow = el("div", "n-status-row");
      lastRow.appendChild(el("div", "n-status-lbl", "Last checkout asked"));
      lastRow.appendChild(chip(s.lastSeenMatched === true ? "good"
        : (s.lastSeenMatched === false ? "warn" : "neutral"),
        s.lastSeenMatched === true ? "Matched" :
          (s.lastSeenMatched === false ? "No newsletter field matched" : "No checkout seen yet")));
      var lv = el("div", "n-status-val");
      if ((s.lastSeenLabels || []).length) {
        (s.lastSeenLabels).forEach(function (l) {
          var c = el("span", "n-code", l); c.style.marginRight = "6px"; lv.appendChild(c);
        });
        lv.appendChild(el("div", "n-hint", s.lastSeenAtIso || ""));
      } else {
        lv.textContent = "No Stripe checkout has been processed since this was deployed.";
      }
      lastRow.appendChild(lv);
      p2.appendChild(lastRow);

      var accRow = el("div", "n-status-row");
      accRow.appendChild(el("div", "n-status-lbl", "Labels we accept"));
      accRow.appendChild(chip("neutral", String((s.acceptedLabels || []).length)));
      var av = el("div", "n-status-val");
      (s.acceptedLabels || []).forEach(function (l) {
        var c = el("span", "n-code", l); c.style.marginRight = "6px"; av.appendChild(c);
      });
      av.appendChild(el("div", "n-hint",
        "If your Stripe field's label is not in this list, no one will ever be subscribed. " +
        "Add it with the NEWSLETTER_FIELD_LABEL environment variable (separate several with |)."));
      accRow.appendChild(av);
      p2.appendChild(accRow);
      host.appendChild(p2);

      var p3 = el("div", "n-panel");
      p3.appendChild(el("h3", null, "This system"));
      p3.appendChild(statusRow("Unsubscribe links",
        chip(d.unsubscribeSecretSet ? "good" : "bad", d.unsubscribeSecretSet ? "Configured" : "Missing"),
        d.unsubscribeSecretSet ? "Links are signed and cannot be guessed."
          : "Set NEWSLETTER_UNSUBSCRIBE_SECRET: sending is blocked without it."));
      p3.appendChild(statusRow("HTML sanitiser", chip("neutral", g.sanitizer),
        "Every newsletter body is sanitised on the server before it is stored or sent."));
      var admins = d.adminEmails || [d.adminEmail];
      var adminRow = el("div", "n-status-row");
      adminRow.appendChild(el("div", "n-status-lbl",
        admins.length > 1 ? "Admin accounts" : "Admin account"));
      adminRow.appendChild(chip("info", String(admins.length)));
      var av2 = el("div", "n-status-val");
      admins.forEach(function (a, i) {
        var c = el("span", "n-code", a + (i === 0 ? "  (primary)" : ""));
        c.style.marginRight = "6px"; av2.appendChild(c);
      });
      av2.appendChild(el("div", "n-hint",
        "Only these exact accounts can open this page. The primary also receives "
        + "new-subscriber notifications and test emails."));
      adminRow.appendChild(av2);
      p3.appendChild(adminRow);
      p3.appendChild(statusRow("Links point at", chip("neutral", d.appBaseUrl), "Website: " + d.siteUrl));
      p3.appendChild(statusRow("Privacy Policy", chip("neutral", d.privacyUrl), "Linked in every email footer."));
      host.appendChild(p3);
    });
  }

  /* ══════════════════════════════════════════════════════════════
     MODAL
     ══════════════════════════════════════════════════════════════ */
  function confirmModal(opts) {
    var back = el("div", "n-modal-back");
    var box = el("div", "n-modal");
    box.setAttribute("role", "dialog");
    box.setAttribute("aria-modal", "true");

    box.appendChild(el("h2", null, opts.title));
    if (opts.body) box.appendChild(el("p", null, opts.body));
    if (opts.warn) box.appendChild(el("div", "n-warn-box", opts.warn));
    if (opts.facts && opts.facts.length) {
      var f = el("div", "n-modal-facts");
      opts.facts.forEach(function (row) {
        var r = el("div", "n-modal-fact");
        r.appendChild(el("span", null, row[0]));
        r.appendChild(el("b", null, String(row[1] == null ? "" : row[1])));
        f.appendChild(r);
      });
      box.appendChild(f);
    }
    if (opts.content) box.appendChild(opts.content);

    var acts = el("div", "n-modal-acts");
    var cancel = el("button", "n-btn", "Cancel");
    var ok = el("button", "n-btn " + (opts.send ? "send" : (opts.danger ? "danger" : "primary")),
      opts.confirmLabel || "Confirm");
    // A result-only modal (the self-test report) has nothing to cancel, and a
    // "Cancel" next to a finished report reads as if it could undo something.
    if (!opts.hideCancel) acts.appendChild(cancel);
    acts.appendChild(ok);
    box.appendChild(acts);
    back.appendChild(box);
    $("modalHost").appendChild(back);

    function close() { if (back.parentNode) back.parentNode.removeChild(back); document.removeEventListener("keydown", onKey); }
    function onKey(e) { if (e.key === "Escape") close(); }
    document.addEventListener("keydown", onKey);
    cancel.addEventListener("click", close);
    back.addEventListener("click", function (e) { if (e.target === back) close(); });

    var busy = false;
    ok.addEventListener("click", function () {
      if (busy) return;
      busy = true; ok.disabled = true; cancel.disabled = true;
      var label = ok.textContent; ok.textContent = "Working…";
      opts.onConfirm(function done() {
        busy = false; ok.disabled = false; cancel.disabled = false; ok.textContent = label;
      }, close);
    });
    setTimeout(function () { ok.focus(); }, 40);
    return { close: close };
  }

  /* ══════════════════════════════════════════════════════════════
     BOOT
     ══════════════════════════════════════════════════════════════ */
  function boot() {
    var cfg = window.__FISH_FIREBASE_CONFIG || {};
    if (!cfg.apiKey) {
      showGate("Sign-in is not configured on this server.");
      return;
    }
    firebase.initializeApp(cfg);
    auth = firebase.auth();

    $("signinBtn").addEventListener("click", function () {
      var provider = new firebase.auth.GoogleAuthProvider();
      // Always show the chooser: Tim has more than one Google account, and
      // silently reusing the wrong one is the confusing failure here.
      provider.setCustomParameters({ prompt: "select_account" });
      auth.signInWithPopup(provider).catch(function (e) {
        showGate((e && e.message) || "Sign-in failed.");
      });
    });
    function signOut() { idTokenCache = { token: "", at: 0 }; auth.signOut(); }
    $("signoutBtn").addEventListener("click", signOut);
    $("gateSignout").addEventListener("click", signOut);

    auth.onAuthStateChanged(function (user) {
      currentUser = user;
      idTokenCache = { token: "", at: 0 };
      if (!user) { isAdmin = false; showGate(""); return; }

      $("gateMsg").textContent = "Checking access…";
      $("gateMsg").className = "n-gate-msg";
      // The SERVER decides. This call is the authorisation check; the page just
      // reflects its answer.
      api("whoami").then(function (d) {
        if (!d || !d.ok) {
          isAdmin = false;
          showGate("The account " + (user.email || "you signed in with") +
                   " is not authorised for the newsletter admin.");
          return;
        }
        isAdmin = true;
        var w = $("whoami"); w.innerHTML = "";
        if (user.photoURL) {
          var img = document.createElement("img");
          img.src = user.photoURL; img.alt = ""; w.appendChild(img);
        }
        w.appendChild(el("span", null, d.email));
        showApp();
        buildNav();
        render();
        loadCampaigns();
      });
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
