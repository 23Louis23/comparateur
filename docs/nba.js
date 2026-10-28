const BOOKS = {
  unibet:     { name: 'Unibet',     color: '#1ea768' },
  winamax:    { name: 'Winamax',    color: '#e2001a' },
  pmu:        { name: 'PMU',        color: '#0e8f4f' },
  betclic:    { name: 'Betclic',    color: '#ff3b3b' },
  bwin:       { name: 'bwin',       color: '#ffcc00', ink: '#111' },
  pokerstars: { name: 'PokerStars', color: '#b3001b' },
  bet365:     { name: 'bet365',     color: '#027b5b', ink: '#ffdf1b' },
};
const STAT_ORDER = ['pts', 'reb', 'ast', '3pt', 'pra', 'pr', 'pa', 'ra', 'stl', 'blk', 'sb', 'tov', 'dd', 'td'];
const YES_STATS = new Set(['dd', 'td']);  // marchés oui/non : pas de palier
const DEMO = new URLSearchParams(location.search).has('demo');
const POLL_MS = 75 * 1000;  // l'API GitHub autorise 60 requêtes/heure sans compte : une toutes les 75 s
// Cotes publiées par l'iMac (branche « data ») ; à défaut, celles de GitHub Actions (OddsPapi)
const REPO = '23Louis23/comparateur';
const RAW_DATA = `https://raw.githubusercontent.com/${REPO}/data/nba.json`;  // mis en cache ~5 min par GitHub
let lastSha = null;
let apiPausedUntil = 0;  // quota de l'API GitHub épuisé (60/h par connexion) : on attend sa remise à zéro

// Adresse de la dernière publication de l'iMac : figée par son identifiant, donc jamais servie en cache.
async function latestDataUrl() {
  if (Date.now() >= apiPausedUntil) {
    try {
      const r = await fetch(`https://api.github.com/repos/${REPO}/commits/data`, { headers: { Accept: 'application/vnd.github.sha' }, cache: 'no-store' });
      if (r.ok) { const sha = (await r.text()).trim(); if (/^[0-9a-f]{40}$/.test(sha)) return { url: `https://raw.githubusercontent.com/${REPO}/${sha}/nba.json`, sha }; }
      if (r.status === 403 || r.status === 429) {
        const reset = Number(r.headers.get('X-RateLimit-Reset')) * 1000;
        apiPausedUntil = reset > Date.now() ? reset : Date.now() + 15 * 60e3;
      }
    } catch {}
  }
  return { url: RAW_DATA + '?t=' + Date.now(), sha: null };  // repli : adresse mise en cache ~5 min
}

const state = { data: null, error: null, gameId: null, playerId: null, search: '', off: new Set(), view: 'joueur', focusStat: null,
  page: 'matchs', gapSport: 'nba', openGaps: new Set() };
try { state.view = localStorage.getItem('view') || 'joueur'; } catch {}
try { state.page = localStorage.getItem('page') || 'matchs'; state.gapSport = localStorage.getItem('gapSport') || 'nba'; } catch {}
if (new URLSearchParams(location.search).get('page') === 'ecarts') state.page = 'ecarts';
const qsView = new URLSearchParams(location.search).get('vue');
if (qsView === 'tableau' || qsView === 'joueur') state.view = qsView;
try { JSON.parse(localStorage.getItem('off') || '[]').forEach(b => state.off.add(b)); } catch {}

// Dernière position (match, joueur, défilement) : retrouvée si le navigateur recharge la page au retour
// sur l'onglet (fréquent sur iPhone), seulement si elle date de moins de 30 min.
const SPOT_KEY = 'spot';
let pendingSpot = null;
try {
  const spot = JSON.parse(localStorage.getItem(SPOT_KEY) || 'null');
  if (spot && Date.now() - spot.at < 30 * 60e3) { state.gameId = spot.gameId; state.playerId = spot.playerId; pendingSpot = spot.scroll; }
} catch {}
if ('scrollRestoration' in history) history.scrollRestoration = 'manual';

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
      const latest = await latestDataUrl();
      if (latest.sha && latest.sha === lastSha && state.data) { render(); return; }  // rien de neuf depuis le dernier passage
      let last;
      for (const url of [latest.url, 'nba.json?t=' + Date.now()]) {
        try {
          const r = await fetch(url, { cache: 'no-store' });
          if (!r.ok) throw new Error('HTTP ' + r.status);
          const d = await r.json();
          // la copie de l'iMac n'est retenue que si elle est récente (iMac éteint → repli sur OddsPapi)
          if (!url.startsWith('nba.json') && Date.now() - new Date(d.generatedAt) > 3 * 3600e3) throw new Error('données anciennes');
          state.data = d; last = null;
          if (url === latest.url) lastSha = latest.sha;
          break;
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
const teamOf = (g, p) => p.team === 'home' ? findTeam(g.home) : p.team === 'away' ? findTeam(g.away) : null;
const label = s => (state.data?.stats || {})[s] || s;
const on = b => !state.off.has(b);

// Ligne « à l'équilibre » d'un bookmaker : celle où Plus et Moins sont les plus proches
function balancedLines(lines) {
  const byBook = {};
  for (const [line, sides] of Object.entries(lines || {})) {
    for (const b of Object.keys(sides.over || {})) {
      const o = sides.over[b], u = sides.under?.[b];
      if (u == null || !on(b)) continue;
      const gap = Math.abs(o - u);
      if (!byBook[b] || gap < byBook[b].gap) byBook[b] = { line, gap };
    }
  }
  const main = new Set(Object.values(byBook).map(x => x.line));
  const all = Object.keys(lines || {}).filter(l => Object.keys(lines[l].over || {}).concat(Object.keys(lines[l].under || {})).some(on));
  if (!main.size && all.length) main.add(all[0]);  // un seul côté publié : on montre quand même
  const sort = arr => arr.sort((x, y) => Number(x) - Number(y));
  return { main: sort([...main]), others: sort(all.filter(l => !main.has(l))) };
}
// Valeur de référence d'un joueur (pour le classer) : sa ligne de points à l'équilibre
function rankOf(p) {
  const { main } = balancedLines(p.lines?.pts);
  if (main.length) return Number(main[0]);
  const cuts = Object.entries(p.stats?.pts || {}).map(([c, cell]) => [Number(c), Math.max(...Object.values(cell.o))]);
  const near = cuts.reduce((best, c) => !best || Math.abs(c[1] - 1.9) < Math.abs(best[1] - 1.9) ? c : best, null);
  return near ? near[0] : 0;
}
const statsOfPlayer = p => STAT_ORDER.filter(s => (p.stats?.[s] && Object.keys(p.stats[s]).length) || (p.lines?.[s] && Object.keys(p.lines[s]).length));


// ------------------------------------------------------------ compositions (RotoWire)
const INJ = {  // statut RotoWire → libellé, couleur
  Out: ['Absent', 'out'], OFS: ['Absent (saison)', 'out'], Doubt: ['Douteux', 'doubt'],
  Ques: ['Incertain', 'ques'], GTD: ['Incertain', 'ques'], Prob: ['Probable', 'prob'],
};
const injTag = (inj, small) => inj ? `<span class="inj inj-${(INJ[inj] || ['', 'ques'])[1]}${small ? ' sm' : ''}">${esc((INJ[inj] || [inj])[0])}</span>` : '';
const normName = n => String(n || '').normalize('NFKD').replace(/[̀-ͯ]/g, '').toLowerCase()
  .replace(/[^a-z ]+/g, ' ').replace(/\b(jr|sr|ii|iii|iv)\b/g, ' ').replace(/\s+/g, ' ').trim();
// Cote américaine (« ORL -132 ») → cote décimale (« ORL 1,76 »)
function usToDec(v) {
  const m = String(v || '').match(/^(\S+)\s+([+-]\d+)$/);
  if (!m) return v || '–';
  const us = Number(m[2]), dec = us < 0 ? 1 + 100 / -us : 1 + us / 100;
  return `${m[1]} ${dec.toFixed(2).replace('.', ',')}`;
}

function renderLineup(g) {
  const lu = g.lineup;
  if (!lu) return '';
  const ids = new Set(g.players.map(p => p.id));
  const name = pl => {
    const id = normName(pl.name);
    return ids.has(id) ? `<button class="lu-name" data-player="${esc(id)}" data-lu="1">${esc(pl.name)}</button>` : `<span class="lu-name">${esc(pl.name)}</span>`;
  };
  const dot = pct => pct == null ? '' : `<i class="lu-dot p${pct}" title="Probabilité de jouer : ${pct} %"></i>`;
  const team = (side, t) => {
    const starters = new Set(side.starters.map(pl => normName(pl.name)));
    side = { ...side, out: side.out.filter(pl => !starters.has(normName(pl.name))) };  // titulaire incertain : badge dans le cinq
    return `<div class="lu-team">
      <div class="lu-th">${badge(t)}<div><div class="lu-nick">${esc(t.nick)}</div><div class="small">${esc(side.record || '')}</div></div>
        <span class="lu-status ${side.status}">${side.status === 'confirmed' ? 'Composition confirmée' : 'Composition probable'}</span></div>
      <ol class="lu-list">${side.starters.map(pl => `<li><span class="lu-pos">${esc(pl.pos)}</span>${dot(pl.pct)}${name(pl)}${injTag(pl.inj, true)}</li>`).join('')}</ol>
      ${side.out.filter(pl => pl.inj !== 'OFS').length ? `<div class="sub">Peuvent manquer le match</div>
        <ul class="lu-list out">${side.out.filter(pl => pl.inj !== 'OFS').map(pl => `<li><span class="lu-pos">${esc(pl.pos)}</span>${name(pl)}${injTag(pl.inj, true)}</li>`).join('')}</ul>` : ''}
      ${side.out.some(pl => pl.inj === 'OFS') ? `<div class="small lu-ofs">Absents pour la saison : ${side.out.filter(pl => pl.inj === 'OFS').map(pl => esc(pl.name)).join(', ')}</div>` : ''}
    </div>`;
  };
  return `<section class="lu">
    <div class="lu-head"><h3>Compositions</h3><span class="small">Source : <a href="https://www.rotowire.com/basketball/nba-lineups.php" target="_blank" rel="noopener">RotoWire</a></span></div>
    <div class="lu-teams">${team(lu.away, findTeam(g.away))}${team(lu.home, findTeam(g.home))}</div>
    <div class="lu-foot">
      ${lu.line ? `<span><b>Vainqueur</b> ${esc(usToDec(lu.line))}</span>` : ''}
      ${lu.spread ? `<span><b>Handicap</b> ${esc(lu.spread.replace('.', ','))}</span>` : ''}
      ${lu.total ? `<span><b>Total</b> ${esc(lu.total.replace(' Pts', ' pts').replace('.', ','))}</span>` : ''}
      ${lu.refs?.length ? `<span><b>Arbitres</b> ${lu.refs.map(esc).join(', ')}</span>` : ''}
    </div>
  </section>`;
}

// ------------------------------------------------------------ rendu
// Zones qui défilent : bandeau des matchs, tableau, liste des joueurs, paliers de la fiche joueur
const SCROLLERS = '#games, .tbl-wrap, .picker, .cuts';
const viewKey = () => state.page === 'ecarts' ? `ecarts|${state.gapSport}` : `${state.gameId}|${state.view}|${state.view === 'joueur' ? state.playerId : ''}`;
function saveScroll() {
  return { key: viewKey(), y: window.scrollY, els: [...document.querySelectorAll(SCROLLERS)].map(el => [el.scrollLeft, el.scrollTop]) };
}
// Même match, même vue : on remet chaque zone où elle était ; sinon seul le bandeau des matchs garde sa place
function restoreScroll(saved) {
  if (!saved) return;
  const els = [...document.querySelectorAll(SCROLLERS)];
  (saved.key === viewKey() ? els : els.slice(0, 1)).forEach((el, i) => { if (saved.els[i]) [el.scrollLeft, el.scrollTop] = saved.els[i]; });
  if (saved.key === viewKey()) window.scrollTo(0, saved.y);
}
function rememberSpot() {
  try { localStorage.setItem(SPOT_KEY, JSON.stringify({ at: Date.now(), gameId: state.gameId, playerId: state.playerId, scroll: saveScroll() })); } catch {}
}

function render() {
  const saved = pendingSpot && state.data ? pendingSpot : saveScroll();
  if (state.data) pendingSpot = null;
  draw();
  restoreScroll(saved);
}

function draw() {
  renderStatus();
  renderPages();
  $('games').hidden = state.page === 'ecarts';
  if (state.page === 'ecarts') { $('match').innerHTML = renderGaps(); return; }
  $('demo').innerHTML = DEMO ? `<div class="demo-flag">⚠️ Mode démo : joueurs réels, cotes fictives. <a href="./">Voir les vraies cotes</a></div>` : '';
  const games = state.data?.games || [];

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
  if (!g.players.length) {
    $('match').innerHTML = renderHero(g) + renderLineup(g) + `<div class="empty"><div class="big">⏳</div>Les bookmakers n'ont pas encore publié les props de ce match.<br>
      Elles apparaissent en général la veille ou le jour du match.<br><a href="?demo=1">Voir à quoi ça ressemblera</a></div>`;
    return;
  }
  const players = [...g.players].sort((x, y) => rankOf(y) - rankOf(x) || x.name.localeCompare(y.name));
  if (!players.find(p => p.id === state.playerId)) state.playerId = players[0].id;
  $('match').innerHTML = renderHero(g) + renderLineup(g) + renderPicker(g, players) + (state.view === 'tableau'
    ? renderTable(g, players)
    : renderPlayer(g, players.find(p => p.id === state.playerId)));
  if (state.focusStat && state.view === 'joueur') {
    const el = document.getElementById('stat-' + state.focusStat);
    state.focusStat = null;
    if (el) requestAnimationFrame(() => el.scrollIntoView({ behavior: 'smooth', block: 'start' }));
  }
}

function renderHero(g) {
  const h = findTeam(g.home), a = findTeam(g.away), d = new Date(g.date);
  const books = Object.keys(g.updated || {}).filter(b => BOOKS[b]);
  return `<div class="hero">
    <div class="hero-bg" style="background:linear-gradient(100deg,${a.c1} 0%,${a.c1} 38%,${h.c1} 62%,${h.c1} 100%)"></div>
    <div class="hero-in">
      <div class="side">${badge(a)}<div><div class="city">${esc(a.city)}</div><div class="nick">${esc(a.nick)}</div></div></div>
      <div class="vs"><div class="t num">${timeFmt.format(d)}</div><div class="d">${esc(dayLabel(d))}</div></div>
      <div class="side right">${badge(h)}<div><div class="city">${esc(h.city)}</div><div class="nick">${esc(h.nick)}</div></div></div>
    </div>
    <div class="hero-meta"><span><b>${g.players.length}</b> joueurs</span>
      ${books.length ? `<span>Mis à jour : ${books.map(b => `${BOOKS[b].name} ${ago(g.updated[b])}`).join(' · ')}</span>` : ''}</div>
  </div>`;
}

function renderPicker(g, players) {
  const q = state.search.trim().toLowerCase();
  const present = new Set(g.players.flatMap(p => [
    ...Object.values(p.stats || {}).flatMap(c => Object.values(c).flatMap(x => Object.keys(x.o))),
    ...Object.values(p.lines || {}).flatMap(l => Object.values(l).flatMap(x => [...Object.keys(x.over || {}), ...Object.keys(x.under || {})]))]));
  const list = players.filter(p => !q || p.name.toLowerCase().includes(q));
  return `
    <div class="controls">
      <div class="seg view-seg">
        <button class="${state.view === 'joueur' ? 'on' : ''}" data-view="joueur">Fiche joueur</button>
        <button class="${state.view === 'tableau' ? 'on' : ''}" data-view="tableau">Tableau</button>
      </div>
      <input class="search" id="search" placeholder="Rechercher un joueur…" value="${esc(state.search)}" autocomplete="off">
      <div class="books">${Object.keys(BOOKS).map(b => present.has(b)
        ? `<button class="book ${state.off.has(b) ? 'off' : ''}" data-book="${b}">${bk(b)}${BOOKS[b].name}</button>`
        : `<span class="book na" title="Pas de cotes pour ce match">${bk(b)}${BOOKS[b].name}</span>`).join('')}</div>
    </div>
    ${state.view === 'tableau' ? '' : `<div class="picker">${list.map(p => {
      const t = teamOf(g, p), r = rankOf(p);
      return `<button class="pchip ${p.id === state.playerId ? 'on' : ''}" data-player="${esc(p.id)}" style="${t ? `--tc:${t.c1}` : ''}">
        <span class="avatar sm">${esc(initials(p.name))}</span>
        <span class="pc-name">${esc(p.name)}</span>${p.starter ? `<span class="pc-pos">${esc(p.starter)}</span>` : ''}${injTag(p.inj, true)}
        ${r ? `<span class="pc-ref num">${r} pts</span>` : ''}
      </button>`;
    }).join('') || '<div class="small">Aucun joueur pour cette recherche.</div>'}</div>`}`;
}

// Case du tableau : ligne principale (celle que le plus de bookmakers proposent à l'équilibre)
// avec la cote Plus et Moins de chaque bookmaker ; à défaut, le palier dont la meilleure cote est
// la plus proche de 2,00, avec la cote de chaque bookmaker.
// Élément cliquable vers la page du match chez le bookmaker (nouvel onglet), sinon simple élément
function oddLink(b, cls, inner) {
  const url = game()?.links?.[b];
  return url
    ? `<a class="${cls} lnk" href="${esc(url)}" target="_blank" rel="noopener" title="Ouvrir le match chez ${esc(BOOKS[b]?.name || b)}">${inner}</a>`
    : `<span class="${cls}">${inner}</span>`;
}

function tableChips(obj) {
  const books = Object.entries(obj || {}).filter(([b]) => on(b)).sort((x, y) => y[1] - x[1]);
  if (!books.length) return '<span class="tc-none">–</span>';
  const best = books[0][1], tie = books.length > 1 && best === books[books.length - 1][1];
  return books.map(([b, o]) => oddLink(b, `tc-chip ${o === best && !tie && books.length > 1 ? 'best' : ''}`, `${bk(b)}<b class="num">${fmt(o)}</b>`)).join('');
}

// ------------------------------------------------------------ 5 derniers matchs (ESPN)
// valeurs stockées par match : [pts, reb, ast, 3pts, int, contres, balles perdues, minutes]
const L5_IDX = { pts: [0], reb: [1], ast: [2], '3pt': [3], stl: [4], blk: [5], tov: [6], pra: [0, 1, 2], pr: [0, 1], pa: [0, 2], ra: [1, 2], sb: [4, 5] };
function l5Vals(p, s) {
  const g = p.l5?.g;
  if (!g?.length) return null;
  if (s === 'dd' || s === 'td') return g.map(r => [r[0], r[1], r[2], r[4], r[5]].filter(v => v >= 10).length >= (s === 'dd' ? 2 : 3) ? 1 : 0);
  return L5_IDX[s] ? g.map(r => L5_IDX[s].reduce((t, i) => t + r[i], 0)) : null;
}
const l5Avg = (p, s) => { const v = l5Vals(p, s); return v ? v.reduce((a, b) => a + b, 0) / v.length : null; };
const dec1 = x => (Math.round(x * 10) / 10).toString().replace('.', ',');
// Ligne principale d'une catégorie : la plus proposée parmi les lignes à l'équilibre
function mainLine(p, s) {
  const lines = p.lines?.[s] || {};
  const { main } = balancedLines(lines);
  const count = l => new Set([...Object.keys(lines[l]?.over || {}), ...Object.keys(lines[l]?.under || {})].filter(on)).size;
  return [...main].sort((x, y) => count(y) - count(x))[0] || null;
}
const l5Class = (avg, line) => line == null || avg == null ? '' : avg > Number(line) ? 'above' : avg < Number(line) ? 'below' : '';
// Pastille « moy. 5 derniers » (ou « 2/5 » pour double/triple-double)
function l5Badge(p, s, line) {
  const avg = l5Avg(p, s);
  if (avg == null) return '';
  const n = p.l5.n;
  if (s === 'dd' || s === 'td') return `<span class="l5" title="${n} derniers matchs">${Math.round(avg * n)}/${n} derniers matchs</span>`;
  return `<span class="l5 ${l5Class(avg, line)}" title="Moyenne des ${n} derniers matchs">Moy. ${n} derniers : <b class="num">${dec1(avg)}</b></span>`;
}
// En-tête d'une case du tableau : moyenne des 5 derniers matchs à gauche, chaque match à droite
// (vert au-dessus de la ligne, rouge en dessous, saison passée en pâle)
function l5Head(p, s, line, extra) {
  const v = l5Vals(p, s), avg = l5Avg(p, s);
  if (avg == null) return extra ? `<span class="tc-head"><span class="tc-head-l">${extra}</span></span>` : '';
  const n = p.l5.n, yes = s === 'dd' || s === 'td';
  const prev = p.l5.prev ? `, dont ${p.l5.prev} de la saison passée (en pâle)` : '';
  const left = `<span class="tc-l5 ${yes ? '' : l5Class(avg, line)}" title="Moyenne des ${n} derniers matchs">${yes
    ? `<b class="num">${Math.round(avg * n)}/${n}</b> derniers matchs` : `moy. <b class="num">${dec1(avg)}</b>`}</span>`;
  const right = yes ? '' : `<span class="tc-l5v num" title="${n} derniers matchs, le plus récent à gauche${prev}">${
    v.map((x, i) => `<i class="${l5Class(x, line)}${i >= n - (p.l5.prev || 0) ? ' prev' : ''}">${dec1(x)}</i>`).join('')}</span>`;
  return `<span class="tc-head"><span class="tc-head-l">${left}${extra || ''}</span>${right}</span>`;
}

// Case du tableau : toutes les lignes à l'équilibre des bookmakers (une par bookmaker, souvent la même),
// chacune avec les cotes Plus et Moins de chaque bookmaker qui la propose.
function tableCell(p, s) {
  const lines = p.lines?.[s] || {};
  const { main } = balancedLines(lines);
  const line = mainLine(p, s);
  if (line) {
    const blocks = main.map(l => `<span class="tc-block"><span class="tc-line num">${l.replace('.', ',')}</span><span class="tc-rows">
      <span class="tc-row"><i class="tc-sign up">▲</i>${tableChips(lines[l].over)}</span>
      <span class="tc-row"><i class="tc-sign down">▼</i>${tableChips(lines[l].under)}</span></span></span>`);
    const multi = main.length > 1 ? `<span class="tc-multi" title="Les bookmakers ne proposent pas tous la même ligne">${main.length} lignes</span>` : '';
    return l5Head(p, s, line, multi) + `<span class="tc-blocks ${main.length > 1 ? 'multi' : ''}">${blocks.join('')}</span>`;
  }
  const best = obj => Math.max(0, ...Object.entries(obj || {}).filter(([b]) => on(b)).map(([, o]) => o));
  const cuts = Object.entries(p.stats?.[s] || {}).filter(([, cell]) => best(cell.o) > 0);
  if (!cuts.length) return '';
  const [c, cell] = cuts.reduce((x, y) => Math.abs(best(y[1].o) - 2) < Math.abs(best(x[1].o) - 2) ? y : x);
  return l5Head(p, s, YES_STATS.has(s) ? null : Number(c) - 0.5) + `<span class="tc-blocks"><span class="tc-block"><span class="tc-line num">${YES_STATS.has(s) ? 'Oui' : c + '+'}</span><span class="tc-rows"><span class="tc-row">${tableChips(cell.o)}</span></span></span></span>`;
}

const shortName = n => { const w = String(n).split(/\s+/); return w.length > 1 ? `${w[0][0]}. ${w.slice(1).join(' ')}` : n; };

function renderTable(g, players) {
  const q = state.search.trim().toLowerCase();
  const rows = players.filter(p => !q || p.name.toLowerCase().includes(q));
  const cols = STAT_ORDER.filter(s => rows.some(p => statsOfPlayer(p).includes(s)));
  if (!rows.length) return `<div class="empty">Aucun joueur pour cette recherche.</div>`;
  return `<div class="tbl-wrap"><table class="tbl">
    <thead><tr><th class="tbl-name">Joueur</th>${cols.map(s => `<th>${esc(label(s))}</th>`).join('')}</tr></thead>
    <tbody>${rows.map(p => {
      const t = teamOf(g, p);
      return `<tr><th class="tbl-name" style="${t ? `--tc:${t.c1}` : ''}"><button class="tbl-player" data-player="${esc(p.id)}" data-goto="1">
          <span class="avatar xs">${esc(initials(p.name))}</span><span class="nm-full">${esc(p.name)}</span><span class="nm-short">${esc(shortName(p.name))}</span>${injTag(p.inj, true)}</button></th>
        ${cols.map(s => { const c = tableCell(p, s); return c
          ? `<td><div class="tc" role="button" tabindex="0" data-cell="1" data-player="${esc(p.id)}" data-stat="${s}" title="${esc(p.name)} · ${esc(label(s))} : voir tous les paliers">${c}</div></td>`
          : '<td class="tc-empty">·</td>'; }).join('')}
      </tr>`;
    }).join('')}</tbody>
  </table></div>
  <div class="small tbl-legend">▲ cotes Plus · ▼ cotes Moins de chaque bookmaker sur sa ligne à l'équilibre (meilleure en vert) ; quand les bookmakers n'ont pas la même ligne, chaque ligne est affichée. En haut de chaque case : moyenne des 5 derniers matchs puis le détail, du plus récent au plus ancien (vert au-dessus de la ligne, rouge en dessous). Sans plus/moins : le palier le plus proche de 2,00. Touchez une case pour voir tous les paliers.</div>`;
}

function renderPlayer(g, p) {
  if (!p) return '';
  const t = teamOf(g, p);
  const stats = statsOfPlayer(p);
  return `<section class="player" style="${t ? `--tc:${t.c1}` : ''}">
    <div class="ph">
      <div class="avatar lg">${esc(initials(p.name))}</div>
      <div><div class="ph-name">${esc(p.name)}</div><div class="pteam">${t ? `${esc(t.city)} ${esc(t.nick)} · ` : ''}${p.starter ? `Titulaire (${esc(p.starter)}) · ` : ''}${p.l5 ? `${dec1(p.l5.g.reduce((t, r) => t + r[7], 0) / p.l5.n)} min sur ses ${p.l5.n} derniers matchs · ` : ''}${stats.length} catégories</div>${p.inj ? `<div style="margin-top:4px">${injTag(p.inj)}</div>` : ''}</div>
    </div>
    ${stats.map(s => renderStat(p, s)).join('')}
  </section>`;
}

function renderStat(p, s) {
  const yes = YES_STATS.has(s);
  const lines = p.lines?.[s] || {};
  const { main, others } = balancedLines(lines);
  const cuts = Object.entries(p.stats?.[s] || {})
    .map(([cut, cell]) => ({ cut: Number(cut), cell, books: Object.entries(cell.o).filter(([b]) => on(b)).sort((x, y) => y[1] - x[1]) }))
    .filter(c => c.books.length).sort((x, y) => x.cut - y.cut);
  if (!main.length && !cuts.length) return '';
  return `<div class="stat" id="stat-${s}">
    <div class="stat-h"><h3>${esc(label(s))}</h3>${l5Badge(p, s, mainLine(p, s))}${main.length ? `<span class="small">ligne à l'équilibre</span>` : ''}</div>
    ${l5Detail(p, s)}
    ${main.map(l => renderLine(l, lines[l])).join('')}
    ${others.length ? `<details class="more"><summary>Autres lignes (${others.length})</summary>${others.map(l => renderLine(l, lines[l])).join('')}</details>` : ''}
    ${cuts.length ? `<div class="sub">${yes ? 'Cotes' : 'Paliers'}</div><div class="cuts">${cuts.map(c => renderCut(c, yes, l5Vals(p, s))).join('')}</div>` : ''}
  </div>`;
}

function renderLine(line, sides) {
  const side = (key, sign) => {
    const books = Object.entries(sides?.[key] || {}).filter(([b]) => on(b)).sort((x, y) => y[1] - x[1]);
    if (!books.length) return `<div class="ou-side"><div class="ou-lab">${sign} ${line}</div><span class="small">—</span></div>`;
    const best = books[0][1], tie = books.length > 1 && best === books[books.length - 1][1];
    return `<div class="ou-side"><div class="ou-lab"><b class="num">${sign}${line}</b></div><div class="ou-books">${books.map(([b, o]) =>
      oddLink(b, `ou ${o === best && !tie && books.length > 1 ? 'best' : ''}`, `${bk(b)}<span class="num">${fmt(o)}</span>`)).join('')}</div></div>`;
  };
  return `<div class="ouline">${side('over', '+')}${side('under', '−')}</div>`;
}

function l5Detail(p, s) {
  const v = l5Vals(p, s);
  if (!v || s === 'dd' || s === 'td') return '';
  const prev = p.l5.prev ? ` <span class="small">(dont ${p.l5.prev} de la saison passée)</span>` : '';
  return `<div class="l5-detail small">${p.l5.n} derniers matchs : ${v.map(x => `<b class="num">${dec1(x)}</b>`).join(' · ')}${prev}</div>`;
}

function renderCut({ cut, cell, books }, yes, vals) {
  const best = books[0][1], worst = books[books.length - 1][1];
  const tie = books.length > 1 && best === worst;  // même cote partout : rien à surligner
  const avg = books.reduce((s, [, o]) => s + o, 0) / books.length;
  const edge = books.length > 1 ? (best / avg - 1) * 100 : 0;
  return `<div class="cut ${books.length === 1 ? 'solo' : ''}">
    <div class="cut-h"><span class="v num">${yes ? 'Oui' : `${cut}<small>+</small>`}</span>${!yes && vals ? `<span class="hit" title="Palier atteint sur les ${vals.length} derniers matchs">${vals.filter(x => x >= cut).length}/${vals.length}</span>` : ''}${edge >= 4 ? `<span class="edge">+${edge.toFixed(0)}%</span>` : ''}</div>
    ${books.map(([b, o]) => {
      const prev = cell.p?.[b];
      const arr = prev != null && prev !== o ? `<span class="arr ${o > prev ? 'up' : 'down'}" title="avant : ${fmt(prev)}">${o > prev ? '▲' : '▼'}</span>` : '';
      return oddLink(b, `line ${o === best && !tie ? 'best' : ''}`, `${bk(b)}<span class="bn">${esc(BOOKS[b]?.name || b)}</span><span class="o num">${fmt(o)}${arr}</span>`);
    }).join('')}
  </div>`;
}

// ------------------------------------------------------------ page « Écarts » (écarts de ligne en cours)
const GAP_SPORTS = [['nba', 'NBA'], ['europe', 'Basket européen']];
const currentGaps = s => (state.data?.gaps?.[s] || []).filter(x => new Date(x.start) > Date.now());
const bookName = b => BOOKS[b]?.name || ({ pmu: 'PMU' }[b] || b);

function renderPages() {
  const n = GAP_SPORTS.reduce((t, [s]) => t + currentGaps(s).length, 0);
  $('pages').innerHTML = `<button class="${state.page === 'matchs' ? 'on' : ''}" data-page="matchs">Matchs</button>
    <button class="${state.page === 'ecarts' ? 'on' : ''}" data-page="ecarts">Écarts${n ? `<span class="pg-n">${n}</span>` : ''}</button>`;
}

function renderGaps() {
  const list = currentGaps(state.gapSport).sort((x, y) => new Date(x.start) - new Date(y.start) || y.gap - x.gap);
  const at = state.gapSport === 'europe' ? state.data?.gaps?.europeAt : state.data?.generatedAt;
  return `<section class="gaps">
    <div class="gaps-h"><h2>Écarts de ligne</h2>
      <p class="small">Deux bookmakers n'ont pas la même ligne à l'équilibre : au moins 3 d'écart pour les points et les combinés (P+R+A, P+R, P+A),
      2 pour les rebonds, passes, R+A, paniers à 3 pts et interceptions, 3 au handicap, ou un handicap inversé.
      Un écart disparaît dès qu'il se referme ou que le match commence.</p></div>
    <div class="tabs">${GAP_SPORTS.map(([k, l]) => `<button class="tab ${state.gapSport === k ? 'on' : ''}" data-gapsport="${k}">${l}<span class="n">${currentGaps(k).length}</span></button>`).join('')}</div>
    ${at ? `<div class="small gaps-note">Cotes lues ${ago(at)} · ${state.gapSport === 'europe' ? 'relues toutes les 10 min' : 'relues toutes les 2 min'}</div>` : ''}
    ${list.length ? list.map(gapCard).join('')
      : `<div class="empty"><div class="big">📏</div>Aucun écart de ligne en ce moment${state.gapSport === 'europe' ? ' en basket européen' : ' en NBA'}.</div>`}
  </section>`;
}

function gapCard(x) {
  const prop = x.kind === 'prop', d = new Date(x.start);
  const fmtL = l => (!prop && Number(l) > 0 ? '+' : '') + String(l).replace('.', ',');
  const books = Object.keys(x.books).sort((a, b) => Number(x.books[a].main ?? 1e9) - Number(x.books[b].main ?? 1e9));
  const mains = books.map(b => x.books[b].main).filter(l => l != null).map(Number);
  const lines = [...new Set(books.flatMap(b => Object.keys(x.books[b].lines)))].sort((a, b) => Number(a) - Number(b));
  const near = l => Number(l) >= Math.min(...mains) - 2 && Number(l) <= Math.max(...mains) + 2;
  const far = lines.filter(l => !near(l)).length;
  const open = state.openGaps.has(x.id);
  const link = (b, inner, cls) => x.books[b].url
    ? `<a class="${cls} lnk" href="${esc(x.books[b].url)}" target="_blank" rel="noopener" title="Ouvrir le match chez ${esc(bookName(b))}">${inner}</a>`
    : `<span class="${cls}">${inner}</span>`;
  const odd = (b, l, i) => { const o = x.books[b]?.lines[l]?.[i]; return o == null ? '–' : fmt(o); };
  // les deux paris de l'écart : ligne basse jouée « Plus » (ou équipe à domicile au plus gros handicap), ligne haute « Moins »
  const lo = x.books[x.lo], hi = x.books[x.hi];
  const bets = x.lo === x.hi || lo?.main == null || hi?.main == null ? '' : prop
    ? `${link(x.lo, `Plus de <b>${fmtL(lo.main)}</b> chez ${esc(bookName(x.lo))} : <b>${odd(x.lo, lo.main, 0)}</b>`, 'gc-bet')}
       ${link(x.hi, `Moins de <b>${fmtL(hi.main)}</b> chez ${esc(bookName(x.hi))} : <b>${odd(x.hi, hi.main, 1)}</b>`, 'gc-bet')}`
    : `${link(x.hi, `${esc(x.home)} <b>${fmtL(hi.main)}</b> chez ${esc(bookName(x.hi))} : <b>${odd(x.hi, hi.main, 0)}</b>`, 'gc-bet')}
       ${link(x.lo, `${esc(x.away)} <b>${fmtL(String(-Number(lo.main)))}</b> chez ${esc(bookName(x.lo))} : <b>${odd(x.lo, lo.main, 1)}</b>`, 'gc-bet')}`;
  const h = x.game ? findTeam(x.home) : null, a = x.game ? findTeam(x.away) : null;
  const match = h ? `${a.nick} @ ${h.nick}` : `${x.home} – ${x.away}`;
  const flipped = (x.flipped || []).map(bookName).join(' et ');
  const cell = (b, l) => {
    const v = x.books[b].lines[l];
    if (!v) return '<td class="gm-na">·</td>';
    const main = x.books[b].main === l;
    return `<td class="${main ? 'main' : ''} ${main && (b === x.lo || b === x.hi) ? 'ext' : ''}">${link(b,
      `<span class="gm-o"><i class="up">${prop ? '▲' : ''}</i>${odd(b, l, 0)}</span><span class="gm-o"><i class="down">${prop ? '▼' : ''}</i>${odd(b, l, 1)}</span>`, 'gm-c')}</td>`;
  };
  return `<article class="gap-card ${open ? 'open' : ''}">
    <div class="gc-top">
      ${prop && x.game ? `<button class="gc-name" data-gotoplayer="${esc(x.player)}" data-gotogame="${esc(x.game)}" data-stat="${esc(x.stat)}" title="Voir la fiche du joueur">${esc(x.name)}</button>`
        : `<span class="gc-name">${esc(x.name)}</span>`}
      ${prop ? `<span class="gc-cat">${esc(x.cat)}</span>` : ''}
      ${x.inverted ? '<span class="gc-inv">Handicap inversé</span>' : ''}
      <span class="gc-gap">écart ${String(x.gap).replace('.', ',')}</span>
    </div>
    <div class="gc-match">${esc(match)} · ${esc(dayLabel(d))} ${timeFmt.format(d)}${x.league && !x.game && x.league !== 'NBA' ? ` · ${esc(x.league)}` : ''}</div>
    ${flipped ? `<div class="gc-flip">${esc(flipped)} donne des points à ${esc(x.fav)} alors qu'il est favori.</div>` : ''}
    ${bets ? `<div class="gc-bets">${bets}</div>` : ''}
    <div class="gm-wrap"><table class="gm">
      <thead><tr><th class="gm-line">${prop ? 'Ligne' : `Handicap ${esc(h ? h.nick : x.home)}`}</th>${books.map(b =>
        `<th class="${b === x.lo || b === x.hi ? 'ext' : ''}">${bk(b)} ${esc(bookName(b))}</th>`).join('')}</tr></thead>
      <tbody>${lines.map(l => `<tr class="${near(l) ? '' : 'gm-far'}"><th class="gm-line num">${fmtL(l)}</th>${books.map(b => cell(b, l)).join('')}</tr>`).join('')}</tbody>
    </table></div>
    <div class="gc-foot small">${prop ? '▲ cote Plus · ▼ cote Moins' : `En haut : ${esc(x.home)} · en bas : ${esc(x.away)}`} · encadré : ligne à l'équilibre du bookmaker
      ${far ? `<button class="gm-more" data-gapmore="${esc(x.id)}">${open ? 'Masquer les lignes éloignées' : `Voir les ${far} autres lignes`}</button>` : ''}</div>
  </article>`;
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
  if (e.target.closest('a[href]')) return;  // cote → page du match chez le bookmaker
  const t = e.target.closest('button, [data-cell]');
  if (!t) return;
  if (t.id === 'refresh') return load();
  if (t.dataset.page) {
    state.page = t.dataset.page;
    try { localStorage.setItem('page', state.page); } catch {}
    window.scrollTo(0, 0);
  }
  else if (t.dataset.gapsport) {
    state.gapSport = t.dataset.gapsport;
    try { localStorage.setItem('gapSport', state.gapSport); } catch {}
  }
  else if (t.dataset.gapmore) { const id = t.dataset.gapmore; state.openGaps.has(id) ? state.openGaps.delete(id) : state.openGaps.add(id); }
  else if (t.dataset.gotoplayer) {  // écart NBA → fiche du joueur, sur la catégorie concernée
    Object.assign(state, { page: 'matchs', gameId: t.dataset.gotogame, playerId: t.dataset.gotoplayer, view: 'joueur', focusStat: t.dataset.stat });
    try { localStorage.setItem('page', 'matchs'); } catch {}
  }
  else if (t.dataset.game) { state.gameId = t.dataset.game; state.playerId = null; state.search = ''; }
  else if (t.dataset.view) {
    state.view = t.dataset.view;
    try { localStorage.setItem('view', state.view); } catch {}
  }
  else if (t.dataset.player) {
    state.playerId = t.dataset.player;
    if (state.view === 'tableau' || t.dataset.lu) { state.view = 'joueur'; state.focusStat = t.dataset.stat || null; }
    if (!state.focusStat) requestAnimationFrame(() => document.querySelector('.player')?.scrollIntoView({ behavior: 'smooth', block: 'start' }));
  }
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
document.addEventListener('visibilitychange', () => { if (document.hidden) rememberSpot(); else if (!DEMO) load(); });
window.addEventListener('pagehide', rememberSpot);
setInterval(() => { if (!document.hidden && !DEMO) load(); }, POLL_MS);
load();
