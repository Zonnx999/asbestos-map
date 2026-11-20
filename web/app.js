/* 전국 석면 건축물 지도 — static front end (Leaflet 1.9.4 + Supercluster 8.0.1 + Fuse.js 7.0.0).
   Data contract: docs/data-contract.md. `?data=fixture` loads web/fixture/ instead of web/data/.
   Everything is driven by the files: when geocoding later fills sido/{code}.json, points simply appear. */
'use strict';
(function () {
  // ---------- config ----------
  const P = new URLSearchParams(location.search);
  const FIXTURE = P.get('data') === 'fixture';
  const BASE = FIXTURE ? 'fixture/' : 'data/';
  const Z = { SGG: 8, POINTS: 9, FADE: 11, SGG_LABEL_MAX: 11, CLUSTER_MAX: 16, ROW: 17 };
  const CLS = {
    2: { name: '석면건축물', color: '#d32f2f', stroke: '#ffffff' },
    1: { name: '여부 미표기', color: '#f9a825', stroke: '#8a5d00' },
    0: { name: '비해당', color: '#9e9e9e', stroke: '#ffffff' },
  };
  const RAMP = {
    c2: ['#fde0dc', '#f7a397', '#ec6a5a', '#d32f2f', '#8e1515'],
    all: ['#e6edf6', '#bacfe5', '#83a9cf', '#4a7fb3', '#1f4e85'],
  };
  const ZERO_FILL = '#f1f0ec';
  const SHORT = { '11': '서울', '21': '부산', '22': '대구', '23': '인천', '24': '광주', '25': '대전', '26': '울산', '29': '세종',
    '31': '경기', '32': '강원', '33': '충북', '34': '충남', '35': '전북', '36': '전남', '37': '경북', '38': '경남', '39': '제주' };
  const GEO = { src: '원자료 좌표', 'vworld-place': '건물명 검색으로 추정 (정확도 낮음)', vworld: '주소로 추정 (VWorld 지오코딩)', kakao: '주소로 추정 (카카오 지오코딩)',
    nominatim: '주소로 추정 (OSM Nominatim, 오차가 클 수 있음)' };
  const LIST_STEP = 200;
  // CARTO raster tiles now need a key: keyless URLs return an "API KEY REQUIRED" watermark tile with HTTP 200.
  // Put the keyed Positron URL template here to make CARTO the default base map (OSM stays as fallback).
  const CARTO_URL = '';
  const MOBILE = window.matchMedia('(max-width: 720px)');

  // ---------- state ----------
  const S = {
    manifest: null, summary: {}, sidoGeo: null, sggGeo: null,
    sidoCounts: new Map(), sources: new Map(),
    metric: 'c2', classes: new Set([2, 1]),
    rows: [], pointsReq: new Map(), nocoordReq: new Map(),
    index: null, fuse: null, fuseDirty: true,
    selected: null, listLimit: LIST_STEP, detail: null, selRow: null,
    query: '', styleKey: '', bins: { c2: [], all: [] },
  };

  // ---------- helpers ----------
  const $ = (id) => document.getElementById(id);
  const fmt = (n) => (n == null || !isFinite(n)) ? '–' : Number(n).toLocaleString('ko-KR');
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } },
  };
  const metricOf = (e) => !e ? 0 : S.metric === 'c2' ? (e.c2 || 0) : (e.c2 || 0) + (e.c1 || 0) + (e.c0 || 0);
  const isSidoOnly = (code) => /000$/.test(code);
  const safeUrl = (u) => (typeof u === 'string' && /^https:\/\//.test(u)) ? u : null;
  const pct = (a, b) => b ? (100 * a / b).toFixed(a / b < 0.1 ? 1 : 0) : '0';

  // fetch queue: max 3 in flight, thin progress bar, 404/parse error → null (treated as empty)
  let inflight = 0;
  const queue = [];
  function getJSON(path) {
    return new Promise((resolve) => { queue.push({ path, resolve }); pump(); });
  }
  function pump() {
    while (inflight < 3 && queue.length) {
      const { path, resolve } = queue.shift();
      inflight++; progress();
      fetch(BASE + path)
        .then((r) => (r.ok ? r.json() : null))
        .catch((e) => { console.warn('load failed', path, e); return null; })
        .then((v) => { inflight--; progress(); resolve(v); pump(); });
    }
  }
  function progress() { $('progress').hidden = inflight + queue.length === 0; }

  function decodeTable(t) {
    if (!t || !Array.isArray(t.fields) || !Array.isArray(t.rows)) return [];
    const f = t.fields;
    return t.rows.map((r) => { const o = {}; for (let i = 0; i < f.length; i++) o[f[i]] = r[i] == null ? null : r[i]; return o; });
  }
  function addRows(list, sido) {
    for (const o of list) {
      o.c = Number(o.c);
      if (!(o.c in CLS)) o.c = 1;
      o.lat = o.lat == null ? null : Number(o.lat);
      o.lng = o.lng == null ? null : Number(o.lng);
      o.hasPos = isFinite(o.lat) && isFinite(o.lng) && o.lat !== null && o.lng !== null;
      o.sido = sido;
      o.sgg = o.sgg == null ? null : String(o.sgg);
      o.i = S.rows.length;
      S.rows.push(o);
    }
    S.fuseDirty = true;
  }

  const sidoName = (code) => (S.sidoCounts.get(code) || {}).name || code;
  function sggName(code) {
    const e = S.summary[code];
    if (e && e.name) return e.name;
    const f = S.sggGeo && S.sggGeo.features.find((x) => String(x.properties.code) === code);
    return f ? f.properties.name : code;
  }
  function regionCounts(level, code) {
    if (level === 'sido') return S.sidoCounts.get(code) || null;
    return S.summary[code] || null;
  }
  function regionName(level, code) {
    if (level === 'sido') return sidoName(code);
    const e = S.summary[code];
    const sc = (e && e.sido) || code.slice(0, 2);
    return `${sidoName(sc)} ${sggName(code)}`;
  }

  // ---------- map ----------
  const map = L.map('map', { preferCanvas: true, minZoom: 6, maxZoom: 18, zoomSnap: 0.5, zoomControl: false,
    maxBounds: [[29.5, 119.5], [41.5, 136]], maxBoundsViscosity: 0.8 });
  L.control.zoom({ position: 'topright', zoomInTitle: '확대', zoomOutTitle: '축소' }).addTo(map);
  map.attributionControl.setPrefix('<a href="https://leafletjs.com" target="_blank" rel="noopener">Leaflet</a>');
  map.attributionControl.addAttribution('경계: 통계청(southkorea-maps) · 자료: <a href="https://www.data.go.kr" target="_blank" rel="noopener">공공데이터포털</a>');
  map.fitBounds([[33.1, 124.9], [38.6, 130.9]]);
  const renderer = L.canvas({ padding: 0.3 });

  const OSM_ATTR = '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors';
  const osm = () => L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, attribution: OSM_ATTR });
  const bases = { '흐린 지도 (OSM)': osm().on('add', () => mapEl.classList.add('muted')).on('remove', () => mapEl.classList.remove('muted')),
    'OpenStreetMap': osm() };
  const mapEl = document.getElementById('map');
  if (CARTO_URL) {
    bases['CARTO 밝은 지도'] = L.tileLayer(CARTO_URL, { subdomains: 'abcd', maxZoom: 19,
      attribution: OSM_ATTR + ' &copy; <a href="https://carto.com/attributions" target="_blank" rel="noopener">CARTO</a>' });
  }
  const savedBase = store.get('asbmap-base');
  const base = bases[savedBase] ? savedBase : CARTO_URL ? 'CARTO 밝은 지도' : '흐린 지도 (OSM)';
  bases[base].addTo(map);
  L.control.layers(bases, null, { position: 'topright' }).addTo(map);
  map.on('baselayerchange', (e) => store.set('asbmap-base', e.name));
  // fallback: CARTO failing entirely (errors, no loads) → switch to OSM once
  if (CARTO_URL) (function () {
    let ok = 0, bad = 0, switched = false;
    const carto = bases['CARTO 밝은 지도'];
    carto.on('tileload', () => { ok++; });
    carto.on('tileerror', () => {
      bad++;
      if (!switched && ok === 0 && bad >= 6 && map.hasLayer(carto)) {
        switched = true; map.removeLayer(carto); bases['흐린 지도 (OSM)'].addTo(map);
      }
    });
  })();

  let sidoLayer = null, sggLayer = null, selLayer = null, selRing = null;
  const labelLayer = L.layerGroup().addTo(map);
  const dotLayer = L.layerGroup().addTo(map);
  const clusterLayer = L.layerGroup().addTo(map);

  // ---------- choropleth ----------
  function computeBins() {
    for (const m of ['c2', 'all']) {
      const save = S.metric; S.metric = m;
      const sidoVals = [...S.sidoCounts.values()].map(metricOf);
      const sggVals = Object.keys(S.summary).filter((k) => !isSidoOnly(k)).map((k) => metricOf(S.summary[k]));
      S.bins[m] = { sido: quantiles(sidoVals), sgg: quantiles(sggVals) };
      S.metric = save;
    }
  }
  function quantiles(values) {
    const v = values.filter((x) => x > 0).sort((a, b) => a - b);
    if (!v.length) return [];
    const t = [];
    for (const p of [0.2, 0.4, 0.6, 0.8]) {
      const q = v[Math.min(v.length - 1, Math.floor(p * v.length))];
      if (q < v[v.length - 1] && !t.includes(q)) t.push(q);
    }
    return t; // thresholds; class = number of thresholds strictly below value
  }
  function rampFor(nClasses) {
    const r = RAMP[S.metric];
    if (nClasses >= 5) return r;
    if (nClasses === 1) return [r[3]];
    return Array.from({ length: nClasses }, (_, k) => r[Math.round(k * 4 / (nClasses - 1))]);
  }
  function colorFor(value, thresholds) {
    if (!value) return ZERO_FILL;
    let k = 0; while (k < thresholds.length && value > thresholds[k]) k++;
    return rampFor(thresholds.length + 1)[k];
  }
  const level = () => (map.getZoom() >= Z.SGG && sggLayer && Object.keys(S.summary).length ? 'sgg' : 'sido');

  function featureStyle(lv, code) {
    const z = map.getZoom();
    const e = lv === 'sido' ? S.sidoCounts.get(code) : S.summary[code];
    const v = metricOf(e);
    const fo = z >= Z.FADE ? 0.14 : lv === 'sido' ? 0.72 : 0.62;
    return { renderer, color: lv === 'sido' ? '#ffffff' : '#8a857c', weight: lv === 'sido' ? 1.2 : 0.7, opacity: lv === 'sido' ? 1 : 0.75,
      fillColor: colorFor(v, S.bins[S.metric][lv]), fillOpacity: v ? fo : fo * 0.6 };
  }
  function makeLayer(geo, lv) {
    return L.geoJSON(geo, {
      renderer,
      style: (f) => featureStyle(lv, String(f.properties.code)),
      onEachFeature: (f, layer) => {
        const code = String(f.properties.code);
        layer.on('click', () => selectRegion(lv, code));
        if (!MOBILE.matches) layer.bindTooltip(() => tipHTML(lv, code), { sticky: true, className: 'rt', direction: 'top', offset: [0, -8] });
        f._lp = labelPoint(f.geometry);
      },
    });
  }
  function tipHTML(lv, code) {
    const e = regionCounts(lv, code);
    if (!e) return `<b>${esc(regionName(lv, code))}</b><br>공개 자료 없음 (0건)`;
    const all = (e.c2 || 0) + (e.c1 || 0) + (e.c0 || 0);
    return `<b>${esc(regionName(lv, code))}</b><br>석면건축물 <b>${fmt(e.c2)}</b> · 조사대상 전체 ${fmt(all)}` +
      `<br><span style="color:#777">지도 위 점 ${fmt(e.points || 0)} · 좌표 없음 ${fmt(e.nocoord || 0)}</span>`;
  }
  function labelPoint(g) {
    if (!g) return null;
    const polys = g.type === 'Polygon' ? [g.coordinates] : g.type === 'MultiPolygon' ? g.coordinates : [];
    let best = null, bestA = 0;
    for (const poly of polys) {
      const ring = poly[0]; if (!ring || ring.length < 3) continue;
      let a = 0, cx = 0, cy = 0;
      for (let i = 0; i < ring.length; i++) {
        const [x0, y0] = ring[i], [x1, y1] = ring[(i + 1) % ring.length];
        const f = x0 * y1 - x1 * y0; a += f; cx += (x0 + x1) * f; cy += (y0 + y1) * f;
      }
      if (Math.abs(a) > bestA && a !== 0) { bestA = Math.abs(a); best = [cy / (3 * a), cx / (3 * a)]; }
    }
    return best;
  }

  function restyle(force) {
    const lv = level();
    const z = map.getZoom();
    const key = `${lv}|${S.metric}|${z >= Z.FADE ? 'fade' : 'full'}`;
    if (!force && key === S.styleKey) return false;
    const changedLayer = !S.styleKey || S.styleKey.split('|')[0] !== lv;
    S.styleKey = key;
    if (sidoLayer) { if (lv === 'sido') { if (!map.hasLayer(sidoLayer)) sidoLayer.addTo(map); sidoLayer.eachLayer((l) => l.setStyle(featureStyle('sido', String(l.feature.properties.code)))); } else map.removeLayer(sidoLayer); }
    if (sggLayer) { if (lv === 'sgg') { if (!map.hasLayer(sggLayer)) sggLayer.addTo(map); sggLayer.eachLayer((l) => l.setStyle(featureStyle('sgg', String(l.feature.properties.code)))); } else map.removeLayer(sggLayer); }
    renderChoroLegend();
    return changedLayer;
  }

  function renderLabels() {
    labelLayer.clearLayers();
    const z = map.getZoom();
    const lv = level();
    let feats = [];
    if (lv === 'sido' && S.sidoGeo) feats = S.sidoGeo.features.map((f) => ['sido', f]);
    else if (lv === 'sgg' && z >= Z.POINTS && z < Z.SGG_LABEL_MAX && S.sggGeo) {
      const b = map.getBounds();
      feats = S.sggGeo.features.filter((f) => f._lp && b.contains(f._lp)).map((f) => ['sgg', f]);
    }
    for (const [lvl, f] of feats) {
      const code = String(f.properties.code);
      const v = metricOf(regionCounts(lvl, code));
      if (!v || !f._lp) continue;
      const nm = lvl === 'sido' ? (SHORT[code] || f.properties.name) : sggName(code);
      L.marker(f._lp, { interactive: false, keyboard: false,
        icon: L.divIcon({ className: 'region-label', html: `${fmt(v)}<span>${esc(nm)}</span>`, iconSize: [90, 28], iconAnchor: [45, 14] }) }).addTo(labelLayer);
    }
  }

  function renderChoroLegend() {
    const lv = level();
    const t = S.bins[S.metric][lv] || [];
    const ramp = rampFor(t.length + 1);
    const items = [`<div class="bin"><div class="sw" style="background:${ZERO_FILL}"></div><div class="lab">0</div></div>`];
    if (t.length) {
      let lo = 1;
      for (let k = 0; k <= t.length; k++) {
        const hi = t[k];
        const lab = k < t.length ? (lo === hi ? fmt(lo) : `${fmt(lo)}–${fmt(hi)}`) : `${fmt(lo)}+`;
        items.push(`<div class="bin"><div class="sw" style="background:${ramp[k]}"></div><div class="lab">${lab}</div></div>`);
        if (k < t.length) lo = hi + 1;
      }
    } else items.push(`<div class="bin"><div class="sw" style="background:${ramp[0]}"></div><div class="lab">1+</div></div>`);
    const tot = (S.manifest && S.manifest.totals) || {};
    const what = S.metric === 'c2' ? '석면건축물 수' : '조사대상 건물 수(세 분류 합)';
    $('choroLegend').innerHTML = `<div>${lv === 'sido' ? '시·도' : '시·군·구'}별 ${what}</div><div class="bins">${items.join('')}</div>` +
      `<div>좌표 없는 ${fmt(tot.nocoord || 0)}건 포함 · 확대하면 시·군·구별로 바뀝니다</div>`;
  }

  // ---------- points ----------
  let indexTimer = null;
  function scheduleIndex() {
    clearTimeout(indexTimer);
    indexTimer = setTimeout(() => { rebuildIndex(); renderPoints(); updateLoadNote(); }, 120);
  }
  function rebuildIndex() {
    const feats = [];
    for (const r of S.rows) {
      if (r.hasPos && S.classes.has(r.c)) feats.push({ type: 'Feature', properties: { i: r.i }, geometry: { type: 'Point', coordinates: [r.lng, r.lat] } });
    }
    if (!feats.length) { S.index = null; return; }
    const idx = new Supercluster({
      radius: 60, maxZoom: Z.CLUSTER_MAX, minZoom: 0,
      map: (p) => { const c = S.rows[p.i].c; return { n2: c === 2 ? 1 : 0, n1: c === 1 ? 1 : 0, n0: c === 0 ? 1 : 0 }; },
      reduce: (a, b) => { a.n2 += b.n2; a.n1 += b.n1; a.n0 += b.n0; },
    });
    idx.load(feats);
    S.index = idx;
  }
  function renderPoints() {
    dotLayer.clearLayers(); clusterLayer.clearLayers();
    if (selLayer) selLayer.bringToFront();
    if (S.index && map.getZoom() >= Z.POINTS) {
      const b = map.getBounds().pad(0.15);
      const bbox = [Math.max(-180, b.getWest()), Math.max(-85, b.getSouth()), Math.min(180, b.getEast()), Math.min(85, b.getNorth())];
      const items = S.index.getClusters(bbox, Math.floor(map.getZoom()));
      const singles = [];
      for (const f of items) {
        if (f.properties.cluster) clusterLayer.addLayer(clusterMarker(f));
        else singles.push(S.rows[f.properties.i]);
      }
      singles.sort((a, b2) => a.c - b2.c); // grey bottom, amber, red on top
      for (const r of singles) dotLayer.addLayer(dotMarker(r));
    }
    drawRing();
  }
  function dotMarker(r) {
    const c = CLS[r.c];
    const m = L.circleMarker([r.lat, r.lng], {
      renderer, radius: r.c === 2 ? 6 : 5.5, color: c.stroke, weight: r.c === 1 ? 1.8 : 1.2, opacity: 1,
      dashArray: r.c === 1 ? '3,2' : null, fillColor: c.color, fillOpacity: 0.95, bubblingMouseEvents: false,
    });
    m.on('click', () => showDetail(r));
    if (!MOBILE.matches) m.bindTooltip(esc(r.name || '(이름 없음)'), { direction: 'top', offset: [0, -6] });
    return m;
  }
  function clusterMarker(f) {
    const p = f.properties;
    const [lng, lat] = f.geometry.coordinates;
    const n = p.point_count;
    const cls = p.n2 > 0 ? 'c2' : p.n1 > 0 ? 'c1' : 'c0';
    const s = n < 10 ? 30 : n < 100 ? 38 : n < 1000 ? 44 : 52;
    const sub = p.n2 > 0 && p.n2 < n && s >= 38 ? `<small>석면 ${p.n2 >= 1000 ? Math.round(p.n2 / 1000) + 'k' : p.n2}</small>` : '';
    const m = L.marker([lat, lng], {
      keyboard: false, title: `건물 ${fmt(n)}곳 (석면건축물 ${fmt(p.n2)})`,
      icon: L.divIcon({ className: '', html: `<div class="cl ${cls}" style="width:${s}px;height:${s}px">${p.point_count_abbreviated}${sub}</div>`, iconSize: [s, s] }),
    });
    m.on('click', () => {
      let z = Z.ROW;
      try { z = S.index.getClusterExpansionZoom(p.cluster_id); } catch (e) { /* stale index */ }
      map.flyTo([lat, lng], Math.min(Math.max(z, map.getZoom() + 1), 18), { duration: 0.6 });
    });
    return m;
  }
  function drawRing() {
    if (selRing) { map.removeLayer(selRing); selRing = null; }
    const r = S.selRow;
    if (!r || !r.hasPos) return;
    selRing = L.circleMarker([r.lat, r.lng], { renderer, radius: 11, color: '#111', weight: 2.5, fill: false, interactive: false }).addTo(map);
  }

  // lazy loading
  function sidoBBox(s) {
    if (Array.isArray(s.bbox) && s.bbox.length === 4 && s.bbox.every(isFinite)) return L.latLngBounds([s.bbox[1], s.bbox[0]], [s.bbox[3], s.bbox[2]]);
    if (sidoLayer) {
      let out = null;
      sidoLayer.eachLayer((l) => { if (String(l.feature.properties.code) === s.code) out = l.getBounds(); });
      return out;
    }
    return null;
  }
  function maybeLoadPoints() {
    if (map.getZoom() < Z.POINTS) return;
    const b = map.getBounds();
    for (const s of S.sidoCounts.values()) {
      if (s.points === 0) continue; // manifest says no coordinates here (yet)
      const bb = sidoBBox(s);
      if (bb && b.intersects(bb)) loadPoints(s.code);
    }
  }
  function loadPoints(code) {
    if (S.pointsReq.has(code)) return S.pointsReq.get(code);
    const e = S.sidoCounts.get(code);
    const p = (e && e.points === 0) ? Promise.resolve() : getJSON(`sido/${code}.json`).then((t) => {
      addRows(decodeTable(t), code); // a row with null lat/lng stays listable, just not drawn
      afterLoad();
    });
    S.pointsReq.set(code, p);
    return p;
  }
  function loadNocoord(code) {
    if (S.nocoordReq.has(code)) return S.nocoordReq.get(code);
    const e = S.sidoCounts.get(code);
    const p = (e && e.nocoord === 0) ? Promise.resolve() : getJSON(`nocoord/${code}.json`).then((t) => {
      addRows(decodeTable(t).map((o) => { o.lat = null; o.lng = null; return o; }), code);
      afterLoad();
    });
    S.nocoordReq.set(code, p);
    return p;
  }
  function afterLoad() {
    scheduleIndex();
    if (S.query) scheduleSearch();
  }
  function loadedSido() {
    const out = [];
    for (const code of S.sidoCounts.keys()) if (S.pointsReq.has(code) || S.nocoordReq.has(code)) out.push(code);
    return out;
  }

  // fit options that keep the target clear of the mobile bottom sheet (half height)
  const fitOpts = (maxZoom) => ({ paddingTopLeft: [20, 20], paddingBottomRight: [20, MOBILE.matches ? Math.round(map.getSize().y * 0.5) : 20], maxZoom });

  // ---------- region card ----------
  function selectRegion(lv, code, opts) {
    opts = opts || {};
    S.selected = { level: lv, code };
    S.listLimit = LIST_STEP;
    if (selLayer) { map.removeLayer(selLayer); selLayer = null; }
    const src = lv === 'sido' ? S.sidoGeo : S.sggGeo;
    const f = src && src.features.find((x) => String(x.properties.code) === code);
    if (f) {
      selLayer = L.geoJSON(f, { renderer, interactive: false, style: { color: '#111', weight: 2.5, fill: false } }).addTo(map);
      if (opts.fit) map.fitBounds(selLayer.getBounds(), fitOpts(14));
    }
    renderPoints();
    renderFocus(true);
    const sc = lv === 'sido' ? code : ((S.summary[code] && S.summary[code].sido) || code.slice(0, 2));
    Promise.all([loadPoints(sc), loadNocoord(sc)]).then(() => renderFocus());
  }
  function regionRows() {
    const sel = S.selected; if (!sel) return [];
    const sc = sel.level === 'sido' ? sel.code : ((S.summary[sel.code] && S.summary[sel.code].sido) || sel.code.slice(0, 2));
    return S.rows.filter((r) => r.sido === sc && S.classes.has(r.c) && (sel.level === 'sido' || r.sgg === sel.code))
      .sort((a, b) => b.c - a.c || (b.hasPos - a.hasPos) || String(a.name || '').localeCompare(String(b.name || ''), 'ko'));
  }
  function rowItem(r) {
    return `<li data-i="${r.i}"><span class="dot c${r.c}"></span><span class="t"><span class="nm">${esc(r.name || '(이름 없음)')}` +
      `${r.hasPos ? '' : '<span class="nopos">위치 미상</span>'}</span><small>${esc(r.addr || '주소 정보 없음')}</small></span></li>`;
  }
  function renderFocus(scroll) {
    const box = $('focus');
    let html = '';
    if (S.detail) {
      html += `<div class="card detail"><div class="row"><span class="meta">건물 정보</span><button class="icon-btn" data-act="close-detail" aria-label="닫기">×</button></div>${detailHTML(S.detail)}</div>`;
    }
    const sel = S.selected;
    if (sel) {
      const e = regionCounts(sel.level, sel.code) || {};
      const sc = sel.level === 'sido' ? sel.code : ((S.summary[sel.code] && S.summary[sel.code].sido) || sel.code.slice(0, 2));
      const ready = S.pointsReq.has(sc) && S.nocoordReq.has(sc);
      const rows = regionRows();
      let extra = '';
      if (sel.level === 'sido') {
        const so = S.summary[`${sel.code}000`];
        if (so) extra = `<div class="meta">시·군·구를 알 수 없는 ${fmt((so.c2 || 0) + (so.c1 || 0) + (so.c0 || 0))}건 포함</div>`;
      }
      const filt = [2, 1, 0].filter((c) => !S.classes.has(c)).map((c) => CLS[c].name);
      html += `<div class="card region"${S.detail ? ' style="margin-top:10px"' : ''}>
        <div class="row"><h3>${esc(regionName(sel.level, sel.code))}</h3><button class="icon-btn" data-act="close-region" aria-label="닫기">×</button></div>
        <div class="mini"><div class="r"><b>${fmt(e.c2 || 0)}</b>석면건축물</div><div><b>${fmt(e.c1 || 0)}</b>여부 미표기</div><div><b>${fmt(e.c0 || 0)}</b>비해당</div></div>
        <div class="meta">지도 위 점 ${fmt(e.points || 0)} · 좌표 없음 ${fmt(e.nocoord || 0)} (지역 색에만 반영)</div>${extra}
        <div class="row" style="margin-top:6px"><button class="link-btn" data-act="fit-region">지도에서 이 지역 보기</button>
          <span class="meta">${filt.length ? esc(filt.join('·')) + ' 숨김' : ''}</span></div>
        ${!ready && !rows.length ? '<p class="empty-list">건물 목록 불러오는 중…</p>'
          : rows.length ? `<ul class="list">${rows.slice(0, S.listLimit).map(rowItem).join('')}${rows.length > S.listLimit ? `<li class="more" data-act="more">더 보기 (${fmt(rows.length - S.listLimit)}건 남음)</li>` : ''}</ul>`
          : '<p class="empty-list">보여 줄 건물이 없습니다 (분류 필터 확인).</p>'}
      </div>`;
    }
    box.innerHTML = html;
    box.hidden = !html;
    if (scroll && html) {
      if (MOBILE.matches && sheetState === 'peek') setSheet('half');
      // not scrollIntoView: it would also scroll the overflow:hidden app container and shift the sheet
      const sc = $('scroll');
      sc.scrollTo({ top: box.getBoundingClientRect().top - sc.getBoundingClientRect().top + sc.scrollTop - 8, behavior: 'smooth' });
    }
  }

  // ---------- detail (popup on desktop, sheet card on mobile / rows without position) ----------
  function detailHTML(r) {
    const na = '<span class="na">정보 없음</span>';
    const v = (x) => (x == null || x === '') ? na : esc(x);
    const area = (r.area == null || r.area === '' || !isFinite(r.area)) ? na : `${Number(r.area).toLocaleString('ko-KR', { maximumFractionDigits: 1 })} ㎡`;
    const region = r.sgg && !isSidoOnly(r.sgg) ? `${sidoName(r.sido)} ${sggName(r.sgg)}` : sidoName(r.sido);
    const pos = r.hasPos ? esc(GEO[r.geo] || (r.geo ? `주소로 추정 (${r.geo})` : '정보 없음')) : '위치 미상 (좌표 없음, 지역 수에만 반영)';
    const srcs = String(r.src || '').split(',').map((s) => s.trim()).filter(Boolean).map((id) => {
      const s = S.sources.get(id);
      const label = s ? `${s.publisher || ''} ${s.title || ''}`.trim() : `자료 ${id}`;
      const date = s && s.updated ? ` · 기준일 ${esc(s.updated)}` : '';
      return /^\d+$/.test(id)
        ? `<a href="https://www.data.go.kr/data/${id}/fileData.do" target="_blank" rel="noopener">${esc(label)}</a>${date}`
        : `${esc(label)}${date}`;
    });
    return `<div class="pop"><h3>${esc(r.name || '(이름 없음)')}</h3><span class="tag c${r.c}">${CLS[r.c].name}</span>
      <table>
        <tr><td>주소</td><td>${v(r.addr)}</td></tr>
        <tr><td>지역</td><td>${esc(region)}</td></tr>
        <tr><td>용도</td><td>${v(r.use)}</td></tr>
        <tr><td>연도</td><td>${v(r.year)}</td></tr>
        <tr><td>면적</td><td>${area}</td></tr>
        <tr><td>위치</td><td>${pos}</td></tr>
      </table>
      <div class="src">출처: ${srcs.length ? srcs.join('<br>') : '정보 없음'}</div>
      ${r.c === 1 ? '<div class="src">‘여부 미표기’는 석면이 없다는 뜻이 아닙니다.</div>' : ''}
      <div class="actions">${r.addr ? `<button class="btn" data-copy="${r.i}">주소 복사</button>` : ''}${!r.hasPos ? `<button class="btn" data-act="zoom-row" data-row="${r.i}">지역으로 이동</button>` : ''}</div></div>`;
  }
  function showDetail(r) {
    if (r.hasPos && !MOBILE.matches) {
      S.detail = null; renderFocus();
      // open first: replacing an open popup fires popupclose, which clears the ring
      L.popup({ maxWidth: 320, autoPanPadding: [30, 30] }).setLatLng([r.lat, r.lng]).setContent(detailHTML(r)).openOn(map);
    } else {
      map.closePopup();
      S.detail = r; renderFocus(true);
    }
    S.selRow = r.hasPos ? r : null;
    drawRing();
  }
  function zoomToRowRegion(r) {
    const g = r.sgg && S.sggGeo && S.sggGeo.features.find((x) => String(x.properties.code) === r.sgg);
    const lv = g ? 'sgg' : 'sido';
    const code = g ? r.sgg : r.sido;
    const geo = g || (S.sidoGeo && S.sidoGeo.features.find((x) => String(x.properties.code) === r.sido));
    if (geo) map.fitBounds(L.geoJSON(geo).getBounds(), fitOpts(13));
    return { lv, code };
  }
  function focusRow(r) {
    if (r.hasPos) {
      const ll = L.latLng(r.lat, r.lng);
      if (map.getZoom() >= Z.CLUSTER_MAX && map.getBounds().contains(ll)) showDetail(r);
      else {
        map.once('moveend', () => showDetail(r));
        // on mobile, centre the point in the upper half (the sheet covers the lower half)
        const c = MOBILE.matches ? map.unproject(map.project(ll, Z.ROW).add([0, map.getSize().y * 0.25]), Z.ROW) : ll;
        map.flyTo(c, Z.ROW, { duration: 0.8 });
      }
    } else {
      zoomToRowRegion(r);
      showDetail(r);
    }
  }
  map.on('popupclose', () => { if (!S.detail) { S.selRow = null; drawRing(); } });

  // ---------- search ----------
  let searchTimer = null;
  function scheduleSearch() { clearTimeout(searchTimer); searchTimer = setTimeout(runSearch, 220); }
  function regionsMatching(q) {
    const toks = q.split(/\s+/).filter(Boolean);
    const out = [];
    for (const s of S.sidoCounts.values()) {
      const full = `${s.name} ${SHORT[s.code] || ''}`;
      if (toks.every((t) => full.includes(t))) out.push({ lv: 'sido', code: s.code, name: s.name });
    }
    for (const [code, e] of Object.entries(S.summary)) {
      if (isSidoOnly(code)) continue;
      const full = `${sidoName(e.sido)} ${SHORT[e.sido] || ''} ${e.name}`;
      if (toks.every((t) => full.includes(t))) out.push({ lv: 'sgg', code, name: `${sidoName(e.sido)} ${e.name}` });
    }
    return out.slice(0, 8);
  }
  function runSearch() {
    const q = S.query.trim();
    const box = $('results');
    if (!q) { box.hidden = true; box.innerHTML = ''; return; }
    const regs = regionsMatching(q);
    if (S.fuseDirty || !S.fuse) {
      S.fuse = new Fuse(S.rows, { keys: ['name', 'addr'], threshold: 0.3, ignoreLocation: true, minMatchCharLength: 2 });
      S.fuseDirty = false;
    }
    const hits = q.length >= 2 ? S.fuse.search(q, { limit: 300 }).map((h) => h.item).filter((r) => S.classes.has(r.c)).slice(0, 50) : [];
    const loaded = loadedSido();
    const allLoaded = loaded.length >= S.sidoCounts.size;
    let html = '<h2>검색 결과</h2>';
    if (regs.length || hits.length) {
      html += '<ul class="list">' + regs.map((g) => `<li class="region" data-region="${g.lv}:${esc(g.code)}"><span class="t"><span class="nm">${esc(g.name)}</span>` +
        `<small>석면건축물 ${fmt((regionCounts(g.lv, g.code) || {}).c2 || 0)}</small></span></li>`).join('') + hits.map(rowItem).join('') + '</ul>';
    } else html += '<p class="empty-list">일치하는 지역·건물이 없습니다.</p>';
    html += `<p class="note">건물 검색 범위: 불러온 시·도 ${loaded.length}곳의 건물 ${fmt(S.rows.length)}건.` +
      (allLoaded ? '' : ' 지역을 누르거나 확대하면 그 지역 건물을 불러옵니다. <button class="link-btn" data-act="load-all">전국 자료 모두 불러와 검색</button>') + '</p>';
    box.innerHTML = html;
    box.hidden = false;
  }
  function loadAll() {
    const ps = [];
    for (const code of S.sidoCounts.keys()) { ps.push(loadPoints(code)); ps.push(loadNocoord(code)); }
    Promise.all(ps).then(runSearch);
  }

  // ---------- panel: totals, filters, sources, footer ----------
  function renderStatic() {
    const m = S.manifest || {};
    const t = m.totals || {};
    $('t2').textContent = fmt(t.c2 || 0); $('t1').textContent = fmt(t.c1 || 0); $('t0').textContent = fmt(t.c0 || 0);
    $('f2').textContent = fmt(t.c2 || 0); $('f1').textContent = fmt(t.c1 || 0); $('f0').textContent = fmt(t.c0 || 0);
    const all = (t.c2 || 0) + (t.c1 || 0) + (t.c0 || 0);
    $('coordline').textContent = all
      ? `좌표가 있어 점으로 찍히는 건물 ${fmt(t.points || 0)}곳(${pct(t.points || 0, all)}%). 나머지 ${fmt(t.nocoord || 0)}곳은 주소만 있어 지역 색으로 셉니다.`
      : '';
    const srcs = (m.sources || []).slice().sort((a, b) => String(a.publisher || '').localeCompare(String(b.publisher || ''), 'ko'));
    $('srcN').textContent = srcs.length ? `(${srcs.length})` : '';
    $('sources').innerHTML = srcs.map((s) => {
      const url = safeUrl(s.url) || (/^\d+$/.test(String(s.id)) ? `https://www.data.go.kr/data/${s.id}/fileData.do` : null);
      const name = esc(s.publisher || s.id);
      return `<li>${url ? `<a href="${esc(url)}" target="_blank" rel="noopener">${name}</a>` : name} · ${esc(s.title || '')}<br>` +
        `<small>기준일 ${esc(s.updated || '정보 없음')}${s.rows != null ? ` · ${fmt(s.rows)}행` : ''}</small></li>`;
    }).join('') || '<li>출처 목록 없음</li>';
    const built = m.built_at ? String(m.built_at).slice(0, 10) : '정보 없음';
    $('foot').innerHTML = `${FIXTURE ? '<b style="color:#a01e1e">시험용 가상 자료(fixture)입니다.</b><br>' : ''}` +
      `자료 기준일(생성) ${esc(built)} · 출처 공공데이터포털(data.go.kr) 각 기관 공개 자료<br>` +
      `지도 © OpenStreetMap contributors${CARTO_URL ? ' © CARTO' : ''} · 경계 통계청(southkorea-maps, 2018)`;
    if (FIXTURE) $('coordline').textContent = '[시험용 가상 자료] ' + $('coordline').textContent;
  }
  function updateLoadNote() {
    const z = map.getZoom();
    const withPts = [...S.sidoCounts.values()].filter((s) => s.points == null || s.points > 0);
    let txt;
    if (!withPts.length) txt = '아직 좌표가 있는 건물이 없어 지역 색과 지역별 목록으로만 보여 줍니다.';
    else if (z < Z.POINTS) txt = `지도를 더 확대하면(${Z.POINTS}단계 이상) 그 지역 건물이 점으로 나타납니다.`;
    else {
      const n = S.rows.filter((r) => r.hasPos).length;
      txt = `불러온 건물 점 ${fmt(n)}개. 지도를 옮기면 그 시·도 자료를 이어서 불러옵니다.`;
    }
    $('loadnote').textContent = txt;
  }

  // ---------- view updates ----------
  function updateView() {
    restyle(false);
    renderLabels();
    maybeLoadPoints();
    renderPoints();
    updateLoadNote();
  }
  map.on('moveend', updateView);

  // ---------- UI events ----------
  $('metric').addEventListener('click', (e) => {
    const b = e.target.closest('button'); if (!b) return;
    S.metric = b.dataset.v;
    [...e.currentTarget.children].forEach((x) => x.classList.toggle('on', x === b));
    restyle(true); renderLabels(); renderPoints();
  });
  $('classes').addEventListener('click', (e) => {
    const b = e.target.closest('button'); if (!b) return;
    const c = Number(b.dataset.c);
    if (S.classes.has(c)) S.classes.delete(c); else S.classes.add(c);
    b.classList.toggle('on', S.classes.has(c)); b.setAttribute('aria-pressed', S.classes.has(c));
    scheduleIndex(); renderFocus(); if (S.query) scheduleSearch();
  });
  $('q').addEventListener('input', (e) => { S.query = e.target.value; scheduleSearch(); });
  $('q').addEventListener('focus', () => { if (MOBILE.matches && sheetState === 'peek') setSheet('half'); });

  function copyText(t) {
    const done = () => {};
    if (navigator.clipboard && navigator.clipboard.writeText) return navigator.clipboard.writeText(t).then(done, () => legacyCopy(t));
    legacyCopy(t);
  }
  function legacyCopy(t) {
    const ta = document.createElement('textarea'); ta.value = t; ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta); ta.select(); try { document.execCommand('copy'); } catch (e) { /* ignore */ } ta.remove();
  }
  document.addEventListener('click', (e) => {
    const el = e.target.closest('[data-i],[data-region],[data-act],[data-copy]');
    if (!el) return;
    if (el.dataset.copy != null) {
      const r = S.rows[Number(el.dataset.copy)]; if (r && r.addr) { copyText(String(r.addr)); el.textContent = '복사됨'; }
      return;
    }
    if (el.dataset.region) { const [lv, code] = el.dataset.region.split(':'); selectRegion(lv, code, { fit: true }); return; }
    if (el.dataset.i != null && !el.dataset.act) { const r = S.rows[Number(el.dataset.i)]; if (r) focusRow(r); return; }
    switch (el.dataset.act) {
      case 'close-region': S.selected = null; if (selLayer) { map.removeLayer(selLayer); selLayer = null; } renderFocus(); break;
      case 'close-detail': S.detail = null; S.selRow = null; drawRing(); renderFocus(); break;
      case 'fit-region': if (selLayer) map.fitBounds(selLayer.getBounds(), fitOpts(14)); break;
      case 'more': S.listLimit += LIST_STEP; renderFocus(); break;
      case 'load-all': el.disabled = true; el.textContent = '불러오는 중…'; loadAll(); break;
      case 'zoom-row': { const r = S.rows[Number(el.dataset.row)]; if (r) zoomToRowRegion(r); break; }
      default: break;
    }
  }, true); // capture: Leaflet popups may stop click propagation

  // desktop collapse
  $('collapse').addEventListener('click', () => { $('app').classList.add('collapsed'); $('expand').hidden = false; map.invalidateSize(); });
  $('expand').addEventListener('click', () => { $('app').classList.remove('collapsed'); $('expand').hidden = true; map.invalidateSize(); });

  // first-visit banner
  if (!store.get('asbmap-banner-ok')) $('banner').hidden = false;
  $('bannerOk').addEventListener('click', () => { $('banner').hidden = true; store.set('asbmap-banner-ok', '1'); });

  // ---------- mobile bottom sheet ----------
  const sheet = $('panel');
  let sheetState = 'half';
  function setSheet(s) {
    sheetState = s;
    for (const x of ['peek', 'half', 'full']) sheet.classList.toggle(x, x === s);
  }
  if (MOBILE.matches) setSheet('peek');
  (function () {
    let startY = 0, startTop = 0, dragging = false, moved = 0;
    const grab = [$('handle'), sheet.querySelector('.title-row'), $('totals')];
    const onDown = (e) => {
      if (!MOBILE.matches || (e.target.closest && e.target.closest('input,button,a'))) return;
      dragging = true; moved = 0; startY = e.clientY;
      startTop = sheet.getBoundingClientRect().top - $('app').getBoundingClientRect().top;
      sheet.classList.add('dragging');
      try { e.currentTarget.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ }
    };
    const onMove = (e) => {
      if (!dragging) return;
      const dy = e.clientY - startY; moved = Math.max(moved, Math.abs(dy));
      const H = $('app').clientHeight;
      const top = Math.min(Math.max(startTop + dy, H * 0.08), H - 60);
      sheet.style.transform = `translateY(${top - H * 0.08}px)`;
    };
    const onUp = (e) => {
      if (!dragging) return;
      dragging = false; sheet.classList.remove('dragging');
      const H = $('app').clientHeight;
      const top = sheet.getBoundingClientRect().top - $('app').getBoundingClientRect().top;
      sheet.style.transform = '';
      if (moved < 6) { setSheet(sheetState === 'peek' ? 'half' : sheetState === 'half' ? 'full' : 'peek'); return; }
      const snaps = { full: H * 0.08, half: H * 0.08 + H * 0.92 * 0.45, peek: H - 150 };
      let best = 'half', bd = Infinity;
      for (const [k, v] of Object.entries(snaps)) { const d = Math.abs(v - top); if (d < bd) { bd = d; best = k; } }
      setSheet(best);
    };
    for (const g of grab) {
      if (!g) continue;
      g.addEventListener('pointerdown', onDown);
      g.addEventListener('pointermove', onMove);
      g.addEventListener('pointerup', onUp);
      g.addEventListener('pointercancel', onUp);
    }
    $('handle').addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setSheet(sheetState === 'full' ? 'peek' : sheetState === 'peek' ? 'half' : 'full'); } });
  })();

  // ---------- boot ----------
  function buildSidoCounts() {
    S.sidoCounts.clear();
    for (const s of (S.manifest && S.manifest.sido) || []) S.sidoCounts.set(String(s.code), Object.assign({}, s, { code: String(s.code) }));
    // sido only in summary or only in boundaries: aggregate / zero
    for (const e of Object.values(S.summary)) {
      const sc = String(e.sido || '');
      if (!sc || S.sidoCounts.has(sc) && !S.sidoCounts.get(sc)._agg) continue;
      const a = S.sidoCounts.get(sc) || { code: sc, name: sc, _agg: true, c2: 0, c1: 0, c0: 0, points: 0, nocoord: 0 };
      for (const k of ['c2', 'c1', 'c0', 'points', 'nocoord']) a[k] += e[k] || 0;
      S.sidoCounts.set(sc, a);
    }
    for (const f of (S.sidoGeo && S.sidoGeo.features) || []) {
      const code = String(f.properties.code);
      const ex = S.sidoCounts.get(code);
      if (!ex) S.sidoCounts.set(code, { code, name: f.properties.name, c2: 0, c1: 0, c0: 0, points: 0, nocoord: 0, _empty: true });
      else if (!ex.name || ex.name === code) ex.name = f.properties.name;
    }
  }
  function showError(msg) { const e = $('error'); e.innerHTML = msg; e.hidden = false; }

  Promise.all([getJSON('manifest.json'), getJSON('summary/sigungu.json'), getJSON('boundaries/sido.geojson')]).then(([man, sum, sidoGeo]) => {
    S.manifest = man || { totals: {}, sources: [], sido: [] };
    S.summary = (sum && typeof sum === 'object') ? sum : {};
    for (const k of Object.keys(S.summary)) if (S.summary[k] && S.summary[k].sido != null) S.summary[k].sido = String(S.summary[k].sido);
    S.sidoGeo = sidoGeo && Array.isArray(sidoGeo.features) ? sidoGeo : null;
    for (const s of S.manifest.sources || []) S.sources.set(String(s.id), s);
    buildSidoCounts();
    computeBins();
    if (!man) showError(`자료를 불러오지 못했습니다 (${esc(BASE)}manifest.json). 시험 자료는 <a href="?data=fixture">?data=fixture</a> 로 볼 수 있습니다.`);
    else if (!S.sidoGeo) showError('시·도 경계 파일을 불러오지 못해 지역 색을 그릴 수 없습니다.');
    if (S.sidoGeo) sidoLayer = makeLayer(S.sidoGeo, 'sido');
    renderStatic();
    S.styleKey = '';
    updateView();
    // sigungu boundaries: needed from zoom 8; fetch right away in the background
    return getJSON('boundaries/sigungu.geojson').then((g) => {
      if (!g || !Array.isArray(g.features)) return;
      S.sggGeo = g;
      sggLayer = makeLayer(g, 'sgg');
      S.styleKey = '';
      updateView();
    });
  });

  // debug handle for console / headless checks
  window.__asbmap = { S, map, Z, getJSON, loadPoints, loadNocoord };
})();
