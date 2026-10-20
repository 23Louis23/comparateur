"""Winamax : la page de la NBA puis la page de chaque match embarquent tout l'état
(var PRELOADED_STATE = {...}) : matchs, paris, issues et cotes."""
import json
import re
from datetime import datetime, timedelta, timezone

from common import Game, get, over_cut, now

NAME = "winamax"
BASE = "https://www.winamax.fr/paris-sportifs"
NBA = BASE + "/sports/2/800000076/177"
STATS = [  # début du titre du pari → statistique
    ("Total du joueur (points + rebonds + passes)", "pra"), ("Total du joueur (points + rebonds)", "pr"),
    ("Total du joueur (points + passes)", "pa"), ("Total du joueur (passes + rebonds)", "ra"),
    ("Total du joueur (interceptions + contres)", "sb"), ("Nombre de points du joueur", "pts"),
    ("Nombre de rebonds du joueur", "reb"), ("Nombre de passes décisives du joueur", "ast"),
    ("Nombre de paniers à 3 points du joueur", "3pt"), ("Nombre d'interceptions du joueur", "stl"),
    ("Nombre de contres du joueur", "blk"), ("Double-double", "dd"), ("Triple-double", "td"),
]


def state(html):
    m = re.search(r"var PRELOADED_STATE\s*=\s*(\{.*?\});\s*(?:var |</script>)", html, re.S)
    return json.loads(m.group(1)) if m else {}


def fetch(memo, horizon_hours=48):
    games = []
    for mt in state(get(NBA)).get("matches", {}).values():
        if mt.get("tournamentId") != 177 or mt.get("status") != "PREMATCH":
            continue
        start = datetime.fromtimestamp(mt["matchStart"], timezone.utc)
        if not (now() < start < now() + timedelta(hours=horizon_hours)):
            continue
        st = state(get("%s/match/%s" % (BASE, mt["matchId"])))
        bets, outcomes, odds = st.get("bets", {}), st.get("outcomes", {}), st.get("odds", {})
        g = Game(start, mt["competitor1Name"], mt["competitor2Name"])
        for b in bets.values():
            if b.get("matchId") != mt["matchId"] or not b.get("available"):
                continue
            title = b.get("betTitle") or ""
            stat = next((s for prefix, s in STATS if title.startswith(prefix)), None)
            if not stat:
                continue
            player = title.rsplit(" - ", 1)[-1] if " - " in title else None
            for oid in b.get("outcomes") or []:
                o = outcomes.get(str(oid)) or {}
                if not o.get("available", True):
                    continue
                label = o.get("label") or ""
                ou = re.match(r"^(Plus|Moins) de ([\d.,]+)$", label)
                if ou and player:
                    if "(paliers)" not in title:
                        g.add_line(player, stat, ou.group(2), "over" if ou.group(1) == "Plus" else "under", odds.get(str(oid)))
                    if ou.group(1) == "Plus":
                        g.add(player, stat, over_cut(ou.group(2)), odds.get(str(oid)))
                elif stat in ("dd", "td") and not label.startswith(("Plus", "Moins", "Non")):
                    g.add(player if label in ("Oui", "Yes") else label, stat, 1, odds.get(str(oid)))
        games.append(g)
    return games
