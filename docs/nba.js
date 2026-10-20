const BOOKS = {
  unibet:     { name: 'Unibet',     color: '#1ea768' },
  winamax:    { name: 'Winamax',    color: '#e2001a' },
  pmu:        { name: 'PMU',        color: '#0e8f4f' },
  betclic:    { name: 'Betclic',    color: '#ff3b3b' },
  bwin:       { name: 'bwin',       color: '#ffcc00', ink: '#111' },
  pokerstars: { name: 'PokerStars', color: '#b3001b' },
};
const STAT_ORDER = ['pts', 'reb', 'ast', '3pt', 'pra', 'pr', 'pa', 'ra', 'stl', 'blk', 'sb', 'tov', 'dd', 'td'];
const YES_STATS = new Set(['dd', 'td']);  // marchés oui/non : pas de palier
const DEMO = new URLSearchParams(location.search).has('demo');
const POLL_MS = 60 * 1000;
// Cotes publiées par l'iMac (branche « data ») ; à défaut, celles de GitHub Actions (OddsPapi)
const DATA_URLS = ['https://raw.githubusercontent.com/23Louis23/comparateur/data/nba.json', 'nba.json'];

const state = { data: null, error: null, gameId: null, stat: null, side: 'all', search: '', off: new Set() };
try { JSON.parse(localStorage.getItem('off') || '[]').forEach(b => state.off.add(b)); } catch {}

const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmt = n => n.toFixed(2);
const timeFmt = new Intl.DateTimeFormat('fr-FR', { hour: '2-digit', minute: '2-digit' });
const dayFmt = new Intl.DateTimeFormat('fr-FR', { weekday: 'long', day: 'numeric', month: 'long' });
const bk = id => { const b = BOOKS[id] || { name: id, color: '#555' }; return `<span class="bk" style="background:${b.color};color:${b.ink || '#fff'}">${esc(b.name[0])}</span>`; };
const badge = t => `<span class="badge" style="background:linear-gradient(135deg,${t.c1} 55%,${t.c2})">${esc(t.abbr)}</span>`;
const initials = n => String(n || '?').split(/\s+/).map(w => w[0]).filter(Boolean).slice(0, 2).join('').toUpperCase();

function ago(iso) {
  if (!iso) return 'jamais';
  const min = Math.round((Date.now() - new Date(iso)) / 60000);
  if (min < 1) return "à l'instant";
  if (min < 60) return `il y a ${min} min`;
  const h = Math.round(min / 60);
  return h < 48 ? `il y a ${h} h` : `il y a ${Math.round(h / 24)} j`;
}
function dayLabel(d) {
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const diff = Math.round((new Date(d).setHours(0, 0, 0, 0) - today) / 864e5);
  return diff === 0 ? "Aujourd'hui" : diff === 1 ? 'Demain' : dayFmt.format(d);
}

// ------------------------------------------------------------ données
async function load() {
  try {
    if (DEMO) state.data = buildNbaDemo();
    else {
      let last;
      for (const url of DATA_URLS) {
        try {
          const r = await fetch(url + '?t=' + Date.now(), { cache: 'no-store' });
          if (!r.ok) throw new Error('HTTP ' + r.status);
          const d = await r.json();
          // la copie de l'iMac n'est retenue que si elle est récente (iMac éteint → repli)
          if (url !== 'nba.json' && Date.now() - new Date(d.generatedAt) > 3 * 3600e3) throw new Error('données anciennes');
          state.data = d; last = null; break;
        } catch (e) { last = e; }
      }
      if (last) throw last;
    }
    state.error = null;
  } catch (e) { state.error = e.message; }
  const games = state.data?.games || [];
  if (!games.find(g => g.id === state.gameId)) {
    const withProps = games.find(g => g.players.length);
    state.gameId = (withProps || games[0])?.id ?? null;
  }
  render();
}

const game = () => state.data?.games.find(g => g.id === state.gameId);
const statsOf = g => STAT_ORDER.filter(s => g.players.some(p => p.stats[s] && Object.keys(p.stats[s]).length));
const teamOf = (g, p) => p.team === 'home' ? findTeam(g.home) : p.team === 'away' ? findTeam(g.away) : null;

// ------------------------------------------------------------ rendu
function render() {
  renderStatus();
  $('demo').innerHTML = DEMO ? `<div class="demo-flag">⚠️ Mode démo : joueurs réels, cotes fictives. <a href="./">Voir les vraies cotes</a></div>` : '';
  const games = state.data?.games || [];

  // bandeau des matchs, groupés par jour
  let html = '', lastDay = '';
  for (const g of games) {
    const d = new Date(g.date), day = dayLabel(d), h = findTeam(g.home), a = findTeam(g.away);
    if (day !== lastDay) { html += `<div class="day">${esc(day)}</div>`; lastDay = day; }
    const n = g.players.length;
    html += `<button class="game ${g.id === state.gameId ? 'on' : ''}" data-game="${esc(g.id)}">
      <div class="time"><b>${timeFmt.format(d)}</b><span>${d < new Date() ? '🔴 En cours' : ''}</span></div>
      <div class="team">${badge(a)}${esc(a.nick)}</div>
      <div class="team">${badge(h)}${esc(h.nick)}</div>
      <div class="count">${n ? `<b>${n}</b> joueurs avec cotes` : 'Props pas encore publiées'}</div>
    </button>`;
  }
  $('games').innerHTML = html;

  const g = game();
  if (!g) {
    $('match').innerHTML = `<div class="empty"><div class="big">🏀</div>${state.error
      ? `Impossible de charger les cotes (${esc(state.error)}).`
      : 'Aucun match NBA à venir dans les données pour le moment.'}<br><a href="?demo=1">Voir la démo</a></div>`;
    return;
  }
  const stats = statsOf(g);
  if (!stats.includes(state.stat)) state.stat = stats[0] || null;
  $('match').innerHTML = renderHero(g) + (stats.length ? renderControls(g, stats) + renderPlayers(g) : `
    <div class="empty"><div class="big">⏳</div>Les bookmakers n'ont pas encore publié les props de ce match.<br>
    Elles apparaissent en général la veille ou le jour du match.<br><a href="?demo=1">Voir à quoi ça ressemblera</a></div>`);
}

function renderHero(g) {
  const h = findTeam(g.home), a = findTeam(g.away), d = new Date(g.date);
  const books = Object.keys(g.updated || {}).filter(b => BOOKS[b]);
  const cotes = g.players.reduce((s, p) => s + Object.values(p.stats).reduce((t, cuts) => t + Object.values(cuts).reduce((u, c) => u + Object.keys(c.o).length, 0), 0), 0);
  return `<div class="hero">
    <div class="hero-bg" style="background:linear-gradient(100deg,${a.c1} 0%,${a.c1} 38%,${h.c1} 62%,${h.c1} 100%)"></div>
    <div class="hero-in">
      <div class="side">${badge(a)}<div><div class="city">${esc(a.city)}</div><div class="nick">${esc(a.nick)}</div></div></div>
      <div class="vs"><div class="t num">${timeFmt.format(d)}</div><div class="d">${esc(dayLabel(d))}</div></div>
      <div class="side right">${badge(h)}<div><div class="city">${esc(h.city)}</div><div class="nick">${esc(h.nick)}</div></div></div>
    </div>
    <div class="hero-meta"><span><b>${g.players.length}</b> joueurs</span><span><b>${cotes}</b> cotes</span>
      ${books.length ? `<span>Mis à jour : ${books.map(b => `${BOOKS[b].name} ${ago(g.updated[b])}`).join(' · ')}</span>` : ''}</div>
  </div>`;
}

function renderControls(g, stats) {
  const count = s => g.players.filter(p => p.stats[s] && Object.keys(p.stats[s]).length).length;
  const label = s => (state.data.stats || {})[s] || s;
  const h = findTeam(g.home), a = findTeam(g.away);
  const hasTeams = g.players.some(p => p.team);
  const present = new Set(g.players.flatMap(p => Object.values(p.stats).flatMap(c => Object.values(c).flatMap(x => Object.keys(x.o)))));
  return `
    <div class="tabs">${stats.map(s => `<button class="tab ${s === state.stat ? 'on' : ''}" data-stat="${s}">${esc(label(s))}<span class="n">${count(s)}</span></button>`).join('')}</div>
    <div class="controls">
      ${hasTeams ? `<div class="seg">
        <button class="${state.side === 'all' ? 'on' : ''}" data-side="all">Tous</button>
        <button class="${state.side === 'away' ? 'on' : ''}" data-side="away">${badge(a)}${esc(a.abbr)}</button>
        <button class="${state.side === 'home' ? 'on' : ''}" data-side="home">${badge(h)}${esc(h.abbr)}</button>
      </div>` : ''}
      <input class="search" id="search" placeholder="Rechercher un joueur…" value="${esc(state.search)}" autocomplete="off">
    </div>
    <div class="books">${Object.keys(BOOKS).map(b => present.has(b)
      ? `<button class="book ${state.off.has(b) ? 'off' : ''}" data-book="${b}">${bk(b)}${BOOKS[b].name}</button>`
      : `<span class="book na" title="Pas de cotes pour ce match">${bk(b)}${BOOKS[b].name}</span>`).join('')}</div>`;
}

function renderPlayers(g) {
  const q = state.search.trim().toLowerCase();
  const yes = YES_STATS.has(state.stat);
  const rows = g.players
    .filter(p => p.stats[state.stat] && Object.keys(p.stats[state.stat]).length)
    .filter(p => state.side === 'all' || p.team === state.side)
    .filter(p => !q || p.name.toLowerCase().includes(q))
    .map(p => {
      const cuts = Object.entries(p.stats[state.stat])
        .map(([cut, cell]) => ({ cut: Number(cut), cell, books: Object.entries(cell.o).filter(([b]) => !state.off.has(b)).sort((x, y) => y[1] - x[1]) }))
        .filter(c => c.books.length)
        .sort((x, y) => x.cut - y.cut);
      // tri : joueurs dont le palier « principal » (cote la plus proche de 1.90) est le plus haut d'abord
      const main = cuts.reduce((best, c) => !best || Math.abs(c.books[0][1] - 1.9) < Math.abs(best.books[0][1] - 1.9) ? c : best, null);
      return { p, cuts, rank: main ? main.cut : 0 };
    })
    .filter(r => r.cuts.length)
    .sort((x, y) => y.rank - x.rank || x.p.name.localeCompare(y.p.name));

  if (!rows.length) return `<div class="empty">Aucun joueur pour ce filtre.</div>`;
  return `<div class="players">${rows.map(({ p, cuts }) => {
    const t = teamOf(g, p);
    return `<div class="prow" style="${t ? `--tc:${t.c1}` : ''}">
      <div class="pid"><div class="avatar">${esc(initials(p.name))}</div>
        <div style="min-width:0"><div class="pname">${esc(p.name)}</div>${t ? `<div class="pteam">${esc(t.city)} ${esc(t.nick)}</div>` : ''}</div></div>
      <div class="cuts">${cuts.map(c => renderCut(c, yes)).join('')}</div>
    </div>`;
  }).join('')}</div>`;
}

function renderCut({ cut, cell, books }, yes) {
  const best = books[0][1], worst = books[books.length - 1][1];
  const tie = books.length > 1 && best === worst;  // même cote partout : rien à surligner
  const avg = books.reduce((s, [, o]) => s + o, 0) / books.length;
  const edge = books.length > 1 ? (best / avg - 1) * 100 : 0;
  return `<div class="cut ${books.length === 1 ? 'solo' : ''}">
    <div class="cut-h"><span class="v num">${yes ? 'Oui' : `${cut}<small>+</small>`}</span>${edge >= 4 ? `<span class="edge">+${edge.toFixed(0)}%</span>` : ''}</div>
    ${books.map(([b, o]) => {
      const prev = cell.p?.[b];
      const arr = prev != null && prev !== o ? `<span class="arr ${o > prev ? 'up' : 'down'}" title="avant : ${fmt(prev)}">${o > prev ? '▲' : '▼'}</span>` : '';
      return `<div class="line ${o === best && !tie ? 'best' : ''}">${bk(b)}<span class="bn">${esc(BOOKS[b]?.name || b)}</span><span class="o num">${fmt(o)}${arr}</span></div>`;
    }).join('')}
  </div>`;
}

function renderStatus() {
  const d = state.data;
  if (!d) { $('status').innerHTML = ''; return; }
  const ok = d.demo || String(d.source?.status || '').startsWith('ok');
  $('status').innerHTML = `<span class="dot-live ${ok ? '' : 'ko'}"></span>
    <span class="txt">${d.demo ? 'Démo' : `Cotes ${ago(d.source?.lastRun || d.generatedAt)}`}</span>
    <button class="icon-btn" id="refresh" title="Recharger">↻</button>`;
}

// ------------------------------------------------------------ interactions
document.addEventListener('click', e => {
  const t = e.target.closest('button');
  if (!t) return;
  if (t.id === 'refresh') return load();
  if (t.dataset.game) { state.gameId = t.dataset.game; state.search = ''; state.side = 'all'; }
  else if (t.dataset.stat) state.stat = t.dataset.stat;
  else if (t.dataset.side) state.side = t.dataset.side;
  else if (t.dataset.book) {
    const b = t.dataset.book;
    state.off.has(b) ? state.off.delete(b) : state.off.add(b);
    try { localStorage.setItem('off', JSON.stringify([...state.off])); } catch {}
  } else return;
  render();
});
document.addEventListener('input', e => {
  if (e.target.id !== 'search') return;
  state.search = e.target.value;
  const pos = e.target.selectionStart;
  render();
  const s = $('search'); s.focus(); s.setSelectionRange(pos, pos);
});
document.addEventListener('visibilitychange', () => { if (!document.hidden && !DEMO) load(); });
setInterval(() => { if (!document.hidden && !DEMO) load(); }, POLL_MS);
load();
