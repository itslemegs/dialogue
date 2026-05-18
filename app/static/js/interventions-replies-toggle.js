(function () {
    if (window.__interventionsRepliesToggle) return;
    window.__interventionsRepliesToggle = true;
  
    document.addEventListener('click', function (e) {
      const btn = e.target.closest('.js-toggle-replies');
      if (!btn) return;
  
      const targetId = btn.getAttribute('data-target');
      const list = document.getElementById(targetId);
      if (!list) return;
  
      const isHidden = list.classList.contains('hidden');
      list.classList.toggle('hidden');
  
      btn.setAttribute('aria-expanded', String(isHidden));
      const openEl = btn.querySelector('[data-open]');
      const closedEl = btn.querySelector('[data-closed]');
      if (openEl && closedEl) {
        openEl.classList.toggle('hidden', !isHidden);
        closedEl.classList.toggle('hidden', isHidden);
      }
      const chev = btn.querySelector('[data-chevron]');
      if (chev) {
        chev.style.transform = isHidden ? 'rotate(180deg)' : 'rotate(0deg)';
      }
    }, false);
  })();
  