/* Execute rendered tour definitions with the real page translator. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const fixtures = JSON.parse(fs.readFileSync(0, 'utf8'));
const translator = fs.readFileSync('app/static/js/ui-i18n.js', 'utf8');
const previous = new Map();
for (const fixture of fixtures) {
  let captured;
  const buttons = [];
  const context = {
    document: {
      documentElement: {lang: fixture.locale},
      getElementById(id) {
        if (id === 'ui-i18n-catalogue') return {textContent: JSON.stringify(fixture.messages)};
        return {addEventListener(event, fn) { buttons.push(fn); }};
      },
      addEventListener(event, fn) { if (event === 'DOMContentLoaded') fn(); }
    },
    window: {
      DialogueTour: {
        completed() { return false; },
        start(steps, options) { captured = {steps, options}; }
      },
      setTimeout(fn, delay) { assert.equal(delay, ['dashboard.html', 'events_menu.html'].includes(fixture.page) ? 700 : 500); fn(); }
    }
  };
  vm.createContext(context);
  vm.runInContext(translator, context);
  vm.runInContext(fixture.script, context, {filename: fixture.page});
  assert.ok(captured && captured.steps.length);
  assert.equal(buttons.length, 1);
  for (const step of captured.steps) {
    assert.ok(Object.values(fixture.messages).includes(step.title), step.title);
    assert.ok(Object.values(fixture.messages).includes(step.text), step.text);
    assert.ok(!step.title.startsWith('tutorial.'));
    if (fixture.locale === 'ja') assert.match(step.text, /[ぁ-んァ-ヶ一-龯]/);
  }
  if (fixture.page === 'events_menu.html') {
    assert.equal(captured.steps.some(step => step.target === '[data-key="review_agenda"]'), fixture.privileged);
  }
  const invariant = JSON.stringify({options: captured.options,
    steps: captured.steps.map(({title, text, ...unchanged}) => unchanged)});
  const key = fixture.page + fixture.privileged;
  if (previous.has(key)) assert.equal(invariant, previous.get(key));
  previous.set(key, invariant);
  buttons[0]();
}
console.log(`${fixtures.length} tutorial variants passed`);

// Exercise engine labels, fallback, target filtering and completion without a browser.
const engine = fs.readFileSync('app/static/js/guided-tour.js', 'utf8');
for (const locale of ['en', 'ja', 'fallback']) {
  const nodes = new Map();
  function node() {
    return {style: {}, dataset: {}, attributes: {}, listeners: {}, textContent: '',
      classList: {add() {}, remove() {}, toggle() {}},
      setAttribute(key, value) { this.attributes[key] = value; },
      addEventListener(event, fn) { this.listeners[event] = fn; },
      querySelector(selector) {
        if (!nodes.has(selector)) nodes.set(selector, node());
        return nodes.get(selector);
      },
      getBoundingClientRect() { return {top: 40, bottom: 100, left: 40, right: 100, width: 60, height: 60}; },
      scrollIntoView() {}
    };
  }
  const messages = fixtures.find(f => f.locale === (locale === 'fallback' ? 'en' : locale)).messages;
  const writes = [];
  const target = node();
  const context = {
    document: {
      documentElement: {lang: locale},
      getElementById() { return {textContent: JSON.stringify(messages)}; },
      createElement: node, body: {appendChild() {}}, addEventListener() {},
      querySelector(selector) { return selector === '#missing' ? null : target; }
    },
    window: {innerWidth: 1200, innerHeight: 900, addEventListener() {},
      getComputedStyle() { return {display: 'block', visibility: 'visible'}; },
      setTimeout(fn, delay) { assert.equal(delay, 260); fn(); }},
    localStorage: {setItem(...args) { writes.push(args); }, getItem() { return null; }}
  };
  vm.createContext(context);
  if (locale !== 'fallback') vm.runInContext(translator, context);
  vm.runInContext(engine, context);
  context.window.DialogueTour.start([
    {target: '#one', title: '<b>literal</b>', text: 'Authored unchanged'},
    {target: '#missing'}, {target: '#two', title: 'Second', text: 'Second text'}
  ], {storageKey: 'unchanged-tour-key'});
  const label = name => messages['tutorial.common.' + name];
  assert.equal(nodes.get('#dialogue-tour-close').attributes['aria-label'], label('close'));
  assert.equal(nodes.get('#dialogue-tour-skip').textContent, label('skip'));
  assert.equal(nodes.get('#dialogue-tour-back').textContent, label('back'));
  assert.equal(nodes.get('#dialogue-tour-next').textContent, label('next'));
  assert.equal(nodes.get('#dialogue-tour-step').textContent, label('step').replace('{current}', 1).replace('{total}', 2));
  assert.equal(nodes.get('#dialogue-tour-title').textContent, '<b>literal</b>');
  assert.equal(nodes.get('#dialogue-tour-text').textContent, 'Authored unchanged');
  nodes.get('#dialogue-tour-next').listeners.click();
  assert.equal(nodes.get('#dialogue-tour-next').textContent, label('done'));
  nodes.get('#dialogue-tour-back').listeners.click();
  assert.equal(nodes.get('#dialogue-tour-next').textContent, label('next'));
  nodes.get('#dialogue-tour-next').listeners.click();
  nodes.get('#dialogue-tour-next').listeners.click();
  assert.deepEqual(writes, [['unchanged-tour-key', 'completed']]);
}
console.log('Engine EN/JA/fallback controls passed');
