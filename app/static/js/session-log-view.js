/* Local filters over the latest 300 records; no requests or telemetry. */
(function () {
  'use strict';
  const form = document.getElementById('log-filters');
  if (!form) return;
  const rows = Array.from(document.querySelectorAll('[data-log-row]'));
  const groups = Array.from(document.querySelectorAll('[data-log-group]'));
  const normalized = value => String(value || '').toLowerCase().trim();
  function apply() {
    const values = Object.fromEntries(new FormData(form));
    const visible = rows.filter(row => {
      const data = row.dataset;
      const matches = ['activity', 'source', 'action', 'target'].every(key => !values[key] || data[key] === values[key]) &&
        normalized(data.identity).includes(normalized(values.identity)) &&
        normalized(data.search).includes(normalized(values.search));
      row.hidden = !matches;
      return matches;
    });
    groups.forEach(group => {
      const count = visible.filter(row => row.dataset.activity === group.dataset.logGroup).length;
      group.hidden = count === 0;
      group.querySelector('[data-group-count]').textContent = String(count);
    });
    const counts = {
      visible: visible.length,
      sessions: new Set(visible.map(row => row.dataset.session).filter(Boolean)).size,
      semantic: visible.filter(row => row.dataset.source === 'semantic').length,
      client: visible.filter(row => row.dataset.source === 'client').length
    };
    Object.entries(counts).forEach(([key, value]) => {
      document.querySelector(`[data-count="${key}"]`).textContent = String(value);
    });
    document.getElementById('log-empty').hidden = visible.length !== 0;
  }
  form.addEventListener('submit', event => event.preventDefault());
  form.addEventListener('input', apply);
  form.addEventListener('change', apply);
  form.addEventListener('reset', () => queueMicrotask(apply));
  apply();
})();
