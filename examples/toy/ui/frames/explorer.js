// Runs in <iframe sandbox="allow-scripts allow-forms"> on the module origin: no cookies, no console DOM.
// The host transfers a MessagePort in the first message; every request goes through it (spec/ui-contract.md).
let port = null, seq = 0, pending = {};
function call(method, params) {
  return new Promise((resolve, reject) => {
    const id = ++seq; pending[id] = {resolve, reject};
    port.postMessage({id, method, params});
  });
}
window.addEventListener("message", (ev) => {
  if (port || !ev.ports || !ev.ports[0] || !ev.data || ev.data.type !== "oarbank.bridge") return;
  port = ev.ports[0];
  port.onmessage = (m) => { const p = pending[m.data.id]; if (!p) return; delete pending[m.data.id];
                            m.data.error ? p.reject(m.data.error) : p.resolve(m.data.result); };
  load();
});
async function load() {
  const rows = (await call("read.view", {view: "sums"})).rows || [];
  const tb = document.querySelector("#t tbody");
  tb.textContent = "";
  for (const r of rows) {
    const tr = document.createElement("tr");
    for (const k of ["n", "sum"]) { const td = document.createElement("td"); td.textContent = String(r[k]); tr.appendChild(td); }
    tb.appendChild(tr);
  }
  call("resize", {height: document.body.scrollHeight + 16});
  document.getElementById("fav").onclick = () => {
    const best = rows.reduce((a, r) => (r.n > (a ? a.n : -1) ? r : a), null);
    if (best) call("request.operation", {op: "self.set_favorite", params: {n: best.n}});
  };
}
