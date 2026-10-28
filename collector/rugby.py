#!/usr/bin/env python3
"""
Rugby à XV et à XIII — alertes uniquement (pas d'interface) : écart de cote ≥ 50 % entre deux
bookmakers sur une même sélection, sur tous les marchés que l'on sait rapprocher d'un bookmaker
à l'autre (compétitions féminines et rugby à 7 exclus).

Bookmakers : PMU (flux Kambi), Winamax, Unibet, Betclic (pages rendues côté serveur).
Sélections comparées (clés normalisées, côté « home »/« away »/« draw » de chaque bookmaker) :
  res|home            résultat du match (80 min)         dnb|home       remboursé si match nul
  dc|home             double chance (home ou nul ; « any » : l'un ou l'autre)
  hcp|home|-8.5       handicap                           h3|-4|draw     handicap à 3 issues (ligne de « home »)
  tot|45.5|over       total de points                    tt|home|20.5|over   points d'une équipe
  ht… (ht, htdnb, htdc, hthcp, httot, htmg)              mêmes marchés, 1re mi-temps
  htft|home|draw      mi-temps / fin de match            hmost|first    mi-temps avec le plus de points
  mg|home|1-7         marge de victoire (mg|draw : nul)  f10|home       premier à 10 points (f10|none)
  fts|home            première équipe à marquer          try|a dupont   marqueur d'essai (try1 : premier)

Usage : python3 collector/rugby.py [--dry] [--diag]
"""
import collections
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
import europe  # noqa: E402
import pmu  # noqa: E402
import unibet  # noqa: E402
import winamax  # noqa: E402
from common import get, iso, now  # noqa: E402
from europe import EGame, bc_state, key_line, num, same_game, when  # noqa: E402

EDGE = 1.50          # meilleure cote ≥ autre cote × 1,50
MAX_ODDS = 10.0      # au-delà, les écarts entre bookmakers sont normaux (un outsider à 8 contre 4 ailleurs reste une erreur)
MAX_ODDS_TRY = 6.0   # marqueurs d'essai : les avants à 8-10 varient beaucoup d'un bookmaker à l'autre
HORIZON_H = 168      # une semaine : les cotes du week-end sortent dès le lundi
REPEAT_H = 6
STATE = HERE / "build" / "rugby-state.json"
PAGE = HERE / "build" / "gaps-rugby.json"  # écarts en cours sur les joueurs, publiés sur le site par collect.py
EXCLUDE = re.compile(r"\(F\)|\sF\.(?:\s|$)|F[ée]minin|Femmes|Women|WXV|[ÉE]lite 1|Sevens|\bà 7\b", re.I)
BOOK_NAMES = {"pmu": "PMU", "winamax": "Winamax", "unibet": "Unibet", "betclic": "Betclic"}
BETCLIC = "https://www.betclic.fr"

# Noms d'équipes : traductions et surnoms d'un bookmaker à l'autre (« Stade Rochelais » ↔ « La Rochelle »)
europe.ALIAS.update({
    "angleterre": "england", "ecosse": "scotland", "galles": "wales", "irlande": "ireland", "italie": "italy",
    "zelande": "zealand", "afrique": "africa", "sud": "south", "argentine": "argentina",
    "australie": "australia", "japon": "japan", "fidji": "fiji", "georgie": "georgia", "liban": "lebanon",
    "papouasie": "papua", "guinee": "guinea", "espagne": "spain", "roumanie": "romania", "chili": "chile",
    "allemagne": "germany", "belgique": "belgium",
    "rochelais": "rochelle", "toulousain": "toulouse", "paloise": "pau", "ubb": "bordeaux", "bayonnais": "bayonne",
    "lou": "lyon", "usap": "perpignan", "aurillacois": "aurillac", "nicois": "nice", "montois": "montdemarsan",
    "edimbourg": "edinburgh", "trevise": "treviso",
})
europe.GENERIC |= {"stade", "rugby", "us", "su", "ca", "asm", "rc", "aviron", "olympique", "section", "pays", "union"}
DRAW = {"nul", "match nul", "x", "n", "egalite", "égalité", "aucun", "aucune"}


def log(*a):
    print(datetime.now().strftime("%H:%M:%S"), "[rugby]", *a, flush=True)


def page(url):
    """Page d'un match ; une erreur passagère du site (5xx) fait sauter ce match, pas tout le bookmaker."""
    try:
        return get(url)
    except urllib.error.HTTPError as e:
        if e.code >= 500:
            log("page ignorée (%s) : %s" % (e.code, url.split("/")[2]))
            return None
        raise


def in_window(start):
    return now() < start < now() + timedelta(hours=HORIZON_H)


def who(g, label):
    """Libellé d'issue → « home » / « away » / « draw » (ou None)."""
    lab = (label or "").strip()
    return "draw" if lab.lower() in DRAW else g.side(lab)


def dc(g, label):
    """« Clermont ou Nul » → « home » ; « Clermont ou Racing 92 » → « any »."""
    parts = {who(g, p) for p in re.split(r"\s+ou\s+|\s*/\s*", label or "")}
    return {frozenset({"home", "draw"}): "home", frozenset({"away", "draw"}): "away", frozenset({"home", "away"}): "any"}.get(frozenset(parts))


def htft(g, label):
    a, _, b = (label or "").partition("/")
    x, y = who(g, a), who(g, b)
    return "%s|%s" % (x, y) if x and y else None


def band(text):
    """« de 8 à 14 points » / « 8-14 » → « 8-14 » ; « d'au moins 15 » / « 15+ » / « 19 ou plus » → « 15+ »."""
    m = re.search(r"(\d+)\s*(?:-|à)\s*(\d+)", text)
    if m:
        return "%s-%s" % m.groups()
    m = re.search(r"au moins (\d+)|(\d+)\s*(?:\+|ou plus)", text)
    return (m.group(1) or m.group(2)) + "+" if m else None


def pkey(name):
    """Joueur → nom complet normalisé : « Antoine Dupont », « Dupont, Antoine » → « antoine dupont » ;
    une initiale reste telle quelle (« A. Dupont » → « a dupont ») et se rapproche ensuite d'un nom complet."""
    name = (name or "").strip()
    if "," in name:
        last, _, first = name.partition(",")
        name = first + " " + last
    return " ".join(unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower().replace(".", " ").split())


def unify_names(books):
    """Marqueurs écrits avec une initiale chez un bookmaker (« t francis ») → nom complet vu ailleurs dans le match,
    seulement s'il n'y a qu'un seul joueur possible (« tony francis » et « tupou francis » : on ne rapproche pas)."""
    full = {k.split("|", 1)[1] for sel in books.values() for k in sel if k.startswith("try") and len(k.split("|", 1)[1].split()[0]) > 1}
    for sel in books.values():
        for key in [k for k in sel if k.startswith("try")]:
            kind, name = key.split("|", 1)
            first, *rest = name.split()
            if len(first) == 1 and rest:
                cands = [f for f in full if f.split()[0][0] == first and f.split()[1:] == rest]
                if len(cands) == 1:
                    sel["%s|%s" % (kind, cands[0])] = sel.pop(key)


TRY_ANY = {"marque un essai", "marqueur d'essai", "marqueur d'essai (à tout moment)", "marqueur d'essai à tout moment",
           "marque un essai (à tout moment)", "buteur"}
TRY_FIRST = {"1er marqueur d'essai", "premier marqueur d'essai", "1er essayeur", "premier essayeur"}


def try_market(title, g, outs):
    """Marqueurs d'essai à tout moment (try) et premier marqueur du match (try1) — seulement ces deux marchés :
    « au moins 2 essais », « 2e marqueur », « 1re mi-temps », marqueur d'une seule équipe… sont ignorés."""
    t = re.sub(r"\s*-\s*(prolongations incluses|temps réglementaire|80 mins|match)$", "", title.strip().lower())
    kind = "try" if t in TRY_ANY else "try1" if t in TRY_FIRST else None
    if not kind:
        return
    for label, odds in outs:
        if label and not who(g, label) and not re.search(r"\d|aucun|autre|pas d'essai|\sou\s|\set\s", label, re.I):
            g.add("%s|%s" % (kind, pkey(label)), odds)
            g.__dict__.setdefault("names", {})[pkey(label)] = label.strip()  # nom complet, pour l'affichage


# ------------------------------------------------------------------ PMU (Kambi)
KAMBI = {"OT_ONE": "home", "OT_CROSS": "draw", "OT_TWO": "away"}
FULL = r"(?:Prolongations incluses|Temps réglementaire)"


def fetch_pmu():
    games = []
    for sport in ("rugby_union", "rugby_league"):
        data = json.loads(get("%s/listView/%s.json?%s&useCombined=true" % (pmu.API, sport, pmu.PARAMS)))
        for item in data.get("events", []):
            ev = item["event"]
            if not ev.get("homeName") or not ev.get("awayName"):
                continue  # paris à long terme (vainqueur de la compétition…)
            league = " / ".join(p.get("name", "") for p in ev.get("path", []))
            start = datetime.fromisoformat(ev["start"].replace("Z", "+00:00"))
            if EXCLUDE.search(league + " " + ev["name"]) or ev.get("state") != "NOT_STARTED" or not in_window(start):
                continue
            g = EGame("pmu", start, ev["homeName"], ev["awayName"], league, "https://www.pmu.fr/sport/event/%s" % ev["id"])
            raw = page("%s/betoffer/event/%s.json?%s" % (pmu.API, ev["id"], pmu.PARAMS))
            if raw is None:
                continue
            for b in json.loads(raw).get("betOffers", []):
                pmu_market(g, b["criterion"]["label"], [o for o in b.get("outcomes", []) if o.get("status") == "OPEN" and o.get("odds")])
            games.append(g)
    return games


def pmu_market(g, label, outs):
    odds = lambda o: o["odds"] / 1000
    line = lambda o: key_line(o["line"] / 1000)
    ou = lambda o: {"OT_OVER": "over", "OT_UNDER": "under"}.get(o.get("type"))
    if re.search(r"essai|marqueur", label, re.I) and any(o.get("participant") and o.get("type") not in KAMBI for o in outs):  # joueurs
        try_market(label, g, [(o["participant"], odds(o)) for o in outs if o.get("participant")])
        return
    for o in outs:
        side, t = KAMBI.get(o.get("type")), o.get("type", "")
        if re.match(r"^(Temps réglementaire|Cotes du match)", label) and "andicap" not in label and side:
            g.add("res|" + side, odds(o))
        elif re.match(r"^Mi-temps \(3-way\)|^1ère mi-temps \(3-way\)", label) and side:
            g.add("ht|" + side, odds(o))
        elif re.match(r"^Handicap - %s$" % FULL, label) and side and o.get("line") is not None:
            g.add("hcp|%s|%s" % (side, line(o)), odds(o))
        elif label == "Handicap - 1ère mi-temps" and side and o.get("line") is not None:
            g.add("hthcp|%s|%s" % (side, line(o)), odds(o))
        elif re.match(r"^3-Way Handicap - %s$" % FULL, label) and side and o.get("line") is not None:
            g.add("h3|%s|%s" % (line(o), side), odds(o))  # Kambi : ligne de l'équipe à domicile pour les 3 issues
        elif re.match(r"^Total de points - %s$" % FULL, label) and ou(o):
            g.add("tot|%s|%s" % (line(o), ou(o)), odds(o))
        elif label == "Total de points - 1ère mi-temps" and ou(o):
            g.add("httot|%s|%s" % (line(o), ou(o)), odds(o))
        elif re.match(r"^Nombre total de points par (.+) - %s$" % FULL, label) and ou(o):
            s = g.side(re.match(r"^Nombre total de points par (.+) - ", label).group(1))
            if s:
                g.add("tt|%s|%s|%s" % (s, line(o), ou(o)), odds(o))
        elif label.startswith("Mi-temps/Fin du match") and re.match(r"^OT_(ONE|CROSS|TWO)_(ONE|CROSS|TWO)$", t):
            a, b = t[3:].split("_")
            g.add("htft|%s|%s" % (KAMBI["OT_" + a], KAMBI["OT_" + b]), odds(o))
        elif label == "Mi-temps avec le plus de points":
            k = {"première mi-temps": "first", "deuxieme mi-temps": "second", "deuxième mi-temps": "second", "même nombre": "equal"}.get((o.get("label") or "").lower())
            if k:
                g.add("hmost|" + k, odds(o))
        elif re.match(r"^Marge de victoire - Temps réglementaire$", label):
            if t == "OT_WM_DRAW":
                g.add("mg|draw", odds(o))
            elif band(o.get("label") or ""):
                s = g.side(re.split(r"\s+gagne\s+", o.get("label") or "")[0])
                if s:
                    g.add("mg|%s|%s" % (s, band(o["label"])), odds(o))
        elif re.match(r"^Premier à 10 Points", label) and "mi-temps" not in label and side:
            g.add("f10|" + ("none" if side == "draw" else side), odds(o))
        elif label == "Première équipe qui marque" and side in ("home", "away"):
            g.add("fts|" + side, odds(o))
        elif label.startswith("Double chance"):
            k = {"OT_ONE_OR_CROSS": "home", "OT_CROSS_OR_TWO": "away", "OT_ONE_OR_TWO": "any"}.get(t)
            if k:
                g.add("dc|" + k, odds(o))
        elif re.match(r"^(Pari )?[Rr]emboursé si match nul", label) and side in ("home", "away"):
            g.add("dnb|" + side, odds(o))


# ------------------------------------------------------------------ Winamax
WINAMAX_SIMPLE = {"Résultat": "res", "Vainqueur (remboursé si match nul)": "dnb", "Mi-temps - Résultat": "ht",
                  "Mi-temps - Vainqueur (remboursé si match nul)": "htdnb"}


def fetch_winamax():
    games = []
    for sid in (12, 9992):  # rugby à XV, à XIII
        st = winamax.state(get("%s/sports/%d" % (winamax.BASE, sid)))
        tours = st.get("tournaments", {})
        for mt in st.get("matches", {}).values():
            if mt.get("sportId") != sid or mt.get("status") != "PREMATCH" or not mt.get("competitor1Name") or not mt.get("competitor2Name"):
                continue
            league = (tours.get(str(mt.get("tournamentId"))) or {}).get("tournamentName", "")
            start = datetime.fromtimestamp(mt["matchStart"], timezone.utc)
            if EXCLUDE.search(league + " " + mt["competitor1Name"] + " " + mt["competitor2Name"]) or not in_window(start):
                continue
            g = EGame("winamax", start, mt["competitor1Name"], mt["competitor2Name"], league, "%s/match/%s" % (winamax.BASE, mt["matchId"]))
            raw = page("%s/match/%s" % (winamax.BASE, mt["matchId"]))
            if raw is None:
                continue
            ms = winamax.state(raw)
            outcomes, odds = ms.get("outcomes", {}), ms.get("odds", {})
            for b in ms.get("bets", {}).values():
                if b.get("matchId") != mt["matchId"] or not b.get("available"):
                    continue
                outs = [((outcomes.get(str(o)) or {}).get("label") or "", odds.get(str(o))) for o in b.get("outcomes") or []
                        if (outcomes.get(str(o)) or {}).get("available", True)]
                winamax_market(g, b.get("betTitle") or "", outs)
            games.append(g)
    return games


def winamax_market(g, title, outs):
    half = title.startswith("Mi-temps - ")
    base = title[len("Mi-temps - "):] if half else title
    pre = "ht" if half else ""
    if title in WINAMAX_SIMPLE:
        for lab, x in outs:
            s = who(g, lab)
            if s and (s != "draw" or WINAMAX_SIMPLE[title] in ("res", "ht")):
                g.add("%s|%s" % (WINAMAX_SIMPLE[title], s), x)
    elif base == "Double chance":
        for lab, x in outs:
            if dc(g, lab):
                g.add("%sdc|%s" % (pre, dc(g, lab)), x)
    elif base == "Écart de points (handicap)":
        for lab, x in outs:
            m = re.match(r"^(.*\S)\s+([+-][\d.,]+)$", lab)
            s = m and g.side(m.group(1))
            if s:
                g.add("%shcp|%s|%s" % (pre, s, key_line(m.group(2))), x)
    elif base == "Nombre de points" or base.startswith("Nombre de points de "):
        s = g.side(base[len("Nombre de points de "):]) if base != "Nombre de points" else None
        for lab, x in outs:
            m = re.match(r"^(Plus|Moins) de ([\d.,]+)$", lab)
            side = m and ("over" if m.group(1) == "Plus" else "under")
            if m and base == "Nombre de points":
                g.add("%stot|%s|%s" % (pre, key_line(m.group(2)), side), x)
            elif m and s and not half:
                g.add("tt|%s|%s|%s" % (s, key_line(m.group(2)), side), x)
    elif title == "Mi-temps/Fin de match":
        for lab, x in outs:
            if htft(g, lab):
                g.add("htft|" + htft(g, lab), x)
    elif title == "Mi-temps avec le plus de points":
        for lab, x in outs:
            k = {"1re mi-temps": "first", "2de mi-temps": "second", "égalité": "equal"}.get(lab.lower())
            if k:
                g.add("hmost|" + k, x)
    elif base == "Marge de victoire":
        for lab, x in outs:
            s = who(g, lab) if lab.lower() in DRAW else g.side(re.split(r"\s+gagne\s+", lab)[0])
            if s == "draw":
                g.add("%smg|draw" % pre, x)
            elif s and band(lab):
                g.add("%smg|%s|%s" % (pre, s, band(lab)), x)
    elif title == "Premier à 10 points":
        for lab, x in outs:
            s = who(g, lab)
            if s:
                g.add("f10|" + ("none" if s == "draw" else s), x)
    elif "essai" in title.lower():
        try_market(title, g, outs)


# ------------------------------------------------------------------ Unibet
def fetch_unibet():
    home_page = get(unibet.BASE + "/paris-rugby")
    leagues = sorted(set(re.findall(r'href="/paris-rugby/([a-z0-9-]+/[a-z0-9-]+)/\d+/', home_page)))
    links = set(re.findall(r'href="(/paris-rugby/[a-z0-9-]+/[a-z0-9-]+/\d+/[a-z0-9-]+-vs-[a-z0-9-]+)"', home_page))
    for lg in leagues:
        if not EXCLUDE.search(lg.replace("-", " ")) and not lg.endswith("-f"):
            links |= set(re.findall(r'href="(/paris-rugby/%s/\d+/[a-z0-9-]+-vs-[a-z0-9-]+)"' % re.escape(lg), get(unibet.BASE + "/paris-rugby/" + lg)))
    games = []
    for path in sorted(links):
        lg = "/".join(path.split("/")[2:4])
        if lg.endswith("-f") or EXCLUDE.search(lg.replace("-", " ")):
            continue
        raw = page(unibet.BASE + path)
        m = raw and re.search(r'<script[^>]*id="serverApp-state"[^>]*>(.*?)</script>', raw, re.S)
        evs = (json.loads(m.group(1)).get("EventsDetail", {}).get("events") or []) if m else []
        if not evs:
            continue
        ev = evs[0]
        start = datetime.fromisoformat(ev["parsedStart"].replace("Z", "+00:00"))
        a, _, b = (ev.get("description") or "").partition(" vs ")
        if not b or not in_window(start):
            continue
        g = EGame("unibet", start, a, b, lg, unibet.BASE + path)
        for group in ev.get("groupedMarkets") or []:
            gd = group.get("description") or ""
            for market in group.get("markets") or []:
                outs = [o for o in market.get("outcomes") or [] if not o.get("suspended") and not o.get("hidden")]
                unibet_market(g, gd, outs)
        games.append(g)
    return games


def unibet_market(g, gd, outs):
    full = re.search(r"- (80 Mins|Match|TR)$", gd)
    half = re.search(r"- 1ère Mi-Temps$", gd, re.I)
    pre = "ht" if half else ""
    for o in outs:
        d, x = (o.get("description") or "").strip(), o.get("price")
        if re.match(r"^1 ?N ?2 - ", gd) and (full or half):
            s = who(g, d)
            if s:
                g.add("%s|%s" % ("ht" if half else "res", s), x)
        elif re.match(r"^1 ?N ?2 Handicap - ", gd) and full and o.get("spread") is not None:
            draw = re.match(r"^N \((.*)\[", d)
            team = draw.group(1) if draw else d.split(" [")[0]
            s = g.side(team.strip())
            if s:
                home_line = num(o["spread"]) if s == "home" else -num(o["spread"])
                g.add("h3|%s|%s" % (key_line(home_line), "draw" if draw else s), x)
        elif gd.startswith("Face à Face Handicap") and (full or half) and o.get("spread") is not None:
            s = g.side(d.split(" [")[0])
            if s:
                g.add("%shcp|%s|%s" % (pre, s, key_line(o["spread"])), x)
        elif gd.startswith("Plus / Moins Points") and "Equipe" not in gd and (full or half) and o.get("spread") is not None:
            side = "over" if d.startswith("Plus") else "under" if d.startswith("Moins") else None
            if side:
                g.add("%stot|%s|%s" % (pre, key_line(o["spread"]), side), x)
        elif gd.startswith("Double Chance") and (full or half):
            k = dc(g, d) or {"1N": "home", "1X": "home", "N2": "away", "X2": "away", "12": "any"}.get(d.replace(" ", ""))
            if k:
                g.add("%sdc|%s" % (pre, k), x)
        elif gd.startswith("Mi-temps / Fin de match") or gd.startswith("Mi-Temps / Fin de Match"):
            if htft(g, d):
                g.add("htft|" + htft(g, d), x)
        elif "essai" in gd.lower():
            try_market(gd, g, [(d, x)])


# ------------------------------------------------------------------ Betclic (marchés rendus côté serveur)
def fetch_betclic():
    links, matches = {}, {}
    for sport in ("/rugby-a-xv-srugby_union", "/rugby-a-xiii-srugby_league"):
        top = get(BETCLIC + sport)
        found = {mid: p for p, mid in re.findall(r'href="(/rugby[a-z_-]*/[a-z0-9-]+-c\d+/[a-z0-9-]+-m(\d+))"', top)}
        links.update(found)
        matches.update({m["matchId"]: m for p in bc_state(top) for m in p.get("matches") or []})
        for c in {re.match(r"(/rugby[a-z_-]*/[a-z0-9-]+-c\d+)/", p).group(1) for p in found.values()}:
            cp = get(BETCLIC + c)
            links.update({mid: p for p, mid in re.findall(r'href="(/rugby[a-z_-]*/[a-z0-9-]+-c\d+/[a-z0-9-]+-m(\d+))"', cp)})
            matches.update({m["matchId"]: m for p in bc_state(cp) for m in p.get("matches") or []})
    games = []
    for mid, mt in matches.items():
        league = (mt.get("competition") or {}).get("name", "")
        start = datetime.fromisoformat(mt["matchDateUtc"][:19] + "+00:00")
        if mid not in links or EXCLUDE.search(league + " " + mt["name"]) or not in_window(start):
            continue
        home, _, away = mt["name"].partition(" - ")
        g = EGame("betclic", start, home, away, league, BETCLIC + links[mid])
        raw = page(BETCLIC + links[mid])
        if raw is None:
            continue
        for p in bc_state(raw):
            for sc in (p.get("match") or {}).get("subCategories") or []:
                for mk in sc.get("markets") or []:
                    outs = [(s.get("name") or "", s.get("odds")) for row in mk.get("selectionMatrix") or [] for sel in row.get("selections") or []
                            for s in [(sel.get("selectionOneof") or {}).get("selection") or {}] if s.get("status") == 1]
                    betclic_market(g, mk.get("name") or "", outs)
        games.append(g)
    return games


def betclic_market(g, name, outs):
    half = name.startswith("1ère mi-temps")
    pre = "ht" if half else ""
    base = re.sub(r"^1ère mi-temps (\(seule\) )?- ", "", name)
    for lab, x in outs:
        if base in ("Résultat du match (tps rég.)", "Résultat du match", "Résultat"):
            s = who(g, lab)
            if s:
                g.add("%s|%s" % ("ht" if half else "res", s), x)
        elif base in ("Résultat du match (remboursé si match nul)", "Résultat (remboursé si match nul)"):
            s = g.side(lab)
            if s:
                g.add("%sdnb|%s" % (pre, s), x)
        elif base.lower() == "double chance":
            if dc(g, lab):
                g.add("%sdc|%s" % (pre, dc(g, lab)), x)
        elif name == "Mi-temps / Fin de match":
            if htft(g, lab):
                g.add("htft|" + htft(g, lab), x)
        elif base == "Écart de points":
            m1 = re.match(r"^(.*\S) gagne de (\d+) ou \+$", lab)
            m2 = re.match(r"^(.*\S) ne perd pas ou perd de (\d+) ou -$", lab)
            if m1 and g.side(m1.group(1)):
                g.add("%shcp|%s|%s" % (pre, g.side(m1.group(1)), key_line(-(int(m1.group(2)) - 0.5))), x)
            elif m2 and g.side(m2.group(1)):
                g.add("%shcp|%s|%s" % (pre, g.side(m2.group(1)), key_line(int(m2.group(2)) + 0.5)), x)
        elif base == "Nombre total de points" or base.startswith("Nombre total de points - "):
            m = re.match(r"^([+-]) de ([\d.,]+)$", lab)
            side = m and ("over" if m.group(1) == "+" else "under")
            if m and base == "Nombre total de points":
                g.add("%stot|%s|%s" % (pre, key_line(m.group(2)), side), x)
            elif m and not half and g.side(base[len("Nombre total de points - "):]):
                g.add("tt|%s|%s|%s" % (g.side(base[len("Nombre total de points - "):]), key_line(m.group(2)), side), x)
        elif name == "Mi-temps avec le plus de points marqués":
            k = {"1ère mi-temps": "first", "2ème mi-temps": "second", "nul": "equal"}.get(lab.lower())
            if k:
                g.add("hmost|" + k, x)
        elif base in ("Marge du vainqueur", "Marge de victoire"):
            s = who(g, lab) if lab.lower() in DRAW else g.side(re.sub(r"\s*\d+(\s*-\s*\d+|\+)$", "", lab))
            if s == "draw":
                g.add("%smg|draw" % pre, x)
            elif s and band(lab):
                g.add("%smg|%s|%s" % (pre, s, band(lab)), x)
    if "essai" in name.lower():
        try_market(name, g, outs)


# ------------------------------------------------------------------ comparaison et alertes
FETCHERS = {"pmu": fetch_pmu, "winamax": fetch_winamax, "unibet": fetch_unibet, "betclic": fetch_betclic}
SWAP = {"home": "away", "away": "home"}


def swap(key):
    """Clé vue de l'autre équipe (bookmaker qui inverse domicile/extérieur)."""
    k = [SWAP.get(t, t) for t in key.split("|")]
    if k[0] == "h3":  # ligne exprimée pour l'équipe à domicile
        k[1] = key_line(0.0 - num(k[1]))
    return "|".join(k)


def describe(key, home, away):
    k = key.split("|")
    team = lambda s: {"home": home, "away": away, "draw": "match nul", "none": "aucune équipe"}.get(s, s)
    ou = lambda s: "Plus" if s == "over" else "Moins"
    ln = lambda x: ("+" if num(x) > 0 else "") + x.replace(".", ",")
    half = k[0].startswith("ht") and k[0] != "htft"
    kind = "res" if k[0] == "ht" else k[0][2:] if half else k[0]
    pre = "1re mi-temps · " if half else ""
    if kind == "res":
        return pre + "Résultat : %s" % team(k[1])
    if kind == "dnb":
        return pre + "Vainqueur (remboursé si nul) : %s" % team(k[1])
    if kind == "dc":
        return pre + "Double chance : %s" % ("%s ou %s" % (home, away) if k[1] == "any" else "%s ou nul" % team(k[1]))
    if kind == "hcp":
        return pre + "Handicap : %s %s" % (team(k[1]), ln(k[2]))
    if kind == "tot":
        return pre + "Total de points : %s de %s" % (ou(k[2]), k[1].replace(".", ","))
    if kind == "mg":
        return pre + ("Marge de victoire : match nul" if k[1] == "draw" else "Marge de victoire : %s gagne de %s pts" % (
            team(k[1]), k[2].replace("-", " à ").replace("+", " ou plus")))
    if kind == "h3":
        return "Handicap 3 issues (%s %s) : %s" % (home, ln(k[1]), team(k[2]))
    if kind == "tt":
        return "Points de %s : %s de %s" % (team(k[1]), ou(k[3]), k[2].replace(".", ","))
    if kind == "htft":
        return "Mi-temps / fin de match : %s / %s" % (team(k[1]), team(k[2]))
    if kind == "hmost":
        return "Mi-temps avec le plus de points : %s" % {"first": "1re", "second": "2e", "equal": "égalité"}[k[1]]
    if kind == "f10":
        return "Premier à 10 points : %s" % team(k[1])
    if kind == "fts":
        return "Première équipe à marquer : %s" % team(k[1])
    if kind in ("try", "try1"):
        return "%s : %s" % ("Premier marqueur d'essai" if kind == "try1" else "Marqueur d'essai", k[1].title())
    return key


def player_name(members, pk):
    """Nom complet du joueur tel qu'écrit par un bookmaker (« Tony Francis »), sinon la clé en capitales."""
    names = [getattr(g, "names", {}).get(pk) for g, _ in members.values()]
    return max((n for n in names if n), key=len, default=pk.title())


def page_gaps(found):
    """Cotes anormales en cours sur les marqueurs d'essai, pour la page « Écarts » du site."""
    out = []
    for ref, members, key, bb, best, by in found:
        k = key.split("|")
        if k[0] not in ("try", "try1"):
            continue
        name = player_name(members, k[1])
        out.append({"id": "%s|%s|%s|%s" % (ref.home, ref.away, ref.start.date(), key), "sport": "rugby", "kind": "odds",
                    "name": name, "stat": k[0], "cat": "Premier marqueur d'essai" if k[0] == "try1" else "Marqueur d'essai",
                    "sel": "", "start": iso(ref.start), "home": ref.home, "away": ref.away,
                    "league": members[bb][0].league or ref.league, "best": bb,
                    "books": {b: {"odds": o, "url": members[b][0].url} for b, o in by.items()}})
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

    found, ratios = [], []
    for ref, members in groups:
        books = {b: {swap(k) if inv else k: o for k, o in g.sel.items()} for b, (g, inv) in members.items()}
        unify_names(books)
        odds = {}
        for b, sel in books.items():
            for key, o in sel.items():
                odds.setdefault(key, {})[b] = o
        for key, by in odds.items():
            if len(by) < 2:
                continue
            (bb, best), = sorted(by.items(), key=lambda kv: -kv[1])[:1]
            second = sorted(by.values(), reverse=True)[1]
            ratios.append((best / second, key, ref, by))
            if best <= (MAX_ODDS_TRY if key.startswith("try") else MAX_ODDS) and best >= second * EDGE:
                found.append((ref, members, key, bb, best, by))
    if "--diag" in sys.argv:  # contrôle du rapprochement : matchs communs, marchés comparés, plus gros écarts
        print("matchs par nombre de bookmakers :", collections.Counter(len(m) for _, m in groups))
        print("sélections comparées :", len(ratios), collections.Counter(r[1].split("|")[0] for r in ratios))
        for r, key, ref, by in sorted(ratios, key=lambda x: -x[0])[:15]:
            print("  %.2f  %s – %s  %s  %s" % (r, ref.home, ref.away, key, by))
        for ref, m in groups:
            if len(m) == 1:
                b, (g, _) = next(iter(m.items()))
                print("  seul chez %s : %s – %s (%s)" % (b, g.home, g.away, g.league))

    PAGE.parent.mkdir(exist_ok=True)
    PAGE.write_text(json.dumps({"at": iso(now()), "gaps": page_gaps(found)}, ensure_ascii=False))
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    sent = state.setdefault("sent", {})
    for k in [k for k, v in sent.items() if datetime.fromisoformat(v["t"]) < now() - timedelta(days=3)]:
        del sent[k]
    todo = []
    for ref, members, key, bb, best, by in found:
        # une clé par façon d'écrire le match chez chaque bookmaker : pas de rappel quand le match de référence change de nom
        ks = sorted({"%s|%s|%s|%s" % (((g.away, g.home) if inv else (g.home, g.away)) + (ref.start.date(), key)) for g, inv in members.values()})
        prev = max((sent[x] for x in ks if x in sent), key=lambda v: v["t"], default=None)
        if prev and best <= prev["v"] + 0.05 and datetime.fromisoformat(prev["t"]) > now() - timedelta(hours=REPEAT_H):
            continue
        rest = " · ".join("%s %s" % (BOOK_NAMES[b], alerts.odds_fr(o)) for b, o in sorted(by.items(), key=lambda kv: -kv[1]) if b != bb)
        title = "🏉 %s · %s – %s" % (BOOK_NAMES[bb], ref.home, ref.away)
        what = describe(key, ref.home, ref.away)
        if key.startswith("try"):
            what = what.split(" : ")[0] + " : " + player_name(members, key.split("|", 1)[1])
        msg = "%s\n%s : %s\nAutres bookmakers : %s\n%s · %s" % (
            what, BOOK_NAMES[bb], alerts.odds_fr(best), rest,
            members[bb][0].league or ref.league, when(ref.start))
        todo.append((title, msg, ks, best))
    if dry:
        for t in todo:
            print("---\n" + t[0] + "\n" + t[1])
        log("%d alerte(s) (essai à blanc, rien envoyé)" % len(todo))
        return
    for title, msg, ks, best in todo[:10]:
        alerts.push(title, msg, "rugby_football")
        sent.update({k: {"v": best, "t": now().isoformat()} for k in ks})
    if len(todo) > 10:
        alerts.push("%d autres écarts en rugby" % (len(todo) - 10),
                    "\n".join("• " + t[0][2:] + " — " + t[1].split("\n")[0] for t in todo[10:30]), "rugby_football")
        for t in todo[10:]:
            sent.update({k: {"v": t[3], "t": now().isoformat()} for k in t[2]})
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False))
    log("%d matchs comparés (2+ bookmakers), %d alerte(s) envoyée(s)" % (sum(1 for _, m in groups if len(m) >= 2), len(todo)))


if __name__ == "__main__":
    main()
