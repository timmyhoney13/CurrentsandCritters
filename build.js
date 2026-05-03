const fs = require('fs');

const cfg = {
  apiKey:            process.env.VITE_FIREBASE_API_KEY             || "",
  authDomain:        process.env.VITE_FIREBASE_AUTH_DOMAIN         || "",
  projectId:         process.env.VITE_FIREBASE_PROJECT_ID          || "",
  storageBucket:     process.env.VITE_FIREBASE_STORAGE_BUCKET      || "",
  messagingSenderId: process.env.VITE_FIREBASE_MESSAGING_SENDER_ID || "",
  appId:             process.env.VITE_FIREBASE_APP_ID              || "",
};

const out = `window.__FISH_FIREBASE_CONFIG = ${JSON.stringify(cfg, null, 2)};\n`;
fs.writeFileSync('firebase-config.js', out, 'utf8');
console.log('firebase-config.js written.');
