/* ================================================================
 * Currents and Critters, the Business Contact and Legal Information page.
 *
 * ⚠️ NOT REVIEWED BY A LAWYER.
 *
 * Confirmed by the owner 2026-10-09: the entity name, the mailing address and
 * the contact email. The same address is published by
 * newsletter_email.BUSINESS_ADDRESS_LINES and client/unsubscribe.html, so all
 * three change together.
 *
 * ⚠️ DELIBERATELY NOT STATED HERE, because nobody has verified it: the LLC's
 * formation date, its Tennessee Secretary of State control number, its
 * registered agent, and any EIN or sales-tax registration. Do not add them
 * from memory. If you want them on this page, read them off the filing.
 *
 * ⚠️⚠️ THE CONSERVATION PLEDGE IS THE RISKIEST CLAIM ON THE WHOLE SITE, and
 * this page states it conservatively ON PURPOSE. An audit on 2026-10-09 found
 * the site makes the pledge in materially DIFFERENT words in different places:
 *   • "5% goes to ocean conservation"                      (generic)
 *   • "5% of every purchase supports ocean conservation"    (basis: purchase)
 *   • "5% goes to ocean conservation on every game sold.
 *      Receipts published."                                 (index.html:945)
 *   • "Help us hit our 2026 goal: $25k raised."             (a figure)
 * Three problems a lawyer and the owner need to settle:
 *   1. "every purchase" and "every game sold" are different bases, and
 *      neither says gross or net of fees, refunds and taxes.
 *   2. "Receipts published" is not true today. donation_report.py is an
 *      ADMIN CLI that reads /api/admin/donations behind a recovery key; there
 *      is no public receipts page and no public route for one. Either publish
 *      them or stop saying they are published.
 *   3. Naming the Surfrider Foundation alongside a percentage pledge may make
 *      this a "charitable sales promotion" / commercial co-venture, which
 *      several states regulate and some require registration for. The
 *      independence disclaimer on index.html:1221 is the right instinct and
 *      may not be sufficient on its own.
 * This page therefore states the commitment and the independence disclaimer,
 * and does NOT invent a basis, a cadence, a recipient list, or a total.
 * ================================================================ */
(function () {
  "use strict";

  var K = window.ccLegalKit;
  var ul = K.ul, callout = K.callout, mailto = K.mailto;
  var EMAIL = K.EMAIL;

  window.CC_LEGAL = window.CC_LEGAL || {};
  window.CC_LEGAL.legal = {
    title: "Business Contact and Legal Information",
    updated: "October 9, 2026",
    lede:
      "Who we are and how to reach us. One studio, one inbox, and a person on the other end of it. " +
      "If you are looking for somebody to write to about anything on this site, this is the page.",

    sections: [
      {
        title: "Who We Are",
        body:
          "<p>Currents and Critters is made and operated by:</p>" +
          K.addressBlock(K.ADDRESS_LINES) +
          "<p>Bearded Seal Studios LLC is a limited liability company formed in the State of Tennessee, United States.</p>" +
          "<p>Currents and Critters is an independent game. It is a very small studio, which is why the contact address below reaches a person rather than a department.</p>",
      },
      {
        title: "How to Reach Us",
        body:
          "<p>Everything comes to one address, and it is read by a person:</p>" +
          "<p>" + mailto(EMAIL) + "</p>" +
          "<p>That address is the right one for all of these:</p>" +
          ul([
            "Privacy requests, including asking what we hold, correcting it, deleting it, or closing your account",
            "Purchases, receipts, refunds, and anything about a payment",
            "Reporting a player, or appealing a decision about your account",
            "Copyright notices and counter-notices, and permission requests",
            "Accessibility problems",
            "Press, partnership, and licensing enquiries",
            "Bug reports, and telling us the game is broken",
          ]) +
          callout("Putting the subject in the subject line genuinely helps, for example “Privacy request”, “Refund”, or “Copyright Notice”.") +
          "<p>We are one studio in one time zone, so please allow a little time for a reply. If something is urgent and involves somebody’s safety, contact your local emergency services first.</p>",
      },
      {
        title: "Our Websites",
        body:
          "<p>These are ours:</p>" +
          ul([
            "<strong>currentsandcritters.com</strong>, the main website",
            "<strong>play.currentsandcritters.com</strong>, the online game",
            "<strong>score.currentsandcritters.com</strong>, Snap &amp; Score, for scoring a physical board",
            "<strong>beardedsealstudios.com</strong>, the studio",
          ]) +
          "<p>Anything else that claims to be us is not us. We will never ask for your password, and we will never ask you to send a payment in chat.</p>",
      },
      {
        title: "Our Policies",
        body:
          "<p>All of our policies are public, and none of them requires an account to read:</p>" +
          ul([
            "<strong>Privacy Policy</strong>, at /privacy: what we collect, store, share, and delete",
            "<strong>Terms of Service</strong>, at /terms: the agreement for using the game",
            "<strong>Cookie Notice</strong>, at /cookies: the browser storage the game uses",
            "<strong>Refund, Shipping, and Preorder Policy</strong>, at /refunds: purchases and refunds",
            "<strong>Community Guidelines</strong>, at /community-guidelines: how to behave, and what happens if you do not",
            "<strong>Copyright and DMCA Policy</strong>, at /copyright: our rights, your permissions, and takedowns",
            "<strong>Accessibility Statement</strong>, at /accessibility: what we have done and what we have not",
          ]) +
          "<p>If a policy contradicts something a page of marketing says, the policy is the one to rely on.</p>",
      },
      {
        title: "Payments and Service Providers",
        body:
          "<p>Payments are processed by <strong>Stripe</strong>. We do not receive or store your complete card number.</p>" +
          "<p>The services we rely on to run the game include Google (sign-in, Firebase and Firestore, and the mail service our newsletter is sent with), Render and Vercel (hosting), Stripe (payments), and Discord (only if you choose to link a Discord account).</p>" +
          "<p>Our Privacy Policy lists them and explains what each one receives.</p>",
      },
      {
        title: "Ocean Conservation",
        body:
          "<p>Currents and Critters pledges <strong>5% of every purchase to ocean conservation</strong>. It is a commitment Bearded Seal Studios LLC makes on its own behalf, out of what it earns.</p>" +
          "<p>Some things worth being plain about:</p>" +
          ul([
            "The pledge does not change the price you pay. You are buying a product from us, not making a donation to a charity.",
            "You are not the donor. Bearded Seal Studios LLC is, and no part of your payment is held on trust for anybody else.",
            "Because you are not the donor, a purchase is not a tax-deductible charitable contribution for you.",
            "If a purchase is refunded, the refund is the full amount you paid.",
          ]) +
          callout("Currents and Critters is an independent supporter of ocean conservation. We are not sponsored by, endorsed by, or officially partnered with the Surfrider Foundation or any other conservation organisation, and nothing we publish should be read as suggesting otherwise.") +
          "<p>If you would like to know more about how the pledge works, or where it has gone, email us and ask. We would rather answer a direct question accurately than publish a number we cannot stand behind.</p>" +
          "<p>If you want to support ocean conservation directly, and with the full tax treatment that comes with it, please give to a conservation charity yourself. That will always do more good per dollar than our 5%, and we will never pretend otherwise.</p>",
      },
      {
        title: "Legal Notices",
        body:
          "<p>Formal legal notices may be sent to the mailing address in the first section, addressed to Bearded Seal Studios LLC.</p>" +
          "<p>Copyright notices and counter-notices should go to the designated agent named in our Copyright and DMCA Policy.</p>" +
          "<p>Our Terms of Service sets out the governing law and where disputes are heard. In short: Tennessee law, and the state or federal courts in Davidson County, Tennessee.</p>" +
          callout("Nothing on this page is legal advice. It is a description of our business and our policies.") +
          "<p>If you have found something on our site that looks wrong, misleading, or out of date, please tell us. We will fix it.</p>",
      },
    ],
  };
})();
