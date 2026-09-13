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
    if (item.isFavorite) {
      const fav = document.createElement('span');
      fav.className = 'fav';
      fav.textContent = '★';
      cell.appendChild(fav);
    }
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

  window.app = { state, setFilter, resetAndLoad, loadMore, selectImage, apiGet, applyCellSize, refreshFolders, selectFolder, selectFixed };

  initCellSize();
  refreshFolders();
  resetAndLoad();
})();
