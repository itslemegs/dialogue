const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const handlers = {};
const records = [];
const context = {
  document: {body:{dataset:{eventId:'1',phase:''}}, title:'Test page',
    addEventListener(type,fn){handlers[type]=fn;}},
  window:{location:{pathname:'/events/1/proposal-discussion/2/rooms/3',search:''},
    innerWidth:1000,innerHeight:800,addEventListener(){}},
  navigator:{}, Blob, URL, URLSearchParams,
  fetch(url,options){records.push(JSON.parse(options.body));return Promise.resolve();}
};
vm.createContext(context);
vm.runInContext(fs.readFileSync('app/static/session_log.js','utf8'),context);
context.window.sessionLog.send('ROR_INVITE_REQUESTED',{phase:'general_floor',target_type:'intervention',target_id:'8'});
assert.equal(records.at(-1).phase,'general_floor');
assert.equal(records.at(-1).target_id,'8');
const form = {id:'draft',dataset:{logAction:'SAVE_DRAFT',targetType:'proposal_draft',targetId:'4'},
  action:'https://example.test/events/1/proposal-discussion/2/rooms/3/draft/save',method:'post',
  getAttribute(){return null;},elements:[{name:'title',type:'text',value:'PRIVATE AUTHORED TEXT'},
    {name:'password',type:'password',value:'SECRET'}]};
const submitter = {dataset:{logAction:'CLICK_SUBMIT_DRAFT_AS_L_DOC',logSubmitAction:'SUBMIT_DRAFT_AS_L_DOC'},
  hasAttribute(name){return name==='formaction';},
  formAction:'https://example.test/events/1/proposal-discussion/2/rooms/3/draft/submit'};
handlers.submit({target:form,submitter});
let row=records.at(-1);
assert.equal(row.action,'SUBMIT_DRAFT_AS_L_DOC');
assert.equal(row.target_id,'4');
assert.equal(row.details.interaction,'submit');
assert.equal(row.details.submitter_action,'CLICK_SUBMIT_DRAFT_AS_L_DOC');
assert.ok(row.details.action_path.endsWith('/draft/submit'));
assert.equal(row.details.fields.length,1);
assert.ok(!JSON.stringify(row).includes('PRIVATE AUTHORED TEXT'));
assert.ok(!JSON.stringify(row).includes('SECRET'));
handlers.submit({target:form});
assert.equal(records.at(-1).action,'SAVE_DRAFT');
assert.ok(records.at(-1).details.action_path.endsWith('/draft/save'));
const button={tagName:'BUTTON',id:'save',dataset:{logAction:'CLICK_SAVE_DRAFT'},
  getAttribute(){return null;},innerText:'Save'};
handlers.click({target:{closest(){return button;}}});
assert.equal(records.at(-1).action,'CLICK_SAVE_DRAFT');
assert.equal(records.at(-1).details.interaction,'click');
const count = records.length;
form.tagName = 'FORM';
handlers.click({target:{closest(){return form;}}});
assert.equal(records.length,count, 'Editing a labeled form must not log a submission');
console.log('Session-log labels passed');
