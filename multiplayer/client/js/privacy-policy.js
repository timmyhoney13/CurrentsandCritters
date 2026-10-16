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

  // ── CONFIRMED BY THE OWNER, 2026-10-09; RE-AUDITED 2026-10-12 ───────────
  // Written from what the code actually does, not from a template.
  //   • Contact email: confirmed.
  //   • Snap & Score review evidence: 90 days, confirmed. Rewards already
  //     earned are explicitly NOT affected, which is why the Snap & Score
  //     section and Data Retention both say so.
  //
  // THE STREET ADDRESS IS DELIBERATELY NOT HERE (owner's instruction,
  // 2026-10-12). Nothing requires a street address in a privacy policy. The
  // one rule that does require a physical postal address is CAN-SPAM,
  // 15 U.S.C. 7704(a)(5)(A)(iii), and it applies to COMMERCIAL EMAIL, not to
  // this page, so the address still ships in
  // newsletter_email.BUSINESS_ADDRESS_LINES and in client/unsubscribe.html.
  // Those two are where it is legally needed and the only places it belongs.
  // Do not "restore" it here.
  //
  // THE 90 DAYS IS NOW ENFORCED, AND CAPPED. It used to be a default that
  // nothing acted on: snap_score.py read image_retention_days once, wrote it
  // into the game document as metadata, and never deleted anything, so this
  // page published a retention period that did not exist. Since 2026-10-12:
  //   • snap_score.purge_expired_snap_evidence() actually does the deleting,
  //     run daily by the "snap-retention" thread in multiplayer_server.
  //   • snap_score.IMAGE_RETENTION_DAYS_MAX clamps the admin-editable
  //     Firestore doc meta/snap_score_config, so an admin can make retention
  //     SHORTER but can never make this page false by making it longer.
  //   • test_snap_retention.py pins both, and pins that no reward moves.
  // Raising that ceiling therefore has to happen in the same diff as the
  // sentence below that it would contradict. Keep it that way.
  //
  // STILL OPEN:
  //   • CalOPPA (Cal. Bus. & Prof. Code 22575) wants a conspicuous policy
  //     link on the first significant page. play.currentsandcritters.com's
  //     sign-in screen has none; the owner was told, and chose to leave the
  //     sign-in screen alone on 2026-10-12. The policy is reachable in game
  //     at Settings -> Legal and from the website footer.
  //   • There is NO self-serve account deletion anywhere in the codebase.
  //     "Account Deletion and Closing Your Account" below therefore promises
  //     only an emailed request, which is what actually exists. Do not
  //     upgrade that wording without building the button.
  //   • "the last 50" games and "about 800 days" of play dates, from the
  //     account stats shape described in analytics_server.py.
  //   • The Discord reward record is kept "for as long as we operate the
  //     reward". discord_server.py never deletes it, so that is accurate
  //     today, but it is a retention commitment worth a deliberate decision.
  //   • Clan chat's 14-day prune is PROBABILISTIC (clan_server.py fires it on
  //     roughly one send in twenty, over a 400-document scan), so the section
  //     below says "usually within about two weeks" rather than promising a
  //     deadline the code does not guarantee.
  // ────────────────────────────────────────────────────────────────────────

  var UPDATED = "October 12, 2026";
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
          "A record that you accepted our Terms of Service: which version of them, and when",
          "A record that you told us you are 13 or older. We ask for a date of birth to work out your age and then discard it, so the date itself is not stored",
          "A recovery email address, if you choose to add one to a username-and-password account",
          "Your selected avatar, clan, achievements, rankings, and leaderboard information",
          "Game progress, statistics, match history, rewards, and activity",
          "Your friends list, friend requests, and friend code",
          "Newsletter subscription status",
          "Purchase and transaction information",
          "Information from a linked Discord account, if you choose to link one",
          "Trades, referrals, and other activity between your account and another player’s account",
          "Clan chat messages you send, and table chat while a game is running",
          "Anti-cheat information about a photographed physical game board, when you use Snap &amp; Score to earn rewards",
          "Messages, questions, bug reports, feedback, or other information you send to us",
          "Device and browser information, and your IP address",
          "Browser storage needed to operate and protect our services",
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
          "Who you are to the game: your username, your avatar, and the email address and Google account identifier you signed in with, or the username and recovery email of a username-and-password account",
          "What you have earned: your level, experience points, Critter Coins, achievements, unlocks, and competitive rank",
          "How you have played: your game statistics, your wins and losses, your most recently finished games, and the dates you played",
          "Who you played with: your clan, your friends list, your tournaments, your completed trades, and the referral code you joined with, if you used one",
        ]) +
        "<p><strong>Only while a game is running, and then deleted:</strong></p>" +
        ul([
          "The live state of the game you are in, including the hands at the table",
          "That game’s most recent table chat",
        ]) +
        "<p><strong>For a limited period, and then deleted:</strong></p>" +
        ul([
          "Anti-cheat information about a photographed physical board, including a small review thumbnail, described in the Snap &amp; Score section below",
          "Clan chat messages, which are removed as the conversation ages",
        ]) +
        "<p><strong>Kept with our business records:</strong></p>" +
        ul([
          "Purchases and transactions, for accounting and tax purposes",
          "Your newsletter subscription and unsubscribe status, so that an unsubscribe can be honored",
          "Messages you send us, and reports about cheating or abuse",
        ]) +
        "<p><strong>What we never store:</strong></p>" +
        ul([
          "Your password. A Google password never reaches us at all, and the password on a username-and-password account is held by Google Firebase Authentication, not by us",
          "Your Discord password",
          "Your complete credit card or debit card number",
          "Your full-resolution board photograph. It is read on your own device and is never uploaded. When a score session can award rewards, a small low-resolution review thumbnail of the board is sent instead, and it is described and given a deletion period in the Snap &amp; Score section below",
        ]) +
        "<p><strong>What we receive but do not keep:</strong></p>" +
        ul([
          "Your IP address. Our servers see it on every request, as any web server must, and use it in memory to rate-limit abuse. It is not written into your account or into our database",
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
      title: "Username-and-Password Accounts",
      body:
        "<p>You can also create a Currents and Critters account with a username and a password instead of signing in with Google.</p>" +
        "<p><strong>We never see your password.</strong> Account sign-in is handled by Google Firebase Authentication. Your password is sent to Firebase, which stores it in a protected form, and Bearded Seal Studios LLC does not receive it or keep a copy.</p>" +
        "<p>Because Firebase identifies accounts by email address rather than by username, a username-and-password account is registered with an internal address built from your username, at a domain that cannot receive mail. That internal address exists only so your username can be unique. It is not a real mailbox and we do not send anything to it.</p>" +
        "<p><strong>A recovery email is optional.</strong> You may add a real email address to a username-and-password account so that a forgotten password can be reset. If you do:</p>" +
        ul([
          "We store that address, lower-cased, beside your account",
          "We store whether you have confirmed it, and when you last set it",
          "An address is not linked to your account until you click the confirmation link we send to it",
          "We use it only to send you account recovery and account security messages, never marketing, unless you separately subscribe to the newsletter",
        ]) +
        "<p>One confirmed address belongs to one account, and one account has one confirmed address.</p>" +
        "<p><strong>Recovery codes.</strong> Every username-and-password account is also issued a recovery code you can write down, so you can get back in without an email address at all. We store only a keyed, one-way representation of that code, never the code itself, so the stored value cannot be turned back into a working code. A code is single-use, and redeeming one issues a fresh one.</p>" +
        callout("Your recovery email address is not publicly displayed to other players."),
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
        "<p>Currents and Critters has two different kinds of chat, and they are kept in two different ways. There are no private one-to-one messages between players anywhere in the game.</p>" +
        "<p><strong>Table chat, during a game.</strong> Chat and emotes at the game table let players talk during a game. What you send is delivered to the other players at your table and to anyone watching that game as a spectator.</p>" +
        "<p>While a game is in progress, our game server holds a limited number of that game’s most recent messages, currently up to 200, so a player who loses their connection and rejoins can still see the recent conversation. The server keeps a working copy of the game, including those recent messages, while the game is live, and deletes that copy when the game is over.</p>" +
        "<p>Table chat is not saved to your account, is not added to your match history, and is not used for advertising.</p>" +
        "<p><strong>Clan chat, which is saved.</strong> If you join a clan, clan chat works differently, because it has to still be there when you come back tomorrow. A clan chat message is stored in our database with:</p>" +
        ul([
          "The message text",
          "Your account identifier and the name your clan sees",
          "The date and time you sent it",
          "Whether it was an ordinary message, an announcement, or a game or tournament invitation",
        ]) +
        "<p>Clan chat is visible to every member of that clan, including members who join after you sent it, for as long as the message is kept.</p>" +
        "<p>Older clan messages are removed as the conversation moves on, usually within about two weeks. That clean-up runs as the clan keeps chatting rather than on a fixed schedule, so a quiet clan may keep its older messages somewhat longer.</p>" +
        "<p><strong>Your in-game inbox.</strong> Your account also has a message inbox, but it holds notices the game generates, such as a clan invitation or the result of a trade, rather than free text written by another player. Those notices stay until your account is closed.</p>" +
        "<p>If you send us a report about another player, the information you send us is handled as described in \u201CMessages and Communication\u201D below.</p>" +
        callout("Chat is a public part of the game, and clan chat is saved. Do not put private information in chat."),
    },
    {
      title: "Friends, Trading, Critter Coins, and Referrals",
      body:
        "<p><strong>Friends and friend codes.</strong> Every account has a friend code, which is how another player adds you without needing your email address. You may also choose a custom friend code. We store:</p>" +
        ul([
          "Your friend code, and your custom friend code if you set one",
          "Your friends list",
          "Friend requests you have sent or received, and whether they were accepted",
        ]) +
        "<p>A friend code can be looked up by any player who has it, together with the username and avatar it belongs to. That is what makes it usable as a way for a friend to find you, so treat your friend code as shareable rather than secret. Your friends list is kept while your account exists.</p>" +
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
        "<p><strong>The photograph is read on your own device.</strong> Your browser recognizes the cards against a card library it downloads. Your full-resolution photograph is never uploaded to us.</p>" +
        "<p>If you score a board on your own, without an account and without rewards, nothing from that photograph is sent to us or stored by us.</p>" +
        "<p>If you use a multiplayer score session that can award experience points, achievements, or unlocks, the app sends limited anti-cheat information about the photograph in place of the photograph itself. That information is:</p>" +
        ul([
          "A cryptographic hash of the captured image, which is a one-way fingerprint that cannot be turned back into a picture",
          "A short image fingerprint used to recognize near-identical photographs",
          "A small, low-resolution review thumbnail of the board, no more than 320 pixels on its longest side",
        ]) +
        "<p>We use that information only to:</p>" +
        ul([
          "Detect one board being submitted for two different players",
          "Detect the same or a nearly identical board being submitted again in a later game",
          "Review a flagged submission before rewards are granted",
        ]) +
        "<p><strong>The 90-day rule.</strong> We keep that review information for 90 days after the game is completed, and then delete it. After 90 days we remove:</p>" +
        ul([
          "The review thumbnail of your board",
          "The image fingerprint used for near-identical matching",
          "The record of which account and which game a submitted image belonged to",
        ]) +
        "<p>What remains after 90 days is the one-way cryptographic hash of the image, with your account and the game no longer attached to it. We keep that single value for the long term for one reason: it is what makes it impossible to submit the same board photograph for rewards a second time, however long somebody waits. A one-way hash does not contain the picture and cannot be used to recreate it or to see what was photographed.</p>" +
        "<p>Separately, a score session that is never finished expires after about 12 hours, and a finished session stays readable for about 24 hours so the players in it can see the result. The review thumbnail is removed with the session at that point as well.</p>" +
        callout("Deleting that review information does not take away anything you earned. Experience points, achievements, unlocks, Critter Coins, and your game record are stored on your account separately from any of the review information above, and they stay with you after the review thumbnail and the image fingerprints have been deleted. The only thing the 90 days removes is our ability to look back at the board.") +
        "<p>Your record of the game itself is not deleted at 90 days. The final standings, the scores, the placements, the cards you confirmed, and the rewards granted stay with your account under the ordinary rules in “Data Retention” and “Account Deletion and Closing Your Account” below.</p>" +
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
      title: "Moderation, Reports, and Enforcement",
      body:
        "<p>Currents and Critters has community features, so it also has moderation.</p>" +
        "<p><strong>If you report somebody.</strong> You can report a clan name, a clan chat message, or another player. When you do, we store a record for review that includes what you reported, the account that reported it, the account or content reported, and when. We use it to decide whether our rules were broken.</p>" +
        "<p><strong>Automatic checks.</strong> Some things are checked automatically rather than by a person. Usernames, clan names, and chat messages pass through a word filter, and unusual patterns in rewards or Snap &amp; Score submissions may raise a flag for review. A flag is a record of suspected activity, not a finding, and it may be wrong.</p>" +
        "<p><strong>What we may do.</strong> Depending on what we find, we may hide or remove content, mute an account in a clan, hold or refuse rewards, remove a name, or restrict or close an account.</p>" +
        "<p><strong>Administrative records.</strong> When an administrator corrects a balance, grants or removes something, or acts on a report, we keep a record of what was changed and when. Those records exist so an action can be explained or reversed and so mistakes can be found.</p>" +
        "<p>We may keep moderation and enforcement records after the content involved has been removed, and after an account has been closed, where that is reasonably necessary to enforce our rules, prevent somebody returning to repeat the same conduct, resolve a dispute, or meet a legal requirement.</p>" +
        "<p>If you believe a decision about your account or a submission was wrong, you may contact us and ask us to look at it again.</p>",
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
          "Google Firebase Authentication for sign-in and for storing the password on a username-and-password account",
          "Google Firebase and Firestore for accounts, game data, and newsletter subscriber records",
          "Google Fonts, which serves the typefaces our pages use",
          "Google Forms, if you send us a bug report or an idea through one of our in-game links",
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
        callout("Our servers do not set any cookies, and we use no advertising or cross-site tracking technology of any kind.") +
        "<p>There is no Google Analytics, no Tag Manager, no advertising pixel, and no third-party analytics service that follows you to other websites. There is nothing here for a cookie banner to ask you about.</p>" +
        "<p>What we do use is <strong>browser storage that stays on your own device</strong>, which the browser keeps for our site and does not send to other websites. It holds things like:</p>" +
        ul([
          "The sign-in session that keeps you signed in, which is managed by Google Firebase Authentication",
          "Your settings and preferences, such as whether music is on",
          "A temporary identifier for guest play, so a guest can keep their seat",
          "Short-lived information needed to return you to the right place after a checkout",
        ]) +
        "<p>You can clear this storage through your browser settings at any time. Clearing it signs you out and forgets your preferences, and blocking it may prevent account, game, checkout, or security features from working correctly.</p>" +
        "<p><strong>Technical information our servers see.</strong> Like any web server, ours receives your IP address, the kind of browser and device you are using, and which page you asked for. We use your IP address in memory to limit abuse, such as somebody hammering a form, and we do not write it into your account or store it in our database.</p>" +
        "<p><strong>Third parties your browser contacts.</strong> Some parts of our pages load directly from other companies, which means your browser contacts them and they receive your IP address as a result. These are:</p>" +
        ul([
          "Google, for Firebase Authentication, Google Sign-In, and the web fonts our pages use",
          "Stripe, when you reach a checkout page",
        ]) +
        "<p>Those companies may set their own cookies on their own domains and handle that information under their own privacy policies. If you report a bug or send an idea through one of our in-game links, that form is a Google Form and what you type goes to Google as well.</p>" +
        "<p>The performance information we review about the game is produced by our own server from the accounts and games it already holds, as aggregated totals. It is not collected by tracking you.</p>" +
        "<p>If we ever begin using optional advertising or analytics technology that requires additional notice or consent, we will update this policy and provide any notice or controls required.</p>",
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
        "<p>How long particular information is kept:</p>" +
        ul([
          "Your account and its game record are kept while your account exists",
          "Your account keeps a list of your most recently finished games, currently the last 50",
          "Your account keeps the dates you played, currently going back about 800 days, so streaks and challenges can be calculated",
          "<strong>A Snap &amp; Score review thumbnail, its near-duplicate image fingerprint, and the record of which account and game an image came from are deleted 90 days after the game is completed.</strong> The one-way cryptographic hash of the image is kept after that, with your account no longer attached to it, so the same photograph cannot be submitted for rewards twice",
          "An unfinished Snap &amp; Score session expires after about 12 hours, and a finished one stays readable for about 24 hours",
          "Table chat is held only while a game is live, and is deleted with the game’s working copy when the game ends",
          "Clan chat messages are removed as the conversation moves on, usually within about two weeks",
          "Your friends list, friend requests, and in-game notices are kept while your account exists",
          "A record connecting a Discord reward to your game account and your Discord account identifier is kept for as long as we operate that reward, so it cannot be claimed twice",
          "A newsletter subscriber record, including the fact that you unsubscribed, is kept so an unsubscribe can be honored",
          "Purchase and transaction records are kept as long as needed for accounting, tax, and business records",
          "Reports about cheating or abuse, and records of action we took, are kept while needed to enforce our rules",
        ]) +
        "<p>These periods describe how the service is configured today and may change as the service changes. If we shorten a period, we may delete information sooner than stated above. We will not quietly extend the 90-day Snap &amp; Score period beyond what this policy says: the limit is enforced in our own software, and lengthening it requires changing this policy at the same time.</p>" +
        callout("Rewards you have already earned are not affected by any of these periods. Your experience points, achievements, unlocks, Critter Coins, competitive rank, and game record are stored separately from the review and operating information described above, and they stay on your account after that information has been deleted.") +
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
          "Account sign-in handled by Google Firebase Authentication, so that we never hold your password",
          "Encrypted connections (HTTPS) between your browser and our services",
          "Stored data held with Google Firestore, which encrypts data at rest as a standard feature of the service",
          "Database rules that prevent one player's browser from reading another player's account",
          "Rewards granted only by our server, inside transactions with one-time keys, so a browser cannot award itself anything",
          "Administrative access restricted to the owner, with administrative actions recorded",
          "Private credentials and signing keys kept out of our published code and held as server configuration",
          "Recovery codes stored only as a keyed one-way value, never as the code itself",
          "Payment card details handled entirely by Stripe and never by us",
          "Limits on how often a form or an endpoint can be called, to blunt abuse",
        ]) +
        "<p>However, no website, game, database, email service, or internet transmission can be guaranteed to be completely secure, and we cannot promise that our safeguards will never fail.</p>" +
        "<p><strong>If something does go wrong.</strong> If we discover a breach of security that affects your personal information, we will investigate it, take reasonable steps to contain it, and notify you and any authority we are required to notify, in the manner and within the time that applicable law requires.</p>" +
        "<p>You are responsible for protecting access to your Google account, email account, device, and Currents and Critters account. Use a password you do not use anywhere else, and keep your recovery code somewhere only you can reach.</p>" +
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
      title: "Account Deletion and Closing Your Account",
      body:
        "<p>You can ask us to close your Currents and Critters account and delete the personal information on it.</p>" +
        callout("There is currently no button in the game that deletes your account. Closing an account is handled by us, by hand, when you ask. We would rather tell you that than imply a self-serve control that does not exist.") +
        "<p><strong>How to ask.</strong> Email us at " + mailto(EMAIL) + " from the email address on your account, or tell us your username, and say that you want your account closed. We may need to confirm that the account is yours before we act, so that nobody else can close it or take a copy of your information.</p>" +
        "<p><strong>What closing an account removes.</strong> When we close an account we delete or de-identify the personal information held on it, which includes:</p>" +
        ul([
          "Your username, avatar, and profile settings",
          "The email address and account identifier you signed in with, and your recovery email if you added one",
          "Your progress: your level, experience points, Critter Coins, achievements, unlocks, statistics, and competitive rank",
          "Your recent game list and the dates you played",
          "Your friends list, friend requests, and friend code",
          "Your clan membership, and the in-game notices in your inbox",
        ]) +
        "<p><strong>Rewards are deleted with the account.</strong> Experience points, achievements, unlocks, and Critter Coins survive the deletion of Snap &amp; Score review information, but they do not survive the deletion of your account. They are part of your account, so closing it removes them, and they cannot be restored afterwards.</p>" +
        "<p><strong>What may remain after an account is closed.</strong> Closing an account does not erase every trace of it. We may keep:</p>" +
        ul([
          "Purchase and transaction records, which we are required to keep for accounting and tax purposes",
          "A newsletter record showing that an address is unsubscribed, so that an unsubscribe is still honored",
          "Moderation and enforcement records, where keeping them is reasonably necessary to enforce our rules or resolve a dispute",
          "Records needed to prevent fraud, including the one-way image hashes described in the Snap &amp; Score section, which are not linked to your account",
          "Messages you sent us, and our replies",
          "Information in a backup, until that backup is replaced in the ordinary course",
          "Information we are required to keep by law, or that is subject to a legal hold",
        ]) +
        "<p>Content you posted in a shared place may not disappear from other people's view. A clan chat message you sent stays visible to that clan until it ages out of the conversation in the ordinary way.</p>" +
        "<p>Deleting your Currents and Critters account does not delete your Google account or your Discord account, which belong to those companies. If you linked Discord, you may also want to remove our application's access from your Discord account settings.</p>" +
        "<p>If you only want to stop receiving marketing email, you do not need to close your account. Use the unsubscribe link in any newsletter, or ask us.</p>",
    },
    {
      title: "Your Privacy Rights in U.S. States",
      body:
        "<p>Privacy laws in the United States are written state by state, and most of them apply only to businesses above a certain size. Bearded Seal Studios LLC is a small company, so several of the best-known state privacy laws do not currently apply to us by their own terms. We would rather set out how we handle requests than claim a legal status we have not had reviewed.</p>" +
        "<p><strong>What we offer everyone, wherever you live.</strong> Whether or not a particular state law covers us, you may ask us to:</p>" +
        ul([
          "Tell you what personal information we hold about you",
          "Give you a copy of it",
          "Correct it if it is wrong",
          "Delete it and close your account",
          "Stop sending you marketing email",
        ]) +
        "<p>We handle those requests the same way for every player. To make one, email " + mailto(EMAIL) + ". We may need to verify that the account is yours first.</p>" +
        "<p><strong>We do not sell personal information</strong>, and we do not share it for cross-context behavioral advertising or targeted advertising. We do not use it for profiling that produces legal or similarly significant effects. There is therefore nothing for you to opt out of in those categories, and we do not need to offer a “do not sell” control, because there is no such activity to switch off.</p>" +
        "<p>We also do not knowingly collect the categories that state laws treat as sensitive, such as precise location, biometric identifiers, health information, racial or ethnic origin, religious beliefs, or sexual orientation. Please do not send them to us.</p>" +
        "<p><strong>If we deny a request.</strong> We will tell you why. Where a state law gives you a right to appeal that decision, you may reply to our response and ask us to reconsider, and we will respond to the appeal.</p>" +
        "<p><strong>Someone acting for you.</strong> You may use an authorized agent to make a request where applicable law allows it. We may ask for proof that you authorized them, and we may still ask to verify your own identity.</p>" +
        "<p><strong>No different treatment.</strong> We will not deny you the game, charge you a different price, or give you a worse experience because you exercised a privacy right.</p>" +
        "<p>If a state privacy law begins to apply to us as the business grows, we will update this policy and provide the notices and controls that law requires.</p>",
    },
    {
      title: "Children’s Privacy",
      body:
        "<p>Currents and Critters and our related online services are not directed to children under 13. The game is a strategy card game intended for a general audience of teenagers and adults, and we do not advertise it to children, use child-focused characters or themes to attract them, or run it on channels aimed at children.</p>" +
        "<p><strong>You must be at least 13 to create an account</strong>, join the newsletter, or make a purchase. If you are under 18, you should have your parent or legal guardian's permission before you do any of those things.</p>" +
        "<p>When you create an account we ask for your date of birth, work out your age from it, and keep only the answer: a single yes-or-no record that you told us you were 13 or older, together with the date and version of the Terms you accepted. <strong>We do not keep the date of birth itself.</strong> It is used for the age screen and nothing else, it is never written to your account, and it is never sent to our game server.</p>" +
        "<p>Please be clear about what that age screen is and is not. It is an age screen, and it is the ordinary standard of care for a service meant for a general audience. It is not age verification, and we do not claim it makes us compliant with the Children\u2019s Online Privacy Protection Act: somebody determined to get past it can type any date. What it does is make the age requirement something you are actually asked about, and something we hold a record of, rather than a rule written only in this document.</p>" +
        "<p>We do not knowingly collect personal information from a child under 13. Beyond that age screen we rely on the age requirement above and on anything we are told or notice.</p>" +
        "<p>If we learn that an account belongs to a child under 13, we will close that account and delete the personal information on it, unless a parent or legal guardian gives the consent that applicable law requires for us to keep it.</p>" +
        "<p><strong>Parents and guardians.</strong> If you believe a child under 13 has created an account or sent us personal information, contact us and we will look into it and act promptly. You may ask us to show you what we hold about your child, to delete it, and to close the account. You do not need to prove anything about the game first; just tell us the username or the email address involved.</p>" +
        "<p>Please also be aware that clan chat and table chat are places where players talk to each other, and that what a player writes there is seen by other players.</p>" +
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
          "Nashville, Tennessee 37204<br>" +
          "United States" +
        "</address>" +
        "<p><strong>Email:</strong> " + mailto(EMAIL) + "</p>" +
        "<p>Email is the fastest way to reach us, and it is the right address for a privacy request, an account deletion request, a question about your information, or a report that an account has been accessed without permission. A person reads it.</p>" +
        "<p>If you need our full mailing address in order to write to us on paper, ask us by email and we will provide it.</p>",
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
