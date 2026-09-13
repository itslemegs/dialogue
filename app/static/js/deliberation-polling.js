/* Small, read-only shared-state poller. Notification and AI jobs remain separate. */
(function () {
  'use strict';
  if (window.DeliberationPolling) return;
  const tasks = new Map();
  const interval = 4000;
  const uiLocale = document.documentElement.lang === 'ja' ? 'ja' : 'en';

  function watch(key, run) {
    if (tasks.has(key)) return tasks.get(key);
    let timer, controller, running = false, stopped = false, urgent = false;
    let epoch = 0, failures = 0;
    function cancel() {
      epoch++;
      clearTimeout(timer);
      controller?.abort();
    }
    function schedule(delay) {
      clearTimeout(timer);
      if (!stopped && !document.hidden) timer = setTimeout(tick, delay);
    }
    async function tick() {
      if (running || stopped || document.hidden) return;
      running = true;
      urgent = false;
      const version = epoch;
      controller = new AbortController();
      const signal = controller.signal;
      const timeout = setTimeout(() => controller.abort(), 20000);
      const current = () => !signal.aborted && version === epoch && !stopped && !document.hidden;
      async function read(url, json = true) {
        const response = await fetch(url, {credentials: 'same-origin', cache: 'no-store', signal, headers: {'X-UI-Language': uiLocale}});
        if (!response.ok) throw new Error(`Polling HTTP ${response.status}`);
        const value = json ? await response.json() : await response.text();
        if (!current()) throw new Error('Obsolete polling response');
        return value;
      }
      try {
        await run({read, current});
        failures = 0;
      } catch (_) {
        // Keep the last successful DOM/revision; retry without disruptive errors.
        if (current()) failures = Math.min(failures + 1, 3);
      } finally {
        clearTimeout(timeout);
        running = false;
        schedule(urgent ? 0 : Math.min(interval * (failures + 1), 12000));
      }
    }
    const api = {refresh() {
      cancel();
      urgent = true;
      if (!running) schedule(0);
    }};
    document.addEventListener('visibilitychange', () => {
      cancel();
      if (!document.hidden) api.refresh();
    });
    window.addEventListener('pagehide', () => { stopped = true; cancel(); });
    window.addEventListener('pageshow', () => { stopped = false; api.refresh(); });
    // Do not apply a response started before a user mutation.
    document.addEventListener('submit', () => api.refresh(), true);
    tasks.set(key, api);
    schedule(0);
    return api;
  }

  function preserveScroll(region, apply) {
    const positions = [];
    for (let node = region; node; node = node.parentElement) {
      positions.push([node, node.scrollTop, node.scrollLeft]);
    }
    const x = window.scrollX, y = window.scrollY;
    apply();
    for (const [node, top, left] of positions) {
      node.scrollTop = top;
      node.scrollLeft = left;
    }
    if (window.scrollX !== x || window.scrollY !== y) window.scrollTo(x, y);
  }

  function markup(html) {
    const template = document.createElement('template');
    template.innerHTML = html; // Only same-origin, server-escaped template output.
    return template.content;
  }
  function own(node, selector) {
    return Array.from(node.querySelectorAll(selector)).find(el => el.closest('[data-post-id]') === node);
  }
  function localInput(node) {
    return node.contains(document.activeElement) || Array.from(node.querySelectorAll('input:not([type=hidden]), textarea, select'))
      .some(el => el.value !== el.defaultValue && el.value !== '');
  }

  // Keep existing post/form nodes alive. Only newly observed DB IDs are inserted.
  function reconcilePosts(target, fresh) {
    let complete = true;
    function merge(list, incoming) {
      const existing = new Map(Array.from(list.children).filter(n => n.hasAttribute('data-post-id'))
        .map(n => [n.dataset.postId, n]));
      const posts = Array.from(incoming.children).filter(n => n.hasAttribute('data-post-id'));
      if (posts.length) {
        for (const child of Array.from(list.children)) {
          if (!child.hasAttribute('data-post-id')) child.remove();
        }
      }
      let previous = null;
      for (const next of posts) {
        let node = existing.get(next.dataset.postId);
        if (!node) {
          node = next.cloneNode(true);
          list.insertBefore(node, previous ? previous.nextSibling : list.firstChild);
        } else {
          const replies = own(node, '[data-reply-list]');
          const nextReplies = own(next, '[data-reply-list]');
          if (replies && nextReplies) {
            const hadReplies = !!replies.querySelector('[data-post-id]');
            const expanded = !replies.classList.contains('hidden');
            merge(replies, nextReplies);
            const toggle = own(node, '[data-reply-toggle]');
            const nextToggle = own(next, '[data-reply-toggle]');
            if (toggle && nextToggle) {
              const button = toggle.querySelector('button');
              if (!button) toggle.replaceChildren(...Array.from(nextToggle.childNodes).map(n => n.cloneNode(true)));
              else {
                // Retain the button itself, including focus and its delegated handler.
                for (const selector of ['[data-open]', '[data-closed]']) {
                  const label = button.querySelector(selector), nextLabel = nextToggle.querySelector(selector);
                  if (label && nextLabel) label.textContent = nextLabel.textContent;
                }
              }
            }
            if (toggle) {
              replies.classList.toggle('hidden', !expanded);
              const button = toggle.querySelector('button');
              if (button) {
                button.setAttribute('aria-expanded', String(expanded));
                button.querySelector('[data-open]')?.classList.toggle('hidden', !expanded);
                button.querySelector('[data-closed]')?.classList.toggle('hidden', expanded);
                const chevron = button.querySelector('[data-chevron]');
                if (chevron) chevron.classList.toggle('rotate-180', expanded);
              }
            } else if (!hadReplies && replies.querySelector('[data-post-id]')) {
              replies.classList.remove('hidden'); // Room replies are not collapsible.
            }
          }
          const actions = own(node, '[data-post-actions]');
          const nextActions = own(next, '[data-post-actions]');
          if (actions && nextActions && actions.innerHTML !== nextActions.innerHTML) {
            if (localInput(actions)) complete = false; // Retry permission changes once local editing finishes.
            else actions.innerHTML = nextActions.innerHTML;
          }
        }
        previous = node;
      }
      if (!posts.length && !existing.size && !list.querySelector('[data-empty-message]')) {
        const empty = incoming.querySelector('[data-empty-message]');
        if (empty) list.replaceChildren(empty.cloneNode(true));
      }
    }
    preserveScroll(target, () => merge(target, fresh));
    return complete;
  }

  function queueLabel(value) {
    const keys = {"GENERAL": "floor.queue.general", "ROR": "floor.queue.ror", "ROR_ALL": "floor.ror.all", "SPEAKING": "floor.queue.speaking", "QUEUED": "floor.queue.queued", "DONE": "floor.queue.done", "WITHDRAWN": "floor.queue.withdrawn", "CHAIR": "floor.queue.chair"};
    return keys[value] ? window.UII18n.t(keys[value]) : value;
  }

  function queueRenderer(generalFloor, initialSpeakers) {
    const ui = document.getElementById('admin-queue-ui');
    const state = {filter: 'ALL', showCount: 10, compact: false, speakers: initialSpeakers || []};
    function render() {
      if (!ui) return;
      const active = state.speakers.filter(s => s.status === 'SPEAKING')
        .concat(state.speakers.filter(s => s.status === 'QUEUED'));
      const history = state.speakers.filter(s => s.status === 'DONE' || s.status === 'WITHDRAWN')
        .slice().sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
      const filtered = state.filter === 'DONE' ? history : state.filter === 'QUEUED'
        ? active.filter(s => s.status === 'QUEUED') : active;
      function rows(body, data) {
        if (!body) return;
        const old = new Map(Array.from(body.children).map(row => [row.dataset.requestId, row]));
        const keep = new Set();
        for (const speaker of data) {
          const id = String(speaker.id);
          keep.add(id);
          let row = old.get(id);
          if (!row) {
            row = document.createElement('tr'); row.dataset.requestId = id; row.className = 'border-t';
            for (let i = 0; i < 5; i++) row.appendChild(document.createElement('td'));
          }
          const values = [speaker.position, speaker.handle ? '@' + speaker.handle : '', generalFloor ? queueLabel(speaker.kind) : speaker.kind, generalFloor ? queueLabel(speaker.status) : speaker.status,
            window.formatJstTimestamp(speaker.created_at)];
          values.forEach((value, i) => {
            const cell = row.children[i];
            if (i === 2 || i === 3) {
              const badge = cell.firstElementChild || cell.appendChild(document.createElement('span'));
              const color = i === 2 ? (['ROR', 'ROR_ALL'].includes(speaker.kind)
                ? 'bg-fuchsia-100 text-fuchsia-700' : 'bg-slate-100 text-slate-700') : ({
                  SPEAKING: 'bg-emerald-100 text-emerald-700', QUEUED: 'bg-amber-100 text-amber-700',
                  DONE: 'bg-slate-200 text-slate-700', WITHDRAWN: 'bg-rose-100 text-rose-700'
                }[speaker.status] || 'bg-slate-100 text-slate-600');
              badge.className = 'px-2 py-0.5 rounded text-xs ' + color;
              badge.textContent = value ?? '';
            } else {
              cell.className = i === 4 ? 'text-right text-slate-500' : 'py-1' + (i === 1 ? ' font-medium' : '');
              cell.textContent = value ?? '';
            }
          });
          body.appendChild(row);
        }
        for (const row of Array.from(body.children)) if (!keep.has(row.dataset.requestId)) row.remove();
      }
      preserveScroll(ui, () => {
        rows(ui.querySelector('#table-nextup tbody'), filtered.slice(0, state.showCount));
        rows(document.getElementById('speaker-history'), history);
      });
      const more = document.getElementById('btn-more');
      if (more) more.hidden = filtered.length <= state.showCount;
      ui.classList.toggle('compact', state.compact);
      ui.querySelectorAll('[data-filter]').forEach(button => {
        button.setAttribute('aria-pressed', String(button.dataset.filter === state.filter));
      });
    }
    ui?.querySelectorAll('[data-filter]').forEach(button => button.addEventListener('click', () => {
      state.filter = button.dataset.filter; render();
    }));
    document.getElementById('btn-more')?.addEventListener('click', () => { state.showCount += 20; render(); });
    document.getElementById('toggle-compact')?.addEventListener('click', () => { state.compact = !state.compact; render(); });
    let snapshot = initialSpeakers ? JSON.stringify(initialSpeakers) : null;
    return speakers => {
      const next = JSON.stringify(speakers);
      if (next === snapshot) return;
      state.speakers = speakers;
      render();
      snapshot = next;
    };
  }

  function floor(options) {
    const list = document.getElementById(options.list);
    const votes = document.getElementById(options.voting || '');
    const renderQueue = queueRenderer(options.generalFloor || options.proposalFloor, options.initialState?.speakers);
    const tr = (key, fallback, params = {}) => (options.generalFloor || options.proposalFloor) ? window.UII18n.t(key, params) : fallback;
    let revision = options.initialRevision ?? null;
    let permissions = options.initialState ? JSON.stringify([options.initialState.can_speak, options.initialState.can_manage]) : null;
    let votingHTML = options.initialVoting ?? null;
    let recognition = options.initialState?.current_req_id ?? null;
    // The initial DOM already represents this snapshot. A state read is still
    // immediate, but unchanged state must not initialize/rearrange the UI again.
    const floorKey = data => JSON.stringify([data.is_open, data.can_speak, data.can_manage,
      data.current_req_id, data.current_user_id, data.current_kind,
      data.current_target_intervention_id, data.current_target_local_no, data.speakers]);
    let initialFloor = options.initialState ? floorKey(options.initialState) : null;
    const text = (id, value) => { const el = document.getElementById(id); if (el && el.textContent !== value) el.textContent = value; };
    function show(id, visible) {
      const el = document.getElementById(id);
      if (el) {
        el.classList.toggle('hidden', !visible);
        const display = visible ? '' : 'none';
        if (el.style.display !== display) el.style.display = display;
      }
    }
    function applyFloor(data, reconcileActions = false) {
      const nextFloor = floorKey(data);
      const alreadyRendered = nextFloor === initialFloor;
      initialFloor = null;
      if (!reconcileActions && alreadyRendered) return;
      const speakers = data.speakers || [];
      const current = speakers.find(s => s.status === 'SPEAKING');
      const queued = speakers.filter(s => s.status === 'QUEUED');
      const mine = queued.find(s => Number(s.user_id) === Number(options.userId));
      const recognized = Number(data.current_user_id) === Number(options.userId);
      renderQueue(speakers);
      text('floor-open-badge', tr(data.is_open ? 'floor.open' : 'floor.closed', data.is_open ? 'Open' : 'Closed'));
      const badge = document.getElementById('floor-open-badge');
      if (badge) {
        for (const cls of ['bg-emerald-100', 'text-emerald-700']) badge.classList.toggle(cls, data.is_open);
        for (const cls of ['bg-slate-200', 'text-slate-600']) badge.classList.toggle(cls, !data.is_open);
      }
      text('admin-now', current ? tr('floor.current', `Current: @${current.handle} (req #${current.id})`, {handle: current.handle, id: current.id}) : tr('floor.none_speaking', 'No one currently speaking.'));
      text('qb-now', current ? tr('floor.now', `Now speaking: @${current.handle}`, {handle: current.handle}) : tr('floor.now_empty', 'Now speaking: —'));
      text('np-text', current ? tr('floor.now', `Now speaking: @${current.handle}`, {handle: current.handle}) : tr('floor.now_empty', 'Now speaking: —'));
      text('qb-ahead', tr('floor.ahead', `People before your turn: ${mine ? queued.filter(s => Number(s.position) < Number(mine.position)).length : '—'}`, {count: mine ? queued.filter(s => Number(s.position) < Number(mine.position)).length : '—'}));
      text('qb-left', tr('floor.remaining', `Speakers left in queue: ${queued.length}`, {count: queued.length}));
      show('btn-request-floor', data.is_open && !recognized && !data.can_manage);
      show('btn-withdraw', !!mine && !data.can_manage);
      show('btn-ror-all', !recognized && !data.can_manage);
      show('form-call-next', queued.length > 0);
      show('form-finish-current', !!current);
      show('recognized-banner', recognized);
      show('now-playing', !!current);
      document.querySelectorAll('.ror-form').forEach(el => el.classList.toggle('hidden', recognized || data.can_manage));
      const composer = document.getElementById('composer-box');
      const body = composer?.querySelector('textarea');
      const target = document.getElementById('relates_to_id');
      // A lost turn must not erase or hide an unfinished intervention.
      show('composer-box', data.can_speak || !!body?.value || !!target?.value || !!composer?.contains(document.activeElement));
      show('composer-locked', !data.can_speak);
      composer?.querySelectorAll('button[type="submit"], button:not([type])').forEach(button => { if (button.disabled !== !data.can_speak) button.disabled = !data.can_speak; });
      if (recognition !== data.current_req_id && recognized && !body?.value && target && !target.value) {
        if (data.current_kind === 'ROR' && data.current_target_intervention_id) {
          target.value = data.current_target_intervention_id;
          text('reply-chip', tr('floor.reply_to', `Replying to #${data.current_target_local_no || data.current_target_intervention_id}`, {target: '#' + (data.current_target_local_no || data.current_target_intervention_id)}));
          show('reply-chip', true);
        } else if (data.current_kind === 'ROR_ALL') {
          text('reply-chip', tr('floor.ror.all', 'Right of Reply to all'));
          show('reply-chip', true);
        }
      }
      recognition = data.current_req_id;
    }
    if (votes) {
      votes.addEventListener('pointerdown', () => { votes.__pointer = true; });
      const releasePointer = () => { setTimeout(() => { votes.__pointer = false; }, 0); };
      window.addEventListener('pointerup', releasePointer);
      window.addEventListener('pointercancel', releasePointer);
      votes.addEventListener('submit', event => {
        if (votes.__submitting) event.preventDefault();
        else votes.__submitting = true;
      });
      window.addEventListener('pageshow', () => { votes.__submitting = false; votes.__pointer = false; });
    }
    function applyVoting(html) {
      if (!votes || html === votingHTML || votes.__submitting || votes.__pointer) return;
      const active = votes.contains(document.activeElement) ? document.activeElement : null;
      const form = active?.closest('form');
      const action = form?.getAttribute('action');
      const choice = form?.querySelector('[name=choice]')?.value;
      preserveScroll(votes, () => {
        votes.replaceChildren(markup(html));
        if (action) {
          const replacement = Array.from(votes.querySelectorAll('form')).find(f =>
            f.getAttribute('action') === action && f.querySelector('[name=choice]')?.value === choice);
          replacement?.querySelector('button')?.focus({preventScroll: true});
        }
      });
      votingHTML = html;
    }
    return watch(options.url, async ({read, current}) => {
      const data = await read(options.url);
      applyFloor(data);
      if (data.voting_html !== undefined) applyVoting(data.voting_html);
      const nextPermissions = JSON.stringify([data.can_speak, data.can_manage]);
      if (list && (revision !== data.discussion_revision || permissions !== nextPermissions)) {
        const html = await read(options.fragmentUrl, false);
        const fresh = markup(html);
        const marker = fresh.querySelector('[data-discussion-revision]');
        if (!marker || !current()) throw new Error('Missing/obsolete discussion revision');
        const complete = reconcilePosts(list, fresh);
        window.wireReplyLinks?.();
        applyFloor(data, true);
        if (complete) {
          revision = marker.dataset.discussionRevision;
          permissions = nextPermissions;
        }
      }
    });
  }

  function room(options) {
    const list = document.getElementById(options.list);
    const shared = document.getElementById(options.shared || '');
    let revision = '';
    let draftRevision = '';



    return watch(options.url, async ({read}) => {
      const params = new URLSearchParams({
        revision,
        draft_revision: draftRevision,
      });

      const data = await read(options.url + '?' + params.toString());

      if (data.html !== null) {
        reconcilePosts(list, markup(data.html));
      }
      revision = data.revision;

      if (shared && data.draft_html !== null) {
        /*
         * Avoid replacing the shared controls while someone is actively
         * interacting with them. Keep the old revision so the next poll
         * requests the fragment again.
         */
        if (!shared.contains(document.activeElement)) {
          preserveScroll(shared, () => {
            shared.replaceChildren(markup(data.draft_html));
          });
          draftRevision = data.draft_revision;
        }
      } else {
        draftRevision = data.draft_revision;
      }
    });
  }

  window.DeliberationPolling = {floor, room};
})();
