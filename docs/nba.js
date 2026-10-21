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

const state = { data: null, error: null, gameId: null, playerId: null, search: '', off: new Set(), view: 'joueur', focusStat: null };
try { state.view = localStorage.getItem('view') || 'joueur'; } catch {}
const qsView = new URLSearchParams(location.search).get('vue');
if (qsView === 'tableau' || qsView === 'joueur') state.view = qsView;
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
function render() {
  renderStatus();
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
function tableChips(obj) {
  const books = Object.entries(obj || {}).filter(([b]) => on(b)).sort((x, y) => y[1] - x[1]);
  if (!books.length) return '<span class="tc-none">–</span>';
  const best = books[0][1], tie = books.length > 1 && best === books[books.length - 1][1];
  return books.map(([b, o]) => `<span class="tc-chip ${o === best && !tie && books.length > 1 ? 'best' : ''}">${bk(b)}<b class="num">${fmt(o)}</b></span>`).join('');
}

function tableCell(p, s) {
  const lines = p.lines?.[s] || {};
  const { main } = balancedLines(lines);
  const count = l => new Set([...Object.keys(lines[l]?.over || {}), ...Object.keys(lines[l]?.under || {})].filter(on)).size;
  const line = [...main].sort((x, y) => count(y) - count(x))[0];
  if (line) {
    return `<span class="tc-line num">${line.replace('.', ',')}</span><span class="tc-rows">
      <span class="tc-row"><i class="tc-sign up">▲</i>${tableChips(lines[line].over)}</span>
      <span class="tc-row"><i class="tc-sign down">▼</i>${tableChips(lines[line].under)}</span></span>`;
  }
  const best = obj => Math.max(0, ...Object.entries(obj || {}).filter(([b]) => on(b)).map(([, o]) => o));
  const cuts = Object.entries(p.stats?.[s] || {}).filter(([, cell]) => best(cell.o) > 0);
  if (!cuts.length) return '';
  const [c, cell] = cuts.reduce((x, y) => Math.abs(best(y[1].o) - 2) < Math.abs(best(x[1].o) - 2) ? y : x);
  return `<span class="tc-line num">${YES_STATS.has(s) ? 'Oui' : c + '+'}</span><span class="tc-rows"><span class="tc-row">${tableChips(cell.o)}</span></span>`;
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
          ? `<td><button class="tc" data-player="${esc(p.id)}" data-stat="${s}" title="${esc(p.name)} · ${esc(label(s))} : voir tous les paliers">${c}</button></td>`
          : '<td class="tc-empty">·</td>'; }).join('')}
      </tr>`;
    }).join('')}</tbody>
  </table></div>
  <div class="small tbl-legend">▲ cotes Plus · ▼ cotes Moins de chaque bookmaker sur la ligne principale (meilleure en vert). Sans plus/moins : le palier le plus proche de 2,00. Touchez une case pour voir tous les paliers.</div>`;
}

function renderPlayer(g, p) {
  if (!p) return '';
  const t = teamOf(g, p);
  const stats = statsOfPlayer(p);
  return `<section class="player" style="${t ? `--tc:${t.c1}` : ''}">
    <div class="ph">
      <div class="avatar lg">${esc(initials(p.name))}</div>
      <div><div class="ph-name">${esc(p.name)}</div><div class="pteam">${t ? `${esc(t.city)} ${esc(t.nick)} · ` : ''}${p.starter ? `Titulaire (${esc(p.starter)}) · ` : ''}${stats.length} catégories</div>${p.inj ? `<div style="margin-top:4px">${injTag(p.inj)}</div>` : ''}</div>
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
    <div class="stat-h"><h3>${esc(label(s))}</h3>${main.length ? `<span class="small">ligne à l'équilibre</span>` : ''}</div>
    ${main.map(l => renderLine(l, lines[l])).join('')}
    ${others.length ? `<details class="more"><summary>Autres lignes (${others.length})</summary>${others.map(l => renderLine(l, lines[l])).join('')}</details>` : ''}
    ${cuts.length ? `<div class="sub">${yes ? 'Cotes' : 'Paliers'}</div><div class="cuts">${cuts.map(c => renderCut(c, yes)).join('')}</div>` : ''}
  </div>`;
}

function renderLine(line, sides) {
  const side = (key, sign) => {
    const books = Object.entries(sides?.[key] || {}).filter(([b]) => on(b)).sort((x, y) => y[1] - x[1]);
    if (!books.length) return `<div class="ou-side"><div class="ou-lab">${sign} ${line}</div><span class="small">—</span></div>`;
    const best = books[0][1], tie = books.length > 1 && best === books[books.length - 1][1];
    return `<div class="ou-side"><div class="ou-lab"><b class="num">${sign}${line}</b></div><div class="ou-books">${books.map(([b, o]) =>
      `<span class="ou ${o === best && !tie && books.length > 1 ? 'best' : ''}">${bk(b)}<span class="num">${fmt(o)}</span></span>`).join('')}</div></div>`;
  };
  return `<div class="ouline">${side('over', '+')}${side('under', '−')}</div>`;
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
  if (t.dataset.game) { state.gameId = t.dataset.game; state.playerId = null; state.search = ''; }
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
document.addEventListener('visibilitychange', () => { if (!document.hidden && !DEMO) load(); });
setInterval(() => { if (!document.hidden && !DEMO) load(); }, POLL_MS);
load();
