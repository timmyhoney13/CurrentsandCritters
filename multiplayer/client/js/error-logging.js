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
      fn.call(console, "[CCError]", entry.kind, entry.detail);
    } catch (_) {}
    return entry;
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
      report("resource_load_failed", {
        tag: tag,
        url: src,
        id: target.id || "",
        className: target.className || ""
      }, "error");
      return;
    }
    report("browser_error", {
      message: ev && ev.message,
      source: ev && ev.filename,
      line: ev && ev.lineno,
      column: ev && ev.colno
    }, "error");
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
