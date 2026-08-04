/* Shared attendance grid. Pages set these globals before loading:
 *   state    = { students: [{id, name, present}] }
 *   MARK_URL = POST endpoint that toggles presence (body {id})
 *   CSRF     = token string for X-CSRFToken header, or null (public page)
 *   ADD_URL  = optional POST endpoint for self-adding a name (public page)
 */
(function () {
  let toastTimer;
  const grid = document.getElementById('grid');
  const searchEl = document.getElementById('search');
  // Public check-in page sets these: hide the roster until a name is typed,
  // and never allow un-marking.
  const requireSearch = (typeof REQUIRE_SEARCH !== 'undefined') && REQUIRE_SEARCH;
  const presentOnly = (typeof PRESENT_ONLY !== 'undefined') && PRESENT_ONLY;

  function escHtml(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function markHeaders() {
    const h = { 'Content-Type': 'application/json' };
    if (typeof CSRF !== 'undefined' && CSRF) h['X-CSRFToken'] = CSRF;
    return h;
  }

  function statusText(present) {
    if (present) return 'Present';
    return presentOnly ? 'Tap to check in' : 'Not marked';
  }

  function render() {
    const q = searchEl.value.trim().toLowerCase();
    const statsEl = document.getElementById('stats');
    if (!state.students.length) {
      grid.innerHTML = '<div class="empty" style="grid-column:1/-1"><h2>No students yet</h2><p>' +
        (requireSearch ? 'Ask your teacher to add the class list.'
                       : 'Use “+ Add students” to build the class list.') + '</p></div>';
      if (statsEl) statsEl.textContent = '';
      return;
    }
    if (requireSearch && !q) {
      grid.innerHTML = '<div class="empty" style="grid-column:1/-1">' +
        '<p>Start typing your name to check in.</p></div>';
      if (statsEl) statsEl.textContent = '';
      return;
    }
    grid.innerHTML = '';
    const noRes = document.createElement('div');
    noRes.className = 'no-results';
    noRes.id = 'no-results';
    grid.appendChild(noRes);

    let visible = 0;
    state.students.forEach(s => {
      const matches = !q || s.name.toLowerCase().includes(q);
      if (requireSearch && !matches) return;  // never show the whole roster
      const card = document.createElement('div');
      card.className = 'kcard' + (s.present ? ' present' : '');
      card.dataset.name = s.name.toLowerCase();
      card.style.display = matches ? '' : 'none';
      if (matches) visible++;
      card.innerHTML =
        '<div class="kcard-icon">' + (s.present ? '✓' : '○') + '</div>' +
        '<div class="kcard-name">' + escHtml(s.name) + '</div>' +
        '<div class="kcard-status">' + statusText(s.present) + '</div>';
      card.addEventListener('click', () => mark(s, card));
      grid.appendChild(card);
    });

    renderNoResults(noRes, visible, q);
    updateStats();
  }

  function renderNoResults(noRes, visible, q) {
    if (visible > 0) { noRes.style.display = 'none'; return; }
    noRes.style.display = 'block';
    if (typeof ADD_URL !== 'undefined' && ADD_URL && q) {
      noRes.innerHTML = 'No match. <button class="btn primary" id="self-add">' +
        '+ Add “' + escHtml(searchEl.value.trim()) + '” &amp; mark present</button>';
      document.getElementById('self-add').addEventListener('click', selfAdd);
    } else {
      noRes.textContent = 'No student found — check the spelling.';
    }
  }

  function updateStats() {
    const el = document.getElementById('stats');
    if (!el) return;
    if (requireSearch) { el.textContent = ''; return; }  // don't reveal roster totals
    if (!state.students.length) { el.textContent = ''; return; }
    const cards = Array.from(document.querySelectorAll('.kcard'))
      .filter(c => c.style.display !== 'none');
    const present = cards.filter(c => c.classList.contains('present')).length;
    const all = state.students.length;
    el.innerHTML = cards.length < all
      ? 'Showing ' + cards.length + ' of ' + all + ' · <b>' + present + '</b> present'
      : all + ' students · <b>' + present + '</b> present today';
  }

  async function mark(s, card) {
    if (presentOnly && s.present) {
      showToast(s.name + ' is already checked in');
      return;  // one-way: can't un-mark from the public page
    }
    card.style.pointerEvents = 'none';
    try {
      const res = await fetch(MARK_URL, {
        method: 'POST', headers: markHeaders(), body: JSON.stringify({ id: s.id })
      });
      const data = await res.json();
      if (!res.ok || data.ok === false) {
        showToast(data.error || 'Could not save — try again', 'warn');
      } else {
        s.present = data.present;
        card.classList.toggle('present', data.present);
        card.querySelector('.kcard-icon').textContent = data.present ? '✓' : '○';
        card.querySelector('.kcard-status').textContent = statusText(data.present);
        updateStats();
        showToast(data.present
          ? '✓  ' + s.name + (presentOnly ? ' — checked in' : ' — present')
          : 'Unmarked: ' + s.name, data.present ? '' : 'warn');
      }
    } catch (e) {
      showToast('Network error — try again', 'warn');
    }
    card.style.pointerEvents = '';
  }

  async function selfAdd() {
    const name = searchEl.value.trim();
    if (!name) return;
    try {
      const res = await fetch(ADD_URL, {
        method: 'POST', headers: markHeaders(), body: JSON.stringify({ name })
      });
      const data = await res.json();
      if (!res.ok || data.ok === false) {
        showToast(data.error || 'Could not add — try again', 'warn');
        return;
      }
      searchEl.value = '';
      await reload();
      showToast('✓  ' + name + ' — added & present');
    } catch (e) {
      showToast('Network error — try again', 'warn');
    }
  }

  async function reload() {
    if (typeof STATE_URL === 'undefined' || !STATE_URL) { render(); return; }
    const fresh = await fetch(STATE_URL).then(r => r.json());
    state.students = fresh.students;
    render();
  }

  function showToast(msg, cls) {
    clearTimeout(toastTimer);
    const t = document.getElementById('toast');
    t.textContent = msg;
    t.className = 'show ' + (cls || '');
    toastTimer = setTimeout(() => { t.className = ''; }, 2400);
  }

  searchEl.addEventListener('input', render);
  render();
})();
