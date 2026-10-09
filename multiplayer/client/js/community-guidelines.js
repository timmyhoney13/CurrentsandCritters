/* ================================================================
 * Currents and Critters, the Community Guidelines.
 *
 * ⚠️ NOT REVIEWED BY A LAWYER, though this is the least legally loaded of the
 * eight documents: it is a conduct policy, not a contract term, and the Terms
 * of Service is what makes it binding.
 *
 * Grounded in the surfaces that actually exist: table chat and emotes, clans
 * and clan chat, tournaments, trading, the Supporter Reef Wall, usernames and
 * clan names, and the vote-kick / skip-turn mechanics in multiplayer_server.
 * Nothing here promises a moderation capability we do not have: there is no
 * 24/7 moderation team, so the enforcement section says what we really do,
 * which is read reports by email and act on them.
 * ================================================================ */
(function () {
  "use strict";

  var K = window.ccLegalKit;
  var ul = K.ul, callout = K.callout, mailto = K.mailto;
  var EMAIL = K.EMAIL;

  window.CC_LEGAL = window.CC_LEGAL || {};
  window.CC_LEGAL["community-guidelines"] = {
    title: "Community Guidelines",
    updated: "October 9, 2026",
    lede:
      "Currents and Critters is a game about a reef, and a reef only works when everything in it " +
      "gets along. These guidelines explain what we expect from players, what is not allowed, and " +
      "what happens when somebody breaks the rules.",

    sections: [
      {
        title: "The Short Version",
        body:
          callout("Play fair. Be decent to the person in the other seat. Do not put private information where strangers can read it.") +
          "<p>That covers almost everything. The rest of this page is the detail, for the times when detail matters.</p>" +
          "<p>These guidelines apply everywhere players meet each other: at the table, in chat, in clans, in tournaments, in trades, in usernames and clan names, and on the Supporter Reef Wall.</p>",
      },
      {
        title: "Be Decent",
        body:
          "<p>Treat other players the way you would want to be treated at a table in somebody’s kitchen.</p>" +
          "<p>Not allowed:</p>" +
          ul([
            "Harassment, bullying, threats, or targeting somebody repeatedly",
            "Slurs or attacks based on race, ethnicity, nationality, religion, disability, age, sex, gender identity, or sexual orientation",
            "Sexual content, sexual harassment, or any sexual content involving minors, ever",
            "Violent threats, encouraging self-harm, or wishing harm on somebody",
            "Posting somebody else’s private information, including their real name, address, school, workplace, or photographs",
            "Impersonating another player, a moderator, or us",
            "Spam, advertising, scams, phishing, or links to malware",
          ]) +
          "<p>Losing a game is allowed to be annoying. Taking it out on the other player is not.</p>",
      },
      {
        title: "Play Fair",
        body:
          "<p>Competitive play only means anything if the results are real.</p>" +
          "<p>Not allowed:</p>" +
          ul([
            "Cheating, exploiting a bug, or using a modified client, script, or program to gain an advantage",
            "Running a bot in place of a person, or automating play to farm rewards",
            "Submitting a board, a photograph, or a result for a game you did not actually play",
            "Colluding with another player to fix a game, a ranking, or a reward",
            "Making extra accounts to claim the same reward twice, to boost your own rank, or to get around a suspension",
            "Deliberately stalling, quitting repeatedly, or holding up a table to waste other players’ time",
            "Abusing the vote-kick or skip-turn tools to push somebody out of a game they are playing properly",
          ]) +
          callout("If you find a bug that gives an advantage, tell us instead of using it. We would much rather hear about it from you.") +
          "<p>If you spot an exploit and report it, we will fix it, and you will have done the reef a favour.</p>",
      },
      {
        title: "Chat and Emotes",
        body:
          "<p>Chat is there so a game feels like a game with people in it.</p>" +
          "<p>Keep in mind:</p>" +
          ul([
            "Chat is public to everyone at your table, and to anyone watching as a spectator",
            "Chat is not private and is not encrypted between players",
            "What you type can be reported by anybody who can read it",
          ]) +
          callout("Never put private information in chat: your address, your phone number, your email, your passwords, your payment details, or anybody else’s.") +
          "<p>We never ask for your password, and we will never ask you to pay us in chat. Anyone who does is not us. Report them.</p>",
      },
      {
        title: "Names: Usernames, Clans, and the Reef Wall",
        body:
          "<p>Your username, your clan name, and a name you choose for the Supporter Reef Wall are all public.</p>" +
          "<p>A name must not:</p>" +
          ul([
            "Contain slurs, hate speech, or sexual content",
            "Impersonate another player, a public figure, a moderator, or us",
            "Advertise, or point people to another service",
            "Infringe somebody’s trademark or other rights",
            "Contain private information, your own or anybody else’s",
          ]) +
          "<p>We may refuse, reclaim, or change a name that breaks these guidelines, and a Supporter Reef Wall name may be held for review before it appears.</p>",
      },
      {
        title: "Clans, Tournaments, and Game Nights",
        body:
          "<p>Running a clan or a tournament means other players rely on you.</p>" +
          ul([
            "Do not use a clan or a tournament to organise cheating, collusion, or rank boosting",
            "Do not promise rewards you cannot give, or run a paid competition using our game without our agreement",
            "Do not use a clan description, icon, or tournament name to break the rules on names above",
            "Settle disputes between members decently, and bring us the ones you cannot settle",
          ]) +
          "<p>Seasons, ladders, and leaderboards may be reset or corrected, and results obtained unfairly may be reversed.</p>",
      },
      {
        title: "Trading",
        body:
          "<p>A trade is an agreement between two players. Make the trade you said you would make.</p>" +
          ul([
            "Do not use a trade to scam another player",
            "Do not promise anything outside the game in exchange for a trade, including real money",
            "Do not use trades to move items between your own accounts",
          ]) +
          "<p>In-game items have no cash value and cannot be sold for money. Selling, buying, or trading items or accounts for real money is not allowed, and we may reverse a trade that came from fraud, a bug, or a compromised account.</p>",
      },
      {
        title: "Reporting Somebody",
        body:
          "<p>If a player is breaking these guidelines, tell us. We read every report.</p>" +
          "<p>Email " + mailto(EMAIL) + " and include whatever you have:</p>" +
          ul([
            "The player’s username",
            "Roughly when it happened, and which game or mode",
            "What happened, in your own words",
            "A screenshot, if you have one",
          ]) +
          "<p>Because table chat is only held while a game is live, a screenshot is genuinely useful: once the game ends, the conversation is gone.</p>" +
          callout("If somebody is in immediate danger, please contact your local emergency services first. We are a card game studio and we cannot help with an emergency.") +
          "<p>We will not share your report with the person you reported where we can avoid it. Do not retaliate, and do not organise others to pile on: that breaks these guidelines too, whoever started it.</p>",
      },
      {
        title: "What Happens When Rules Are Broken",
        body:
          "<p>We are a very small studio. There is no round-the-clock moderation team, and we are honest about that. What there is, is a person who reads the reports and acts on them.</p>" +
          "<p>Depending on what happened and whether it has happened before, we may:</p>" +
          ul([
            "Give a warning",
            "Change or reclaim a name",
            "Remove content, a clan, or a Supporter Reef Wall entry",
            "Reverse rewards, items, ranking changes, or a trade",
            "Hold rewards while something is reviewed",
            "Limit access to a feature such as chat, trading, or competitive play",
            "Suspend an account temporarily",
            "Close an account permanently",
          ]) +
          "<p>We aim to match the response to what actually happened, and to warn first where warning is reasonable. Serious things, especially anything involving a minor, a threat, or cheating that affects other players, may be acted on immediately and permanently.</p>" +
          "<p>Automated anti-cheat checks can hold or refuse rewards before any person has looked. Our Terms of Service explains that, and a person will review it if you ask.</p>",
      },
      {
        title: "If You Think We Got It Wrong",
        body:
          "<p>We get things wrong sometimes. If you think a decision about your account was a mistake, email " + mailto(EMAIL) + " and say so plainly.</p>" +
          "<p>Tell us your username, what happened, and why you think it was wrong. A person will look at it again.</p>" +
          "<p>We would rather reverse our own mistake than defend it.</p>" +
          "<p>These guidelines form part of our Terms of Service, and we may update them as the game grows. The “Last updated” date at the top of this page tells you when they last changed.</p>",
      },
    ],
  };
})();
