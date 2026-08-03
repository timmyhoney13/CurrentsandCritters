/* ================================================================
 * Currents & Critters — the Rule Book, one shared source of truth.
 *
 * The rule text below is the printed rulebook, reproduced WORD FOR WORD.
 * Nothing here is paraphrased, trimmed or "improved" — only the layout
 * (headings, cards, figures, example boxes) is ours. If a rule changes on
 * the printed book, change it here and nowhere else.
 *
 * Rendered in two places, both from this one string:
 *   • Player Home → How to play → Full Rulebook   (light sea-glass skin)
 *   • In game     → Menu → Rule Book              (deep-ocean skin)
 * The skin comes from a wrapper class (.rb-light / .rb-dark); every rule in
 * preview.css reads its colours from CSS variables set by that wrapper, so
 * the same markup looks at home on the Player Home page and over the table.
 * ================================================================ */
(function () {
  "use strict";

  // Card art lives on printed sheet pages: a horizontal page holds two cards
  // (top/bottom), a vertical page holds two (left/right), an ocean page is one
  // whole card. Same uid → page mapping the game itself uses, so a figure can
  // never drift away from the card a player is holding.
  var ART_V = "20260717-v12b";
  function pad2(n) { return String(n).padStart(2, "0"); }

  // One horizontal card face, cropped out of its two-up sheet page.
  function hFace(uid) {
    var page = pad2(Math.floor((uid + 1) / 2));
    var half = uid % 2 === 1 ? "top" : "bottom";
    return '<span class="rb-face rb-face-h rb-face-' + half + '">'
      + '<img src="/horizontal_cards/page_' + page + '.png?v=' + ART_V + '" alt="" loading="lazy" draggable="false">'
      + '</span>';
  }
  // One vertical card face (left/right half of its sheet page).
  function vFace(uid) {
    var page = pad2(Math.floor((uid - 101) / 2) + 1);
    var half = uid % 2 === 1 ? "left" : "right";
    return '<span class="rb-face rb-face-v rb-face-' + half + '">'
      + '<img src="/vertical_cards/page_' + page + '.png?v=' + ART_V + '" alt="" loading="lazy" draggable="false">'
      + '</span>';
  }
  // A whole ocean card (one card per sheet page).
  function oFace(uid) {
    return '<span class="rb-face rb-face-o">'
      + '<img src="/oceans_cards/page_' + pad2(uid - 200) + '.png?v=' + ART_V + '" alt="" loading="lazy" draggable="false">'
      + '</span>';
  }
  // A WHOLE physical card — the entire printed sheet page, uncropped. Every
  // animal card carries two creatures, one at each end (Up/Down cards stack
  // them, Left/Right cards sit them side by side); an Ocean card is a single
  // picture. This is the card as it lies on the table, and the three shapes all
  // share one 720×1008 portrait outline, so ten of them make an even grid.
  function fullFace(uid) {
    var src;
    if (uid >= 201) src = "/oceans_cards/page_" + pad2(uid - 200) + ".png";
    else if (uid >= 101) src = "/vertical_cards/page_" + pad2(Math.floor((uid - 101) / 2) + 1) + ".png";
    else src = "/horizontal_cards/page_" + pad2(Math.floor((uid + 1) / 2)) + ".png";
    return '<span class="rb-face rb-face-full">'
      + '<img src="' + src + '?v=' + ART_V + '" alt="" loading="lazy" draggable="false">'
      + '</span>';
  }

  // One card sitting in the Pool, shown whole and captioned with BOTH of its
  // creatures — never a cropped half, which reads as if a card were one animal.
  function poolCard(uid, nameA, nameB) {
    return '<figure class="rb-pool-card">' + fullFace(uid)
      + '<figcaption><span class="rb-pool-name">' + nameA + '</span>'
      + (nameB ? '<span class="rb-pool-name">' + nameB + '</span>' : '')
      + '</figcaption></figure>';
  }

  // A labelled card in a figure: the art plus a caption underneath.
  function figCard(faceHtml, name, note) {
    return '<figure class="rb-figcard">' + faceHtml
      + '<figcaption><span class="rb-figcard-name">' + name + '</span>'
      + (note ? '<span class="rb-figcard-note">' + note + '</span>' : '')
      + '</figcaption></figure>';
  }

  // ── The figures the printed book calls for ──────────────────────
  // Ocean layout: where each attachment slot sits around an Ocean card.
  function oceanLayoutFigure() {
    return ''
      + '<div class="rb-fig">'
      +   '<div class="rb-fig-head">Ocean Layout Diagram Coral Reef</div>'
      +   '<div class="rb-ocean-diagram">'
      +     '<div class="rb-od-slot rb-od-top"><span class="rb-od-arrow">&#9650;</span><span class="rb-od-lbl">Top = Surface</span></div>'
      +     '<div class="rb-od-mid">'
      +       '<div class="rb-od-slot rb-od-left"><span class="rb-od-arrow">&#9664;</span><span class="rb-od-lbl">Left</span></div>'
      +       '<div class="rb-od-card">' + oFace(217) + '</div>'
      +       '<div class="rb-od-slot rb-od-right"><span class="rb-od-arrow">&#9654;</span><span class="rb-od-lbl">Right</span></div>'
      +     '</div>'
      +     '<div class="rb-od-slot rb-od-bottom"><span class="rb-od-arrow">&#9660;</span><span class="rb-od-lbl">Bottom = Ocean Floor</span></div>'
      +   '</div>'
      +   '<div class="rb-fig-note">Left/Right attachment slots</div>'
      +   '<div class="rb-fig-note rb-fig-note-gold">Charts mean 1 of this card, you get this many points</div>'
      + '</div>';
  }

  // Card anatomy: the five things printed on every card, called out in place.
  function cardAnatomyFigure() {
    return ''
      + '<div class="rb-fig">'
      +   '<div class="rb-fig-head">Labeled Card Diagram</div>'
      +   '<div class="rb-anatomy">'
      +     '<div class="rb-an-card">' + hFace(11)
      +       '<span class="rb-an-pin rb-an-pin-cost">1</span>'
      +       '<span class="rb-an-pin rb-an-pin-sym">2</span>'
      +       '<span class="rb-an-pin rb-an-pin-name">3</span>'
      +       '<span class="rb-an-pin rb-an-pin-spec">4</span>'
      +       '<span class="rb-an-pin rb-an-pin-abil">5</span>'
      +     '</div>'
      +     '<ol class="rb-an-key">'
      +       '<li><span class="rb-an-num">1</span><span class="rb-an-txt"><b>Cost</b> represented by number of sanddollars (Top Left) <span class="rb-arrow">&rarr;</span> Number of cards to discard</span></li>'
      +       '<li><span class="rb-an-num">2</span><span class="rb-an-txt"><b>Symbol</b> (Top Right) <span class="rb-arrow">&rarr;</span> Used for abilities</span></li>'
      +       '<li><span class="rb-an-num">3</span><span class="rb-an-txt"><b>Name</b> (Bottom left)</span></li>'
      +       '<li><span class="rb-an-num">4</span><span class="rb-an-txt"><b>Species</b> (Bottom Right) <span class="rb-arrow">&rarr;</span> Crustaceans, Cephalopods, Bait Fish, etc.</span></li>'
      +       '<li><span class="rb-an-num">5</span><span class="rb-an-txt"><b>Ability / Points</b> (Bottom Box)</span></li>'
      +     '</ol>'
      +   '</div>'
      + '</div>';
  }

  // The Pool board filling to its ten-card limit, then resetting. Laid out the
  // way the real board is — two rows of five — and filled with ten actual cards
  // rather than anonymous card backs, because in play the Pool is always face up.
  //
  // Ten DIFFERENT physical cards, each drawn whole: six Up/Down cards, three
  // Left/Right cards and one Ocean. The uid names the card's first end (top, or
  // left) and both creatures are captioned — every entry must be a distinct
  // sheet page, or two slots would show the same card twice.
  var POOL_EXAMPLE = [
    [11,  "Horned Puffin",      "Lobster"],                 // Up/Down page 06
    [33,  "Great Albatross",    "Staghorn Coral"],          // Up/Down page 17
    [25,  "Peruvian Pelican",   "Red Beaded Anemone"],      // Up/Down page 13
    [47,  "Osprey",             "Mandarin Goby"],           // Up/Down page 24
    [53,  "Mullet",             "Johnson’s Sea Cucumber"],  // Up/Down page 27
    [69,  "Sardine",            "Sea Urchin"],              // Up/Down page 35
    [107, "Yellowfin Tuna",     "Spinner Dolphin"],         // Left/Right page 04
    [109, "Bottlenose Dolphin", "Common Octopus"],          // Left/Right page 05
    [141, "King Salmon",        "Whale Shark"],             // Left/Right page 21
    [217, "Coral Reef"],                                    // Ocean page 17
  ];
  function poolFigure() {
    var slots = POOL_EXAMPLE.map(function (c) { return poolCard(c[0], c[1], c[2]); }).join("");
    return ''
      + '<div class="rb-fig">'
      +   '<div class="rb-fig-head">Pool Layout Example</div>'
      +   '<div class="rb-pool-board">' + slots + '</div>'
      +   '<div class="rb-fig-note">Ten face-up cards, two rows of five. Anyone may draw from here on their turn.</div>'
      +   '<div class="rb-fig-note">Each one is a whole card: every animal card has a creature at each end, and the orientation you attach it in (Up, Down, Left or Right) decides which end you are playing.</div>'
      +   '<div class="rb-pool-flow">'
      +     '<span class="rb-pool-step">Pool reaches 10 cards</span>'
      +     '<span class="rb-arrow">&rarr;</span>'
      +     '<span class="rb-pool-step">Move all cards to a face-down discard pile</span>'
      +     '<span class="rb-arrow">&rarr;</span>'
      +     '<span class="rb-pool-step rb-pool-step-done">Reset the Pool</span>'
      +   '</div>'
      + '</div>';
  }

  // All four Mandarin Gobies, the "Shooting the Moon" set.
  function gobyFigure() {
    var gobies = [28, 48, 80, 90].map(function (u) { return '<span class="rb-goby">' + hFace(u) + '</span>'; }).join("");
    return ''
      + '<div class="rb-fig">'
      +   '<div class="rb-fig-head">Goby Set Example</div>'
      +   '<div class="rb-goby-row">' + gobies + '</div>'
      +   '<div class="rb-fig-note rb-fig-note-gold">All 4 Mandarin Gobies &rarr; +20 points</div>'
      + '</div>';
  }

  // ── The book ────────────────────────────────────────────────────
  var COMPONENTS = [
    "24 vertical cards", "22 horizontal cards", "67 oceans", "1 end game card",
    "1 score pad", "1 game board (the pool)", "12 strategy cards",
  ];

  var SECTIONS = [
    ["rb-objective",   "Objective"],
    ["rb-setup",       "Setup"],
    ["rb-gameplay",    "Gameplay"],
    ["rb-cardtypes",   "Card Types"],
    ["rb-attachment",  "Attachment"],
    ["rb-anatomy",     "Card Anatomy"],
    ["rb-star",        "Star Abilities"],
    ["rb-pool",        "The Pool"],
    ["rb-special",     "Special Rules"],
    ["rb-endgame",     "End Game"],
    ["rb-scoring",     "Scoring"],
    ["rb-competitive", "Competitive Play"],
    ["rb-encyclopedia","Encyclopedia"],
  ];

  var ENCYCLOPEDIA = [
    ["🐦", "Birds", "bird", [
      ["Emperor Penguin", 4], ["Horned Puffin", 4], ["California Seagull", 4], ["Peruvian Pelican", 4],
      ["Great Albatross", 4], ["Osprey", 4], ["Magnificent Frigatebird", 2], ["Razorbill Auk", 2],
    ]],
    ["🦀", "Crustaceans", "crustacean", [
      ["Mantis Shrimp", 3], ["Spiny Lobster", 3], ["Lobster", 7], ["King Crab", 2], ["Hermit Crab", 2],
    ]],
    ["🪸", "Coral", "coral", [
      ["Staghorn Coral", 2], ["Deep Sea Coral", 2], ["Grooved Brain Coral", 2], ["Elk Horn Coral", 3],
    ]],
    ["🐠", "Game Fish", "gamefish", [
      ["Sailfish", 3], ["Goliath Grouper", 3], ["Tarpon", 4], ["Mahi Mahi", 4], ["Blue Marlin", 4],
      ["Bigeye Tuna", 5], ["Yellowfin Tuna", 14], ["Roosterfish", 5], ["King Salmon", 4],
      ["Whale Shark", 4], ["Barracuda", 3],
    ]],
    ["🐙", "Cephalopods", "cephalopod", [
      ["Bobtail Squid", 3], ["Common Octopus", 3], ["Cuttlefish", 3], ["Giant Squid", 3],
    ]],
    ["🐟", "Bait Fish", "baitfish", [
      ["Mullet", 4], ["Bunker", 4], ["Sardine", 4], ["Flying Fish", 4], ["Bonito", 4],
    ]],
    ["🌊", "Invertebrates", "invertebrate", [
      ["Sea Sponge", 2], ["Sea Urchin", 2], ["Sea Star", 2], ["Sea Cucumber", 2], ["Sea Anemone", 2],
    ]],
    ["🐋", "Mammals", "mammal", [
      ["Spinner Dolphin", 4], ["Bottlenose Dolphin", 4], ["Narwhal", 3],
    ]],
    ["🌎", "Crosscurrent", "crosscurrent", [
      ["Cleaner Wrasse", 2], ["Blue Tang", 3], ["Mandarin Goby", 4], ["Loggerhead Sea Turtle", 3],
      ["Clownfish", 3], ["Reef Trigger", 3], ["Manta Ray", 2], ["Great White Shark", 4],
    ]],
    ["🌊", "Ocean Cards", "ocean", [
      ["Coral Reef", 13], ["Deep Ocean", 8], ["Mangroves", 9], ["Artificial Reef", 6],
      ["Pier", 8], ["Arctic Ocean", 8], ["Kelp Forest", 9], ["Tide Pool", 6],
    ]],
  ];

  function encyclopediaHtml() {
    return ENCYCLOPEDIA.map(function (grp) {
      var total = grp[3].reduce(function (a, c) { return a + c[1]; }, 0);
      var rows = grp[3].map(function (c) {
        return '<li><span class="rb-enc-name">' + c[0] + '</span><span class="rb-enc-x">&times;' + c[1] + '</span></li>';
      }).join("");
      return '<div class="rb-enc-group" data-fam="' + grp[2] + '">'
        + '<div class="rb-enc-head"><span class="rb-enc-ico">' + grp[0] + '</span>'
        + '<span class="rb-enc-title">' + grp[1] + '</span>'
        + '<span class="rb-enc-total">' + total + ' cards</span></div>'
        + '<ul class="rb-enc-list">' + rows + '</ul></div>';
    }).join("");
  }

  var HTML = ''
    // ── Cover ─────────────────────────────────────────────────────
    + '<div class="rb-hero">'
    +   '<h1 class="rb-hero-title">Currents and Critters</h1>'
    +   '<div class="rb-hero-eyebrow">Rules of the Ocean</div>'
    +   '<p class="rb-hero-sub">The game you build ecosystems underneath the waves</p>'
    +   '<div class="rb-components">'
    +     '<span class="rb-comp-lbl">Components</span>'
    +     COMPONENTS.map(function (c) { return '<span class="rb-comp-chip">' + c + '</span>'; }).join("")
    +   '</div>'
    + '</div>'

    // ── Contents ──────────────────────────────────────────────────
    + '<nav class="rb-toc" aria-label="Rulebook contents">'
    +   SECTIONS.map(function (s, i) {
          return '<button type="button" class="rb-toc-btn" data-rb-jump="' + s[0] + '">'
            + '<span class="rb-toc-n">' + String(i + 1).padStart(2, "0") + '</span>' + s[1] + '</button>';
        }).join("")
    + '</nav>'

    // ── Objective ─────────────────────────────────────────────────
    + '<section class="rb-sec" id="rb-objective">'
    +   '<h2 class="rb-h2"><span class="rb-h2-ico">🎯</span>Objective</h2>'
    +   '<p class="rb-lead">Create the highest-scoring marine ecosystem before the END GAME card is revealed.</p>'
    +   '<div class="rb-block">'
    +     '<h3 class="rb-h3">Players earn points by:</h3>'
    +     '<ul class="rb-list">'
    +       '<li>Playing Oceans</li>'
    +       '<li>Playing Animals such as Mammals, Coral, Game fish, and other marine life, to create powerful card combinations and synergies you can use to win the game</li>'
    +     '</ul>'
    +   '</div>'
    +   '<div class="rb-block rb-block-gold">'
    +     '<h3 class="rb-h3">When the END GAME card is drawn:</h3>'
    +     '<ul class="rb-list">'
    +       '<li>Each player (including the one who drew it) takes one final turn</li>'
    +       '<li>All players total their points</li>'
    +       '<li>The highest score wins</li>'
    +     '</ul>'
    +   '</div>'
    + '</section>'

    // ── Setup ─────────────────────────────────────────────────────
    + '<section class="rb-sec" id="rb-setup">'
    +   '<h2 class="rb-h2"><span class="rb-h2-ico">🌊</span>Setup</h2>'
    +   '<div class="rb-steps">'
    +     '<div class="rb-step"><div class="rb-step-n">1</div><div class="rb-step-body">'
    +       '<h3 class="rb-h3">Prepare the Deck</h3>'
    +       '<ul class="rb-list"><li>Remove the END GAME card</li><li>Shuffle all remaining cards</li></ul>'
    +     '</div></div>'
    +     '<div class="rb-step"><div class="rb-step-n">2</div><div class="rb-step-body">'
    +       '<h3 class="rb-h3">Deal Starting Hands</h3>'
    +       '<ul class="rb-list"><li>Each player draws 8 cards</li>'
    +       '<li>If a player has no Ocean cards, they may discard and redraw 8 cards</li></ul>'
    +     '</div></div>'
    +     '<div class="rb-step"><div class="rb-step-n">3</div><div class="rb-step-body">'
    +       '<h3 class="rb-h3">Prepare the End Game</h3>'
    +       '<ul class="rb-list"><li>Take the bottom 15 cards from the deck</li>'
    +       '<li>Shuffle the END GAME card into them</li>'
    +       '<li>Place this stack at the bottom of the deck</li></ul>'
    +     '</div></div>'
    +     '<div class="rb-step"><div class="rb-step-n">4</div><div class="rb-step-body">'
    +       '<h3 class="rb-h3">Create Play Areas</h3>'
    +       '<ul class="rb-list"><li>Place the deck face down (Draw Pile)</li>'
    +       '<li>Create a space for the Discard Board (Pool)</li>'
    +       '<li>Finally, create a space for the graveyard</li></ul>'
    +     '</div></div>'
    +     '<div class="rb-step"><div class="rb-step-n">5</div><div class="rb-step-body">'
    +       '<h3 class="rb-h3">Choose First Player</h3>'
    +       '<ul class="rb-list"><li>The player who most recently visited an ocean goes first</li>'
    +       '<li>If the players don’t want to figure can’t or don’t want to figure that out, the youngest goes first</li>'
    +       '<li>The game goes clockwise</li></ul>'
    +     '</div></div>'
    +   '</div>'
    + '</section>'

    // ── Gameplay overview ─────────────────────────────────────────
    + '<section class="rb-sec" id="rb-gameplay">'
    +   '<h2 class="rb-h2"><span class="rb-h2-ico">🃏</span>Gameplay Overview</h2>'
    +   '<p class="rb-lead">On your turn, choose ONE:</p>'
    +   '<div class="rb-choose">'
    +     '<div class="rb-choice"><div class="rb-choice-ico">🎣</div>'
    +       '<h3 class="rb-h3">Draw</h3>'
    +       '<p class="rb-p">Draw 2 cards total from any combination of:</p>'
    +       '<ul class="rb-list"><li>The Draw Pile</li><li>The Pool (Discard Board)</li></ul>'
    +     '</div>'
    +     '<div class="rb-choice"><div class="rb-choice-ico">🐠</div>'
    +       '<h3 class="rb-h3">Play a Card</h3>'
    +       '<p class="rb-p">Play 1 card from your hand by:</p>'
    +       '<ul class="rb-list"><li>Placing an Ocean, or</li><li>Attaching a card to an existing Ocean</li></ul>'
    +       '<p class="rb-p rb-p-warn">You must pay the cost by discarding cards to the Pool.</p>'
    +     '</div>'
    +     '<div class="rb-choice"><div class="rb-choice-ico">⇄</div>'
    +       '<h3 class="rb-h3">Move an animal</h3>'
    +       '<p class="rb-p">Move 1 animal on your board by:</p>'
    +       '<ul class="rb-list"><li>Relocating it to a different Ocean</li></ul>'
    +       '<p class="rb-p rb-p-warn">The card must keep the same orientation (Up, Down, Left, or Right)</p>'
    +     '</div>'
    +   '</div>'
    +   '<div class="rb-block">'
    +     '<h3 class="rb-h3">End of Turn</h3>'
    +     '<ul class="rb-list"><li>If you have more than 10 cards, discard excess cards into the pool until you have 10</li>'
    +     '<li>Play passes clockwise</li></ul>'
    +   '</div>'
    + '</section>'

    // ── Card types ────────────────────────────────────────────────
    + '<section class="rb-sec" id="rb-cardtypes">'
    +   '<h2 class="rb-h2"><span class="rb-h2-ico">🌊</span>Card Types</h2>'
    +   '<h3 class="rb-h3 rb-h3-big">OCEANS <span class="rb-h3-tail">(Your Foundation)</span></h3>'
    +   '<ul class="rb-list rb-list-lg">'
    +     '<li>All cards must be attached to an Ocean</li>'
    +     '<li>Oceans are placed left to right</li>'
    +     '<li>Oceans cannot be moved once placed</li>'
    +   '</ul>'
    +   '<div class="rb-callout"><span class="rb-callout-lbl">When you play an Ocean:</span>'
    +     '<span class="rb-arrow">&rarr;</span> Flip 1 card from the deck into the Pool</div>'
    +   '<div class="rb-block">'
    +     '<h3 class="rb-h3">Placement Rules</h3>'
    +     '<p class="rb-p">Each Ocean has limited attachment spaces</p>'
    +     '<p class="rb-p rb-p-muted">Normally:</p>'
    +     '<ul class="rb-list rb-list-slots">'
    +       '<li><b>Top (Surface)</b> <span class="rb-arrow">&rarr;</span> Birds, Baitfish</li>'
    +       '<li><b>Bottom (Ocean Floor)</b> <span class="rb-arrow">&rarr;</span> Lobster, Crab, Goby, etc.</li>'
    +       '<li><b>Left/Right attachment slots</b> <span class="rb-arrow">&rarr;</span> Yellowfin tuna, Whale shark, Mahi Mahi, Etc.</li>'
    +     '</ul>'
    +   '</div>'
    +   oceanLayoutFigure()
    + '</section>'

    // ── Attachment rules ──────────────────────────────────────────
    + '<section class="rb-sec" id="rb-attachment">'
    +   '<h2 class="rb-h2"><span class="rb-h2-ico">🔗</span>Attachment Rules</h2>'
    +   '<ul class="rb-list rb-list-lg">'
    +     '<li>Only 1 card per space</li>'
    +     '<li><b>Exception:</b> Lobster and Yellowfin Tuna may share spaces with themselves (per their cards)</li>'
    +     '<li>Creatures may be played on any Ocean, unless the spot is occupied</li>'
    +   '</ul>'
    +   '<div class="rb-example">'
    +     '<div class="rb-example-lbl">Example</div>'
    +     '<div class="rb-example-cards">'
    +     figCard(hFace(12), "Lobster", "Any number of lobsters can share the same spot")
    +     figCard(vFace(107), "Yellowfin Tuna", "Any number of yellowfin may share the same spot")
    +     '</div>'
    +   '</div>'
    + '</section>'

    // ── Card anatomy ──────────────────────────────────────────────
    + '<section class="rb-sec" id="rb-anatomy">'
    +   '<h2 class="rb-h2"><span class="rb-h2-ico">🔍</span>Card Anatomy</h2>'
    +   '<p class="rb-lead">Each card includes:</p>'
    +   cardAnatomyFigure()
    + '</section>'

    // ── Star abilities ────────────────────────────────────────────
    + '<section class="rb-sec" id="rb-star">'
    +   '<h2 class="rb-h2"><span class="rb-h2-ico">★</span>Star Abilities</h2>'
    +   '<p class="rb-p">Some cards have an optional ★ ability.</p>'
    +   '<p class="rb-p">When you play a card with a ★ ability, you may activate it by discarding at least one card from your hand with the same symbol as the card you played. Discard that card to the Pool (if it costs more than one, you have to pay the cost, but not all the cards have to match).</p>'
    +   '<p class="rb-p">If you do not discard a matching symbol, you may still play the card, but you do not activate its ★ ability.</p>'
    +   '<p class="rb-p">If an ability allows you to play a free Animal, the free Animal must come from your hand. It can not come from the Pool, the deck, or the discard pile.</p>'
    +   '<div class="rb-quote">A card says “★ Play a free crustacean ★.”<br>'
    +     'To use this ability, you must first activate the ★ ability by discarding a matching symbol. Then immediately, you may play one crustacean from your hand to an available spot on the board.</div>'
    +   '<div class="rb-example">'
    +     '<div class="rb-example-lbl">Example</div>'
    +     '<p class="rb-example-txt">Have five cards in your hand and play the cleanser wrasse, and show a free crustacean</p>'
    +     '<div class="rb-example-cards">'
    +     figCard(hFace(30), "Cleanser Wrasse", "Play one free crustacean ★")
    +     '<div class="rb-example-plus">★</div>'
    +     figCard(hFace(12), "Lobster", "the free crustacean, from your hand")
    +     '</div>'
    +   '</div>'

    +   '<h3 class="rb-h3 rb-h3-big">★ Play Again</h3>'
    +   '<p class="rb-p">If a ★ ability says “Play Again,” the extra play is treated as a new play.</p>'
    +   '<p class="rb-p">For that new play:</p>'
    +   '<ul class="rb-list"><li>You can do all of the allowed actions. '
    +     '<button type="button" class="rb-inline-jump" data-rb-jump="rb-gameplay">Refer to page three for actions.</button></li></ul>'
    +   '<div class="rb-example">'
    +     '<div class="rb-example-lbl">Example</div>'
    +     '<p class="rb-example-txt">Playing a horned puffin and playing again</p>'
    +     '<div class="rb-example-cards">'
    +     figCard(hFace(11), "Horned Puffin", "+3 · play again ★")
    +     '</div>'
    +   '</div>'

    +   '<div class="rb-block rb-block-warn">'
    +     '<h3 class="rb-h3">Important Rules</h3>'
    +     '<ul class="rb-list">'
    +       '<li>★ abilities must be used immediately</li>'
    +       '<li>They cannot be saved for later turns</li>'
    +       '<li>“Play a free animal” = from your hand only</li>'
    +       '<li>Not from the deck</li>'
    +       '<li>Not from the pool</li>'
    +     '</ul>'
    +   '</div>'
    +   '<div class="rb-block">'
    +     '<h3 class="rb-h3">Pool Restriction</h3>'
    +     '<p class="rb-p">If an ability allows you to take cards from the Pool:</p>'
    +     '<ul class="rb-list"><li>You cannot take back both cards you just discarded</li>'
    +     '<li>You may take back only one of them</li></ul>'
    +   '</div>'
    + '</section>'

    // ── The Pool ──────────────────────────────────────────────────
    + '<section class="rb-sec" id="rb-pool">'
    +   '<h2 class="rb-h2"><span class="rb-h2-ico">🌀</span>The Pool <span class="rb-h2-tail">(Discard Board)</span></h2>'
    +   '<ul class="rb-list rb-list-lg"><li>All discarded cards go here</li><li>Players may draw from it</li></ul>'
    +   '<div class="rb-block rb-block-warn">'
    +     '<h3 class="rb-h3">Limit Rule</h3>'
    +     '<p class="rb-p">When the Pool reaches 10 cards:</p>'
    +     '<ul class="rb-list"><li>Move all cards to a face-down discard pile</li><li>Reset the Pool</li></ul>'
    +   '</div>'
    +   poolFigure()
    + '</section>'

    // ── Special rules ─────────────────────────────────────────────
    + '<section class="rb-sec" id="rb-special">'
    +   '<h2 class="rb-h2"><span class="rb-h2-ico">✨</span>Special Rules</h2>'

    +   '<div class="rb-block">'
    +     '<h3 class="rb-h3">Animal Movement</h3>'
    +     '<ul class="rb-list"><li>You may move one Animal card from one ocean to another ocean</li>'
    +     '<li>This uses your turn</li>'
    +     '<li>You can not move oceans on your turn, only one animal</li></ul>'
    +   '</div>'

    +   '<div class="rb-block">'
    +     '<h3 class="rb-h3">End Game Edge Cases</h3>'
    +     '<ul class="rb-list">'
    +       '<li><b>If END GAME is drawn while drawing:</b> You may finish drawing</li>'
    +       '<li><b>If END GAME enters the Pool:</b> All players still get one final turn, including the person it flipped out from</li>'
    +       '<li>Extra plays (Play Again, Turtles, etc.) still work during final turns</li>'
    +     '</ul>'
    +   '</div>'

    +   '<div class="rb-block rb-block-gold">'
    +     '<h3 class="rb-h3">“Shooting the Moon” <span class="rb-h3-tail">(Mandarin Goby Bonus)</span></h3>'
    +     '<p class="rb-p">If you play all 4 Mandarin Gobies:</p>'
    +     '<p class="rb-big-award"><span class="rb-arrow">&rarr;</span> Gain +20 points</p>'
    +     '<p class="rb-p">Send a photo of you and the board to get featured on Currents and Critters Instagram: '
    +       '<a class="rb-link" href="https://www.instagram.com/currentsandcritters_official/" target="_blank" rel="noopener noreferrer">@CurrentsandCritters_official</a></p>'
    +   '</div>'
    +   gobyFigure()

    +   '<div class="rb-block">'
    +     '<h3 class="rb-h3">Leaving the Table</h3>'
    +     '<p class="rb-p">If the player who plays after you leaves the table without saying “Surf’s Up,” you may draw two cards for them on their turn.</p>'
    +     '<p class="rb-p">If they say “Surf’s Up” at any time when they are away from the game, their turn is paused with no penalty.</p>'
    +   '</div>'
    + '</section>'

    // ── End game ──────────────────────────────────────────────────
    + '<section class="rb-sec" id="rb-endgame">'
    +   '<h2 class="rb-h2"><span class="rb-h2-ico">🏁</span>End Game</h2>'
    +   '<p class="rb-lead">When the END GAME card is revealed:</p>'
    +   '<div class="rb-steps rb-steps-flat">'
    +     '<div class="rb-step"><div class="rb-step-n">1</div><div class="rb-step-body"><p class="rb-p">Finish the current turn</p></div></div>'
    +     '<div class="rb-step"><div class="rb-step-n">2</div><div class="rb-step-body"><p class="rb-p">Each player takes one final turn</p></div></div>'
    +     '<div class="rb-step"><div class="rb-step-n">3</div><div class="rb-step-body"><p class="rb-p">Proceed to scoring</p></div></div>'
    +   '</div>'
    + '</section>'

    // ── Scoring ───────────────────────────────────────────────────
    + '<section class="rb-sec" id="rb-scoring">'
    +   '<h2 class="rb-h2"><span class="rb-h2-ico">🧮</span>Scoring</h2>'
    +   '<p class="rb-lead">Add:</p>'
    +   '<ul class="rb-list rb-list-lg"><li>All printed points</li><li>All ability bonuses</li></ul>'
    +   '<div class="rb-callout rb-callout-tip"><span class="rb-callout-lbl">Tip:</span> count bottoms cards, top cards, right, Left, Then oceans</div>'
    +   '<div class="rb-total">'
    +     '<div class="rb-total-eq">Total across all Oceans = final score</div>'
    +     '<div class="rb-total-win">Highest score wins</div>'
    +   '</div>'
    +   '<a class="rb-snap" href="https://score.currentsandcritters.com/" target="_blank" rel="noopener noreferrer">'
    +     '<span class="rb-snap-ico">📸</span>'
    +     '<span class="rb-snap-txt"><b>HAVING TROUBLE COUNTING:</b> click this link and take a picture to count your points</span>'
    +   '</a>'
    + '</section>'

    // ── Competitive play ──────────────────────────────────────────
    + '<section class="rb-sec" id="rb-competitive">'
    +   '<h2 class="rb-h2"><span class="rb-h2-ico">⚔️</span>Competitive Play <span class="rb-h2-tail">(2-Player Variant)</span></h2>'
    +   '<p class="rb-p">In 1 vs 1 games, players may choose to play using the Advanced variant.</p>'
    +   '<p class="rb-p">In this mode, each player controls two separate hands. Players may only look at and play from one hand at a time, and must complete their turn before switching to the next hand.</p>'
    +   '<p class="rb-p">Turns alternate between hands in the following order:</p>'
    +   '<ol class="rb-order">'
    +     '<li><span class="rb-order-p rb-order-p1">Player 1</span> <span class="rb-order-dash">–</span> Hand 1</li>'
    +     '<li><span class="rb-order-p rb-order-p2">Player 2</span> <span class="rb-order-dash">–</span> Hand 1</li>'
    +     '<li><span class="rb-order-p rb-order-p1">Player 1</span> <span class="rb-order-dash">–</span> Hand 2</li>'
    +     '<li><span class="rb-order-p rb-order-p2">Player 2</span> <span class="rb-order-dash">–</span> Hand 2</li>'
    +   '</ol>'
    +   '<p class="rb-p">This system introduces a strategic layer where players can attempt to set up cards in the pool for their other hand to collect. However, because the opponent takes a turn between your hands, they may take those cards before you can.</p>'
    + '</section>'

    // ── Encyclopedia ──────────────────────────────────────────────
    + '<section class="rb-sec" id="rb-encyclopedia">'
    +   '<h2 class="rb-h2"><span class="rb-h2-ico">📖</span>Encyclopedia</h2>'
    +   '<div class="rb-enc">' + encyclopediaHtml() + '</div>'
    + '</section>'

    + '<div class="rb-end">≈ Currents <span>&amp;</span> Critters ≈</div>';

  window.CC_RULEBOOK_HTML = HTML;

  /**
   * Wire the contents nav inside a freshly-rendered rulebook. Jumps scroll the
   * nearest scrolling ancestor rather than the whole page, so the same markup
   * behaves inside the Player Home panel and inside the in-game modal.
   */
  window.ccRulebookInit = function (rootEl) {
    if (!rootEl || rootEl.__rbWired) return;
    rootEl.__rbWired = true;
    rootEl.addEventListener("click", function (ev) {
      var btn = ev.target.closest("[data-rb-jump]");
      if (!btn || !rootEl.contains(btn)) return;
      var target = rootEl.querySelector("#" + btn.getAttribute("data-rb-jump"));
      if (!target) return;
      var scroller = rootEl;
      while (scroller && scroller !== document.body) {
        var oy = getComputedStyle(scroller).overflowY;
        if ((oy === "auto" || oy === "scroll") && scroller.scrollHeight > scroller.clientHeight) break;
        scroller = scroller.parentElement;
      }
      if (!scroller || scroller === document.body) {
        target.scrollIntoView({ behavior: "smooth", block: "start" });
      } else {
        var top = target.getBoundingClientRect().top - scroller.getBoundingClientRect().top + scroller.scrollTop - 12;
        scroller.scrollTo({ top: top, behavior: "smooth" });
      }
      target.classList.remove("rb-flash");
      // Force a reflow so the highlight replays when the same section is
      // picked twice in a row.
      void target.offsetWidth;
      target.classList.add("rb-flash");
    });
  };
})();
