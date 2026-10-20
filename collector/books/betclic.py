"""Betclic : la page NBA liste les matchs ; la page de chaque match embarque (script #ng-state)
les marchés de l'onglet « Le Top ». Les autres onglets passent par une connexion temps réel
binaire qu'on ne lit pas : on récupère donc surtout les points du joueur (+/-) et, quand ils
sont présents, les paliers et le P+R+A."""
import json
import re
from datetime import datetime, timedelta

from common import Game, get, over_cut, now

NAME = "betclic"
BASE = "https://www.betclic.fr"
LIST = BASE + "/basketball-sbasketball/nba-c13"
STATS = [  # motif sur le nom du marché → statistique
    (r"pts\+reb\+pas", "pra"), (r"points du joueur", "pts"), (r"rebonds du joueur", "reb"),
    (r"passes d\S* du joueur", "ast"), (r"3 points du joueur|paniers? à 3", "3pt"),
]


def state(html):
    m = re.search(r'<script[^>]*id="ng-state"[^>]*>(.*?)</script>', html, re.S)
    return json.loads(m.group(1)) if m else {}


def payloads(st):
    for v in st.values():
        if isinstance(v, dict) and isinstance(v.get("response"), dict):
            p = v["response"].get("payload")
            if isinstance(p, dict):
                yield p


def fetch(memo, horizon_hours=48):
    html = get(LIST)
    links = dict(re.findall(r'href="(/basketball-sbasketball/nba-c13/[a-z0-9-]+-m(\d+))"', html))
    links = {mid: path for path, mid in links.items()}
    matches = [m for p in payloads(state(html)) for m in p.get("matches") or []]
    games = []
    for mt in matches:
        start = datetime.fromisoformat(mt["matchDateUtc"][:19] + "+00:00")
        path = links.get(mt["matchId"])
        if not path or not (now() - timedelta(hours=3) < start < now() + timedelta(hours=horizon_hours)):
            continue
        home, _, away = mt["name"].partition(" - ")
        g = Game(start, home, away)
        for p in payloads(state(get(BASE + path))):
            match = p.get("match") or {}
            for sc in match.get("subCategories") or []:
                for mk in sc.get("markets") or []:
                    stat = next((s for pat, s in STATS if re.search(pat, mk.get("name", ""), re.I)), None)
                    if not stat:
                        continue
                    for row in mk.get("selectionMatrix") or []:
                        for sel in row.get("selections") or []:
                            s = (sel.get("selectionOneof") or {}).get("selection") or {}
                            if s.get("status") != 1:
                                continue
                            name = (s.get("name") or "").strip()
                            over = re.match(r"^(.*\S)\s+\+\s*de\s+([\d.,]+)$", name)
                            mile = re.match(r"^(.*\S)\s+(\d+)\s*\+$", name) or re.match(r"^(.*\S)\s*:\s*(\d+)\s*\+", name)
                            if over:
                                g.add(over.group(1), stat, over_cut(over.group(2)), s.get("odds"))
                            elif mile:
                                g.add(mile.group(1), stat, int(mile.group(2)), s.get("odds"))
        games.append(g)
    return games
