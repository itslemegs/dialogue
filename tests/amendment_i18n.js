/* Execute the real amendment builder with a minimal DOM double; no AI/network. */
'use strict';
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const fixtures = JSON.parse(fs.readFileSync(0, 'utf8'));
let original;
for (const fixture of fixtures) {
  class Element {
    constructor(tag) { this.tag = tag; this.attrs = {}; this.children = []; this.handlers = {}; this.dataset = {}; }
    setAttribute(k,v) { this.attrs[k] = v; }
    appendChild(child) { child.parent = this; this.children.push(child); return child; }
    remove() { this.parent.children = this.parent.children.filter(c => c !== this); }
    set innerHTML(value) { assert.equal(value, ''); this.children = []; }
    set textContent(value) { this.children = []; this.text = value; }
    get textContent() { return this.text || this.children.map(c => c.textContent).join(''); }
    set value(value) { this.selected = value; }
    get value() { return this.selected ?? (this.tag === 'select' ? this.children[0]?.attrs.value : this.attrs.value) ?? ''; }
    addEventListener(name, fn) { this.handlers[name] = fn; }
    querySelectorAll(selector) {
      const match = /^\[([^=\]]+)(?:="([^"]+)")?\]$/.exec(selector);
      assert.ok(match, selector);
      const result = [];
      const visit = node => { for (const child of node.children || []) {
        if (Object.hasOwn(child.attrs || {}, match[1]) && (match[2] === undefined || child.attrs[match[1]] === match[2])) result.push(child);
        visit(child);
      }};
      visit(this); return result;
    }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  }
  const ids = Object.fromEntries(['amend-tool','ops','add-op','amend-form','amend-body','am_btn_generate','am_ai_status','am_plain_text']
    .map(id => [id, new Element('div')]));
  ids['amend-tool'].dataset.generateEndpoint = '/unused-ai';
  const logs = [], alerts = [];
  const window = {sessionLog: {send: (action,details) => logs.push({action,details})}, UII18n: {t(key,params={}) {
    assert.ok(Object.hasOwn(fixture.catalogue,key),key);
    return fixture.catalogue[key].replace(/\{(\w+)\}/g,(_,name)=>String(params[name]));
  }}};
  const document = {getElementById: id => ids[id], createElement: tag => new Element(tag),
    createTextNode: text => ({textContent: text})};
  const source = fixture.script.replace(/\}\)\(\);\s*$/, `window.testBuilder = {ACTIONS, CLAUSE_TYPES, buildMarkdown, fillFromOperations, validateRows, displayAmendmentError};\n})();`);
  vm.runInNewContext(source, {window, document, console, alert: value => alerts.push(value),
    fetch: () => {throw Error('Unexpected AI/translation');}});
  const api = window.testBuilder;
  assert.deepEqual(Array.from(api.ACTIONS, item => item.v), ['REMOVE','ADD','REPLACE']);
  assert.deepEqual(Array.from(api.CLAUSE_TYPES, item => item.v), ['preambular','operative']);
  if (fixture.locale === 'ja') {
    assert.deepEqual(Array.from(api.ACTIONS,item=>item.label),['削除','追加','置換']);
    assert.deepEqual(Array.from(api.CLAUSE_TYPES,item=>item.label),['前文条項','主文条項']);
  }
  const content = 'Authored 日本語 <literal> & unchanged';
  api.fillFromOperations([
    {action:'ADD',clause_type:'operative',target:'4(a)',content},
    {action:'REMOVE',clause_type:'preambular',target:'2',content:''},
    {action:'REPLACE',clause_type:'operative',target:'7',content},
  ]);
  assert.equal(api.validateRows(),'');
  ids['amend-form'].handlers.submit({preventDefault(){throw Error('Valid operations blocked');}});
  const markdown = ids['amend-body'].value;
  assert.ok(markdown.includes('ADD the following operative 4(a)'));
  assert.ok(markdown.includes('REMOVE the whole preambular 2'));
  assert.ok(markdown.includes('REPLACE in the operative 7'));
  assert.ok(markdown.includes(content));
  if (original) assert.equal(markdown,original); else original = markdown;
  api.fillFromOperations([]);
  let blocked = false;
  ids['amend-form'].handlers.submit({preventDefault(){blocked=true;}});
  assert.ok(blocked);
  assert.equal(logs.at(-1).details.error,'Operation 1: target is required.');
  assert.equal(alerts.at(-1), fixture.locale === 'ja' ? '変更1：対象を指定してください。' : 'Operation 1: target is required.');
  assert.equal(api.displayAmendmentError('<unrecognized provider error>'),'<unrecognized provider error>');
}
console.log('PASS: EN/JA builder labels, identical raw submissions, validation and logs');
