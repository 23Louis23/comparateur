#!/usr/bin/env python3
"""
Tous sports, paris annexes — alertes et page « Écarts » : cote anormale (≥ 50 % au-dessus du 2e meilleur bookmaker
sur la même sélection), écart de ligne sur les totaux, écart de handicap et handicap inversé (le favori reçoit des buts
chez un bookmaker). Le foot d'abord ; le basket et le rugby ont déjà leurs tâches (europe.py, rugby.py).

Bookmakers : PMU (flux Kambi), Winamax, Betclic, Unibet — les mêmes sites que pour la NBA, donc on les ménage :
  - à chaque passage, la liste des matchs (une page par bookmaker ; quelques pages de championnat chez Betclic et Unibet) ;
  - puis la page de chaque match, seulement pour les matchs des prochaines heures vus chez au moins 2 bookmakers, dans
    la limite de BUDGET pages par bookmaker et par passage (matchs vus chez le plus de bookmakers d'abord, puis les plus
    proches) ; une page lue est gardée en cache et relue à chaque passage (11 min) dans les 3 h avant le match, 40 min avant,
    2 h au-delà de 12 h ;
  - une alerte ne part que sur des cotes lues il y a moins de FRESH_MIN minutes chez les bookmakers concernés : sinon
    leurs pages sont relues tout de suite et l'écart revérifié.

Sélections comparées (foot ; clés normalisées côté « home »/« away » de chaque bookmaker) :
  res|home       1N2 (sert aussi à repérer le favori)   dnb|home        remboursé si match nul
  tot|2.5|over   total de buts                          tt|home|1.5|over   buts d'une équipe
  hcp|home|-1.5  handicap asiatique                     eh|-1|draw      handicap à 3 issues (ligne de « home »)
  btts|yes       les 2 équipes marquent
  sc|e haaland   buteur   sc2|…  2 buts ou plus   fs|…  premier buteur   as|…  passeur décisif   dec|…  buteur ou passeur

Usage : python3 collector/sports.py [--dry] [--diag]   (--dry : rien n'est envoyé ; seul le cache des pages est gardé)
"""
import collections
import json
import re
import statistics
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
import winamax  # noqa: E402
from common import Blocked, get, iso, now  # noqa: E402
from europe import EGame, bc_state, close, key_line, num, when  # noqa: E402

EDGE = 1.50          # meilleure cote ≥ 2e meilleure × 1,50
MAX_ODDS = 6.0       # au-delà, les écarts entre bookmakers sont normaux (outsiders, défenseurs buteurs…)
PLAYER_EDGE = 1.60   # joueurs (buteur, passeur…) : prix plus dispersés d'un site à l'autre, seuil plus haut…
PLAYER_MAX = 5.0     # …et cotes plus basses seulement
REL_EDGE = 1.40      # et l'écart doit dépasser de 40 % l'écart habituel entre ces deux sites sur ce marché, dans ce match
FRESH_MIN = 20       # une alerte ne part que sur des cotes lues il y a moins de 20 min
LINE_GAP = 1         # écart (en buts) entre les lignes à l'équilibre de deux bookmakers : total, buts d'une équipe, handicap
HCP_FAV = 1.60       # favori net (cote moyenne au 1N2)…
HCP_FLIP = 0.5       # …à qui un bookmaker donne pourtant au moins +0,5 but : handicap inversé
DETAIL_H = {"pmu": 24, "winamax": 24, "betclic": 24, "unibet": 6}  # pages de match lues jusqu'à N h avant le coup d'envoi
BUDGET = {"pmu": 80, "winamax": 30, "betclic": 15, "unibet": 6}     # pages de match par passage (Unibet : 3 Mo la page)
CONFIRM = 8          # pages relues en plus par bookmaker pour confirmer un écart
LIST_EVERY = {"betclic": 60, "unibet": 60}  # minutes : listes en plusieurs pages (une par championnat), relues 1 fois par heure
STATE = HERE / "build" / "sports-state.json"
CACHE = HERE / "build" / "cache-sports.json"
PAGE = HERE / "build" / "gaps-sports.json"  # écarts en cours, publiés sur le site par collect.py
BOOK_NAMES = {"pmu": "PMU", "winamax": "Winamax", "betclic": "Betclic", "unibet": "Unibet"}
BETCLIC = "https://www.betclic.fr"
UNIBET = "https://www.unibet.fr"
EXCLUDE = re.compile(r"virtu|e-?soccer|\besports?\b|cyber|\bfifa\b", re.I)  # matchs virtuels
FOOT = {"name": "Foot", "emoji": "⚽", "tag": "soccer", "pmu": "football", "winamax": 1, "betclic": "/football-sfootball",
        "betclic_comps": ["ligue-1-mcdonald-s-c4", "ligue-2-bkt-c19", "angl-premier-league-c3", "espagne-laliga-c7",
                          "italie-serie-a-c6", "allemagne-bundesliga-c5", "ligue-des-champions-c8", "ligue-europa-c3453",
                          "ligue-conference-c28946"],
        "unibet": "/paris-football",
        "unibet_comps": ["france/ligue-1-mcdonalds", "france/ligue-2-bkt", "angleterre/premier-league", "espagne/laliga",
                         "italie/serie-a", "allemagne/bundesliga-1", "coupes-d-europe/ligue-des-champions",
                         "coupes-d-europe/ligue-europa", "coupes-d-europe/ligue-conference"]}

# noms d'équipes : mots sans valeur pour le rapprochement, et noms français ou abrégés → nom local
europe.GENERIC.discard("city")  # Manchester City / Manchester United
europe.GENERIC |= {"fc", "afc", "cf", "sc", "ssc", "ac", "us", "rc", "sv", "fk", "nk", "calcio", "cd", "ud", "sd", "rcd",
                   "ogc", "osc", "sco", "fsv", "spvgg", "vfl", "vfb", "tsg", "bsc", "ec", "se", "cr", "sad", "1907", "1899",
                   "1846", "1909", "1913", "1860", "1904", "1900", "1910", "04", "05", "09", "96", "98", "01"}
europe.ALIAS.update({"man": "manchester", "utd": "united", "come": "como", "atl": "atletico", "wolves": "wolverhampton",
                     "spurs": "tottenham", "psg": "paris", "gladbach": "monchengladbach", "mgladbach": "monchengladbach",
                     "lisbonne": "lisbon", "lisboa": "lisbon", "porto": "porto", "bruges": "brugge", "copenhague": "kobenhavn",
                     "copenhagen": "kobenhavn", "glasgow": "glasgow", "londres": "london", "rome": "roma",
                     "genes": "genoa", "athenes": "athens", "eindhoven": "psv", "salonique": "thessaloniki"})
DRAW = {"nul", "match nul", "x", "n", "egalite", "égalité"}
TRANS = str.maketrans({"ø": "o", "Ø": "o", "æ": "ae", "Æ": "ae", "ß": "ss", "đ": "d", "Đ": "d", "ł": "l", "Ł": "l",
                       "ı": "i", "œ": "oe", "Œ": "oe", "ð": "d", "þ": "th"})


_tokens = europe.tokens


def tokens(name):
    """Mots d'un nom d'équipe, lettres nordiques comprises (« København » → « kobenhavn »)."""
    return _tokens((name or "").translate(TRANS))


europe.tokens = tokens  # EGame.side s'en sert aussi


def log(*a):
    print(datetime.now().strftime("%H:%M:%S"), "[sports]", *a, flush=True)


def page(url):
    """Page d'un match ; une erreur passagère (5xx) ou un match retiré (404, 410) fait sauter ce match, pas le bookmaker."""
    try:
        return get(url)
    except urllib.error.HTTPError as e:
        if e.code >= 500 or e.code in (404, 410):
            return None
        raise


def utc(s):
    """« 2026-11-24T20:00:00Z », « …00.0000000Z », « …00.000Z » → date UTC."""
    return datetime.fromisoformat(s[:19] + "+00:00")


def plain(s):
    return unicodedata.normalize("NFKD", (s or "").translate(TRANS)).encode("ascii", "ignore").decode().lower()


def pkey(name):
    """Joueur → « initiale nom » : « Erling Haaland », « Haaland, Erling », « E. Haaland » → « e haaland » ;
    « Kevin De Bruyne » → « k debruyne » ; « Nico O'Reilly » et « O Reilly, Nico » → « n oreilly » ; un nom seul
    (« Giovane ») reste tel quel. Les prénoms différents d'un bookmaker à l'autre (Phil / Philip) se rejoignent."""
    name = re.sub(r"\s+\d+\+\s*$", "", (name or "").strip())  # « Haaland, Erling 2+ »
    if "," in name:
        last, _, first = name.partition(",")
        name = "%s %s" % (first, last)
    words = [w for w in re.findall(r"[a-z0-9]+", plain(name).replace("'", "")) if w not in ("jr", "sr", "ii", "iii")]
    if not words:
        return None
    return words[0] if len(words) == 1 else "%s %s" % (words[0][0], "".join(words[1:]))


class Game(EGame):
    def __init__(self, book, start, home, away, league, url, eid=None):
        super().__init__(book, start, home, away, league, url)
        self.eid = str(eid) if eid is not None else None
        self.full = {}                      # clé joueur → noms écrits par ce bookmaker
        self.detail, self.detail_full, self.detail_at = {}, {}, None   # page du match (cache)

    def player(self, kind, name, odds):
        k = pkey(name)
        if k and plain(name).strip() not in DRAW and "aucun" not in plain(name):
            self.full.setdefault(k, set()).add(" ".join(name.split()))
            self.add("%s|%s" % (kind, k), odds)

    def view(self):
        """Toutes les cotes connues : page du match (cache) complétée par la liste, plus récente."""
        return {**self.detail, **self.sel}

    def names(self):
        out = {k: set(v) for k, v in self.detail_full.items()}
        for k, v in self.full.items():
            out.setdefault(k, set()).update(v)
        return out

    def fresh(self, key):
        return key in self.sel or bool(self.detail_at and now() - self.detail_at <= timedelta(minutes=FRESH_MIN) and key in self.detail)


def who(g, label):
    lab = (label or "").strip()
    return "draw" if plain(lab) in DRAW else g.side(lab)


def ou(text):
    """« Plus de 2,5 » / « + de 2,5 » / « Plus 2.5 » → (« over », « 2.5 »)."""
    m = re.match(r"^(Plus|Moins|\+|-)\s*(?:de\s+)?([\d.,]+)", (text or "").strip())
    return ("over" if m.group(1) in ("Plus", "+") else "under", key_line(m.group(2))) if m else (None, None)


# ------------------------------------------------------------------ PMU (Kambi)
KAMBI = {"OT_ONE": "home", "OT_CROSS": "draw", "OT_TWO": "away"}
PMU_PLAYER = [("Marque ou effectue une passe décisive", "dec"), ("Effectue une passe décisive", "as"),
              ("Marque au moins 2 buts", "sc2"), ("1er buteur", "fs"), ("Marque", "sc")]


def pmu_market(g, b):
    label = b["criterion"]["label"]
    outs = [o for o in b.get("outcomes", []) if o.get("status") == "OPEN" and o.get("odds")]
    odds = lambda o: o["odds"] / 1000
    if label == "Temps réglementaire":
        for o in outs:
            if o.get("type") in KAMBI:
                g.add("res|" + KAMBI[o["type"]], odds(o))
    elif label == "Pari remboursé en cas d'égalité":
        for o in outs:
            if o.get("type") in ("OT_ONE", "OT_TWO"):
                g.add("dnb|" + KAMBI[o["type"]], odds(o))
    elif label == "Nombre total de buts":
        for o in outs:
            if o.get("type") in ("OT_OVER", "OT_UNDER") and o.get("line") is not None:
                g.add("tot|%s|%s" % (key_line(o["line"] / 1000), "over" if o["type"] == "OT_OVER" else "under"), odds(o))
    elif label.startswith("Nombre total de buts par ") and " - " not in label:
        s = g.side(label[len("Nombre total de buts par "):])
        for o in outs:
            if s and o.get("type") in ("OT_OVER", "OT_UNDER") and o.get("line") is not None:
                g.add("tt|%s|%s|%s" % (s, key_line(o["line"] / 1000), "over" if o["type"] == "OT_OVER" else "under"), odds(o))
    elif label == "Handicap":
        for o in outs:
            if o.get("type") in ("OT_ONE", "OT_TWO") and o.get("line") is not None:
                g.add("hcp|%s|%s" % (KAMBI[o["type"]], key_line(o["line"] / 1000)), odds(o))
    elif label == "3-Way Handicap":
        for o in outs:
            if o.get("type") in KAMBI and o.get("line") is not None:
                g.add("eh|%s|%s" % (key_line(o["line"] / 1000), KAMBI[o["type"]]), odds(o))
    elif label == "Les deux équipes marquent":
        for o in outs:
            if o.get("type") in ("OT_YES", "OT_NO"):
                g.add("btts|" + ("yes" if o["type"] == "OT_YES" else "no"), odds(o))
    else:
        kind = next((k for prefix, k in PMU_PLAYER if label == prefix or label.startswith(prefix + " (")), None)
        for o in outs:
            if kind and o.get("type") == "OT_YES" and o.get("participant"):
                g.player(kind, o["participant"], odds(o))


def list_pmu(sp):
    data = json.loads(get("%s/listView/%s/all/all/all/matches.json?%s&useCombined=true" % (pmu.API, sp["pmu"], pmu.PARAMS)))
    out = []
    for item in data.get("events", []):
        ev = item["event"]
        league = " / ".join(p.get("name", "") for p in ev.get("path", [])[1:])
        start = utc(ev["start"])
        if ev.get("state") != "NOT_STARTED" or not now() < start < now() + timedelta(hours=DETAIL_H["pmu"]) \
                or EXCLUDE.search(league) or not ev.get("homeName") or not ev.get("awayName"):
            continue
        g = Game("pmu", start, ev["homeName"], ev["awayName"], league, "https://www.pmu.fr/sport/event/%s" % ev["id"], ev["id"])
        for b in item.get("betOffers", []):
            pmu_market(g, b)
        out.append(g)
    return out


def detail_pmu(g, d):
    raw = page("%s/betoffer/event/%s.json?%s" % (pmu.API, g.eid, pmu.PARAMS))
    if raw is None:
        return False
    for b in json.loads(raw).get("betOffers", []):
        pmu_market(d, b)
    return True


# ------------------------------------------------------------------ Winamax
WINAMAX_PLAYER = {"Buteur": "sc", "Marque 2 buts ou plus": "sc2", "Premier buteur": "fs", "1 passes décisives ou plus": "as",
                  "Joueur décisif": "dec"}


def winamax_market(g, title, outs):
    if title == "Résultat":
        for o, x in outs:
            s = who(g, o.get("label"))
            if s:
                g.add("res|" + s, x)
    elif title == "Vainqueur (remboursé si match nul)":
        for o, x in outs:
            s = g.side(o.get("label"))
            if s:
                g.add("dnb|" + s, x)
    elif title == "Nombre de buts" or title.startswith("Nombre de buts de "):
        s = g.side(title[len("Nombre de buts de "):]) if title != "Nombre de buts" else None
        for o, x in outs:
            side, line = ou(o.get("label"))
            if side and title == "Nombre de buts":
                g.add("tot|%s|%s" % (line, side), x)
            elif side and s:
                g.add("tt|%s|%s|%s" % (s, line, side), x)
    elif title == "Écart de buts (handicap)":
        rows = []
        for o, x in outs:
            m = re.match(r"^(.*\S)\s+([+-][\d.,]+)$", o.get("label") or "")
            rows.append((m and g.side(m.group(1)), m and key_line(m.group(2)), plain(o.get("label")) in DRAW, x))
        if not any(r[2] for r in rows):  # deux issues : handicap asiatique
            for s, line, _, x in rows:
                if s:
                    g.add("hcp|%s|%s" % (s, line), x)
    elif title == "Les 2 équipes marquent":
        for o, x in outs:
            lab = plain(o.get("label"))
            if lab in ("oui", "non"):
                g.add("btts|" + ("yes" if lab == "oui" else "no"), x)
    elif title in WINAMAX_PLAYER:
        for o, x in outs:
            g.player(WINAMAX_PLAYER[title], o.get("label"), x)


def winamax_bets(g, st, mid, only=None):
    outcomes, odds = st.get("outcomes", {}), st.get("odds", {})
    for bid, b in st.get("bets", {}).items():
        if b.get("matchId") != mid or not b.get("available") or (only and str(bid) != str(only)):
            continue
        outs = [(outcomes.get(str(o)) or {}, odds.get(str(o))) for o in b.get("outcomes") or []]
        winamax_market(g, b.get("betTitle") or "", [(o, x) for o, x in outs if o.get("available", True)])


def list_winamax(sp):
    st = winamax.state(get("%s/sports/%s" % (winamax.BASE, sp["winamax"])))
    tours = st.get("tournaments", {})
    out = []
    for mt in st.get("matches", {}).values():
        if mt.get("sportId") != sp["winamax"] or mt.get("status") != "PREMATCH":
            continue
        league = (tours.get(str(mt.get("tournamentId"))) or {}).get("tournamentName", "")
        start = datetime.fromtimestamp(mt["matchStart"], timezone.utc)
        if not now() < start < now() + timedelta(hours=DETAIL_H["winamax"]) or EXCLUDE.search(league):
            continue
        g = Game("winamax", start, mt["competitor1Name"], mt["competitor2Name"], league,
                 "%s/match/%s" % (winamax.BASE, mt["matchId"]), mt["matchId"])
        winamax_bets(g, st, mt["matchId"], only=mt.get("mainBetId"))
        out.append(g)
    return out


def detail_winamax(g, d):
    raw = page(g.url)
    if raw is None:
        return False
    winamax_bets(d, winamax.state(raw), int(g.eid))
    return True


# ------------------------------------------------------------------ Betclic (pages rendues côté serveur)
BETCLIC_PLAYER = {"Buteur (t. rég)": "sc", "Joueur décisif (buteur ou passeur décisif dans le t. rég)": "dec"}


def betclic_market(g, name, sels):
    if name == "Résultat du match (t. rég)":
        for lab, x in sels:
            s = who(g, lab)
            if s:
                g.add("res|" + s, x)
    elif name == "Nombre total de buts (t. rég)" or name.startswith("Total de buts (t. rég) - "):
        s = g.side(name[len("Total de buts (t. rég) - "):]) if name.startswith("Total") else None
        for lab, x in sels:
            side, line = ou(lab)
            if side and not name.startswith("Total"):
                g.add("tot|%s|%s" % (line, side), x)
            elif side and s:
                g.add("tt|%s|%s|%s" % (s, line, side), x)
    elif name == "Les 2 équipes marquent (t. rég)":
        for lab, x in sels:
            if plain(lab) in ("oui", "non"):
                g.add("btts|" + ("yes" if plain(lab) == "oui" else "no"), x)
    elif name in BETCLIC_PLAYER:
        for lab, x in sels:
            g.player(BETCLIC_PLAYER[name], lab, x)


def betclic_sels(selections):
    out = []
    for sel in selections:
        s = (sel.get("selectionOneof") or {}).get("selection") or sel
        if s.get("status") == 1 and s.get("odds"):
            out.append(((s.get("name") or "").strip(), s.get("odds")))
    return out


def list_betclic(sp):
    pages = [get(BETCLIC + sp["betclic"])]
    for c in sp["betclic_comps"]:
        try:
            pages.append(get("%s%s/%s" % (BETCLIC, sp["betclic"], c)))
        except urllib.error.HTTPError:  # championnat absent ce jour-là ; un refus (Blocked) arrête tout le bookmaker
            pass
    links, matches = {}, {}
    for h in pages:
        links.update({mid: path for path, mid in re.findall(r'href="(%s/[a-z0-9-]+-c\d+/[a-z0-9-]+-m(\d+))"' % re.escape(sp["betclic"]), h)})
        matches.update({str(m["matchId"]): m for p in bc_state(h) for m in p.get("matches") or []})
    out = []
    for mid, mt in matches.items():
        league = (mt.get("competition") or {}).get("name", "")
        start = utc(mt["matchDateUtc"])
        home, _, away = (mt.get("name") or "").partition(" - ")
        if mid not in links or mt.get("isLive") or not now() < start < now() + timedelta(hours=DETAIL_H["betclic"]) \
                or EXCLUDE.search(league) or not away:
            continue
        g = Game("betclic", start, home, away, league, BETCLIC + links[mid], mid)
        mk = mt.get("market") or {}
        betclic_market(g, mk.get("name") or "", betclic_sels(mk.get("mainSelections") or []))
        out.append(g)
    return out


def detail_betclic(g, d):
    raw = page(g.url)
    if raw is None:
        return False
    for p in bc_state(raw):
        for sc in (p.get("match") or {}).get("subCategories") or []:
            for mk in sc.get("markets") or []:
                sels = [s for row in mk.get("selectionMatrix") or [] for s in row.get("selections") or []]
                betclic_market(d, mk.get("name") or "", betclic_sels(sels))
    return True


# ------------------------------------------------------------------ Unibet (page de chaque match : script #serverApp-state)
def unibet_market(g, gd, md, outs):
    if gd == "1 N 2 - 90 Mins":
        for d, o in outs:
            s = who(g, d)
            if s:
                g.add("res|" + s, o.get("price"))
    elif gd == "Plus / Moins Buts - 90 Mins":
        for d, o in outs:
            side, line = ou(d)
            if side:
                g.add("tot|%s|%s" % (line, side), o.get("price"))
    elif gd == "Plus / Moins Buts Equipe - 90 Mins":
        m = re.match(r"^Plus / Moins But\(s\) - (.*\S)\s+[\d.,]+$", md)
        s = m and g.side(m.group(1))
        for d, o in outs:
            side, line = ou(d)
            if s and side:
                g.add("tt|%s|%s|%s" % (s, line, side), o.get("price"))
    elif gd == "Face à Face Handicap Buts - 90 Mins":
        for d, o in outs:
            s = g.side(d.split(" [")[0])
            if s and o.get("spread") is not None:
                g.add("hcp|%s|%s" % (s, key_line(o["spread"])), o.get("price"))
    elif gd == "1N2 Handicap - 90 Mins":
        home_line = None
        for d, o in outs:  # « Man. City [-4] » (ligne de l'équipe), « N (Man. City [-4]) », « Naples [+4] »
            s = g.side(d.split(" [")[0])
            if s in ("home", "away") and o.get("spread") is not None:
                home_line = num(o["spread"]) if s == "home" else 0.0 - num(o["spread"])
        for d, o in outs:
            s = "draw" if d.startswith("N (") else g.side(d.split(" [")[0])
            if s and home_line is not None:
                g.add("eh|%s|%s" % (key_line(home_line), s), o.get("price"))
    elif gd == "Les 2 équipes marquent - 90 Mins":
        for d, o in outs:
            if plain(d) in ("oui", "non"):
                g.add("btts|" + ("yes" if plain(d) == "oui" else "no"), o.get("price"))
    elif gd == "Buteurs - 90 Mins" and md in ("Buteur", "1er Buteur"):
        for d, o in outs:
            g.player("sc" if md == "Buteur" else "fs", d, o.get("price"))
    elif gd == "Buteurs Multiples - 90 Mins" and md == "Buteur 2+":
        for d, o in outs:
            g.player("sc2", d, o.get("price"))
    # passeurs et joueur décisif : « remboursé si non titulaire » chez Unibet, règle différente des autres : pas comparés


def list_unibet(sp):
    out = []
    for c in sp["unibet_comps"]:
        try:
            h = get("%s%s/%s" % (UNIBET, sp["unibet"], c))
        except urllib.error.HTTPError:
            continue
        for path, eid, slug in set(re.findall(r'href="(%s/%s/(\d+)/([a-z0-9-]+-vs-[a-z0-9-]+))"' % (re.escape(sp["unibet"]), re.escape(c)), h)):
            home, _, away = slug.partition("-vs-")
            out.append(Game("unibet", None, home.replace("-", " "), away.replace("-", " "), c.split("/")[-1], UNIBET + path, eid))
    return out


def detail_unibet(g, d):
    raw = page(g.url)
    m = raw and re.search(r'<script[^>]*id="serverApp-state"[^>]*>(.*?)</script>', raw, re.S)
    evs = (json.loads(m.group(1)).get("EventsDetail", {}).get("events") or []) if m else []
    if not evs:
        return False
    ev = evs[0]
    d.start = utc(ev["parsedStart"])
    a, _, b = (ev.get("description") or "").partition(" vs ")
    if a and b:
        d.home, d.away = a, b
    for group in ev.get("groupedMarkets") or []:
        gd = group.get("description") or ""
        for market in group.get("markets") or []:
            outs = [((o.get("description") or "").strip(), o) for o in market.get("outcomes") or []
                    if not o.get("suspended") and not o.get("hidden") and str(o.get("eventId", g.eid)) == str(g.eid)]
            unibet_market(d, gd, market.get("description") or "", outs)
    return True


LISTS = {"pmu": list_pmu, "winamax": list_winamax, "betclic": list_betclic, "unibet": list_unibet}


def get_list(b, sp, cache):
    """Liste des matchs d'un bookmaker ; celles de Betclic et d'Unibet (une page par championnat) sont gardées une heure.
    Une liste gardée ne garde pas ses cotes : seules comptent alors celles des pages de match, datées."""
    kept = cache.setdefault("lists", {}).get(b)
    if b in LIST_EVERY and kept and now() - utc(kept["at"]) < timedelta(minutes=LIST_EVERY[b]):
        games = [Game(b, utc(t) if t else None, h, a, lg, u, e) for h, a, t, lg, u, e in kept["games"]]
        return [g for g in games if not g.start or g.start > now()]
    games = LISTS[b](sp)
    if b in LIST_EVERY:
        cache["lists"][b] = {"at": iso(now()), "games": [[g.home, g.away, iso(g.start) if g.start else None, g.league, g.url, g.eid]
                                                         for g in games]}
    return games
DETAILS = {"pmu": detail_pmu, "winamax": detail_winamax, "betclic": detail_betclic, "unibet": detail_unibet}


# ------------------------------------------------------------------ rapprochement des matchs
def score(x, y):
    """Ressemblance de deux noms d'équipe : mots communs, puis abréviations (0 : rien en commun)."""
    common = len(x & y)
    return 2 * common + 1 if common else 1 if close(x, y) else 0


def same(a, b):
    """→ None (pas le même match) ou inversé ? Le sens qui ressemble le plus l'emporte (derbys : Real / Atlético Madrid)."""
    if a.start and b.start and abs((a.start - b.start).total_seconds()) > 2.5 * 3600:
        return None
    ah, aa, bh, ba = tokens(a.home), tokens(a.away), tokens(b.home), tokens(b.away)
    d1, d2, i1, i2 = score(ah, bh), score(aa, ba), score(ah, ba), score(aa, bh)
    direct, inverse = (d1 + d2 if d1 and d2 else 0), (i1 + i2 if i1 and i2 else 0)
    if direct > inverse:
        return False
    if inverse > direct:
        return True
    return None


def group_games(per_book):
    groups = []  # [(référence, {book: (Game, inversé)})]
    for b in ("pmu", "winamax", "betclic"):  # bookmakers qui donnent l'heure du match dans leur liste
        for g in per_book.get(b, []):
            best = None
            for ref, members in groups:
                inv = same(ref, g)
                if inv is not None and b not in members:
                    best = (ref, members, inv)
                    break
            if best:
                best[1][b] = (g, best[2])
            else:
                groups.append((g, {b: (g, False)}))
    for g in per_book.get("unibet", []):  # Unibet : noms tirés de l'adresse, heure connue seulement par la page du match
        cands = [(ref, members, inv) for ref, members in groups if "unibet" not in members
                 and ref.start < now() + timedelta(hours=DETAIL_H["unibet"]) for inv in [same(ref, g)] if inv is not None]
        if len(cands) == 1:
            cands[0][1]["unibet"] = (g, cands[0][2])
    return groups


# ------------------------------------------------------------------ lecture des pages de match (avec cache)
def ttl(start):
    left = start - now()
    return timedelta(minutes=10 if left <= timedelta(hours=3) else 40 if left <= timedelta(hours=12) else 120)


def load_cache(per_book, cache):
    for b, games in per_book.items():
        for g in games:
            c = cache.get(b, {}).get(g.eid)
            if c:
                g.detail, g.detail_at = c["sel"], utc(c["at"])
                g.detail_full = {k: set(v) for k, v in c.get("full", {}).items()}
                if g.start is None and c.get("start"):
                    g.start = utc(c["start"])


def read_detail(b, g):
    d = Game(b, g.start, g.home, g.away, g.league, g.url, g.eid)
    if not DETAILS[b](g, d):
        return False
    g.detail, g.detail_full, g.detail_at = d.sel, d.full, now()
    if b == "unibet":
        g.start = d.start
    return True


def fetch_details(todo):
    """todo : {book: [Game]} → pages lues, un fil par bookmaker ; un refus (403) arrête ce bookmaker pour ce passage."""
    def run(b, games):
        n = 0
        for g in games:
            try:
                n += read_detail(b, g)
            except Blocked as e:
                log("%s : refus (%s), on s'arrête là pour ce bookmaker" % (b, e))
                break
            except Exception as e:  # noqa: BLE001
                log("%s : page illisible (%s) %s" % (b, type(e).__name__, g.url))
        return n
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = {b: pool.submit(run, b, games) for b, games in todo.items() if games}
        return {b: f.result() for b, f in futs.items()}


def plan(groups, cache_hint=None):
    """Pages de match à lire à ce passage : matchs vus chez 2 bookmakers ou plus, les plus suivis puis les plus proches."""
    todo = {b: [] for b in BUDGET}
    for ref, members in sorted(groups, key=lambda gm: (-len(gm[1]), gm[0].start)):
        if len(members) < 2:
            continue
        for b, (g, _) in members.items():
            start = g.start or ref.start
            if start > now() + timedelta(hours=DETAIL_H[b]):
                continue
            if (not g.detail_at or now() - g.detail_at > ttl(start)) and len(todo[b]) < BUDGET[b]:
                todo[b].append(g)
    return todo


# ------------------------------------------------------------------ comparaison
SWAP = {"home": "away", "away": "home"}
PLAYER_KINDS = {"sc": "Buteur", "sc2": "Doublé (2 buts ou +)", "fs": "Premier buteur", "as": "Passeur décisif",
                "dec": "Buteur ou passeur"}


def swap(key):
    k = [SWAP.get(t, t) for t in key.split("|")]
    if k[0] == "eh":
        k[1] = key_line(0.0 - num(k[1]))
    return "|".join(k)


def views(members):
    """{book: (cotes vues du côté de la référence, Game, inversé)} ; un joueur ambigu chez un bookmaker est écarté."""
    out = {}
    for b, (g, inv) in members.items():
        names = g.names()
        bad = {k for k, v in names.items() if len({pkey(n) and plain(n).replace(".", "") for n in v}) > 1
               and len({plain(n).split()[0][:1] for n in v if plain(n).split()}) > 1}
        sel = {swap(k) if inv else k: o for k, o in g.view().items() if k.split("|", 1)[-1] not in bad}
        out[b] = (sel, g, inv)
    return out


def describe(key, home, away):
    k = key.split("|")
    team = lambda s: {"home": home, "away": away, "draw": "match nul"}.get(s, s)
    side = lambda s: "Plus" if s == "over" else "Moins"
    ln = lambda x: ("+" if num(x) > 0 else "") + x.replace(".", ",")
    if k[0] == "res":
        return "Résultat : %s" % team(k[1])
    if k[0] == "dnb":
        return "Vainqueur (remboursé si nul) : %s" % team(k[1])
    if k[0] == "tot":
        return "Total de buts : %s de %s" % (side(k[2]), k[1].replace(".", ","))
    if k[0] == "tt":
        return "Buts de %s : %s de %s" % (team(k[1]), side(k[3]), k[2].replace(".", ","))
    if k[0] == "hcp":
        return "Handicap : %s %s" % (team(k[1]), ln(k[2]))
    if k[0] == "eh":
        return "Handicap 3 issues (%s %s) : %s" % (home, ln(k[1]), team(k[2]))
    if k[0] == "btts":
        return "Les 2 équipes marquent : %s" % ("oui" if k[1] == "yes" else "non")
    if k[0] in PLAYER_KINDS:
        return "%s : %s" % (PLAYER_KINDS[k[0]], k[1].title())
    return key


def player_name(v, pk):
    names = [n for sel, g, inv in v.values() for k, ns in g.names().items() if k == pk for n in ns]
    return max(names, key=len, default=pk.title())


def balanced(sel, prefix):
    """Ligne à l'équilibre d'un bookmaker pour un total (prefix « tot| » ou « tt|home| ») → (ligne, Plus, Moins) ou None."""
    lines = {}
    for key, o in sel.items():
        if key.startswith(prefix):
            line, side = key[len(prefix):].split("|")
            lines.setdefault(line, {})[side] = o
    cands = [(abs(v["over"] - v["under"]), num(l), v["over"], v["under"]) for l, v in lines.items() if "over" in v and "under" in v]
    best = min(cands) if cands else None
    return best[1:] if best and best[0] <= alerts.GAP_BALANCE else None


def favourite(v):
    avg = {}
    for side in ("home", "away"):
        x = [sel["res|" + side] for sel, g, inv in v.values() if "res|" + side in sel]
        if x:
            avg[side] = sum(x) / len(x)
    side = min(avg, key=avg.get) if avg else None
    return (side, avg[side]) if side and avg[side] <= HCP_FAV else None


def detail_fresh(g):
    return bool(g.detail_at and now() - g.detail_at <= timedelta(minutes=FRESH_MIN))


def usual(odds):
    """Écart habituel entre deux sites, par marché, dans ce match : {(sorte, site1, site2): médiane de cote1 / cote2}.
    Winamax paie par exemple les passeurs décisifs ~15 % plus cher que PMU sur tous les joueurs : ce n'est pas une
    anomalie, seul ce qui dépasse nettement cet écart habituel compte."""
    pairs = collections.defaultdict(list)
    for key, by in odds.items():
        items = sorted(by.items())
        for i, (b1, o1) in enumerate(items):
            for b2, o2 in items[i + 1:]:
                if max(o1, o2) <= 8:
                    pairs[(key.split("|")[0], b1, b2)].append(o1 / o2)
    return {k: statistics.median(r) for k, r in pairs.items() if len(r) >= 5}


def outlier(key, by, base):
    """→ (meilleur site, cote, 2e site) si un site sort nettement du lot sur cette sélection, sinon None."""
    if len(by) < 2:
        return None
    (bb, best), (sb, second) = sorted(by.items(), key=lambda kv: -kv[1])[:2]
    kind = key.split("|")[0]
    edge, top = (PLAYER_EDGE, PLAYER_MAX) if kind in PLAYER_KINDS else (EDGE, MAX_ODDS)
    b1, b2 = sorted((bb, sb))
    norm = base.get((kind, b1, b2), 1.0)
    norm = norm if bb == b1 else 1 / norm
    if best <= top and best >= second * edge and best >= second * norm * REL_EDGE:
        return bb, best, sb
    return None


def compare(groups):
    """→ (cotes anormales, écarts de ligne sur les totaux, handicaps, pages à relire pour confirmer)."""
    found, lines, hcps, stale = [], [], [], set()
    for ref, members in groups:
        if len(members) < 2:
            continue
        v = views(members)
        odds = {}
        for b, (sel, g, inv) in v.items():
            for key, o in sel.items():
                odds.setdefault(key, {})[b] = o
        base = usual(odds)
        for key, by in odds.items():
            hit = outlier(key, by, base)
            if hit:
                bb, best, sb = hit
                raw = lambda b: swap(key) if v[b][2] else key
                if v[bb][1].fresh(raw(bb)) and v[sb][1].fresh(raw(sb)):
                    found.append((ref, v, key, bb, best, by))
                else:
                    stale.update((b, v[b][1]) for b in (bb, sb) if not v[b][1].fresh(raw(b)))
        # écarts de ligne : total de buts, buts de chaque équipe
        for prefix, label in (("tot|", "Total de buts"), ("tt|home|", "Buts de %s" % ref.home), ("tt|away|", "Buts de %s" % ref.away)):
            bal = {b: x for b, x in ((b, balanced(sel, prefix)) for b, (sel, g, inv) in v.items()) if x}
            if len(bal) < 2:
                continue
            lo_b, hi_b = min(bal, key=lambda b: bal[b][0]), max(bal, key=lambda b: bal[b][0])
            if bal[hi_b][0] - bal[lo_b][0] >= LINE_GAP:
                if detail_fresh(v[lo_b][1]) and detail_fresh(v[hi_b][1]):
                    lines.append(dict(ref=ref, v=v, prefix=prefix, label=label, bal=bal, lo_b=lo_b, hi_b=hi_b))
                else:
                    stale.update((b, v[b][1]) for b in (lo_b, hi_b) if not detail_fresh(v[b][1]))
        # handicap asiatique : écart entre bookmakers, ou favori qui reçoit des buts
        bal = {b: x for b, x in ((b, europe.hcp_main(sel)) for b, (sel, g, inv) in v.items()) if x}
        if not bal:
            continue
        fav = favourite(v)
        side = fav[0] if fav else "home"
        sign = 1 if side == "home" else -1
        fair = {b: sign * x[0] for b, x in bal.items()}
        shown = {b: sign * x[1] for b, x in bal.items()}
        flipped = [b for b in bal if fav and fair[b] >= HCP_FLIP]
        lo_b, hi_b = min(bal, key=lambda b: bal[b][0]), max(bal, key=lambda b: bal[b][0])
        gap = bal[hi_b][0] - bal[lo_b][0]
        if flipped or (len(bal) > 1 and gap >= LINE_GAP):
            involved = set(flipped) | ({lo_b, hi_b} if gap >= LINE_GAP else set())
            if all(detail_fresh(v[b][1]) for b in involved):
                hcps.append(dict(ref=ref, v=v, bal=bal, fav=fav, side=side, shown=shown, flipped=flipped,
                                 lo_b=lo_b, hi_b=hi_b, gap=gap, inverted=bool(flipped)))
            else:
                stale.update((b, v[b][1]) for b in involved if not detail_fresh(v[b][1]))
    return found, lines, hcps, stale


# ------------------------------------------------------------------ page « Écarts » et alertes
def page_gaps(sp_id, found, lines, hcps):
    out = []
    for ref, v, key, bb, best, by in found:
        k = key.split("|")
        if k[0] in PLAYER_KINDS:
            name, cat = player_name(v, k[1]), PLAYER_KINDS[k[0]]
        else:
            name, _, cat = describe(key, ref.home, ref.away).partition(" : ")
        out.append({"id": "%s|%s|%s|%s" % (ref.home, ref.away, ref.start.date(), key), "sport": sp_id, "kind": "odds",
                    "name": name, "stat": k[0], "cat": cat, "sel": "", "start": iso(ref.start), "home": ref.home,
                    "away": ref.away, "league": v[bb][1].league or ref.league, "best": bb,
                    "books": {b: {"odds": o, "url": v[b][1].url} for b, o in by.items()}})
    for x in lines:
        ref, v, prefix, bal = x["ref"], x["v"], x["prefix"], x["bal"]
        books = {}
        for b, (sel, g, inv) in v.items():
            ls = {}
            for key, o in sel.items():
                if key.startswith(prefix):
                    line, side = key[len(prefix):].split("|")
                    ls.setdefault(line, [None, None])[0 if side == "over" else 1] = o
            if ls:
                books[b] = {"main": key_line(bal[b][0]) if b in bal else None, "url": g.url, "lines": ls}
        out.append({"id": "%s|%s|%s|%s" % (ref.home, ref.away, ref.start.date(), prefix), "sport": sp_id, "kind": "prop",
                    "name": x["label"], "stat": prefix.split("|")[0], "cat": "Écart de ligne", "start": iso(ref.start),
                    "home": ref.home, "away": ref.away, "league": ref.league, "gap": bal[x["hi_b"]][0] - bal[x["lo_b"]][0],
                    "lo": x["lo_b"], "hi": x["hi_b"], "books": books})
    for h in hcps:
        ref, v, bal = h["ref"], h["v"], h["bal"]
        books = {}
        for b, (sel, g, inv) in v.items():
            ls = {}
            for key, o in sel.items():
                k = key.split("|")
                if k[0] == "hcp":
                    line = key_line(num(k[2]) if k[1] == "home" else 0.0 - num(k[2]))
                    ls.setdefault(line, [None, None])[0 if k[1] == "home" else 1] = o
            if ls:
                books[b] = {"main": key_line(bal[b][1]) if b in bal else None, "url": g.url, "lines": ls}
        out.append({"id": "%s|%s|%s|hcp" % (ref.home, ref.away, ref.start.date()), "sport": sp_id, "kind": "hcp",
                    "name": "Handicap", "cat": "Handicap", "start": iso(ref.start), "home": ref.home, "away": ref.away,
                    "league": ref.league, "gap": h["gap"], "lo": h["lo_b"], "hi": h["hi_b"], "inverted": h["inverted"],
                    "flipped": h["flipped"], "fav": (ref.home if h["side"] == "home" else ref.away) if h["fav"] else None,
                    "books": books})
    return out


def alert_keys(v, ref, key):
    """Une clé par façon d'écrire le match chez chaque bookmaker : pas de rappel quand la référence change de nom."""
    return sorted({alerts.subject_eu("%s|%s|%s|%s" % (((g.away, g.home) if inv else (g.home, g.away)) + (ref.start.date(), key)))
                   for sel, g, inv in v.values()})


def messages(sp, found, lines, hcps):
    ln = lambda x: ("%g" % x).replace(".", ",")
    sg = lambda x: ("+" if x > 0 else "") + ln(x)
    todo = []
    for ref, v, key, bb, best, by in found:
        rest = " · ".join("%s %s" % (BOOK_NAMES[b], alerts.odds_fr(o)) for b, o in sorted(by.items(), key=lambda kv: -kv[1]) if b != bb)
        what = describe(key, ref.home, ref.away)
        if key.split("|")[0] in PLAYER_KINDS:
            what = "%s : %s" % (PLAYER_KINDS[key.split("|")[0]], player_name(v, key.split("|")[1]))
        todo.append(("%s %s · %s – %s" % (sp["emoji"], BOOK_NAMES[bb], ref.home, ref.away),
                     "%s\n%s : %s\nAutres bookmakers : %s\n%s · %s" % (what, BOOK_NAMES[bb], alerts.odds_fr(best), rest,
                                                                       v[bb][1].league or ref.league, when(ref.start)),
                     alert_keys(v, ref, key), best))
    for x in lines:
        ref, bal, lo_b, hi_b = x["ref"], x["bal"], x["lo_b"], x["hi_b"]
        todo.append(("📏 %s · écart de ligne %s · %s – %s" % (x["label"], ln(bal[hi_b][0] - bal[lo_b][0]), ref.home, ref.away),
                     "Plus de %s chez %s : %s\nMoins de %s chez %s : %s\nToutes les lignes : %s\n%s · %s" % (
                         ln(bal[lo_b][0]), BOOK_NAMES[lo_b], alerts.odds_fr(bal[lo_b][1]), ln(bal[hi_b][0]), BOOK_NAMES[hi_b],
                         alerts.odds_fr(bal[hi_b][2]), " · ".join("%s %s" % (BOOK_NAMES[b], ln(l[0])) for b, l in sorted(bal.items(), key=lambda kv: kv[1][0])),
                         ref.league, when(ref.start)),
                     alert_keys(x["v"], ref, "gap|" + x["prefix"].rstrip("|")), bal[hi_b][0] - bal[lo_b][0]))
    for h in hcps:
        ref, bal, shown = h["ref"], h["bal"], h["shown"]
        fav_team = ref.home if h["side"] == "home" else ref.away
        rows = []
        if h["inverted"]:
            title = "🔄 Handicap inversé · %s – %s" % (ref.home, ref.away)
            for b in h["flipped"]:
                rows.append("%s : %s %s (cote %s) alors qu'il est favori" % (BOOK_NAMES[b], fav_team, sg(shown[b]),
                            alerts.odds_fr(bal[b][2] if h["side"] == "home" else bal[b][3])))
        else:
            title = "📏 Écart de handicap %s · %s – %s" % (ln(abs(bal[h["hi_b"]][1] - bal[h["lo_b"]][1])), ref.home, ref.away)
        if len(bal) > 1 and h["gap"] >= LINE_GAP:
            hi_b, lo_b = h["hi_b"], h["lo_b"]
            rows.append("%s %s chez %s : %s" % (ref.home, sg(bal[hi_b][1]), BOOK_NAMES[hi_b], alerts.odds_fr(bal[hi_b][2])))
            rows.append("%s %s chez %s : %s" % (ref.away, sg(0.0 - bal[lo_b][1]), BOOK_NAMES[lo_b], alerts.odds_fr(bal[lo_b][3])))
        rows.append("Handicap de %s : %s" % (fav_team, " · ".join("%s %s" % (BOOK_NAMES[b], sg(shown[b])) for b in sorted(bal, key=shown.get))))
        if h["fav"]:
            rows.append("Résultat : %s favori (cote moyenne %s)" % (fav_team, alerts.odds_fr(h["fav"][1])))
        rows.append("%s · %s" % (ref.league, when(ref.start)))
        todo.append((title, "\n".join(rows), alert_keys(h["v"], ref, "hcp"), h["gap"]))
    return todo


# ------------------------------------------------------------------ passage
def run_sport(sp_id, sp, cache, dry, diag):
    per_book = {}
    with ThreadPoolExecutor(max_workers=len(LISTS)) as pool:
        futs = {b: pool.submit(get_list, b, sp, cache) for b in LISTS}
        for b, fut in futs.items():
            try:
                per_book[b] = fut.result()
            except Exception as e:  # noqa: BLE001 — un bookmaker en panne ou qui refuse : on continue sans lui
                log("%s %s : liste illisible (%s)" % (sp["name"], b, e if isinstance(e, Blocked) else type(e).__name__))
                if not isinstance(e, (Blocked, urllib.error.HTTPError)):
                    traceback.print_exc()
    load_cache(per_book, cache)
    groups = group_games(per_book)
    read = fetch_details(plan(groups))
    groups = [(ref, {b: m for b, m in members.items() if b != "unibet" or not m[0].start or not ref.start
                     or abs((m[0].start - ref.start).total_seconds()) <= 2.5 * 3600}) for ref, members in groups]
    found, lines, hcps, stale = compare(groups)
    if stale:  # écart vu sur une cote ancienne : on relit ces pages tout de suite avant d'alerter
        again = {b: [g for b2, g in stale if b2 == b][:CONFIRM] for b in BUDGET}
        more = fetch_details(again)
        read = {b: read.get(b, 0) + more.get(b, 0) for b in BUDGET}
        found, lines, hcps, _ = compare(groups)
    for b, games in per_book.items():  # cache des pages de match (matchs pas encore commencés)
        cache[b] = {g.eid: {"at": iso(g.detail_at), "sel": g.detail, "full": {k: sorted(v) for k, v in g.detail_full.items()},
                            "start": iso(g.start) if g.start else None}
                    for g in games if g.detail_at and (g.start or now()) > now()}
    log("%s : listes %s ; %d matchs chez 2+ bookmakers ; pages lues %s ; %d cote(s) anormale(s), %d écart(s) de ligne, %d handicap(s)" % (
        sp["name"], " ".join("%s %d" % (b, len(per_book.get(b, []))) for b in LISTS),
        sum(1 for _, m in groups if len(m) >= 2), " ".join("%s %d" % (b, n) for b, n in read.items()), len(found), len(lines), len(hcps)))
    if diag:
        print("matchs par nombre de bookmakers :", collections.Counter(len(m) for _, m in groups))
        ratios = []
        for ref, members in groups:
            if len(members) < 2:
                continue
            odds = {}
            for b, (sel, g, inv) in views(members).items():
                for key, o in sel.items():
                    odds.setdefault(key, {})[b] = o
            for key, by in odds.items():
                if len(by) >= 2:
                    x = sorted(by.values(), reverse=True)
                    ratios.append((x[0] / x[1], key, ref, by))
        print("sélections comparées :", len(ratios), collections.Counter(r[1].split("|")[0] for r in ratios))
        for r, key, ref, by in sorted(ratios, key=lambda t: -t[0])[:20]:
            print("  %.2f  %s – %s  %s  %s" % (r, ref.home, ref.away, key, by))
    return page_gaps(sp_id, found, lines, hcps), messages(sp, found, lines, hcps)


def main():
    dry, diag = "--dry" in sys.argv, "--diag" in sys.argv
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    sent = state.setdefault("sent", {})
    for k in [k for k, v in sent.items() if datetime.fromisoformat(v["t"]) < now() - timedelta(days=3)]:
        del sent[k]
    recent = alerts.recent_subjects(sent, alerts.subject_eu)
    gaps, pushed = [], 0
    for sp_id, sp in (("foot", FOOT),):
        try:
            entries, todo = run_sport(sp_id, sp, cache.setdefault(sp_id, {}), dry, diag)
        except Exception as e:  # noqa: BLE001
            log(sp["name"], ": erreur", type(e).__name__)
            traceback.print_exc()
            continue
        gaps += entries
        todo = [t for t in todo if not recent.intersection(t[2])]
        if dry:
            for t in todo:
                print("---\n" + t[0] + "\n" + t[1])
            log("%s : %d alerte(s) (essai à blanc, rien envoyé)" % (sp["name"], len(todo)))
            continue
        for title, msg, ks, v in todo[:10]:
            alerts.push(title, msg, sp["tag"])
            sent.update({k: {"v": v, "t": now().isoformat()} for k in ks})
        if len(todo) > 10:
            alerts.push("%d autres écarts · %s" % (len(todo) - 10, sp["name"]),
                        "\n".join("• " + t[0][2:] + " — " + t[1].split("\n")[0] for t in todo[10:30]), sp["tag"])
            for t in todo[10:]:
                sent.update({k: {"v": t[3], "t": now().isoformat()} for k in t[2]})
        pushed += len(todo)
    PAGE.parent.mkdir(exist_ok=True)
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, separators=(",", ":")))  # pages déjà lues, même à blanc
    if dry:
        return
    PAGE.write_text(json.dumps({"at": iso(now()), "sports": ["foot"], "gaps": gaps}, ensure_ascii=False))
    STATE.write_text(json.dumps(state, ensure_ascii=False))
    log("%d alerte(s) envoyée(s)" % pushed)


if __name__ == "__main__":
    main()
