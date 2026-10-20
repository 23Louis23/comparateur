# CoteRadar NBA — comparateur de props joueurs

Site : https://23louis23.github.io/comparateur/ (démo : `?demo=1`)

Pour chaque match NBA, une ligne par joueur avec tous les paliers (cuts) proposés
par Unibet, Winamax, PMU, bwin et PokerStars, et la cote de chaque bookmaker.
Statistiques : points, rebonds, passes, paniers à 3 pts, combinaisons (P+R+A, P+R,
P+A, R+A), interceptions, contres, balles perdues, double-double, triple-double.

```
docs/                    le site (GitHub Pages)
  index.html, nba.js     interface
  nba-teams.js           équipes (abréviations, couleurs)
  nba-demo.js            données fictives pour ?demo=1
  nba.json               props, réécrit à chaque mise à jour
scripts/fetch_nba.py     collecte OddsPapi (Python, sans dépendance)
data/nba-state.json      quota consommé, caches (catalogue, équipes)
data/nba-diagnostic.json ce que chaque bookmaker a renvoyé au dernier passage
```

## Source

[OddsPapi](https://oddspapi.io), secret GitHub `ODDSPAPI_KEY`. Betclic n'y est pas
disponible. Un « Over 24.5 points » est affiché comme le palier **25+**.

Offre gratuite : 250 requêtes/mois. Une mise à jour complète coûte environ
5 requêtes (une par bookmaker) ; le script répartit le quota sur le mois et ne
dépense rien s'il n'y a pas de match dans les 36 h. Avec un abonnement, régler
`ODDSPAPI_MONTHLY_LIMIT` dans GitHub → Settings → Secrets and variables → Actions
→ Variables : la fréquence augmente toute seule.

## En local

```bash
ODDSPAPI_KEY=... python3 scripts/fetch_nba.py --force
python3 -m http.server 5173 --directory docs
```
