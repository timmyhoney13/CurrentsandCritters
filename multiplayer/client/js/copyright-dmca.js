/* ================================================================
 * Currents and Critters, the Copyright and DMCA Policy.
 *
 * ⚠️ NOT REVIEWED BY A LAWYER, and this one has a HARD PREREQUISITE.
 *
 * ⚠️⚠️ ACTION REQUIRED BEFORE THIS PROTECTS YOU:
 * The owner chose (2026-10-09) to act as the designated agent at the business
 * address. DMCA safe harbour under 17 U.S.C. §512 requires that the agent be
 * designated to the public AND registered with the United States Copyright
 * Office through its online directory (a small fee, renewable every three
 * years). Publishing this page does NOT complete that registration.
 *   → Register at: https://dmca.copyright.gov/
 * Until that registration exists, this page is a courtesy takedown process,
 * not a safe harbour. Nothing in here claims otherwise, deliberately.
 *
 * ⚠️ Also worth a lawyer's time: the repeat-infringer policy in section 7 has
 * to be real and actually applied for safe harbour to hold, and section 5's
 * fan-content permission is a licence the studio is granting, so it should be
 * granted on purpose rather than inherited from a template.
 * ================================================================ */
(function () {
  "use strict";

  var K = window.ccLegalKit;
  var ul = K.ul, callout = K.callout, mailto = K.mailto;
  var EMAIL = K.EMAIL;

  window.CC_LEGAL = window.CC_LEGAL || {};
  window.CC_LEGAL.copyright = {
    title: "Copyright and DMCA Policy",
    updated: "October 9, 2026",
    lede:
      "Currents and Critters is drawn, written, and built by hand, and we take other people’s work " +
      "as seriously as our own. This page explains what belongs to us, what you may do with it, and " +
      "how to report material you believe infringes a copyright.",

    sections: [
      {
        title: "What Belongs to Us",
        body:
          "<p>Currents and Critters, including the artwork, the critters, the card designs and card text, the Rules of the Ocean, the names, the logos, the music, the software, and the look of our websites, is owned by Bearded Seal Studios LLC or licensed to it.</p>" +
          "<p>It is protected by copyright and other laws. We keep every right we do not expressly grant.</p>" +
          "<p>Our Terms of Service sets out the permission you have to use the game. In short: play it, enjoy it, and do not reproduce, manufacture, or sell it.</p>",
      },
      {
        title: "What You May Not Do",
        body:
          "<p>Without our written permission, please do not:</p>" +
          ul([
            "Print, manufacture, or sell copies of the cards, the board, or the artwork",
            "Sell merchandise using our art, our critters, our names, or our logos",
            "Redistribute the game or its files, or host your own copy of it",
            "Make a reskin, clone, or derivative version of the game for public release",
            "Train a machine-learning model on our artwork or card designs",
            "Remove or obscure a copyright notice, credit, signature, or watermark",
            "Use our name or logo in a way that suggests we endorse, sponsor, or are partnered with you",
          ]) +
          "<p>If you want to do something that is not on this page, ask us. The answer is often yes.</p>",
      },
      {
        title: "Fan Content, Videos, and Streaming",
        body:
          "<p>We like seeing the game played. You have our permission to:</p>" +
          ul([
            "Stream or record Currents and Critters, including monetised videos on platforms that pay creators",
            "Take screenshots and share them",
            "Write reviews, guides, strategy posts, and rules explanations, using screenshots and card images as needed to make your point",
            "Draw your own fan art of the critters and share it non-commercially",
            "Run a casual tournament or game night for your own community, with no entry fee",
          ]) +
          "<p>When you do, please:</p>" +
          ul([
            "Credit Currents and Critters and link to currentsandcritters.com where it is practical",
            "Make it clear your channel or project is not made by us or endorsed by us",
            "Do not use our art or name as the primary branding of a commercial product",
            "Do not distribute print-and-play files, card scans, or anything that lets somebody make the physical game without buying it",
          ]) +
          callout("This permission is a courtesy and we can withdraw it from a particular person or project. It does not transfer ownership of anything.") +
          "<p>For anything commercial beyond streaming, including merchandise, a paid tournament, or a licensing arrangement, email us first.</p>",
      },
      {
        title: "Reporting Material That Infringes Your Copyright",
        body:
          "<p>If you own a copyright and believe material on our services infringes it, you can ask us to remove it.</p>" +
          "<p>Send a written notice to our designated agent, below, including all of the following:</p>" +
          ul([
            "Your physical or electronic signature",
            "Identification of the copyrighted work you say has been infringed",
            "Identification of the material you say is infringing, with enough detail for us to find it, such as a link, a username, or a description of where it appears",
            "Your name, address, telephone number, and email address",
            "A statement that you believe in good faith that the use is not authorised by the copyright owner, its agent, or the law",
            "A statement that the information in your notice is accurate, and, under penalty of perjury, that you are the copyright owner or authorised to act on the owner’s behalf",
          ]) +
          "<p>Our designated agent for copyright notices:</p>" +
          K.addressBlock([
            "<strong>Timothy Honey, Designated Copyright Agent</strong>",
            "Bearded Seal Studios LLC",
            "916A South Douglas Avenue",
            "Nashville, Tennessee 37204-2021",
            "United States",
          ]) +
          "<p><strong>Email:</strong> " + mailto(EMAIL) + "</p>" +
          callout("Please put “Copyright Notice” in the subject line so it is not missed. Email reaches us fastest.") +
          "<p>A notice that leaves out the items above may not be effective. Knowingly sending a false notice can make you liable for damages, including costs and legal fees, so please be sure before you send one.</p>",
      },
      {
        title: "What We Do With a Notice",
        body:
          "<p>When we receive a notice that looks complete and valid, we will:</p>" +
          ul([
            "Review it, and remove or disable access to the material if removal is the right response",
            "Make a reasonable effort to tell the person who provided the material, and pass on your notice",
            "Keep a record of the notice and what we did",
          ]) +
          "<p>Most material on our services that a player provided is short-lived, such as a username, a clan name, or a chat message in a game that has already ended. Where the material no longer exists, there may be nothing to remove, and we will tell you that.</p>" +
          "<p>We may also remove material on our own initiative where we believe it infringes somebody’s rights.</p>",
      },
      {
        title: "If Your Material Was Removed: Counter-Notice",
        body:
          "<p>If your material was removed and you believe that was a mistake, or that you are permitted to use it, you can send a counter-notice to the agent above, including:</p>" +
          ul([
            "Your physical or electronic signature",
            "Identification of the material that was removed, and where it appeared before removal",
            "A statement, under penalty of perjury, that you believe in good faith the material was removed as a result of mistake or misidentification",
            "Your name, address, and telephone number",
            "A statement that you consent to the jurisdiction of the federal court for the district where you live, or, if you are outside the United States, of a federal court in a district where we may be found, and that you will accept service of process from the person who sent the original notice or their agent",
          ]) +
          "<p>We may forward your counter-notice to the person who made the original complaint, and we may restore the material in line with the law.</p>" +
          "<p>As with a notice, a knowingly false counter-notice can make you liable for damages.</p>",
      },
      {
        title: "Repeat Infringers",
        body:
          "<p>We will suspend or close the account of a player who repeatedly infringes copyright.</p>" +
          "<p>In deciding, we look at how many valid notices concern that account, whether the material was provided deliberately, and whether anything changed after a warning. We take account of counter-notices and of notices that turn out to be unfounded.</p>" +
          "<p>Our Terms of Service and Community Guidelines cover the rest of our enforcement.</p>",
      },
      {
        title: "Trademarks, and Reporting Other Rights",
        body:
          "<p>“Currents and Critters”, “Bearded Seal Studios”, and our logos are marks we use to identify ourselves and the game.</p>" +
          "<p>You may use the name “Currents and Critters” plainly, to refer to the game, for example in a review, a video title, or a guide. Please do not use our name or logo as the branding of your own product or service, or in a way that suggests we endorse you.</p>" +
          "<p>If you believe something on our services infringes a trademark, a right of publicity, or another right that is not copyright, email " + mailto(EMAIL) + " with the same level of detail a copyright notice needs, and we will look into it.</p>",
      },
      {
        title: "Questions and Permissions",
        body:
          "<p>For permission requests, licensing questions, press use of our artwork, or anything else about this policy:</p>" +
          "<p>" + mailto(EMAIL) + "</p>" +
          K.addressBlock(K.ADDRESS_LINES) +
          "<p>Nothing on this page is legal advice, and nothing on it waives any right or remedy we have.</p>",
      },
    ],
  };
})();
