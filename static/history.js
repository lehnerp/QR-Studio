/* Library page */
(function () {
  'use strict';

  const list = document.getElementById('library');
  const emptyEl = document.getElementById('library-empty');
  const tpl = document.getElementById('card-tpl');
  const search = document.getElementById('search');
  const kindFilter = document.getElementById('kind-filter');
  const favFilter = document.getElementById('fav-filter');

  function fmtDate(iso) {
    const d = new Date(iso);
    return d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
  }

  function render(codes) {
    list.replaceChildren();
    const filtered = search.value || kindFilter.value || favFilter.checked;
    emptyEl.hidden = codes.length > 0;
    if (!codes.length && filtered) {
      emptyEl.hidden = true;
      const p = document.createElement('p');
      p.className = 'hint';
      p.textContent = 'No codes match these filters.';
      list.append(p);
    }
    codes.forEach(c => {
      const node = tpl.content.firstElementChild.cloneNode(true);
      node.dataset.id = c.id;
      const t = node.querySelector('.thumb img');
      t.src = `/codes/${c.id}/thumb.png?v=${encodeURIComponent(c.updated_at)}`;
      t.alt = `QR code: ${c.name}`;
      node.querySelector('.badge').textContent = c.type_label;
      node.querySelector('.code-name').textContent = c.name;
      node.querySelector('.code-payload').textContent = c.payload
        .replace(/(^WIFI:.*?P:)((?:\\.|[^;])*)/, (_, a) => `${a}••••••`)
        .replace(/\s+/g, ' ');
      node.querySelector('.code-date').textContent =
        `${fmtDate(c.created_at)} · ${c.downloads} download${c.downloads === 1 ? '' : 's'}`;
      const star = node.querySelector('.star');
      star.classList.toggle('on', c.favorite);
      star.setAttribute('aria-pressed', c.favorite);
      node.querySelectorAll('[data-edit]').forEach(a => { a.href = `/?load=${c.id}`; });
      node.querySelector('[data-png]').href = `/codes/${c.id}.png`;
      node.querySelector('[data-svg]').href = `/codes/${c.id}.svg`;
      list.append(node);
    });
  }

  async function load() {
    const params = new URLSearchParams();
    if (search.value.trim()) params.set('q', search.value.trim());
    if (kindFilter.value) params.set('kind', kindFilter.value);
    if (favFilter.checked) params.set('favorites', '1');
    try {
      const { codes } = await QR.api(`/api/codes?${params}`);
      render(codes);
    } catch (err) { QR.toast(err.message, 'err'); }
  }

  async function loadStats() {
    const s = await QR.refreshStats();
    const bars = document.getElementById('type-bars');
    bars.replaceChildren();
    if (!s || !s.by_type.length) { bars.textContent = '—'; return; }
    const max = Math.max(...s.by_type.map(t => t.count));
    s.by_type.slice(0, 5).forEach(t => {
      const row = document.createElement('div');
      row.className = 'bar';
      row.innerHTML = '<span class="bar-label"></span><span class="bar-track"><span class="bar-fill"></span></span><span class="bar-num"></span>';
      row.querySelector('.bar-label').textContent = t.label;
      row.querySelector('.bar-fill').style.width = `${(t.count / max) * 100}%`;
      row.querySelector('.bar-num').textContent = t.count;
      bars.append(row);
    });
  }

  list.addEventListener('click', async e => {
    const card = e.target.closest('.code-card');
    if (!card) return;
    const id = card.dataset.id;

    if (e.target.closest('.star')) {
      const on = !e.target.classList.contains('on');
      try {
        await QR.api(`/api/codes/${id}`, { method: 'PATCH', body: { favorite: on } });
        e.target.classList.toggle('on', on);
        if (favFilter.checked && !on) card.remove();
      } catch (err) { QR.toast(err.message, 'err'); }
    }

    if (e.target.closest('[data-delete]')) {
      const btn = e.target.closest('[data-delete]');
      if (btn.dataset.armed !== '1') {
        btn.dataset.armed = '1';
        btn.textContent = 'Confirm';
        setTimeout(() => { btn.dataset.armed = ''; btn.textContent = 'Delete'; }, 3000);
        return;
      }
      try {
        await QR.api(`/api/codes/${id}`, { method: 'DELETE' });
        card.remove();
        QR.toast('Deleted');
        loadStats();
        if (!list.querySelector('.code-card')) load();
      } catch (err) { QR.toast(err.message, 'err'); }
    }

    if (e.target.closest('[data-png], [data-svg]')) setTimeout(loadStats, 800);
  });

  // inline rename
  list.addEventListener('click', e => {
    const h = e.target.closest('.code-name');
    if (!h || h.isContentEditable) return;
    h.contentEditable = 'true';
    h.focus();
    document.getSelection().selectAllChildren(h);
    const original = h.textContent;
    const finish = async (commit) => {
      h.contentEditable = 'false';
      const name = h.textContent.trim();
      if (!commit || !name || name === original) { h.textContent = original; return; }
      try {
        await QR.api(`/api/codes/${h.closest('.code-card').dataset.id}`, { method: 'PATCH', body: { name } });
        QR.toast('Renamed');
      } catch (err) { h.textContent = original; QR.toast(err.message, 'err'); }
    };
    h.addEventListener('keydown', function onKey(ev) {
      if (ev.key === 'Enter') { ev.preventDefault(); h.removeEventListener('keydown', onKey); h.blur(); }
      if (ev.key === 'Escape') { h.textContent = original; h.removeEventListener('keydown', onKey); h.blur(); }
    });
    h.addEventListener('blur', () => finish(true), { once: true });
  });

  search.addEventListener('input', QR.debounce(load, 200));
  kindFilter.addEventListener('change', load);
  favFilter.addEventListener('change', load);

  load();
  loadStats();
})();
