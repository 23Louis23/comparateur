"""xA par match : la colonne « Potential AST » de NBA.com (passes qui mènent à un tir, réussi ou non).

NBA.com refuse les lectures automatiques (protection Akamai) : on ne cherche pas à passer. Le projet public
gabriel1200/site_Data relève chaque jour les totaux de la saison sur GitHub ; l'écart entre deux relevés donne le
chiffre d'un match, à condition qu'un seul match les sépare. Sinon, le match reste sans valeur.
Vérifié sur nba.com le 2026-11-02 : 10/10 sur le dernier match, 43/46 sur le 25/10 (3 écarts de 1, corrigés depuis)."""
import csv
import io
from datetime import datetime, timedelta

from common import get, get_json, iso, norm_name, now
from stats import season_year

REPO = "gabriel1200/site_Data"
COMMITS = "https://api.github.com/repos/%s/commits?path=tracking/passing.csv&since=%%s&per_page=100" % REPO
RAW = "https://raw.githubusercontent.com/%s/%%s/tracking/passing.csv" % REPO
CHECK_HOURS = 3
TEAM_MAX = 100  # xA d'une équipe sur un match : 47 en médiane, 75 au plus ; au-delà le relevé est faux


def update(cache):
    """Lit les nouveaux relevés et range {joueur: {rang du match dans la saison: [passes, xA]}}. → nombre de relevés lus."""
    season = season_year()
    x = cache.get("xa")
    if not x or x.get("season") != season:
        x = cache["xa"] = {"season": season, "last": "%d-09-01T00:00:00Z" % (season - 1), "totals": {}, "games": {}}
    if x.get("checked") and now() - datetime.fromisoformat(x["checked"][:-1] + "+00:00") < timedelta(hours=CHECK_HOURS):
        return 0
    read = 0
    for c in reversed(get_json(COMMITS % x["last"])):  # du plus ancien au plus récent
        at = c["commit"]["committer"]["date"]
        if at <= x["last"]:
            continue
        fresh = {}
        for r in csv.DictReader(io.StringIO(get(RAW % c["sha"]))):
            if r["year"] != str(season):
                continue
            key = norm_name(r["PLAYER_NAME"])
            gp, ast, pot = int(r["GP"]), int(r["AST"]), int(r["POTENTIAL_AST"])
            old = x["totals"].get(key, [0, 0, 0])
            if gp == old[0] + 1 and pot >= old[2]:
                fresh.setdefault(r["TEAM_ABBREVIATION"], []).append((key, gp, ast - old[1], pot - old[2]))
            elif gp == old[0] and str(gp) in x["games"].get(key, {}):  # NBA.com a corrigé son dernier match
                hit = x["games"][key][str(gp)]
                hit[0] += ast - old[1]
                hit[1] += pot - old[2]
            x["totals"][key] = [gp, ast, pot]
        for rows in fresh.values():
            if sum(r[3] for r in rows) <= TEAM_MAX:  # sinon, match laissé vide (Thunder-Nuggets du 22/10 : 3 fois trop)
                for key, gp, a, p in rows:
                    x["games"].setdefault(key, {})[str(gp)] = [a, p]
        x["last"] = at
        read += 1
    x["checked"] = iso(now())
    return read


def by_game(x, key, s):
    """xA de chaque match du journal ESPN `s` (None si inconnu). Le match est retrouvé par son rang dans la saison ;
    le nombre de passes doit être le même des deux côtés, sinon on préfère ne rien afficher."""
    games = (x or {}).get("games", {}).get(key, {})
    out = []
    for i, row in enumerate(s["g"]):
        hit = games.get(str(s.get("sn", 0) - i)) if i < s["n"] - s["prev"] else None
        out.append(hit[1] if hit and hit[0] == row[2] else None)
    return out
