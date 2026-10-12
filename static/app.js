/* Shared helpers for QR Studio */
window.QR = (function () {
  'use strict';

  const toastEl = document.getElementById('toast');
  let toastTimer;

  function toast(message, kind = 'ok') {
    toastEl.textContent = message;
    toastEl.className = `toast show ${kind}`;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { toastEl.className = 'toast'; }, 2600);
  }

  async function api(url, { method = 'GET', body, raw = false, signal } = {}) {
    const res = await fetch(url, {
      method,
      signal,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) {
      let msg = `Request failed (${res.status})`;
      try { msg = (await res.json()).error || msg; } catch (_) { /* not JSON */ }
      throw new Error(msg);
    }
    return raw ? res : res.json();
  }

  async function downloadFrom(res, fallbackName) {
    const blob = await res.blob();
    const cd = res.headers.get('Content-Disposition') || '';
    const match = /filename="?([^";]+)"?/.exec(cd);
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = match ? match[1] : fallbackName;
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  }

  async function refreshStats() {
    try {
      const s = await api('/api/stats');
      document.querySelectorAll('[data-stat]').forEach(el => {
        const v = s[el.dataset.stat];
        if (v !== undefined) el.textContent = Number(v).toLocaleString();
      });
      return s;
    } catch (_) { return null; }
  }

  function debounce(fn, ms) {
    let t;
    return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
  }

  refreshStats();
  return { toast, api, downloadFrom, refreshStats, debounce };
})();
