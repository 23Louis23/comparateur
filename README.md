# CoteRadar — comparateur de cotes

Compare les cotes de Unibet, Winamax, PMU, Betclic, bwin et PokerStars.
Le site est hébergé gratuitement sur GitHub Pages ; une tâche GitHub Actions
récupère les cotes automatiquement et met le site à jour.

```
docs/                    le site (servi par GitHub Pages)
  index.html, app.js     interface
  odds.json              cotes, réécrit à chaque mise à jour
  demo.js                cotes fictives (index.html?demo=1)
scripts/fetch_odds.py    collecte des cotes (Python, sans dépendance)
data/state.json          quotas consommés, horodatages
data/coverage/           échantillons bruts pour vérifier la couverture réelle
.github/workflows/       planification (toutes les 15 min)
```

## Sources de cotes

| Source | Clé (secret GitHub) | Bookmakers | Marchés |
|---|---|---|---|
| [The Odds API](https://the-odds-api.com) | `ODDS_API_KEY` | Unibet, Winamax, PMU, Betclic | 1N2 / vainqueur (les marchés joueurs n'y existent que pour les bookmakers US) |
| [OddsPapi](https://oddspapi.io) | `ODDSPAPI_KEY` | bwin.fr, PokerStars.fr, PMU, Unibet.fr, Winamax.fr (pas Betclic) | 1N2, buteur, premier buteur, passeur, paliers NBA (points, rebonds, passes, 3 pts, PRA), vainqueur des courses cyclistes |

Offres gratuites : 500 crédits/mois (The Odds API), 250 requêtes/mois (OddsPapi).
Le script répartit le quota restant sur le reste du mois : en gratuit, chaque
source passe quelques fois par jour ; avec un abonnement, la fréquence monte
toute seule (jusqu'à toutes les 15 min, rythme de la tâche planifiée).

Avec un quota OddsPapi plus élevé, ajuster dans GitHub → Settings → Secrets and
variables → Actions → **Variables** : `ODDSPAPI_MONTHLY_LIMIT` et `ODDSPAPI_PER_RUN`.

## Mise en ligne (une fois)

1. Créer un dépôt **public** sur GitHub (GitHub Pages est gratuit pour les dépôts publics ; les clés restent secrètes).
2. Pousser ce dossier sur la branche `main`.
3. Settings → Pages → *Deploy from a branch* → `main` / `/docs`.
4. Settings → Secrets and variables → Actions → *New repository secret* : `ODDS_API_KEY`, `ODDSPAPI_KEY`.
5. Actions → « Mise à jour des cotes » → *Run workflow* (cocher *force* pour le premier passage).

Le site est alors sur `https://<utilisateur>.github.io/<dépôt>/`. Sur iPhone :
Safari → Partager → « Sur l'écran d'accueil » pour l'avoir comme une application.

## En local

```bash
ODDS_API_KEY=... ODDSPAPI_KEY=... python3 scripts/fetch_odds.py --force
python3 -m http.server 5173 --directory docs
```

## Après le test gratuit

Regarder la barre d'état du site (nombre de matchs par bookmaker) et
`data/coverage/` (réponses brutes d'OddsPapi) pour voir ce qui est réellement
disponible, puis choisir l'abonnement. Les marchés OddsPapi autres que le 1N2
apparaissent sous leur nom d'origine ; une fois l'échantillon reçu, on pourra les
renommer et regrouper les paliers (buteur, passeur, cuts NBA, top 3/10…).
