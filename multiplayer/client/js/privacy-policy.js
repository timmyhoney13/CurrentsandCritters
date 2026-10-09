/* ================================================================
 * Currents and Critters, the Privacy Policy, one shared source.
 *
 * The policy text below is the legal document, reproduced WORD FOR WORD.
 * Nothing here is paraphrased, trimmed or "improved", only the layout
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

  // ── CONFIRMED BY THE OWNER, 2026-10-09 ──────────────────────────────────
  // Written from what the code actually does, not from a template.
  //   • Contact email and mailing address: confirmed. The same address is
  //     already published by newsletter_email.BUSINESS_ADDRESS_LINES (every
  //     marketing email) and by client/unsubscribe.html, so the three must
  //     be changed together if it ever changes.
  //   • Snap & Score review evidence: 90 days, confirmed. Rewards already
  //     earned are explicitly NOT affected, which is why the Snap & Score
  //     section and Data Retention both say so.
  //
  // STILL WORTH A LAWYER'S EYE, and one live trap:
  //   • The 90 days is the DEFAULT in snap_score.py (image_retention_days)
  //     and is admin-editable through the Firestore doc
  //     meta/snap_score_config. Editing that doc silently makes the
  //     Snap & Score and Data Retention sections wrong. Change both here if
  //     it is ever retuned.
  //   • 12h unfinalized / 24h finalized score sessions (SESSION_TTL_SEC,
  //     FINALIZED_KEEP_SEC in snap_score.py).
  //   • "the last 50" games and "about 800 days" of play dates, from the
  //     account stats shape described in analytics_server.py.
  //   • The Discord reward record is kept "for as long as we operate the
  //     reward". discord_server.py never deletes it, so that is accurate
  //     today, but it is a retention commitment worth a deliberate decision.
  // ────────────────────────────────────────────────────────────────────────

  var UPDATED = "October 9, 2026";
  var EMAIL   = "timothy.honey@beardedsealstudios.com";

  // ── The document ────────────────────────────────────────────────
  // One entry per numbered section. `body` is the section's HTML; the number,
  // the heading, its id and the anchor link are all generated below from the
  // entry's POSITION in this array, so the TOC, the headings and the deep
  // links are always the same list and a section cannot carry a stale number.
  // Reordering or inserting a section renumbers the document and changes the
  // #pp-sN deep links after the insertion point.
  var SECTIONS = [
    {
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
          "Information from a linked Discord account, if you choose to link one",
          "Trades, referrals, and other activity between your account and another player’s account",
          "Anti-cheat information about a photographed physical game board, when you use Snap &amp; Score to earn rewards",
          "Messages, questions, bug reports, feedback, or other information you send to us",
          "Device, browser, IP address, and basic technical information",
          "Cookies and similar information needed to operate and protect our services",
        ]) +
        "<p>You may be able to use certain parts of Currents and Critters as a guest without creating an account.</p>" +
        "<p>We only collect information that is reasonably needed to provide, operate, protect, and improve our services.</p>",
    },
    {
      title: "What We Store, in Plain Terms",
      body:
        "<p>This section is a plain summary of what we actually keep, and for how long. The sections that follow describe each area in more detail.</p>" +
        "<p><strong>On your account, for as long as your account exists:</strong></p>" +
        ul([
          "Who you are to the game: your username, your avatar, and the email address and Google account identifier you signed in with",
          "What you have earned: your level, experience points, Critter Coins, achievements, unlocks, and competitive rank",
          "How you have played: your game statistics, your wins and losses, your most recently finished games, and the dates you played",
          "Who you played with: your clan, your tournaments, your completed trades, and the referral code you joined with, if you used one",
        ]) +
        "<p><strong>Only while a game is running, and then deleted:</strong></p>" +
        ul([
          "The live state of the game you are in, including the hands at the table",
          "That game’s most recent table chat",
        ]) +
        "<p><strong>For a limited review period, and then deleted:</strong></p>" +
        ul([
          "Anti-cheat information about a photographed physical board, described in the Snap &amp; Score section below",
        ]) +
        "<p><strong>Kept with our business records:</strong></p>" +
        ul([
          "Purchases and transactions, for accounting and tax purposes",
          "Your newsletter subscription and unsubscribe status, so that an unsubscribe can be honored",
          "Messages you send us, and reports about cheating or abuse",
        ]) +
        "<p><strong>What we never store:</strong></p>" +
        ul([
          "Your Google password or your Discord password",
          "Your complete credit card or debit card number",
          "The photograph of your physical board, which is read on your own device and never uploaded",
        ]) +
        callout("Your email address and your Google account identifier are never shown to other players."),
    },
    {
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
          "The kind of device you are playing on, such as a computer or a phone",
          "The name you chose for the Supporter Reef Wall, if you bought something and asked to be shown there",
          "Other information you intentionally place on a public game profile",
        ]) +
        callout("Do not place private information in your username, clan name, profile, messages, or other public areas of the game."),
    },
    {
      title: "Google Sign-In",
      body:
        "<p>Currents and Critters may allow you to sign in using your Google account.</p>" +
        "<p>Depending on the permissions you approve, Google may provide us with basic account information, such as:</p>" +
        ul([
          "Your name",
          "Your email address",
          "Your unique Google account identifier",
          "Your profile picture",
        ]) +
        "<p>We use this information to:</p>" +
        ul([
          "Create and manage your Currents and Critters account",
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
      title: "Discord Account Linking",
      body:
        "<p>Currents and Critters may allow you to link your Discord account to claim a one-time reward for being a member of our Discord server.</p>" +
        "<p>Linking your Discord account is optional. You do not need a Discord account to create an account or to play.</p>" +
        "<p>If you choose to link it, you authorize Discord to confirm your membership to us. Depending on the permissions you approve, we may receive:</p>" +
        ul([
          "Your unique Discord account identifier",
          "Your Discord username",
          "Whether your Discord account is a member of our Discord server",
        ]) +
        "<p>We use this information to:</p>" +
        ul([
          "Confirm that you are a member of our Discord server",
          "Award the reward one time",
          "Prevent the same Discord account or the same game account from claiming the reward more than once",
        ]) +
        "<p>We keep a record connecting that reward to both your game account and your Discord account identifier. That record exists so the reward cannot be claimed twice, and we keep it for as long as we operate the reward.</p>" +
        callout("We do not receive or store your Discord password, your Discord messages, or your activity in other Discord servers.") +
        "<p>Your Discord account identifier is not publicly displayed to other players.</p>" +
        "<p>Discord is a separate company with its own privacy policy and practices. You should review Discord’s privacy information to understand how Discord handles information associated with your Discord account.</p>",
    },
    {
      title: "Game Accounts and Activity",
      body:
        "<p>When you create an account or play Currents and Critters, we may collect and save information such as:</p>" +
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
      title: "Player Chat and Emotes",
      body:
        "<p>Currents and Critters includes chat and emotes at the game table so players can talk during a game.</p>" +
        "<p>What you send in chat is delivered to the other players at your table and to anyone watching that game as a spectator.</p>" +
        "<p>While a game is in progress, our game server holds a limited number of that game’s most recent messages, currently up to 200, so a player who loses their connection and rejoins can still see the recent conversation. The server keeps a working copy of the game, including those recent messages, while the game is live, and deletes that copy when the game is over.</p>" +
        "<p>Table chat is not saved to your account, is not added to your match history, and is not used for advertising.</p>" +
        "<p>If you send us a report about another player, the information you send us is handled as described in \u201CMessages and Communication\u201D below.</p>" +
        callout("Chat is a public part of the game. Do not put private information in chat."),
    },
    {
      title: "Trading, Critter Coins, and Referrals",
      body:
        "<p>Currents and Critters includes in-game balances, such as Critter Coins and experience points, and lets players trade with each other.</p>" +
        "<p>In-game balances are virtual items inside the game. They have no cash value and cannot be exchanged for money.</p>" +
        "<p>When you complete a trade with another player, we store a record of that trade. That record may include:</p>" +
        ul([
          "The accounts involved in the trade",
          "What each side offered and received",
          "The date and time the trade was completed",
        ]) +
        "<p>We keep trade records so both players have an accurate balance, so a trade cannot be completed twice, and so we can investigate disputes, fraud, and abuse.</p>" +
        "<p>The other player in a trade can see your username, your avatar, and what you offer in that trade.</p>" +
        "<p>If you enter another player’s referral code when you create an account, we store a record connecting your account to the account whose code you entered. That record exists so the referral reward is paid once and cannot be farmed with extra accounts. A player whose code is used may be able to see how many players have joined using it.</p>",
    },
    {
      title: "Snap &amp; Score and Board Photographs",
      body:
        "<p>Snap &amp; Score, at score.currentsandcritters.com, lets you photograph a finished physical game board so that the board can be scored.</p>" +
        "<p><strong>The photograph is read on your own device.</strong> Your browser recognizes the cards against a card library it downloads, and the photograph itself is not uploaded to us.</p>" +
        "<p>If you score a board on your own, without an account and without rewards, nothing from that photograph is sent to us or stored by us.</p>" +
        "<p>If you use a multiplayer score session that can award experience points, achievements, or unlocks, the app sends limited anti-cheat information about the photograph in place of the photograph. That information may include:</p>" +
        ul([
          "A cryptographic hash of the captured image",
          "A short image fingerprint used to recognize near-identical photographs",
          "A small, low-resolution review thumbnail of the board",
        ]) +
        "<p>We use that information only to:</p>" +
        ul([
          "Detect one board being submitted for two different players",
          "Detect the same or a nearly identical board being submitted again in a later game",
          "Review a flagged submission before rewards are granted",
        ]) +
        "<p>We keep the review thumbnail and the image fingerprints for a limited review period, currently 90 days. A score session that is never finished expires after about 12 hours, and a finished session stays readable for about 24 hours so the players in it can see the result.</p>" +
        callout("Deleting that review information does not take away anything you earned. Experience points, achievements, unlocks, and other rewards granted for a scored physical game are added to your online account and stay with you, including after the review thumbnail and image fingerprints have been deleted.") +
        "<p>Rewards in a score session are decided automatically, and an automated check may flag a submission for review, hold its rewards, or refuse the submission. If you believe a submission was wrongly held or refused, you may contact us at the address in the final section and ask us to look at it.</p>" +
        "<p>Other players in your score session may see your username, your avatar, the board you submitted, and your score.</p>",
    },
    {
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
        "<p>Providing an email address for a receipt, payment, or purchase does not automatically add you to the Currents and Critters newsletter.</p>" +
        "<p><strong>The Supporter Reef Wall.</strong> Our checkout asks whether you would like your name shown publicly on the Supporter Reef Wall, and what name to use. That wall is public.</p>" +
        "<p>Your name appears there only if you ask for it. If you decline, or you leave that question blank, your purchase is recorded without a public name. A name may be held for manual review before it appears.</p>" +
        "<p>The wall shows only the name you chose and a size and tier worked out from your lifetime purchase total, so a name on the wall indicates a spending range rather than an exact amount. The wall never shows your email address, your payment details, or which item you bought.</p>",
    },
    {
      title: "Newsletter and Marketing Emails",
      body:
        "<p>You will only be added to the Currents and Critters email list when you intentionally provide your email address for that purpose.</p>" +
        "<p>For example, during checkout you may enter your email address in an optional field labeled:</p>" +
        "<blockquote>“Enter your email to get updates”</blockquote>" +
        "<p>Leaving that field blank does not subscribe you. Completing a purchase does not subscribe you. Providing an email address for a receipt or order confirmation does not subscribe you.</p>" +
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
          "The date and time you first subscribed",
          "The date and time you most recently subscribed or resubscribed",
          "How you subscribed",
          "A reference to the checkout you subscribed through, when applicable",
          "The date and time you unsubscribed, when applicable",
          "Whether a welcome email was successfully sent to you",
          "Whether a particular newsletter was sent to you, and whether it was delivered, failed, or was skipped",
          "Information needed to process and honor your unsubscribe request",
        ]) +
        "<p>We do not include tracking pixels in our newsletters, and we do not record whether you opened an email or which links you clicked.</p>" +
        "<p>Newsletters are sent to each subscriber individually. Your email address is never shown to other subscribers and is never placed in a shared To, CC, or BCC field.</p>" +
        "<p>Every marketing email will include a working unsubscribe option, along with our business name and mailing address and a link to this Privacy Policy.</p>" +
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
      title: "How We Share Information",
      body:
        callout("We do not sell your personal information.") +
        "<p>We may share limited information with service providers that help us operate our business. These may include:</p>" +
        ul([
          "Stripe for payment processing and for collecting an optional newsletter signup at checkout",
          "Google for account sign-in, Google Workspace, and the Gmail service used to send our newsletters",
          "Google Firebase and Firestore for accounts, game data, and newsletter subscriber records",
          "Render for game server and website hosting",
          "Vercel for hosting and delivering our websites",
          "Discord, when you choose to link your Discord account to claim a reward",
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
        "<p>We do not use third-party advertising cookies, and we do not use third-party analytics services that track you across other websites. The performance information we review is produced by our own server from the accounts and games it already holds.</p>" +
        "<p>You may be able to control cookies through your browser settings.</p>" +
        "<p>Disabling cookies that are necessary to operate the service may prevent account, game, checkout, or security features from working correctly.</p>" +
        "<p>If we begin using optional advertising or analytics cookies that require additional notice or consent, we may provide additional information or controls as required.</p>",
    },
    {
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
        "<p>Some examples of how long particular information is kept:</p>" +
        ul([
          "Your account and its game record are kept while your account exists",
          "Your account keeps a list of your most recently finished games, currently the last 50",
          "Your account keeps the dates you played, currently going back about 800 days, so streaks and challenges can be calculated",
          "A Snap &amp; Score review thumbnail and its image fingerprints are kept for a limited review period, currently 90 days",
          "An unfinished Snap &amp; Score session expires after about 12 hours, and a finished one stays readable for about 24 hours",
          "Table chat is held only while a game is live, and is deleted with the game’s working copy when the game ends",
          "Purchase and transaction records are kept as long as needed for accounting, tax, and business records",
        ]) +
        "<p>These periods describe how the service is configured today and may change as the service changes.</p>" +
        "<p>Rewards you have already earned are not affected by any of these periods. Your experience points, achievements, unlocks, Critter Coins, and game record stay on your account after the information we used to review or operate a game has been deleted.</p>" +
        "<p>When information is no longer reasonably needed, we may delete it, remove identifying details, or securely retain it when required for legal, security, tax, or recordkeeping purposes.</p>" +
        "<p>Unsubscribing from the newsletter does not always mean that the subscriber record will be immediately deleted. We may keep the email address and unsubscribe status so we do not accidentally send additional marketing emails.</p>" +
        "<p>Closing a game account may not result in the immediate deletion of every record. We may retain limited information when reasonably necessary for security, fraud prevention, dispute resolution, financial recordkeeping, or legal compliance.</p>",
    },
    {
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
        "<p>You are responsible for protecting access to your Google account, email account, device, and Currents and Critters account.</p>" +
        "<p>Contact us if you believe your Currents and Critters account has been accessed without permission.</p>",
    },
    {
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
      title: "Children’s Privacy",
      body:
        "<p>Currents and Critters and our related online services are not directed to children under 13.</p>" +
        "<p>We do not knowingly collect personal information online from children under 13 without any permission or consent required by applicable law.</p>" +
        "<p>A person under 13 should not create an account, join the newsletter, make a purchase, or submit personal information through our services without the involvement of a parent or legal guardian.</p>" +
        "<p>If we learn that we collected personal information from a child under 13 without any required permission, we will take reasonable steps to delete it.</p>" +
        "<p>A parent or legal guardian who believes a child under 13 provided personal information may contact us at:</p>" +
        "<p>" + mailto(EMAIL) + "</p>",
    },
    {
      title: "Third-Party Services and Links",
      body:
        "<p>Our services may use or contain links to third-party websites and services, including Google and Stripe.</p>" +
        "<p>These companies have their own privacy policies and practices.</p>" +
        "<p>We are not responsible for the content, privacy, or security practices of third-party services that we do not control.</p>" +
        "<p>You should review the privacy policies of third-party services before providing them with personal information.</p>",
    },
    {
      title: "International Users",
      body:
        "<p>Bearded Seal Studios LLC is based in the United States.</p>" +
        "<p>If you access our services from another country, your information may be processed and stored in the United States or another country where our service providers operate.</p>" +
        "<p>Privacy and data protection laws in those locations may be different from the laws where you live.</p>" +
        "<p>Depending on your location and applicable law, you may have additional privacy rights. You may contact us to ask about your information or submit a privacy request.</p>",
    },
    {
      title: "Changes to This Privacy Policy",
      body:
        "<p>We may update this Privacy Policy as Currents and Critters, Bearded Seal Studios LLC, and our services change.</p>" +
        "<p>When we update the policy, we will change the “Last updated” date at the top of this page.</p>" +
        "<p>If we make a significant change, we may provide additional notice through the website, online game, account, or email when appropriate or legally required.</p>" +
        "<p>The updated Privacy Policy will apply from the date it is posted unless a different effective date is stated.</p>",
    },
    {
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
        "Currents and Critters",
        "beardedsealstudios.com",
        "currentsandcritters.com",
        "play.currentsandcritters.com",
        "score.currentsandcritters.com",
        "The Currents and Critters online game",
        "Snap &amp; Score",
        "Currents and Critters accounts, newsletters, purchases, game nights, events, clans, competitions, and related services",
      ]) +
    "</div>";

  var BODY = SECTIONS.map(function (s, i) {
    var n = i + 1;
    return '<section class="pp-sec" id="' + slug(n) + '">' +
      '<h3 class="pp-h"><span class="pp-num">' + n + "</span>" + s.title + "</h3>" +
      '<div class="pp-body">' + s.body + "</div>" +
    "</section>";
  }).join("");

  // The whole policy, minus any page chrome. Drop it inside an element
  // carrying .pp-light (website) or .pp-dark (in game) and it is styled.
  window.CC_PRIVACY_HTML = '<div class="pp-doc">' + INTRO + BODY + "</div>";

  // The same sections, in the same order, for building a table of contents
  // that can never drift away from the headings above.
  window.CC_PRIVACY_SECTIONS = SECTIONS.map(function (s, i) {
    return { n: i + 1, id: slug(i + 1), title: s.title };
  });

  window.CC_PRIVACY_UPDATED = UPDATED;
  window.CC_PRIVACY_EMAIL   = EMAIL;
})();
