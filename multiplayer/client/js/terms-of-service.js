/* ================================================================
 * Currents and Critters, the Terms of Service.
 *
 * ⚠️ NOT REVIEWED BY A LAWYER. This document was drafted on 2026-10-09 from
 * what the code actually does plus four decisions the owner made that day:
 *   • Critter Coins: no refunds once delivered, billing errors and anything
 *     the law requires still honoured. (See js/refund-policy.js.)
 *   • Disputes: Tennessee law, courts in Davidson County. No arbitration
 *     clause and no class-action waiver, by choice.
 *   • No preorders are taken on this site; the physical game is being backed
 *     through Kickstarter, so nothing here promises a ship date.
 *   • Age: mirrors the Privacy Policy exactly (not directed to under 13).
 *
 * THE SECTIONS A LICENSED ATTORNEY SHOULD READ FIRST, because they are the
 * ones that allocate risk and are most likely to be wrong or unenforceable:
 *   14 Disclaimers, 15 Limitation of Liability, 16 Indemnity,
 *   17 Governing Law and Disputes.
 * (Numbers are positional: inserting a section renumbers them, so re-check
 * this comment if you add one.)
 * Limitation-of-liability caps and warranty disclaimers are read narrowly by
 * courts and some states limit them outright. Do not treat these as settled.
 *
 * Registered into window.CC_LEGAL by js/legal-kit.js, which also supplies the
 * builders. Keep to ul/callout/mailto/addressBlock: no new CSS classes, so
 * both the website skin and the in-game dark skin style this for free.
 * ================================================================ */
(function () {
  "use strict";

  var K = window.ccLegalKit;
  var ul = K.ul, callout = K.callout, mailto = K.mailto;
  var EMAIL = K.EMAIL;

  window.CC_LEGAL = window.CC_LEGAL || {};
  window.CC_LEGAL.terms = {
    title: "Terms of Service",
    updated: "October 9, 2026",
    lede:
      "These Terms are the agreement between you and Bearded Seal Studios LLC for the use of " +
      "Currents and Critters, our websites, the online game, and related services. Please read them. " +
      "By using our services you accept them.",
    appliesHead: "These Terms apply to:",
    appliesTo: [
      "currentsandcritters.com",
      "play.currentsandcritters.com",
      "score.currentsandcritters.com",
      "beardedsealstudios.com",
      "The Currents and Critters online game, including guest play",
      "Snap &amp; Score",
      "Accounts, purchases, newsletters, clans, tournaments, game nights, and related services",
    ],

    sections: [
      {
        title: "Accepting These Terms",
        body:
          "<p>By visiting our websites, creating an account, playing the game as a guest or as a signed-in player, buying something, or joining our newsletter, you agree to these Terms of Service and to our Privacy Policy.</p>" +
          "<p>If you do not agree with these Terms, please do not use our services.</p>" +
          "<p>These Terms are between you and Bearded Seal Studios LLC, a Tennessee limited liability company. In these Terms, “we”, “us” and “our” mean Bearded Seal Studios LLC, and “you” means the person using our services.</p>",
      },
      {
        title: "Who May Use Currents and Critters",
        body:
          "<p>Currents and Critters and our related online services are not directed to children under 13.</p>" +
          "<p>A person under 13 should not create an account, join the newsletter, make a purchase, or submit personal information through our services without the involvement of a parent or legal guardian.</p>" +
          "<p>If you are under the age of majority where you live, you may use our services only with the involvement of a parent or legal guardian, who accepts these Terms with you and is responsible for your use of the services.</p>" +
          "<p>You may use our services only if you can form a binding agreement with us and are not barred from doing so under applicable law.</p>",
      },
      {
        title: "Your Account",
        body:
          "<p>Some features require an account. You can create one by signing in with Google.</p>" +
          "<p>You are responsible for:</p>" +
          ul([
            "Keeping access to your Google account, your email account, and your device secure",
            "Keeping any account recovery code you are given somewhere safe",
            "Everything that happens on your account",
          ]) +
          "<p>You agree not to:</p>" +
          ul([
            "Share your account with someone else, or sell, rent, or transfer it",
            "Create accounts in order to claim the same reward more than once",
            "Create an account on behalf of another person without their permission",
            "Use another player’s account",
          ]) +
          callout("Tell us promptly if you believe your account has been accessed without your permission.") +
          "<p>We may refuse, reclaim, or change a username, clan name, or display name that impersonates somebody, infringes a trademark, or breaks our Community Guidelines.</p>",
      },
      {
        title: "Your Permission to Use the Game",
        body:
          "<p>We give you a personal, limited, non-exclusive, non-transferable, revocable permission to use Currents and Critters for your own, non-commercial enjoyment, in the way the services are meant to be used.</p>" +
          "<p>Everything in the game and on our websites, including the artwork, the critters, the card designs, the rules text, the names, the logos, the music, the software, and the layout, belongs to Bearded Seal Studios LLC or to the people we license it from. We keep all rights we do not expressly give you here.</p>" +
          "<p>You may not:</p>" +
          ul([
            "Copy, sell, rent, or redistribute the game, the artwork, or the card designs",
            "Print or manufacture copies of the cards or the board for sale or distribution",
            "Make a derivative work, adaptation, or reskin of the game for public release",
            "Remove or hide a copyright notice, a credit, or a watermark",
            "Use our names or logos in a way that suggests we endorse you",
          ]) +
          "<p>Our Copyright and DMCA Policy explains how to report material you believe infringes a copyright, and how to use our artwork for things like reviews and videos.</p>",
      },
      {
        title: "Fair Play and Prohibited Conduct",
        body:
          "<p>Currents and Critters is a game people play against each other, so fair play is a rule and not a suggestion.</p>" +
          "<p>You agree not to:</p>" +
          ul([
            "Cheat, exploit a bug, or use a program, script, or modified client to gain an advantage",
            "Automate play, farm rewards, or run a bot in place of a person",
            "Submit a board, a photograph, or a result that does not reflect a game you actually played",
            "Collude with another player to change the outcome of a game, a ranking, or a reward",
            "Interfere with our servers, our network, or another player’s game",
            "Probe, scan, or attempt to get around our security, our rate limits, or our access controls",
            "Access another player’s account, private information, or hidden game information such as another player’s hand",
            "Scrape, harvest, or bulk-collect information from our services",
            "Resell access to our services, or use them to advertise without our permission",
            "Break the law, or use our services to harm somebody",
          ]) +
          "<p>Our Community Guidelines cover how to behave toward other players, including in chat, in clans, and in tournaments. They are part of these Terms.</p>",
      },
      {
        title: "Anti-Cheat, Reviews, and Automated Decisions",
        body:
          "<p>We use automated and manual checks to protect rankings, rewards, and competitive play.</p>" +
          "<p>Those checks may:</p>" +
          ul([
            "Flag a game, a submission, or an account for review",
            "Hold rewards until a review is finished",
            "Refuse a submission",
            "Reverse rewards, experience points, items, or ranking changes that were obtained unfairly or in error",
            "Limit how often rewards can be earned",
          ]) +
          "<p>Snap &amp; Score, which scores a photographed physical board, uses automated anti-cheat checks of this kind. Our Privacy Policy explains exactly what those checks look at and how long that information is kept.</p>" +
          callout("If you believe a decision about your account or a submission was wrong, email us and ask us to look at it. A person will.") +
          "<p>We aim to be proportionate, and we would rather correct a mistake than keep it.</p>",
      },
      {
        title: "Critter Coins and In-Game Items",
        body:
          "<p>Currents and Critters includes in-game balances and items, such as Critter Coins, experience points, levels, avatars, backgrounds, achievements, and unlocks.</p>" +
          callout("In-game balances and items are a licence to use a feature inside the game. They are not money, they have no cash value, and they cannot be exchanged for money, redeemed for cash, or transferred out of the game.") +
          "<p>You do not own in-game items. We give you a limited permission to use them inside Currents and Critters for as long as we offer the relevant feature and your account is in good standing.</p>" +
          "<p>We may:</p>" +
          ul([
            "Change what items cost, what they do, and how they are earned",
            "Add items, retire items, or change how a feature works",
            "Correct an error in a balance, an item, or a ranking",
            "Remove items or balances obtained through cheating, fraud, or a bug",
          ]) +
          "<p>Players may trade with each other where the game offers trading. A trade is between the two players making it. We keep a record of a completed trade so both balances are accurate and so we can investigate disputes and fraud, and we may reverse a trade that resulted from fraud, a bug, or a compromised account.</p>" +
          "<p>Our Refund, Shipping, and Preorder Policy explains what happens when you buy Critter Coins.</p>",
      },
      {
        title: "Purchases",
        body:
          "<p>Payments are processed by Stripe. We do not receive or store your complete card number.</p>" +
          "<p>When you buy something:</p>" +
          ul([
            "The price and what you are buying are shown at checkout before you pay",
            "You confirm you are allowed to use the payment method",
            "Taxes may be added where they apply",
            "Digital items, including Critter Coins, are delivered to your account as soon as payment is confirmed",
          ]) +
          "<p>If a purchase does not reach your account, email us with the receipt and we will sort it out.</p>" +
          "<p><strong>Refunds.</strong> Digital items are delivered immediately and are generally not refundable once delivered. We do honour billing errors, duplicate charges, purchases that were never delivered, and any refund the law requires. The Refund, Shipping, and Preorder Policy sets this out in full and governs purchases if it and these Terms disagree.</p>" +
          "<p><strong>The physical card game.</strong> We are not taking preorders for the physical game on this site. It is being backed through Kickstarter, and anything pledged there is governed by Kickstarter’s own terms as well as ours. Nothing on our websites is a promise of a delivery date for the physical game.</p>",
      },
      {
        title: "The Supporter Reef Wall",
        body:
          "<p>If you buy something, our checkout asks whether you would like your name shown publicly on the Supporter Reef Wall, and what name to use.</p>" +
          "<p>Your name appears there only if you ask for it. A name may be held for manual review before it appears, and we may decline or remove a name that breaks our Community Guidelines, impersonates somebody, or infringes somebody’s rights.</p>" +
          "<p>The wall shows only the name you chose and a size and tier worked out from your lifetime purchase total. You can ask us to change or remove your name at any time.</p>",
      },
      {
        title: "Things You Post, Send, and Type",
        body:
          "<p>Some parts of our services let you provide content. That includes your username, your clan name and clan details, chat messages and emotes, a Supporter Reef Wall name, bug reports, feedback, and anything else you send us.</p>" +
          "<p>You keep whatever rights you already have in what you provide. By providing it, you give us a non-exclusive, worldwide, royalty-free permission to host, store, copy, display, and use it as reasonably needed to operate, protect, moderate, and improve our services.</p>" +
          "<p>You confirm that what you provide is yours to provide, and that it does not infringe somebody else’s rights or break the law.</p>" +
          "<p>We are not obliged to monitor what players provide, but we may review, moderate, refuse, hide, or remove content, and we may act on reports from other players.</p>" +
          callout("Chat and public profile areas are not private. Do not put private information in them.") +
          "<p><strong>Feedback.</strong> If you send us an idea or a suggestion, you agree we may use it to improve the game without owing you payment, credit, or confidentiality. We are grateful for it all the same.</p>",
      },
      {
        title: "Guest Play",
        body:
          "<p>You may be able to use parts of Currents and Critters as a guest, without creating an account.</p>" +
          "<p>Guest play is provided for convenience. Guest progress, rewards, statistics, and session information may not be saved, may not transfer to an account later, and may be cleared at any time.</p>" +
          "<p>These Terms apply to guest play as well.</p>",
      },
      {
        title: "Availability and Changes to the Service",
        body:
          "<p>We are a very small studio and Currents and Critters is a service that is still growing.</p>" +
          "<p>We may, at any time and without owing you compensation:</p>" +
          ul([
            "Change, add, or remove features, rules, cards, balance, modes, or prices",
            "Take the service down for maintenance, or suffer an unplanned outage",
            "Reset a season, a leaderboard, a ranking, or a competitive ladder as announced",
            "Limit or discontinue a feature, a mode, or the service as a whole",
          ]) +
          "<p>Games in progress may be interrupted by an outage, a deploy, or a connection problem. We try to recover a game in progress where we can, and we cannot promise it.</p>" +
          "<p>If we ever discontinue the online service entirely, we will try to give reasonable notice where we can.</p>",
      },
      {
        title: "Suspension and Ending Your Account",
        body:
          "<p><strong>You</strong> may stop using our services at any time, and you may ask us to close your account by emailing " + mailto(EMAIL) + ". Our Privacy Policy explains what happens to your information when an account is closed.</p>" +
          "<p><strong>We</strong> may suspend or close an account, remove content, or withdraw access to a feature if we reasonably believe that:</p>" +
          ul([
            "These Terms or our Community Guidelines have been broken",
            "There has been cheating, fraud, abuse, or an attempt to get around our security",
            "An account is being used to harm another player or the service",
            "We are required to act by law",
          ]) +
          "<p>Where it is reasonable to do so, we will warn first, and we will aim to match the response to what happened. Serious cases, including cheating that affects other players, may be acted on immediately.</p>" +
          "<p>If your account is closed, in-game balances and items are lost and are not refunded, except where the law requires otherwise or where we closed the account in error.</p>" +
          "<p>Sections that should survive the end of this agreement do survive it, including the sections on in-game items, content you provided, disclaimers, limitation of liability, indemnity, and governing law.</p>",
      },
      {
        title: "Disclaimers",
        body:
          "<p>We care a great deal about this game, and we still have to be plain about what we can promise.</p>" +
          "<p>Our services are provided “as is” and “as available”. To the fullest extent permitted by law, we disclaim all warranties that are not expressly stated in these Terms, including implied warranties of merchantability, fitness for a particular purpose, and non-infringement.</p>" +
          "<p>We do not promise that:</p>" +
          ul([
            "The service will be uninterrupted, timely, or error-free",
            "A defect will always be corrected",
            "The service will meet your particular expectations",
            "Game progress, rankings, or in-game items can never be lost",
            "A score or a card recognised from a photograph will always be correct",
          ]) +
          "<p>Snap &amp; Score helps you score a physical game. It identifies cards and the players confirm the board by hand. It is an aid, not a referee, and the printed Rules of the Ocean are what govern a physical game.</p>" +
          "<p>Some jurisdictions do not allow certain warranty exclusions, so some of the above may not apply to you. Nothing in these Terms removes a right you have under consumer law that cannot be removed by agreement.</p>",
      },
      {
        title: "Limitation of Liability",
        body:
          "<p>To the fullest extent permitted by law:</p>" +
          ul([
            "We are not liable for indirect, incidental, special, consequential, exemplary, or punitive damages, or for lost profits, lost data, lost game progress, lost in-game items, or loss of goodwill",
            "Our total liability to you for all claims relating to our services is limited to the greater of the amount you paid us in the twelve months before the claim arose, or twenty-five United States dollars",
          ]) +
          "<p>These limits apply whatever the legal theory, and even if we were told that the loss was possible.</p>" +
          "<p>Some jurisdictions do not allow these limits, so they may not apply to you in full. Nothing in these Terms limits liability for fraud, for death or personal injury caused by negligence, or for anything else that cannot be limited by law.</p>",
      },
      {
        title: "Indemnity",
        body:
          "<p>You agree to indemnify and hold harmless Bearded Seal Studios LLC and the people who work with it from claims, damages, losses, and reasonable legal costs arising out of:</p>" +
          ul([
            "Your use of our services in a way that breaks these Terms or the law",
            "Content you provided through our services",
            "Your infringement of somebody else’s rights",
          ]) +
          "<p>We may take over the defence of any such claim, and you agree not to settle it in a way that binds us without our written agreement.</p>",
      },
      {
        title: "Governing Law and Disputes",
        body:
          "<p>These Terms are governed by the laws of the State of Tennessee, United States, without regard to its conflict-of-laws rules.</p>" +
          "<p>You and we agree that any dispute arising out of these Terms or our services will be brought exclusively in the state or federal courts located in Davidson County, Tennessee, and you and we consent to the personal jurisdiction of those courts.</p>" +
          "<p>Nothing here prevents either of us from seeking an injunction or similar urgent relief where it is appropriate, and nothing here removes a right you have under the consumer law of the place where you live, including any right to bring a claim in your local courts where the law gives you that right.</p>" +
          callout("Before anything formal, please just email us at " + mailto(EMAIL) + ". Almost everything is quicker to fix that way, and we would rather fix it.") +
          "<p>If a part of these Terms is found unenforceable, the rest stays in force. If we do not enforce a part of these Terms straight away, we have not given it up.</p>",
      },
      {
        title: "Changes to These Terms",
        body:
          "<p>We may update these Terms as Currents and Critters and our services change.</p>" +
          "<p>When we update them, we will change the “Last updated” date at the top of this page.</p>" +
          "<p>If we make a significant change, we will give additional notice through the website, the online game, your account, or email where that is appropriate or legally required, before the change takes effect where we reasonably can.</p>" +
          "<p>The updated Terms apply from the date they are posted unless a different effective date is stated. If you keep using our services after a change takes effect, you accept the updated Terms. If you do not accept them, please stop using the services and you may ask us to close your account.</p>",
      },
      {
        title: "The Whole Agreement, and Contacting Us",
        body:
          "<p>These Terms, together with our Privacy Policy, Community Guidelines, Cookie Notice, Refund, Shipping, and Preorder Policy, and Copyright and DMCA Policy, are the whole agreement between you and us about our services.</p>" +
          "<p>You may not transfer your rights under these Terms. We may transfer ours as part of a merger, sale, or reorganisation of the business, and your rights under these Terms travel with it.</p>" +
          "<p>Questions about these Terms may be sent to:</p>" +
          K.addressBlock(K.ADDRESS_LINES) +
          "<p><strong>Email:</strong> " + mailto(EMAIL) + "</p>",
      },
    ],
  };
})();
