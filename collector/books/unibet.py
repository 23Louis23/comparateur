"""Unibet (plateforme FDJ « psel ») : liste des matchs NBA puis page de chaque match,
dont le HTML embarque toutes les cotes (script #serverApp-state)."""
import json
import re
from datetime import datetime, timedelta, timezone

from common import Game, get, over_cut, now

NAME = "unibet"
BASE = "https://www.unibet.fr"

# groupe de marchés (id, sinon motif sur le libellé) → statistique
MILESTONES = {  # « Jayson Tatum 25+ »
    3624: "pts", 3684: "reb", 3683: "ast", 3682: "3pt", 3685: "blk", 3686: "stl", 3623: "dd", 3622: "td",
}
MILESTONE_LABELS = [
    (r"^Performance Joueur Points", "pts"), (r"Rebonds - Match$", "reb"), (r"Passes D", "ast"),
    (r"Panier à 3", "3pt"), (r"Contres", "blk"), (r"Interceptions", "stl"),
    (r"^Double Double", "dd"), (r"^Triple Double", "td"),
]
COMBOS = {2295: "pra", 2296: "pr", 2297: "pa", 2298: "ra"}  # « Cade Cunningham + de 36.5 »
OVER_UNDER = {3102: "pts", 1593: "reb", 1592: "ast", 2805: "3pt"}  # marché « … - Jordan Walsh », issue « Plus 4.5 »


def events():
    """Liens des matchs depuis la page NBA (la liste via l'API demande un jeton de session)."""
    html = get(BASE + "/paris-basketball/usa/nba")
    return sorted(set(re.findall(r"/paris-basketball/usa/nba/(\d+)/[a-z0-9-]*-vs-[a-z0-9-]*", html)))


def match_url(eid, slug="match"):
    return "%s/paris-basketball/usa/nba/%s/%s" % (BASE, eid, slug)


def classify(group):
    gid, label = group.get("id"), group.get("description") or ""
    if gid in COMBOS:
        return "combo", COMBOS[gid]
    if gid in OVER_UNDER:
        return "ou", OVER_UNDER[gid]
    if gid in MILESTONES:
        return "milestone", MILESTONES[gid]
    if "Total Points + Rebonds + Passes" in label:
        return "combo", "pra"
    for pat, stat in MILESTONE_LABELS:
        if re.search(pat, label) and "Plus / Moins" not in label:
            return "milestone", stat
    return None, None


def fetch(memo, horizon_hours=48):
    """memo : {id du match: heure ISO}, conservé d'un passage à l'autre par le collecteur."""
    games = []
    for eid in events():
        known = memo.get(eid)
        if known and not (now() - timedelta(hours=3) < datetime.fromisoformat(known) < now() + timedelta(hours=horizon_hours)):
            continue  # match lointain ou terminé : inutile d'ouvrir sa page
        html = get("%s/paris-basketball/usa/nba/%s/match" % (BASE, eid))
        m = re.search(r'<script[^>]*id="serverApp-state"[^>]*>(.*?)</script>', html, re.S)
        evs = (json.loads(m.group(1)).get("EventsDetail", {}).get("events") or []) if m else []
        if not evs:
            continue
        ev = evs[0]
        start = datetime.fromisoformat(ev["parsedStart"].replace("Z", "+00:00"))
        memo[eid] = start.isoformat()
        if not (now() - timedelta(hours=3) < start < now() + timedelta(hours=horizon_hours)):
            continue
        a, _, b = (ev.get("description") or "").partition(" vs ")
        g = Game(start, home=a, away=b)  # Unibet affiche « Domicile vs Extérieur »
        g.url = match_url(eid, re.sub(r"[^a-z0-9]+", "-", (ev.get("description") or "match").lower()).strip("-"))
        for group in ev.get("groupedMarkets") or []:
            kind, stat = classify(group)
            if not kind:
                continue
            for market in group.get("markets") or []:
                for o in market.get("outcomes") or []:
                    if o.get("suspended") or o.get("hidden") or str(o.get("eventId", eid)) != str(eid):
                        continue
                    d = (o.get("description") or "").strip()
                    if kind == "milestone":
                        mm = re.match(r"^(.*\S)\s+(\d+)\+$", d)
                        if mm:
                            g.add(mm.group(1), stat, int(mm.group(2)), o.get("price"))
                    elif kind == "combo":  # « Cade Cunningham + de 36.5 » / « - de 36.5 »
                        mm = re.match(r"^(.*\S)\s+([+-])\s*de\s+([\d.,]+)$", d)
                        if mm:
                            side = "over" if mm.group(2) == "+" else "under"
                            g.add_line(mm.group(1), stat, mm.group(3), side, o.get("price"))
                            if side == "over":
                                g.add(mm.group(1), stat, over_cut(mm.group(3)), o.get("price"))
                    elif kind == "ou" and d.startswith(("Plus", "Moins")):
                        player = (market.get("description") or "").rsplit(" - ", 1)[-1]
                        side = "over" if d.startswith("Plus") else "under"
                        g.add_line(player, stat, o.get("spread"), side, o.get("price"))
                        if side == "over":
                            g.add(player, stat, over_cut(o.get("spread")), o.get("price"))
        games.append(g)
    return games
