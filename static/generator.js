/* Create page: content tabs, live preview, downloads, library save */
(function () {
  'use strict';

  const DRAFT_KEY = 'qrstudio.draft.v1';
  const $ = id => document.getElementById(id);
  const img = $('preview-img'), empty = $('preview-empty'), spinner = $('spinner'), errBox = $('preview-error');
  const meta = $('meta'), payloadBox = $('payload-box'), payloadPre = $('payload'), nameInput = $('code-name');
  const actionBtns = ['dl-png', 'dl-svg', 'save-btn', 'copy-btn'].map($);

  let kind = 'url';
  let ready = false;
  let inflight = null;
  let seq = 0;

  const design = DesignPanel(() => schedule());

  // ---------- content
  function fieldset(k = kind) { return document.querySelector(`fieldset[data-kind="${k}"]`); }

  function getFields() {
    const out = {};
    fieldset().querySelectorAll('[data-field]').forEach(el => {
      out[el.dataset.field] = el.type === 'checkbox' ? el.checked : el.value;
    });
    return out;
  }

  function setFields(k, fields) {
    fieldset(k).querySelectorAll('[data-field]').forEach(el => {
      const v = fields?.[el.dataset.field];
      if (v === undefined) return;
      if (el.type === 'checkbox') el.checked = Boolean(v); else el.value = v;
    });
  }

  function setKind(k) {
    kind = k;
    document.querySelectorAll('.type').forEach(b => {
      const on = b.dataset.kind === k;
      b.classList.toggle('on', on);
      b.setAttribute('aria-selected', on);
    });
    document.querySelectorAll('fieldset[data-kind]').forEach(fs => { fs.hidden = fs.dataset.kind !== k; });
    syncWifi();
  }

  function syncWifi() {
    const enc = document.querySelector('fieldset[data-kind="wifi"] [data-field="encryption"]').value;
    document.querySelector('[data-hide-if-open]').hidden = enc === 'nopass';
  }

  document.getElementById('types').addEventListener('click', e => {
    const b = e.target.closest('.type');
    if (!b) return;
    if (b.dataset.kind !== kind) nameInput.value = '';
    setKind(b.dataset.kind);
    schedule(0);
    const first = fieldset().querySelector('input, textarea');
    first?.focus();
  });

  document.getElementById('content-form').addEventListener('input', () => { syncWifi(); schedule(); });
  document.getElementById('content-form').addEventListener('change', () => { syncWifi(); schedule(); });
  document.querySelector('.pw-toggle').addEventListener('click', e => {
    const input = e.target.previousElementSibling;
    const show = input.type === 'password';
    input.type = show ? 'text' : 'password';
    e.target.textContent = show ? 'Hide' : 'Show';
  });

  // ---------- preview
  function state() { return { kind, fields: getFields(), design: design.get() }; }

  function setReady(on) {
    ready = on;
    actionBtns.forEach(b => { b.disabled = !on; });
  }

  function hasInput() {
    return [...fieldset().querySelectorAll('[data-field]')]
      .some(el => el.type !== 'checkbox' && el.tagName !== 'SELECT' && el.value.trim());
  }

  function showEmpty() {
    setReady(false);
    img.hidden = true; empty.hidden = false; meta.hidden = true; payloadBox.hidden = true; errBox.hidden = true;
    spinner.hidden = true;
  }

  async function refresh() {
    const my = ++seq;
    saveDraft();
    if (!hasInput()) { if (inflight) inflight.abort(); showEmpty(); return; }
    if (inflight) inflight.abort();
    inflight = new AbortController();
    spinner.hidden = false;
    try {
      const res = await QR.api('/api/preview', { method: 'POST', body: state(), signal: inflight.signal });
      if (my !== seq) return;
      img.src = res.image;
      img.hidden = false;
      empty.hidden = true;
      errBox.hidden = true;
      meta.hidden = false;
      meta.querySelector('[data-meta="version"]').textContent = res.info.version;
      meta.querySelector('[data-meta="modules"]').textContent = `${res.info.modules}×${res.info.modules}`;
      meta.querySelector('[data-meta="error_level"]').textContent = res.info.error_level;
      meta.querySelector('[data-meta="bytes"]').textContent = `${res.info.bytes} B`;
      payloadPre.textContent = res.payload;
      payloadBox.hidden = false;
      setReady(true);
    } catch (err) {
      if (err.name === 'AbortError' || my !== seq) return;
      setReady(false);
      const incomplete = /required fields|Nothing to encode/.test(err.message);
      img.hidden = incomplete || img.hidden;
      empty.hidden = !img.hidden;
      meta.hidden = payloadBox.hidden = incomplete;
      errBox.hidden = incomplete;
      errBox.textContent = err.message;
    } finally {
      if (my === seq) spinner.hidden = true;
    }
  }

  const debounced = QR.debounce(refresh, 220);
  function schedule(delay) { setReady(false); delay === 0 ? refresh() : debounced(); }

  // ---------- actions
  async function download(fmt) {
    if (!ready) return;
    const btn = $(fmt === 'png' ? 'dl-png' : 'dl-svg');
    const label = btn.textContent;
    btn.textContent = 'Preparing…';
    btn.disabled = true;
    try {
      const res = await QR.api(`/api/download/${fmt}`, { method: 'POST', body: { ...state(), name: nameInput.value }, raw: true });
      await QR.downloadFrom(res, `qr-code.${fmt}`);
      QR.toast(`${fmt.toUpperCase()} downloaded and saved to your library`);
      QR.refreshStats();
    } catch (err) {
      QR.toast(err.message, 'err');
    } finally {
      btn.textContent = label;
      btn.disabled = !ready;
    }
  }

  $('dl-png').addEventListener('click', () => download('png'));
  $('dl-svg').addEventListener('click', () => download('svg'));

  $('save-btn').addEventListener('click', async () => {
    try {
      await QR.api('/api/codes', { method: 'POST', body: { ...state(), name: nameInput.value } });
      QR.toast('Saved to library');
      QR.refreshStats();
    } catch (err) { QR.toast(err.message, 'err'); }
  });

  $('copy-btn').addEventListener('click', async () => {
    try {
      const blob = await (await fetch(img.src)).blob();
      await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })]);
      QR.toast('Image copied to clipboard');
    } catch (_) {
      QR.toast('Your browser blocked clipboard access — use Download instead.', 'err');
    }
  });

  $('reset-btn').addEventListener('click', () => {
    document.getElementById('content-form').reset();
    document.querySelector('[data-preset="classic"]').click();
    design.set({ frame_style: 'none', caption: '', logo: '', size: 512, margin: 4, error_level: 'M', transparent: false });
    nameInput.value = '';
    try { localStorage.removeItem(DRAFT_KEY); } catch (_) { /* storage unavailable */ }
    history.replaceState(null, '', '/');
    setKind('url');
    seq++;
    showEmpty();
  });

  // ---------- drafts & loading from library
  function saveDraft() {
    try {
      const s = state();
      s.design = { ...s.design, logo: '' }; // keep drafts small
      localStorage.setItem(DRAFT_KEY, JSON.stringify({ ...s, name: nameInput.value }));
    } catch (_) { /* storage unavailable */ }
  }

  async function init() {
    const id = new URLSearchParams(location.search).get('load');
    let loaded = null;
    if (id) {
      try { loaded = await QR.api(`/api/codes/${encodeURIComponent(id)}`); } catch (_) { QR.toast('That code no longer exists.', 'err'); }
    }
    if (!loaded) {
      try { loaded = JSON.parse(localStorage.getItem(DRAFT_KEY) || 'null'); } catch (_) { loaded = null; }
    }
    if (loaded) {
      setKind(loaded.kind || 'url');
      setFields(loaded.kind || 'url', loaded.fields);
      design.set(loaded.design || {});
      nameInput.value = loaded.name || '';
      refresh();
    } else {
      setKind('url');
    }
  }

  init();
})();
