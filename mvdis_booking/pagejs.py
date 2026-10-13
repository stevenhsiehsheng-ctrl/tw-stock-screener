"""在網頁裡執行、把欄位／按鈕／表格整理成 dict 回傳的 JavaScript。

每個抓到的元素都會被加上 data-mvdis-* 屬性，Python 端用這個屬性定位、點擊。
"""
from __future__ import annotations

SCAN_FIELDS = r"""
() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const SKIP = new Set(['hidden', 'submit', 'button', 'image', 'reset', 'file']);
  const visible = el => {
    if (!el.getClientRects().length) return false;
    const s = getComputedStyle(el);
    return s.visibility !== 'hidden' && s.display !== 'none';
  };
  const all = Array.from(document.querySelectorAll('input, select, textarea'));
  const isCtrl = el => el.tagName !== 'INPUT' || !SKIP.has((el.type || 'text').toLowerCase());
  const ctrlSet = new Set(all.filter(isCtrl));
  // 依文件順序，記下每個欄位「前一個欄位之後、自己之前」出現的文字
  const before = new Map();
  let buf = '';
  if (document.body) {
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT, {
      acceptNode(n) {
        if (n.nodeType === 1 && ['SCRIPT', 'STYLE', 'OPTION', 'NOSCRIPT', 'TEMPLATE', 'SELECT'].includes(n.tagName)) {
          return ctrlSet.has(n) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
        }
        return NodeFilter.FILTER_ACCEPT;
      }
    });
    let n;
    while ((n = walker.nextNode())) {
      if (n.nodeType === 3) {
        if (n.parentElement && n.parentElement.getClientRects().length) {
          buf += ' ' + n.textContent;
          if (buf.length > 600) buf = buf.slice(-300);
        }
      } else if (ctrlSet.has(n)) {
        before.set(n, clean(buf).slice(-60));
        buf = '';
      }
    }
  }
  const out = [];
  let order = 0;
  all.forEach((el, i) => {
    if (!isCtrl(el)) return;
    const type = el.tagName === 'INPUT' ? (el.type || 'text').toLowerCase() : el.tagName.toLowerCase();
    const vis = visible(el);
    if (!vis && type !== 'checkbox' && type !== 'radio') return;
    el.setAttribute('data-mvdis-id', String(i));
    let label = '';
    if (el.id) {
      const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      if (l) label = clean(l.innerText);
    }
    if (!label) { const l = el.closest('label'); if (l) label = clean(l.innerText); }
    if (!label) label = clean(el.getAttribute('aria-label') || el.getAttribute('title') || '');
    let row = '', col = '';
    const cell = el.closest('td, th');
    if (cell) {
      let c = cell.previousElementSibling;
      while (c) {
        if (!c.querySelector('input, select, textarea')) {
          const t = clean(c.innerText);
          if (t) { row = t; break; }
        }
        c = c.previousElementSibling;
      }
      const table = cell.closest('table');
      const idx = cell.cellIndex;
      if (table && table.rows.length > 1 && idx >= 0) {
        const first = table.rows[0];
        // 只有第一列是真正的表頭（全部 <th>、沒有欄位）時，才把它當成這一欄的名稱
        const isHeader = Array.from(first.cells).every(c => c.tagName === 'TH') && !first.querySelector('input, select, textarea');
        if (first !== cell.parentElement && isHeader && first.cells[idx]) {
          col = clean(first.cells[idx].innerText);
        }
      }
    }
    const options = el.tagName === 'SELECT' ? Array.from(el.options).map(o => [o.value, clean(o.text)]) : null;
    out.push({
      mid: String(i), order: order++, tag: el.tagName.toLowerCase(), type,
      name: el.getAttribute('name') || '', id: el.id || '',
      label: label.slice(0, 80), row_label: row.slice(0, 80), col_header: col.slice(0, 60),
      before_text: before.get(el) || '', placeholder: el.getAttribute('placeholder') || '',
      required: !!el.required || el.getAttribute('aria-required') === 'true',
      readonly: !!el.readOnly, disabled: !!el.disabled,
      maxlength: el.maxLength > 0 ? el.maxLength : null,
      value_len: (el.value || '').length, checked: !!el.checked, visible: vis,
      options, selected_value: el.tagName === 'SELECT' ? el.value : null,
      selected_text: el.tagName === 'SELECT' && el.selectedIndex >= 0 ? clean(el.options[el.selectedIndex].text) : null,
      form_index: el.form ? Array.from(document.forms).indexOf(el.form) : -1,
    });
  });
  return out;
}
"""

SCAN_CLICKABLES = r"""
() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const visible = el => {
    if (!el.getClientRects().length) return false;
    const s = getComputedStyle(el);
    return s.visibility !== 'hidden' && s.display !== 'none' && parseFloat(s.opacity || '1') > 0.05;
  };
  const sel = 'a, button, input[type=button], input[type=submit], input[type=image], [role=button]';
  const out = [];
  Array.from(document.querySelectorAll(sel)).forEach((el, i) => {
    if (!visible(el)) return;
    const text = clean(el.innerText || el.value || el.getAttribute('alt') || el.getAttribute('title') || el.getAttribute('aria-label') || '');
    if (!text) return;
    el.setAttribute('data-mvdis-click', String(i));
    out.push({
      cid: String(i), tag: el.tagName.toLowerCase(), text: text.slice(0, 80),
      href: (el.getAttribute('href') || '').slice(0, 200), onclick: (el.getAttribute('onclick') || '').slice(0, 200),
      disabled: !!el.disabled || el.getAttribute('aria-disabled') === 'true',
      in_dialog: !!el.closest('[role=dialog], .modal, .ui-dialog, .popup, .dialog, .swal2-popup, .lightbox'),
    });
  });
  return out;
}
"""

# 把每個表格轉成「格子」：處理 rowspan/colspan，讓同一欄永遠在同一個位置。
SCAN_TABLES = r"""
() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const actSel = 'a, button, input[type=button], input[type=submit], input[type=image], [role=button]';
  const out = [];
  Array.from(document.querySelectorAll('table')).forEach((tb, ti) => {
    if (!tb.getClientRects().length) return;
    const pending = [];
    const start = new Map();
    const rows = [];
    Array.from(tb.rows).forEach((tr, ri) => {
      const cells = [];
      let col = 0;
      const fill = () => {
        while (pending[col] && pending[col].left > 0) {
          cells[col] = pending[col].text; pending[col].left--; col++;
        }
      };
      Array.from(tr.cells).forEach(td => {
        fill();
        const text = clean(td.innerText);
        const cs = Math.max(1, td.colSpan || 1), rs = Math.max(1, td.rowSpan || 1);
        start.set(td, col);
        for (let k = 0; k < cs; k++) {
          cells[col + k] = text;
          if (rs > 1) pending[col + k] = { left: rs - 1, text };
        }
        col += cs;
      });
      for (; col < pending.length; col++) {
        if (pending[col] && pending[col].left > 0) { cells[col] = pending[col].text; pending[col].left--; }
      }
      const actions = [];
      Array.from(tr.querySelectorAll(actSel)).forEach((el, k) => {
        if (el.closest('tr') !== tr || !el.getClientRects().length) return;
        const text = clean(el.innerText || el.value || el.getAttribute('alt') || el.getAttribute('title') || '');
        const aid = ti + '-' + ri + '-' + k;
        el.setAttribute('data-mvdis-act', aid);
        const td = el.closest('td, th');
        actions.push({ aid, text: text.slice(0, 60), col: td && start.has(td) ? start.get(td) : -1,
                       disabled: !!el.disabled });
      });
      rows.push({ cells: Array.from(cells, c => c || ''), actions });
    });
    out.push({ tid: ti, rows });
  });
  return out;
}
"""

SCAN_IMAGES = r"""
() => Array.from(document.images).filter(i => i.getClientRects().length).map(i => {
  const box = i.closest('td, div, span, p, li') || i.parentElement;
  const near = box ? /換一張|重新產生|更換|refresh|reload/i.test(box.innerText || '') : false;
  return { src: (i.getAttribute('src') || '').slice(0, 200), alt: i.getAttribute('alt') || '', near_refresh: near };
})
"""

FORMS = r"""
() => Array.from(document.forms).map((f, i) => ({
  index: i, name: f.getAttribute('name') || '', id: f.id || '',
  action: f.getAttribute('action') || '', method: (f.getAttribute('method') || 'get').toLowerCase(),
  hidden: Array.from(f.querySelectorAll('input[type=hidden]')).map(h => h.name).filter(Boolean),
}))
"""

SET_VALUE = r"""
(el, v) => {
  el.value = v;
  el.dispatchEvent(new Event('input', { bubbles: true }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
  el.dispatchEvent(new Event('blur', { bubbles: true }));
}
"""

FIRE_CHANGE = r"""
el => {
  el.dispatchEvent(new Event('change', { bubbles: true }));
  el.dispatchEvent(new Event('blur', { bubbles: true }));
}
"""

OPTIONS = r"el => Array.from(el.options).map(o => [o.value, (o.text || '').replace(/\s+/g, ' ').trim()])"

# 等你打驗證碼：按 Enter 時記下來（不讓瀏覽器自己送出，由程式按送出鈕）
WATCH_ENTER = r"""
el => {
  window.__mvdisEnter = false;
  if (!el.__mvdisHooked) {
    el.addEventListener('keydown', e => {
      if (e.key === 'Enter') { e.preventDefault(); window.__mvdisEnter = true; }
    }, true);
    el.__mvdisHooked = true;
  }
}
"""

CAPTCHA_STATE = r"""
sel => {
  const el = document.querySelector(sel);
  if (!el) return null;
  const v = (el.value || '').trim();
  return { len: v.length, val: v, enter: !!window.__mvdisEnter };
}
"""

# 把符合規則的文字藏起來（舊的「驗證碼錯誤」訊息），參數是 Python 的正規表示式字串
HIDE_TEXT = r"""
src => {
  let rx;
  try { rx = new RegExp(src, 'i'); } catch (e) { return 0; }
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let n, hidden = 0;
  const hit = [];
  while ((n = walker.nextNode())) { if (rx.test(n.textContent)) hit.push(n); }
  hit.forEach(t => { t.textContent = ''; hidden++; });
  return hidden;
}
"""
