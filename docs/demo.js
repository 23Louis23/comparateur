// =============================================================
//  Données de DÉMO (index.html?demo=1) — cotes fictives générées
//  localement, au même format que odds.json produit par
//  scripts/fetch_odds.py.
// =============================================================

const DEMO_BOOKMAKERS = [
  { id: 'unibet',     name: 'Unibet',     short: 'UNI', color: '#1ea768', margin: 0.065 },
  { id: 'winamax',    name: 'Winamax',    short: 'WIN', color: '#e2001a', margin: 0.055 },
  { id: 'pmu',        name: 'PMU',        short: 'PMU', color: '#0e8f4f', margin: 0.085 },
  { id: 'betclic',    name: 'Betclic',    short: 'BCL', color: '#ff3b3b', margin: 0.060 },
  { id: 'bwin',       name: 'bwin',       short: 'BWI', color: '#ffcc00', margin: 0.075 },
  { id: 'pokerstars', name: 'PokerStars', short: 'PKS', color: '#c8102e', margin: 0.070 },
];

// ---------- helpers de modélisation ----------
function normCdf(x) {
  // approximation d'Abramowitz-Stegun
  const t = 1 / (1 + 0.2316419 * Math.abs(x));
  const d = 0.3989423 * Math.exp(-x * x / 2);
  const p = d * t * (0.3193815 + t * (-0.3565638 + t * (1.781478 + t * (-1.821256 + t * 1.330274))));
  return x > 0 ? 1 - p : p;
}
const SD = { pts: m => 0.27 * m + 1.5, reb: m => 0.3 * m + 1, ast: m => 0.35 * m + 0.8, pra: m => 0.2 * m + 2.5, '3pt': m => 0.5 * m + 0.6 };
function probOver(mean, line, stat) {
  return 1 - normCdf((line - 0.5 - mean) / SD[stat](mean));
}

// ---------- constructeurs de marchés ----------
function football(ev) {
  const all = [...ev.homePlayers.map(p => ({ ...p, team: ev.home })), ...ev.awayPlayers.map(p => ({ ...p, team: ev.away }))];
  return [
    { id: '1x2', label: 'Résultat 1N2', complete: true,
      selections: [
        { name: ev.home, p: ev.probs[0] },
        { name: 'Match nul', p: ev.probs[1] },
        { name: ev.away, p: ev.probs[2] },
      ] },
    { id: 'scorer', label: 'Buteur', hint: 'Marque à tout moment', props: true,
      selections: all.map(p => ({ name: p.n, team: p.team, p: p.g })) },
    { id: 'assist', label: 'Passeur décisif', hint: 'Délivre au moins une passe décisive', props: true, notAt: ['pmu'],
      selections: all.map(p => ({ name: p.n, team: p.team, p: p.a })) },
    { id: 'decisive', label: 'Décisif (but ou passe)', hint: 'Marque ou délivre une passe décisive', props: true, notAt: ['bwin'],
      selections: all.map(p => ({ name: p.n, team: p.team, p: 1 - (1 - p.g) * (1 - p.a) * 1.04 })) },
    { id: 'first_scorer', label: 'Premier buteur', props: true,
      selections: all.map(p => ({ name: p.n, team: p.team, p: p.g * 0.24 })) },
  ];
}

function basket(ev) {
  const all = [...ev.homePlayers.map(p => ({ ...p, team: ev.home })), ...ev.awayPlayers.map(p => ({ ...p, team: ev.away }))];
  const cut = (id, label, stat, lines, pick, notAt) => ({
    id, label, props: true, lines, notAt, hint: 'Choisis un palier (cut)',
    selections: all.map(p => ({ name: p.n, team: p.team, mean: pick(p), stat })),
  });
  return [
    { id: 'ml', label: 'Vainqueur', complete: true,
      selections: [{ name: ev.home, p: ev.probs[0] }, { name: ev.away, p: ev.probs[1] }] },
    cut('pts', 'Points', 'pts', [10, 15, 20, 25, 30, 35, 40], p => p.pts),
    cut('reb', 'Rebonds', 'reb', [4, 6, 8, 10, 12, 14], p => p.reb, ['pmu']),
    cut('ast', 'Passes', 'ast', [2, 4, 6, 8, 10, 12], p => p.ast, ['pmu']),
    cut('3pt', 'Paniers à 3 pts', '3pt', [1, 2, 3, 4, 5], p => p.tp, ['pmu', 'bwin']),
    cut('pra', 'Pts + Reb + Pas', 'pra', [20, 25, 30, 35, 40, 45, 50], p => p.pts + p.reb + p.ast, ['pmu', 'pokerstars']),
  ];
}

function rugby(ev) {
  const all = [...ev.homePlayers.map(p => ({ ...p, team: ev.home })), ...ev.awayPlayers.map(p => ({ ...p, team: ev.away }))];
  return [
    { id: '1x2', label: 'Résultat 1N2', complete: true,
      selections: [{ name: ev.home, p: ev.probs[0] }, { name: 'Match nul', p: ev.probs[1] }, { name: ev.away, p: ev.probs[2] }] },
    { id: 'try_any', label: 'Marqueur d\'essai', hint: 'Marque un essai à tout moment', props: true,
      selections: all.map(p => ({ name: p.n, team: p.team, p: p.t })) },
    { id: 'try_first', label: 'Premier marqueur d\'essai', props: true, notAt: ['bwin'],
      selections: all.map(p => ({ name: p.n, team: p.team, p: p.t * 0.17 })) },
    { id: 'try_2plus', label: '2 essais ou +', props: true, notAt: ['pmu', 'pokerstars'],
      selections: all.map(p => ({ name: p.n, team: p.team, p: p.t * p.t * 0.38 })) },
  ];
}

function cycling(ev) {
  return [
    { id: 'win', label: 'Vainqueur', selections: ev.riders.map(r => ({ name: r.n, team: r.team, p: r.w })) },
    { id: 'podium', label: 'Top 3', notAt: ['bwin'],
      selections: ev.riders.map(r => ({ name: r.n, team: r.team, p: Math.min(0.82, r.w * 2.3 + 0.025) })) },
    { id: 'top10', label: 'Top 10', notAt: ['pmu', 'pokerstars'],
      selections: ev.riders.map(r => ({ name: r.n, team: r.team, p: Math.min(0.9, r.w * 3.6 + 0.16) })) },
    { id: 'h2h', label: 'Meilleur Français', notAt: ['bwin', 'pokerstars', 'unibet'],
      selections: ev.riders.filter(r => r.fr).map((r, _, arr) => {
        const tot = arr.reduce((s, x) => s + Math.sqrt(x.w), 0);
        return { name: r.n, team: r.team, p: Math.sqrt(r.w) / tot };
      }), complete: true },
  ];
}

// ---------- événements ----------
const EVENTS = {
  football: [
    { id: 'psg-om', competition: 'Ligue 1 · J7', date: '2026-10-04T21:00', home: 'Paris SG', away: 'Marseille', probs: [0.62, 0.21, 0.17],
      homePlayers: [
        { n: 'Ousmane Dembélé', g: 0.42, a: 0.25 }, { n: 'Khvicha Kvaratskhelia', g: 0.33, a: 0.27 },
        { n: 'Gonçalo Ramos', g: 0.38, a: 0.10 }, { n: 'Bradley Barcola', g: 0.30, a: 0.22 },
        { n: 'Désiré Doué', g: 0.27, a: 0.24 }, { n: 'Achraf Hakimi', g: 0.12, a: 0.20 },
        { n: 'Vitinha', g: 0.10, a: 0.18 }, { n: 'João Neves', g: 0.10, a: 0.15 },
      ],
      awayPlayers: [
        { n: 'Mason Greenwood', g: 0.32, a: 0.17 }, { n: 'Pierre-Emerick Aubameyang', g: 0.26, a: 0.12 },
        { n: 'Amine Gouiri', g: 0.25, a: 0.12 }, { n: 'Igor Paixão', g: 0.17, a: 0.17 },
        { n: 'Matt O\'Riley', g: 0.12, a: 0.14 }, { n: 'Pierre-Emile Højbjerg', g: 0.07, a: 0.10 },
      ] },
    { id: 'liv-che', competition: 'Premier League · J7', date: '2026-10-04T18:30', home: 'Liverpool', away: 'Chelsea', probs: [0.50, 0.25, 0.25],
      homePlayers: [
        { n: 'Mohamed Salah', g: 0.40, a: 0.28 }, { n: 'Alexander Isak', g: 0.40, a: 0.10 },
        { n: 'Hugo Ekitiké', g: 0.35, a: 0.14 }, { n: 'Cody Gakpo', g: 0.27, a: 0.15 },
        { n: 'Florian Wirtz', g: 0.24, a: 0.26 }, { n: 'Dominik Szoboszlai', g: 0.15, a: 0.15 },
      ],
      awayPlayers: [
        { n: 'Cole Palmer', g: 0.33, a: 0.24 }, { n: 'João Pedro', g: 0.30, a: 0.16 },
        { n: 'Liam Delap', g: 0.28, a: 0.08 }, { n: 'Pedro Neto', g: 0.18, a: 0.20 },
        { n: 'Enzo Fernández', g: 0.15, a: 0.14 }, { n: 'Moisés Caicedo', g: 0.07, a: 0.08 },
      ] },
    { id: 'rma-vil', competition: 'LaLiga · J8', date: '2026-10-05T21:00', home: 'Real Madrid', away: 'Villarreal', probs: [0.66, 0.19, 0.15],
      homePlayers: [
        { n: 'Kylian Mbappé', g: 0.56, a: 0.20 }, { n: 'Vinícius Júnior', g: 0.35, a: 0.25 },
        { n: 'Jude Bellingham', g: 0.26, a: 0.20 }, { n: 'Rodrygo', g: 0.22, a: 0.15 },
        { n: 'Arda Güler', g: 0.18, a: 0.22 }, { n: 'Federico Valverde', g: 0.14, a: 0.13 },
      ],
      awayPlayers: [
        { n: 'Gerard Moreno', g: 0.24, a: 0.12 }, { n: 'Ayoze Pérez', g: 0.24, a: 0.10 },
        { n: 'Georges Mikautadze', g: 0.22, a: 0.09 }, { n: 'Nicolas Pépé', g: 0.15, a: 0.15 },
        { n: 'Santi Comesaña', g: 0.07, a: 0.09 },
      ] },
  ],
  basket: [
    { id: 'sas-okc', competition: 'NBA · Pré-saison', date: '2026-10-06T02:00', home: 'San Antonio Spurs', away: 'Oklahoma City Thunder', probs: [0.41, 0.59],
      homePlayers: [
        { n: 'Victor Wembanyama', pts: 25.5, reb: 11.5, ast: 3.6, tp: 2.8 }, { n: 'De\'Aaron Fox', pts: 20.5, reb: 4.0, ast: 6.2, tp: 1.6 },
        { n: 'Stephon Castle', pts: 15.0, reb: 5.0, ast: 4.2, tp: 1.2 }, { n: 'Devin Vassell', pts: 14.5, reb: 4.0, ast: 2.7, tp: 2.4 },
        { n: 'Dylan Harper', pts: 12.5, reb: 3.5, ast: 4.0, tp: 1.1 }, { n: 'Harrison Barnes', pts: 11.0, reb: 4.0, ast: 1.8, tp: 1.7 },
      ],
      awayPlayers: [
        { n: 'Shai Gilgeous-Alexander', pts: 31.0, reb: 5.0, ast: 6.4, tp: 2.0 }, { n: 'Jalen Williams', pts: 21.0, reb: 5.2, ast: 5.0, tp: 1.6 },
        { n: 'Chet Holmgren', pts: 17.0, reb: 8.6, ast: 2.1, tp: 1.7 }, { n: 'Isaiah Hartenstein', pts: 10.0, reb: 9.2, ast: 3.6, tp: 0.1 },
        { n: 'Luguentz Dort', pts: 10.0, reb: 4.0, ast: 1.5, tp: 2.1 }, { n: 'Cason Wallace', pts: 8.5, reb: 3.2, ast: 2.4, tp: 1.4 },
      ] },
    { id: 'lal-gsw', competition: 'NBA · Pré-saison', date: '2026-10-07T04:00', home: 'Los Angeles Lakers', away: 'Golden State Warriors', probs: [0.55, 0.45],
      homePlayers: [
        { n: 'Luka Dončić', pts: 29.5, reb: 8.3, ast: 8.4, tp: 3.4 }, { n: 'LeBron James', pts: 22.5, reb: 7.2, ast: 7.8, tp: 2.0 },
        { n: 'Austin Reaves', pts: 20.0, reb: 4.3, ast: 5.5, tp: 2.6 }, { n: 'Deandre Ayton', pts: 13.5, reb: 9.8, ast: 1.5, tp: 0.1 },
        { n: 'Rui Hachimura', pts: 12.0, reb: 4.6, ast: 1.3, tp: 1.4 },
      ],
      awayPlayers: [
        { n: 'Stephen Curry', pts: 25.0, reb: 4.3, ast: 6.0, tp: 4.4 }, { n: 'Jimmy Butler', pts: 18.5, reb: 5.6, ast: 5.4, tp: 0.8 },
        { n: 'Jonathan Kuminga', pts: 15.0, reb: 5.0, ast: 2.2, tp: 1.0 }, { n: 'Draymond Green', pts: 8.5, reb: 6.2, ast: 5.8, tp: 1.2 },
        { n: 'Brandin Podziemski', pts: 11.5, reb: 5.0, ast: 3.6, tp: 1.6 },
      ] },
  ],
  rugby: [
    { id: 'st-lr', competition: 'Top 14 · J5', date: '2026-10-04T21:05', home: 'Stade Toulousain', away: 'La Rochelle', probs: [0.66, 0.03, 0.31],
      homePlayers: [
        { n: 'Matthis Lebel', t: 0.42 }, { n: 'Antoine Dupont', t: 0.38 }, { n: 'Peato Mauvaka', t: 0.35 },
        { n: 'Blair Kinghorn', t: 0.32 }, { n: 'Juan Cruz Mallía', t: 0.30 }, { n: 'Thomas Ramos', t: 0.22 },
        { n: 'Romain Ntamack', t: 0.19 }, { n: 'François Cros', t: 0.14 },
      ],
      awayPlayers: [
        { n: 'Teddy Thomas', t: 0.30 }, { n: 'Dillyn Leyds', t: 0.27 }, { n: 'Grégory Alldritt', t: 0.24 },
        { n: 'Pierre Bourgarit', t: 0.22 }, { n: 'Jonathan Danty', t: 0.17 }, { n: 'Tawera Kerr-Barlow', t: 0.13 },
      ] },
    { id: 'ubb-r92', competition: 'Top 14 · J5', date: '2026-10-05T21:05', home: 'Bordeaux-Bègles', away: 'Racing 92', probs: [0.64, 0.03, 0.33],
      homePlayers: [
        { n: 'Louis Bielle-Biarrey', t: 0.48 }, { n: 'Damian Penaud', t: 0.45 }, { n: 'Yoram Moefana', t: 0.22 },
        { n: 'Matthieu Jalibert', t: 0.20 }, { n: 'Maxime Lucu', t: 0.15 }, { n: 'Pete Samu', t: 0.18 },
      ],
      awayPlayers: [
        { n: 'Henry Arundell', t: 0.40 }, { n: 'Max Spring', t: 0.30 }, { n: 'Gaël Fickou', t: 0.18 },
        { n: 'Siya Kolisi', t: 0.15 }, { n: 'Nolann Le Garrec', t: 0.14 },
      ] },
  ],
  cyclisme: [
    { id: 'lombardia', competition: 'Il Lombardia · Monument', date: '2026-10-10T10:30', home: 'Il Lombardia', away: 'Côme → Bergame · 241 km',
      riders: [
        { n: 'Tadej Pogačar', team: 'UAE', w: 0.56 }, { n: 'Remco Evenepoel', team: 'Red Bull-Bora', w: 0.10 },
        { n: 'Isaac del Toro', team: 'UAE', w: 0.05 }, { n: 'Primož Roglič', team: 'Red Bull-Bora', w: 0.045 },
        { n: 'Juan Ayuso', team: 'Lidl-Trek', w: 0.04 }, { n: 'Tom Pidcock', team: 'Q36.5', w: 0.035 },
        { n: 'Paul Seixas', team: 'Decathlon AG2R', w: 0.02, fr: true }, { n: 'Mattias Skjelmose', team: 'Lidl-Trek', w: 0.018 },
        { n: 'Giulio Ciccone', team: 'Lidl-Trek', w: 0.015 }, { n: 'Ben Healy', team: 'EF', w: 0.013 },
        { n: 'Richard Carapaz', team: 'EF', w: 0.012 }, { n: 'Lenny Martinez', team: 'Bahrain', w: 0.011, fr: true },
        { n: 'Romain Grégoire', team: 'Groupama-FDJ', w: 0.01, fr: true }, { n: 'Kévin Vauquelin', team: 'Ineos', w: 0.009, fr: true },
        { n: 'Simon Yates', team: 'Visma', w: 0.008 }, { n: 'Valentin Madouas', team: 'Groupama-FDJ', w: 0.004, fr: true },
      ] },
    { id: 'paris-tours', competition: 'Paris-Tours · Classique', date: '2026-10-11T11:00', home: 'Paris-Tours', away: 'Chartres → Tours · 213 km',
      riders: [
        { n: 'Mads Pedersen', team: 'Lidl-Trek', w: 0.12 }, { n: 'Arnaud De Lie', team: 'Lotto', w: 0.10 },
        { n: 'Christophe Laporte', team: 'Visma', w: 0.08, fr: true }, { n: 'Jasper Philipsen', team: 'Alpecin', w: 0.08 },
        { n: 'Paul Magnier', team: 'Soudal Quick-Step', w: 0.07, fr: true }, { n: 'Biniam Girmay', team: 'Intermarché', w: 0.06 },
        { n: 'Olav Kooij', team: 'Decathlon AG2R', w: 0.055 }, { n: 'Axel Laurance', team: 'Ineos', w: 0.05, fr: true },
        { n: 'Romain Grégoire', team: 'Groupama-FDJ', w: 0.045, fr: true }, { n: 'Valentin Madouas', team: 'Groupama-FDJ', w: 0.04, fr: true },
        { n: 'Benoît Cosnefroy', team: 'Decathlon AG2R', w: 0.035, fr: true }, { n: 'Matteo Trentin', team: 'Tudor', w: 0.03 },
        { n: 'Ethan Vernon', team: 'Israel', w: 0.025 }, { n: 'Kévin Vauquelin', team: 'Ineos', w: 0.025, fr: true },
      ] },
  ],
};

const BUILDERS = { football, basket, rugby, cyclisme: cycling };
for (const id in BUILDERS) for (const ev of EVENTS[id]) ev.markets = BUILDERS[id](ev);

// ---------- génération déterministe des cotes ----------
function hash(str) {
  let h = 2166136261;
  for (let i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 16777619); }
  return h >>> 0;
}
function rand(seed) { // mulberry32 → [0,1)
  let t = (seed += 0x6D2B79F5);
  t = Math.imul(t ^ (t >>> 15), t | 1);
  t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
  return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
}

/**
 * Renvoie la cote décimale d'un bookmaker, ou null si non proposée.
 * `tick` simule l'évolution des cotes à chaque rafraîchissement.
 */
function getOdds(ev, market, sel, line, book, tick = 0) {
  if (market.notAt && market.notAt.includes(book.id)) return null;
  const key = `${ev.id}|${market.id}|${sel.name}|${line ?? ''}|${book.id}`;
  if (market.props && rand(hash(key + '#avail')) < 0.06) return null;

  const p = sel.mean !== undefined ? probOver(sel.mean, line, sel.stat) : sel.p;
  if (p < 0.012 || p > 0.97) return null;

  const margin = book.margin * (market.props ? 1.7 : 1);
  const noise = (rand(hash(key)) - 0.5) * 0.12;
  let drift = 0;
  for (let t = 1; t <= tick; t++) {
    const r = rand(hash(key + '#t' + t));
    if (r < 0.3) drift += (rand(hash(key + '#d' + t)) - 0.5) * 0.06;
  }
  let odds = (1 / (p * (1 + margin))) * (1 + noise + drift);
  odds = Math.max(1.01, odds);
  // arrondis façon bookmaker
  return odds < 3 ? Math.round(odds * 100) / 100 : odds < 10 ? Math.round(odds * 20) / 20 : Math.round(odds * 2) / 2;
}

// Convertit le modèle de démo au format odds.json
function buildDemoData(tick = 0) {
  const events = [];
  for (const sport in EVENTS) for (const ev of EVENTS[sport]) {
    const markets = ev.markets.map(m => {
      const selections = [];
      for (const line of (m.lines || [null])) m.selections.forEach((sel, i) => {
        const odds = {}, prev = {};
        for (const b of DEMO_BOOKMAKERS) {
          const v = getOdds(ev, m, sel, line, b, tick);
          if (v == null) continue;
          odds[b.id] = v;
          const p = tick > 0 ? getOdds(ev, m, sel, line, b, tick - 1) : null;
          if (p != null && p !== v) prev[b.id] = p;
        }
        if (Object.keys(odds).length) selections.push({ key: sel.name + '|' + (line ?? ''), name: sel.name, team: sel.team, line, order: i, odds, prev });
      });
      return { key: m.id, label: m.label, hint: m.hint, complete: !!m.complete, selections };
    });
    events.push({ id: ev.id, sport, competition: ev.competition, date: ev.date, home: ev.home, away: ev.away, markets });
  }
  return { generatedAt: new Date().toISOString(), demo: true, sources: {}, events };
}
