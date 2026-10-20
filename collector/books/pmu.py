"""PMU : offre servie par la plateforme Kambi (flux JSON public de pmu.fr, client « pmusportsfr »).
Liste des matchs NBA, puis l'offre complète de chaque match."""
import json
import re
from datetime import datetime, timedelta

from common import Game, get, over_cut, now

NAME = "pmu"
API = "https://eu1.offering-api.kambicdn.com/offering/v2018/pmusportsfr"
PARAMS = "lang=fr_FR&market=FR"
STATS = [  # motif sur le libellé du marché (le plus spécifique d'abord) → statistique
    (r"points, rebonds & passes", "pra"), (r"points & rebonds", "pr"), (r"points & passes", "pa"),
    (r"rebonds & passes", "ra"), (r"interceptions & contres", "sb"), (r"triple-double", "td"),
    (r"double-double", "dd"), (r"paniers à (3|trois) points", "3pt"), (r"points marqués", "pts"),
    (r"rebonds", "reb"), (r"passes décisives", "ast"), (r"interceptions", "stl"), (r"contres", "blk"),
    (r"balles perdues", "tov"),
]


def fetch(memo, horizon_hours=48):
    events = json.loads(get("%s/listView/basketball/nba/all/all/matches.json?%s&useCombined=true" % (API, PARAMS)))
    games = []
    for item in events.get("events", []):
        ev = item["event"]
        start = datetime.fromisoformat(ev["start"].replace("Z", "+00:00"))
        if ev.get("state") != "NOT_STARTED" or not (now() < start < now() + timedelta(hours=horizon_hours)):
            continue
        offer = json.loads(get("%s/betoffer/event/%s.json?%s" % (API, ev["id"], PARAMS)))
        g = Game(start, ev.get("homeName"), ev.get("awayName"))
        for b in offer.get("betOffers", []):
            label = b["criterion"]["label"]
            if "Prolongations incluses" not in label or "joueur" not in label and "double" not in label:
                continue
            stat = next((s for pat, s in STATS if re.search(pat, label, re.I)), None)
            if not stat:
                continue
            milestone = re.match(r"^(\d+)\+", label)
            for o in b.get("outcomes", []):
                if o.get("status") != "OPEN" or not o.get("participant"):
                    continue
                odds = o.get("odds", 0) / 1000
                if o.get("type") in ("OT_OVER", "OT_UNDER") and o.get("line") is not None:
                    side = "over" if o["type"] == "OT_OVER" else "under"
                    g.add_line(o["participant"], stat, o["line"] / 1000, side, odds)
                    if side == "over":
                        g.add(o["participant"], stat, over_cut(o["line"] / 1000), odds)
                elif o.get("type") == "OT_YES":
                    cut = 1 if stat in ("dd", "td") else int(milestone.group(1)) if milestone else None
                    g.add(o["participant"], stat, cut, odds)
        games.append(g)
    return games
