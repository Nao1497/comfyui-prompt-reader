/* ComfyUI Prompt Reader front end (design.md §9). No build step; plain ES2020. */
(() => {
  'use strict';

  const PAGE_SIZE = 100;

  const state = {
    filter: { kind: 'all', dir: null, recursive: true, lora: null, tags: [], tagMatch: 'and' }, // kind: all | favorite | missing
    cursor: null,
    done: false,
    loading: false,
    totalCount: null,
    items: [],
    selectedId: null,
    generation: 0, // bumped on every reset so stale responses are dropped
  };

  const $ = (sel) => document.querySelector(sel);
  const grid = $('#grid');
  const sentinel = $('#sentinel');
  const statusEl = $('#status');
  const totalEl = $('#total');

  async function apiGet(path, params) {
    const url = new URL(path, window.location.origin);
    if (params) {
      for (const [k, v] of Object.entries(params)) {
        if (v === null || v === undefined) continue;
        if (Array.isArray(v)) v.forEach((x) => url.searchParams.append(k, String(x)));
        else url.searchParams.set(k, String(v));
      }
    }
    const res = await fetch(url);
    const body = await res.json();
    if (!res.ok) throw new Error(body.error ? `${body.error.code}: ${body.error.message}` : res.statusText);
    return body;
  }

  function listParams() {
    const f = state.filter;
    const p = { limit: PAGE_SIZE, cursor: state.cursor };
    if (f.kind === 'favorite') p.favorite_only = true;
    if (f.kind === 'missing') p.missing_only = true;
    if (f.dir !== null) { p.dir = f.dir; p.recursive = f.recursive; }
    if (f.lora !== null) p.lora = f.lora;
    if (f.tags.length) { p.tag = f.tags; p.tag_match = f.tagMatch; }
    return p;
  }

  /** Change the filter: discard cursor and loaded items, then reload from the top. */
  function setFilter(patch) {
    Object.assign(state.filter, patch);
    resetAndLoad();
  }

  function resetAndLoad() {
    state.generation += 1;
    state.cursor = null;
    state.done = false;
    state.loading = false;
    state.totalCount = null;
    state.items = [];
    grid.replaceChildren();
    totalEl.textContent = '';
    loadMore();
  }

  async function loadMore() {
    if (state.loading || state.done) return;
    state.loading = true;
    const gen = state.generation;
    statusEl.textContent = '読み込み中…';
    try {
      const body = await apiGet('/images', listParams());
      if (gen !== state.generation) return; // filter changed meanwhile
      if (body.totalCount !== null) {
        state.totalCount = body.totalCount;
        totalEl.textContent = `${body.totalCount} 件`;
      }
      state.items.push(...body.items);
      appendItems(body.items);
      state.cursor = body.nextCursor;
      state.done = body.nextCursor === null;
      statusEl.textContent = `${state.items.length} 件表示` + (state.done ? '（末尾）' : '');
      // If the first page did not fill the viewport, keep loading.
      if (!state.done && sentinelVisible()) loadMore();
    } catch (err) {
      statusEl.textContent = `エラー: ${err.message}`;
    } finally {
      if (gen === state.generation) state.loading = false;
    }
  }

  function sentinelVisible() {
    const wrap = $('#grid-wrap');
    const r = sentinel.getBoundingClientRect();
    const w = wrap.getBoundingClientRect();
    return r.top <= w.bottom + 200;
  }

  function appendItems(items) {
    const frag = document.createDocumentFragment();
    for (const item of items) frag.appendChild(renderCell(item));
    grid.appendChild(frag);
  }

  function renderCell(item) {
    const cell = document.createElement('div');
    cell.className = 'cell';
    cell.dataset.id = String(item.id);
    cell.title = item.filePath;
    if (item.thumbnailStatus === 'ok') {
      const img = document.createElement('img');
      img.loading = 'lazy';
      img.decoding = 'async';
      img.alt = item.fileName;
      img.src = item.thumbnailUrl; // thumbnails only; the original is never fetched here (NFR-4)
      cell.appendChild(img);
    } else {
      const span = document.createElement('div');
      span.className = 'noimg';
      span.textContent = item.fileName;
      cell.appendChild(span);
    }
    if (item.presence === 'missing') {
      const badge = document.createElement('span');
      badge.className = 'badge';
      badge.textContent = 'missing';
      cell.appendChild(badge);
    }
    // Favorite toggle at the top-right of every cell.
    const fav = document.createElement('button');
    fav.type = 'button';
    fav.className = 'fav' + (item.isFavorite ? ' on' : '');
    fav.textContent = item.isFavorite ? '★' : '☆';
    fav.title = 'お気に入り';
    fav.setAttribute('aria-pressed', String(item.isFavorite));
    fav.addEventListener('click', (ev) => {
      ev.stopPropagation();
      const current = state.items.find((i) => i.id === item.id);
      setFavorite(item.id, !(current ? current.isFavorite : item.isFavorite));
    });
    cell.appendChild(fav);
    cell.addEventListener('click', () => selectImage(item.id));
    return cell;
  }

  function selectImage(id) {
    state.selectedId = id;
    for (const el of grid.querySelectorAll('.cell.selected')) el.classList.remove('selected');
    const cell = grid.querySelector(`.cell[data-id="${id}"]`);
    if (cell) cell.classList.add('selected');
    document.dispatchEvent(new CustomEvent('image-selected', { detail: { id } }));
  }

  // Infinite scroll: fetch the next page when the sentinel enters the viewport.
  const observer = new IntersectionObserver((entries) => {
    if (entries.some((e) => e.isIntersecting)) loadMore();
  }, { root: $('#grid-wrap'), rootMargin: '400px 0px' });
  observer.observe(sentinel);

  // --- left pane: collapsible sections (remembered per viewer) ----------------
  const SECTIONS_KEY = 'leftSections';
  const LEFT_WIDTH_KEY = 'leftWidth';
  const LEFT_WIDTH_DEFAULT = 280;
  const LEFT_WIDTH_MIN = 180;
  const LEFT_WIDTH_MAX = 560;

  function readSectionState() {
    try { return JSON.parse(localStorage.getItem(SECTIONS_KEY) || '{}'); } catch (_) { return {}; }
  }

  function initSections() {
    const state = readSectionState();
    for (const sec of document.querySelectorAll('details.sec')) {
      const key = sec.dataset.sec;
      if (key in state) sec.open = state[key];
      sec.addEventListener('toggle', () => {
        const next = readSectionState();
        next[key] = sec.open;
        try { localStorage.setItem(SECTIONS_KEY, JSON.stringify(next)); } catch (_) { /* ignore */ }
      });
    }
  }

  /** Count shown on a section header, so a collapsed section still says how much is inside. */
  function setSectionCount(id, n) {
    const node = document.getElementById(id);
    if (node) node.textContent = n === null || n === undefined ? '' : String(n);
  }

  // --- left pane: drag to resize ----------------------------------------------
  function applyLeftWidth(px, persist = true) {
    const w = Math.min(LEFT_WIDTH_MAX, Math.max(LEFT_WIDTH_MIN, Math.round(px)));
    document.documentElement.style.setProperty('--left-w', `${w}px`);
    if (persist) {
      try { localStorage.setItem(LEFT_WIDTH_KEY, String(w)); } catch (_) { /* ignore */ }
    }
    return w;
  }

  function initLeftResizer() {
    let saved = null;
    try { saved = Number(localStorage.getItem(LEFT_WIDTH_KEY)) || null; } catch (_) { /* ignore */ }
    applyLeftWidth(saved || LEFT_WIDTH_DEFAULT, false);

    const handle = $('#left-resizer');
    if (!handle) return;
    let dragging = false;

    handle.addEventListener('pointerdown', (ev) => {
      dragging = true;
      handle.setPointerCapture(ev.pointerId);
      handle.classList.add('dragging');
      document.body.classList.add('resizing');
      ev.preventDefault();
    });
    handle.addEventListener('pointermove', (ev) => {
      if (dragging) applyLeftWidth(ev.clientX);
    });
    const stop = (ev) => {
      if (!dragging) return;
      dragging = false;
      try { handle.releasePointerCapture(ev.pointerId); } catch (_) { /* ignore */ }
      handle.classList.remove('dragging');
      document.body.classList.remove('resizing');
    };
    handle.addEventListener('pointerup', stop);
    handle.addEventListener('pointercancel', stop);
    handle.addEventListener('dblclick', () => applyLeftWidth(LEFT_WIDTH_DEFAULT));
  }

  // --- left pane: scan button, fixed items, folder tree ---------------------
  const scanBtn = $('#scan-btn');
  const scanResult = $('#scan-result');
  const fixedItems = $('#fixed-items');
  const folderTree = $('#folder-tree');
  const recursiveBox = $('#recursive');
  const filterLabel = $('#filter-label');

  async function refreshFolders() {
    try {
      const body = await apiGet('/folders');
      fixedItems.querySelector('[data-count="all"]').textContent = body.rootTotalCount;
      fixedItems.querySelector('[data-count="favorite"]').textContent = body.favoriteCount;
      fixedItems.querySelector('[data-count="missing"]').textContent = body.missingCount;
      folderTree.replaceChildren(...body.folders.map(renderFolder));
      setSectionCount('count-folders', body.folders.length);
      highlightSelection();
    } catch (err) {
      statusEl.textContent = `フォルダ取得エラー: ${err.message}`;
    }
  }

  function renderFolder(node) {
    const li = document.createElement('li');
    const row = document.createElement('div');
    row.className = 'item';
    row.dataset.dir = node.path;
    row.title = node.path;
    const label = document.createElement('span');
    label.className = 'label';
    label.textContent = node.name;
    const count = document.createElement('span');
    count.className = 'count';
    count.textContent = node.totalCount; // 確認事項 #10(c): show totalCount
    row.append(label, count);
    row.addEventListener('click', () => selectFolder(node.path));
    li.appendChild(row);
    if (node.children.length) {
      const ul = document.createElement('ul');
      ul.append(...node.children.map(renderFolder));
      li.appendChild(ul);
    }
    return li;
  }

  function selectFixed(kind) {
    filterLabel.textContent = { all: 'すべて', favorite: 'お気に入り', missing: '見つからない' }[kind];
    setFilter({ kind, dir: null, lora: null });
    highlightSelection();
  }

  function selectFolder(dir) {
    filterLabel.textContent = dir;
    setFilter({ kind: 'all', dir, recursive: recursiveBox.checked, lora: null });
    highlightSelection();
  }

  function highlightSelection() {
    const f = state.filter;
    for (const el of document.querySelectorAll('#left .item')) {
      let on = false;
      if (el.dataset.tag !== undefined) on = f.tags.includes(Number(el.dataset.tag));
      else if (el.dataset.lora !== undefined) on = f.lora !== null && Number(el.dataset.lora) === f.lora;
      else if (el.dataset.dir !== undefined) on = f.lora === null && f.dir === el.dataset.dir;
      else if (el.dataset.kind !== undefined) on = f.lora === null && f.dir === null && el.dataset.kind === f.kind;
      el.classList.toggle('selected', on);
    }
  }

  // --- LoRA list (FR-41) -------------------------------------------------------
  const loraList = $('#lora-list');
  const loraScanBtn = $('#lora-scan-btn');
  const loraScanResult = $('#lora-scan-result');
  let loras = [];

  async function refreshLoras() {
    try {
      const body = await apiGet('/loras');
      loras = body.items;
      loraList.replaceChildren(...loras.map(renderLoraItem));
      if (!loras.length) loraList.appendChild(el('li', { class: 'muted' }, '登録なし'));
      setSectionCount('count-lora', loras.length);
      highlightSelection();
    } catch (err) {
      statusEl.textContent = `LoRA 取得エラー: ${err.message}`;
    }
  }

  function renderLoraItem(lora) {
    const li = document.createElement('li');
    const row = el('div', { class: 'item', title: lora.name });
    row.dataset.lora = String(lora.id);
    row.appendChild(el('span', { class: 'label' }, lora.name));
    if (lora.presence !== 'active') row.appendChild(el('span', { class: `presence ${lora.presence}` }, lora.presence));
    row.appendChild(el('span', { class: 'count' }, String(lora.imageCount)));
    row.addEventListener('click', () => selectLora(lora.id));
    li.appendChild(row);
    return li;
  }

  /** Filter the grid to images using this LoRA and open its editor in the right pane. */
  function selectLora(id) {
    const lora = loras.find((l) => l.id === id);
    filterLabel.textContent = `LoRA: ${lora ? lora.name : id}`;
    setFilter({ kind: 'all', dir: null, lora: id });
    highlightSelection();
    openLoraEditor(id);
  }

  loraScanBtn.addEventListener('click', async () => {
    loraScanBtn.disabled = true;
    loraScanResult.textContent = 'スキャン中…';
    try {
      const res = await fetch('/loras/scan', { method: 'POST' });
      const body = await res.json();
      if (!res.ok) throw new Error(`${body.error.code}: ${body.error.message}`);
      loraScanResult.textContent =
        (body.loraRootConfigured
          ? `ファイル ${body.scannedFileCount} / 新規 ${body.fileCreatedCount} / missing ${body.fileMissingCount}\n`
          : 'lora_root 未設定（ワークフローからのみ登録）\n') +
        `画像との関連付け ${body.backfilledImageCount} 件`;
      await refreshLoras();
      if (state.filter.lora !== null) resetAndLoad();
    } catch (err) {
      loraScanResult.textContent = `エラー: ${err.message}`;
    } finally {
      loraScanBtn.disabled = false;
    }
  });

  fixedItems.addEventListener('click', (ev) => {
    const li = ev.target.closest('.item');
    if (li) selectFixed(li.dataset.kind);
  });

  recursiveBox.addEventListener('change', () => {
    if (state.filter.dir !== null) setFilter({ recursive: recursiveBox.checked });
  });

  scanBtn.addEventListener('click', async () => {
    scanBtn.disabled = true;
    scanResult.textContent = 'スキャン中…';
    try {
      const res = await fetch('/scan', { method: 'POST' });
      const body = await res.json();
      if (!res.ok) throw new Error(`${body.error.code}: ${body.error.message}`);
      scanResult.textContent =
        `走査 ${body.scannedCount} / 新規 ${body.createdCount} / 更新 ${body.updatedCount} / ` +
        `missing ${body.missingCount} / 抽出失敗 ${body.extractFailedCount}\n` +
        `サムネイル生成 ${body.thumbnailGeneratedCount} / 失敗 ${body.thumbnailFailedCount}`;
      await refreshFolders();
      await refreshUsedTags();
      resetAndLoad();
    } catch (err) {
      scanResult.textContent = `エラー: ${err.message}`;
    } finally {
      scanBtn.disabled = false;
    }
  });

  // --- right pane: detail view (design §9) ----------------------------------
  const detailEl = $('#detail');
  let currentDetail = null;

  document.addEventListener('image-selected', (ev) => loadDetail(ev.detail.id));

  async function loadDetail(id) {
    detailEl.classList.remove('placeholder');
    detailEl.textContent = '読み込み中…';
    try {
      const body = await apiGet(`/images/${id}`);
      if (state.selectedId !== id) return;
      currentDetail = body;
      renderDetail(body);
    } catch (err) {
      detailEl.textContent = `エラー: ${err.message}`;
    }
  }

  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (k === 'class') node.className = v;
      else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v);
    }
    for (const c of children) node.append(c);
    return node;
  }

  function fmtBytes(n) {
    if (n === null || n === undefined) return '';
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    return `${(n / 1024 / 1024).toFixed(2)} MB`;
  }

  function fmtValue(v) {
    return v === null || v === undefined ? el('span', { class: 'muted' }, '未取得') : String(v);
  }

  function kvTable(rows) {
    const table = el('table', { class: 'kv' });
    for (const [k, v] of rows) {
      table.appendChild(el('tr', {}, el('th', {}, k), el('td', {}, v instanceof Node ? v : String(v))));
    }
    return table;
  }

  function renderDetail(d) {
    const g = d.generation;
    const frag = document.createDocumentFragment();

    // Original image: fetched only here, never in the grid (design §1).
    const preview = el('div', { id: 'preview' });
    if (d.presence === 'missing') {
      preview.appendChild(el('div', { class: 'noimg muted' }, 'ファイルが見つかりません（missing）'));
    } else {
      const img = el('img', { src: d.fileUrl, alt: d.fileName });
      img.addEventListener('error', () => {
        preview.replaceChildren(el('div', { class: 'noimg muted' }, '原寸画像を取得できません'));
      });
      preview.appendChild(img);
    }
    frag.appendChild(preview);

    const favBtn = el('button', { id: 'fav-btn', type: 'button', class: d.isFavorite ? 'on' : '', onclick: () => toggleFavorite(d) },
      d.isFavorite ? '★ お気に入り' : '☆ お気に入り');
    frag.appendChild(el('div', { class: 'detail-head' }, el('span', { class: 'name', title: d.fileName }, d.fileName), favBtn));

    frag.appendChild(el('h2', {}, 'ファイル情報'));
    frag.appendChild(kvTable([
      ['ファイル名', d.fileName],
      ['パス', d.filePath],
      ['サイズ', fmtBytes(d.fileSize)],
      ['解像度', d.imageWidth !== null ? `${d.imageWidth} × ${d.imageHeight}` : fmtValue(null)],
      ['更新日時', d.fileMtime],
      ['状態', d.presence],
    ]));

    if (d.extractionStatus !== 'full') {
      const notice = el('div', { class: 'notice' });
      if (d.extractionStatus === 'none') {
        notice.append('ComfyUI メタデータがありません。生成パラメータとプロンプトは取得できませんでした。');
      } else {
        notice.append('一部の項目を抽出できませんでした（partial）。取得できなかった項目は「未取得」と表示されます。 ');
        notice.appendChild(el('a', { href: `/images/${d.id}/raw-metadata`, target: '_blank' }, '生メタデータを開く'));
      }
      frag.appendChild(notice);
    }

    frag.appendChild(el('h2', {}, '生成パラメータ'));
    frag.appendChild(kvTable([
      ['モデル', fmtValue(g.modelName)],
      ['seed', fmtValue(g.seed)],
      ['steps', fmtValue(g.steps)],
      ['cfg', fmtValue(g.cfg)],
      ['sampler', fmtValue(g.samplerName)],
      ['scheduler', fmtValue(g.scheduler)],
      ['生成解像度', g.genWidth !== null && g.genHeight !== null ? `${g.genWidth} × ${g.genHeight}` : fmtValue(null)],
      ['抽出状態', d.extractionStatus],
    ]));

    frag.appendChild(promptBlock('Positive', g.positivePrompt, 'positive'));
    if (d.promptTokens && d.promptTokens.length) {
      frag.appendChild(el('h2', {}, 'タグ'));
      frag.appendChild(renderPromptTokens(d.promptTokens));
    }
    frag.appendChild(promptBlock('Negative', g.negativePrompt, 'negative'));

    if (d.loras && d.loras.length) {
      frag.appendChild(el('h2', {}, 'LoRA'));
      const ul = el('ul', { class: 'lora-usage' });
      for (const u of d.loras) {
        const name = el('span', { class: 'lora-name', title: 'この LoRA の画像を表示', onclick: () => selectLora(u.id) }, u.name);
        const strength = el('span', { class: 'strength' },
          `model ${u.strengthModel === null ? '?' : u.strengthModel} / clip ${u.strengthClip === null ? '?' : u.strengthClip}`);
        const li = el('li', {}, name, strength);
        if (u.presence !== 'active') li.appendChild(el('span', { class: `presence ${u.presence}` }, u.presence));
        if (u.triggerWords) {
          const copyBtn = el('button', { type: 'button', class: 'copy-btn', style: 'float:right' }, 'コピー');
          copyBtn.addEventListener('click', () => copyText(u.triggerWords));
          li.appendChild(el('div', { class: 'trigger' }, copyBtn, u.triggerWords));
        }
        ul.appendChild(li);
      }
      frag.appendChild(ul);
    }

    if (d.extractionStatus === 'full') {
      frag.appendChild(el('div', {}, el('a', { href: `/images/${d.id}/raw-metadata`, target: '_blank' }, '生メタデータを開く')));
    }
    detailEl.replaceChildren(frag);
  }

  // --- LoRA editor (right pane) ----------------------------------------------
  async function openLoraEditor(id) {
    state.selectedId = null;
    for (const c of grid.querySelectorAll('.cell.selected')) c.classList.remove('selected');
    detailEl.classList.remove('placeholder');
    detailEl.textContent = '読み込み中…';
    try {
      const lora = await apiGet(`/loras/${id}`);
      renderLoraEditor(lora);
    } catch (err) {
      detailEl.textContent = `エラー: ${err.message}`;
    }
  }

  function renderLoraEditor(lora) {
    const frag = document.createDocumentFragment();
    frag.appendChild(el('div', { class: 'detail-head' }, el('span', { class: 'name', title: lora.name }, lora.name)));
    frag.appendChild(el('h2', {}, 'LoRA ファイル'));
    frag.appendChild(kvTable([
      ['名前', lora.name],
      ['ファイル名', lora.fileName],
      ['サイズ', lora.fileSize === null ? fmtValue(null) : fmtBytes(lora.fileSize)],
      ['更新日時', lora.fileMtime === null ? fmtValue(null) : lora.fileMtime],
      ['状態', lora.presence],
      ['使用画像', `${lora.imageCount} 件`],
    ]));
    if (lora.presence === 'unknown') {
      frag.appendChild(el('div', { class: 'notice' }, 'ワークフローから検出された LoRA です。lora_root を設定して「LoRA スキャン」を実行するとファイル情報が入ります。'));
    }

    const editor = el('div', { class: 'lora-editor' });
    const trigger = el('textarea', { id: 'lora-trigger', placeholder: 'Trigger Words' });
    trigger.value = lora.triggerWords;
    const memo = el('textarea', { id: 'lora-memo', placeholder: 'メモ' });
    memo.value = lora.memo;
    const saved = el('span', { class: 'saved' });
    const saveBtn = el('button', { type: 'button', id: 'lora-save' }, '保存');
    const copyBtn = el('button', { type: 'button', id: 'lora-copy-trigger' }, 'Trigger Words をコピー');
    saveBtn.addEventListener('click', async () => {
      saveBtn.disabled = true;
      try {
        const res = await fetch(`/loras/${lora.id}`, {
          method: 'PUT', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ trigger_words: trigger.value, memo: memo.value }),
        });
        const body = await res.json();
        if (!res.ok) throw new Error(`${body.error.code}: ${body.error.message}`);
        saved.textContent = '保存しました';
        setTimeout(() => { saved.textContent = ''; }, 1500);
        refreshUsedTags(); // trigger words may have become dictionary tags (FR-49)
      } catch (err) {
        saved.textContent = `エラー: ${err.message}`;
      } finally {
        saveBtn.disabled = false;
      }
    });
    copyBtn.addEventListener('click', async () => {
      const ok = await copyText(trigger.value);
      saved.textContent = ok ? 'コピーしました' : 'コピーできませんでした';
      setTimeout(() => { saved.textContent = ''; }, 1500);
    });
    editor.append(
      el('label', { for: 'lora-trigger' }, 'Trigger Words'), trigger,
      el('label', { for: 'lora-memo' }, 'メモ'), memo,
      el('div', { class: 'actions' }, saveBtn, copyBtn, saved),
    );
    frag.appendChild(editor);
    detailEl.replaceChildren(frag);
  }

  /** Copy button hands the API string itself to the clipboard; the <pre> is display only (FR-9). */
  function promptBlock(title, text, key) {
    const status = el('span', { class: 'copied' });
    const btn = el('button', { type: 'button', class: 'copy-btn', 'data-copy': key, disabled: text === null ? '' : null }, 'コピー');
    if (text !== null) btn.removeAttribute('disabled');
    btn.addEventListener('click', async () => {
      if (text === null) return;
      const ok = await copyText(text);
      status.textContent = ok ? 'コピーしました' : 'コピーできませんでした';
      setTimeout(() => { status.textContent = ''; }, 1500);
    });
    const pre = el('pre', { class: 'prompt' + (text === null ? ' empty' : '') }, text === null ? '未取得' : text);
    return el('div', { class: 'prompt-block' },
      el('div', { class: 'prompt-head' }, el('span', { class: 'title' }, title), el('span', {}, btn, status)),
      pre);
  }

  async function copyText(text) {
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
        return true;
      }
    } catch (_) { /* fall through */ }
    try {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand('copy');
      ta.remove();
      return ok;
    } catch (_) {
      return false;
    }
  }

  function toggleFavorite(d) {
    return setFavorite(d.id, !d.isFavorite);
  }

  /** PUT the new favorite state, then sync the grid cell, the detail pane and the counts. */
  async function setFavorite(id, next) {
    try {
      const res = await fetch(`/images/${id}/favorite`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ is_favorite: next }),
      });
      const body = await res.json();
      if (!res.ok) throw new Error(`${body.error.code}: ${body.error.message}`);
      const item = state.items.find((i) => i.id === id);
      if (item) item.isFavorite = body.isFavorite;
      updateCellFavorite(id, body.isFavorite);
      if (currentDetail && currentDetail.id === id) {
        currentDetail.isFavorite = body.isFavorite;
        if (state.selectedId === id) renderDetail(currentDetail);
      }
      refreshFolders();
    } catch (err) {
      statusEl.textContent = `お気に入り更新エラー: ${err.message}`;
    }
  }

  function updateCellFavorite(id, on) {
    const cell = grid.querySelector(`.cell[data-id="${id}"]`);
    if (!cell) return;
    if (state.filter.kind === 'favorite' && !on) {
      cell.remove();
      state.items = state.items.filter((i) => i.id !== id);
      if (state.totalCount !== null) { state.totalCount -= 1; totalEl.textContent = `${state.totalCount} 件`; }
      return;
    }
    const fav = cell.querySelector('.fav');
    if (!fav) return;
    fav.classList.toggle('on', on);
    fav.textContent = on ? '★' : '☆';
    fav.setAttribute('aria-pressed', String(on));
  }

  // --- tag dictionary (FR-45 .. FR-52) ----------------------------------------
  const TAG_COLORS = {
    general: '#6aa9ff', artist: '#ff7b7b', copyright: '#c58bff', character: '#7fd48a', meta: '#ffb45c', lora: '#ffcc33',
  };
  const tagCache = new Map(); // id -> tag json (for chips)
  const tagUsedEl = $('#tag-used');
  const tagSearchEl = $('#tag-search');
  const tagResultsEl = $('#tag-search-results');
  const tagBar = $('#tag-bar');
  const tagChips = $('#tag-chips');
  const tagMatchSel = $('#tag-match');
  const tagImportBtn = $('#tag-import-btn');
  const tagCsvInput = $('#tag-csv');
  const tagImportResult = $('#tag-import-result');

  function tagColor(tag) {
    return TAG_COLORS[tag && tag.categoryName] || '#888';
  }

  function fmtCount(n) {
    return n >= 1000000 ? `${(n / 1000000).toFixed(1)}M` : n >= 1000 ? `${(n / 1000).toFixed(0)}k` : String(n);
  }

  function renderTagRow(tag, countText) {
    const li = document.createElement('li');
    const row = el('div', { class: 'item', title: `${tag.name}${tag.otherNames ? '\n' + tag.otherNames : ''}` });
    row.dataset.tag = String(tag.id);
    row.style.setProperty('--tagc', tagColor(tag));
    row.append(
      el('span', { class: 'tag-dot' }),
      el('span', { class: 'label' }, tag.name),
      el('span', { class: 'post' }, tag.categoryName),
      el('span', { class: 'count' }, countText),
    );
    row.addEventListener('click', () => toggleTag(tag));
    li.appendChild(row);
    return li;
  }

  async function refreshUsedTags() {
    try {
      const body = await apiGet('/tags/used', { limit: 300 });
      for (const t of body.items) tagCache.set(t.id, t);
      tagUsedEl.replaceChildren(...body.items.map((t) => renderTagRow(t, String(t.imageCount))));
      if (!body.items.length) tagUsedEl.appendChild(el('li', { class: 'muted' }, '辞書に一致する語を持つ画像がありません'));
      setSectionCount('count-tags', body.items.length);
      highlightSelection();
    } catch (err) {
      statusEl.textContent = `タグ取得エラー: ${err.message}`;
    }
  }

  /** Add or remove a tag from the filter (FR-50); other filters stay as they are. */
  function toggleTag(tag) {
    tagCache.set(tag.id, tag);
    const tags = state.filter.tags.slice();
    const i = tags.indexOf(tag.id);
    if (i >= 0) tags.splice(i, 1); else tags.push(tag.id);
    setFilter({ tags });
    renderTagBar();
    highlightSelection();
  }

  function clearTags() {
    setFilter({ tags: [] });
    renderTagBar();
    highlightSelection();
  }

  function renderTagBar() {
    const tags = state.filter.tags;
    tagBar.hidden = tags.length === 0;
    tagChips.replaceChildren(...tags.map((id) => {
      const tag = tagCache.get(id) || { id, name: `#${id}`, categoryName: '' };
      const chip = el('span', { class: 'chip' }, tag.name);
      chip.style.setProperty('--tagc', tagColor(tag));
      const x = el('span', { class: 'x', title: '外す' }, '✕');
      x.addEventListener('click', () => toggleTag(tag));
      chip.appendChild(x);
      return chip;
    }));
    tagMatchSel.value = state.filter.tagMatch;
  }

  tagMatchSel.addEventListener('change', () => {
    setFilter({ tagMatch: tagMatchSel.value });
  });
  $('#tag-clear').addEventListener('click', clearTags);

  let searchTimer = null;
  tagSearchEl.addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(searchTags, 250);
  });

  async function searchTags() {
    const q = tagSearchEl.value.trim();
    if (!q) { tagResultsEl.replaceChildren(); return; }
    try {
      const body = await apiGet('/tags/search', { q, limit: 30 });
      for (const t of body.items) tagCache.set(t.id, t);
      tagResultsEl.replaceChildren(...body.items.map((t) => renderTagRow(t, fmtCount(t.postCount))));
      if (!body.items.length) tagResultsEl.appendChild(el('li', { class: 'muted' }, '該当なし'));
      highlightSelection();
    } catch (err) {
      tagResultsEl.replaceChildren(el('li', { class: 'muted' }, `エラー: ${err.message}`));
    }
  }

  tagImportBtn.addEventListener('click', () => tagCsvInput.click());
  tagCsvInput.addEventListener('change', async () => {
    const file = tagCsvInput.files[0];
    if (!file) return;
    tagImportBtn.disabled = true;
    tagImportResult.textContent = `取り込み中… (${file.name})`;
    try {
      const form = new FormData();
      form.append('file', file);
      const res = await fetch('/tags/import', { method: 'POST', body: form });
      const body = await res.json();
      if (!res.ok) throw new Error(`${body.error.code}: ${body.error.message}`);
      tagImportResult.textContent =
        `読み取り ${body.readCount} / 取り込み ${body.importedCount} / 飛ばした行 ${body.skippedCount}\n` +
        `別名 ${body.aliasCount} / 関連付いた画像 ${body.linkedImageCount}`;
      await refreshUsedTags();
      if (state.filter.tags.length) resetAndLoad();
      if (state.selectedId !== null) loadDetail(state.selectedId);
    } catch (err) {
      tagImportResult.textContent = `エラー: ${err.message}`;
    } finally {
      tagImportBtn.disabled = false;
      tagCsvInput.value = '';
    }
  });

  /** Chips for the positive prompt words in the detail pane (FR-52). */
  function renderPromptTokens(tokens) {
    const wrap = el('div', { class: 'tokens' });
    for (const { token, tag } of tokens) {
      if (!tag) {
        wrap.appendChild(el('span', { class: 'chip unknown', title: '辞書に無い語' }, token));
        continue;
      }
      tagCache.set(tag.id, tag);
      const chip = el('span', { class: 'chip clickable' + (state.filter.tags.includes(tag.id) ? ' on' : ''), title: `${tag.name} [${tag.categoryName}]` });
      chip.style.setProperty('--tagc', tagColor(tag));
      chip.append(el('span', {}, token));
      const sub = [tag.categoryName];
      if (tag.postCount) sub.push(fmtCount(tag.postCount));
      if (tag.otherNames) sub.push(tag.otherNames.split(',')[0].trim());
      chip.appendChild(el('span', { class: 'sub' }, sub.join(' · ')));
      chip.addEventListener('click', () => toggleTag(tag));
      if (tag.source === 'lora' && tag.loraId !== null) {
        const link = el('a', { href: '#', title: '登録元の LoRA' }, 'LoRA');
        link.addEventListener('click', (ev) => { ev.preventDefault(); ev.stopPropagation(); selectLora(tag.loraId); });
        chip.appendChild(link);
      }
      wrap.appendChild(chip);
    }
    return wrap;
  }

  // --- cell size slider (FR-39) ---------------------------------------------
  const slider = $('#cell-slider');
  const cellValue = $('#cell-value');

  function applyCellSize(px) {
    document.documentElement.style.setProperty('--cell', `${px}px`);
    cellValue.textContent = `${px}px`;
    try { localStorage.setItem('cellSize', String(px)); } catch (_) { /* ignore */ }
  }

  slider.addEventListener('input', () => applyCellSize(Number(slider.value)));

  async function initCellSize() {
    try {
      const cfg = await apiGet('/config');
      slider.min = String(cfg.gridMinCell);
      slider.max = String(cfg.gridMaxCell);
    } catch (_) { /* keep HTML defaults */ }
    let saved = null;
    try { saved = Number(localStorage.getItem('cellSize')) || null; } catch (_) { /* ignore */ }
    const min = Number(slider.min), max = Number(slider.max);
    const initial = Math.min(max, Math.max(min, saved || Number(slider.value)));
    slider.value = String(initial);
    applyCellSize(initial);
  }

  window.app = { state, setFilter, applyLeftWidth, resetAndLoad, loadMore, selectImage, apiGet, applyCellSize, refreshFolders, refreshLoras, refreshUsedTags, selectFolder, selectFixed, selectLora, toggleTag, clearTags, getDetail: () => currentDetail };

  initSections();
  initLeftResizer();
  initCellSize();
  refreshFolders();
  refreshLoras();
  refreshUsedTags();
  resetAndLoad();
})();
