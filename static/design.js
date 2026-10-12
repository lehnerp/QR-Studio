/* Design panel: reads/writes every [data-design] control and wires presets, logo, etc. */
window.DesignPanel = function (onChange) {
  'use strict';

  const controls = () => document.querySelectorAll('[data-design]');
  const byKey = key => document.querySelector(`[data-design="${key}"]`);
  const eyeToggle = document.getElementById('eye-color-toggle');
  const HEX = /^#[0-9a-fA-F]{6}$/;

  const PRESETS = {
    classic: { foreground: '#111827', background: '#ffffff', gradient: 'none', shape: 'square', eye_shape: 'square', frame_color: '#111827', eye: null },
    indigo:  { foreground: '#4338ca', foreground2: '#7c3aed', gradient: 'linear', background: '#ffffff', shape: 'rounded', eye_shape: 'rounded', frame_color: '#4338ca', eye: null },
    ocean:   { foreground: '#0369a1', foreground2: '#0d9488', gradient: 'radial', background: '#f0f9ff', shape: 'dots', eye_shape: 'circle', frame_color: '#0369a1', eye: null },
    sunset:  { foreground: '#e11d48', foreground2: '#f59e0b', gradient: 'linear', background: '#fffbeb', shape: 'leaf', eye_shape: 'rounded', frame_color: '#e11d48', eye: '#be123c' },
    forest:  { foreground: '#166534', background: '#f0fdf4', gradient: 'none', shape: 'gapped', eye_shape: 'square', frame_color: '#166534', eye: '#14532d' },
    night:   { foreground: '#0f172a', background: '#f8fafc', gradient: 'none', shape: 'rounded', eye_shape: 'circle', frame_color: '#0f172a', eye: '#4338ca' },
  };

  function get() {
    const d = {};
    controls().forEach(el => {
      const key = el.dataset.design;
      d[key] = el.type === 'checkbox' ? el.checked : el.value;
    });
    if (!eyeToggle?.checked) d.eye_color = '';
    if (d.gradient === 'none') d.foreground2 = '';
    d.size = Number(d.size);
    d.margin = Number(d.margin);
    d.logo_scale = Number(d.logo_scale);
    return d;
  }

  function set(d) {
    Object.entries(d || {}).forEach(([key, value]) => {
      const el = byKey(key);
      if (!el || value === undefined || value === null) return;
      if (el.type === 'checkbox') el.checked = Boolean(value);
      else if (el.type === 'color') { if (HEX.test(value)) el.value = value; }
      else el.value = value;
    });
    if ('eye_color' in (d || {})) eyeToggle.checked = Boolean(d.eye_color);
    if (d && d.logo !== undefined) showLogo(d.logo);
    sync();
  }

  // ---- visual sync: hex fields, segments, conditional rows, outputs
  function sync() {
    document.querySelectorAll('.hex').forEach(h => {
      const src = byKey(h.dataset.for);
      if (src && document.activeElement !== h) h.value = src.value;
    });
    document.querySelectorAll('[data-seg]').forEach(seg => {
      const val = byKey(seg.dataset.seg).value;
      seg.querySelectorAll('button').forEach(b => b.classList.toggle('on', b.dataset.value === val));
    });
    const d = get();
    document.querySelectorAll('[data-show-if]').forEach(el => {
      const rule = el.dataset.showIf;
      el.hidden = rule === 'eyecolor' ? !eyeToggle.checked : d.gradient === 'none';
    });
    setText('size-out', `${d.size} px`);
    setText('margin-out', d.margin);
    setText('logo-scale-out', `${Math.round(d.logo_scale * 100)}%`);
    const ec = byKey('error_level');
    ec.disabled = Boolean(d.logo);
    if (d.logo) ec.value = 'H';
    const hint = document.getElementById('contrast-hint');
    if (hint) hint.hidden = d.transparent || contrast(d.foreground, d.background) >= 3;
  }

  function setText(id, v) { const el = document.getElementById(id); if (el) el.textContent = v; }

  function lum(hex) {
    const c = [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16) / 255)
      .map(v => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
  }
  function contrast(a, b) {
    if (!HEX.test(a) || !HEX.test(b)) return 21;
    const [l1, l2] = [lum(a), lum(b)].sort((x, y) => y - x);
    return (l1 + 0.05) / (l2 + 0.05);
  }

  // ---- events
  const changed = () => { sync(); onChange(get()); };

  controls().forEach(el => {
    el.addEventListener(el.type === 'range' || el.type === 'color' || el.type === 'text' ? 'input' : 'change', changed);
  });
  eyeToggle?.addEventListener('change', changed);

  document.querySelectorAll('.hex').forEach(h => {
    h.addEventListener('input', () => {
      let v = h.value.trim();
      if (!v.startsWith('#')) v = '#' + v;
      if (HEX.test(v)) { byKey(h.dataset.for).value = v.toLowerCase(); changed(); }
    });
    h.addEventListener('blur', sync);
  });

  document.querySelectorAll('[data-seg]').forEach(seg => {
    seg.addEventListener('click', e => {
      const btn = e.target.closest('button');
      if (!btn) return;
      byKey(seg.dataset.seg).value = btn.dataset.value;
      changed();
    });
  });

  document.querySelectorAll('[data-preset]').forEach(btn => {
    btn.addEventListener('click', () => {
      const p = PRESETS[btn.dataset.preset];
      set({ ...p, foreground2: p.foreground2 || '#7c3aed', eye_color: p.eye || '' });
      eyeToggle.checked = Boolean(p.eye);
      changed();
    });
  });

  // ---- logo
  const logoInput = document.getElementById('logo-input');
  const logoThumb = document.getElementById('logo-thumb');
  const logoLabel = document.getElementById('logo-label');
  const logoRemove = document.getElementById('logo-remove');

  function showLogo(dataUrl) {
    byKey('logo').value = dataUrl || '';
    logoThumb.hidden = !dataUrl;
    if (dataUrl) logoThumb.src = dataUrl;
    logoLabel.textContent = dataUrl ? 'Replace logo' : 'Add PNG or JPG (max 2 MB)';
    logoRemove.hidden = !dataUrl;
  }

  function readLogo(file) {
    if (!file) return;
    if (!/^image\/(png|jpeg|webp|gif)$/.test(file.type)) { QR.toast('Use a PNG, JPG, WebP or GIF logo.', 'err'); return; }
    if (file.size > 2 * 1024 * 1024) { QR.toast('Logo must be under 2 MB.', 'err'); return; }
    const reader = new FileReader();
    reader.onload = e => { showLogo(e.target.result); changed(); };
    reader.readAsDataURL(file);
  }

  logoInput.addEventListener('change', () => { readLogo(logoInput.files[0]); logoInput.value = ''; });
  logoRemove.addEventListener('click', () => { showLogo(''); changed(); });
  const drop = document.getElementById('logo-drop');
  ['dragover', 'dragenter'].forEach(t => drop.addEventListener(t, e => { e.preventDefault(); drop.classList.add('over'); }));
  ['dragleave', 'drop'].forEach(t => drop.addEventListener(t, () => drop.classList.remove('over')));
  drop.addEventListener('drop', e => { e.preventDefault(); readLogo(e.dataTransfer.files[0]); });

  sync();
  return { get, set, sync };
};
