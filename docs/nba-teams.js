// Équipes NBA : abréviation, ville, surnom, couleurs (principale, secondaire)
const NBA_TEAMS = [
  ['ATL', 'Atlanta', 'Hawks', '#C8102E', '#FDB927'],
  ['BOS', 'Boston', 'Celtics', '#007A33', '#BA9653'],
  ['BKN', 'Brooklyn', 'Nets', '#1d1d1d', '#777D84'],
  ['CHA', 'Charlotte', 'Hornets', '#1D1160', '#00788C'],
  ['CHI', 'Chicago', 'Bulls', '#CE1141', '#000000'],
  ['CLE', 'Cleveland', 'Cavaliers', '#860038', '#FDBB30'],
  ['DAL', 'Dallas', 'Mavericks', '#00538C', '#B8C4CA'],
  ['DEN', 'Denver', 'Nuggets', '#0E2240', '#FEC524'],
  ['DET', 'Detroit', 'Pistons', '#C8102E', '#1D42BA'],
  ['GSW', 'Golden State', 'Warriors', '#1D428A', '#FFC72C'],
  ['HOU', 'Houston', 'Rockets', '#CE1141', '#000000'],
  ['IND', 'Indiana', 'Pacers', '#002D62', '#FDBB30'],
  ['LAC', 'LA', 'Clippers', '#C8102E', '#1D428A'],
  ['LAL', 'Los Angeles', 'Lakers', '#552583', '#FDB927'],
  ['MEM', 'Memphis', 'Grizzlies', '#5D76A9', '#12173F'],
  ['MIA', 'Miami', 'Heat', '#98002E', '#F9A01B'],
  ['MIL', 'Milwaukee', 'Bucks', '#00471B', '#EEE1C6'],
  ['MIN', 'Minnesota', 'Timberwolves', '#0C2340', '#78BE20'],
  ['NOP', 'New Orleans', 'Pelicans', '#0C2340', '#C8102E'],
  ['NYK', 'New York', 'Knicks', '#006BB6', '#F58426'],
  ['OKC', 'Oklahoma City', 'Thunder', '#007AC1', '#EF3B24'],
  ['ORL', 'Orlando', 'Magic', '#0077C0', '#C4CED4'],
  ['PHI', 'Philadelphia', '76ers', '#006BB6', '#ED174C'],
  ['PHX', 'Phoenix', 'Suns', '#1D1160', '#E56020'],
  ['POR', 'Portland', 'Trail Blazers', '#E03A3E', '#000000'],
  ['SAC', 'Sacramento', 'Kings', '#5A2D81', '#63727A'],
  ['SAS', 'San Antonio', 'Spurs', '#1d1d1d', '#C4CED4'],
  ['TOR', 'Toronto', 'Raptors', '#CE1141', '#000000'],
  ['UTA', 'Utah', 'Jazz', '#002B5C', '#F9A01B'],
  ['WAS', 'Washington', 'Wizards', '#002B5C', '#E31837'],
].map(([abbr, city, nick, c1, c2]) => ({ abbr, city, nick, c1, c2 }));

// Retrouve une équipe à partir du nom renvoyé par le fournisseur (« Denver Nuggets », « LA Clippers »…)
function findTeam(name) {
  const n = String(name || '').toLowerCase();
  return NBA_TEAMS.find(t => n.includes(t.nick.toLowerCase()))
    || NBA_TEAMS.find(t => n.includes(t.city.toLowerCase()))
    || { abbr: String(name || '?').slice(0, 3).toUpperCase(), city: '', nick: name || '?', c1: '#334155', c2: '#64748b' };
}
