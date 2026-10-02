#!/usr/bin/env python3
"""
Collecte des cotes Unibet / Winamax / PMU / Betclic / bwin / PokerStars
et écriture de docs/odds.json (lu par le site).

Sources :
  - The Odds API  (clé : ODDS_API_KEY)   → 1N2 / vainqueur, région FR
  - OddsPapi      (clé : ODDSPAPI_KEY)   → bwin, PokerStars, PMU, Unibet, Winamax,
                                           marchés joueurs, cyclisme

Le script dose sa consommation : il répartit le quota restant sur le reste
du mois et ne lance une source que si elle a « gagné » assez de requêtes
depuis son dernier passage. Avec un abonnement payant (quota plus grand),
la fréquence augmente donc automatiquement.

Usage :  python3 scripts/fetch_odds.py [--force]
Aucune dépendance externe (stdlib uniquement, Python ≥ 3.9).
"""
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "odds.json"
STATE = ROOT / "data" / "state.json"
COVERAGE_DIR = ROOT / "data" / "coverage"

FORCE = "--force" in sys.argv or os.environ.get("FORCE") == "true"
NOW = datetime.now(timezone.utc)
HORIZON = NOW + timedelta(days=int(os.environ.get("HORIZON_DAYS", "7")))

# Nos 6 bookmakers et les motifs qui reconnaissent leurs identifiants chez les fournisseurs
BOOKS = ["unibet", "winamax", "pmu", "betclic", "bwin", "pokerstars"]
BOOK_PATTERNS = {
    "unibet": r"unibet",
    "winamax": r"winamax",
    "pmu": r"\bpmu",
    "betclic": r"betclic",
    "bwin": r"bwin",
    "pokerstars": r"pokerstars",
}


def log(*a):
    print(*a, flush=True)


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_dt(s):
    if not s:
        return None
    if isinstance(s, (int, float)):
        return datetime.fromtimestamp(s / 1000 if s > 1e11 else s, timezone.utc)
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def load_json(path, default):
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return default


def get_json(url, params):
    """GET JSON. Ne journalise jamais l'URL (elle contient la clé)."""
    req = urllib.request.Request(
        url + "?" + urllib.parse.urlencode(params),
        headers={"User-Agent": "CoteRadar/1.0", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r), {k.lower(): v for k, v in r.headers.items()}


def hours_left_in_month():
    first_next = (NOW.replace(day=1) + timedelta(days=32)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return max(1.0, (first_next - NOW).total_seconds() / 3600)


def is_due(src_state, remaining, cost):
    """Vrai si la source a accumulé assez de quota depuis son dernier passage."""
    if FORCE:
        return True
    if remaining is not None and remaining < cost:
        return False
    last = parse_dt(src_state.get("lastRun"))
    if last is None or remaining is None:
        return True
    per_hour = remaining / hours_left_in_month()
    elapsed = (NOW - last).total_seconds() / 3600
    return elapsed * per_hour >= cost


# ---------------------------------------------------------------- noms & matching
STOP = {"fc", "sc", "ac", "as", "cf", "afc", "ssc", "sv", "rc", "us", "og", "stade", "club", "de", "la", "le", "the", "olympique"}


ALIASES = {  # abréviations courantes → nom complet
    "psg": "paris saint germain", "paris fc": "paris football club", "paris": "paris football club", "om": "marseille", "ol": "lyon", "losc": "lille", "ogc nice": "nice",
    "inter": "internazionale", "man utd": "manchester united", "man united": "manchester united",
    "man city": "manchester city", "spurs": "tottenham", "atletico": "atletico madrid", "barca": "barcelona",
    "bayern": "bayern munich", "bayern munchen": "bayern munich", "st toulousain": "toulouse",
    "ubb": "bordeaux begles", "sf": "stade francais", "okc": "oklahoma city thunder",
}


def norm_tokens(name):
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s).strip()
    s = ALIASES.get(s, s)
    toks = re.findall(r"[a-z0-9]+", s)
    return {t for t in toks if t not in STOP and len(t) > 1} or set(toks)


def slug(name):
    return "-".join(sorted(norm_tokens(name)))[:40] or "x"


WEAK = {"paris", "madrid", "manchester", "city", "united", "real", "saint", "st", "sporting", "inter", "athletic",
        "atletico", "racing", "royal", "los", "angeles", "new", "york", "san", "golden", "state", "wanderers", "rovers", "town"}


def same_team(a, b):
    """Au moins un mot distinctif en commun (« Paris » ou « Madrid » seuls ne suffisent pas)."""
    common = norm_tokens(a) & norm_tokens(b)
    return bool(common - WEAK) or (common and common == norm_tokens(a)) or (common and common == norm_tokens(b))


def map_book(provider_key):
    k = provider_key.lower()
    for book, pat in BOOK_PATTERNS.items():
        if re.search(pat, k):
            return book
    return None


# ---------------------------------------------------------------- modèle interne
class Store:
    """Événements indexés ; conserve les cotes d'un passage à l'autre."""

    def __init__(self, previous):
        self.events = {}
        for ev in previous.get("events", []):
            d = parse_dt(ev.get("date"))
            if d and d > NOW - timedelta(hours=3):
                ev["markets"] = {m["key"]: {**m, "selections": {s["key"]: s for s in m["selections"]}} for m in ev["markets"]}
                self.events[ev["id"]] = ev

    def find_or_create(self, sport, competition, date, home, away):
        for ev in self.events.values():
            d = parse_dt(ev["date"])
            gap = abs((d - date).total_seconds())
            if ev["sport"] != sport or gap > 3 * 3600:
                continue
            h, a = same_team(ev["home"], home), same_team(ev["away"], away)
            if (h and a) or (same_team(ev["home"], away) and same_team(ev["away"], home)):
                return ev
            if gap <= 15 * 60 and (h or a):  # même coup d'envoi + une équipe identique
                return ev
        eid = "%s-%s-%s-%s" % (sport, date.strftime("%Y%m%d"), slug(home), slug(away))
        ev = {"id": eid, "sport": sport, "competition": competition, "date": iso(date),
              "home": home, "away": away, "markets": {}, "updated": {}}
        self.events[eid] = ev
        return ev

    @staticmethod
    def set_odds(ev, mkey, label, sel_key, sel_name, book, price, complete=False, team=None, line=None, order=None):
        try:
            price = round(float(price), 2)
        except (TypeError, ValueError):
            return
        if price <= 1.0:
            return
        m = ev["markets"].setdefault(mkey, {"key": mkey, "label": label, "complete": complete, "selections": {}})
        s = m["selections"].setdefault(sel_key, {"key": sel_key, "name": sel_name, "odds": {}, "prev": {}})
        if team:
            s["team"] = team
        if line is not None:
            s["line"] = line
        if order is not None:
            s["order"] = order
        old = s["odds"].get(book)
        if old is not None and old != price:
            s["prev"][book] = old
        s["odds"][book] = price
        ev["updated"][book] = iso(NOW)

    def export(self):
        out = []
        for ev in sorted(self.events.values(), key=lambda e: e["date"]):
            d = parse_dt(ev["date"])
            if d < NOW - timedelta(hours=3):
                continue
            markets = []
            for m in ev["markets"].values():
                sels = [s for s in m["selections"].values() if s["odds"]]
                if sels:
                    markets.append({**m, "selections": sels})
            if markets:
                out.append({**ev, "markets": markets})
        return out


# ---------------------------------------------------------------- The Odds API
TOA_BASE = "https://api.the-odds-api.com/v4"
TOA_SPORTS = [  # ordre = priorité quand le quota est serré
    ("football", r"^soccer_france_ligue_one$"),
    ("football", r"^soccer_uefa_champs_league$"),
    ("football", r"^soccer_epl$"),
    ("basket", r"^basketball_nba$"),
    ("football", r"^soccer_spain_la_liga$"),
    ("rugby", r"^rugbyunion_"),
    ("basket", r"^basketball_euroleague$"),
    ("football", r"^soccer_(italy_serie_a|germany_bundesliga|uefa_europa_league)$"),
]


def run_the_odds_api(store, state):
    key = os.environ.get("ODDS_API_KEY")
    st = state.setdefault("theoddsapi", {})
    if not key:
        st["status"] = "pas de clé"
        return

    try:  # /sports ne consomme pas de quota
        sports, _ = get_json(TOA_BASE + "/sports", {"apiKey": key})
    except urllib.error.HTTPError as e:
        st["status"] = "erreur %s" % e.code
        log("The Odds API : erreur", e.code)
        return

    targets = []
    for sport, pat in TOA_SPORTS:
        for s in sports:
            if s.get("active") and not s.get("has_outrights") and re.match(pat, s["key"]) and (sport, s) not in targets:
                targets.append((sport, s))

    remaining = st.get("remaining")
    cost = len(targets)
    if not targets:
        st["status"] = "aucune compétition active"
        return
    if not is_due(st, remaining, cost):
        log("The Odds API : pas encore dû (reste %s crédits)" % remaining)
        return

    n_events = 0
    st["status"] = "ok"
    for sport, s in targets:
        try:
            data, headers = get_json(TOA_BASE + "/sports/%s/odds" % s["key"], {
                "apiKey": key, "regions": "fr", "markets": "h2h", "oddsFormat": "decimal"})
        except urllib.error.HTTPError as e:
            log("The Odds API :", s["key"], "erreur", e.code)
            if e.code in (401, 429):
                st["status"] = "erreur %s (quota ou clé)" % e.code
                break
            continue
        if "x-requests-remaining" in headers:
            st["remaining"] = int(float(headers["x-requests-remaining"]))
        for g in data:
            date = parse_dt(g["commence_time"])
            if not date or date > HORIZON:
                continue
            ev = None
            for bk in g.get("bookmakers", []):
                book = map_book(bk["key"])
                if not book:
                    continue
                for mk in bk.get("markets", []):
                    if mk["key"] != "h2h":
                        continue
                    outs = mk["outcomes"]
                    has_draw = len(outs) == 3
                    mkey, label = ("1x2", "Résultat 1N2") if has_draw else ("ml", "Vainqueur")
                    ev = ev or store.find_or_create(sport, s["title"], date, g["home_team"], g["away_team"])
                    for o in outs:
                        if o["name"] == g["home_team"]:
                            k, name, order = "home", ev["home"], 0
                        elif o["name"] == g["away_team"]:
                            k, name, order = "away", ev["away"], 2
                        else:
                            k, name, order = "draw", "Match nul", 1
                        store.set_odds(ev, mkey, label, k, name, book, o["price"], complete=True, order=order)
            n_events += ev is not None
    st["lastRun"] = iso(NOW)
    log("The Odds API : %d événements, reste %s crédits" % (n_events, st.get("remaining")))


# ---------------------------------------------------------------- OddsPapi
# Doc : https://docs.oddspapi.io — limites : 10 req/s (cotes), 100 req/min (le reste)
OP_BASE = "https://api.oddspapi.io/v4"
OP_SPORT_IDS = {"football": 10, "basket": 11, "rugby": 26, "cyclisme": 68}

# Compétitions suivies : (sport, motif sur le nom, motif sur la catégorie/pays)
OP_TOURNAMENTS = [
    ("football", r"^ligue 1$", r"france"),
    ("football", r"^uefa champions league$", r""),
    ("football", r"^premier league$", r"england"),
    ("basket", r"^nba$", r"usa"),
    ("rugby", r"^top 14$", r"france"),
    ("football", r"^la ?liga$", r"spain"),
    ("basket", r"^euroleague$", r""),
    ("football", r"^serie a$", r"italy"),
    ("football", r"^bundesliga$", r"germany"),
    ("football", r"^uefa europa league$", r""),
    ("rugby", r"champions cup", r""),
    ("basket", r"betclic elite|^pro a$|^lnb", r"france"),
]

# Marchés joueurs : (sport, motif sur marketName, clé, libellé, type)
#   yes  → une seule issue « Yes » (ex. buteur)
#   over → issue « Over » sur une ligne x.5, convertie en palier (24.5 → 25+)
OP_PROPS = [
    ("football", r"^Anytime Goal Scorer$", "scorer", "Buteur", "yes"),
    ("football", r"^First Goal Scorer$", "first_scorer", "Premier buteur", "yes"),
    ("football", r"^Over Under Player Assists \(incl", "assist", "Passeur décisif", "over"),
    ("football", r"^Over Under Player Goals \+ Assists|^Player To Score Or Assist", "decisive", "Décisif (but ou passe)", "over"),
    ("football", r"^Over Under Player Shots On Goal \(incl", "sot", "Tirs cadrés", "over"),
    ("basket", r"^Over Under Player Points \(incl", "pts", "Points", "over"),
    ("basket", r"^Over Under Player Rebounds \(incl", "reb", "Rebonds", "over"),
    ("basket", r"^Over Under Player Assists \(incl", "ast", "Passes", "over"),
    ("basket", r"^Over Under Player 3 Point FG \(incl", "3pt", "Paniers à 3 pts", "over"),
    ("basket", r"^Over Under Player Points \+ Assists \+ Rebounds \(incl", "pra", "Pts + Reb + Pas", "over"),
    ("rugby", r"Anytime Try ?Scorer", "try_any", "Marqueur d'essai", "yes"),
    ("rugby", r"First Try ?Scorer", "try_first", "Premier marqueur d'essai", "yes"),
]


class Budget(Exception):
    pass


def player_name(raw):
    """« Jokic, Nikola » → « Nikola Jokic »."""
    if raw and "," in raw:
        last, _, firstn = raw.partition(",")
        return "%s %s" % (firstn.strip(), last.strip())
    return raw


def run_oddspapi(store, state):
    key = os.environ.get("ODDSPAPI_KEY")
    st = state.setdefault("oddspapi", {})
    if not key:
        st["status"] = "pas de clé"
        return

    month = NOW.strftime("%Y-%m")
    if st.get("month") != month:
        st["month"], st["used"] = month, 0
    limit = int(os.environ.get("ODDSPAPI_MONTHLY_LIMIT", "250"))
    per_run = int(os.environ.get("ODDSPAPI_PER_RUN", "6"))
    remaining = limit - st["used"]
    if not is_due(st, remaining, per_run):
        log("OddsPapi : pas encore dû (reste %d requêtes ce mois)" % remaining)
        return
    budget = [min(per_run, remaining)]
    cache = st.get("cache") if isinstance(st.get("cache"), dict) and "tournaments" in st.get("cache", {}) else {}
    st["cache"] = cache
    players = st.setdefault("players", {})

    def call(path, params, fast=False, catalog=False):
        # le catalogue hebdomadaire ne compte pas dans le budget du passage (mais dans le quota du mois)
        if (budget[0] <= 0 and not catalog) or st["used"] >= limit:
            raise Budget()
        for attempt in (1, 2):
            time.sleep(0.15 if fast else 0.7)
            if not catalog:
                budget[0] -= 1
            st["used"] += 1
            try:
                data, _ = get_json(OP_BASE + path, {"apiKey": key, **params})
                return data
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt == 1:
                    wait = float(e.headers.get("Retry-After") or 2)
                    log("OddsPapi : 429, nouvelle tentative dans %.0fs" % wait)
                    time.sleep(min(wait, 20) + 0.5)
                    continue
                raise

    summary = {"run": iso(NOW), "fixtures": [], "unmapped": {}}
    try:
        if not parse_dt(cache.get("at")) or NOW - parse_dt(cache["at"]) > timedelta(days=7):
            refresh_oddspapi_catalog(lambda path, params: call(path, params, catalog=True), cache)
        books = cache.get("books", {})
        st["books"] = sorted(books)
        if not books:
            st["status"] = "aucun de nos bookmakers trouvé"
            return
        slug_to_book = {v: k for k, v in books.items()}
        slugs = ",".join(books.values())

        jobs = [("t", t) for t in cache.get("tournaments", [])] + [("cycling", None)]
        idx = st.get("rotation", 0) % len(jobs)
        st["rotation"] = idx + 1
        kind, t = jobs[idx]
        if kind == "cycling":
            run_oddspapi_cycling(call, store, slug_to_book, slugs, summary)
        else:
            fixtures = call("/fixtures/odds/main", {"tournamentId": t["id"], "bookmakers": slugs}, fast=True)
            fixtures = [f for f in as_list(fixtures) if NOW < (parse_dt(f.get("startTime")) or NOW) <= HORIZON]
            fixtures.sort(key=lambda f: f.get("startTime") or 0)
            for f in fixtures:
                ingest_fixture(store, f, t, slug_to_book, cache["markets"], players, summary)
            # marchés joueurs : matchs les plus proches, tant qu'il reste du budget
            # (on garde 1 requête pour les noms des joueurs)
            for f in fixtures:
                if budget[0] <= 1:
                    break
                full = call("/fixtures/odds", {"fixtureId": f["fixtureId"], "bookmakers": slugs}, fast=True)
                ingest_fixture(store, full, t, slug_to_book, cache["markets"], players, summary)
        resolve_player_names(call, store, players)
        st["status"] = "ok"
    except Budget:
        st["status"] = "ok (budget du passage épuisé)"
    except urllib.error.HTTPError as e:
        st["status"] = "erreur %s" % e.code
        log("OddsPapi : erreur", e.code)
    st["lastRun"] = iso(NOW)
    if len(players) > 4000:  # garde le cache de noms compact
        st["players"] = dict(list(players.items())[-3000:])
    (COVERAGE_DIR / "oddspapi-dernier-passage.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False))
    log("OddsPapi : %d requêtes utilisées ce mois / %d" % (st["used"], limit))


def refresh_oddspapi_catalog(call, cache):
    """Bookmakers, compétitions suivies et marchés utiles (rafraîchi chaque semaine)."""
    slugs = [first(b, "slug", "bookmaker", default="") if isinstance(b, dict) else str(b)
             for b in as_list(call("/bookmakers", {}))]
    chosen = {}
    for sl in slugs:
        book = map_book(str(sl))
        if book and (book not in chosen or ".fr" in str(sl)):
            chosen[book] = sl
    cache["books"] = chosen

    tournaments, markets = [], {}
    for sport, sid in OP_SPORT_IDS.items():
        if sport == "cyclisme":
            continue
        tlist = as_list(call("/tournaments", {"sportId": sid}))
        for tsport, name_pat, cat_pat in OP_TOURNAMENTS:
            if tsport != sport:
                continue
            for t in tlist:
                if re.search(name_pat, str(t.get("tournamentName", "")).lower()) and \
                        re.search(cat_pat, str(t.get("categoryName", "")).lower()):
                    tournaments.append({"id": t["tournamentId"], "sport": sport,
                                        "name": t["tournamentName"], "category": t.get("categoryName")})
                    break
        for m in as_list(call("/markets", {"sportId": sid})):
            entry = classify_market(sport, m)
            if entry:
                markets[str(m["marketId"])] = entry
    order = {(s, n): i for i, (s, n, _) in enumerate(OP_TOURNAMENTS)}
    tournaments.sort(key=lambda t: next((i for (s, n), i in order.items()
                                         if s == t["sport"] and re.search(n, t["name"].lower())), 99))
    cache["tournaments"] = tournaments
    cache["markets"] = markets
    cache["at"] = iso(NOW)
    (COVERAGE_DIR / "oddspapi-catalogue.json").write_text(json.dumps(
        {"bookmakers_dispo": [s for s in slugs if map_book(str(s))], "retenus": chosen,
         "competitions": tournaments, "marches_utiles": len(markets)}, indent=1, ensure_ascii=False))


def classify_market(sport, m):
    """Renvoie [clé, libellé, type, palier, {outcomeId: côté}] pour un marché utile, sinon None."""
    name, mtype = m.get("marketName") or "", m.get("marketType")
    period, hcp = m.get("period"), float(m.get("handicap") or 0)
    outs = {str(o["outcomeId"]): o.get("outcomeName") for o in m.get("outcomes") or []}
    if not m.get("playerProp"):
        if mtype == "1x2" and period == "fulltime" and hcp == 0 and sport != "basket":
            return ["1x2", "Résultat 1N2", "main", None, outs]
        if mtype == "moneyline" and period == "result" and sport == "basket":
            return ["ml", "Vainqueur", "main", None, outs]
        return None
    for psport, pat, k, label, kind in OP_PROPS:
        if psport == sport and re.search(pat, name):
            line = int(hcp) + 1 if kind == "over" else None
            return [k, label, kind, line, outs]
    return None


def ingest_fixture(store, f, t, slug_to_book, markets, players, summary):
    parts = f.get("participants") or {}
    home, away = parts.get("participant1Name"), parts.get("participant2Name")
    date = parse_dt(f.get("startTime"))
    if not (home and away and date):
        return
    comp = "%s · %s" % (t["name"], t["category"]) if t.get("category") and t["category"].lower() not in t["name"].lower() else t["name"]
    ev = store.find_or_create(t["sport"], comp, date, home, away)
    by_outcome = {oid: mid for mid, m in markets.items() for oid in m[4]}
    n = 0
    for sl, rows in (f.get("odds") or {}).items():
        book = slug_to_book.get(sl)
        if not book or not isinstance(rows, dict):
            continue
        for row in rows.values():
            if not row.get("active", True) or row.get("marketActive") is False:
                continue
            m = markets.get(str(row.get("marketId"))) or markets.get(by_outcome.get(str(row.get("outcomeId")), ""))
            if not m:
                mid = str(row.get("marketId"))
                summary["unmapped"][mid] = summary["unmapped"].get(mid, 0) + 1
                continue
            key, label, kind, line, outs = m
            side = outs.get(str(row.get("outcomeId")))
            pid = str(row.get("playerId") or 0)
            if kind == "main":
                k, name, order = {"1": ("home", ev["home"], 0), "X": ("draw", "Match nul", 1),
                                  "2": ("away", ev["away"], 2)}.get(side, (None, None, None))
                if k:
                    store.set_odds(ev, key, label, k, name, book, row.get("price"), complete=True, order=order)
                    n += 1
                continue
            if (kind == "yes" and side != "Yes") or (kind == "over" and side != "Over") or pid == "0":
                continue
            name = players.get(pid) or "#" + pid
            store.set_odds(ev, key, label, "%s|%s" % (pid, line if line is not None else ""), name, book,
                           row.get("price"), line=line)
            n += 1
    summary["fixtures"].append({"match": "%s - %s" % (home, away), "cotes": n})


def resolve_player_names(call, store, players):
    """Remplace les « #id » par les noms (une requête groupée)."""
    missing = sorted({s["key"].split("|")[0] for ev in store.events.values() for m in ev["markets"].values()
                      for s in m["selections"].values() if s["name"].startswith("#")} - set(players))
    if missing:
        for p in as_list(call("/players", {"playerIds": ",".join(missing[:200])})):
            players[str(p.get("playerId"))] = player_name(p.get("playerName"))
    for ev in store.events.values():
        for m in ev["markets"].values():
            for s in m["selections"].values():
                if s["name"].startswith("#"):
                    s["name"] = players.get(s["name"][1:], s["name"])


def run_oddspapi_cycling(call, store, slug_to_book, slugs, summary):
    """Courses cyclistes = « futures » (marché vainqueur, participant = coureur)."""
    futures = as_list(call("/futures", {"sportId": OP_SPORT_IDS["cyclisme"],
                                        "startTimeFrom": int(NOW.timestamp()) - 86400,
                                        "startTimeTo": int(HORIZON.timestamp()), "bookmakers": slugs}))
    futures = [f for f in futures if str(f.get("futureId", "")).endswith("68001")]
    futures.sort(key=lambda f: f.get("startTime") or 0)
    names = {}
    for fut in futures[:2]:
        data = call("/futures/odds", {"futureId": fut["futureId"], "bookmakers": slugs}, fast=True)
        date = parse_dt(fut.get("startTime"))
        race = (fut.get("season") or {}).get("seasonName") or (fut.get("tournament") or {}).get("tournamentName") or "Course"
        comp = (fut.get("tournament") or {}).get("tournamentName") or "Cyclisme"
        ev = store.find_or_create("cyclisme", comp, date, race, "Vainqueur de la course")
        rows = []
        for sl, b in (data.get("bookmakers") or {}).items():
            book = slug_to_book.get(sl)
            for o in (b.get("odds") or []) if book else []:
                if o.get("active", True) and o.get("futureOutcomeId") in (0, None):
                    rows.append((book, str(o.get("participantId")), o.get("price")))
        ids = sorted({r[1] for r in rows} - set(names))
        if ids:
            for p in as_list(call("/participants", {"participantIds": ",".join(ids[:200])})):
                names[str(p.get("participantId"))] = p.get("name")
        for book, pid, price in rows:
            store.set_odds(ev, "win", "Vainqueur", pid, names.get(pid, "Coureur #" + pid), book, price)
        summary["fixtures"].append({"course": race, "cotes": len(rows)})


def first(d, *keys, default=None):
    for k in keys:
        if isinstance(d, dict) and d.get(k) not in (None, ""):
            return d[k]
    return default


def as_list(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in ("data", "results"):
            if isinstance(data.get(k), list):
                return data[k]
        if data and all(isinstance(v, dict) for v in data.values()):
            return list(data.values())
    return []


# ---------------------------------------------------------------- main
def main():
    COVERAGE_DIR.mkdir(parents=True, exist_ok=True)
    previous = load_json(OUT, {})
    state = load_json(STATE, {})
    store = Store(previous)

    for name, fn in (("The Odds API", run_the_odds_api), ("OddsPapi", run_oddspapi)):
        try:
            fn(store, state)
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            log(name, ": échec", type(e).__name__)
            state.setdefault(name.lower().replace(" ", ""), {})["status"] = "échec réseau"

    events = store.export()
    coverage = {b: {"events": 0, "odds": 0} for b in BOOKS}
    for ev in events:
        seen = set()
        for m in ev["markets"]:
            for s in m["selections"]:
                for b in s["odds"]:
                    if b in coverage:
                        coverage[b]["odds"] += 1
                        seen.add(b)
        for b in seen:
            coverage[b]["events"] += 1

    public_sources = {k: {kk: v.get(kk) for kk in ("status", "lastRun", "remaining", "used", "books")}
                      for k, v in state.items() if isinstance(v, dict)}
    OUT.write_text(json.dumps({"generatedAt": iso(NOW), "sources": public_sources,
                               "coverage": coverage, "events": events}, ensure_ascii=False, separators=(",", ":")))
    STATE.write_text(json.dumps(state, indent=1, ensure_ascii=False))
    log("→ %d événements écrits dans docs/odds.json" % len(events))
    log("Couverture :", ", ".join("%s %d" % (b, c["odds"]) for b, c in coverage.items()))


if __name__ == "__main__":
    main()
