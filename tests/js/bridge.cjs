// The renderer's bridge (src/oarbank_sdk/render/static/ui.js) in jsdom, driven from the frame's side of its MessagePort.
//   NODE_PATH=<dir with jsdom> node bridge.cjs <ui.js>  -> JSON on stdout: what each call did
// jsdom has no MessageChannel; Node's is installed in the window, and the frame's postMessage is replaced to catch the port.
const fs = require("fs");
const { JSDOM, VirtualConsole } = require("jsdom");
const page = `<!doctype html><html><head><meta name="csrf-token" content="tok-123"></head><body>
<div class="mod-region" data-module="toy" data-op-base="/do/">
<iframe data-bridge-module="toy" data-bridge-view="explorer" data-bridge-base="/m/toy/_bridge/explorer"
 data-bridge-caps="read.query request.operation navigate" data-context='{"job":42}' data-max-height="640"></iframe>
</div></body></html>`;
// location.assign is not implemented in jsdom: each navigation shows up as a "not implemented" error, which is counted
const out = { fetches: [], confirms: [], forms: [], navigations: 0, replies: {} };
const vc = new VirtualConsole();
vc.on("jsdomError", (e) => { if (/navigation/i.test(e.message)) out.navigations += 1; });
const dom = new JSDOM(page, { runScripts: "outside-only", url: "http://127.0.0.1:7400/jobs/42", virtualConsole: vc });
const w = dom.window;
w.MessageChannel = MessageChannel;
const answers = {
  "/m/toy/_bridge/explorer/op/": { id: "mod.toy.set_favorite", title: "Set the favorite n", tier: "T1" },
  "/m/toy/_bridge/explorer/query": { rows: [{ job_id: 42 }] },
  "/m/toy/_bridge/explorer/link": null,
};
w.fetch = (url) => {
  out.fetches.push(url);
  if (url.indexOf("/link?") >= 0) {
    const to = JSON.parse(decodeURIComponent(url.split("to=")[1]));
    return Promise.resolve({ ok: true, json: () => Promise.resolve({ href: to.job === 666 ? "//evil.example/x" : "/jobs/" + to.job }) });
  }
  const key = Object.keys(answers).find((k) => url.startsWith(k));
  return Promise.resolve({ ok: true, json: () => Promise.resolve(answers[key]) });
};
w.confirm = (text) => { out.confirms.push(text); return true; };
w.HTMLFormElement.prototype.submit = function () {
  const fields = {};
  this.querySelectorAll("input").forEach((i) => { fields[i.name] = i.value; });
  out.forms.push({ action: this.getAttribute("action"), method: this.method, fields });
};
w.eval(fs.readFileSync(process.argv[2], "utf8"));
w.document.addEventListener("DOMContentLoaded", () => {             // after ui.js's own listener bound the frame
  const frame = w.document.querySelector("iframe");
  let port = null;
  frame.contentWindow.postMessage = (msg, origin, transfer) => { out.hello = msg; port = transfer[0]; };
  frame.dispatchEvent(new w.Event("load"));
  const calls = [
    [1, "read.view", { view: "sums" }],                               // not declared by this frame
    [2, "read.query", { query: "results", params: { job_id: "$job" } }],
    [3, "request.operation", { op: "self.set_favorite", target: "x:1", params: { n: 7, code: "007", tags: ["a"], on: true } }],
    [4, "navigate", { to: { job: 9 } }],
    [5, "navigate", { to: { job: 666 } }],                            // the host answered an off-site href: refused
    [6, "resize", { height: 50 }],                                    // not declared
  ];
  port.onmessage = (ev) => { out.replies[ev.data.id] = ev.data; };
  for (const [id, method, params] of calls) port.postMessage({ id, method, params });
  setTimeout(() => { port.close(); out.height = frame.height; process.stdout.write(JSON.stringify(out)); process.exit(0); }, 500);
});
