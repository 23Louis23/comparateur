// Page « Analyse map foot » : les stats lues par l'iMac arrivent chiffrées (foot.enc, branche data).
// Le mot de passe ne quitte jamais l'appareil : il sert ici, dans le navigateur, à retrouver la clé qui déchiffre le fichier.
(() => {
  const KEY_STORE = 'footKey', REFRESH_MS = 10 * 60e3, RETRY_MS = 60e3;
  const STAT_KEYS = ['min', 's', 'sot', 'g', 'xg', 'kp', 'a', 'xa'];
  const COLUMNS = [['s', 'Tirs', 'Tirs'], ['sot', 'Cadrés', 'Cad.'], ['xg', 'xG', 'xG'], ['kp', 'Passes clés', 'PC'], ['xa', 'xA', 'xA']];
  const stored = k => { try { return localStorage.getItem(k); } catch { return null; } };
  const store = (k, v) => { try { v === null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch {} };
  const foot = { enc: null, data: null, key: stored(KEY_STORE), n: stored('footN') === '5' ? 5 : 3, sort: null, open: null, side: 0,
                 busy: false, at: 0, missing: false, wrong: false, opening: false };

  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const dec2 = x => (Math.round(x * 100) / 100).toFixed(2).replace('.', ',');
  const dec1 = x => (Math.round(x * 10) / 10).toFixed(1).replace('.', ',');
  const parisDay = d => new Intl.DateTimeFormat('fr-CA', { timeZone: 'Europe/Paris' }).format(d);
  const hour = new Intl.DateTimeFormat('fr-FR', { timeZone: 'Europe/Paris', hour: '2-digit', minute: '2-digit' });
  const shortDay = new Intl.DateTimeFormat('fr-FR', { timeZone: 'Europe/Paris', day: '2-digit', month: '2-digit' });
  const label = (full, short) => `<span class="ft-l">${full}</span><span class="ft-s">${short}</span>`;
  const matchId = () => (location.hash.match(/^#m=(.+)$/) || [])[1] || null;
  const ago = iso => {
    const min = Math.round((Date.now() - new Date(iso)) / 60000);
    return min < 1 ? "à l'instant" : min < 60 ? `il y a ${min} min` : `il y a ${Math.floor(min / 60)} h ${String(min % 60).padStart(2, '0')}`;
  };

  // ---------------------------------------------------------- chiffrement (AES-256-GCM, clé PBKDF2-SHA256)
  const bytes = s => Uint8Array.from(atob(s), c => c.charCodeAt(0));
  const base64 = b => btoa(String.fromCharCode(...b));
  async function derive(pass, enc) {
    const base = await crypto.subtle.importKey('raw', new TextEncoder().encode(pass.normalize('NFC')), 'PBKDF2', false, ['deriveBits']);
    const bits = await crypto.subtle.deriveBits({ name: 'PBKDF2', hash: 'SHA-256', salt: bytes(enc.salt), iterations: enc.iter }, base, 256);
    return base64(new Uint8Array(bits));
  }
  async function decrypt(key, enc) {
    const k = await crypto.subtle.importKey('raw', bytes(key), 'AES-GCM', false, ['decrypt']);
    const plain = await crypto.subtle.decrypt({ name: 'AES-GCM', iv: bytes(enc.iv) }, k, bytes(enc.data));
    return JSON.parse(new TextDecoder().decode(plain));
  }

  async function fetchEnc() {
    if (foot.busy) return;
    foot.busy = true;
    foot.at = Date.now();
    try {
      const url = lastSha ? `https://raw.githubusercontent.com/${REPO}/${lastSha}/foot.enc`
        : `https://raw.githubusercontent.com/${REPO}/data/foot.enc?t=${Date.now()}`;
      const r = await fetch(url, { cache: 'no-store' });
      foot.missing = r.status === 404;
      if (r.ok) {
        const enc = await r.json();
        const changed = enc.iv !== foot.enc?.iv;
        foot.enc = enc;
        if (foot.key && (changed || !foot.data)) {
          try { foot.data = await decrypt(foot.key, enc); }
          catch { foot.key = null; foot.data = null; store(KEY_STORE, null); }  // mot de passe changé depuis : on le redemande
        }
      }
    } catch {}
    foot.busy = false;
    render();
  }

  async function unlock(pass) {
    foot.opening = true;
    foot.wrong = false;
    render();
    try {
      const key = await derive(pass, foot.enc);
      foot.data = await decrypt(key, foot.enc);
      foot.key = key;
      store(KEY_STORE, key);
    } catch { foot.wrong = true; }
    foot.opening = false;
    render();
  }

  // ---------------------------------------------------------- données d'une équipe
  function teamData(lg, name) {
    const d = foot.data;
    const ids = (d.teams[`${lg}|${name}`] || []).slice(0, foot.n);
    const games = ids.map(id => d.matches[id]).filter(Boolean).map(m => {
      const side = m.h === name ? 0 : 1;
      return { ...m, side, opp: side ? m.h : m.a, gf: side ? m.sa : m.sh, ga: side ? m.sh : m.sa };
    });
    const players = new Map();
    for (const g of games) for (const row of g.pl) {
      const p = Object.fromEntries(d.cols.map((c, i) => [c, row[i]]));
      if (p.t !== g.side) continue;
      if (!players.has(p.id)) players.set(p.id, { id: p.id, n: p.n, p: p.p, rows: {}, ...Object.fromEntries(STAT_KEYS.map(k => [k, 0])) });
      const t = players.get(p.id);
      t.rows[g.id] = p;
      for (const k of STAT_KEYS) t[k] += p[k];
    }
    return { ids, games, players: [...players.values()] };
  }

  // ---------------------------------------------------------- liste des matchs
  function fixtureRow(f) {
    const d = foot.data, want = [f.home, f.away].flatMap(t => (d.teams[`${f.lg}|${t}`] || []).slice(0, foot.n));
    const ok = want.filter(id => d.matches[id]?.pl.length).length;
    const tag = new Date(f.start) <= Date.now() ? 'commencé' : ok < want.length ? `${ok}/${want.length} lus` : '';
    return `<a class="ft-fx" href="#m=${esc(f.id)}"><span class="ft-t">${hour.format(new Date(f.start))}</span>
      <span class="ft-teams">${esc(f.home)} – ${esc(f.away)}</span>${tag ? `<span class="ft-tag">${tag}</span>` : ''}<span class="ft-go">›</span></a>`;
  }

  function renderList() {
    const d = foot.data;
    const fx = d.fixtures.filter(f => new Date(f.start) > Date.now() - 2.5 * 3600e3);
    const block = (title, day) => {
      const rows = fx.filter(f => parisDay(new Date(f.start)) === day);
      if (!rows.length) return '';
      return `<h2 class="ft-day">${title}</h2>` + d.leagues.map(l => {
        const mine = rows.filter(f => f.lg === l.id);
        return mine.length ? `<div class="ft-lg"><div class="ft-lg-h">${esc(l.name)} <span>· ${esc(l.country)}</span></div>${mine.map(fixtureRow).join('')}</div>` : '';
      }).join('');
    };
    const html = block("Aujourd'hui", parisDay(new Date())) + block('Demain', parisDay(new Date(Date.now() + 864e5)));
    return (d.read < d.total ? `<div class="ft-note">Lecture en cours : ${d.read} matchs lus sur ${d.total}. La page se complète toute seule.</div>` : '')
      + (html || '<div class="ft-empty">Aucun match aujourd\'hui ni demain dans ces championnats.</div>');
  }

  // ---------------------------------------------------------- détail d'un match
  function teamTable(t) {
    if (!t.games.length) return '<div class="ft-tw"><div class="ft-empty">Matchs pas encore lus.</div></div>';
    const stat = (g, k, f) => g.st[k] ? f(g.st[k][g.side]) : '–';
    const mean = (k, f) => {
      const v = t.games.filter(g => g.st[k]).map(g => g.st[k][g.side]);
      return v.length ? f(v.reduce((a, b) => a + b, 0) / v.length) : '–';
    };
    const rows = t.games.map(g => {
      const cls = g.gf > g.ga ? 'w' : g.gf < g.ga ? 'l' : 'd';
      return `<tr><td>${g.start ? shortDay.format(new Date(g.start)) : ''} <span class="ft-m">${g.side ? 'à' : 'vs'}</span> ${esc(g.opp)}</td>
        <td><span class="ft-res ${cls}">${g.gf}-${g.ga}</span></td><td>${stat(g, 'pos', v => `${v} %`)}</td><td>${stat(g, 'sh', v => v)}</td>
        <td>${stat(g, 'sot', v => v)}</td><td>${stat(g, 'xg', dec2)}</td></tr>`;
    }).join('');
    return `<div class="ft-tw"><table><thead><tr><th>Match</th><th>Score</th><th>${label('Possession', 'Poss.')}</th><th>Tirs</th><th>${label('Cadrés', 'Cad.')}</th><th>xG</th></tr></thead>
      <tbody>${rows}<tr class="ft-avg"><td>Moyenne</td><td></td><td>${mean('pos', v => `${Math.round(v)} %`)}</td><td>${mean('sh', dec1)}</td><td>${mean('sot', dec1)}</td><td>${mean('xg', dec2)}</td></tr></tbody></table></div>`;
  }

  function playerDetail(t, p) {
    const rows = t.games.map(g => {
      const r = p.rows[g.id];
      const name = `${g.start ? shortDay.format(new Date(g.start)) : ''} ${g.side ? 'à' : 'vs'} ${esc(g.opp)}`;
      if (!g.pl.length) return `<tr class="ft-abs"><td>${name}</td><td colspan="8">stats joueurs indisponibles</td></tr>`;
      if (!r) return `<tr class="ft-abs"><td>${name}</td><td colspan="8">n'a pas joué</td></tr>`;
      return `<tr><td>${name}</td><td>${r.min}'</td><td>${r.s}</td><td>${r.sot}</td><td>${dec2(r.xg)}</td><td>${r.g}</td><td>${r.kp}</td><td>${dec2(r.xa)}</td><td>${r.a}</td></tr>`;
    }).join('');
    return `<div class="ft-tw-in"><table><thead><tr><th>Match</th><th>Min</th><th>Tirs</th><th>${label('Cadrés', 'Cad.')}</th><th>xG</th><th>Buts</th><th>${label('Passes clés', 'PC')}</th><th>xA</th><th>${label('Passes déc.', 'PD')}</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }

  function playerTable(t, key) {
    const played = t.games.filter(g => g.pl.length);
    if (!t.players.length) return '<div class="ft-tw"><div class="ft-empty">Stats joueurs pas encore lues.</div></div>';
    const by = foot.sort;
    const list = [...t.players].sort((a, b) => by ? b[by] - a[by] : (b.xg + b.xa) - (a.xg + a.xa));
    const top = Object.fromEntries(COLUMNS.map(([k]) => [k, Math.max(...list.map(p => p[k]))]));
    const cell = (p, k) => `<td class="${!p[k] ? 'ft-zero' : p[k] === top[k] ? 'ft-hot' : ''}">${k === 'xg' || k === 'xa' ? dec2(p[k]) : p[k]}</td>`;
    const rows = list.map(p => {
      const id = `${key}|${p.id}`, open = foot.open === id;
      const mins = played.map(g => p.rows[g.id] ? p.rows[g.id].min : '–').join(' · ');
      return `<tr class="ft-pl ${open ? 'open' : ''}" data-ftplayer="${esc(id)}"><td><span class="ft-pn">${esc(p.n)}</span><span class="ft-pp">${esc(p.p)}</span></td>
        <td><b>${p.min}'</b><span class="ft-mins">${mins}</span></td>${COLUMNS.map(([k]) => cell(p, k)).join('')}</tr>
        ${open ? `<tr class="ft-det"><td colspan="7">${playerDetail(t, p)}</td></tr>` : ''}`;
    }).join('');
    return `<div class="ft-tw"><table><thead><tr><th>Joueur</th><th class="ft-sort ${by === 'min' ? 'on' : ''}" data-ftsort="min">${label('Temps de jeu', 'Min')}</th>
      ${COLUMNS.map(([k, l, c]) => `<th class="ft-sort ${by === k ? 'on' : ''}" data-ftsort="${k}">${label(l, c)}</th>`).join('')}</tr></thead><tbody>${rows}</tbody></table></div>`;
  }

  function renderMatch(id) {
    const d = foot.data, f = d.fixtures.find(x => x.id === id);
    if (!f) return '<a class="ft-back" href="#">‹ Tous les matchs</a><div class="ft-empty">Ce match n\'est plus dans la liste.</div>';
    const league = d.leagues.find(l => l.id === f.lg), start = new Date(f.start);
    const block = (name, i) => {
      const t = teamData(f.lg, name);
      const missing = Math.min(foot.n, (d.teams[`${f.lg}|${name}`] || []).length) - t.games.filter(g => g.pl.length).length;
      return `<section class="ft-team ${foot.side === i ? '' : 'off'}"><h3>${esc(name)}</h3>
        ${missing > 0 ? `<div class="ft-note">${missing} match(s) pas encore lu(s) pour cette équipe.</div>` : ''}
        <h4>${foot.n} derniers matchs de championnat</h4>${teamTable(t)}
        <h4>Joueurs</h4>${playerTable(t, `${f.id}|${i}`)}</section>`;
    };
    return `<a class="ft-back" href="#">‹ Tous les matchs</a>
      <div class="ft-head"><div class="ft-ttl">${esc(f.home)} – ${esc(f.away)}</div>
        <div class="small">${esc(league?.name)} · ${parisDay(start) === parisDay(new Date()) ? "aujourd'hui" : 'demain'} à ${hour.format(start)}</div></div>
      <div class="ft-seg ft-pick"><button class="${foot.side === 0 ? 'on' : ''}" data-ftside="0">${esc(f.home)}</button><button class="${foot.side === 1 ? 'on' : ''}" data-ftside="1">${esc(f.away)}</button></div>
      <div class="ft-grid">${block(f.home, 0)}${block(f.away, 1)}</div>
      <p class="small">Totaux sur les ${foot.n} derniers matchs de championnat (coupes et Europe non comptées). PC : passes clés, PD : passes décisives. Sous le temps de jeu : les minutes de chaque match, du plus récent au plus ancien. En vert : le meilleur de l'équipe. Touchez un joueur pour le détail match par match, un titre de colonne pour trier.</p>`;
  }

  function lockScreen() {
    return `<div class="ft-lock"><h2>Analyse map foot</h2><p class="small">Page privée. Entrez votre mot de passe : les données sont déchiffrées ici, sur cet appareil.</p>
      <form id="ft-form"><input id="ft-pass" type="password" autocomplete="current-password" placeholder="Mot de passe" aria-label="Mot de passe" ${foot.opening ? 'disabled' : ''}>
      <button type="submit" ${foot.opening ? 'disabled' : ''}>${foot.opening ? 'Ouverture…' : 'Ouvrir'}</button></form>
      ${foot.wrong ? '<div class="ft-wrong">Mot de passe incorrect.</div>' : ''}</div>`;
  }

  window.renderFoot = () => {
    if (!foot.busy && Date.now() - foot.at > (foot.enc ? REFRESH_MS : RETRY_MS)) setTimeout(fetchEnc);
    let body;
    if (foot.data) {
      const id = matchId();
      body = `<div class="ft-top"><h2>Analyse map foot</h2>
        <div class="ft-seg">${[3, 5].map(n => `<button class="${foot.n === n ? 'on' : ''}" data-ftn="${n}">${n} matchs</button>`).join('')}</div>
        <span class="ft-at small">Données ${ago(foot.data.at)}</span><button class="ft-lockbtn" data-ftlock="1">Verrouiller</button></div>
        ${id ? renderMatch(id) : renderList()}`;
    }
    else if (foot.enc) body = lockScreen();
    else if (foot.missing) body = '<div class="ft-empty">Les données ne sont pas encore publiées. La page se remplira toute seule.</div>';
    else body = '<div class="ft-empty">Chargement…</div>';
    return `<section class="ft">${body}</section>`;
  };

  // Le site se redessine à chaque passage : on ne touche pas au champ pendant la saisie du mot de passe
  window.footTyping = () => {
    const i = document.getElementById('ft-pass');
    return !!i && (document.activeElement === i || !!i.value);
  };

  document.addEventListener('submit', e => {
    if (e.target.id !== 'ft-form') return;
    e.preventDefault();
    const i = document.getElementById('ft-pass'), pass = i.value;
    i.value = '';
    i.blur();
    if (pass) unlock(pass);
  });
  document.addEventListener('click', e => {
    const t = e.target.closest('[data-ftn], [data-ftside], [data-ftsort], [data-ftplayer], [data-ftlock]');
    if (!t) return;
    if (t.dataset.ftn) { foot.n = Number(t.dataset.ftn); store('footN', foot.n); }
    else if (t.dataset.ftside) foot.side = Number(t.dataset.ftside);
    else if (t.dataset.ftsort) foot.sort = foot.sort === t.dataset.ftsort ? null : t.dataset.ftsort;
    else if (t.dataset.ftlock) { foot.key = null; foot.data = null; store(KEY_STORE, null); }
    else foot.open = foot.open === t.dataset.ftplayer ? null : t.dataset.ftplayer;
    render();
  });
  window.addEventListener('hashchange', () => {
    if (state.page !== 'foot') return;
    foot.open = null;
    foot.side = 0;
    render();
    window.scrollTo(0, 0);
  });
})();
