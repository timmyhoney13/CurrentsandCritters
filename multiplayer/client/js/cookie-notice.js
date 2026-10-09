/* ================================================================
 * Currents and Critters, the Cookie Notice.
 *
 * ⚠️ NOT REVIEWED BY A LAWYER. Drafted 2026-10-09 from an actual audit of the
 * client and the server, not from a template. What the audit found:
 *   • The SERVER sets no cookies at all. `grep -rn "Set-Cookie" *.py` is empty.
 *   • The CLIENT never writes document.cookie. Not once.
 *   • It uses localStorage (10 keys: cc_clans, cc_guest_id,
 *     cc_last_guest_nick, cc_prestige, cc_prestige_still, cc_stripe_return,
 *     cc_tournaments, cc_active_tournament, fish_api_base_url, fish_music_on)
 *     and sessionStorage (per-room seat and hand keys, cc_h2h_homecoming).
 *   • Firebase Authentication keeps its own sign-in state in browser storage.
 *   • There is NO Google Analytics, no gtag, no Tag Manager, no Meta pixel,
 *     no Hotjar, no Mixpanel, no Segment, no Plausible, no DoubleClick.
 *     Verified by grep over index.html, score.html, shop.html and
 *     multiplayer/client/*.html. Analytics is first-party and admin-only
 *     (analytics_server.py), computed server-side from the accounts.
 *
 * ⚠️ THE JURISDICTION QUESTION A LAWYER SHOULD ANSWER: everything we store is
 * either strictly necessary (sign-in, security, game state) or a functional
 * preference the player set themselves, which is why this notice does not ship
 * a consent banner. Under the EU ePrivacy Directive and the UK PECR, consent is
 * required for storage that is NOT strictly necessary, and "functional
 * preference" is a narrower exemption in some readings than in others. If the
 * site ever adds advertising, cross-site analytics, or any third-party
 * measurement, this notice is wrong and a real consent gate is required.
 *
 * Do not add a decorative banner. A banner that does not actually gate
 * anything is worse than no banner: it claims a control that does not exist.
 * ================================================================ */
(function () {
  "use strict";

  var K = window.ccLegalKit;
  var ul = K.ul, callout = K.callout, mailto = K.mailto;
  var EMAIL = K.EMAIL;

  window.CC_LEGAL = window.CC_LEGAL || {};
  window.CC_LEGAL.cookies = {
    title: "Cookie Notice",
    updated: "October 9, 2026",
    lede:
      "This notice explains the cookies and similar browser storage that Currents and Critters uses. " +
      "The short version: we use what the game needs to sign you in, keep you in your seat, and " +
      "remember your settings. We do not use advertising cookies and we do not track you across " +
      "other websites.",

    sections: [
      {
        title: "The Short Version",
        body:
          callout("We do not use advertising cookies. We do not use third-party analytics that follow you around the internet. We do not sell your information.") +
          "<p>What we do use falls into two groups:</p>" +
          ul([
            "<strong>Strictly necessary.</strong> Storage the game cannot work without, such as keeping you signed in, holding your seat in a game, and protecting against fraud and abuse.",
            "<strong>Functional preferences.</strong> Small settings you chose yourself, such as whether the music is on.",
          ]) +
          "<p>Because we only use those two groups, there is no advertising or tracking to switch off, and you will not find a consent banner on our site asking you to accept tracking that we do not do.</p>",
      },
      {
        title: "Cookies, and What We Actually Use Instead",
        body:
          "<p>A cookie is a small file a website asks your browser to keep. Related technologies do a similar job, including <strong>local storage</strong>, <strong>session storage</strong>, and the <strong>service worker cache</strong> that lets the game load quickly.</p>" +
          "<p>Currents and Critters mostly does not use cookies. Our own servers do not set any, and our own code does not write any. What we use is browser storage, which stays on your device and is not sent along with every request the way a cookie is.</p>" +
          "<p>Sign-in is handled by Google Firebase Authentication, which keeps your signed-in state in your browser’s storage so you do not have to sign in on every visit.</p>" +
          "<p>Payment pages are hosted by Stripe. Stripe sets what it needs on its own checkout pages, under its own privacy practices.</p>",
      },
      {
        title: "What We Store, and Why",
        body:
          "<p><strong>Strictly necessary, for signing in and playing:</strong></p>" +
          ul([
            "Your signed-in state, kept by Firebase Authentication",
            "A guest identifier, if you play as a guest without an account",
            "The seat you hold in a game you are in, and your hand in a shared-device competitive game, so a refresh does not lose your place",
            "The last nickname you used as a guest, so you do not retype it",
            "Whether you were sent out to Stripe and came back, so a purchase can be matched to your visit",
            "The address of the game server your browser should talk to",
          ]) +
          "<p><strong>Functional, because you chose it:</strong></p>" +
          ul([
            "Whether the music is on or off",
            "Clan, tournament, and prestige views you have open, so the screen comes back the way you left it",
          ]) +
          "<p><strong>On the device, not on your account:</strong></p>" +
          "<p>Some progress is filed on the device you played on rather than on an account, such as challenge slots, win streaks, and which opponents you have met. Clearing your browser storage clears that progress.</p>" +
          "<p><strong>The service worker cache:</strong></p>" +
          "<p>The game stores its own files so it starts quickly and survives a poor connection. That cache holds game files, not information about you, and it is replaced when we publish a new build.</p>",
      },
      {
        title: "Analytics",
        body:
          "<p>We do look at how the game and the website are performing. We do it without tracking you.</p>" +
          "<p>Our performance figures are produced on our own server from the account and game records it already holds, and they are only visible to us as an administrator. There is no third-party analytics script on our pages, and no measurement product is told about your visit.</p>" +
          callout("We do not use Google Analytics, Google Tag Manager, Meta Pixel, or any similar product anywhere on our websites or in the game.") +
          "<p>We also do not put tracking pixels in our newsletters, and we do not record whether you opened an email or which links you clicked.</p>",
      },
      {
        title: "Your Choices",
        body:
          "<p>You can control browser storage yourself:</p>" +
          ul([
            "Your browser settings can block or clear cookies and site data for our site",
            "Clearing site data signs you out, clears your settings, and clears progress that was filed on the device rather than on your account",
            "A private or incognito window keeps nothing after you close it",
            "Turning the music off is a setting in the game, not something you need your browser for",
          ]) +
          "<p>Because almost everything we store is needed to sign you in and keep you in your game, blocking it will stop the account, game, checkout, or security features working properly.</p>" +
          "<p>Progress saved to your <strong>account</strong> is not affected by clearing browser storage. Sign in again and it is there.</p>",
      },
      {
        title: "If This Ever Changes",
        body:
          "<p>If we ever begin using advertising cookies, cross-site analytics, or any other optional tracking, we will update this notice first, and we will provide a real consent control that genuinely prevents that tracking until you agree to it, wherever the law requires one.</p>" +
          "<p>We will not add a banner that claims to control something it does not.</p>" +
          "<p>Our Privacy Policy explains everything else we collect and why. Questions about this notice can go to " + mailto(EMAIL) + ".</p>",
      },
    ],
  };
})();
