/* Execute the production scheduler against server-rendered node fixtures.
 * No browser framework, application startup, database or network access. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const fixtures = JSON.parse(fs.readFileSync(0, 'utf8'));
const source = fs.readFileSync('app/static/js/deliberation-polling.js', 'utf8');

async function check(fixture) {
  let destructive = 0, rowMoves = 0;
  const ids = new Map(), listeners = {}, windowListeners = {}, timers = new Map();
  let timerId = 0, revision = fixture.revision, failFragment = false;
  const requests = [];
  function matches(node, selector) {
    if (selector.startsWith('#')) return node.attrs.id === selector.slice(1);
    if (selector.startsWith('.')) return node.classList.contains(selector.slice(1));
    if (selector === '[data-filter]') return 'data-filter' in node.attrs;
    if (selector === 'button[type="submit"]') return node.tag === 'button' && node.attrs.type === 'submit';
    if (selector === 'button:not([type])') return node.tag === 'button' && !('type' in node.attrs);
    return node.tag === selector;
  }
  function make(data, parent = null) {
    const node = {tag: data.tag, attrs: {...data.attrs}, parentElement: parent, scrollTop: 0, scrollLeft: 0};
    node.children = data.children.map(child => make(child, node));
    node.textContent = data.text;
    node.value = node.attrs.value || '';
    node.defaultValue = node.value;
    node.disabled = 'disabled' in node.attrs;
    node.style = {display: /display:\s*none/.test(node.attrs.style || '') ? 'none' : ''};
    const classes = new Set((node.attrs.class || '').split(/\s+/));
    node.classList = {
      contains: value => classes.has(value),
      toggle(value, force) { if (force) classes.add(value); else classes.delete(value); },
    };
    node.dataset = {};
    for (const [key, value] of Object.entries(node.attrs)) {
      if (key.startsWith('data-')) node.dataset[key.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = value;
    }
    node.contains = other => node === other || node.children.some(child => child.contains(other));
    node.querySelectorAll = selector => {
      const result = [];
      function visit(n) {
        for (const child of n.children) {
          if (selector.split(',').some(part => matches(child, part.trim()))) result.push(child);
          visit(child);
        }
      }
      visit(node); return result;
    };
    node.querySelector = selector => node.querySelectorAll(selector)[0] || null;
    node.addEventListener = () => {};
    node.replaceChildren = () => { destructive++; };
    node.appendChild = () => { rowMoves++; throw Error('Unexpected queue row mutation'); };
    node.getAttribute = name => node.attrs[name] ?? null;
    node.setAttribute = (name, value) => { node.attrs[name] = value; };
    if (node.attrs.id) ids.set(node.attrs.id, node);
    return node;
  }
  const root = make(fixture.dom);
  const document = {
    documentElement: {lang: fixture.locale}, hidden: false, activeElement: null,
    getElementById: id => ids.get(id) || null,
    querySelectorAll: selector => root.querySelectorAll(selector),
    addEventListener: (name, fn) => { listeners[name] = fn; },
    createElement() { destructive++; throw Error('Unexpected fragment parsing/queue creation'); },
  };
  const window = {UII18n: {t(key, params = {}) {
    return (fixture.catalogue[key] || key).replace(/\{(\w+)\}/g, (_, name) => String(params[name]));
  }}, addEventListener: (name, fn) => { windowListeners[name] = fn; }, scrollX: 0, scrollY: 0};
  const context = {document, window, AbortController, URLSearchParams, console,
    setTimeout(fn, delay) { const id = ++timerId; timers.set(id, {fn, delay}); return id; },
    clearTimeout: id => timers.delete(id),
    fetch: async (url, options) => {
      requests.push(url);
      assert.equal(options.headers['X-UI-Language'], fixture.locale);
      assert.equal(options.credentials, 'same-origin');
      assert.equal(options.cache, 'no-store');
      assert.ok(options.signal instanceof AbortSignal);
      if (url === '/fragment') {
        assert.ok(failFragment);
        throw Error('Transient fragment failure');
      }
      return {ok: true, json: async () => ({...fixture.state, discussion_revision: revision,
        ...(fixture.voting === null ? {} : {voting_html: fixture.voting})})};
    },
  };
  vm.runInNewContext(source, context);
  window.DeliberationPolling.floor({userId: fixture.userId, generalFloor: !fixture.proposal,
    proposalFloor: fixture.proposal, url: '/state', fragmentUrl: '/fragment',
    list: fixture.list, voting: 'pf-voting', initialState: fixture.state,
    initialRevision: fixture.revision, initialVoting: fixture.voting});
  async function tick(delay) {
    const entry = [...timers.entries()].find(([, task]) => task.delay === delay);
    assert.ok(entry, `Expected scheduled delay ${delay}`);
    timers.delete(entry[0]); await entry[1].fn();
  }
  await tick(0); // Initial render must not wait for this network response.
  assert.deepEqual(requests, ['/state']);
  assert.equal(destructive, 0);
  assert.equal(rowMoves, 0);
  const before = Object.fromEntries([...ids].filter(([id]) => ['qb-now','qb-ahead','qb-left','admin-now','floor-open-badge','np-text','reply-chip'].includes(id))
    .map(([id, node]) => [id, [node.textContent, node.style.display]]));
  await tick(4000);
  for (const [id, [text, display]] of Object.entries(before)) {
    assert.equal(ids.get(id).textContent, text, id + ' initial/polled label');
    assert.equal(ids.get(id).style.display, display, id + ' initial/polled visibility');
  }
  assert.equal(destructive, 0); assert.equal(rowMoves, 0);
  // Failed content must not consume its revision; retain the initial DOM and retry.
  revision = '99:999'; failFragment = true;
  await tick(4000); await tick(8000);
  assert.equal(requests.filter(url => url === '/fragment').length, 2);
  assert.equal(destructive, 0); assert.equal(rowMoves, 0);
  document.hidden = true; listeners.visibilitychange();
  assert.equal(timers.size, 0);
  document.hidden = false; listeners.visibilitychange();
  await tick(0);
  windowListeners.pagehide();
  assert.equal(timers.size, 0);
}
(async () => {
  for (const fixture of fixtures) await check(fixture);
  console.log(`PASS: ${fixtures.length} initial/poll lifecycles, repeated reads, retries, visibility and cleanup`);
})().catch(error => { console.error(error); process.exitCode = 1; });
