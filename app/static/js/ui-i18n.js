/* Document-scoped UI strings and explicit language-switch navigation. */
(function () {
  'use strict';
  const locale = document.documentElement.lang === 'ja' ? 'ja' : 'en';
  const messages = JSON.parse(document.getElementById('ui-i18n-catalogue').textContent);
  function t(key, params = {}) {
    const message = Object.prototype.hasOwnProperty.call(messages, key) ? messages[key] : key;
    return message.replace(/\{([A-Za-z_][A-Za-z0-9_]*)\}/g, (match, name) =>
      Object.prototype.hasOwnProperty.call(params, name) ? String(params[name]) : match);
  }
  window.UII18n = Object.freeze({locale, t});

  function hasChangedInput() {
    return Array.from(document.querySelectorAll('input, textarea, select')).some(el => {
      if (el.closest('[data-ui-language]')) return false;
      if (el.type === 'file') return el.files.length > 0;
      if (['submit', 'button', 'reset', 'image'].includes(el.type)) return false;
      // Hidden reply IDs can be populated by polling without a user edit.
      if (el.type === 'hidden') return false;
      // Reset only a detached clone: native defaults handle selects, checkboxes,
      // and date/number value normalization without touching the real form.
      const baseline = el.cloneNode(true);
      baseline.removeAttribute('form');
      const form = document.createElement('form');
      form.appendChild(baseline);
      HTMLFormElement.prototype.reset.call(form);
      if (el.tagName === 'SELECT') {
        return Array.from(el.options).some((option, index) => option.selected !== baseline.options[index].selected);
      }
      if (el.type === 'checkbox' || el.type === 'radio') return el.checked !== baseline.checked;
      return el.value !== baseline.value;
    });
  }
  document.addEventListener('submit', event => {
    if (!event.target.matches('[data-ui-language]')) return;
    if (hasChangedInput() && !window.confirm(t('ui.unsaved_language'))) {
      event.preventDefault();
      event.stopImmediatePropagation();
    }
  }, true);
})();
