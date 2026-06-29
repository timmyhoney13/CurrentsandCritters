const fs = require('fs');

const RENDER_URL = 'https://play.currentsandcritters.com';

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
