/* ================================================================
 * Currents and Critters, the Refund, Shipping, and Preorder Policy.
 *
 * ⚠️ NOT REVIEWED BY A LAWYER. Drafted 2026-10-09 on the owner's explicit
 * decisions, taken that day:
 *   • Critter Coins: NO refunds once delivered, because they are delivered
 *     the moment payment is confirmed. Billing errors, duplicate charges,
 *     non-delivery, and anything the law requires are still honoured.
 *   • Preorders: NOT being taken on this site. The physical game is being
 *     backed through Kickstarter.
 *
 * Grounded facts:
 *   • Payments are Stripe Payment Links; the webhook grants by amount_total
 *     (COIN_PACKS_BY_CENTS / SUPPORTER_TIERS_BY_CENTS in multiplayer_server).
 *   • The four SUPPORTER TIERS render with every price and perk but have NO
 *     checkout on either surface (a deliberate display lock, see
 *     _standby/README.md), so nothing physical currently ships from the site.
 *
 * ⚠️ FOR A LAWYER, the two live questions:
 *   1. EU/UK/consumer withdrawal rights. For digital content delivered
 *      immediately, the 14-day right of withdrawal can be lost only if the
 *      consumer gave express consent AND acknowledged losing it, BEFORE
 *      delivery. Section 4 states that acknowledgement, but the CHECKOUT
 *      itself should capture it for the waiver to hold. Stripe Payment Links
 *      can carry a custom confirmation checkbox; it is not configured today.
 *   2. Whether any state-level "virtual currency" or gift-card rules reach
 *      Critter Coins. They are non-transferable out of the game and have no
 *      cash value, which is the usual reason such rules do not apply, but it
 *      is a question worth asking in Tennessee specifically.
 * ================================================================ */
(function () {
  "use strict";

  var K = window.ccLegalKit;
  var ul = K.ul, callout = K.callout, mailto = K.mailto;
  var EMAIL = K.EMAIL;

  window.CC_LEGAL = window.CC_LEGAL || {};
  window.CC_LEGAL.refunds = {
    title: "Refund, Shipping, and Preorder Policy",
    updated: "October 9, 2026",
    lede:
      "What happens when you buy something. When a purchase can be refunded, what we will always " +
      "put right no matter what, and where the physical card game stands.",

    sections: [
      {
        title: "What You Can Buy Today",
        body:
          "<p>Today, what you can buy from us is digital: <strong>Critter Coins</strong>, the in-game balance used for things inside Currents and Critters.</p>" +
          "<p>Our four Supporter Tiers are shown on the website with every price and perk listed, and they deliberately have no checkout on them. They are being backed through our Kickstarter instead, so you cannot buy a tier on this site.</p>" +
          callout("We are not currently shipping any physical product bought through this website.") +
          "<p>Payments are processed by Stripe. We do not receive or store your complete card number.</p>",
      },
      {
        title: "Digital Purchases and Refunds",
        body:
          "<p>Critter Coins are delivered to your account as soon as your payment is confirmed. Because they are delivered immediately and cannot be taken back once they are in your account and available to spend, <strong>digital purchases are generally not refundable once delivered.</strong></p>" +
          "<p>That is the rule. The exceptions below are not favours, they are things we will always put right.</p>",
      },
      {
        title: "What We Will Always Put Right",
        body:
          "<p>Email us and we will fix any of these:</p>" +
          ul([
            "<strong>You paid and nothing arrived.</strong> If a purchase did not reach your account, we will deliver it or refund it.",
            "<strong>You were charged twice.</strong> Duplicate or repeated charges for the same purchase are refunded.",
            "<strong>You were charged the wrong amount.</strong> We refund the difference, or the whole charge if that is cleaner.",
            "<strong>Something on our side went wrong.</strong> A bug, a failed grant, a wrong balance, or a reward that did not land.",
            "<strong>A purchase you did not make.</strong> Tell us promptly and we will investigate and work with Stripe.",
            "<strong>Anything the law requires.</strong> Where consumer law in your country gives you a refund right, you have it, and this policy does not take it away.",
          ]) +
          "<p>We would always rather sort out a problem than win an argument about it. If something about a purchase feels wrong, write to us and say so.</p>" +
          "<p>To ask about a purchase, email " + mailto(EMAIL) + " with the receipt or the Stripe confirmation, the email address you paid with, and your in-game username. That is usually everything we need.</p>",
      },
      {
        title: "Immediate Delivery, and Your Right to Change Your Mind",
        body:
          "<p>Some countries, including in the European Union and the United Kingdom, give consumers a period in which to change their mind about an online purchase.</p>" +
          "<p>That right can be lost for digital content that is delivered immediately, where the buyer asked for immediate delivery and understood that the right would be lost by accepting it.</p>" +
          "<p>Critter Coins are delivered immediately by design: you buy them in order to use them straight away. By buying them you are asking us to deliver them at once.</p>" +
          "<p>If you are a consumer in a country with a statutory withdrawal or cooling-off right and you believe it applies to your purchase, contact us and we will honour what the law requires. We would rather apply the law correctly than rely on a clause.</p>",
      },
      {
        title: "Spent, Traded, or Earned Items",
        body:
          "<p>Critter Coins and other in-game items have no cash value and cannot be exchanged for money, redeemed for cash, or transferred out of the game.</p>" +
          "<p>We cannot refund:</p>" +
          ul([
            "Coins you have already spent, except where the purchase itself is being refunded under this policy",
            "Items or balances you earned through play rather than bought",
            "Items or balances received in a trade with another player",
            "Items or balances removed because of cheating, fraud, or a reversed trade",
          ]) +
          "<p>If a balance is wrong because of a bug or a mistake on our side, that is not a refund question, it is something for us to correct. Tell us and we will.</p>",
      },
      {
        title: "Chargebacks",
        body:
          "<p>If something has gone wrong, please contact us first. We can almost always fix it faster than a bank can, and a chargeback takes weeks.</p>" +
          "<p>Where a chargeback is raised, we may suspend the account’s ability to buy, and may remove items or balances the disputed payment paid for, while the dispute is open.</p>" +
          "<p>If a chargeback turns out to be the right outcome, that is fine and we will not hold it against you.</p>",
      },
      {
        title: "Shipping",
        body:
          "<p>We are not currently shipping physical products bought through this website, because there is nothing physical to buy here yet.</p>" +
          "<p>When physical products do ship, this policy will be updated before they go on sale to set out:</p>" +
          ul([
            "Where we ship to, and what it costs",
            "How long orders take to be made and dispatched",
            "What happens to an order that is lost, damaged, or delivered to the wrong address",
            "Who pays return shipping, and the condition goods must be returned in",
            "How customs charges and import duties are handled",
          ]) +
          "<p>Anything backed through Kickstarter is fulfilled under the terms of that campaign.</p>",
      },
      {
        title: "Preorders and the Physical Card Game",
        body:
          "<p><strong>We are not taking preorders for the physical card game on this website.</strong></p>" +
          "<p>The physical game is being backed through Kickstarter. If you pledge there, your pledge is governed by Kickstarter’s own terms as well as ours, and Kickstarter handles the payment.</p>" +
          callout("Nothing on our websites is a promise of a delivery date for the physical card game. Making a card game takes as long as it takes, and we would rather say so than invent a date.") +
          "<p>Prices, contents, art, and card lists shown for the physical game are what we currently intend, and they may change while the game is still being made.</p>" +
          "<p>If we ever begin taking preorders directly, this policy will be updated first to explain what you are paying for, when you can cancel, what happens on a delay, and how to get your money back.</p>",
      },
      {
        title: "Ocean Conservation",
        body:
          "<p>Currents and Critters pledges 5% of every purchase to ocean conservation.</p>" +
          "<p>That pledge does not change the price you pay and does not change your rights under this policy. If a purchase is refunded, the refund is for the full amount you paid.</p>" +
          "<p>Our Business and Legal Information page explains how that pledge works and what it does and does not claim.</p>",
      },
      {
        title: "Questions",
        body:
          "<p>Email us about any purchase, refund, or delivery question:</p>" +
          "<p>" + mailto(EMAIL) + "</p>" +
          K.addressBlock(K.ADDRESS_LINES) +
          "<p>If this policy and our Terms of Service ever disagree about a purchase, this policy governs the purchase.</p>",
      },
    ],
  };
})();
