#!/usr/bin/env node
/* The game's name is written out: "Currents and Critters", and "CandC" when it
 * has to be short. Never "Currents & Critters", never "C&C".
 *
 * Run:  node test_game_name.js
 *
 * Reads every page, script, stylesheet and manifest a player can load, the
 * marketing site, and the code that writes the newsletter emails, and fails on
 * any ampersand spelling of the name in any of the forms it has hidden in
 * before: a bare &, &amp;, an & wrapped in its own <span> for colour (the side
 * menu's logo, the rulebook's last line, the lobby title), %26 in a URL, and
 * & in a string.
 *
 * Deliberately NOT scanned: multiplayer_server.py, which still has to MATCH
 * "Currents & Critters Online Username", the label on Stripe payment links that
 * went out before the rename.
 */
"use strict";

const fs = require("fs");
const path = require("path");

const ROOT = __dirname;
const AMP = String.raw`(?:&|&amp;|&#38;|&#x26;|\\u0026|%26)`;
const TAG = String.raw`(?:\s*<[^>]{0,60}>\s*)?`;
const BAD = [
  ["Currents & Critters", new RegExp(String.raw`Currents\s*${TAG}${AMP}${TAG}\s*Critters`, "i")],
  ["C&C",                 new RegExp(String.raw`\bC\s*${TAG}${AMP}${TAG}\s*C\b`)],
  ["≈ around the name",   /≈\s*Currents|Critters\s*(?:<[^>]*>\s*)?≈/i],
];

const TEXT = /\.(html|js|css|json|webmanifest|txt|svg)$/i;
function walk(dir, out) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) walk(p, out);
    else if (TEXT.test(e.name)) out.push(p);
  }
  return out;
}

const files = walk(path.join(ROOT, "multiplayer/client"), [])
  .concat(["index.html", "shop.html", "score.html", "newsletter_email.py", "newsletter_server.py", "render.yaml"]
    .map(f => path.join(ROOT, f)).filter(f => fs.existsSync(f)));

let fails = 0;
let scanned = 0;
for (const file of files) {
  // The icons carry a base64 PNG, which can spell anything by accident.
  const text = fs.readFileSync(file, "utf8").replace(/base64,[A-Za-z0-9+/=]+/g, "");
  scanned++;
  text.split("\n").forEach((line, i) => {
    for (const [name, re] of BAD) {
      if (re.test(line)) {
        fails++;
        console.log(`FAIL  ${path.relative(ROOT, file)}:${i + 1}  ${name}:  ${line.trim().slice(0, 140)}`);
      }
    }
  });
}

const logo = fs.readFileSync(path.join(ROOT, "multiplayer/client/preview.html"), "utf8")
  .match(/<div class="ph-sidebar-logo">([\s\S]*?)<\/div>/);
const logoText = logo && logo[1].replace(/<[^>]*>/g, "").trim();
if (logoText === "Currents and Critters") console.log("PASS  the side menu logo reads Currents and Critters");
else { fails++; console.log(`FAIL  the side menu logo reads ${JSON.stringify(logoText)}`); }

const css = fs.readFileSync(path.join(ROOT, "multiplayer/client/css/preview.css"), "utf8");
if (/\.ph-sidebar-logo::after\s*\{[^}]*content:\s*"CandC"/.test(css)) console.log("PASS  the collapsed rail shows CandC");
else { fails++; console.log("FAIL  the collapsed rail's logo is not CandC"); }

console.log(fails ? `\n${fails} failure(s) across ${scanned} files` : `\nPASS  no & spelling of the name in ${scanned} files`);
process.exit(fails ? 1 : 0);
