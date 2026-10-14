/* ================================================================
 * film-captions.js — the Captions switch under the How to Play film.
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
 * Why a button of our own, when <video controls> already has one: in
 * Chrome the captions control is not on the bar, it is behind the "..."
 * overflow menu, and on a narrow phone half the bar collapses in there
 * too. A caption switch nobody can find is a caption switch nobody has.
 * So the real control sits next to the running time, in the page, where
 * it is read as text.
 *
 * The native menu still works, and still wins: every button is repainted
 * from the track's own mode on the textTracks `change` event, so the two
 * controls can never disagree.
 *
 * Markup contract, the same in all three places:
 *   <div class="…-film" data-film>
 *     <figure …><video …><track kind="captions" …></video></figure>
 *     <p class="…-film-cap">
 *       4 minutes 13 seconds.
 *       <button type="button" data-film-captions aria-pressed="false">…</button>
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

  document.addEventListener("click", function (event) {
    var btn = event.target && event.target.closest
      ? event.target.closest("[data-film-captions]")
      : null;
    if (!btn) return;
    event.preventDefault();

    var video = videoFor(btn);
    var track = captionTrack(video);
    if (!track) return;          // no caption file: leave the button alone

    watch(video, btn);
    // "hidden" rather than "disabled" on the way back off: the .vtt stays
    // parsed, so turning captions on a second time is instant.
    var on = track.mode !== "showing";
    track.mode = on ? "showing" : "hidden";
    paint(btn, on);
  });

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
