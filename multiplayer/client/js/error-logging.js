(function () {
  "use strict";

  var KEY = "cc_error_log_v1";
  var MAX = 80;

  function nowIso() {
    try { return new Date().toISOString(); } catch (_) { return ""; }
  }

  function safeText(value, maxLen) {
    var s = "";
    try { s = String(value == null ? "" : value); } catch (_) { s = ""; }
    s = s.replace(/[^\S\r\n]+/g, " ").trim();
    if (maxLen && s.length > maxLen) s = s.slice(0, maxLen) + "…";
    return s;
  }

  function safeUrl(value) {
    var raw = safeText(value, 500);
    if (!raw) return "";
    try {
      var u = new URL(raw, location.href);
      return u.origin === location.origin ? u.pathname : u.origin + u.pathname;
    } catch (_) {
      return raw.split("?")[0].slice(0, 220);
    }
  }

  function load() {
    try {
      var parsed = JSON.parse(localStorage.getItem(KEY) || "[]");
      return Array.isArray(parsed) ? parsed.slice(-MAX) : [];
    } catch (_) {
      return [];
    }
  }

  function save(items) {
    try { localStorage.setItem(KEY, JSON.stringify(items.slice(-MAX))); } catch (_) {}
  }

  function report(kind, detail, level) {
    var entry = {
      at: nowIso(),
      kind: safeText(kind || "unknown", 80),
      level: safeText(level || "warn", 20),
      path: safeUrl(location.href),
      detail: {}
    };
    detail = detail && typeof detail === "object" ? detail : {};
    Object.keys(detail).slice(0, 24).forEach(function (key) {
      var v = detail[key];
      if (/url|src|href|path/i.test(key)) entry.detail[key] = safeUrl(v);
      else entry.detail[key] = safeText(v, 260);
    });
    var items = load();
    items.push(entry);
    save(items);
    try {
      var fn = entry.level === "error" ? console.error : console.warn;
      // The headline carries the url/message inline. Logging only the detail
      // object leaves a collapsed "Object" in the console, which tells whoever
      // is reading the log nothing about WHICH resource died.
      var head = entry.detail.url || entry.detail.message || "";
      fn.call(console, "[CCError]", entry.kind, head, entry.detail);
    } catch (_) {}
    return entry;
  }

  // Browser extensions inject their own images, styles and scripts into the
  // page. Their failures reach our capture-phase listener and would otherwise
  // be filed as game bugs. They are not ours and we cannot fix them.
  function isExtensionUrl(raw) {
    return /^(chrome|moz|safari|safari-web|ms-browser)-extension:/i.test(String(raw || ""));
  }
  // Same-origin misses are our bug (level "error"). A third-party host that
  // fails — a Google profile photo, a CDN hiccup — is worth recording but is
  // not something the game can fix, so it must not read as a game error.
  // `unknownLevel` covers a blank url: for a script that is the anonymised
  // cross-origin "Script error." (not ours), but a blank img src is our own
  // markup emitting an empty attribute (very much ours).
  function originLevel(raw, unknownLevel) {
    var s = String(raw || "");
    if (!s) return unknownLevel || "warn";
    try {
      return new URL(s, location.href).origin === location.origin ? "error" : "warn";
    } catch (_) { return "warn"; }
  }

  window.CCErrorLog = {
    report: report,
    get: load,
    clear: function () { try { localStorage.removeItem(KEY); } catch (_) {} },
    safeText: safeText,
    safeUrl: safeUrl
  };

  window.addEventListener("error", function (ev) {
    var target = ev && ev.target;
    if (target && target !== window) {
      var tag = safeText(target.tagName || "resource", 30).toLowerCase();
      var src = target.currentSrc || target.src || target.href || "";
      if (isExtensionUrl(src)) return;
      report("resource_load_failed", {
        tag: tag,
        url: src,
        id: target.id || "",
        className: safeText(target.className || "", 120)
      }, originLevel(src, "error"));
      return;
    }
    if (isExtensionUrl(ev && ev.filename)) return;
    report("browser_error", {
      message: ev && ev.message,
      source: ev && ev.filename,
      line: ev && ev.lineno,
      column: ev && ev.colno
    }, originLevel(ev && ev.filename));
  }, true);

  window.addEventListener("unhandledrejection", function (ev) {
    var reason = ev && ev.reason;
    report("unhandled_promise_rejection", {
      name: reason && reason.name,
      code: reason && reason.code,
      message: reason && (reason.message || reason)
    }, "error");
  });
})();
