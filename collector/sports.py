#!/usr/bin/env python3
"""
Tous sports, paris annexes — alertes et page « Écarts » : cote anormale (≥ 50 % au-dessus du 2e meilleur bookmaker
sur la même sélection), écart de ligne sur les totaux ; sur les handicaps, seulement les surebets (≥ 1 % de gain
garanti, en jouant les deux côtés chez deux bookmakers) et les énormes erreurs (cote au moins 2 fois plus haute
qu'ailleurs). Foot, hockey, foot US, handball, volley, snooker, tennis ; le basket et le rugby
ont déjà leurs tâches (europe.py, rugby.py).

Bookmakers : PMU (flux Kambi), Winamax, Betclic (pages rendues côté serveur), Unibet (foot seulement) — les mêmes sites
que pour la NBA, donc on les ménage :
  - à chaque passage, la liste des matchs de chaque sport (une page par bookmaker ; les pages de championnat de Betclic
    et d'Unibet sont gardées une heure) ;
  - puis la page de chaque match, seulement pour les matchs des prochaines heures vus chez au moins 2 bookmakers, dans
    la limite de pages par bookmaker et par passage (TOTAL pour tous les sports, CAP pour chacun ; matchs vus chez le
    plus de bookmakers d'abord, puis les plus proches) ; une page lue est gardée en cache et relue à chaque passage
    (11 min) dans les 3 h avant le match, toutes les 40 min avant, toutes les 2 h au-delà de 12 h ;
  - une alerte ne part que sur des cotes lues il y a moins de FRESH_MIN minutes chez les bookmakers concernés : sinon
    leurs pages sont relues tout de suite et l'écart revérifié.

Sélections comparées (clés normalisées côté « home »/« away » de chaque bookmaker ; « ~sets », « ~manches »… quand
l'unité n'est pas celle du sport ; « ~ot » quand les prolongations comptent alors que les marchés principaux du sport
ne les comptent pas, comme au hockey) :
  res|home       résultat à 3 issues                    win|home        vainqueur (2 issues, prolongations comprises)
  dnb|home       remboursé si match nul                 btts|yes        les 2 équipes marquent
  tot|2.5|over   total                                  tt|home|1.5|over   total d'une équipe
  hcp|home|-1.5  handicap (Betclic : « gagne de 2 ou + » = -1,5 ; « ne perd pas ou perd de 1 ou - » = +1,5)
  eh|-1|draw     handicap à 3 issues (ligne de « home »)
  joueurs : sc|e haaland buteur, sc2 2 buts ou +, fs premier buteur, as passeur, dec buteur ou passeur (foot) ;
            sc, sc2, fs, hp2 2 points ou +, ha1 1 passe ou + (hockey) ; td, td2, ftd touchdowns,
            ydrec|j love|74.5|over yards à la réception, ydpass à la passe, ydrush à la course (foot US)

Usage : python3 collector/sports.py [--dry] [--diag] [--only hockey,nfl]
        (--dry : rien n'est envoyé ; seul le cache des pages est gardé)
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
DETAIL_H = {"pmu": 24, "winamax": 24, "betclic": 24, "unibet": 6}  # pages de match lues jusqu'à N h avant le coup d'envoi
TOTAL = {"pmu": 140, "winamax": 44, "betclic": 24, "unibet": 6}    # pages de match par passage, tous sports confondus
CAP = {"pmu": 25, "winamax": 8, "betclic": 5}                       # … et pour chaque sport autre que le foot
CONFIRM = 8          # pages relues en plus par bookmaker pour confirmer un écart
LIST_EVERY = {"betclic": 60, "unibet": 60}  # minutes : listes en plusieurs pages (une par championnat), relues 1 fois par heure
MAX_PUSH = 10        # alertes envoyées une par une à chaque passage ; au-delà, un seul message les regroupe
STATE = HERE / "build" / "sports-state.json"
CACHE = HERE / "build" / "cache-sports.json"
PAGE = HERE / "build" / "gaps-sports.json"  # écarts en cours, publiés sur le site par collect.py
BOOK_NAMES = {"pmu": "PMU", "winamax": "Winamax", "betclic": "Betclic", "unibet": "Unibet"}
BETCLIC = "https://www.betclic.fr"
UNIBET = "https://www.unibet.fr"
EXCLUDE = re.compile(r"virtu|e-?soccer|\besports?\b|cyber|\bfifa\b|\bsrl\b|simulat", re.I)  # matchs virtuels
UNITS = ("buts", "points", "jeux", "manches", "sets")

# Sports : flux PMU (Kambi), identifiant Winamax, pages Betclic (et Unibet pour le foot), unité principale,
# qualificatifs PMU qui valent « match entier » (full) ou « prolongations comprises » (ot), écarts de ligne minimaux
SPORTS = {
    "foot": {"name": "Foot", "label": "en football", "emoji": "⚽", "tag": "soccer", "unit": "buts",
             "pmu": "football", "winamax": 1, "betclic": "/football-sfootball",
             "betclic_comps": ["ligue-1-mcdonald-s-c4", "ligue-2-bkt-c19", "angl-premier-league-c3", "espagne-laliga-c7",
                               "italie-serie-a-c6", "allemagne-bundesliga-c5", "ligue-des-champions-c8", "ligue-europa-c3453",
                               "ligue-conference-c28946"],
             "unibet": "/paris-football",
             "unibet_comps": ["france/ligue-1-mcdonalds", "france/ligue-2-bkt", "angleterre/premier-league", "espagne/laliga",
                              "italie/serie-a", "allemagne/bundesliga-1", "coupes-d-europe/ligue-des-champions",
                              "coupes-d-europe/ligue-europa", "coupes-d-europe/ligue-conference"],
             "gap": {"tot": 1, "tt": 1},
             "cap": {"pmu": 80, "winamax": 24, "betclic": 12, "unibet": 6}},
    "hockey": {"name": "Hockey", "label": "en hockey", "emoji": "🏒", "tag": "ice_hockey", "unit": "buts",
               "pmu": "ice_hockey", "winamax": 4, "betclic": "/hockey-sur-glace-sice_hockey", "betclic_comps": ["nhl-c83"],
               "full": {"Temps réglementaire"}, "ot": {"Prolongations et séance de tirs au but incluses"},
               "gap": {"tot": 1, "tt": 1}},
    "nfl": {"name": "Foot US", "label": "en foot US", "emoji": "🏈", "tag": "football", "unit": "points",
            "pmu": "american_football", "winamax": 16, "betclic": "/football-americain-samerican_football",
            "betclic_comps": ["nfl-c84"], "full": {"", "Prolongations incluses", "Prolongations Incluses"},
            "gap": {"tot": 3, "tt": 3, "ydpass": 20, "ydrec": 12, "ydrush": 12}},
    "handball": {"name": "Handball", "label": "en handball", "emoji": "🤾", "tag": "handball_person", "unit": "buts",
                 "pmu": "handball", "winamax": 6, "betclic": "/handball-shandball",
                 "betclic_comps": ["ligue-des-champions-h-c110", "ligue-europeenne-ehf-h-c7490", "allemagne-bundesliga-1-h-c252"],
                 "gap": {"tot": 3, "tt": 2}},
    "volley": {"name": "Volley", "label": "en volley", "emoji": "🏐", "tag": "volleyball", "unit": "points",
               "pmu": "volleyball", "winamax": 23, "betclic": "/volley-ball-svolleyball", "betclic_comps": [],
               # points : 3, 4 ou 5 sets, deux bosses (≈ 140 sur un 3-0, ≈ 185 en 4 sets) ; deux lignes « à l'équilibre »
               # très différentes peuvent être justes toutes les deux : pas d'écart de ligne en points, seulement en sets
               "gap": {"tot~sets": 1}},
    "snooker": {"name": "Snooker", "label": "en snooker", "emoji": "🎱", "tag": "8ball", "unit": "manches",
                "pmu": "snooker", "winamax": 19, "betclic": "/snooker-ssnooker", "betclic_comps": [],
                "gap": {"tot": 2}},
    "tennis": {"name": "Tennis", "label": "en tennis", "emoji": "🎾", "tag": "tennis", "unit": "jeux",
               "pmu": "tennis", "winamax": 5, "betclic": "/tennis-stennis", "betclic_comps": [],
               "strict": True,  # « Handicap » sans unité chez PMU : jeux ou sets ? Pas comparé tant qu'on ne l'a pas vu
               "gap": {}},  # jeux : même souci que les points au volley (deux bosses selon le nombre de sets)
}

# noms d'équipes : mots sans valeur pour le rapprochement, et noms français ou abrégés → nom local
europe.GENERIC.discard("city")  # Manchester City / Manchester United
europe.GENERIC |= {"fc", "afc", "cf", "sc", "ssc", "ac", "us", "rc", "sv", "fk", "nk", "calcio", "cd", "ud", "sd", "rcd",
                   "ogc", "osc", "sco", "fsv", "spvgg", "vfl", "vfb", "tsg", "bsc", "ec", "se", "cr", "sad", "1907", "1899",
                   "1846", "1909", "1913", "1860", "1904", "1900", "1910", "04", "05", "09", "96", "98", "01",
                   "hc", "hk", "kc", "vc", "vk", "ks", "thw"}
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


def kind_parts(kind):
    """« tot~sets~ot » → (« tot », « sets » ou None, prolongations comprises ?)."""
    base, *mods = kind.split("~")
    return base, next((m for m in mods if m in UNITS), None), "ot" in mods


def kind_of(sp, base, unit=None, tag=""):
    """Clé d'un marché : « tot » dans l'unité du sport, « tot~sets » sinon, « …~ot » prolongations comprises."""
    unit = (unit or sp["unit"]).lower()
    return base + ("" if unit == sp["unit"] else "~" + unit) + (tag or "")


class Game(EGame):
    def __init__(self, book, start, home, away, league, url, eid=None, sp=None):
        super().__init__(book, start, home, away, league, url)
        self.eid = str(eid) if eid is not None else None
        self.sp = sp or {}
        self.full = {}                      # clé joueur → noms écrits par ce bookmaker
        self.detail, self.detail_full, self.detail_at = {}, {}, None   # page du match (cache)

    def player(self, kind, name, odds):
        k = pkey(name)
        if k and plain(name).strip() not in DRAW and "aucun" not in plain(name):
            self.full.setdefault(k, set()).add(" ".join(name.split()))
            self.add("%s|%s" % (kind, k), odds)

    def player_line(self, kind, name, line, side, odds):
        k = pkey(name)
        if k and side:
            self.full.setdefault(k, set()).add(" ".join(name.split()))
            self.add("%s|%s|%s|%s" % (kind, k, line, side), odds)

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
PMU_WIN = {"Temps réglementaire", "Cotes du match", "Prolongations incluses", "Tie Vainqueur"}
PMU_HCP = {"Handicap": None, "Set Handicap": "sets", "Handicap de sets": "sets", "Handicap Manche": "manches",
           "Handicap de manches": "manches", "Handicap de jeux": "jeux", "Handicap Jeux": "jeux"}
PMU_TOT = re.compile(r"^(?:Nombre total de|Total de|Total des) (buts|points|jeux|manches|sets)$", re.I)
PMU_TT = re.compile(r"^(?:Nombre total de|Total de|Total des) (buts|points|jeux|manches|sets) par (.+)$", re.I)
PMU_PLAYER = [("Marque ou effectue une passe décisive", "dec"), ("Effectue une passe décisive", "as"),
              ("Marque au moins 2 buts", "sc2"), ("1er buteur", "fs"), ("Marque", "sc"),
              ("Marqueur de touchdown", "td"), ("2+ touchdowns qui seront marqués par le joueur", "td2"),
              ("Premier marqueur de touchdown", "ftd")]
PMU_PLAYER_N = [(re.compile(r"^Le joueur marque au moins (\d) point"), "hp"),
                (re.compile(r"^Le joueur enregistre au moins (\d) passe"), "ha")]
PMU_YARDS = re.compile(r"^Nombre total de (receiving|passing|rushing) yards par le joueur$")
YARDS = {"receiving": "ydrec", "passing": "ydpass", "rushing": "ydrush", "réception": "ydrec", "passe": "ydpass", "course": "ydrush"}
PLAYER_QUAL = {"", "Prolongations incluses", "Prolongations Incluses", "Temps réglementaire"}


def pmu_market(g, b):
    sp = g.sp
    label = b["criterion"]["label"].replace("but(s)", "buts")
    base, _, qual = label.partition(" - ")
    every = b.get("outcomes", [])
    outs = [o for o in every if o.get("status") == "OPEN" and o.get("odds")]
    odds = lambda o: o["odds"] / 1000
    line = lambda o: key_line(o["line"] / 1000)
    side = lambda o: "over" if o["type"] == "OT_OVER" else "under"
    if qual in PLAYER_QUAL:  # joueurs (le foot écrit « Marque », le hockey « Marque - Prolongations incluses »)
        kind = next((k for prefix, k in PMU_PLAYER if base == prefix or base.startswith(prefix + " (")), None)
        for rx, k in PMU_PLAYER_N:
            m = not kind and rx.match(base)
            kind = k + m.group(1) if m else kind
        if kind:
            for o in outs:
                if o.get("type") in ("OT_YES", "OT_PLAYER_PARTICIPANT") and o.get("participant"):
                    g.player(kind, o["participant"], odds(o))
            return
        m = PMU_YARDS.match(base)
        if m:
            for o in outs:
                if o.get("type") in ("OT_OVER", "OT_UNDER") and o.get("participant") and o.get("line") is not None:
                    g.player_line(YARDS[m.group(1)], o["participant"], line(o), side(o), odds(o))
            return
    tag = "" if qual in sp.get("full", {""}) else "~ot" if qual in sp.get("ot", ()) else None
    if tag is None:  # mi-temps, période, set, quart-temps…
        return
    if base in PMU_WIN:
        three = base == "Temps réglementaire" or any(o.get("type") == "OT_CROSS" for o in every)
        for o in outs:
            if o.get("type") in KAMBI and (three and not tag):
                g.add("res|" + KAMBI[o["type"]], odds(o))
            elif o.get("type") in ("OT_ONE", "OT_TWO") and not three:
                g.add("win|" + KAMBI[o["type"]], odds(o))
    elif base == "Pari remboursé en cas d'égalité":
        for o in outs:
            if o.get("type") in ("OT_ONE", "OT_TWO"):
                g.add("dnb%s|%s" % (tag, KAMBI[o["type"]]), odds(o))
    elif PMU_TOT.match(base):
        k = kind_of(sp, "tot", PMU_TOT.match(base).group(1), tag)
        for o in outs:
            if o.get("type") in ("OT_OVER", "OT_UNDER") and o.get("line") is not None:
                g.add("%s|%s|%s" % (k, line(o), side(o)), odds(o))
    elif PMU_TT.match(base):
        m = PMU_TT.match(base)
        k, s = kind_of(sp, "tt", m.group(1), tag), g.side(m.group(2))
        for o in outs:
            if s and o.get("type") in ("OT_OVER", "OT_UNDER") and o.get("line") is not None:
                g.add("%s|%s|%s|%s" % (k, s, line(o), side(o)), odds(o))
    elif base in PMU_HCP and not (base == "Handicap" and sp.get("strict")):
        k = kind_of(sp, "hcp", PMU_HCP[base], tag)
        for o in outs:
            if o.get("type") in ("OT_ONE", "OT_TWO") and o.get("line") is not None:
                g.add("%s|%s|%s" % (k, KAMBI[o["type"]], line(o)), odds(o))
    elif base == "3-Way Handicap":
        for o in outs:
            if o.get("type") in KAMBI and o.get("line") is not None:
                g.add("eh%s|%s|%s" % (tag, line(o), KAMBI[o["type"]]), odds(o))
    elif base == "Les deux équipes marquent":
        for o in outs:
            if o.get("type") in ("OT_YES", "OT_NO"):
                g.add("btts%s|%s" % (tag, "yes" if o["type"] == "OT_YES" else "no"), odds(o))


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
        g = Game("pmu", start, ev["homeName"], ev["awayName"], league, "https://www.pmu.fr/sport/event/%s" % ev["id"], ev["id"], sp)
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
W_RES = {"Résultat", "Résultat (hors prolongations)"}
W_DNB = {"Vainqueur (remboursé si match nul)", "Vainqueur - Remboursé si match nul"}
W_TOT = re.compile(r"^Nombre de (buts|points|jeux|manches|sets)$")
W_TT = re.compile(r"^Nombre de (buts|points|jeux|manches|sets) de (.+)$")
W_HCP = re.compile(r"^Écart de (buts|points|jeux|manches|sets)(?: \(handicap\))?$")
W_PLAYER = {"Buteur": "sc", "Marque 2 buts ou plus": "sc2", "Premier buteur": "fs", "1 passes décisives ou plus": "as",
            "Joueur décisif": "dec", "Marqueur de touchdown": "td", "Marque 2 touchdowns ou plus": "td2",
            "Premier marqueur de touchdown": "ftd"}
W_PLAYER_N = [(re.compile(r"^Points du joueur : (\d) ou plus$"), "hp"), (re.compile(r"^Passes décisives du joueur : (\d) ou plus$"), "ha")]
W_YARDS = re.compile(r"^Nombre de yards à la (réception|passe|course) - (.+)$")


def winamax_market(g, title, outs):
    sp = g.sp
    if title in W_RES:
        for o, x in outs:
            s = who(g, o.get("label"))
            if s:
                g.add("res|" + s, x)
    elif title == "Vainqueur":
        for o, x in outs:
            s = g.side(o.get("label"))
            if s:
                g.add("win|" + s, x)
    elif title in W_DNB:
        for o, x in outs:
            s = g.side(o.get("label"))
            if s:
                g.add("dnb|" + s, x)
    elif W_TOT.match(title):
        k = kind_of(sp, "tot", W_TOT.match(title).group(1))
        for o, x in outs:
            side, line = ou(o.get("label"))
            if side:
                g.add("%s|%s|%s" % (k, line, side), x)
    elif W_TT.match(title):
        m = W_TT.match(title)
        k, s = kind_of(sp, "tt", m.group(1)), g.side(m.group(2))
        for o, x in outs:
            side, line = ou(o.get("label"))
            if side and s:
                g.add("%s|%s|%s|%s" % (k, s, line, side), x)
    elif W_HCP.match(title):
        k = kind_of(sp, "hcp", W_HCP.match(title).group(1))
        rows = []
        for o, x in outs:
            m = re.match(r"^(.*\S)\s+([+-][\d.,]+)$", o.get("label") or "")
            rows.append((m and g.side(m.group(1)), m and key_line(m.group(2)), plain(o.get("label")) in DRAW, x))
        if not any(r[2] for r in rows):  # deux issues : handicap « asiatique »
            for s, line, _, x in rows:
                if s:
                    g.add("%s|%s|%s" % (k, s, line), x)
    elif title == "Les 2 équipes marquent":
        for o, x in outs:
            lab = plain(o.get("label"))
            if lab in ("oui", "non"):
                g.add("btts|" + ("yes" if lab == "oui" else "no"), x)
    elif title in W_PLAYER:
        for o, x in outs:
            g.player(W_PLAYER[title], o.get("label"), x)
    elif W_YARDS.match(title):
        m = W_YARDS.match(title)
        for o, x in outs:
            side, line = ou(o.get("label"))
            g.player_line(YARDS[m.group(1)], m.group(2), line, side, x)
    else:
        for rx, k in W_PLAYER_N:
            m = rx.match(title)
            if m:
                for o, x in outs:
                    g.player(k + m.group(1), o.get("label"), x)


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
        if mt.get("sportId") != sp["winamax"] or mt.get("status") != "PREMATCH" or not mt.get("competitor2Name"):
            continue
        league = (tours.get(str(mt.get("tournamentId"))) or {}).get("tournamentName", "")
        start = datetime.fromtimestamp(mt["matchStart"], timezone.utc)
        if not now() < start < now() + timedelta(hours=DETAIL_H["winamax"]) or EXCLUDE.search(league):
            continue
        g = Game("winamax", start, mt["competitor1Name"], mt["competitor2Name"], league,
                 "%s/match/%s" % (winamax.BASE, mt["matchId"]), mt["matchId"], sp)
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
B_RES = {"Résultat du match (t. rég)", "Résultat du match (tps rég.)", "Résultat"}
B_TOT = re.compile(r"^Nombre total de (buts|points|jeux|manches|sets)( \(t\. rég\)| \(prolongations et penaltys inclus\))?$")
B_TT = re.compile(r"^(?:Total|Nombre total|Nombre) de (buts|points|jeux|manches|sets)(?: \(t\. rég\))? - (.+)$")
B_TT_OT = re.compile(r"^(.+) - Nombre total de (buts|points) \(prolong\. & pen\. inclus\)$")
B_HCP = re.compile(r"^Écart de (buts|points|jeux|manches|sets)(?: \(t\. rég\))?$")
B_PLAYER = {"Buteur (t. rég)": "sc", "Joueur décisif (buteur ou passeur décisif dans le t. rég)": "dec"}
# « Écart de buts » : marges de victoire, qui valent des handicaps (score entier)
MARGIN = [(re.compile(r"^(.+?) gagne de (\d+)(?: [a-z]+)? ou \+$"), lambda n: 0.5 - n),                   # -(n - 0,5)
          (re.compile(r"^(.+?) ne perd pas ou perd de (\d+)(?: [a-z]+)? (?:ou -|exactement)$"), lambda n: n + 0.5),
          (re.compile(r"^(.+?) ne perd pas$"), None)]                                                    # +0,5


def margin(g, label):
    """« Lens gagne de 2 buts ou + » → (« home », -1.5) ; « Lyon ne perd pas ou perd de 1 but ou - » → (« away », 1.5)."""
    for rx, f in MARGIN:
        m = rx.match(label or "")
        if m:
            s = g.side(m.group(1))
            return (s, 0.5 if f is None else f(int(m.group(2)))) if s else None
    return None


def betclic_market(g, name, sels):
    sp = g.sp
    if name in B_RES:
        for lab, x in sels:
            s = who(g, lab)
            if s:
                g.add("res|" + s, x)
    elif name == "Vainqueur du match":
        for lab, x in sels:
            s = g.side(lab)
            if s:
                g.add("win|" + s, x)
    elif B_TOT.match(name):
        m = B_TOT.match(name)
        k = kind_of(sp, "tot", m.group(1), "~ot" if "prolong" in (m.group(2) or "") and sp.get("ot") else "")
        for lab, x in sels:
            side, line = ou(lab)
            if side:
                g.add("%s|%s|%s" % (k, line, side), x)
    elif B_TT.match(name) or B_TT_OT.match(name):
        m = B_TT.match(name)
        unit, team, tag = (m.group(1), m.group(2), "") if m else (B_TT_OT.match(name).group(2), B_TT_OT.match(name).group(1),
                                                                    "~ot" if sp.get("ot") else "")
        k, s = kind_of(sp, "tt", unit, tag), g.side(team)
        for lab, x in sels:
            side, line = ou(lab)
            if side and s:
                g.add("%s|%s|%s|%s" % (k, s, line, side), x)
    elif B_HCP.match(name):
        k = kind_of(sp, "hcp", B_HCP.match(name).group(1))
        for lab, x in sels:
            hit = margin(g, lab)
            if hit:
                g.add("%s|%s|%s" % (k, hit[0], key_line(hit[1])), x)
    elif name == "Les 2 équipes marquent (t. rég)":
        for lab, x in sels:
            if plain(lab) in ("oui", "non"):
                g.add("btts|" + ("yes" if plain(lab) == "oui" else "no"), x)
    elif name in B_PLAYER:
        for lab, x in sels:
            g.player(B_PLAYER[name], lab, x)


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
        g = Game("betclic", start, home, away, league, BETCLIC + links[mid], mid, sp)
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


# ------------------------------------------------------------------ Unibet (foot ; page de chaque match : script #serverApp-state)
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
            out.append(Game("unibet", None, home.replace("-", " "), away.replace("-", " "), c.split("/")[-1], UNIBET + path, eid, sp))
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
DETAILS = {"pmu": detail_pmu, "winamax": detail_winamax, "betclic": detail_betclic, "unibet": detail_unibet}


def get_list(b, sp, cache):
    """Liste des matchs d'un bookmaker ; celles de Betclic et d'Unibet (une page par championnat) sont gardées une heure.
    Une liste gardée ne garde pas ses cotes : seules comptent alors celles des pages de match, datées."""
    kept = cache.setdefault("lists", {}).get(b)
    if b in LIST_EVERY and kept and now() - utc(kept["at"]) < timedelta(minutes=LIST_EVERY[b]):
        games = [Game(b, utc(t) if t else None, h, a, lg, u, e, sp) for h, a, t, lg, u, e in kept["games"]]
        return [g for g in games if not g.start or g.start > now()]
    games = LISTS[b](sp)
    if b in LIST_EVERY:
        cache["lists"][b] = {"at": iso(now()), "games": [[g.home, g.away, iso(g.start) if g.start else None, g.league, g.url, g.eid]
                                                         for g in games]}
    return games


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
    d = Game(b, g.start, g.home, g.away, g.league, g.url, g.eid, g.sp)
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


def plan(groups, caps):
    """Pages de match à lire à ce passage : matchs vus chez 2 bookmakers ou plus, les plus suivis puis les plus proches."""
    todo = {b: [] for b in caps}
    for ref, members in sorted(groups, key=lambda gm: (-len(gm[1]), gm[0].start)):
        if len(members) < 2:
            continue
        for b, (g, _) in members.items():
            start = g.start or ref.start
            if b not in caps or start > now() + timedelta(hours=DETAIL_H[b]):
                continue
            if (not g.detail_at or now() - g.detail_at > ttl(start)) and len(todo[b]) < caps[b]:
                todo[b].append(g)
    return todo


# ------------------------------------------------------------------ comparaison
SWAP = {"home": "away", "away": "home"}
PLAYER_KINDS = {"sc": "Buteur", "sc2": "Doublé (2 buts ou +)", "fs": "Premier buteur", "as": "Passeur décisif",
                "dec": "Buteur ou passeur", "td": "Marqueur de touchdown", "td2": "2 touchdowns ou +",
                "ftd": "Premier touchdown", "ydrec": "Yards à la réception", "ydpass": "Yards à la passe",
                "ydrush": "Yards à la course"}
PLAYER_KINDS.update({"hp%d" % n: "%d point%s ou + (buts et passes)" % (n, "s" if n > 1 else "") for n in range(1, 6)})
PLAYER_KINDS.update({"ha%d" % n: "%d passe%s décisive%s ou +" % (n, "s" if n > 1 else "", "s" if n > 1 else "") for n in range(1, 6)})
PLAYER_LINES = {"ydrec", "ydpass", "ydrush"}  # joueurs avec une ligne (Plus / Moins de 74,5 yards)


def swap(key):
    k = [SWAP.get(t, t) for t in key.split("|")]
    if kind_parts(k[0])[0] == "eh":
        k[1] = key_line(0.0 - num(k[1]))
    return "|".join(k)


def player_of(key):
    k = key.split("|")
    return k[1] if k[0] in PLAYER_KINDS and len(k) > 1 else None


def views(members):
    """{book: (cotes vues du côté de la référence, Game, inversé)} ; un joueur ambigu chez un bookmaker est écarté."""
    out = {}
    for b, (g, inv) in members.items():
        names = g.names()
        bad = {k for k, v in names.items() if len({pkey(n) and plain(n).replace(".", "") for n in v}) > 1
               and len({plain(n).split()[0][:1] for n in v if plain(n).split()}) > 1}
        sel = {swap(k) if inv else k: o for k, o in g.view().items() if player_of(k) not in bad}
        out[b] = (sel, g, inv)
    return out


def unit_of(sp, kind):
    return kind_parts(kind)[1] or sp["unit"]


def describe(sp, key, home, away):
    k = key.split("|")
    base, unit, ot = kind_parts(k[0])
    u = unit or sp["unit"]
    extra = " (prolongations comprises)" if ot else ""
    team = lambda s: {"home": home, "away": away, "draw": "match nul"}.get(s, s)
    side = lambda s: "Plus" if s == "over" else "Moins"
    ln = lambda x: ("+" if num(x) > 0 else "") + x.replace(".", ",")
    if k[0] in PLAYER_LINES:
        return "%s : %s %s de %s" % (PLAYER_KINDS[k[0]], k[1].title(), side(k[3]), k[2].replace(".", ","))
    if k[0] in PLAYER_KINDS:
        return "%s : %s" % (PLAYER_KINDS[k[0]], k[1].title())
    if base == "res":
        return "Résultat : %s" % team(k[1])
    if base == "win":
        return "Vainqueur : %s" % team(k[1])
    if base == "dnb":
        return "Vainqueur (remboursé si nul)%s : %s" % (extra, team(k[1]))
    if base == "tot":
        return "Total de %s%s : %s de %s" % (u, extra, side(k[2]), k[1].replace(".", ","))
    if base == "tt":
        return "%s de %s%s : %s de %s" % (u.capitalize(), team(k[1]), extra, side(k[3]), k[2].replace(".", ","))
    if base == "hcp":
        return "Handicap%s%s : %s %s" % (" (%s)" % u if unit else "", extra, team(k[1]), ln(k[2]))
    if base == "eh":
        return "Handicap 3 issues%s (%s %s) : %s" % (extra, home, ln(k[1]), team(k[2]))
    if base == "btts":
        return "Les 2 équipes marquent%s : %s" % (extra, "oui" if k[1] == "yes" else "non")
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
    edge, top = (PLAYER_EDGE, PLAYER_MAX) if kind in PLAYER_KINDS else (alerts.HUGE, alerts.HUGE_MAX) if leg(key) else (EDGE, MAX_ODDS)
    b1, b2 = sorted((bb, sb))
    norm = base.get((kind, b1, b2), 1.0)
    norm = norm if bb == b1 else 1 / norm
    if best <= top and best >= second * edge and best >= second * norm * REL_EDGE:
        return bb, best, sb
    return None


def leg(key):
    """Handicaps à 2 issues (hcp…) et à 3 issues (eh…), pour alerts.surebets ; le marché garde l'unité (« hcp~sets »)."""
    k = key.split("|")
    base = kind_parts(k[0])[0]
    if base == "hcp":
        return (k[0], k[1], num(k[2]), 2)
    return (k[0], k[2], num(k[1]), 3) if base == "eh" else None


def line_checks(sp, ref, v, kinds):
    """Totaux à comparer : (préfixe des clés, écart minimal, intitulé, catégorie)."""
    out = []
    for kind in sorted(kinds):
        base, unit, ot = kind_parts(kind)
        need = sp["gap"].get(kind.replace("~ot", ""))
        u = unit or sp["unit"]
        extra = " (prolongations comprises)" if ot else ""
        if not need:
            continue
        if base == "tot":
            out.append((kind + "|", need, "Total de %s%s" % (u, extra), "Écart de ligne"))
        elif base == "tt":
            out += [("%s|%s|" % (kind, s), need, "%s de %s%s" % (u.capitalize(), t, extra), "Écart de ligne")
                    for s, t in (("home", ref.home), ("away", ref.away))]
        elif kind in PLAYER_LINES:
            players = {player_of(k) for sel, g, inv in v.values() for k in sel if k.startswith(kind + "|")}
            out += [("%s|%s|" % (kind, p), need, player_name(v, p), PLAYER_KINDS[kind]) for p in sorted(players)]
    return out


def compare(groups, sp):
    """→ (cotes anormales, écarts de ligne sur les totaux, surebets sur les handicaps, pages à relire pour confirmer)."""
    found, lines, sures, stale = [], [], [], set()
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
        kinds = {k.split("|")[0] for k in odds}
        # écarts de ligne : totaux, totaux d'une équipe, joueurs (yards)
        for prefix, need, label, cat in line_checks(sp, ref, v, kinds):
            bal = {b: x for b, x in ((b, balanced(sel, prefix)) for b, (sel, g, inv) in v.items()) if x}
            if len(bal) < 2:
                continue
            lo_b, hi_b = min(bal, key=lambda b: bal[b][0]), max(bal, key=lambda b: bal[b][0])
            if bal[hi_b][0] - bal[lo_b][0] >= need:
                if detail_fresh(v[lo_b][1]) and detail_fresh(v[hi_b][1]):
                    lines.append(dict(ref=ref, v=v, prefix=prefix, label=label, cat=cat, bal=bal, lo_b=lo_b, hi_b=hi_b))
                else:
                    stale.update((b, v[b][1]) for b in (lo_b, hi_b) if not detail_fresh(v[b][1]))
        # handicaps : surebets (cotes fraîches chez les deux ou trois sites), qui remplacent une énorme erreur sur ce marché
        for market, (gain, legs) in alerts.surebets(odds, leg).items():
            old = [b for k, b, o in legs if not v[b][1].fresh(swap(k) if v[b][2] else k)]
            if old:
                stale.update((b, v[b][1]) for b in old)
            else:
                sures.append(dict(ref=ref, v=v, market=market, gain=gain, legs=legs))
                found = [f for f in found if f[0] is not ref or f[2].split("|")[0] != market]
    return found, lines, sures, stale


# ------------------------------------------------------------------ page « Écarts » et alertes
def hcp_name(sp, kind):
    base, unit, ot = kind_parts(kind)
    return ("Handicap 3 issues" if base == "eh" else "Handicap") + (" (%s)" % unit if unit else "") + (" (prolongations comprises)" if ot else "")


def leg_text(sp, ref):
    """Un pari du surebet : « Lyon -1,5 » ; à 3 issues « Lyon -1 », « Nul (Lyon -1) », « Lens +1 »."""
    def text(key):
        k = key.split("|")
        return alerts.leg3(k[2], num(k[1]), ref) if kind_parts(k[0])[0] == "eh" else describe(sp, key, ref.home, ref.away).partition(" : ")[2]
    return text


def page_gaps(sp_id, sp, found, lines, sures):
    out = []
    for ref, v, key, bb, best, by in found:
        k = key.split("|")
        if k[0] in PLAYER_KINDS:
            name, cat = player_name(v, k[1]), PLAYER_KINDS[k[0]]
            sel = "%s de %s" % ("Plus" if k[3] == "over" else "Moins", k[2]) if k[0] in PLAYER_LINES else ""
        else:
            (name, _, cat), sel = describe(sp, key, ref.home, ref.away).partition(" : "), ""
        out.append({"id": "%s|%s|%s|%s" % (ref.home, ref.away, ref.start.date(), key), "sport": sp_id, "kind": "odds",
                    "name": name, "stat": k[0], "cat": cat, "sel": sel, "start": iso(ref.start), "home": ref.home,
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
                    "name": x["label"], "stat": prefix.split("|")[0], "cat": x["cat"], "start": iso(ref.start),
                    "home": ref.home, "away": ref.away, "league": ref.league, "gap": bal[x["hi_b"]][0] - bal[x["lo_b"]][0],
                    "lo": x["lo_b"], "hi": x["hi_b"], "books": books})
    for x in sures:
        ref, v = x["ref"], x["v"]
        e = {"id": "%s|%s|%s|sure|%s" % (ref.home, ref.away, ref.start.date(), x["market"]), "sport": sp_id,
             "name": hcp_name(sp, x["market"]), "start": iso(ref.start), "home": ref.home, "away": ref.away, "league": ref.league}
        e.update(alerts.sure_entry(x["gain"], x["legs"], leg_text(sp, ref), lambda b: v[b][1].url))
        out.append(e)
    return out


def alert_keys(v, ref, key):
    """Une clé par façon d'écrire le match chez chaque bookmaker : pas de rappel quand la référence change de nom."""
    return sorted({alerts.subject_eu("%s|%s|%s|%s" % (((g.away, g.home) if inv else (g.home, g.away)) + (ref.start.date(), key)))
                   for sel, g, inv in v.values()})


def messages(sp, found, lines, sures):
    ln = lambda x: ("%g" % x).replace(".", ",")
    todo = []
    for ref, v, key, bb, best, by in found:
        rest = " · ".join("%s %s" % (BOOK_NAMES[b], alerts.odds_fr(o)) for b, o in sorted(by.items(), key=lambda kv: -kv[1]) if b != bb)
        k = key.split("|")
        what = describe(sp, key, ref.home, ref.away)
        if k[0] in PLAYER_KINDS:
            what = what.replace(k[1].title(), player_name(v, k[1]), 1)
        todo.append((ref.start, "%s %s · %s – %s" % (sp["emoji"], BOOK_NAMES[bb], ref.home, ref.away),
                     "%s\n%s : %s\nAutres bookmakers : %s\n%s · %s" % (what, BOOK_NAMES[bb], alerts.odds_fr(best), rest,
                                                                       v[bb][1].league or ref.league, when(ref.start)),
                     alert_keys(v, ref, key), best))
    for x in lines:
        ref, bal, lo_b, hi_b = x["ref"], x["bal"], x["lo_b"], x["hi_b"]
        label = x["label"] if x["cat"] == "Écart de ligne" else "%s · %s" % (x["label"], x["cat"].lower())
        todo.append((ref.start, "📏 %s · écart de ligne %s · %s – %s" % (label, ln(bal[hi_b][0] - bal[lo_b][0]), ref.home, ref.away),
                     "Plus de %s chez %s : %s\nMoins de %s chez %s : %s\nToutes les lignes : %s\n%s · %s" % (
                         ln(bal[lo_b][0]), BOOK_NAMES[lo_b], alerts.odds_fr(bal[lo_b][1]), ln(bal[hi_b][0]), BOOK_NAMES[hi_b],
                         alerts.odds_fr(bal[hi_b][2]), " · ".join("%s %s" % (BOOK_NAMES[b], ln(l[0])) for b, l in sorted(bal.items(), key=lambda kv: kv[1][0])),
                         ref.league, when(ref.start)),
                     alert_keys(x["v"], ref, "gap|" + x["prefix"].rstrip("|")), bal[hi_b][0] - bal[lo_b][0]))
    for x in sures:
        ref, gain = x["ref"], x["gain"]
        rows = alerts.sure_lines(x["legs"], leg_text(sp, ref), BOOK_NAMES) + ["%s · %s" % (ref.league, when(ref.start))]
        todo.append((ref.start, "💰 Surebet +%s %% · %s · %s – %s" % (("%.1f" % (gain * 100)).replace(".", ","), hcp_name(sp, x["market"]),
                                                                      ref.home, ref.away),
                     "\n".join(rows), alert_keys(x["v"], ref, "sure|" + x["market"]), gain))
    return todo


# ------------------------------------------------------------------ passage
def run_sport(sp_id, sp, cache, left, diag):
    """Un sport : listes, pages de match (dans la limite de « left », pages restantes par bookmaker), comparaison."""
    books = [b for b in LISTS if sp.get(b)]
    per_book = {}
    with ThreadPoolExecutor(max_workers=len(books)) as pool:
        futs = {b: pool.submit(get_list, b, sp, cache) for b in books}
        for b, fut in futs.items():
            try:
                per_book[b] = fut.result()
            except Exception as e:  # noqa: BLE001 — un bookmaker en panne ou qui refuse : on continue sans lui
                log("%s %s : liste illisible (%s)" % (sp["name"], b, e if isinstance(e, Blocked) else type(e).__name__))
                if not isinstance(e, (Blocked, urllib.error.HTTPError)):
                    traceback.print_exc()
    load_cache(per_book, cache)
    groups = group_games(per_book)
    caps = {b: max(0, min(sp.get("cap", CAP).get(b, 0), left.get(b, 0))) for b in books}
    read = fetch_details(plan(groups, caps))
    groups = [(ref, {b: m for b, m in members.items() if b != "unibet" or not m[0].start or not ref.start
                     or abs((m[0].start - ref.start).total_seconds()) <= 2.5 * 3600}) for ref, members in groups]
    found, lines, sures, stale = compare(groups, sp)
    if stale:  # écart vu sur une cote ancienne : on relit ces pages tout de suite avant d'alerter
        again = {b: [g for b2, g in stale if b2 == b][:CONFIRM] for b in books}
        more = fetch_details(again)
        read = {b: read.get(b, 0) + more.get(b, 0) for b in books}
        found, lines, sures, _ = compare(groups, sp)
    for b in books:
        left[b] = left.get(b, 0) - read.get(b, 0)
    for b, games in per_book.items():  # cache des pages de match (matchs pas encore commencés)
        cache[b] = {g.eid: {"at": iso(g.detail_at), "sel": g.detail, "full": {k: sorted(v) for k, v in g.detail_full.items()},
                            "start": iso(g.start) if g.start else None}
                    for g in games if g.detail_at and (g.start or now()) > now()}
    log("%s : listes %s ; %d matchs chez 2+ bookmakers ; pages lues %s ; %d cote(s) anormale(s), %d écart(s) de ligne, %d surebet(s)" % (
        sp["name"], " ".join("%s %d" % (b, len(per_book.get(b, []))) for b in books),
        sum(1 for _, m in groups if len(m) >= 2), " ".join("%s %d" % (b, n) for b, n in read.items()), len(found), len(lines), len(sures)))
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
        for r, key, ref, by in sorted(ratios, key=lambda t: -t[0])[:12]:
            print("  %.2f  %s – %s  %s  %s" % (r, ref.home, ref.away, key, by))
    return page_gaps(sp_id, sp, found, lines, sures), messages(sp, found, lines, sures)


def main():
    dry, diag = "--dry" in sys.argv, "--diag" in sys.argv
    only = sys.argv[sys.argv.index("--only") + 1].split(",") if "--only" in sys.argv else list(SPORTS)
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    old = json.loads(PAGE.read_text()) if PAGE.exists() else {}
    sent = state.setdefault("sent", {})
    for k in [k for k, v in sent.items() if datetime.fromisoformat(v["t"]) < now() - timedelta(days=3)]:
        del sent[k]
    recent = alerts.recent_subjects(sent, alerts.subject_eu)
    left = dict(TOTAL)
    gaps, todo = [x for x in old.get("gaps", []) if x.get("sport") not in only], []
    for sp_id in only:
        sp = SPORTS[sp_id]
        try:
            entries, msgs = run_sport(sp_id, sp, cache.setdefault(sp_id, {}), left, diag)
        except Exception as e:  # noqa: BLE001
            log(sp["name"], ": erreur", type(e).__name__)
            traceback.print_exc()
            continue
        gaps += entries
        todo += [(sp, t) for t in msgs if not recent.intersection(t[3])]
    todo.sort(key=lambda st: st[1][0])  # les matchs les plus proches d'abord
    PAGE.parent.mkdir(exist_ok=True)
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, separators=(",", ":")))  # pages déjà lues, même à blanc
    if dry:
        for sp, t in todo:
            print("---\n" + t[1] + "\n" + t[2])
        log("%d alerte(s) (essai à blanc, rien envoyé) : %s" % (len(todo), dict(collections.Counter(sp["name"] for sp, t in todo))))
        return
    for sp, (start, title, msg, ks, v) in todo[:MAX_PUSH]:
        alerts.push(title, msg, sp["tag"])
        sent.update({k: {"v": v, "t": now().isoformat()} for k in ks})
    if len(todo) > MAX_PUSH:
        alerts.push("%d autres écarts" % (len(todo) - MAX_PUSH),
                    "\n".join("• " + t[1] + " — " + t[2].split("\n")[0] for sp, t in todo[MAX_PUSH:MAX_PUSH + 20]), "bar_chart")
        for sp, t in todo[MAX_PUSH:]:
            sent.update({k: {"v": t[4], "t": now().isoformat()} for k in t[3]})
    PAGE.write_text(json.dumps({"at": iso(now()), "sports": list(SPORTS), "gaps": gaps}, ensure_ascii=False))
    STATE.write_text(json.dumps(state, ensure_ascii=False))
    log("%d alerte(s) envoyée(s)" % len(todo))


if __name__ == "__main__":
    main()
