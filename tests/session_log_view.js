const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const handlers = {};
let values = {};
const form = {addEventListener(name,fn){handlers[name]=fn;}};
const rows = [
  {dataset:{activity:'general_floor',source:'semantic',action:'POSTED',target:'intervention',session:'one',identity:'@a one',search:'POSTED /floor/8 17'},open:true},
  {dataset:{activity:'drafting',source:'client',action:'SAVE_DRAFT',target:'draft',session:'two',identity:'@b two',search:'SAVE_DRAFT /room/4 4'}}
];
const counts = Object.fromEntries(['visible','sessions','semantic','client'].map(k=>[k,{}]));
const groups=['general_floor','drafting'].map(activity=>({dataset:{logGroup:activity},count:{},querySelector(){return this.count;}}));
const empty={};
const context={
  FormData: class {constructor(){return Object.entries(values);}}, queueMicrotask:fn=>fn(),
  document:{getElementById:id=>id==='log-filters'?form:empty,
    querySelectorAll:selector=>selector==='[data-log-row]'?rows:groups,
    querySelector:selector=>counts[selector.match(/"(.*?)"/)[1]]}
};
vm.runInNewContext(fs.readFileSync('app/static/js/session-log-view.js','utf8'),context);
assert.equal(counts.visible.textContent,'2');
values={source:'client'};handlers.input();
assert.equal(rows[0].hidden,true);assert.equal(groups[0].hidden,true);
assert.equal(counts.sessions.textContent,'1');
values={identity:'@A',search:'17',activity:'general_floor',target:'intervention',action:'POSTED'};handlers.change();
assert.equal(rows[0].hidden,false);assert.equal(rows[1].hidden,true);
assert.equal(rows[0].open,true);
values={search:'no match'};handlers.input();assert.equal(empty.hidden,false);
values={};handlers.reset();assert.equal(counts.visible.textContent,'2');
assert.equal(empty.hidden,true);
console.log('Read-only filters passed');
