// Exercise the shipped browser logic with a small DOM/API fixture, without extra dependencies.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

async function browserFixture(demoEnabled = true) {
  const elements = new Map();
  class Element {
    constructor(value = '') {
      this.value = value;
      this.children = [];
      this.handlers = {};
      this.textContent = '';
      this.disabled = false;
    }
    append(child) { this.children.push(child); }
    replaceChildren(...children) {
      this.children = children;
      if (children[0]?.value) this.value = children[0].value;
    }
    addEventListener(event, handler) { this.handlers[event] = handler; }
    reportValidity() { return true; }
  }
  const get = (id) => {
    if (!elements.has(id)) elements.set(id, new Element(id === 'quantity' ? '1' : ''));
    return elements.get(id);
  };
  let selected = 'normal';
  const radios = ['normal', 'slow', 'failure'].map((value) => {
    const radio = new Element(value);
    Object.defineProperty(radio, 'checked', {
      get: () => selected === value,
      set: (checked) => { if (checked) selected = value; },
    });
    return radio;
  });
  const requests = [];
  let active = 0, peak = 0;
  const context = vm.createContext({
    document: {
      getElementById: get,
      createElement: () => new Element(),
      querySelectorAll: (selector) => selector.includes('scenario') ? radios : [],
      querySelector: (selector) => radios.find((radio) => selector.includes(':checked') ? radio.checked : radio.value === 'normal'),
    },
    Option: class extends Element { constructor(text, value) { super(value); this.textContent = text; } },
    Intl, Date, AbortSignal,
    fetch: async (url, options) => {
      if (url === '/api/config') return { ok: true, json: async () => ({ demo_enabled: demoEnabled, slow_seconds: 1.5, burst_count: 12, burst_concurrency: 3, dashboard_url: 'http://localhost:3000/d/telemetrylab' }) };
      if (url === '/api/products') return { ok: true, json: async () => [{ sku: 'LAB-MUG', name: 'Trace mug', stock: 50, price_cents: 1800 }] };
      assert.equal(url, '/api/orders');
      requests.push(JSON.parse(options.body));
      active += 1; peak = Math.max(peak, active);
      await new Promise((resolve) => setTimeout(resolve, 5));
      active -= 1;
      return { status: 201, json: async () => ({ trace_id: '1234567890abcdef1234567890abcdef', trace_url: 'http://localhost:3000/explore', elapsed_ms: 10, message: 'Test fixture response' }) };
    },
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../services/order/static/app.js'), 'utf8'), context);
  // Initialization performs only immediately resolved configuration/catalog fetches.
  await new Promise((resolve) => setImmediate(resolve));
  return { context, get, radios, requests, peak: () => peak };
}

test('a burst sends exactly 12 requests with at most three in flight and rejects double-clicks', async () => {
  const fixture = await browserFixture();
  fixture.radios[1].checked = true;
  await Promise.all([
    vm.runInContext('run(true)', fixture.context),
    vm.runInContext('run(true)', fixture.context),
  ]);
  assert.equal(fixture.requests.length, 12);
  assert.equal(fixture.peak(), 3);
  assert.ok(fixture.requests.every((request) => request.scenario === 'slow'));
  assert.equal(fixture.get('activity').textContent, '12/12 completed · 12 successful · 0 failed');
  assert.equal(fixture.get('place-order').disabled, false);
});

test('reset makes the next checkout normal and returns its trace link to the result panel', async () => {
  const fixture = await browserFixture();
  fixture.radios[2].checked = true;
  fixture.get('reset').handlers.click();
  await vm.runInContext('run()', fixture.context);
  assert.equal(fixture.requests.length, 1);
  assert.equal(fixture.requests[0].scenario, 'normal');
  assert.equal(fixture.get('http-status').textContent, 201);
  assert.equal(fixture.get('trace-link').href, 'http://localhost:3000/explore');
});

test('disabled demo configuration blocks bursts and disables fault controls', async () => {
  const fixture = await browserFixture(false);
  await vm.runInContext('run(true)', fixture.context);
  assert.equal(fixture.requests.length, 0);
  assert.equal(fixture.get('burst').disabled, true);
  assert.equal(fixture.radios[1].disabled, true);
  assert.equal(fixture.radios[2].disabled, true);
  await vm.runInContext('run()', fixture.context);
  assert.equal(fixture.requests.length, 1);
});
