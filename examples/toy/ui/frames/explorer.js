// Runs in <iframe sandbox="allow-scripts allow-forms"> on the module origin: no cookies, no console DOM.
// The host transfers a MessagePort in the first message, with the frame's context (the job, node or campaign it is shown
// for); every request goes through that port, and the host answers only what the manifest's `bridge` list allows
// (spec/ui-contract.md, "The iframe placement").
let port = null, seq = 0, pending = {}, context = {};
function call(method, params) {
  return new Promise((resolve, reject) => {
    const id = ++seq; pending[id] = {resolve, reject};
    port.postMessage({id, method, params});
  });
}
window.addEventListener("message", (ev) => {
  if (port || !ev.ports || !ev.ports[0] || !ev.data || ev.data.type !== "oarbank.bridge") return;
  port = ev.ports[0];
  context = ev.data.context || {};
  port.onmessage = (m) => { const p = pending[m.data.id]; if (!p) return; delete pending[m.data.id];
                            m.data.error ? p.reject(m.data.error) : p.resolve(m.data.result); };
  load();
});
function cell(tr, text) { const td = document.createElement("td"); td.textContent = String(text); tr.appendChild(td); return td; }
async function load() {
  const rows = (await call("read.view", {view: "sums"})).rows || [];
  const tb = document.querySelector("#t tbody");
  tb.textContent = "";
  for (const r of rows) { const tr = document.createElement("tr"); cell(tr, r.n); cell(tr, r.sum); tb.appendChild(tr); }
  // the latest results, through the same host query a page uses (owner-scoped by the host)
  const latest = (await call("read.query", {query: "results", fields: ["job_id", "value"], order_by: "job_id", limit: 5})).rows || [];
  const ul = document.getElementById("jobs");
  ul.textContent = "";
  for (const r of latest) {
    const li = document.createElement("li"), b = document.createElement("button");
    b.type = "button"; b.textContent = "job " + r.job_id + ": " + r.value;
    b.onclick = () => call("navigate", {to: {job: r.job_id}});          // a typed reference; the host picks the URL
    li.appendChild(b); ul.appendChild(li);
  }
  document.getElementById("ctx").textContent = context.job ? "shown for job " + context.job : "shown on the module page";
  call("resize", {height: document.body.scrollHeight + 16});
  document.getElementById("fav").onclick = () => {
    const best = rows.reduce((a, r) => (r.n > (a ? a.n : -1) ? r : a), null);
    // params travel as JSON: n stays a number
    if (best) call("request.operation", {op: "self.set_favorite", params: {n: best.n}});
  };
}
