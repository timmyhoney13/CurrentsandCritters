/* ================================================================
 * Currents and Critters, the legal document kit.
 *
 * Shared by the seven legal pages that are not /privacy. It does three
 * things and nothing else:
 *
 *   1. REGISTRY.  window.CC_LEGAL[slug] = { title, updated, lede, sections }
 *      Each document module (js/terms-of-service.js, js/cookie-notice.js …)
 *      registers itself here. One document, one file, one source of its text,
 *      exactly the arrangement js/privacy-policy.js already uses.
 *
 *   2. BUILDERS.  ul() / callout() / mailto() / addressBlock(), so every
 *      document produces the SAME markup the privacy policy does and is
 *      styled by css/privacy.css with no new classes. A document body may
 *      only use those classes: that is what keeps the in-game dark skin and
 *      the website light skin both working for free.
 *
 *   3. RENDER.  ccLegalRender(slug) mounts the document, builds the contents
 *      rail and the mobile disclosure from the SAME section list the headings
 *      come from, tracks the section being read, and wires back-to-top.
 *
 * Section numbers are derived from array POSITION, never stored on the
 * section, so a document cannot carry a stale number and #sN deep links
 * always match the headings. Reordering a section renumbers the document.
 *
 * NOTHING in here states a legal position. The text lives in the document
 * modules; this file is plumbing.
 * ================================================================ */
(function () {
  "use strict";

  window.CC_LEGAL = window.CC_LEGAL || {};

  // ── Builders: the only markup a document body may produce ───────────────
  function ul(items) {
    return '<ul class="pp-list">' + items.map(function (t) {
      return "<li>" + t + "</li>";
    }).join("") + "</ul>";
  }
  function callout(text) {
    return '<p class="pp-callout">' + text + "</p>";
  }
  function mailto(addr) {
    return '<a class="pp-mail" href="mailto:' + addr + '">' + addr + "</a>";
  }
  function addressBlock(lines) {
    return '<address class="pp-address">' + lines.join("<br>") + "</address>";
  }
  function slug(n) { return "s" + n; }

  window.ccLegalKit = {
    ul: ul, callout: callout, mailto: mailto, addressBlock: addressBlock,
    // The business facts every document repeats. Confirmed by the owner on
    // 2026-10-09; the same address is published by
    // newsletter_email.BUSINESS_ADDRESS_LINES and client/unsubscribe.html,
    // so all three change together or not at all.
    EMAIL: "timothy.honey@beardedsealstudios.com",
    COMPANY: "Bearded Seal Studios LLC",
    ADDRESS_LINES: [
      "<strong>Bearded Seal Studios LLC</strong>",
      "916A South Douglas Avenue",
      "Nashville, Tennessee 37204-2021",
      "United States",
    ],
  };

  // ── Render ──────────────────────────────────────────────────────────────
  // Builds the same document shape js/privacy-policy.js produces, so
  // css/privacy.css styles it with no additions.
  function docHtml(doc) {
    var intro =
      '<div class="pp-updated">Last updated: <strong>' + doc.updated + "</strong></div>" +
      '<p class="pp-lede">' + doc.lede + "</p>";

    if (doc.appliesTo && doc.appliesTo.length) {
      intro +=
        '<div class="pp-applies">' +
          '<div class="pp-applies-head">' + (doc.appliesHead || "This document applies to:") + "</div>" +
          ul(doc.appliesTo) +
        "</div>";
    }

    var body = doc.sections.map(function (s, i) {
      var n = i + 1;
      return '<section class="pp-sec" id="' + slug(n) + '">' +
        '<h3 class="pp-h"><span class="pp-num">' + n + "</span>" + s.title + "</h3>" +
        '<div class="pp-body">' + s.body + "</div>" +
      "</section>";
    }).join("");

    return '<div class="pp-doc">' + intro + body + "</div>";
  }

  function sectionList(doc) {
    return doc.sections.map(function (s, i) {
      return { n: i + 1, id: slug(i + 1), title: s.title };
    });
  }

  window.ccLegalRender = function (which) {
    var doc = window.CC_LEGAL[which];
    var mount = document.getElementById("doc-mount");
    // Leave the page's own fallback message alone if the document module
    // did not load; an empty white card is worse than saying so.
    if (!doc || !mount) return false;

    mount.innerHTML = docHtml(doc);
    var secs = sectionList(doc);

    var built = secs.map(function (s) {
      return '<a href="#' + s.id + '" data-sec="' + s.id + '"><i>' + s.n + "</i><span>" + s.title + "</span></a>";
    }).join("");
    var list  = document.getElementById("toc-list");
    var listM = document.getElementById("toc-m-list");
    if (list)  list.innerHTML  = built;
    if (listM) listM.innerHTML = built;

    // Tapping a mobile entry closes the disclosure so the section is visible.
    var det = document.getElementById("toc-m");
    if (listM && det) {
      listM.addEventListener("click", function (ev) {
        if (ev.target.closest("a")) det.open = false;
      });
    }

    // ── Highlight the section being read (desktop rail only) ─────────────
    var links = {};
    if (list) {
      list.querySelectorAll("a").forEach(function (a) { links[a.dataset.sec] = a; });
    }
    var nodes = Array.prototype.slice.call(document.querySelectorAll(".pp-sec"));
    var current = "";
    function markCurrent() {
      var best = "";
      for (var i = 0; i < nodes.length; i++) {
        // 120px down the viewport: whatever is under that line is what is
        // being read, which beats "topmost visible" on short sections.
        if (nodes[i].getBoundingClientRect().top <= 120) best = nodes[i].id;
        else break;
      }
      if (!best && nodes.length) best = nodes[0].id;
      if (best === current) return;
      if (links[current]) links[current].classList.remove("on");
      if (links[best]) {
        links[best].classList.add("on");
        var r = links[best].getBoundingClientRect(), p = list.getBoundingClientRect();
        if (r.top < p.top || r.bottom > p.bottom) links[best].scrollIntoView({ block: "nearest" });
      }
      current = best;
    }

    var top = document.getElementById("totop");
    if (top) top.addEventListener("click", function () {
      window.scrollTo({ top: 0, behavior: "smooth" });
    });

    var ticking = false;
    function onScroll() {
      if (ticking) return;
      ticking = true;
      requestAnimationFrame(function () {
        ticking = false;
        markCurrent();
        if (top) top.classList.toggle("show", window.scrollY > 600);
      });
    }
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll, { passive: true });
    window.addEventListener("hashchange", markCurrent);
    markCurrent();
    return true;
  };

  // Exposed for the test: prove the contents and the headings come from one
  // list, without a browser.
  window.ccLegalDocHtml = docHtml;
  window.ccLegalSections = sectionList;
})();
