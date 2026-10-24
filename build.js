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

refreshStatFloors().catch(err => {
  // Belt and braces: nothing about three numbers may take the deploy down.
  console.log('stat floors: skipped (' + ((err && err.message) || err) + ')');
});
