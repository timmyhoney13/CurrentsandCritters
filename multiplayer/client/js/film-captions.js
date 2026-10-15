/* ================================================================
 * film-captions.js — the controls under the How to Play film:
 * the Captions switch and the Full Screen button.
 *
 * The film appears in three places and this file drives all three:
 *   /rules            multiplayer/client/rules.html   (both hosts)
 *   the home page     index.html                      (Vercel)
 *   Player Home       js/preview-app.js -> How to play (the game)
 *
 * Captions start OFF. The <track> carries no `default`, so the browser
 * leaves it `disabled` and never even fetches the .vtt until somebody
 * asks for it.
 *
 * Why buttons of our own, when <video controls> already has both: in
 * Chrome the captions control is not on the bar, it is behind the "..."
 * overflow menu, and on a narrow phone half the bar collapses in there
 * too — full screen included. A control nobody can find is a control
 * nobody has. So the real controls sit next to the running time, in the
 * page, where they are read as text.
 *
 * The native controls still work, and still win: every button is
 * repainted from the browser's own state (the track's `mode`, the
 * document's fullscreen element), so the two sets can never disagree.
 *
 * Markup contract, the same in all three places:
 *   <div class="…-film" data-film>
 *     <figure …><video …><track kind="captions" …></video></figure>
 *     <p class="…-film-cap">
 *       4 minutes 13 seconds.
 *       <button type="button" data-film-captions aria-pressed="false">…</button>
 *       <button type="button" data-film-full aria-pressed="false">…</button>
 *     </p>
 *   </div>
 *
 * Everything is delegated off `document`, because in the game this markup
 * is built as a string and inserted long after this file has run.
 * ================================================================ */
(function () {
  "use strict";

  var LABEL_ON = "Captions on";
  var LABEL_OFF = "Captions off";
  var FULL_ON = "Exit Full Screen";
  var FULL_OFF = "Full Screen";

  function captionTrack(video) {
    if (!video || !video.textTracks) return null;
    for (var i = 0; i < video.textTracks.length; i++) {
      var t = video.textTracks[i];
      if (t.kind === "captions" || t.kind === "subtitles") return t;
    }
    return null;
  }

  function videoFor(btn) {
    var wrap = btn.closest ? btn.closest("[data-film]") : null;
    return wrap ? wrap.querySelector("video") : null;
  }

  function paint(btn, on) {
    btn.setAttribute("aria-pressed", on ? "true" : "false");
    btn.textContent = on ? LABEL_ON : LABEL_OFF;
  }

  // One listener per <video>, added the first time we touch it, so a
  // caption turned on from the browser's own menu repaints our button.
  function watch(video, btn) {
    if (!video.textTracks || video._filmCaptionsWatched) return;
    video._filmCaptionsWatched = true;
    var repaint = function () {
      var track = captionTrack(video);
      paint(btn, !!track && track.mode === "showing");
    };
    if (video.textTracks.addEventListener) {
      video.textTracks.addEventListener("change", repaint);
    } else {
      video.textTracks.onchange = repaint;
    }
  }

  /* ── FULL SCREEN ────────────────────────────────────────────────
   * The <video> itself goes full screen, not the frame around it, so
   * the browser's own player — controls, captions menu, letterboxing —
   * comes with it.
   *
   * Three ways in, in order of how modern they are:
   *   video.requestFullscreen()        standard
   *   video.webkitRequestFullscreen()  older Safari and Chrome
   *   video.webkitEnterFullscreen()    iPhone, which has no element
   *                                    full screen at all and answers
   *                                    only with its native player
   */

  function fsRoot() {
    return document.fullscreenElement || document.webkitFullscreenElement || null;
  }

  // Only the <video> counts. The game has its OWN full screen button that
  // puts the whole document up (see setupHomeFullScreenBtn in
  // preview-app.js); a `contains` test here would read that as the film
  // being full screen, and one click would drop the player out of it.
  function videoIsFull(video) {
    return Boolean(video && (fsRoot() === video || video._filmNativeFull));
  }

  function fullSupported(video) {
    if (!video) return false;
    // iPhone first: it has no element full screen, so fullscreenEnabled is
    // false there even though the video's own player works fine.
    if (video.webkitEnterFullscreen || video.webkitSupportsFullscreen) return true;
    if (document.fullscreenEnabled || document.webkitFullscreenEnabled) return true;
    return Boolean(video.requestFullscreen || video.webkitRequestFullscreen);
  }

  // The iPhone player reports itself through its own two events, and no
  // fullscreenchange ever fires, so the button has to be told.
  function watchNative(video) {
    if (video._filmNativeWatched) return;
    video._filmNativeWatched = true;
    if (!video.addEventListener) return;
    video.addEventListener("webkitbeginfullscreen", function () {
      video._filmNativeFull = true;
      syncFull();
    });
    video.addEventListener("webkitendfullscreen", function () {
      video._filmNativeFull = false;
      syncFull();
    });
  }

  function nativeFull(video) {
    if (!video.webkitEnterFullscreen) return;
    try { video.webkitEnterFullscreen(); } catch (e) { /* refused */ }
  }

  function enterFull(video) {
    watchNative(video);
    var req = video.requestFullscreen || video.webkitRequestFullscreen || null;
    if (!req) { nativeFull(video); return; }
    var p;
    try { p = req.call(video); } catch (e) { nativeFull(video); return; }
    // A rejection means the browser refused the element (a policy, an
    // iframe without the permission, a gesture it did not count). iPad
    // Safari refuses the element and still allows its own player, so that
    // is the fallback rather than the end of it.
    if (p && p.then) p.then(syncFull, function () { nativeFull(video); syncFull(); });
    else syncFull();
  }

  function exitFull(video) {
    if (video._filmNativeFull) {
      if (video.webkitExitFullscreen) {
        try { video.webkitExitFullscreen(); } catch (e) { /* ignore */ }
      }
      return;
    }
    var exit = document.exitFullscreen || document.webkitExitFullscreen || null;
    if (!exit) return;
    try {
      var p = exit.call(document);
      if (p && p.then) p.then(syncFull, syncFull);
    } catch (e) { /* ignore */ }
  }

  function paintFull(btn, on) {
    btn.setAttribute("aria-pressed", on ? "true" : "false");
    btn.textContent = on ? FULL_ON : FULL_OFF;
    btn.title = on ? "Leave full screen" : "Fill the screen with the video";
  }

  // A button that cannot do anything is worse than no button, so one the
  // browser will never honour is taken off the line entirely.
  function syncFull() {
    var buttons = document.querySelectorAll("[data-film-full]");
    for (var i = 0; i < buttons.length; i++) {
      var btn = buttons[i];
      var video = videoFor(btn);
      if (!video) continue;
      if (!fullSupported(video)) { btn.hidden = true; continue; }
      btn.hidden = false;
      watchNative(video);
      paintFull(btn, videoIsFull(video));
    }
  }

  /* ── Clicks, for both controls ──────────────────────────────────── */

  document.addEventListener("click", function (event) {
    var hit = event.target && event.target.closest
      ? event.target.closest("[data-film-captions], [data-film-full]")
      : null;
    if (!hit) return;
    event.preventDefault();

    var video = videoFor(hit);
    if (!video) return;

    if (hit.hasAttribute("data-film-full")) {
      if (videoIsFull(video)) {
        exitFull(video);
      } else {
        // Full screen on a poster frame is a big poster. The click is a
        // gesture the browser will honour, so start it — but only from the
        // very beginning, never over somebody's deliberate pause.
        if (video.paused && !video.currentTime) {
          var play = video.play();
          if (play && play.catch) play.catch(function () { /* blocked */ });
        }
        enterFull(video);
      }
      return;
    }

    var track = captionTrack(video);
    if (!track) return;          // no caption file: leave the button alone

    watch(video, hit);
    // "hidden" rather than "disabled" on the way back off: the .vtt stays
    // parsed, so turning captions on a second time is instant.
    var on = track.mode !== "showing";
    track.mode = on ? "showing" : "hidden";
    paint(hit, on);
  });

  // Esc, F11 and the browser's own full screen button all leave without
  // touching ours, so the label follows the document rather than our clicks.
  document.addEventListener("fullscreenchange", syncFull);
  document.addEventListener("webkitfullscreenchange", syncFull);

  // Start every switch from the truth, in case a browser restored a
  // caption preference of its own across a reload.
  function sync() {
    var buttons = document.querySelectorAll("[data-film-captions]");
    for (var i = 0; i < buttons.length; i++) {
      var btn = buttons[i];
      var video = videoFor(btn);
      if (!video) continue;
      var track = captionTrack(video);
      watch(video, btn);
      paint(btn, !!track && track.mode === "showing");
    }
    syncFull();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", sync);
  } else {
    sync();
  }
  // The game builds its How to Play pane on demand; re-syncing when it
  // opens costs one querySelectorAll.
  window.CC_SYNC_FILM_CAPTIONS = sync;
})();
