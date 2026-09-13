/* ComfyUI Prompt Reader front end (design.md §9). No build step; plain ES2020. */
(() => {
  'use strict';

  const PAGE_SIZE = 100;

  const state = {
    filter: { kind: 'all', dir: null, recursive: true }, // kind: all | favorite | missing
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
        if (v !== null && v !== undefined) url.searchParams.set(k, String(v));
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
    setFilter({ kind, dir: null });
    highlightSelection();
  }

  function selectFolder(dir) {
    filterLabel.textContent = dir;
    setFilter({ kind: 'all', dir, recursive: recursiveBox.checked });
    highlightSelection();
  }

  function highlightSelection() {
    const f = state.filter;
    for (const el of document.querySelectorAll('#left .item')) {
      const isFixed = el.dataset.kind !== undefined;
      const on = f.dir === null ? (isFixed && el.dataset.kind === f.kind) : (!isFixed && el.dataset.dir === f.dir);
      el.classList.toggle('selected', on);
    }
  }

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
    frag.appendChild(promptBlock('Negative', g.negativePrompt, 'negative'));
    if (d.extractionStatus === 'full') {
      frag.appendChild(el('div', {}, el('a', { href: `/images/${d.id}/raw-metadata`, target: '_blank' }, '生メタデータを開く')));
    }
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

  window.app = { state, setFilter, resetAndLoad, loadMore, selectImage, apiGet, applyCellSize, refreshFolders, selectFolder, selectFixed, getDetail: () => currentDetail };

  initCellSize();
  refreshFolders();
  resetAndLoad();
})();
