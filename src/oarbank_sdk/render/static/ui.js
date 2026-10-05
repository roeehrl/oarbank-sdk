// UI contract 1 browser side (Apache-2.0, oarbank-sdk): charts and the sandboxed-iframe bridge.
// Loaded by the console and by `oarbank-sdk preview`. Binds through data- attributes only (strict CSP).
(function () {
  "use strict";

  function charts(root) {
    (root || document).querySelectorAll(".mod-chart:not([data-drawn])").forEach(function (el) {
      el.dataset.drawn = "1";
      if (!window.uPlot) return;
      var d = JSON.parse(el.dataset.series || "{}");
      var xs = (d.x || []).map(function (v) { var n = Number(v); return isNaN(n) ? Date.parse(v) / 1000 : n; });
      if (xs.length < 2) { el.textContent = "not enough data"; return; }
      var series = [{}].concat((d.y || []).map(function (name) { return {label: name}; }));
      new uPlot({width: Math.max(280, el.clientWidth || 600), height: 200, series: series}, [xs].concat(d.values || []), el);
    });
  }

  // Bridge: the frame is sandboxed without allow-same-origin, so its origin is opaque ("null"). We never
  // accept messages from it on window; we hand it one MessagePort and only answer on that port. Every method must be one
  // the frame declared ([[ui.iframes]].bridge, rendered as data-bridge-caps); the host checks it again on each request.
  function bridges(root) {
    (root || document).querySelectorAll("iframe[data-bridge-module]:not([data-bridged])").forEach(function (frame) {
      frame.dataset.bridged = "1";
      var region = frame.closest(".mod-region");
      var b = {frame: frame, base: frame.dataset.bridgeBase || "", opBase: region ? region.dataset.opBase : "",
               module: frame.dataset.bridgeModule, caps: (frame.dataset.bridgeCaps || "").split(" ").filter(Boolean),
               context: JSON.parse(frame.dataset.context || "{}")};
      frame.addEventListener("load", function () {
        var ch = new MessageChannel();
        ch.port1.onmessage = function (ev) { handle(b, ch.port1, ev.data || {}); };
        // targetOrigin "*" is required for an opaque-origin frame; the port goes only to this frame's window.
        frame.contentWindow.postMessage({type: "oarbank.bridge", module: b.module, view: frame.dataset.bridgeView,
                                         context: b.context}, "*", [ch.port2]);
      });
    });
  }

  function reply(port, id, result, error) { port.postMessage(error ? {id: id, error: String(error)} : {id: id, result: result}); }

  function csrfToken() {
    var m = document.querySelector('meta[name="csrf-token"]');
    return m ? m.content : "";
  }

  function get(url) {
    return fetch(url, {credentials: "same-origin"}).then(function (r) {
      return r.ok ? r.json() : r.json().then(function (j) { return Promise.reject(j.error || r.status); },
                                              function () { return Promise.reject(r.status); });
    });
  }

  function q(name, value) { return name + "=" + encodeURIComponent(typeof value === "string" ? value : JSON.stringify(value)); }

  function answer(port, id, promise, what) {
    promise.then(function (j) { reply(port, id, j); }, function (e) { reply(port, id, null, what + ": " + e); });
  }

  function handle(b, port, msg) {
    var id = msg.id, p = msg.params || {}, ctx = q("ctx", b.context);
    if (b.caps.indexOf(msg.method) < 0) { reply(port, id, null, "method not allowed"); return; }
    switch (msg.method) {
      case "read.view":
        answer(port, id, get(b.base + "/view/" + encodeURIComponent(p.view || "") + "?" + ctx), "read failed");
        return;
      case "read.query":
        answer(port, id, get(b.base + "/query?" + q("spec", p) + "&" + ctx), "read failed");
        return;
      case "read.media":
        answer(port, id, get(b.base + "/media?" + q("ref", p.ref || {}) + "&" + q("kind", String(p.kind || ""))), "no media");
        return;
      case "resize":
        var max = Number(b.frame.dataset.maxHeight || 2000);
        b.frame.height = String(Math.max(120, Math.min(max, Number(p.height) || 0)));
        reply(port, id, {ok: true});
        return;
      case "navigate":
        // a typed reference only; the host maps it to one of its own pages
        get(b.base + "/link?" + q("to", p.to || {})).then(function (j) {
          if (typeof j.href !== "string" || !/^\/(?![\/\\])/.test(j.href)) { reply(port, id, null, "bad link"); return; }
          reply(port, id, {ok: true});
          window.location.assign(j.href);
        }, function (e) { reply(port, id, null, "bad link: " + e); });
        return;
      case "request.operation":
        // The host confirms outside the frame, with registry metadata (never frame text), then submits a normal
        // operation form: T2/T3 continue on the host's plan page. The CSRF token travels as the form's field (a
        // navigation cannot carry a header); the frame never sees it. Parameters go as one JSON field, intact.
        var op = String(p.op || ""), target = p.target == null ? "" : String(p.target);
        if (op.indexOf("self.") === 0) op = "mod." + b.module.replace(/-/g, "_") + "." + op.slice(5);
        if (!/^[a-z]+(\.[a-z_]+)+$/.test(op)) { reply(port, id, null, "bad operation"); return; }
        get(b.base + "/op/" + encodeURIComponent(op) + "?" + q("target", target)).then(function (meta) {
          if (!window.confirm("The module frame asks to run:\n\n" + meta.title + " (" + meta.tier + ")" +
                              (target ? "\non " + target : "") + "\n\nAllow?")) {
            reply(port, id, {ok: false, declined: true}); return;
          }
          var f = document.createElement("form");
          f.method = "post"; f.action = b.opBase + op;
          var add = function (k, v) { var i = document.createElement("input"); i.type = "hidden"; i.name = k; i.value = v; f.appendChild(i); };
          add("target", target); add("return_to", window.location.pathname + window.location.search);
          add("idem", (self.crypto && crypto.randomUUID) ? crypto.randomUUID() : String(Date.now()));
          add("params", JSON.stringify(p.params || {})); add("csrf", csrfToken());
          document.body.appendChild(f);
          reply(port, id, {ok: true, submitted: true});
          f.submit();
        }, function (e) { reply(port, id, null, "operation refused: " + e); });
        return;
      default:
        reply(port, id, null, "method not allowed");
    }
  }

  // compare (slider): the top image is clipped to the slider's position; no module script is involved
  function compares(root) {
    (root || document).querySelectorAll(".mod-compare[data-compare]:not([data-bound])").forEach(function (fig) {
      fig.dataset.bound = "1";
      var top = fig.querySelector(".mod-compare-top"), range = fig.querySelector("input[type=range]");
      if (!top || !range) return;
      var set = function () { top.style.clipPath = "inset(0 0 0 " + Number(range.value) + "%)"; };
      range.addEventListener("input", set);
      set();
    });
  }

  function init(root) { charts(root); bridges(root); compares(root); }
  document.addEventListener("htmx:afterSwap", function (ev) { init(ev.target); });
  if (document.readyState !== "loading") init(); else document.addEventListener("DOMContentLoaded", function () { init(); });
})();
