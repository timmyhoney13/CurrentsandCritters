const fs = require('fs');

// Overridable only so test_site_stats.js can point the stat-floor refresh at a
// stub instead of the live server. Production never sets it.
const RENDER_URL = process.env.CC_RENDER_URL || 'https://play.currentsandcritters.com';

// 1. Write firebase-config.js from env vars
const cfg = {
  apiKey:            process.env.VITE_FIREBASE_API_KEY             || "",
  authDomain:        process.env.VITE_FIREBASE_AUTH_DOMAIN         || "",
  projectId:         process.env.VITE_FIREBASE_PROJECT_ID          || "",
  storageBucket:     process.env.VITE_FIREBASE_STORAGE_BUCKET      || "",
  messagingSenderId: process.env.VITE_FIREBASE_MESSAGING_SENDER_ID || "",
  appId:             process.env.VITE_FIREBASE_APP_ID              || "",
};
fs.writeFileSync('firebase-config.js', `window.__FISH_FIREBASE_CONFIG = ${JSON.stringify(cfg, null, 2)};\n`, 'utf8');
console.log('firebase-config.js written.');

// 2. Rewrite all game links in index.html to point to Render
let html = fs.readFileSync('index.html', 'utf8');
const before = html;

// Replace any href that points to /game, /preview, /play/... with the Render URL
html = html.replace(/href="\/game"/g,        `href="${RENDER_URL}"`);
html = html.replace(/href="\/preview"/g,     `href="${RENDER_URL}"`);
html = html.replace(/href="\/play\/[^"]*"/g, `href="${RENDER_URL}"`);

if (html !== before) {
  fs.writeFileSync('index.html', html, 'utf8');
  console.log('index.html: game links rewritten to Render URL.');
} else {
  console.log('index.html: game links already correct.');
}

// 3. Refresh the homepage's stat floors from the live server.
//
// The floors (FLOOR_GAMES / FLOOR_PLAYERS / FLOOR_HOURS in index.html) are
// what a visitor reads while the free-tier game server wakes up, and nearly
// every first visit is one of those. Hard-coded by hand, they go stale the
// moment anybody plays: on 2026-10-24 the page published 101 games, 18 players
// and 0 hours while the server was serving 561, 106 and 916. A stale floor is
// not a cosmetic problem, it is a wrong number on a public page.
//
// So every deploy asks the server and writes the answer in. Two rules:
//
//   UPWARD ONLY. A number that is already true cannot become less true, and
//   the server's stats file lives on a disk that is not in force on this plan,
//   so it HAS reset to a seed before. A build must not be able to publish a
//   smaller figure than the one in the repo.
//
//   NEVER FAIL THE BUILD. A hibernating, slow or broken server leaves the
//   committed values exactly as they are and says so in the log. The whole
//   site deploying is worth more than the freshness of three numbers.
const FLOOR_SOURCES = [
  ['FLOOR_GAMES',   d => Number(d.games_played)],
  ['FLOOR_PLAYERS', d => (Number.isFinite(Number(d.players_total))
                            ? Number(d.players_total)
                            : Number(d.registered_players))],
  // Whole hours, floored, which is what the page shows above 10 hours.
  ['FLOOR_HOURS',   d => Math.floor(Number(d.play_seconds) / 3600)],
];

async function refreshStatFloors() {
  let data = null;
  try {
    const ctrl = new AbortController();
    const kill = setTimeout(() => ctrl.abort(), 25000);
    const resp = await fetch(`${RENDER_URL}/api/stats`, { signal: ctrl.signal });
    clearTimeout(kill);
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    data = await resp.json();
  } catch (err) {
    console.log('stat floors: left as committed (' + ((err && err.message) || err) + ')');
    return;
  }

  let out = fs.readFileSync('index.html', 'utf8');
  let changed = false;
  for (const [name, pick] of FLOOR_SOURCES) {
    const found = out.match(new RegExp('(const ' + name + '\\s*=\\s*)(\\d+)'));
    if (!found) { console.log('stat floors: no ' + name + ' in index.html'); continue; }
    const live = pick(data || {});
    const have = Number(found[2]);
    if (!Number.isFinite(live) || live <= have) {
      console.log('stat floors: ' + name + ' stays ' + have +
                  ' (live ' + (Number.isFinite(live) ? live : 'unknown') + ')');
      continue;
    }
    out = out.replace(found[0], found[1] + live);
    changed = true;
    console.log('stat floors: ' + name + ' ' + have + ' -> ' + live);
  }
  if (changed) {
    fs.writeFileSync('index.html', out, 'utf8');
    console.log('index.html: stat floors refreshed from the live server.');
  }
}

// 4. Refresh the plushy vote's standings snapshot from the live server.
//
// TALLY_SNAPSHOT in index.html is what a reader sees in the first frame,
// before the free-tier server has woken up and answered /api/plush/tally.
// The page's three layers are snapshot -> this browser's cached tally ->
// live, newest wins, so this one only ever has to be true at deploy time.
//
// Same two rules as the floors, for the same reasons:
//
//   NEVER GO BACKWARDS. A snapshot with FEWER ballots in it than the one in
//   the repo is a server that has lost its records, not a poll that shrank,
//   and publishing it would take real votes off a public page.
//
//   NEVER FAIL THE BUILD. A sleeping server leaves the committed snapshot
//   alone and says so.
async function refreshPlushSnapshot() {
  let data = null;
  try {
    const ctrl = new AbortController();
    const kill = setTimeout(() => ctrl.abort(), 25000);
    const resp = await fetch(`${RENDER_URL}/api/plush/tally`, { signal: ctrl.signal });
    clearTimeout(kill);
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    data = await resp.json();
  } catch (err) {
    console.log('plush snapshot: left as committed (' + ((err && err.message) || err) + ')');
    return;
  }
  if (!data || !data.ok || !data.counts || !Object.keys(data.counts).length) {
    console.log('plush snapshot: left as committed (server has no counts to give)');
    return;
  }

  const out = fs.readFileSync('index.html', 'utf8');
  const found = out.match(/(const TALLY_SNAPSHOT = )(\{[\s\S]*?\});/);
  if (!found) {
    console.log('plush snapshot: no TALLY_SNAPSHOT in index.html');
    return;
  }
  let have = { ballots: 0 };
  try { have = JSON.parse(found[2]); } catch (_) { /* rewrite a broken one */ }
  const live = Number(data.ballots) || 0;
  if (live < (Number(have.ballots) || 0)) {
    console.log('plush snapshot: stays at ' + have.ballots + ' ballots (live ' + live + ')');
    return;
  }

  // Counts only. The tally endpoint is already the public shape, and never
  // carries an address or anything anybody typed, but this writes the three
  // fields the page reads and nothing else, so a future field on the
  // endpoint cannot end up baked into a public page by accident.
  const snap = JSON.stringify({
    ballots: live, counts: data.counts, firsts: data.firsts || {}
  });
  if (snap === found[2]) {
    console.log('plush snapshot: already current at ' + live + ' ballots');
    return;
  }
  fs.writeFileSync('index.html', out.replace(found[0], found[1] + snap + ';'), 'utf8');
  console.log('plush snapshot: ' + (have.ballots || 0) + ' -> ' + live + ' ballots refreshed.');
}

// In series, not in parallel: both rewrite index.html, and two reads of the
// same file either side of one write lose whichever change was read first.
refreshStatFloors()
  .catch(err => {
    // Belt and braces: nothing about three numbers may take the deploy down.
    console.log('stat floors: skipped (' + ((err && err.message) || err) + ')');
  })
  .then(() => refreshPlushSnapshot())
  .catch(err => {
    console.log('plush snapshot: skipped (' + ((err && err.message) || err) + ')');
  });
