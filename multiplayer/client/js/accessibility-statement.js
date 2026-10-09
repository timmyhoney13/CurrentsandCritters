/* ================================================================
 * Currents and Critters, the Accessibility Statement.
 *
 * ⚠️ NOT REVIEWED BY A LAWYER, and more importantly NOT the product of a
 * formal accessibility audit. Everything claimed below was checked by hand on
 * 2026-10-09 and nothing is claimed that was not:
 *   ✓ prefers-reduced-motion is honoured: 8 stylesheets, 12 occurrences in
 *     css/preview.css alone.
 *   ✓ <html lang="en"> and aria-label landmarks on the published pages.
 *   ✓ The legal and informational pages reflow at 390px with no horizontal
 *     scrolling, verified in a real browser.
 *   ✓ The documents are real headings in order (h1 then h3 per section) with
 *     a contents list, and they print without the site chrome (@media print).
 *   ✓ Text contrast MEASURED on the legal pages with the real WCAG 2.1
 *     formula (linearised channels, not raw sRGB): body text 7.5:1, list
 *     items 7.5:1, headings and callouts 12.8:1, contents links 15.2:1,
 *     against the #f7fbff page. AA needs 4.5:1 for normal text, so these
 *     pass with room to spare. This is the ONLY contrast figure anybody has
 *     measured; the game over artwork is still unmeasured.
 *   ✓ Keyboard order walked at 1280px: Tab lands on the brand link, then the
 *     nav, then every contents entry in document order, then the document,
 *     then the footer. 28 focusable elements, no tabindex tricks.
 *   ✗ There is NO skip-to-content link on any page. Checked; absent.
 *   ✗ No custom :focus style exists anywhere in these pages' stylesheets, so
 *     focus visibility is the BROWSER DEFAULT ring. Nothing removes it
 *     (no `outline: none` rules), so this is adequate rather than good.
 *   ✗ No screen-reader pass, no axe/Lighthouse run, no keyboard-only run of
 *     the GAME itself. The game is a drag-and-drop card table and is the
 *     least accessible part of the product.
 * Section 5 says all of that out loud. Do not upgrade these claims without
 * doing the work first: an accessibility statement that overclaims is worse
 * than none, because disabled players rely on it to decide whether to bother.
 *
 * ⚠️ For a lawyer: ADA Title III web obligations for a commercial site, and
 * whether a VPAT or formal WCAG 2.1 AA conformance claim is wanted. This page
 * states a GOAL of WCAG 2.1 AA and does not claim conformance.
 * ================================================================ */
(function () {
  "use strict";

  var K = window.ccLegalKit;
  var ul = K.ul, callout = K.callout, mailto = K.mailto;
  var EMAIL = K.EMAIL;

  window.CC_LEGAL = window.CC_LEGAL || {};
  window.CC_LEGAL.accessibility = {
    title: "Accessibility Statement",
    updated: "October 9, 2026",
    lede:
      "We want as many people as possible to be able to read our pages and play our game. This " +
      "page says what we have actually done, what we know is not good enough yet, and how to tell " +
      "us when something is in your way.",

    sections: [
      {
        title: "What We Are Aiming For",
        body:
          "<p>We aim to meet <strong>WCAG 2.1 Level AA</strong> on our websites and informational pages.</p>" +
          callout("We are not claiming that we currently conform to WCAG 2.1 AA. We have not had a formal accessibility audit, and we would rather tell you that than imply a standard we have not tested against.") +
          "<p>This statement describes the real state of things on the date at the top of the page. It will change as we fix things.</p>",
      },
      {
        title: "What Works Today",
        body:
          "<p>On our websites and policy pages:</p>" +
          ul([
            "Pages declare their language, and use real headings in order, so a screen reader can move through a document by heading",
            "Each long document has a contents list, and every entry is a working link to a real section",
            "Pages reflow to a narrow phone screen without sideways scrolling, and they survive being zoomed in",
            "Links and buttons are real links and buttons, so they work with a keyboard and are announced properly",
            "Images used as decoration are marked as decoration, so they are not read out",
            "Colour is not the only way information is conveyed",
            "On these policy pages we have measured the text contrast rather than guessed at it: body text and lists come out at about 7.5 to 1, and headings at about 12.8 to 1, where the AA standard asks for 4.5 to 1",
            "Tabbing through a policy page goes in the order you would expect: the navigation, then each contents entry, then the document, then the footer",
            "Long documents print cleanly, without the site navigation",
          ]) +
          "<p>Across the game and the site:</p>" +
          ul([
            "If your system or browser is set to reduce motion, we honour that and turn down animation",
            "Text can be resized by your browser without the layout breaking",
            "All of our legal and policy pages can be read without an account, and without signing in",
          ]),
      },
      {
        title: "What Is Not Good Enough Yet",
        body:
          "<p>Being honest about this is the point of the page.</p>" +
          ul([
            "<strong>There is no skip-to-content link</strong> on our pages, so a keyboard or screen-reader user has to pass the navigation on every page. We intend to add one.",
            "<strong>We rely on your browser’s own focus ring</strong> rather than drawing a clearer one of our own. Nothing on our pages removes it, so you can always see where you are, but a stronger focus style would be easier to follow.",
            "<strong>The game itself is the least accessible part of the product.</strong> It is a card table built around dragging cards, reading a board, and spotting symbols and colours at a glance. It has not been tested with a screen reader, and it is not currently playable without sight.",
            "<strong>We have not done a keyboard-only pass of the game.</strong> Some actions at the table may be reachable only by pointer or touch.",
            "<strong>Snap &amp; Score needs a camera</strong> and the ability to frame a physical board in a photograph, which will not work for everybody. The board can always be scored by hand instead, using the printed Rules of the Ocean.",
            "<strong>We have not run a formal audit</strong>, automated or human, and we have not had the game tested by disabled players. No VPAT exists.",
            "<strong>Some colour contrast has not been measured</strong> across every screen in the game, particularly over artwork.",
          ]) +
          "<p>We are not listing these to excuse them. We are listing them so you know what you are walking into before you spend your time on it.</p>",
      },
      {
        title: "Playing Without the Online Game",
        body:
          "<p>Currents and Critters is a physical card game as well as an online one, and the physical game does not need a screen at all.</p>" +
          ul([
            "The printed Rules of the Ocean are available at /rules, as real text rather than an image, so they can be read aloud by a screen reader or enlarged",
            "A physical game can be scored by hand, with no camera and no account",
            "Snap &amp; Score is an aid for scoring, never a requirement",
          ]) +
          "<p>If reading the rules in another format would help, email us and tell us which format. We will do what we can.</p>",
      },
      {
        title: "Third-Party Parts We Do Not Control",
        body:
          "<p>Some things happen on somebody else’s pages, and their accessibility is theirs:</p>" +
          ul([
            "Signing in with Google happens on Google’s pages",
            "Paying happens on Stripe’s checkout pages",
            "Linking a Discord account happens on Discord’s pages",
          ]) +
          "<p>If one of those blocks you, tell us anyway. We can sometimes offer another route, and we would like to know.</p>",
      },
      {
        title: "Tell Us When Something Is in Your Way",
        body:
          "<p>If any part of Currents and Critters is difficult or impossible for you to use, please email us:</p>" +
          "<p>" + mailto(EMAIL) + "</p>" +
          "<p>It helps if you can tell us:</p>" +
          ul([
            "The page or the part of the game where it happened",
            "What you were trying to do",
            "What assistive technology, browser, or device you were using, if you know",
          ]) +
          callout("You do not need to use the right words or know the standards. “I cannot read this” or “I cannot get past this screen” is a perfectly good report.") +
          "<p>We will reply, we will tell you honestly whether we can fix it and roughly when, and if there is a way around it in the meantime we will tell you what it is.</p>" +
          "<p>If you need something from us in a different format in order to use it, ask, and we will try to provide it.</p>",
      },
      {
        title: "How This Page Changes",
        body:
          "<p>We will update this statement as we fix things and as we test more, and the “Last updated” date at the top will tell you when it last changed.</p>" +
          "<p>When we make an accessibility claim on this page, we will have checked it first.</p>" +
          "<p>Our Business and Legal Information page lists our other policies and how to contact us about anything else.</p>",
      },
    ],
  };
})();
