/* ================================================================
 * Currents and Critters — the Privacy Policy, one shared source.
 *
 * The policy text below is the legal document, reproduced WORD FOR WORD.
 * Nothing here is paraphrased, trimmed or "improved" — only the layout
 * (headings, cards, the contact block) is ours. If the policy changes,
 * change it HERE and nowhere else.
 *
 * Rendered in two places, both from this one string:
 *   • The public page   /privacy            (light sea-glass skin)
 *   • In game → Settings → Legal → Privacy   (deep-ocean skin)
 * The skin comes from a wrapper class (.pp-light / .pp-dark); every rule in
 * css/privacy.css reads its colours from CSS variables set by that wrapper,
 * so the same markup looks at home on the website and over the game table.
 *
 * CC_PRIVACY_SECTIONS is the SAME list the headings are generated from, so a
 * table of contents can never drift out of step with the document.
 * ================================================================ */
(function () {
  "use strict";

  var UPDATED = "August 5, 2026";
  var EMAIL   = "timothy.honey@beardedsealstudios.com";

  // ── The document ────────────────────────────────────────────────
  // One entry per numbered section. `body` is the section's HTML; the
  // heading, its id and the anchor link are generated below so the TOC,
  // the headings and the deep links are always the same 18 things.
  var SECTIONS = [
    {
      n: 1,
      title: "Information We Collect",
      body:
        "<p>Depending on how you use our services, we may collect:</p>" +
        ul([
          "Your name and email address",
          "Information provided through Google Sign-In",
          "Your username, account information, and profile settings",
          "Your selected avatar, clan, achievements, rankings, and leaderboard information",
          "Game progress, statistics, match history, rewards, and activity",
          "Newsletter subscription status",
          "Purchase and transaction information",
          "Messages, questions, bug reports, feedback, or other information you send to us",
          "Device, browser, IP address, and basic technical information",
          "Cookies and similar information needed to operate and protect our services",
        ]) +
        "<p>You may be able to use certain parts of Currents &amp; Critters as a guest without creating an account.</p>" +
        "<p>We only collect information that is reasonably needed to provide, operate, protect, and improve our services.</p>",
    },
    {
      n: 2,
      title: "Private and Public Account Information",
      body:
        "<p>Your email address, Google account identifier, and other private account information are <strong>not publicly displayed</strong> to other players.</p>" +
        "<p>We use and share private account information only as described in this Privacy Policy. This may include securely providing limited information to service providers that help us operate our services, such as Google, Stripe, Render, database providers, and email providers.</p>" +
        "<p>These service providers may only receive information reasonably needed to provide their services.</p>" +
        "<p>Your name is not publicly displayed unless you intentionally use it as your username, profile name, clan name, or in another public part of the service.</p>" +
        "<p>Certain game information may be visible to other players. This may include:</p>" +
        ul([
          "Your username",
          "Your selected avatar",
          "Your clan name and clan icon",
          "Your rankings",
          "Your achievements",
          "Your level",
          "Your leaderboard position",
          "Your tournament activity",
          "Other information you intentionally place on a public game profile",
        ]) +
        callout("Do not place private information in your username, clan name, profile, messages, or other public areas of the game."),
    },
    {
      n: 3,
      title: "Google Sign-In",
      body:
        "<p>Currents &amp; Critters may allow you to sign in using your Google account.</p>" +
        "<p>Depending on the permissions you approve, Google may provide us with basic account information, such as:</p>" +
        ul([
          "Your name",
          "Your email address",
          "Your unique Google account identifier",
          "Your profile picture",
        ]) +
        "<p>We use this information to:</p>" +
        ul([
          "Create and manage your Currents &amp; Critters account",
          "Confirm your identity",
          "Keep you signed in",
          "Save your game progress",
          "Protect your account",
          "Prevent duplicate or fraudulent accounts",
        ]) +
        callout("We do not receive or store your Google password.") +
        "<p>Your email address, unique Google account identifier, and private Google account information are not publicly displayed to other players.</p>" +
        "<p>Your Google profile picture or name will only be displayed publicly if the game clearly allows you to choose them for a public profile feature.</p>" +
        "<p>You should review Google’s privacy information to understand how Google handles information associated with your Google account.</p>",
    },
    {
      n: 4,
      title: "Game Accounts and Activity",
      body:
        "<p>When you create an account or play Currents &amp; Critters, we may collect and save information such as:</p>" +
        ul([
          "Your username",
          "Your avatar",
          "Your level and experience points",
          "Your Critter Coins",
          "Your achievements and unlocked rewards",
          "Your game statistics",
          "Your wins and losses",
          "Your competitive rank",
          "Your clan membership and clan activity",
          "Your tournament activity",
          "Your challenge progress",
          "Your match history",
          "Reports involving cheating, abuse, or rule violations",
        ]) +
        "<p>We use this information to:</p>" +
        ul([
          "Operate the game",
          "Save your progress",
          "Calculate rankings",
          "Provide rewards",
          "Support multiplayer features",
          "Prevent cheating and abuse",
          "Resolve technical problems",
          "Improve the player experience",
        ]) +
        "<p>Some game activity may be visible to other players through leaderboards, clans, tournaments, profiles, rankings, match results, or other multiplayer features.</p>",
    },
    {
      n: 5,
      title: "Payments and Purchases",
      body:
        "<p>Payments are processed by Stripe.</p>" +
        "<p>Bearded Seal Studios LLC does not directly receive or store your complete credit card or debit card number.</p>" +
        "<p>Stripe may collect and process payment and billing information according to its own privacy practices.</p>" +
        "<p>We may receive limited transaction information, such as:</p>" +
        ul([
          "Your name",
          "Your email address",
          "Your billing or shipping information",
          "The item or service purchased",
          "The purchase amount",
          "Payment status",
          "Transaction identification number",
          "Refund status",
        ]) +
        "<p>We use this information to:</p>" +
        ul([
          "Complete purchases",
          "Deliver physical products",
          "Provide digital products or rewards",
          "Provide customer support",
          "Issue refunds",
          "Prevent fraud",
          "Maintain accounting, tax, and business records",
        ]) +
        "<p>Providing an email address for a receipt, payment, or purchase does not automatically add you to the Currents &amp; Critters newsletter.</p>",
    },
    {
      n: 6,
      title: "Newsletter and Marketing Emails",
      body:
        "<p>You will only be added to the Currents &amp; Critters email list when you intentionally provide your email address for that purpose.</p>" +
        "<p>For example, you may enter your email address in an optional field labeled:</p>" +
        "<blockquote>“Enter your email to join the Currents &amp; Critters newsletter and receive occasional updates.”</blockquote>" +
        "<p>We may send subscribers occasional emails about:</p>" +
        ul([
          "New game features and updates",
          "Online game nights and special events",
          "Progress on the physical card game",
          "Rewards and important announcements",
          "Opportunities to playtest and help improve the game",
        ]) +
        "<p>We may store:</p>" +
        ul([
          "Your email address",
          "Your subscription status",
          "The date and time you subscribed",
          "How you subscribed",
          "The date and time you unsubscribed, when applicable",
          "Information needed to process and honor your unsubscribe request",
        ]) +
        "<p>Every marketing email will include a working unsubscribe option.</p>" +
        "<p>When you unsubscribe, we will mark your email address as unsubscribed and stop sending you marketing emails.</p>" +
        "<p>We may keep a limited record of your email address and unsubscribe status so we can honor your request and avoid accidentally sending additional marketing emails.</p>" +
        "<p>You will not receive marketing emails again unless you intentionally subscribe again.</p>" +
        "<p>Unsubscribing from marketing emails will not prevent us from sending necessary messages related to:</p>" +
        ul([
          "Purchases",
          "Receipts",
          "Refunds",
          "Account security",
          "Changes that significantly affect the operation of the service",
          "Direct responses to questions or support requests you send us",
        ]),
    },
    {
      n: 7,
      title: "Messages and Communication",
      body:
        "<p>If you contact us, submit a bug report, participate in a playtest, attend an event, or send us feedback, we may collect the information you provide.</p>" +
        "<p>This may include:</p>" +
        ul([
          "Your name",
          "Your email address",
          "Your username",
          "Your message",
          "Screenshots or attachments",
          "Information about a game or technical problem",
        ]) +
        "<p>We use this information to:</p>" +
        ul([
          "Respond to you",
          "Investigate problems",
          "Provide customer support",
          "Improve the game",
          "Protect our services",
          "Maintain records of important support conversations",
        ]) +
        callout("Do not send us sensitive personal information that is not needed for your request."),
    },
    {
      n: 8,
      title: "How We Use Information",
      body:
        "<p>We may use information to:</p>" +
        ul([
          "Operate our websites and games",
          "Create and manage accounts",
          "Authenticate users",
          "Save game progress and settings",
          "Provide multiplayer features",
          "Calculate rankings and rewards",
          "Manage clans, tournaments, game nights, and events",
          "Process purchases and refunds",
          "Deliver physical or digital products",
          "Send newsletters to subscribers",
          "Respond to questions and support requests",
          "Investigate bugs and technical problems",
          "Prevent fraud, cheating, abuse, and security threats",
          "Improve our websites, games, and services",
          "Understand general website and game performance",
          "Maintain accounting, tax, and business records",
          "Enforce our rules and terms",
          "Comply with applicable legal requirements",
        ]) +
        "<p>We do not use newsletter information for unrelated purposes.</p>",
    },
    {
      n: 9,
      title: "How We Share Information",
      body:
        callout("We do not sell your personal information.") +
        "<p>We may share limited information with service providers that help us operate our business. These may include:</p>" +
        ul([
          "Stripe for payment processing",
          "Google for account sign-in, email, and Google Workspace services",
          "Render for website and server hosting",
          "Database and data storage providers",
          "Domain and website service providers",
          "Email delivery providers",
          "Security and technical service providers",
          "Shipping providers when physical products need to be delivered",
        ]) +
        "<p>These providers may receive only the information reasonably needed to perform their services.</p>" +
        "<p>We may also disclose information when reasonably necessary to:</p>" +
        ul([
          "Follow applicable laws, court orders, or legal requests",
          "Protect Bearded Seal Studios LLC and its services",
          "Investigate fraud, abuse, cheating, or security threats",
          "Protect our users or the public",
          "Enforce our terms, rules, or agreements",
          "Complete a business transfer, merger, sale, or reorganization",
        ]) +
        "<p>If ownership of the business or its services changes, personal information may be transferred as part of that transaction. Information transferred as part of a business transaction will remain subject to applicable privacy laws.</p>",
    },
    {
      n: 10,
      title: "Cookies and Technical Information",
      body:
        "<p>Our websites and online game may use cookies and similar technologies.</p>" +
        "<p>We may use cookies and similar technologies to:</p>" +
        ul([
          "Keep users signed in",
          "Maintain secure sessions",
          "Remember settings and preferences",
          "Save game-related information",
          "Prevent fraud and abuse",
          "Diagnose technical problems",
          "Understand basic website and game performance",
        ]) +
        "<p>You may be able to control cookies through your browser settings.</p>" +
        "<p>Disabling cookies that are necessary to operate the service may prevent account, game, checkout, or security features from working correctly.</p>" +
        "<p>If we begin using optional advertising or analytics cookies that require additional notice or consent, we may provide additional information or controls as required.</p>",
    },
    {
      n: 11,
      title: "Data Retention",
      body:
        "<p>We keep personal information only for as long as reasonably necessary to:</p>" +
        ul([
          "Provide our services",
          "Maintain accounts and game records",
          "Complete transactions",
          "Provide customer support",
          "Honor unsubscribe requests",
          "Prevent fraud, cheating, and abuse",
          "Resolve disputes",
          "Enforce agreements",
          "Maintain accounting, tax, and business records",
          "Meet applicable legal requirements",
        ]) +
        "<p>When information is no longer reasonably needed, we may delete it, remove identifying details, or securely retain it when required for legal, security, tax, or recordkeeping purposes.</p>" +
        "<p>Unsubscribing from the newsletter does not always mean that the subscriber record will be immediately deleted. We may keep the email address and unsubscribe status so we do not accidentally send additional marketing emails.</p>" +
        "<p>Closing a game account may not result in the immediate deletion of every record. We may retain limited information when reasonably necessary for security, fraud prevention, dispute resolution, financial recordkeeping, or legal compliance.</p>",
    },
    {
      n: 12,
      title: "Data Security",
      body:
        "<p>We use reasonable administrative, technical, and organizational safeguards designed to protect personal information.</p>" +
        "<p>These safeguards may include:</p>" +
        ul([
          "Secure account authentication",
          "Protected database access",
          "Encrypted internet connections",
          "Restricted administrative access",
          "Secure storage of private credentials",
          "Access controls",
          "Security logging",
          "Secure payment processing through Stripe",
        ]) +
        "<p>However, no website, game, database, email service, or internet transmission can be guaranteed to be completely secure.</p>" +
        "<p>You are responsible for protecting access to your Google account, email account, device, and Currents &amp; Critters account.</p>" +
        "<p>Contact us if you believe your Currents &amp; Critters account has been accessed without permission.</p>",
    },
    {
      n: 13,
      title: "Your Choices and Privacy Requests",
      body:
        "<p>Depending on your location and applicable law, you may ask us to:</p>" +
        ul([
          "Explain what personal information we maintain about you",
          "Correct inaccurate information",
          "Delete certain information",
          "Close your account",
          "Unsubscribe you from marketing emails",
          "Update your newsletter preferences",
        ]) +
        "<p>Some information may need to be retained for:</p>" +
        ul([
          "Purchases and transactions",
          "Tax and accounting records",
          "Fraud prevention",
          "Security",
          "Legal requirements",
          "Enforcing rules or resolving disputes",
          "Honoring an unsubscribe request",
        ]) +
        "<p>To make a privacy request, contact:</p>" +
        "<p>" + mailto(EMAIL) + "</p>" +
        "<p>We may need to verify your identity before completing a request. This helps prevent another person from accessing, changing, or deleting your information without permission.</p>" +
        "<p>We will respond to valid privacy requests within the time required by applicable law.</p>",
    },
    {
      n: 14,
      title: "Children’s Privacy",
      body:
        "<p>Currents &amp; Critters and our related online services are not directed to children under 13.</p>" +
        "<p>We do not knowingly collect personal information online from children under 13 without any permission or consent required by applicable law.</p>" +
        "<p>A person under 13 should not create an account, join the newsletter, make a purchase, or submit personal information through our services without the involvement of a parent or legal guardian.</p>" +
        "<p>If we learn that we collected personal information from a child under 13 without any required permission, we will take reasonable steps to delete it.</p>" +
        "<p>A parent or legal guardian who believes a child under 13 provided personal information may contact us at:</p>" +
        "<p>" + mailto(EMAIL) + "</p>",
    },
    {
      n: 15,
      title: "Third-Party Services and Links",
      body:
        "<p>Our services may use or contain links to third-party websites and services, including Google and Stripe.</p>" +
        "<p>These companies have their own privacy policies and practices.</p>" +
        "<p>We are not responsible for the content, privacy, or security practices of third-party services that we do not control.</p>" +
        "<p>You should review the privacy policies of third-party services before providing them with personal information.</p>",
    },
    {
      n: 16,
      title: "International Users",
      body:
        "<p>Bearded Seal Studios LLC is based in the United States.</p>" +
        "<p>If you access our services from another country, your information may be processed and stored in the United States or another country where our service providers operate.</p>" +
        "<p>Privacy and data protection laws in those locations may be different from the laws where you live.</p>" +
        "<p>Depending on your location and applicable law, you may have additional privacy rights. You may contact us to ask about your information or submit a privacy request.</p>",
    },
    {
      n: 17,
      title: "Changes to This Privacy Policy",
      body:
        "<p>We may update this Privacy Policy as Currents &amp; Critters, Bearded Seal Studios LLC, and our services change.</p>" +
        "<p>When we update the policy, we will change the “Last updated” date at the top of this page.</p>" +
        "<p>If we make a significant change, we may provide additional notice through the website, online game, account, or email when appropriate or legally required.</p>" +
        "<p>The updated Privacy Policy will apply from the date it is posted unless a different effective date is stated.</p>",
    },
    {
      n: 18,
      title: "Contact Us",
      body:
        "<p>Questions, concerns, or requests about this Privacy Policy may be sent to:</p>" +
        '<address class="pp-address">' +
          "<strong>Bearded Seal Studios LLC</strong><br>" +
          "916A South Douglas Avenue<br>" +
          "Nashville, Tennessee 37204-2021<br>" +
          "United States" +
        "</address>" +
        "<p><strong>Email:</strong> " + mailto(EMAIL) + "</p>",
    },
  ];

  // ── Little builders ─────────────────────────────────────────────
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
  function slug(n) { return "pp-s" + n; }

  // ── The rendered document ───────────────────────────────────────
  // Everything above the numbered sections: who it's from, when it was last
  // updated, and the exact list of properties it covers.
  var INTRO =
    '<div class="pp-updated">Last updated: <strong>' + UPDATED + "</strong></div>" +
    "<p class=\"pp-lede\">Bearded Seal Studios LLC respects your privacy. This Privacy Policy explains what information we collect, how we use it, how we share it, and how we protect it when you use our websites, games, newsletters, purchases, events, and related services.</p>" +
    '<div class="pp-applies">' +
      '<div class="pp-applies-head">This Privacy Policy applies to:</div>' +
      ul([
        "Bearded Seal Studios",
        "Currents &amp; Critters",
        "beardedsealstudios.com",
        "currentsandcritters.com",
        "The Currents &amp; Critters online game",
        "Currents &amp; Critters accounts, newsletters, purchases, game nights, events, clans, competitions, and related services",
      ]) +
    "</div>";

  var BODY = SECTIONS.map(function (s) {
    return '<section class="pp-sec" id="' + slug(s.n) + '">' +
      '<h3 class="pp-h"><span class="pp-num">' + s.n + "</span>" + s.title + "</h3>" +
      '<div class="pp-body">' + s.body + "</div>" +
    "</section>";
  }).join("");

  // The whole policy, minus any page chrome. Drop it inside an element
  // carrying .pp-light (website) or .pp-dark (in game) and it is styled.
  window.CC_PRIVACY_HTML = '<div class="pp-doc">' + INTRO + BODY + "</div>";

  // The same 18 sections, for building a table of contents that can never
  // drift away from the headings above.
  window.CC_PRIVACY_SECTIONS = SECTIONS.map(function (s) {
    return { n: s.n, id: slug(s.n), title: s.title };
  });

  window.CC_PRIVACY_UPDATED = UPDATED;
  window.CC_PRIVACY_EMAIL   = EMAIL;
})();
