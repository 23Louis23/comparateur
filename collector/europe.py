#!/usr/bin/env python3
"""
Basket européen — alertes uniquement (pas d'interface) : écart de cote ≥ 50 % entre deux
bookmakers sur une même sélection, écart de ligne sur un même joueur, et veille des handicaps
(écart ≥ 3 points entre bookmakers, ou handicap inversé : le favori reçoit des points). La NBA
passe aussi ici, mais seulement pour le vainqueur et le handicap (ses props sont suivies par collect.py). Euroligue, Eurocoupe, Betclic Élite, Grèce A1, Turquie BSL,
ligue Adriatique, Allemagne BBL, Espagne ACB, Italie Lega A, BCL… (compétitions féminines,
NBA, WNBA, NCAA et Australie exclues).

Bookmakers : PMU (flux Kambi), Winamax, Unibet, Betclic (pages rendues côté serveur).
Sélections comparées (clés normalisées, côté « home »/« away » de chaque bookmaker) :
  win|home                 vainqueur (prolongations incluses)
  tot|160.5|over           total de points
  hcp|home|-8.5            handicap de l'équipe
  tt|home|80.5|over        points d'une équipe
  p|<joueur>|pts|25        palier joueur (25+) ; pl|<joueur>|pts|24.5|over : ligne joueur

Usage : python3 collector/europe.py [--dry]   (--dry : affiche les alertes sans les envoyer)
"""
import html as htmllib
import json
import re
import sys
import traceback
import unicodedata
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "books"))

import alerts  # noqa: E402
import pmu  # noqa: E402
import unibet  # noqa: E402
import winamax  # noqa: E402
from common import get, iso, norm_name, now, over_cut, price  # noqa: E402

EDGE = 1.50          # meilleure cote ≥ autre cote × 1,50
MAX_ODDS = 6.0       # au-delà, les écarts entre bookmakers sont normaux
HCP_GAP = 3          # écart entre les handicaps équilibrés de deux bookmakers
HCP_FAV = 1.60       # favori net (cote moyenne au vainqueur)…
HCP_FLIP = 1.5       # …à qui un bookmaker donne pourtant au moins +1,5 point : handicap inversé
HORIZON_H = 48
REPEAT_H = 6
STATE = HERE / "build" / "europe-state.json"
EXCLUDE = re.compile(r"\bNBA\b|WNBA|NCAA|\bNBL\b|Australie|Chine|Japon|Cor[ée]e|Philippines|\(F\)|F[ée]minin|Femmes|Women|LFB|"
                     r"Cotes des S[ée]ries|March Madness|\sF\.(?:\s|$)", re.I)
STAT_FR = {"pts": "points", "reb": "rebonds", "ast": "passes", "3pt": "paniers à 3 pts", "pra": "pts+reb+pas",
           "pr": "pts+reb", "pa": "pts+pas", "ra": "reb+pas", "stl": "interceptions", "blk": "contres",
           "sb": "int+contres", "tov": "balles perdues", "dd": "double-double", "td": "triple-double"}
BOOK_NAMES = {"pmu": "PMU", "winamax": "Winamax", "unibet": "Unibet", "betclic": "Betclic"}


def log(*a):
    print(datetime.now().strftime("%H:%M:%S"), "[europe]", *a, flush=True)


def page(url):
    """Page d'un match ; une erreur passagère du site (5xx) fait sauter ce match, pas tout le bookmaker."""
    try:
        return get(url)
    except urllib.error.HTTPError as e:
        if e.code >= 500:
            log("page ignorée (%s) : %s" % (e.code, url.split("/")[2]))
            return None
        raise


def num(s):
    return float(str(s).replace(",", ".").replace("+", ""))


def key_line(x):
    return "%g" % num(x)


class EGame:
    def __init__(self, book, start, home, away, league, url, nba=False):
        self.book, self.start, self.home, self.away, self.league, self.url = book, start, home, away, league, url
        self.nba = nba
        self.sel = {}

    def add(self, key, odds):
        o = price(odds)
        if o:
            self.sel[key] = max(o, self.sel.get(key, 0))

    def side(self, team):
        """Nom d'équipe tel qu'écrit dans un marché → « home » / « away » (ou None)."""
        t = tokens(team)
        h, a = len(t & tokens(self.home)), len(t & tokens(self.away))
        if h == a:  # départage par abréviation
            h, a = int(close(t, tokens(self.home))), int(close(t, tokens(self.away)))
        return "home" if h > a else "away" if a > h else None


# ------------------------------------------------------------------ rapprochement des équipes
GENERIC = {"bc", "kk", "sk", "ks", "bk", "kb", "cb", "ab", "as", "ao", "pao", "fc", "cd", "ud", "sl", "club", "basket",
           "basketball", "baloncesto", "pallacanestro", "the", "de", "la", "le", "les", "di", "del", "city", "men", "team"}
ALIAS = {"athenes": "athens", "piree": "piraeus", "olympiakos": "olympiacos", "belgrade": "beograd", "munich": "munchen",
         "crvena": "zvezda", "etoile": "zvezda", "saragosse": "zaragoza", "murcie": "murcia", "andorre": "andorra",
         "grenade": "granada", "groningue": "groningen", "prometheus": "promitheas", "barcelone": "barcelona",
         "seville": "sevilla", "valence": "valencia", "malaga": "malaga", "majorque": "mallorca", "lisbonne": "lisboa",
         "bourgenbresse": "bourg", "mitteldeutscher": "mbc", "venise": "venezia", "milan": "milano", "naples": "napoli",
         "turin": "torino", "trente": "trento", "florence": "firenze", "bologne": "bologna", "cologne": "koln",
         "vienne": "vienna", "wien": "vienna", "prague": "praha", "varsovie": "warszawa", "anvers": "antwerp"}


def tokens(name):
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    out = set()
    for w in re.findall(r"[a-z0-9]+", s):
        w = ALIAS.get(w, w)
        if w not in GENERIC and len(w) > 1:
            out.add(w)
    return out


def close(x, y):
    """Un mot en commun, abréviations comprises (« subo » ↔ « subotica », « spar » ↔ « spartak »)."""
    return bool(x & y) or any(len(u) >= 4 and len(v) >= 4 and (u.startswith(v) or v.startswith(u)) for u in x for v in y)


def same_game(a, b):
    """→ (même match ?, orientation inversée ?)."""
    if abs((a.start - b.start).total_seconds()) > 2.5 * 3600:
        return False, False
    ah, aa, bh, ba = tokens(a.home), tokens(a.away), tokens(b.home), tokens(b.away)
    if close(ah, bh) and close(aa, ba):
        return True, False
    if close(ah, ba) and close(aa, bh):
        return True, True
    return False, False


def swap(key):
    return key.replace("|home|", "|@@|").replace("|away|", "|home|").replace("|@@|", "|away|") \
        .replace("win|home", "win|@@").replace("win|away", "win|home").replace("win|@@", "win|away")


# ------------------------------------------------------------------ PMU (Kambi)
def fetch_pmu():
    data = json.loads(get("%s/listView/basketball/all/all/all/matches.json?%s&useCombined=true" % (pmu.API, pmu.PARAMS)))
    games = []
    for item in data.get("events", []):
        ev = item["event"]
        league = " / ".join(p.get("name", "") for p in ev.get("path", [])[1:])
        start = datetime.fromisoformat(ev["start"].replace("Z", "+00:00"))
        nba = league == "NBA"
        if (EXCLUDE.search(league) and not nba) or ev.get("state") != "NOT_STARTED" or not now() < start < now() + timedelta(hours=HORIZON_H):
            continue
        g = EGame("pmu", start, ev.get("homeName"), ev.get("awayName"), league, "https://www.pmu.fr/sport/event/%s" % ev["id"], nba)
        raw = page("%s/betoffer/event/%s.json?%s" % (pmu.API, ev["id"], pmu.PARAMS))
        if raw is None:
            continue
        offer = json.loads(raw)
        for b in offer.get("betOffers", []):
            label = b["criterion"]["label"]
            if "Prolongations incluses" not in label:
                continue
            outs = [o for o in b.get("outcomes", []) if o.get("status") == "OPEN"]
            if label.startswith("Cotes du match"):
                for o in outs:
                    g.add("win|%s" % {"OT_ONE": "home", "OT_TWO": "away"}.get(o.get("type"), "?"), o["odds"] / 1000)
            elif label.startswith("Total de points"):
                for o in outs:
                    if o.get("type") in ("OT_OVER", "OT_UNDER"):
                        g.add("tot|%s|%s" % (key_line(o["line"] / 1000), "over" if o["type"] == "OT_OVER" else "under"), o["odds"] / 1000)
            elif label.startswith("Handicap"):
                for o in outs:
                    s = {"OT_ONE": "home", "OT_TWO": "away"}.get(o.get("type"))
                    if s and o.get("line") is not None:
                        g.add("hcp|%s|%s" % (s, key_line(o["line"] / 1000)), o["odds"] / 1000)
            elif label.startswith("Nombre total de points de "):
                s = g.side(label[len("Nombre total de points de "):].split(" - ")[0])
                for o in outs:
                    if s and o.get("type") in ("OT_OVER", "OT_UNDER"):
                        g.add("tt|%s|%s|%s" % (s, key_line(o["line"] / 1000), "over" if o["type"] == "OT_OVER" else "under"), o["odds"] / 1000)
            elif "joueur" in label:
                stat = next((st for pat, st in pmu.STATS if re.search(pat, label, re.I)), None)
                mile = re.match(r"^(\d+)\+", label)
                for o in outs:
                    if not stat or not o.get("participant"):
                        continue
                    who = norm_name(o["participant"])
                    if o.get("type") in ("OT_OVER", "OT_UNDER") and o.get("line") is not None:
                        g.add("pl|%s|%s|%s|%s" % (who, stat, key_line(o["line"] / 1000), "over" if o["type"] == "OT_OVER" else "under"), o["odds"] / 1000)
                    elif o.get("type") == "OT_YES" and mile:
                        g.add("p|%s|%s|%s" % (who, stat, mile.group(1)), o["odds"] / 1000)
        games.append(g)
    return games


# ------------------------------------------------------------------ Winamax
def fetch_winamax():
    st = winamax.state(get(winamax.BASE + "/sports/2"))
    tours = st.get("tournaments", {})
    games = []
    for mt in st.get("matches", {}).values():
        if mt.get("sportId") != 2 or mt.get("status") != "PREMATCH":
            continue
        league = (tours.get(str(mt.get("tournamentId"))) or {}).get("tournamentName", "")
        start = datetime.fromtimestamp(mt["matchStart"], timezone.utc)
        nba = mt.get("tournamentId") == 177
        if (EXCLUDE.search(league) and not nba) or not now() < start < now() + timedelta(hours=HORIZON_H):
            continue
        g = EGame("winamax", start, mt["competitor1Name"], mt["competitor2Name"], league, "%s/match/%s" % (winamax.BASE, mt["matchId"]), nba)
        raw = page("%s/match/%s" % (winamax.BASE, mt["matchId"]))
        if raw is None:
            continue
        ms = winamax.state(raw)
        outcomes, odds = ms.get("outcomes", {}), ms.get("odds", {})
        for b in ms.get("bets", {}).values():
            if b.get("matchId") != mt["matchId"] or not b.get("available"):
                continue
            title = b.get("betTitle") or ""
            outs = [(outcomes.get(str(o)) or {}, odds.get(str(o))) for o in b.get("outcomes") or []]
            outs = [(o, x) for o, x in outs if o.get("available", True)]
            if title == "Vainqueur":
                for o, x in outs:
                    s = {"1": "home", "2": "away"}.get(str(o.get("code"))) or g.side(o.get("label"))
                    if s:
                        g.add("win|" + s, x)
            elif title == "Écart de points (handicap)":
                for o, x in outs:
                    m = re.match(r"^(.*\S)\s+([+-][\d.,]+)$", o.get("label") or "")
                    s = m and g.side(m.group(1))
                    if s:
                        g.add("hcp|%s|%s" % (s, key_line(m.group(2))), x)
            elif title == "Nombre de points" or title.startswith("Nombre de points de "):
                s = g.side(title[len("Nombre de points de "):]) if title != "Nombre de points" else None
                for o, x in outs:
                    m = re.match(r"^(Plus|Moins) de ([\d.,]+)$", o.get("label") or "")
                    if m and (title == "Nombre de points" or s):
                        side = "over" if m.group(1) == "Plus" else "under"
                        g.add(("tot|%s|%s" % (key_line(m.group(2)), side)) if title == "Nombre de points"
                              else ("tt|%s|%s|%s" % (s, key_line(m.group(2)), side)), x)
            else:
                stat = next((s2 for prefix, s2 in winamax.STATS if title.startswith(prefix)), None)
                player = norm_name(title.rsplit(" - ", 1)[-1]) if stat and " - " in title else None
                for o, x in outs:
                    m = re.match(r"^(Plus|Moins) de ([\d.,]+)$", o.get("label") or "")
                    if player and m:
                        if "(paliers)" in title and m.group(1) == "Plus":
                            g.add("p|%s|%s|%d" % (player, stat, over_cut(m.group(2))), x)
                        elif "(paliers)" not in title:
                            g.add("pl|%s|%s|%s|%s" % (player, stat, key_line(m.group(2)), "over" if m.group(1) == "Plus" else "under"), x)
        games.append(g)
    return games


# ------------------------------------------------------------------ Unibet
def fetch_unibet():
    home_page = get(unibet.BASE + "/paris-basketball")
    leagues = sorted(set(re.findall(r'href="/paris-basketball/([a-z0-9-]+/[a-z0-9-]+)/\d+/', home_page)))
    links = set(re.findall(r'href="(/paris-basketball/[a-z0-9-]+/[a-z0-9-]+/(\d+)/[a-z0-9-]+-vs-[a-z0-9-]+)"', home_page))
    for lg in leagues:
        if lg.startswith("usa/") or EXCLUDE.search(lg.replace("-", " ")) or lg.endswith("-f") or "femmes" in lg:
            continue
        links |= set(re.findall(r'href="(/paris-basketball/%s/(\d+)/[a-z0-9-]+-vs-[a-z0-9-]+)"' % re.escape(lg), get(unibet.BASE + "/paris-basketball/" + lg)))
    links |= set(re.findall(r'href="(/paris-basketball/usa/nba/(\d+)/[a-z0-9-]+-vs-[a-z0-9-]+)"', get(unibet.BASE + "/paris-basketball/usa/nba")))
    games = []
    for path, eid in sorted(links):
        lg = path.split("/")[2] + "/" + path.split("/")[3]
        nba = lg == "usa/nba"
        if not nba and (lg.startswith("usa/") or lg.endswith("-f") or "femmes" in lg or EXCLUDE.search(lg.replace("-", " ").replace("/", " "))):
            continue
        raw = page(unibet.BASE + path)
        m = raw and re.search(r'<script[^>]*id="serverApp-state"[^>]*>(.*?)</script>', raw, re.S)
        evs = (json.loads(m.group(1)).get("EventsDetail", {}).get("events") or []) if m else []
        if not evs:
            continue
        ev = evs[0]
        start = datetime.fromisoformat(ev["parsedStart"].replace("Z", "+00:00"))
        if not now() < start < now() + timedelta(hours=HORIZON_H):
            continue
        a, _, b = (ev.get("description") or "").partition(" vs ")
        g = EGame("unibet", start, a, b, lg, unibet.BASE + path, nba)
        regular = {}  # handicap « temps réglementaire » : seulement si celui du match (prolongations incluses) manque (NBA)
        for group in ev.get("groupedMarkets") or []:
            gd = group.get("description") or ""
            kind, stat = unibet.classify(group)
            for market in group.get("markets") or []:
                md = market.get("description") or ""
                for o in market.get("outcomes") or []:
                    if o.get("suspended") or o.get("hidden") or str(o.get("eventId", eid)) != str(eid):
                        continue
                    d = (o.get("description") or "").strip()
                    if gd == "Face à Face - Match":
                        s = g.side(d)
                        if s:
                            g.add("win|" + s, o.get("price"))
                    elif gd == "Plus / Moins Points - Match":
                        side = "over" if d.startswith("Plus") else "under" if d.startswith("Moins") else None
                        if side and o.get("spread") is not None:
                            g.add("tot|%s|%s" % (key_line(o["spread"]), side), o.get("price"))
                    elif gd == "Plus / Moins Points - Equipe - Match":
                        tm = re.match(r"^Plus / Moins Point\(s\) - (.*\S)\s+[\d.,]+$", md)
                        s = tm and g.side(tm.group(1))
                        side = "over" if d.startswith("Plus") else "under" if d.startswith("Moins") else None
                        if s and side and o.get("spread") is not None:
                            g.add("tt|%s|%s|%s" % (s, key_line(o["spread"]), side), o.get("price"))
                    elif gd in ("Face à Face Handicap Points - Match", "Face à Face Handicap Points - TR"):
                        s = g.side(d.split(" [")[0])
                        if s and o.get("spread") is not None:
                            key = "hcp|%s|%s" % (s, key_line(o["spread"]))
                            if gd.endswith("Match"):
                                g.add(key, o.get("price"))
                            else:
                                regular[key] = o.get("price")
                    elif kind == "milestone":
                        mm = re.match(r"^(.*\S)\s+(\d+)\+$", d)
                        if mm:
                            g.add("p|%s|%s|%s" % (norm_name(mm.group(1)), stat, mm.group(2)), o.get("price"))
                    elif kind == "combo":
                        mm = re.match(r"^(.*\S)\s+([+-])\s*de\s+([\d.,]+)$", d)
                        if mm:
                            g.add("pl|%s|%s|%s|%s" % (norm_name(mm.group(1)), stat, key_line(mm.group(3)), "over" if mm.group(2) == "+" else "under"), o.get("price"))
                    elif kind == "ou" and d.startswith(("Plus", "Moins")):
                        g.add("pl|%s|%s|%s|%s" % (norm_name(md.rsplit(" - ", 1)[-1]), stat, key_line(o.get("spread")), "over" if d.startswith("Plus") else "under"), o.get("price"))
        if not any(k.startswith("hcp|") for k in g.sel):
            for key, o in regular.items():
                g.add(key, o)
        games.append(g)
    return games


# ------------------------------------------------------------------ Betclic (onglet « Le Top » rendu côté serveur)
def bc_state(html):
    m = re.search(r'<script[^>]*id="ng-state"[^>]*>(.*?)</script>', html, re.S)
    st = json.loads(m.group(1)) if m else {}
    for v in st.values():
        if isinstance(v, dict) and isinstance(v.get("response"), dict) and isinstance(v["response"].get("payload"), dict):
            yield v["response"]["payload"]


def fetch_betclic():
    base = "https://www.betclic.fr"
    top = get(base + "/basketball-sbasketball")
    links = {mid: path for path, mid in re.findall(r'href="(/basketball-sbasketball/[a-z0-9-]+-c\d+/[a-z0-9-]+-m(\d+))"', top)}
    comps = {re.match(r"(/basketball-sbasketball/[a-z0-9-]+-c\d+)/", p).group(1) for p in links.values()}
    comps.add("/basketball-sbasketball/nba-c13")  # absente de la page d'accueil basket
    matches = {m["matchId"]: m for p in bc_state(top) for m in p.get("matches") or []}
    for c in comps:
        if "wnba" in c:
            continue
        cp = get(base + c)
        links.update({mid: path for path, mid in re.findall(r'href="(/basketball-sbasketball/[a-z0-9-]+-c\d+/[a-z0-9-]+-m(\d+))"', cp)})
        matches.update({m["matchId"]: m for p in bc_state(cp) for m in p.get("matches") or []})
    games = []
    for mid, mt in matches.items():
        league = (mt.get("competition") or {}).get("name", "")
        start = datetime.fromisoformat(mt["matchDateUtc"][:19] + "+00:00")
        nba = mid in links and "/nba-c13/" in links[mid]
        if mid not in links or (EXCLUDE.search(league) and not nba) or not now() < start < now() + timedelta(hours=HORIZON_H):
            continue
        home, _, away = mt["name"].partition(" - ")
        g = EGame("betclic", start, home, away, league, base + links[mid], nba)
        raw = page(base + links[mid])
        if raw is None:
            continue
        for p in bc_state(raw):
            for sc in (p.get("match") or {}).get("subCategories") or []:
                for mk in sc.get("markets") or []:
                    name = mk.get("name") or ""
                    for row in mk.get("selectionMatrix") or []:
                        for sel in row.get("selections") or []:
                            s = (sel.get("selectionOneof") or {}).get("selection") or {}
                            if s.get("status") != 1:
                                continue
                            lab, odds = (s.get("name") or "").strip(), s.get("odds")
                            if name == "Vainqueur du match":
                                side = g.side(lab)
                                if side:
                                    g.add("win|" + side, odds)
                            elif name == "Nombre total de points":
                                m = re.match(r"^([+-]) de ([\d.,]+)$", lab)
                                if m:
                                    g.add("tot|%s|%s" % (key_line(m.group(2)), "over" if m.group(1) == "+" else "under"), odds)
                            elif name.startswith("Nombre total de points - "):
                                side = g.side(name[len("Nombre total de points - "):])
                                m = re.match(r"^([+-]) de ([\d.,]+)$", lab)
                                if side and m:
                                    g.add("tt|%s|%s|%s" % (side, key_line(m.group(2)), "over" if m.group(1) == "+" else "under"), odds)
                            elif name == "Écart de points entre les équipes":
                                m1 = re.match(r"^(.*\S) gagne de (\d+) ou \+$", lab)
                                m2 = re.match(r"^(.*\S) ne perd pas ou perd de (\d+) ou -$", lab)
                                if m1 and g.side(m1.group(1)):
                                    g.add("hcp|%s|%s" % (g.side(m1.group(1)), key_line(-(int(m1.group(2)) - 0.5))), odds)
                                elif m2 and g.side(m2.group(1)):
                                    g.add("hcp|%s|%s" % (g.side(m2.group(1)), key_line(int(m2.group(2)) + 0.5)), odds)
        games.append(g)
    return games


# ------------------------------------------------------------------ comparaison et alertes
FETCHERS = {"pmu": fetch_pmu, "winamax": fetch_winamax, "unibet": fetch_unibet, "betclic": fetch_betclic}


def when(d):
    return "%s %s" % (("lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim.")[d.astimezone().weekday()], d.astimezone().strftime("%d/%m %H:%M"))


def describe(key, home, away):
    k = key.split("|")
    team = lambda s: home if s == "home" else away
    ou = lambda s: "Plus" if s == "over" else "Moins"
    if k[0] == "win":
        return "Vainqueur : %s" % team(k[1])
    if k[0] == "tot":
        return "Total de points : %s de %s" % (ou(k[2]), k[1].replace(".", ","))
    if k[0] == "hcp":
        return "Handicap : %s %s%s" % (team(k[1]), "+" if num(k[2]) > 0 else "", k[2].replace(".", ","))
    if k[0] == "tt":
        return "Points de %s : %s de %s" % (team(k[1]), ou(k[3]), k[2].replace(".", ","))
    if k[0] == "p":
        return "%s : %s+ %s" % (k[1].title(), k[3], STAT_FR.get(k[2], k[2]))
    if k[0] == "pl":
        return "%s : %s de %s %s" % (k[1].title(), ou(k[4]), k[3].replace(".", ","), STAT_FR.get(k[2], k[2]))
    return key


def hcp_main(sel):
    """Handicap principal d'un bookmaker (vu de « home ») → (ligne juste, ligne la plus équilibrée, cote home,
    cote away) ou None. Ligne juste : interpolée entre deux lignes voisines quand aucune n'est pile à l'équilibre."""
    rows = []
    for key, o in sel.items():
        k = key.split("|")
        if k[0] == "hcp" and k[1] == "home":
            u = sel.get("hcp|away|" + key_line(0.0 - num(k[2])))
            if u:
                rows.append((num(k[2]), o, u))
    best = min(rows, key=lambda r: abs(r[1] - r[2])) if rows else None
    if not best or abs(best[1] - best[2]) > alerts.GAP_BALANCE:
        return None
    fair = best[0]
    rows.sort()
    for (l1, o1, u1), (l2, o2, u2) in zip(rows, rows[1:]):  # la cote de « home » baisse quand il reçoit plus de points
        if o1 - u1 >= 0 >= o2 - u2 and o1 - u1 > o2 - u2 and l2 - l1 <= 2:
            fair = l1 + (l2 - l1) * (o1 - u1) / ((o1 - u1) - (o2 - u2))
            break
    return (fair,) + best


def favourite(books):
    """Favori net au vainqueur (cote moyenne des bookmakers ≤ HCP_FAV) → (« home »/« away », cote) ou None."""
    avg = {}
    for side in ("home", "away"):
        v = [sel["win|" + side] for sel in books.values() if "win|" + side in sel]
        if v:
            avg[side] = sum(v) / len(v)
    side = min(avg, key=avg.get) if avg else None
    return (side, avg[side]) if side and avg[side] <= HCP_FAV else None


def handicaps(groups):
    """Veille des handicaps : écart ≥ HCP_GAP entre bookmakers, ou handicap inversé (favori qui reçoit des points)."""
    out = []
    for ref, members in groups:
        books = {b: {swap(k) if inv else k: o for k, o in g.sel.items()} for b, (g, inv) in members.items()}
        bal = {b: v for b, v in ((b, hcp_main(sel)) for b, sel in books.items()) if v}
        if not bal:
            continue
        fav = favourite(books)
        side = fav[0] if fav else "home"
        sign = 1 if side == "home" else -1
        fair = {b: sign * v[0] for b, v in bal.items()}  # handicaps vus du favori (sinon de l'équipe à domicile)
        shown = {b: sign * v[1] for b, v in bal.items()}
        flipped = [b for b in bal if fav and fair[b] >= HCP_FLIP]
        lo_b, hi_b = min(bal, key=lambda b: bal[b][0]), max(bal, key=lambda b: bal[b][0])
        gap = bal[hi_b][0] - bal[lo_b][0]
        opposite = min(-bal[lo_b][0], bal[hi_b][0]) >= HCP_FLIP + 1  # favori différent d'un bookmaker à l'autre
        if flipped or gap >= HCP_GAP:
            out.append(dict(ref=ref, members=members, bal=bal, fav=fav, side=side, shown=shown, flipped=flipped,
                            lo_b=lo_b, hi_b=hi_b, gap=gap, inverted=bool(flipped or opposite)))
    return out


def main():
    dry = "--dry" in sys.argv
    per_book = {}
    with ThreadPoolExecutor(max_workers=len(FETCHERS)) as pool:
        futs = {pool.submit(f): b for b, f in FETCHERS.items()}
        for fut, b in futs.items():
            try:
                per_book[b] = fut.result()
                log("%s : %d matchs, %d cotes" % (b, len(per_book[b]), sum(len(g.sel) for g in per_book[b])))
            except Exception as e:  # noqa: BLE001
                log("%s : erreur %s" % (b, type(e).__name__))
                traceback.print_exc()
    for games in per_book.values():  # NBA : vainqueur et handicap seulement (les props sont suivies par collect.py)
        for g in games:
            if g.nba:
                g.sel = {k: o for k, o in g.sel.items() if k.startswith(("win|", "hcp|"))}

    # regroupement des matchs entre bookmakers (orientation du premier qui l'a vu)
    groups = []  # [(référence, {book: (EGame, inversé)})]
    for b, games in per_book.items():
        for g in games:
            for ref, members in groups:
                ok, inv = same_game(ref, g)
                if ok and b not in members:
                    members[b] = (g, inv)
                    break
            else:
                groups.append((g, {b: (g, False)}))

    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    sent = state.setdefault("sent", {})
    for k in [k for k, v in sent.items() if datetime.fromisoformat(v["t"]) < now() - timedelta(days=2)]:
        del sent[k]
    found = []
    for ref, members in groups:
        if len(members) < 2:
            continue
        odds = {}
        for b, (g, inv) in members.items():
            for key, o in g.sel.items():
                odds.setdefault(swap(key) if inv else key, {})[b] = o
        for key, by in odds.items():
            if len(by) < 2 or (ref.nba and not key.startswith("hcp|")):
                continue
            (bb, best), = sorted(by.items(), key=lambda kv: -kv[1])[:1]
            low_b, low = min(by.items(), key=lambda kv: kv[1])
            second = sorted(by.values(), reverse=True)[1]
            if best <= MAX_ODDS and best >= second * EDGE:
                found.append((ref, members, key, bb, best, by))
    if "--diag" in sys.argv:  # contrôle du rapprochement : matchs communs, sélections comparées, plus gros écarts
        import collections
        print("matchs par nombre de bookmakers :", collections.Counter(len(m) for _, m in groups))
        ratios = []
        for ref, members in groups:
            odds = {}
            for b, (g, inv) in members.items():
                for key, o in g.sel.items():
                    odds.setdefault(swap(key) if inv else key, {})[b] = o
            for key, by in odds.items():
                if len(by) >= 2:
                    v = sorted(by.values(), reverse=True)
                    ratios.append((v[0] / v[1], key.split("|")[0], ref.home, ref.away, key, by))
        print("sélections comparées :", len(ratios), collections.Counter(r[1] for r in ratios))
        for r in sorted(ratios, reverse=True)[:12]:
            print("  %.2f  %s – %s  %s  %s" % (r[0], r[2], r[3], r[4], r[5]))
        print("handicaps principaux (ligne de l'équipe à domicile) :")
        for ref, members in groups:
            books = {b: {swap(k) if inv else k: o for k, o in g.sel.items()} for b, (g, inv) in members.items()}
            bal = {b: v for b, v in ((b, hcp_main(sel)) for b, sel in books.items()) if v}
            if bal:
                print("  %s%s – %s  %s  favori %s" % ("[NBA] " if ref.nba else "", ref.home, ref.away,
                      " · ".join("%s %+g [%+.1f] (%s/%s)" % (b, v[1], v[0], v[2], v[3]) for b, v in bal.items()), favourite(books)))
        unmatched = [(b, g.home, g.away, g.league) for ref, m in groups if len(m) == 1 for b, (g, _) in m.items()]
        print("matchs vus par un seul bookmaker :", len(unmatched))
        for u in unmatched[:25]:
            print("  ", u)
    # écarts de ligne sur un même joueur (lignes équilibrées chez chaque bookmaker)
    gaps = []
    for ref, members in groups:
        if len(members) < 2:
            continue
        per = {}  # (joueur, stat) → {book: {ligne: {over, under}}}
        for b, (g, inv) in members.items():
            for key, o in g.sel.items():
                k = key.split("|")
                if k[0] == "pl":
                    per.setdefault((k[1], k[2]), {}).setdefault(b, {}).setdefault(k[3], {})[k[4]] = o
        for (player, stat), books in per.items():
            if stat not in alerts.GAP_MIN:
                continue
            bal = {}
            for b, lines in books.items():
                cands = [(abs(v["over"] - v["under"]), float(l), v["over"], v["under"]) for l, v in lines.items() if "over" in v and "under" in v]
                if cands and min(cands)[0] <= alerts.GAP_BALANCE:
                    bal[b] = min(cands)[1:]
            if len(bal) >= 2:
                lo_b, hi_b = min(bal, key=lambda b: bal[b][0]), max(bal, key=lambda b: bal[b][0])
                if bal[hi_b][0] - bal[lo_b][0] >= alerts.GAP_MIN[stat]:
                    gaps.append((ref, members, player, stat, lo_b, hi_b, bal))
    todo = []
    for ref, members, player, stat, lo_b, hi_b, bal in gaps:
        k = "%s|%s|%s|gap|%s|%s" % (ref.home, ref.away, ref.start.date(), player, stat)
        gap = bal[hi_b][0] - bal[lo_b][0]
        prev = sent.get(k)
        if prev and abs(gap - prev["v"]) < 1 and datetime.fromisoformat(prev["t"]) > now() - timedelta(hours=REPEAT_H * 2):
            continue
        ln = lambda x: ("%g" % x).replace(".", ",")
        title = "📏 %s · %s · écart de ligne %s" % (player.title(), STAT_FR.get(stat, stat), ln(gap))
        msg = "Plus de %s chez %s : %s\nMoins de %s chez %s : %s\nToutes les lignes : %s\n%s – %s · %s" % (
            ln(bal[lo_b][0]), BOOK_NAMES[lo_b], alerts.odds_fr(bal[lo_b][1]), ln(bal[hi_b][0]), BOOK_NAMES[hi_b],
            alerts.odds_fr(bal[hi_b][2]), " · ".join("%s %s" % (BOOK_NAMES[b], ln(v[0])) for b, v in sorted(bal.items(), key=lambda kv: kv[1][0])),
            ref.home, ref.away, when(ref.start))
        todo.append((title, msg, members[lo_b][0].url, k, gap))
    for h in handicaps(groups):  # rappel si l'écart (ou la ligne inversée) bouge d'1 point, ou après 12 h
        ref, bal, shown = h["ref"], h["bal"], h["shown"]
        team = lambda s: ref.home if s == "home" else ref.away
        k = "%s|%s|%s|hcp" % (ref.home, ref.away, ref.start.date())
        v = h["gap"] if len(bal) > 1 else shown[h["flipped"][0]]
        prev = sent.get(k)
        if prev and abs(v - prev["v"]) < 1 and datetime.fromisoformat(prev["t"]) > now() - timedelta(hours=REPEAT_H * 2):
            continue
        ln = lambda x: ("+" if x > 0 else "") + ("%g" % x).replace(".", ",")
        fav_team = team(h["side"])
        rows = []
        if h["inverted"]:
            title = "🔄 Handicap inversé · %s – %s" % (ref.home, ref.away)
            for b in h["flipped"]:
                rows.append("%s : %s %s (cote %s) alors qu'il est favori" % (BOOK_NAMES[b], fav_team, ln(shown[b]),
                            alerts.odds_fr(bal[b][2] if h["side"] == "home" else bal[b][3])))
        else:
            title = "📏 Écart de handicap %s · %s – %s" % (
                ("%g" % abs(bal[h["hi_b"]][1] - bal[h["lo_b"]][1])).replace(".", ","), ref.home, ref.away)
        if len(bal) > 1 and h["gap"] >= HCP_GAP:
            hi_b, lo_b = h["hi_b"], h["lo_b"]
            rows.append("%s %s chez %s : %s" % (ref.home, ln(bal[hi_b][1]), BOOK_NAMES[hi_b], alerts.odds_fr(bal[hi_b][2])))
            rows.append("%s %s chez %s : %s" % (ref.away, ln(0.0 - bal[lo_b][1]), BOOK_NAMES[lo_b], alerts.odds_fr(bal[lo_b][3])))
        rows.append("Handicap de %s : %s" % (fav_team, " · ".join("%s %s" % (BOOK_NAMES[b], ln(shown[b])) for b in sorted(bal, key=shown.get))))
        if h["fav"]:
            rows.append("Vainqueur : %s favori (cote moyenne %s)" % (fav_team, alerts.odds_fr(h["fav"][1])))
        rows.append("%s · %s" % ("NBA" if ref.nba else ref.league, when(ref.start)))
        url = h["members"][(h["flipped"] or [h["hi_b"]])[0]][0].url
        todo.append((title, "\n".join(rows), url, k, v))
    for ref, members, key, bb, best, by in found:
        k = "%s|%s|%s|%s" % (ref.home, ref.away, ref.start.date(), key)
        prev = sent.get(k)
        if prev and best <= prev["v"] + 0.05 and datetime.fromisoformat(prev["t"]) > now() - timedelta(hours=REPEAT_H):
            continue
        what = describe(key, ref.home, ref.away)
        league = members.get(bb, (ref, False))[0].league or ref.league
        rest = " · ".join("%s %s" % (BOOK_NAMES[b], alerts.odds_fr(o)) for b, o in sorted(by.items(), key=lambda kv: -kv[1]) if b != bb)
        title = "🏀 %s · %s – %s" % (BOOK_NAMES[bb], ref.home, ref.away)
        msg = "%s\n%s : %s\nAutres bookmakers : %s\n%s · %s" % (
            what, BOOK_NAMES[bb], alerts.odds_fr(best), rest, league, when(ref.start))
        todo.append((title, msg, members[bb][0].url, k, best))
    if dry:
        for t in todo:
            print("---\n" + t[0] + "\n" + t[1])
        log("%d alerte(s) (essai à blanc, rien envoyé)" % len(todo))
        return
    for title, msg, url, k, best in todo[:10]:
        alerts.push(title, msg, "basketball")
        sent[k] = {"v": best, "t": now().isoformat()}
    if len(todo) > 10:
        alerts.push("%d autres écarts en basket européen" % (len(todo) - 10),
                    "\n".join("• " + t[0][2:] + " — " + t[1].split("\n")[0] for t in todo[10:30]), "basketball")
        for t in todo[10:]:
            sent[t[3]] = {"v": t[4], "t": now().isoformat()}
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False))
    log("%d matchs comparés (2+ bookmakers), %d alerte(s) envoyée(s)" % (sum(1 for _, m in groups if len(m) >= 2), len(todo)))


if __name__ == "__main__":
    main()
