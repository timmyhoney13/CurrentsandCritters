/* ================================================================
 * Currents and Critters, the phone reading bar for the legal pages.
 *
 * All eight policy pages load this. It is a PROGRESSIVE ENHANCEMENT and it
 * reads the DOM rather than any document model, so it does not care whether
 * the page was built by js/legal-kit.js or by privacy.html's own inline
 * renderer. If it never runs, the <details> contents list is still there and
 * still works, which is why the stylesheet only hides that list once this
 * script has set html.lm-on.
 *
 * WHAT IT BUILDS, on phones only (<=767px):
 *   • one fixed bar at the BOTTOM of the screen, because that is where a
 *     thumb is on a six-inch phone and a long policy is read one-handed.
 *     It says which section you are in, opens the contents, and takes you
 *     back to the top.
 *   • a reading-progress hairline on the bar's top edge, so a nineteen
 *     section document stops feeling bottomless.
 *   • a contents sheet, keyboard operable, Escape to close, focus returned
 *     to the button that opened it.
 *
 * WHY A BOTTOM BAR AND NOT A STICKY HEADER: the sticky site header is
 * already at the top and a second bar under it ate a fifth of an iPhone SE's
 * viewport before a word of the policy appeared. The bar also replaces the
 * floating "Top" pill rather than sitting beside it, so a phone has ONE
 * floating control instead of two that overlap.
 *
 * On iPad and desktop this does nothing at all: the contents rail is a
 * better answer whenever there is room for it.
 * ================================================================ */
(function () {
  "use strict";

  // Must match the phone breakpoint in css/legal-page.css (and the copy
  // inlined in privacy.html), or the bar appears beside the contents rail or
  // vanishes where there is no rail. 699 because the iPad mini is 744pt wide
  // in portrait and should get the rail, not the bar.
  var PHONE = "(max-width: 699px)";
  var mq = window.matchMedia ? window.matchMedia(PHONE) : null;
  if (!mq) return;

  var secs = [];          // [{ id, n, title, el }]
  var built = false;
  var bar, prog, where, countEl, titleEl, sheet, scrim, list, openBtn;
  var current = -1;

  function collect() {
    var nodes = document.querySelectorAll(".pp-sec");
    secs = [];
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      var h = el.querySelector(".pp-h");
      if (!h) continue;
      var num = h.querySelector(".pp-num");
      // The heading prints its number in a span; the title is the rest.
      var title = h.textContent || "";
      if (num && num.textContent) title = title.slice(num.textContent.length);
      secs.push({ id: el.id, n: i + 1, title: title.trim(), el: el });
    }
    return secs.length > 0;
  }

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  function build() {
    if (built || !collect()) return built;

    // ── the bar ───────────────────────────────────────────────────────
    bar = el("div", "lm-bar");
    prog = el("div", "lm-prog");

    where = el("button", "lm-where");
    where.type = "button";
    where.setAttribute("aria-haspopup", "dialog");
    where.setAttribute("aria-expanded", "false");
    countEl = el("span", "lm-count", "Section 1 of " + secs.length);
    titleEl = el("span", "lm-title", secs[0].title);
    where.appendChild(countEl);
    where.appendChild(titleEl);

    var top = el("button", "lm-top", "↑");
    top.type = "button";
    top.setAttribute("aria-label", "Back to top");

    bar.appendChild(prog);
    bar.appendChild(where);
    bar.appendChild(top);

    // ── the sheet ─────────────────────────────────────────────────────
    scrim = el("div", "lm-scrim");
    sheet = el("div", "lm-sheet");
    sheet.setAttribute("role", "dialog");
    sheet.setAttribute("aria-modal", "true");
    sheet.setAttribute("aria-label", "Contents");

    var head = el("div", "lm-sheet-head");
    head.appendChild(el("b", null, "Contents"));
    var close = el("button", "lm-close", "Close");
    close.type = "button";
    head.appendChild(close);

    list = el("div", "lm-sheet-list");
    for (var i = 0; i < secs.length; i++) {
      var a = document.createElement("a");
      a.href = "#" + secs[i].id;
      a.dataset.sec = secs[i].id;
      var num = el("i", null, String(secs[i].n));
      var sp = el("span", null, secs[i].title);
      a.appendChild(num);
      a.appendChild(sp);
      list.appendChild(a);
    }

    sheet.appendChild(head);
    sheet.appendChild(list);

    document.body.appendChild(scrim);
    document.body.appendChild(sheet);
    document.body.appendChild(bar);

    where.addEventListener("click", openSheet);
    close.addEventListener("click", closeSheet);
    scrim.addEventListener("click", closeSheet);
    top.addEventListener("click", function () {
      closeSheet();
      // "instant", not "auto": per spec "auto" falls back to the CSS
      // scroll-behavior, and this site sets html { scroll-behavior: smooth },
      // so "auto" would still animate for somebody who asked it not to.
      window.scrollTo({ top: 0, behavior: reduced() ? "instant" : "smooth" });
    });
    // Tapping an entry closes the sheet; the browser does the jump, and
    // scroll-margin-top on .pp-sec keeps it clear of the sticky header.
    list.addEventListener("click", function (ev) {
      if (ev.target.closest("a")) closeSheet();
    });
    document.addEventListener("keydown", function (ev) {
      if (!sheet.classList.contains("open")) return;
      if (ev.key === "Escape") {
        ev.preventDefault();
        closeSheet();
        return;
      }
      // aria-modal is a promise that Tab stays inside, so keep it.
      if (ev.key !== "Tab") return;
      var stops = sheet.querySelectorAll("a[href], button");
      if (!stops.length) return;
      var first = stops[0], last = stops[stops.length - 1];
      if (ev.shiftKey && document.activeElement === first) {
        ev.preventDefault();
        last.focus();
      } else if (!ev.shiftKey && document.activeElement === last) {
        ev.preventDefault();
        first.focus();
      }
    });

    built = true;
    return true;
  }

  function reduced() {
    return window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  function openSheet() {
    if (!sheet) return;
    // Always the bar button. Reading document.activeElement here was wrong:
    // a tap does not reliably focus a <button> first, so closing the sheet
    // dropped focus onto <body> and a keyboard user lost their place.
    openBtn = where;
    sheet.classList.add("open");
    scrim.classList.add("open");
    where.setAttribute("aria-expanded", "true");
    markSheet();
    var on = list.querySelector("a.on") || list.querySelector("a");
    if (on) on.focus();
  }

  function closeSheet() {
    if (!sheet || !sheet.classList.contains("open")) return;
    sheet.classList.remove("open");
    scrim.classList.remove("open");
    where.setAttribute("aria-expanded", "false");
    if (openBtn && openBtn.focus) openBtn.focus();
  }

  function markSheet() {
    if (!list) return;
    var links = list.querySelectorAll("a");
    for (var i = 0; i < links.length; i++) {
      links[i].classList.toggle("on", i === current);
    }
  }

  // The sticky site header's real height, so a jumped-to heading is not
  // hidden underneath it. Measured rather than guessed: the header is two
  // lines on a narrow phone and one on a wide one.
  function syncSticky() {
    var tb = document.querySelector(".topbar");
    var h = tb ? Math.round(tb.getBoundingClientRect().height) : 58;
    document.documentElement.style.setProperty("--lm-sticky", h + "px");
  }

  function update() {
    if (!built) return;
    // Which section is under a line a third of the way down the screen.
    var line = window.innerHeight * 0.33;
    var found = 0;
    for (var i = 0; i < secs.length; i++) {
      if (secs[i].el.getBoundingClientRect().top <= line) found = i;
      else break;
    }
    if (found !== current) {
      current = found;
      countEl.textContent = "Section " + secs[found].n + " of " + secs.length;
      titleEl.textContent = secs[found].title;
      markSheet();
    }
    // Progress through the document, not the page: 0% at the first heading,
    // 100% when the last section's bottom clears the viewport.
    var first = secs[0].el.getBoundingClientRect();
    var last = secs[secs.length - 1].el.getBoundingClientRect();
    var startY = window.scrollY + first.top;
    var endY = window.scrollY + last.bottom - window.innerHeight;
    var pct = endY > startY ? (window.scrollY - startY) / (endY - startY) : 1;
    pct = Math.max(0, Math.min(1, pct));
    prog.style.width = (pct * 100).toFixed(1) + "%";
  }

  var ticking = false;
  function onScroll() {
    if (ticking) return;
    ticking = true;
    requestAnimationFrame(function () {
      ticking = false;
      update();
    });
  }

  function teardown() {
    closeSheet();
    document.documentElement.classList.remove("lm-on");
  }

  function apply() {
    if (mq.matches) {
      if (!build()) return;            // no sections yet, try again later
      document.documentElement.classList.add("lm-on");
      syncSticky();
      current = -1;
      update();
    } else if (built) {
      teardown();
    }
  }

  function start() {
    apply();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", function () { syncSticky(); apply(); onScroll(); }, { passive: true });
    window.addEventListener("orientationchange", function () {
      setTimeout(function () { syncSticky(); apply(); onScroll(); }, 120);
    });
    if (mq.addEventListener) mq.addEventListener("change", apply);
    else if (mq.addListener) mq.addListener(apply);
    window.addEventListener("hashchange", onScroll);
  }

  // The documents are injected by another script, so wait for the sections
  // to exist rather than assuming they do at DOMContentLoaded.
  function waitForDoc(tries) {
    if (document.querySelector(".pp-sec")) { start(); return; }
    if (tries <= 0) { start(); return; }
    setTimeout(function () { waitForDoc(tries - 1); }, 60);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { waitForDoc(40); });
  } else {
    waitForDoc(40);
  }
})();
