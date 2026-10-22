"""PokerStars : la page de chaque match est rendue côté serveur avec l'onglet « Populaire »
(paliers de points, double-double, triple-double). Les autres onglets (rebonds, passes…)
arrivent ensuite par une connexion temps réel qu'on ne lit pas : pour ces stats-là, le
collecteur garde l'appoint OddsPapi."""
import html as htmllib
import re
from datetime import datetime, timedelta

from common import Game, get, now

NAME = "pokerstars"
BASE = "https://www.pokerstars.fr"
NBA = BASE + "/sports/basketball/7522/nba/10547864/"
TABLES = {"Points": "pts", "Rebonds": "reb", "Passes décisives": "ast", "Trois points": "3pt"}
LISTS = {"Réalise un double double": "dd", "Réalise un triple double": "td"}


def text(s):
    return htmllib.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def fetch(memo, horizon_hours=48):
    page = get(NBA)
    teams = {}
    for href, label in re.findall(r'<a[^>]+href="(/sports/basketball/7522/nba/10547864/[a-z0-9-]+/\d+/)"[^>]*>(.*?)</a>', page, re.S):
        name = text(label)
        if name and name not in teams.setdefault(href, []):
            teams[href].append(name)
    games = []
    for href, names in teams.items():
        if len(names) < 2:
            continue
        known = memo.get(href)
        if known and not (now() < datetime.fromisoformat(known) < now() + timedelta(hours=horizon_hours)):
            continue  # match lointain ou commencé : inutile d'ouvrir sa page
        h = get(BASE + href)
        m = re.search(r'"eventTime":"([^"]+)"', h)
        if not m:
            continue
        start = datetime.fromisoformat(m.group(1).replace("Z", "+00:00"))
        memo[href] = start.isoformat()
        if not (now() < start < now() + timedelta(hours=horizon_hours)):
            continue
        g = Game(start, home=names[1], away=names[0])  # PokerStars affiche « Extérieur @ Domicile »
        g.url = BASE + href
        for block in h.split('data-testid="sports-expandable-accordion"')[1:]:
            title = htmllib.unescape((re.search(r'aria-label="([^"]+)"', block) or [None, ""])[1])
            if title in TABLES:
                cuts = [int(c) for c in re.findall(r'<th scope="col">(\d+)(?:<!-- -->)?\+</th>', block)]
                for row in re.findall(r"<tr><th scope=\"row\"[^>]*>(.*?)</tr>", block, re.S):
                    player = text(row.split("</th>")[0])
                    cells = re.findall(r"<td>(.*?)</td>", row, re.S)
                    for cut, cell in zip(cuts, cells):
                        odds = re.search(r"<strong>([\d,]+)</strong>", cell)
                        if odds:
                            g.add(player, TABLES[title], cut, odds.group(1))
            elif title in LISTS:
                for player, odds in re.findall(r"<li[^>]*>.*?<span[^>]*>([^<]+)</span>.*?<strong>([\d,]+)</strong>", block, re.S):
                    g.add(text(player), LISTS[title], 1, odds)
        games.append(g)
    return games
