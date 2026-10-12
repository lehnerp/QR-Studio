/* Bulk page */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  const rows = $('bulk-rows'), kindSel = $('bulk-kind'), fmtSel = $('bulk-format');
  const go = $('bulk-go'), count = $('bulk-count');
  const img = $('preview-img'), empty = $('preview-empty'), spinner = $('spinner'), errBox = $('preview-error');

  const design = DesignPanel(() => preview());

  function lines() {
    return rows.value.split(/\r?\n/).map(l => l.trim()).filter(Boolean);
  }

  function firstContent() {
    const first = lines()[0];
    if (!first) return '';
    const comma = first.indexOf(',');
    // "name, content" unless the line itself looks like a URL with commas
    return comma > -1 && !/^[a-z]+:\/\//i.test(first) ? first.slice(comma + 1).trim() : first;
  }

  let seq = 0;
  const preview = QR.debounce(async () => {
    const n = lines().length;
    count.textContent = `${n} item${n === 1 ? '' : 's'}`;
    go.disabled = n === 0;
    const content = firstContent();
    if (!content) { img.hidden = true; empty.hidden = false; errBox.hidden = true; return; }
    const my = ++seq;
    spinner.hidden = false;
    const kind = kindSel.value;
    try {
      const res = await QR.api('/api/preview', {
        method: 'POST',
        body: { kind, fields: kind === 'url' ? { url: content } : { text: content }, design: design.get() },
      });
      if (my !== seq) return;
      img.src = res.image; img.hidden = false; empty.hidden = true; errBox.hidden = true;
    } catch (err) {
      if (my !== seq) return;
      errBox.textContent = err.message; errBox.hidden = false;
    } finally {
      if (my === seq) spinner.hidden = true;
    }
  }, 250);

  rows.addEventListener('input', preview);
  kindSel.addEventListener('change', preview);

  $('bulk-file').addEventListener('change', e => {
    const file = e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = ev => { rows.value = String(ev.target.result).trim(); preview(); };
    reader.readAsText(file);
    e.target.value = '';
  });

  go.addEventListener('click', async () => {
    const label = go.textContent;
    go.disabled = true;
    go.textContent = `Generating ${lines().length}…`;
    try {
      const res = await QR.api('/api/bulk', {
        method: 'POST', raw: true,
        body: { kind: kindSel.value, rows: rows.value, format: fmtSel.value, design: design.get(), save: $('bulk-save').checked },
      });
      await QR.downloadFrom(res, 'qr-codes.zip');
      QR.toast('ZIP downloaded');
      QR.refreshStats();
    } catch (err) {
      QR.toast(err.message, 'err');
    } finally {
      go.textContent = label;
      go.disabled = lines().length === 0;
    }
  });

  preview();
})();
