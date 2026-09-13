/* Run actual page-scoped translations and JST presentation without a browser/DB. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const fixtures = JSON.parse(fs.readFileSync(0, 'utf8'));
for (const fixture of fixtures) {
  const context = vm.createContext({window: {}, document: {
    documentElement: {lang: fixture.locale},
    getElementById: () => ({textContent: JSON.stringify(fixture.catalogue)}),
    addEventListener() {}
  }});
  for (const name of ['ui-i18n.js', 'jst-time.js']) {
    vm.runInContext(fs.readFileSync('app/static/js/' + name, 'utf8'), context);
  }
  const format = context.window.formatJstTimestamp;
  for (const value of ['2027-05-20T09:46:00Z', '2027-05-20 09:46:00', '2027-05-20T18:46:00+09:00']) {
    // Existing initial floor HTML and polls must retain the exact same timestamp.
    assert.equal(format(value), '2027-05-20 18:46 JST');
    const localized = format(value, true);
    if (fixture.locale === 'ja') {
      assert.match(localized, /2027\/05\/20 18:46 JST/);
      assert.doesNotMatch(localized, /May| at |AM|PM/);
    } else assert.equal(localized, '2027-05-20 18:46 JST');
  }
  assert.equal(format('2027-05-20T23:46:00Z'), '2027-05-21 08:46 JST');
  assert.equal(format(''), '');
  assert.equal(format('unrecognized'), 'unrecognized');
  const dateOutput = vm.runInContext("window.formatJstTimestamp(new Date('2027-05-20T09:46:00Z'), true)", context);
  assert.ok(dateOutput.includes('18:46'));
  const notice = {type: 'FLOOR', message: 'You have the floor — Right of Reply to #9: “Authored <title> 日本語”', payload: {kind:'ROR'}};
  const original = JSON.stringify(notice);
  const output = context.window.UII18n.notificationText(notice);
  assert.ok(output.includes('“Authored <title> 日本語”'));
  if (fixture.locale === 'ja') { assert.ok(output.includes('答弁権')); assert.ok(!output.includes('You have the floor')); }
  else assert.equal(output, notice.message);
  assert.equal(JSON.stringify(notice), original);
  const arbitrary = {type:'ANNOUNCEMENT',message:'You have the floor — User-written English'};
  assert.equal(context.window.UII18n.notificationText(arbitrary), arbitrary.message);
}
console.log('PASS: JST, pinned locale, unchanged floor timestamps and notification payloads');
