/* Clan System — client module smoke test (node vm, no browser).
 *
 * Loads the real js/clans-ui.js against a stub __ccClans bridge and checks the
 * public hooks the rest of the app depends on:
 *   • module registers window.__ccClansRender / __ccClanClaimGame /
 *     __ccClanTradePoint / __ccClanInvite / __ccClanCanInvite
 *   • end-game claim posts /api/clan/claim-game ONCE per room (session dedup),
 *     uppercases the room id, and toasts the earned points + daily-goal bonus
 *   • trade-point hook toasts
 *   • disabled bridge → module registers nothing (hard off-switch)
 *
 * Run:  node test_clans_ui.js
 */
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const SRC = fs.readFileSync(path.join(__dirname, "multiplayer/client/js/clans-ui.js"), "utf8");

let pass = 0, fail = 0;
function check(name, cond) {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { fail++; console.log("  ✗ FAIL: " + name); }
}

function makeEnv({ enabled = true, postResp } = {}) {
  const calls = [];
  const toasts = [];
  const bridge = {
    ENABLED: enabled,
    APP_BUILD: "test",
    get: async () => ({ ok: true }),
    post: async (p, b) => { calls.push({ p, b }); return typeof postResp === "function" ? postResp(p, b) : (postResp || { ok: true }); },
    toast: (m, t) => toasts.push({ m: String(m), t }),
    nickname: () => "Tester",
    authUser: () => ({ uid: "u1", getIdToken: async () => "tok" }),
    idToken: async () => "tok",
    avSrc: (u) => u,
    animalAvatars: () => [{ id: "clownfish", name: "Clownfish", img: "/avatars/clownfish.png" }],
  };
  const documentStub = {
    querySelector: () => null,
    getElementById: () => null,
    createElement: () => ({ style: {}, classList: { add() {}, remove() {}, toggle() {} },
                            appendChild() {}, addEventListener() {}, setAttribute() {} }),
    head: { appendChild() {} },
    body: { appendChild() {}, removeChild() {}, contains: () => false },
  };
  const windowStub = { __ccClans: bridge };
  const sandbox = { window: windowStub, document: documentStub, console,
                    setInterval: () => 0, clearInterval: () => {},
                    setTimeout: (f) => 0, clearTimeout: () => {},
                    location: { search: "" }, confirm: () => true, Date, Math, JSON, Number, String };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(SRC, sandbox, { filename: "clans-ui.js" });
  return { sandbox, windowStub, calls, toasts };
}

(async () => {
  console.log("module registration:");
  const env = makeEnv({
    postResp: (p) => p.endsWith("claim-game")
      ? { ok: true, points: 2, clan_name: "Reef Riders", goal_done: true }
      : { ok: true },
  });
  const W = env.windowStub;
  check("__ccClansRender registered", typeof W.__ccClansRender === "function");
  check("__ccClanClaimGame registered", typeof W.__ccClanClaimGame === "function");
  check("__ccClanTradePoint registered", typeof W.__ccClanTradePoint === "function");
  check("__ccClanInvite registered", typeof W.__ccClanInvite === "function");
  check("__ccClanCanInvite registered", typeof W.__ccClanCanInvite === "function");

  console.log("end-game claim:");
  await W.__ccClanClaimGame("abcd");
  const claims = env.calls.filter(c => c.p === "/api/clan/claim-game");
  check("posts one claim", claims.length === 1);
  check("room id uppercased", claims[0] && claims[0].b.room_id === "ABCD");
  check("idToken attached", claims[0] && claims[0].b.idToken === "tok");
  check("points toast shown", env.toasts.some(t => t.m.includes("+2 Clan Point")));
  check("daily-goal toast shown", env.toasts.some(t => t.m.includes("Daily Goal")));
  await W.__ccClanClaimGame("abcd");
  await W.__ccClanClaimGame("ABCD");
  check("same room never claimed twice", env.calls.filter(c => c.p === "/api/clan/claim-game").length === 1);

  console.log("claim retry on a dead network:");
  let flaky = 0;
  const env2 = makeEnv({
    postResp: (p) => {
      if (!p.endsWith("claim-game")) return { ok: true };
      flaky++;
      return flaky === 1 ? null : { ok: true, points: 3, clan_name: "Reef Riders" };
    },
  });
  await env2.windowStub.__ccClanClaimGame("ROOM");
  check("no toast when the request never landed", !env2.toasts.some(t => t.m.includes("Clan Point")));
  await env2.windowStub.__ccClanClaimGame("ROOM");
  check("a dropped request can be retried", flaky === 2);
  check("retry awards the points", env2.toasts.some(t => t.m.includes("+3 Clan Point")));
  await env2.windowStub.__ccClanClaimGame("ROOM");
  check("but a landed claim is still one-and-done", flaky === 2);

  console.log("trade point hook:");
  W.__ccClanTradePoint(1);
  check("trade toast shown", env.toasts.some(t => t.m.includes("daily clan trade")));

  console.log("invite permission probe:");
  const before = env.calls.length;
  const can = W.__ccClanCanInvite();
  check("unknown state returns false", can === false);
  await new Promise(r => setImmediate(r));
  check("and primes /api/clan/home in the background",
        env.calls.slice(before).some(c => c.p === "/api/clan/home"));

  console.log("hard off-switch:");
  const off = makeEnv({ enabled: false });
  check("disabled bridge registers nothing", typeof off.windowStub.__ccClansRender !== "function");

  console.log(`\n${"=".repeat(40)}\nRESULT: ${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
