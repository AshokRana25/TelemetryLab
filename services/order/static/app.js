"use strict";
const $ = (id) => document.getElementById(id);
let config;
let products = [];
let busy = false;
const history = [];
const money = (cents) => new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(cents / 100);

function lock(value) {
  busy = value;
  $("place-order").disabled = value || !config;
  $("burst").disabled = value || !config?.demo_enabled;
  $("reset").disabled = value || !config;
  $("product").disabled = value || !config;
  $("quantity").disabled = value || !config;
  document.querySelectorAll("input[name=scenario]").forEach((input) => {
    input.disabled = value || !config || (input.value !== "normal" && !config.demo_enabled);
  });
}

function updateProduct() {
  const product = products.find((p) => p.sku === $("product").value);
  if (!product) return;
  $("product-name").textContent = product.name;
  $("product-price").textContent = money(product.price_cents);
  $("product-description").textContent = `${product.stock} synthetic units · no payment`;
}

function showResult(data, status) {
  $("result-empty").hidden = true;
  $("result-content").hidden = false;
  const ok = status === 201;
  $("result-badge").textContent = ok ? "CONFIRMED" : "REQUEST FAILED";
  $("result-symbol").textContent = ok ? "✓" : "!";
  $("result-symbol").className = `result-symbol${ok ? "" : " error"}`;
  $("result-title").textContent = ok ? "Checkout complete." : "A failure worth investigating.";
  $("result-message").textContent = data.message || "Could not reach the Order service. Check that the stack is running.";
  $("http-status").textContent = status || "Network";
  $("elapsed").textContent = data.elapsed_ms == null ? "—" : `${data.elapsed_ms.toFixed(0)} ms`;
  $("trace-id").textContent = data.trace_id || "No trace returned";
  $("trace-link").hidden = !data.trace_url;
  if (data.trace_url) $("trace-link").href = data.trace_url;
}

function renderHistory() {
  $("history").replaceChildren();
  for (const row of history) {
    const tr = document.createElement("tr");
    for (const value of [row.time, row.scenario, row.status ? `${row.status} ${row.status === 201 ? "Confirmed" : "Failed"}` : "Network error", row.data.elapsed_ms == null ? "—" : `${row.data.elapsed_ms.toFixed(0)} ms`]) {
      const td = document.createElement("td"); td.textContent = value; tr.append(td);
    }
    tr.children[2].className = row.status === 201 ? "success-text" : "error-text";
    const td = document.createElement("td");
    if (row.data.trace_url) {
      const link = document.createElement("a");
      link.href = row.data.trace_url; link.target = "_blank"; link.rel = "noopener";
      link.textContent = `${row.data.trace_id.slice(0, 12)}… ↗`;
      link.title = `Inspect trace ${row.data.trace_id}`; td.append(link);
    } else td.textContent = "Unavailable";
    tr.append(td); $("history").append(tr);
  }
}

async function placeOrder(payload) {
  let data, status = 0;
  try {
    const response = await fetch("/api/orders", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload), signal: AbortSignal.timeout(20000),
    });
    status = response.status; data = await response.json();
  } catch (error) {
    data = { message: error.name === "TimeoutError" ? "Browser deadline reached. Inspect service logs before retrying." : "Could not reach the Order service. Check Docker Compose." };
  }
  history.unshift({ time: new Date().toLocaleTimeString(), scenario: payload.scenario, status, data });
  history.splice(20); showResult(data, status); renderHistory();
  return status === 201;
}

async function run(burst = false) {
  if (busy || !config || (burst && !config.demo_enabled)) return;
  if (!$("quantity").reportValidity()) return;
  const payload = { sku: $("product").value, quantity: Number($("quantity").value), scenario: document.querySelector("input[name=scenario]:checked").value };
  lock(true);
  const count = burst ? config.burst_count : 1;
  let next = 0, completed = 0, successes = 0;
  $("activity").textContent = `Sending ${count === 1 ? "checkout" : "12 bounded checkouts"}…`;
  try {
    async function worker() {
      while (next < count) {
        next += 1;
        if (await placeOrder(payload)) successes += 1;
        completed += 1;
        $("activity").textContent = `${completed}/${count} completed · ${successes} successful · ${completed - successes} failed`;
      }
    }
    await Promise.all(Array.from({ length: Math.min(count, config.burst_concurrency) }, worker));
  } finally { lock(false); }
}

$("place-order").addEventListener("click", () => run());
$("burst").addEventListener("click", () => run(true));
$("reset").addEventListener("click", () => {
  document.querySelector('input[value="normal"]').checked = true;
  $("activity").textContent = "Normal behavior restored. The next checkout has no injected fault.";
});
$("product").addEventListener("change", updateProduct);

async function initialize() {
  lock(true);
  try {
    const responses = await Promise.all([fetch("/api/config"), fetch("/api/products")]);
    if (responses.some((response) => !response.ok)) throw new Error("Configuration unavailable");
    [config, products] = await Promise.all(responses.map((response) => response.json()));
    $("product").replaceChildren(...products.map((product) => new Option(`${product.name} — ${money(product.price_cents)}`, product.sku)));
    updateProduct();
    $("connection").textContent = config.demo_enabled ? "Local demo enabled" : "Fault injection disabled";
    $("slow-description").textContent = `Add ${config.slow_seconds.toFixed(1)}s inside the inventory span`;
    document.querySelectorAll(".dashboard-link").forEach((link) => { link.href = config.dashboard_url; });
    if (!config.demo_enabled) $("demo-note").textContent = "Enable DEMO_ENABLED=true on both services to use faults and bursts.";
    lock(false);
  } catch {
    config = null; lock(false);
    $("connection").textContent = "Service unavailable";
    $("activity").textContent = "Could not load the lab. Check Docker Compose and refresh this page.";
  }
}
initialize();
