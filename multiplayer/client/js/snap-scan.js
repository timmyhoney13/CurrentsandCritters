/* Snap & Score, main-thread scanning API (window.SnapScan).
 *
 * Owns: card-library loading, the scan Web Worker (with a synchronous
 * main-thread fallback), photo decoding, and the anti-cheat photo evidence
 * (sha256 + dHash + review thumbnail) that multiplayer sessions submit in
 * place of the photo itself. Recognition is 100% on-device, the photo is
 * never uploaded anywhere for identification.
 *
 * Requires snap-vision-core.js to be loaded first (it is also used directly
 * for interactive re-identification after the player edits boxes).
 */
(function () {
  "use strict";

  var LIB_URL = "/snap-card-library.json";
  var library = null, libraryPromise = null;
  var worker = null, workerReady = false, workerBroken = false;
  var pending = {};   // token → {resolve, reject, onProgress}
  var tokenSeq = 1;

  function loadLibrary() {
    if (libraryPromise) return libraryPromise;
    libraryPromise = fetch(LIB_URL + "?v=" + (window.SNAP_LIBRARY_V || "1"), { cache: "force-cache" })
      .then(function (r) {
        if (!r.ok) throw new Error("card library failed to load (" + r.status + ")");
        return r.json();
      })
      .then(function (lib) {
        library = lib;
        return lib;
      })
      .catch(function (err) { libraryPromise = null; throw err; });
    return libraryPromise;
  }

  function ensureWorker() {
    if (workerBroken) return null;
    if (worker) return worker;
    try {
      worker = new Worker("/js/snap-vision-worker.js");
    } catch (e) {
      workerBroken = true;
      return null;
    }
    worker.onmessage = function (e) {
      var msg = e.data || {};
      if (msg.type === "ready") { workerReady = true; return; }
      var p = pending[msg.token];
      if (!p) return;
      if (msg.type === "progress") { if (p.onProgress) p.onProgress(msg.stage, msg.label); return; }
      delete pending[msg.token];
      if (msg.type === "result") p.resolve(msg.result);
      else p.reject(new Error(msg.error || "scan failed"));
    };
    worker.onerror = function () {
      workerBroken = true;
      var keys = Object.keys(pending);
      keys.forEach(function (k) {
        var p = pending[k];
        delete pending[k];
        p.reject(new Error("scan worker crashed"));
      });
      try { worker.terminate(); } catch (e) {}
      worker = null;
    };
    worker.postMessage({ type: "init", library: library });
    return worker;
  }

  function decodePhoto(dataUrl) {
    return new Promise(function (resolve, reject) {
      var img = new Image();
      img.onload = function () {
        try {
          var cv = document.createElement("canvas");
          cv.width = img.naturalWidth; cv.height = img.naturalHeight;
          var ctx = cv.getContext("2d", { willReadFrequently: true });
          ctx.drawImage(img, 0, 0);
          resolve(ctx.getImageData(0, 0, cv.width, cv.height));
        } catch (e) { reject(e); }
      };
      img.onerror = function () { reject(new Error("couldn't decode the photo")); };
      img.src = dataUrl;
    });
  }

  function scanImageData(imageData, opts, onProgress) {
    return loadLibrary().then(function () {
      var w = ensureWorker();
      if (w) {
        return new Promise(function (resolve, reject) {
          var token = "t" + (tokenSeq++);
          pending[token] = { resolve: resolve, reject: reject, onProgress: onProgress };
          // copy the pixels: the caller keeps its ImageData for the box editor
          var buf = imageData.data.slice().buffer;
          w.postMessage({ type: "scan", token: token, buffer: buf,
                          width: imageData.width, height: imageData.height,
                          opts: opts || null }, [buf]);
        }).catch(function (err) {
          if (!workerBroken) throw err;
          return mainThreadScan(imageData, opts, onProgress); // worker died → fallback
        });
      }
      return mainThreadScan(imageData, opts, onProgress);
    });
  }

  function mainThreadScan(imageData, opts, onProgress) {
    return new Promise(function (resolve, reject) {
      // let the progress UI paint before the synchronous crunch
      setTimeout(function () {
        try {
          resolve(SnapVisionCore.scanBoard(imageData, library, opts || null, onProgress || function () {}));
        } catch (e) { reject(e); }
      }, 30);
    });
  }

  // ── photo evidence (multiplayer anti-cheat) ───────────────────────────────
  // The photo itself stays on the device; sessions submit only a sha256 of the
  // captured JPEG, a 64-bit dHash, and a small review thumbnail, the same
  // signals the server used to compute for duplicate-photo detection.

  function dataUrlBytes(dataUrl) {
    var comma = dataUrl.indexOf(",");
    var b64 = dataUrl.slice(comma + 1);
    var bin = atob(b64);
    var bytes = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return bytes;
  }

  function sha256Hex(bytes) {
    if (!(window.crypto && crypto.subtle && crypto.subtle.digest)) return Promise.resolve("");
    return crypto.subtle.digest("SHA-256", bytes).then(function (buf) {
      var v = new Uint8Array(buf), hex = "";
      for (var i = 0; i < v.length; i++) hex += (v[i] < 16 ? "0" : "") + v[i].toString(16);
      return hex;
    }).catch(function () { return ""; });
  }

  function evidenceFor(dataUrl) {
    return decodePhoto(dataUrl).then(function (imageData) {
      // dHash on a mid-size render (deterministic box downsample from pixels)
      var w = imageData.width, h = imageData.height;
      var scale = Math.min(1, 320 / Math.max(w, h));
      var gw = Math.max(9, Math.round(w * scale)), gh = Math.max(8, Math.round(h * scale));
      var gray = new Float64Array(gw * gh);
      var counts = new Int32Array(gw * gh);
      var d = imageData.data;
      for (var sy = 0; sy < h; sy++) {
        var dy = Math.floor(sy * gh / h), row = sy * w;
        for (var sx = 0; sx < w; sx++) {
          var dx = Math.floor(sx * gw / w);
          var si = (row + sx) * 4;
          gray[dy * gw + dx] += SnapVisionCore.luma(d[si], d[si + 1], d[si + 2]);
          counts[dy * gw + dx]++;
        }
      }
      for (var i = 0; i < gw * gh; i++) gray[i] /= counts[i] || 1;
      var g98 = SnapVisionCore.boxDownsample(gray, gw, gh, 1, 9, 8);
      var bits = 0n;
      for (var r = 0; r < 8; r++)
        for (var c = 0; c < 8; c++)
          bits = (bits << 1n) | (g98[r * 9 + c] > g98[r * 9 + c + 1] ? 1n : 0n);
      var dhash = bits.toString(16).padStart(16, "0");

      // small review thumbnail (kept by multiplayer sessions for verification)
      var cv = document.createElement("canvas");
      var ts = Math.min(1, 320 / Math.max(w, h));
      cv.width = Math.max(1, Math.round(w * ts));
      cv.height = Math.max(1, Math.round(h * ts));
      var ctx = cv.getContext("2d");
      ctx.putImageData(scaleImageData(imageData, cv.width, cv.height), 0, 0);
      var thumb = cv.toDataURL("image/jpeg", 0.6);

      return sha256Hex(dataUrlBytes(dataUrl)).then(function (hash) {
        return { hash: hash, dhash: dhash, thumb: thumb };
      });
    });
  }

  function scaleImageData(imageData, dw, dh) {
    var cv = document.createElement("canvas");
    cv.width = imageData.width; cv.height = imageData.height;
    cv.getContext("2d").putImageData(imageData, 0, 0);
    var out = document.createElement("canvas");
    out.width = dw; out.height = dh;
    var ctx = out.getContext("2d", { willReadFrequently: true });
    ctx.drawImage(cv, 0, 0, dw, dh);
    return ctx.getImageData(0, 0, dw, dh);
  }

  window.SnapScan = {
    loadLibrary: loadLibrary,
    getLibrary: function () { return library; },
    decodePhoto: decodePhoto,
    scanImageData: scanImageData,
    evidenceFor: evidenceFor,
  };
})();
