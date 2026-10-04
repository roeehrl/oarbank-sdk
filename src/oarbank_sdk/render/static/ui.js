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
  // accept messages from it on window; we hand it one MessagePort and only answer on that port.
  function bridges(root) {
    (root || document).querySelectorAll("iframe[data-bridge-module]:not([data-bridged])").forEach(function (frame) {
      frame.dataset.bridged = "1";
      var region = frame.closest(".mod-region");
      var base = region ? region.dataset.bridgeBase : "";
      var opBase = region ? region.dataset.opBase : "";
      var module = frame.dataset.bridgeModule;
      frame.addEventListener("load", function () {
        var ch = new MessageChannel();
        ch.port1.onmessage = function (ev) { handle(frame, ch.port1, base, opBase, module, ev.data || {}); };
        // targetOrigin "*" is required for an opaque-origin frame; the port goes only to this frame's window.
        frame.contentWindow.postMessage({type: "oarbank.bridge", module: module, view: frame.dataset.bridgeView}, "*", [ch.port2]);
      });
    });
  }

  function reply(port, id, result, error) { port.postMessage(error ? {id: id, error: String(error)} : {id: id, result: result}); }

  function handle(frame, port, base, opBase, module, msg) {
    var id = msg.id, p = msg.params || {};
    switch (msg.method) {
      case "read.view":
        fetch(base + "/view/" + encodeURIComponent(p.view || ""), {credentials: "same-origin"})
          .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
          .then(function (j) { reply(port, id, j); }, function (e) { reply(port, id, null, "read failed: " + e); });
        return;
      case "read.query":
        fetch(base + "/query?spec=" + encodeURIComponent(JSON.stringify(p)), {credentials: "same-origin"})
          .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
          .then(function (j) { reply(port, id, j); }, function (e) { reply(port, id, null, "read failed: " + e); });
        return;
      case "resize":
        var max = Number(frame.dataset.maxHeight || 2000);
        frame.height = String(Math.max(120, Math.min(max, Number(p.height) || 0)));
        reply(port, id, {ok: true});
        return;
      case "request.operation":
        // The host confirms outside the frame, with registry metadata (never frame text), then submits a
        // normal operation form: T2/T3 continue on the host's plan page.
        var op = String(p.op || "");
        if (op.indexOf("self.") === 0) op = "mod." + module.replace(/-/g, "_") + "." + op.slice(5);
        if (!/^[a-z]+(\.[a-z_]+)+$/.test(op)) { reply(port, id, null, "bad operation"); return; }
        fetch(base + "/op/" + encodeURIComponent(op), {credentials: "same-origin"})
          .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
          .then(function (meta) {
            if (!window.confirm("The module frame asks to run:\n\n" + meta.title + " (" + meta.tier + ")\n\nAllow?")) {
              reply(port, id, {ok: false, declined: true}); return;
            }
            var f = document.createElement("form");
            f.method = "post"; f.action = opBase + op;
            var add = function (k, v) { var i = document.createElement("input"); i.type = "hidden"; i.name = k; i.value = v; f.appendChild(i); };
            add("target", p.target || ""); add("return_to", window.location.pathname);
            add("idem", (self.crypto && crypto.randomUUID) ? crypto.randomUUID() : String(Date.now()));
            Object.keys(p.params || {}).forEach(function (k) { add("p." + k, String(p.params[k])); });
            document.body.appendChild(f); f.submit();
          }, function (e) { reply(port, id, null, "unknown operation: " + e); });
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
