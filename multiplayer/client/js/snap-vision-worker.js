/* Snap & Score, scan Web Worker.
 *
 * Runs the heavy pipeline (quality → detect → warp → match → assemble) off
 * the UI thread. All recognition is local: the photo enters as a transferred
 * RGBA buffer and never leaves the device.
 *
 * Protocol (postMessage):
 *   in : {type:"init", library}                       → {type:"ready"}
 *   in : {type:"scan", token, buffer, width, height,
 *          opts?: {manualQuads?: [[x,y]×4][]}}        → {type:"progress", token, stage, label}…
 *                                                       {type:"result", token, result}
 *   err: {type:"error", token, error}
 */
"use strict";

// carry the ?v= cache-bust from our own URL into the core import so the
// worker's core version always matches the page's library version
importScripts("/js/snap-vision-core.js" + (self.location && self.location.search ? self.location.search : ""));

var LIBRARY = null;

self.onmessage = function (e) {
  var msg = e.data || {};
  try {
    if (msg.type === "init") {
      LIBRARY = msg.library;
      // build the matcher once so the first scan doesn't pay for it
      LIBRARY.__matcher = SnapVisionCore.buildMatcher(LIBRARY);
      self.postMessage({ type: "ready" });
      return;
    }
    if (msg.type === "scan") {
      if (!LIBRARY) throw new Error("worker not initialized with a card library");
      var image = {
        data: new Uint8ClampedArray(msg.buffer),
        width: msg.width,
        height: msg.height,
      };
      var result = SnapVisionCore.scanBoard(image, LIBRARY, msg.opts || null, function (stage, label) {
        self.postMessage({ type: "progress", token: msg.token, stage: stage, label: label });
      });
      self.postMessage({ type: "result", token: msg.token, result: result });
      return;
    }
  } catch (err) {
    self.postMessage({ type: "error", token: msg.token, error: String((err && err.message) || err) });
  }
};
