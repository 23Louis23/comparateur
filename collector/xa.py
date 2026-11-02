"""xA par match : la colonne « Potential AST » de NBA.com (passes qui mènent à un tir, réussi ou non).

NBA.com refuse les lectures automatiques (protection Akamai) : on ne cherche pas à passer. Le projet public
gabriel1200/site_Data relève chaque jour les totaux de la saison sur GitHub ; l'écart entre deux relevés donne le
chiffre d'un match, à condition qu'un seul match les sépare. Sinon, le match reste sans valeur."""
import csv
import io
from datetime import datetime, timedelta

from common import get, get_json, iso, norm_name, now
from stats import season_year

REPO = "gabriel1200/site_Data"
COMMITS = "https://api.github.com/repos/%s/commits?path=tracking/passing.csv&since=%%s&per_page=100" % REPO
RAW = "https://raw.githubusercontent.com/%s/%%s/tracking/passing.csv" % REPO
CHECK_HOURS = 3


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
        for r in csv.DictReader(io.StringIO(get(RAW % c["sha"]))):
            if r["year"] != str(season):
                continue
            key = norm_name(r["PLAYER_NAME"])
            gp, ast, pot = int(r["GP"]), int(r["AST"]), int(r["POTENTIAL_AST"])
            old = x["totals"].get(key, [0, 0, 0])
            if gp == old[0] + 1 and pot >= old[2]:
                x["games"].setdefault(key, {})[str(gp)] = [ast - old[1], pot - old[2]]
            x["totals"][key] = [gp, ast, pot]
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
