const BOOKMAKERS = [
  { id: 'unibet',     name: 'Unibet',     color: '#1ea768' },
  { id: 'winamax',    name: 'Winamax',    color: '#e2001a' },
  { id: 'pmu',        name: 'PMU',        color: '#0e8f4f' },
  { id: 'betclic',    name: 'Betclic',    color: '#ff3b3b' },
  { id: 'bwin',       name: 'bwin',       color: '#ffcc00', ink: '#111' },
  { id: 'pokerstars', name: 'PokerStars', color: '#c8102e' },
];
const SPORTS = [
  { id: 'football', label: 'Football', icon: '⚽' },
  { id: 'basket',   label: 'Basket',   icon: '🏀' },
  { id: 'rugby',    label: 'Rugby',    icon: '🏉' },
  { id: 'cyclisme', label: 'Cyclisme', icon: '🚴' },
];
const SOURCE_NAMES = { theoddsapi: 'The Odds API', oddspapi: 'OddsPapi' };
const DEMO = new URLSearchParams(location.search).has('demo');
const POLL_MS = 2 * 60 * 1000;

const state = {
  data: null, error: null, tick: 0,
  sport: 'football', eventId: null, marketKey: null, line: null,
  books: new Set(BOOKMAKERS.map(b => b.id)),
  search: '', sort: 'best', stake: 10,
};
try { state.stake = Number(localStorage.getItem('stake')) || 10; } catch {}

const $ = id => document.getElementById(id);
const fmt = n => n.toFixed(2);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const dateFmt = new Intl.DateTimeFormat('fr-FR', { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
const dot = b => `<span class="dot" style="background:${b.color};color:${b.ink || '#fff'}">${b.name[0]}</span>`;
const activeBooks = () => BOOKMAKERS.filter(b => state.books.has(b.id));

function ago(iso) {
  if (!iso) return 'jamais';
  const min = Math.round((Date.now() - new Date(iso)) / 60000);
  if (min < 1) return "à l'instant";
  if (min < 60) return `il y a ${min} min`;
  const h = Math.round(min / 60);
  return h < 48 ? `il y a ${h} h` : `il y a ${Math.round(h / 24)} j`;
}

// ---------------------------------------------------------------- chargement
async function load() {
  try {
    if (DEMO) {
      state.data = buildDemoData(state.tick++);
    } else {
      const r = await fetch('odds.json?t=' + Date.now(), { cache: 'no-store' });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      state.data = await r.json();
    }
    state.error = null;
  } catch (e) {
    state.error = e.message;
  }
  ensureSelection();
  render();
}

const eventsOf = sport => (state.data?.events || []).filter(e => e.sport === sport);
const currentEvent = () => eventsOf(state.sport).find(e => e.id === state.eventId);
const currentMarket = () => currentEvent()?.markets.find(m => m.key === state.marketKey);

function sortedMarkets(ev) {
  const rank = m => (m.key === '1x2' || m.key === 'ml') ? 0 : m.complete ? 1 : 2;
  return [...ev.markets].sort((a, b) => rank(a) - rank(b));
}
function linesOf(m) {
  return [...new Set(m.selections.map(s => s.line).filter(l => l != null))].sort((a, b) => a - b);
}

// Garde la sélection courante si elle existe encore après un rafraîchissement
function ensureSelection() {
  if (!state.data) return;
  if (!eventsOf(state.sport).length) {
    const withEvents = SPORTS.find(s => eventsOf(s.id).length);
    if (withEvents && !state.userPickedSport) state.sport = withEvents.id;
  }
  const evs = eventsOf(state.sport);
  if (!evs.find(e => e.id === state.eventId)) state.eventId = evs[0]?.id ?? null;
  const ev = currentEvent();
  if (!ev) return;
  if (!ev.markets.find(m => m.key === state.marketKey)) state.marketKey = sortedMarkets(ev)[0]?.key;
  const m = currentMarket();
  const lines = linesOf(m);
  if (lines.length && !lines.includes(state.line)) {
    state.line = m.lineFmt === 'raw'  // plus/moins : ligne la plus jouée (2.5)
      ? lines.reduce((a, b) => Math.abs(b - 2.5) < Math.abs(a - 2.5) ? b : a)
      : lines[Math.max(0, Math.floor(lines.length / 2) - 1)];
  }
}

// ---------------------------------------------------------------- rendu
function buildRows(m) {
  const lines = linesOf(m);
  return m.selections
    .filter(s => !lines.length || s.line === state.line)
    .map(sel => {
      const vals = activeBooks().map(b => sel.odds[b.id]).filter(v => v != null);
      if (!vals.length) return null;
      return {
        sel, best: Math.max(...vals), worst: Math.min(...vals),
        avg: vals.reduce((a, b) => a + b, 0) / vals.length, count: vals.length,
      };
    })
    .filter(Boolean);
}

function render() {
  renderStatus();
  $('sports').innerHTML = SPORTS.map(s => {
    const n = eventsOf(s.id).length;
    return `<button class="${s.id === state.sport ? 'on' : ''}" data-sport="${s.id}">${s.icon} ${s.label}<span class="count">${n}</span></button>`;
  }).join('');

  const evs = eventsOf(state.sport);
  $('events').innerHTML = evs.map(e => `
    <button class="event ${e.id === state.eventId ? 'on' : ''}" data-event="${esc(e.id)}">
      <div class="comp"><span>${esc(e.competition)}</span><span>${dateFmt.format(new Date(e.date))}</span></div>
      <div class="teams">${e.sport === 'cyclisme'
        ? `<span>${esc(e.home)}</span><span class="sub">${esc(e.away)}</span>`
        : `<span>${esc(e.home)}</span><span>${esc(e.away)}</span>`}</div>
    </button>`).join('');

  const ev = currentEvent();
  if (state.error && !state.data) {
    $('main').innerHTML = `<div class="empty">Impossible de charger les cotes (${esc(state.error)}).<br><a href="?demo=1">Voir la démo</a></div>`;
    return;
  }
  if (!ev) {
    const sport = SPORTS.find(s => s.id === state.sport);
    $('main').innerHTML = `<div class="empty"><div style="font-size:32px">${sport.icon}</div>
      Aucune cote ${esc(sport.label.toLowerCase())} disponible pour le moment chez nos sources.<br>
      <span class="small">Les données arrivent à chaque passage de la mise à jour automatique.</span></div>`;
    return;
  }

  const m = currentMarket();
  const lines = linesOf(m);
  let rows = buildRows(m);
  const q = state.search.trim().toLowerCase();
  if (q) rows = rows.filter(r => (r.sel.name + ' ' + (r.sel.team || '')).toLowerCase().includes(q));
  if (m.complete && !q) rows.sort((a, b) => (a.sel.order ?? 9) - (b.sel.order ?? 9));
  else if (state.sort === 'best') rows.sort((a, b) => a.best - b.best);
  else if (state.sort === 'edge') rows.sort((a, b) => b.best / b.avg - a.best / a.avg);
  else if (state.sort === 'name') rows.sort((a, b) => a.sel.name.localeCompare(b.sel.name));
  else if (state.sort === 'team') rows.sort((a, b) => (a.sel.team || '').localeCompare(b.sel.team || '') || a.best - b.best);

  const books = activeBooks();
  const isRace = ev.sport === 'cyclisme';
  const title = isRace ? esc(ev.home) : `${esc(ev.home)} <span class="vs">vs</span> ${esc(ev.away)}`;

  $('main').innerHTML = `
    <div class="ev-head">
      <div>
        <div class="comp">${esc(ev.competition)}</div>
        <h1>${title}</h1>
        ${isRace ? `<div class="when">${esc(ev.away)}</div>` : ''}
      </div>
      <div class="when">${dateFmt.format(new Date(ev.date))}</div>
    </div>

    <div class="markets">${sortedMarkets(ev).map(x =>
      `<button class="${x.key === m.key ? 'on' : ''}" data-market="${esc(x.key)}">${esc(x.label)}</button>`).join('')}</div>
    ${m.hint ? `<div class="hint">${esc(m.hint)}</div>` : ''}

    <div class="controls">
      ${lines.length ? `<div class="chips">${lines.map(l =>
        `<button class="chip mono ${l === state.line ? 'on' : ''}" data-line="${l}">${l}${m.lineFmt === 'raw' ? '' : '+'}</button>`).join('')}</div>` : ''}
      ${m.selections.length > 4 ? `
        <input class="search" id="search" placeholder="Rechercher un joueur, une équipe…" value="${esc(state.search)}">
        <select id="sort" aria-label="Tri">
          <option value="best" ${state.sort === 'best' ? 'selected' : ''}>Favoris d'abord</option>
          <option value="edge" ${state.sort === 'edge' ? 'selected' : ''}>Plus gros écart vs moyenne</option>
          <option value="team" ${state.sort === 'team' ? 'selected' : ''}>Par équipe</option>
          <option value="name" ${state.sort === 'name' ? 'selected' : ''}>Alphabétique</option>
        </select>` : ''}
    </div>

    <div class="books">${BOOKMAKERS.map(b =>
      `<button class="book-toggle ${state.books.has(b.id) ? '' : 'off'}" data-book="${b.id}">${dot(b)}${b.name}</button>`).join('')}</div>

    <div class="table-wrap">${rows.length ? renderTable(rows, books, m, lines) : `<div class="empty">Aucune cote pour cette sélection.</div>`}</div>
    ${ev.updated ? `<div class="small" style="margin-top:8px">Dernières cotes reçues : ${BOOKMAKERS.filter(b => ev.updated[b.id]).map(b => `${b.name} ${ago(ev.updated[b.id])}`).join(' · ')}</div>` : ''}
  `;
}

function renderTable(rows, books, m, lines) {
  const cell = (r, b) => {
    const v = r.sel.odds[b.id];
    if (v == null) return `<td><span class="na">—</span></td>`;
    const p = r.sel.prev?.[b.id];
    const arr = p != null && p !== v ? `<span class="arr ${v > p ? 'up' : 'down'}" title="avant : ${fmt(p)}">${v > p ? '▲' : '▼'}</span>` : '';
    const cls = v === r.best && r.count > 1 && r.best !== r.worst ? 'best' : v === r.worst && r.count > 2 && r.best !== r.worst ? 'worst' : '';
    return `<td><span class="odd mono ${cls}" title="${b.name} · gain ${fmt(v * state.stake)} €">${fmt(v)}${arr}</span></td>`;
  };
  const bestCell = r => {
    const who = books.filter(b => r.sel.odds[b.id] === r.best).map(b => b.name).join(', ');
    const edge = r.count > 1 ? (r.best / r.avg - 1) * 100 : 0;
    return `<td class="bestcol">
      <div class="v mono">${fmt(r.best)}</div>
      <div class="s">${esc(who)}</div>
      <div class="s">Gain ${fmt(r.best * state.stake)} € ${edge >= 0.5 ? `<span class="edge">+${edge.toFixed(1)}%</span>` : ''}</div>
    </td>`;
  };

  let foot = '';
  if (m.complete && rows.length > 1) {
    const trj = b => rows.every(r => r.sel.odds[b.id] != null) ? 1 / rows.reduce((s, r) => s + 1 / r.sel.odds[b.id], 0) : null;
    const bestTrj = 1 / rows.reduce((s, r) => s + 1 / r.best, 0);
    const top = Math.max(...books.map(trj).filter(Boolean));
    foot = `<tfoot><tr>
      <td class="sel">TRJ (retour joueur)</td>
      ${books.map(b => { const t = trj(b); return `<td class="mono ${t === top ? 'trj-good' : ''}">${t ? (t * 100).toFixed(1) + '%' : '—'}</td>`; }).join('')}
      <td class="bestcol mono">${(bestTrj * 100).toFixed(1)}% ${bestTrj > 1 ? '<span class="surebet">SUREBET</span>' : ''}</td>
    </tr></tfoot>`;
  }

  return `<table>
    <thead><tr>
      <th class="sel">Sélection${lines.length && m.lineFmt !== 'raw' ? ` · ${state.line}+` : ''}</th>
      ${books.map(b => `<th class="book">${dot(b)}${b.name}</th>`).join('')}
      <th class="bestcol" style="text-align:left">Meilleure cote</th>
    </tr></thead>
    <tbody>${rows.map(r => `<tr>
      <td class="sel"><div class="name">${esc(r.sel.name)}</div>${r.sel.team ? `<div class="team">${esc(r.sel.team)}</div>` : ''}</td>
      ${books.map(b => cell(r, b)).join('')}
      ${bestCell(r)}
    </tr>`).join('')}</tbody>
    ${foot}
  </table>`;
}

function renderStatus() {
  const d = state.data;
  if (!d) { $('status').innerHTML = ''; return; }
  if (d.demo) {
    $('status').innerHTML = `<div class="status demo">⚠️ Mode démo : cotes fictives. <a href="./">Voir les vraies cotes</a></div>`;
    return;
  }
  const cov = d.coverage || {};
  const sources = Object.entries(d.sources || {}).map(([k, s]) => {
    const ok = s.status && s.status.startsWith('ok');
    return `<span class="src ${ok ? 'ok' : 'ko'}" title="${esc(s.status || '')}">${SOURCE_NAMES[k] || k} · ${ok ? ago(s.lastRun) : esc(s.status || '—')}</span>`;
  }).join('');
  const books = BOOKMAKERS.map(b => {
    const c = cov[b.id];
    return `<span class="cov ${c && c.odds ? '' : 'none'}" title="${c ? c.events + ' matchs, ' + c.odds + ' cotes' : 'non couvert'}">${dot(b)}${c && c.odds ? c.events : '0'}</span>`;
  }).join('');
  $('status').innerHTML = `<div class="status">
    <span><b>Cotes ${ago(d.generatedAt)}</b></span>${sources}
    <span class="covs" title="Nombre de matchs couverts par bookmaker">${books}</span>
  </div>`;
}

// ---------------------------------------------------------------- interactions
document.addEventListener('click', e => {
  const t = e.target.closest('button');
  if (!t) return;
  if (t.dataset.sport) {
    state.sport = t.dataset.sport; state.userPickedSport = true; state.eventId = null; state.search = '';
  } else if (t.dataset.event) {
    state.eventId = t.dataset.event; state.marketKey = null; state.search = '';
  } else if (t.dataset.market) {
    state.marketKey = t.dataset.market; state.line = null;
  } else if (t.dataset.line) {
    state.line = Number(t.dataset.line);
  } else if (t.dataset.book) {
    const id = t.dataset.book;
    if (state.books.has(id) && state.books.size > 1) state.books.delete(id); else state.books.add(id);
  } else return;
  ensureSelection();
  render();
});
document.addEventListener('input', e => {
  if (e.target.id === 'search') {
    state.search = e.target.value;
    const pos = e.target.selectionStart;
    render();
    const s = $('search'); s.focus(); s.setSelectionRange(pos, pos);
  }
  if (e.target.id === 'stake') {
    state.stake = Math.max(1, Number(e.target.value) || 1);
    try { localStorage.setItem('stake', state.stake); } catch {}
    render();
  }
});
document.addEventListener('change', e => { if (e.target.id === 'sort') { state.sort = e.target.value; render(); } });
$('refresh').addEventListener('click', load);
document.addEventListener('visibilitychange', () => { if (!document.hidden) load(); });
setInterval(() => { if (!document.hidden) load(); }, POLL_MS);

$('stake').value = state.stake;
load();
