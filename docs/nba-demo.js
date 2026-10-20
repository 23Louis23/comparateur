// Données de DÉMO (nba.html?demo=1) : cotes fictives au même format que nba.json
function buildNbaDemo() {
  const rand = seed => () => (seed = (seed * 16807) % 2147483647) / 2147483647;
  const r = rand(42);
  const cdf = x => { const t = 1 / (1 + .2316419 * Math.abs(x)); const d = .3989423 * Math.exp(-x * x / 2);
    const p = d * t * (.3193815 + t * (-.3565638 + t * (1.781478 + t * (-1.821256 + t * 1.330274)))); return x > 0 ? 1 - p : p; };
  const SD = { pts: m => .27 * m + 1.5, reb: m => .3 * m + 1, ast: m => .35 * m + .8, '3pt': m => .5 * m + .6, pra: m => .2 * m + 2.5 };
  const CUTS = { pts: [10, 15, 20, 25, 30, 35, 40], reb: [4, 6, 8, 10, 12, 14], ast: [2, 4, 6, 8, 10, 12], '3pt': [1, 2, 3, 4, 5], pra: [20, 25, 30, 35, 40, 45, 50] };
  const MARGIN = { unibet: .08, winamax: .07, pmu: .1, betclic: .075, bwin: .09, pokerstars: .085 };
  const now = Date.now();
  const games = [
    ['Los Angeles Lakers', 'Golden State Warriors', 18, [['Luka Dončić', 30, 8.5, 8.5, 3.4, 'home'], ['LeBron James', 23, 7, 8, 2, 'home'], ['Austin Reaves', 20, 4.3, 5.5, 2.6, 'home'], ['Stephen Curry', 26, 4.5, 6, 4.5, 'away'], ['Jimmy Butler', 19, 5.5, 5, .8, 'away'], ['Draymond Green', 9, 6.5, 6, 1.2, 'away']]],
    ['Oklahoma City Thunder', 'Denver Nuggets', 20.5, [['Shai Gilgeous-Alexander', 31, 5, 6.5, 2, 'home'], ['Jalen Williams', 21, 5.2, 5, 1.6, 'home'], ['Chet Holmgren', 17, 8.5, 2, 1.7, 'home'], ['Nikola Jokić', 28, 12.5, 10, 1.8, 'away'], ['Jamal Murray', 21, 4, 6, 2.6, 'away'], ['Aaron Gordon', 14, 6, 3, 1.2, 'away']]],
    ['San Antonio Spurs', 'Houston Rockets', 43, [['Victor Wembanyama', 25.5, 11.5, 3.6, 2.8, 'home'], ['De\'Aaron Fox', 21, 4, 6.5, 1.6, 'home'], ['Stephon Castle', 15, 5, 4.5, 1.2, 'home'], ['Kevin Durant', 26, 6, 4, 2.2, 'away'], ['Alperen Şengün', 20, 10, 5, .4, 'away'], ['Amen Thompson', 14, 8, 4, .5, 'away']]],
  ].map(([home, away, inH, roster], gi) => {
    const players = roster.map(([name, pts, reb, ast, tp, team], pi) => {
      const means = { pts, reb, ast, '3pt': tp, pra: pts + reb + ast };
      const books = Object.keys(MARGIN).filter(() => r() > .25);
      const stats = {};
      for (const k in means) {
        stats[k] = {};
        for (const c of CUTS[k]) {
          const p = 1 - cdf((c - .5 - means[k]) / SD[k](means[k]));
          if (p < .06 || p > .88) continue;
          const cell = { o: {}, p: {} };
          for (const b of books) {
            if (r() < .15) continue;
            const o = Math.max(1.02, (1 / (p * (1 + MARGIN[b] * 1.6))) * (1 + (r() - .5) * .14));
            cell.o[b] = o < 3 ? Math.round(o * 100) / 100 : Math.round(o * 20) / 20;
            if (r() < .15) cell.p[b] = Math.round(cell.o[b] * (1 + (r() - .5) * .12) * 100) / 100;
          }
          if (Object.keys(cell.o).length) stats[k][c] = cell;
        }
      }
      return { id: `${gi}-${pi}`, name, team, stats };
    });
    return { id: 'demo' + gi, date: new Date(now + inH * 3600e3).toISOString(), home, away, players, updated: {} };
  });
  return { generatedAt: new Date().toISOString(), demo: true, source: { status: 'démo' }, books: Object.keys(MARGIN),
    stats: { pts: 'Points', reb: 'Rebonds', ast: 'Passes', '3pt': 'Paniers à 3 pts', pra: 'Pts + Reb + Pas' }, games };
}
