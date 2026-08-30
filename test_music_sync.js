#!/usr/bin/env node
/* The theme song plays on ONE clock for everybody in the room.
 * (multiplayer/client/js/preview-app.js)
 *
 * Run:  node test_music_sync.js
 *
 * Every client used to call startThemeSong() and get the track from the top,
 * so the four people in a game were four different distances into the same
 * song, and it began at whatever second each of them walked in. A refresh, a
 * rejoin, coming back to a backgrounded tab, or nudging the volume slider off
 * zero each restarted it alone. On a tab nobody had clicked yet the browser
 * held the audio back entirely and then let go at some unrelated click, which
 * is where "the music just started on its own" came from.
 *
 * The room now publishes one timeline (room.music_epoch_ms + server_now_ms,
 * pinned server-side by test_music_sync.py) and the client rides it:
 *
 *   - where in the loop to be is (server now - epoch) modulo track length,
 *     the same number on every device however late it joined
 *   - the server's clock is estimated from a TIMED poll, so a device whose
 *     own clock is wrong still lands on the beat
 *   - drift is held with a 0.5% speed nudge, and only a real dislocation
 *     (a frozen tab, a moved epoch) is allowed to re-seat the loop
 *   - a suspended context never starts a source; it waits for a gesture and
 *     then joins the timeline where the room already is
 *
 * The whole audio block is lifted out of the file and run against stubs, so a
 * rename or a reshape fails loudly here rather than quietly testing nothing.
 */
"use strict";

const fs = require("fs");
const path = require("path");

const APP = fs.readFileSync(
  path.join(__dirname, "multiplayer", "client", "js", "preview-app.js"), "utf8");

let failures = 0, checks = 0;
function ok(cond, label) {
  checks++;
  if (cond) return;
  failures++; console.log("  ✗ " + label);
}
function near(actual, expected, tol, label) {
  ok(Math.abs(actual - expected) <= tol,
     label + "  (got " + actual + ", want " + expected + " +/-" + tol + ")");
}
function eq(actual, expected, label) {
  ok(actual === expected,
     label + "  (got " + JSON.stringify(actual) + ", want " + JSON.stringify(expected) + ")");
}

// ── Lift the audio block out of the module ──────────────────────────────────
function slice(from, to) {
  const s = APP.indexOf(from);
  if (s < 0) throw new Error("could not find the start of the audio block: " + from);
  const e = APP.indexOf(to, s);
  if (e < 0) throw new Error("could not find the end of the audio block: " + to);
  return APP.slice(s, e);
}
const AUDIO = slice("  let _themeAudioCtx = null;", "  // ── Spectator state ─");
["function themeTargetOffset(", "function noteServerClock(", "function noteMusicEpoch(",
 "function _themeResync(", "function _themeReseek(", "function _armThemeGesture(",
 "function startThemeSong(", "function stopThemeSong("].forEach(sig => {
  if (AUDIO.indexOf(sig) < 0) throw new Error("audio block no longer contains " + sig);
});

const TRACK_SEC = 96;              // stand-in for the real theme's length
const EPOCH_SEC = 1_699_999_000;   // when the room's timeline starts

// ── A world we control: clocks, an audio device, a room ─────────────────────
function harness(opts) {
  opts = opts || {};
  const clock = { wall: opts.wall || 1_700_000_000_000, ctx: 0, serverAhead: opts.serverAhead || 0 };
  const log = { started: [], stopped: 0, timers: 0 };
  const gate = { allowed: opts.ctxState !== "suspended" };
  const store = new Map([["fish_music_volume", String(opts.volume === undefined ? 100 : opts.volume)]]);

  function Gain() {
    return {
      gain: {
        value: 0,
        setValueAtTime() {}, linearRampToValueAtTime() {},
        cancelScheduledValues() {}, setTargetAtTime() {},
      },
      connect(x) { return x; },
      disconnect() {},
    };
  }
  function Source() {
    const s = {
      buffer: null, loop: false, playbackRate: { value: 1 }, onended: null,
      connect(x) { return x; },
      start(_when, offset) { s.startedAt = offset; log.started.push(offset); },
      stop() { log.stopped++; },
      disconnect() {},
    };
    return s;
  }
  const BUFFER = { duration: TRACK_SEC };
  const ctx = {
    state: opts.ctxState || "running",
    get currentTime() { return clock.ctx; },
    destination: {},
    createBufferSource: Source,
    createGain: Gain,
    decodeAudioData(_arr, resolve) { resolve(BUFFER); return null; },
    resume() { ctx.state = gate.allowed ? "running" : "suspended"; return Promise.resolve(); },
  };

  const listeners = {};
  const document = {
    hidden: false,
    addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
    removeEventListener(type, fn) {
      listeners[type] = (listeners[type] || []).filter(f => f !== fn);
    },
  };
  const window = { AudioContext: function () { return ctx; } };
  const Date_ = { now: () => clock.wall };
  // Time only moves when the code under test asks to wait, so every test is
  // deterministic and instant.
  function setTimeout_(fn, ms) {
    clock.wall += (ms || 0);
    clock.ctx += (ms || 0) / 1000;
    fn();
    return 1;
  }
  function setInterval_() { log.timers++; return { interval: true }; }
  function clearInterval_() { log.timers--; }

  const api = new Function(
    "document", "window", "Date", "setTimeout", "setInterval", "clearInterval",
    "localStorage", "fetch", "APP_BUILD", "activeApiBaseUrl", "__ctx", "__buffer",
    // The module state the audio block reads from around it.
    "let roomId = null; let _spectatorRoomId = '';\n" +
    AUDIO +
    "\nreturn {\n" +
    "  noteServerClock, serverNowMs, noteMusicEpoch, musicEpochMs, themeTargetOffset,\n" +
    "  startThemeSong, stopThemeSong, _themeResync, _themePlayhead, _themeDrift,\n" +
    "  enterRoom: (id) => { roomId = id; },\n" +
    "  leaveRoom: () => { roomId = null; _spectatorRoomId = ''; },\n" +
    "  spectate: (id) => { _spectatorRoomId = id; },\n" +
    "  loadBuffer: () => { _themeAudioCtx = __ctx; _themeBuffer = __buffer; },\n" +
    "  seat: (offset) => { _themeAudioCtx = __ctx; _themeBuffer = __buffer;\n" +
    "                      _themeSource = __ctx.createBufferSource();\n" +
    "                      _themeGain = __ctx.createGain();\n" +
    "                      _themeStartedOffset = offset; _themeStartedAtCtx = __ctx.currentTime;\n" +
    "                      _themeRate = 1; },\n" +
    "  rate: () => _themeRate,\n" +
    "  playing: () => !!_themeSource,\n" +
    "  syncTimers: () => (_themeSyncTimer ? 1 : 0),\n" +
    "  skew: () => _srvSkewMs,\n" +
    "};"
  )(
    document, window, Date_, setTimeout_, setInterval_, clearInterval_,
    { getItem: k => (store.has(k) ? store.get(k) : null), setItem: (k, v) => store.set(k, v) },
    async () => ({ ok: true, arrayBuffer: async () => new ArrayBuffer(8) }),
    "test", () => "",
    ctx, BUFFER
  );

  // One timed poll, exactly as refreshState does it: send, wait, then fold the
  // reply's send time in with the round trip we just measured.
  api.poll = (rttMs, epochSec, room) => {
    const sentAt = clock.wall;
    clock.wall += rttMs;
    clock.ctx += rttMs / 1000;
    const serverNow = clock.wall + clock.serverAhead - rttMs / 2; // written mid-flight
    api.noteServerClock(serverNow, clock.wall - sentAt, clock.wall);
    api.noteMusicEpoch({ room: { room_id: room || "ROOM", music_epoch_ms: epochSec * 1000 } });
  };
  api.tick = (sec) => { clock.wall += sec * 1000; clock.ctx += sec; };
  api.fire = (type) => (listeners[type] || []).slice().forEach(fn => fn());
  api.allowAudio = () => { gate.allowed = true; };
  api.setVolume = (v) => store.set("fish_music_volume", String(v));
  api.clock = clock;
  api.log = log;
  api.ctx = ctx;
  api.document = document;
  return api;
}
const flush = () => new Promise(r => setImmediate(r));

async function main() {

// ── Two players, two clocks, two arrival times, one beat ────────────────────
console.log("everyone in the room is at the same place in the song");
{
  // Alice's laptop clock is 37 seconds fast, Bob's phone is 8 seconds slow,
  // and Bob walks in twelve minutes after Alice.
  const alice = harness({ wall: 1_700_000_037_000, serverAhead: -37_000 });
  const bob = harness({ wall: 1_699_999_992_000, serverAhead: 8_000 });
  alice.enterRoom("ROOM"); bob.enterRoom("ROOM");
  alice.loadBuffer(); bob.loadBuffer();

  alice.poll(80, EPOCH_SEC);
  bob.tick(12 * 60);
  bob.poll(220, EPOCH_SEC);

  // Both devices are now at the same true instant: line their wall clocks up
  // on it, then ask each one what part of the track the room is playing.
  const trueNow = 1_700_001_500_000;
  alice.clock.wall = trueNow - alice.clock.serverAhead;
  bob.clock.wall = trueNow - bob.clock.serverAhead;

  const a = alice.themeTargetOffset(), b = bob.themeTargetOffset();
  near(a, b, 0.25, "two devices twelve minutes apart agree on the beat");
  near(a, ((trueNow - EPOCH_SEC * 1000) / 1000) % TRACK_SEC, 0.25,
       "and it is where the room is, not where their own clocks say");
  ok(a >= 0 && a < TRACK_SEC, "the offset stays inside the track");
}

// ── The offset wraps, and is never a guess ──────────────────────────────────
console.log("the timeline wraps, and stays quiet when it cannot be trusted");
{
  const h = harness({ wall: EPOCH_SEC * 1000 });
  h.enterRoom("ROOM"); h.loadBuffer();
  eq(h.themeTargetOffset(), null, "no server clock yet: no answer, so no start at the top");

  h.poll(40, EPOCH_SEC);
  near(h.themeTargetOffset(), 0.04, 0.05, "at the epoch the track is at its start");
  h.tick(TRACK_SEC * 3 + 10);
  near(h.themeTargetOffset(), 10.04, 0.1, "three loops on it has wrapped, not run off the end");

  // A stamp from the room we just left is not an answer about this one.
  h.leaveRoom();
  eq(h.themeTargetOffset(), null, "out of the room there is no timeline to be on");
  h.enterRoom("OTHER");
  eq(h.themeTargetOffset(), null, "another room's epoch is ignored, not reused");
  h.enterRoom(null); h.spectate("ROOM");
  ok(h.themeTargetOffset() !== null, "spectating the room counts as being in it");
}

// ── Estimating the server's clock off a timed poll ──────────────────────────
console.log("the server's clock is measured, not assumed");
{
  const h = harness({ serverAhead: 5_000 });
  h.enterRoom("ROOM"); h.loadBuffer();
  h.poll(200, EPOCH_SEC);
  near(h.skew(), 5_000, 5, "half the round trip is taken off the reply's age");

  // A slower poll wobbling by a few tens of milliseconds is network jitter,
  // not news, and must not drag the playhead about.
  const before = h.skew();
  const slowReply = h.clock.wall + 5_000 - 450 + 60; // written half a slow trip ago, 60ms of noise
  h.noteServerClock(slowReply, 900, h.clock.wall);
  eq(h.skew(), before, "a slower, noisier sample is ignored");

  // A device clock that really moved (a laptop waking, an NTP correction) is a
  // different thing, and is taken even from a slow poll.
  h.noteServerClock(h.clock.wall + 45_000, 900, h.clock.wall);
  near(h.skew(), 45_000, 500, "a real correction is taken, whatever the round trip");

  // Rubbish never sets the clock the music is played against.
  const good = h.skew();
  h.noteServerClock(NaN, 50, h.clock.wall);
  h.noteServerClock(h.clock.wall, 60_000, h.clock.wall);
  h.noteServerClock(0, 50, h.clock.wall);
  eq(h.skew(), good, "a missing, absurd or timed-out sample changes nothing");
}

// ── Holding the beat: nudge for the small stuff, re-seat when lost ──────────
console.log("drift is held, not papered over");
{
  const h = harness();
  h.enterRoom("ROOM"); h.loadBuffer(); h.poll(40, EPOCH_SEC);

  // Dead on: leave the speed alone.
  h.seat(h.themeTargetOffset());
  h._themeResync();
  eq(h.rate(), 1, "inside the deadband nothing is touched");

  // A fifth of a second out: glide back at 0.5%, about nine cents of pitch,
  // under what an ear picks out, instead of jumping the track.
  h.seat(h.themeTargetOffset() + 0.2);
  h._themeResync();
  ok(h.rate() < 1 && h.rate() > 0.99, "running ahead, ease off (rate " + h.rate() + ")");
  eq(h.log.stopped, 0, "a nudge never restarts the song");
  h.seat(h.themeTargetOffset() - 0.2);
  h._themeResync();
  ok(h.rate() > 1 && h.rate() < 1.01, "running behind, ease up (rate " + h.rate() + ")");

  // The short way round the loop: one second in is two seconds AHEAD of one
  // second from the end, not ninety-four seconds behind it.
  h.seat(1);
  h.clock.wall = EPOCH_SEC * 1000 + (TRACK_SEC - 1) * 1000 - (h.skew() || 0);
  near(h._themeDrift(), 2, 0.1, "drift is measured the short way round the loop");
}

// ── A frozen tab comes back ─────────────────────────────────────────────────
console.log("a backgrounded tab catches up instead of playing its own song");
{
  const h = harness();
  h.enterRoom("ROOM"); h.loadBuffer(); h.poll(40, EPOCH_SEC);
  h.seat(h.themeTargetOffset());

  // Away for four minutes with the audio clock frozen: the wall clock (and so
  // the room) moved on, this device's playhead did not.
  h.clock.wall += 240_000;
  h.document.hidden = false;
  h.fire("visibilitychange");
  ok(h.log.stopped > 0, "the stranded source is faded out, not left running");
  ok(h.playing(), "and the song is running again straight away");
  near(h.log.started[h.log.started.length - 1], h.themeTargetOffset(), 0.3,
       "it picks the track up where the ROOM is now");
}

// ── Starting: on the timeline, or not at all ────────────────────────────────
console.log("the first note lands on the shared timeline");
{
  const h = harness();
  h.enterRoom("ROOM");
  h.poll(40, EPOCH_SEC);
  await h.startThemeSong();
  ok(h.playing(), "a normal start plays");
  near(h.log.started[0], h.themeTargetOffset(), 0.3,
       "it opens where the room is, not at the top of the track");
  eq(h.syncTimers(), 1, "and it starts watching its own drift");

  h.stopThemeSong();
  eq(h.playing(), false, "stopping stops it");
  eq(h.syncTimers(), 0, "and takes the drift watch down with it");
}

// Two people whose games open half a minute apart still hear the same bar.
{
  const first = harness();
  const second = harness({ wall: 1_700_000_000_000 });
  first.enterRoom("ROOM"); first.poll(60, EPOCH_SEC);
  await first.startThemeSong();
  const gap = 30;
  first.tick(gap);
  second.enterRoom("ROOM"); second.tick(gap); second.poll(300, EPOCH_SEC);
  await second.startThemeSong();
  near(second._themePlayhead(), first._themePlayhead(), 0.5,
       "joining half a minute later, you come in where they are");
}

// A tab nobody has touched cannot play audio. It must WAIT for a gesture
// rather than firing a source into a suspended context, which is what used to
// start the music, from the top, at some unrelated click minutes later.
{
  const h = harness({ ctxState: "suspended" });
  h.enterRoom("ROOM"); h.poll(40, EPOCH_SEC);
  await h.startThemeSong();
  eq(h.playing(), false, "a tab nobody has touched does not start a source");
  eq(h.log.started.length, 0, "nothing is queued up to blurt out later either");

  h.allowAudio();
  h.tick(45);            // three quarters of a minute before the first click
  h.fire("pointerdown");
  await flush();
  ok(h.playing(), "the first real gesture starts it");
  near(h.log.started[0], h.themeTargetOffset(), 0.4,
       "and it joins the room's timeline, not the top of the track");
}

// Music off means off: the slider at zero never starts anything.
{
  const h = harness({ volume: 0 });
  h.enterRoom("ROOM"); h.poll(40, EPOCH_SEC);
  await h.startThemeSong();
  eq(h.playing(), false, "with the volume slider at Off, nothing plays");
}

// ── Kickoff moves the epoch, together ───────────────────────────────────────
console.log("the theme opens the match for everyone at once");
{
  const h = harness();
  h.enterRoom("ROOM");
  h.poll(40, EPOCH_SEC);          // lobby: measured from room creation
  await h.startThemeSong();
  h.tick(200);
  const kickoff = Math.floor(h.clock.wall / 1000);
  h.poll(40, kickoff);            // the match starts: the epoch moves to kickoff
  near(h._themePlayhead(), h.themeTargetOffset(), 0.3,
       "the running song follows the epoch to kickoff instead of running on");
  near(h.log.started[h.log.started.length - 1], 0, 0.5,
       "which puts everyone at the top of the track as the game opens");
}

console.log();
if (failures) {
  console.log("FAILED  " + failures + " of " + checks + " checks");
  process.exit(1);
}
console.log("PASSED  " + checks + " checks");
}

main().catch(e => { console.error(e); process.exit(1); });
