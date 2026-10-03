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
HORIZON = NOW + timedelta(days=int(os.environ.get("HORIZON_DAYS", "14")))

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
    def set_odds(ev, mkey, label, sel_key, sel_name, book, price, complete=False, team=None, line=None, order=None, extra=None):
        try:
            price = round(float(price), 2)
        except (TypeError, ValueError):
            return
        if price <= 1.0:
            return
        m = ev["markets"].setdefault(mkey, {"key": mkey, "label": label, "complete": complete, "selections": {}})
        if extra:
            m.update(extra)
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
TOA_SPORTS = [  # ordre = priorité ; chaque compétition coûte 1 crédit par passage, quel que soit le nombre de matchs
    ("football", r"^soccer_france_ligue_one$"),
    ("football", r"^soccer_uefa_champs_league$"),
    ("football", r"^soccer_epl$"),
    ("football", r"^soccer_spain_la_liga$"),
    ("football", r"^soccer_italy_serie_a$"),
    ("football", r"^soccer_germany_bundesliga$"),
    ("basket", r"^basketball_nba(_preseason)?$"),
    ("basket", r"^basketball_euroleague$"),
    ("rugby", r"^rugbyunion_(top_14|france|champions_cup|six_nations)"),
    ("football", r"^soccer_france_ligue_two$"),
    ("football", r"^soccer_uefa_europa_league$"),
    ("football", r"^soccer_uefa_europa_conference_league$"),
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

    (COVERAGE_DIR / "theoddsapi-competitions.json").write_text(json.dumps(  # diagnostic gratuit
        sorted("%s | %s%s" % (x["key"], x["title"], "" if x.get("active") else " (inactive)") for x in sports
               if re.match(r"^(soccer|basketball|rugby)", x["key"])), indent=1, ensure_ascii=False))
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
    ("rugby", r"^top 14$", r""),
    ("football", r"^la ?liga$", r"spain"),
    ("basket", r"^euroleague$", r""),
    ("football", r"^serie a$", r"italy"),
    ("football", r"^bundesliga$", r"germany"),
    ("football", r"^uefa europa league$", r""),
    ("football", r"^ligue 2$", r"france"),
    ("football", r"conference league", r""),
    ("rugby", r"champions cup", r""),
    ("basket", r"betclic elite|^pro a$|^lnb", r"france"),
    ("cyclisme", r"^(il lombardia|paris-tours|tour de france|giro d ?italia|vuelta a espana|paris-roubaix|tour of flanders|"
                 r"liege-bastogne-liege|milano-sanremo|milan-san ?remo|paris-nice|amstel gold|la fleche wallone|"
                 r"criterium du dauphine|world championship road race)$", r""),
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
    ("cyclisme", r"^Winner|^Outright", "win", "Vainqueur", "any"),
    ("cyclisme", r"Top ?3|Podium", "podium", "Top 3", "any"),
    ("cyclisme", r"Top ?10", "top10", "Top 10", "any"),
]


CATALOG_VERSION = 8   # à incrémenter quand les règles ci-dessus changent


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
    teams = st.setdefault("teams", {})
    empty = st.setdefault("empty", {})  # compétitions sans match → date de la prochaine vérification

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
        if cache.get("v") != CATALOG_VERSION or not parse_dt(cache.get("at")) or NOW - parse_dt(cache["at"]) > timedelta(days=7):
            refresh_oddspapi_catalog(lambda path, params: call(path, params, catalog=True), cache)
        books = cache.get("books", {})
        st["books"] = sorted(books)
        if not books:
            st["status"] = "aucun de nos bookmakers trouvé"
            return
        slug_to_book = {v: k for k, v in books.items()}

        tinfo = st.setdefault("tinfo", {})  # compétition → prochain match connu, dernier passage
        st.pop("rotation", None)

        def priority(t):
            info = tinfo.get(str(t["id"]), {})
            nxt = parse_dt(info.get("next"))
            soon = nxt is None or nxt <= NOW + timedelta(days=3)  # inconnu = à découvrir
            return (0 if soon else 1, parse_dt(info.get("last")) or datetime(2000, 1, 1, tzinfo=timezone.utc))

        focus = st.pop("focus", None)  # demande ponctuelle : ces compétitions d'abord
        ordered = sorted(cache.get("tournaments", []), key=priority)
        if focus:
            ordered = [t for t in ordered if re.search(focus, t["name"], re.I)] + \
                      [t for t in ordered if not re.search(focus, t["name"], re.I)]
        for t in ordered:
            if budget[0] < 2:
                break
            jid = str(t["id"])
            if parse_dt(empty.get(jid)) and NOW < parse_dt(empty[jid]) and not (focus and re.search(focus, t["name"], re.I)):
                continue  # sans match lors d'un passage récent : on ne repaie pas une requête
            try:
                found, nxt = run_oddspapi_tournament(call, budget, store, t, slug_to_book, cache, teams, summary, st)
            except urllib.error.HTTPError as e:
                body = ""
                try:
                    body = e.read().decode("utf-8", "replace")[:300]
                except Exception:
                    pass
                summary.setdefault("erreurs", []).append({"nom": t["name"], "code": e.code, "message": body})
                if e.code != 404:
                    raise
                continue  # erreur ≠ compétition vide : on ne la met pas en attente
            tinfo[jid] = {"last": iso(NOW), "next": iso(nxt) if nxt else None}
            summary.setdefault("competitions", []).append({"nom": t["name"], "matchs": found})
            if found:
                empty.pop(jid, None)
            else:  # rien dans les 7 jours : on revient 2 jours avant le prochain match connu (au moins 24 h)
                wait = max(NOW + timedelta(hours=24), nxt - timedelta(days=2)) if nxt else NOW + timedelta(hours=72)
                empty[jid] = iso(wait)
        try:
            resolve_player_names(call, store, players)
        except urllib.error.HTTPError:
            pass
        if FORCE:  # diagnostic seulement lors d'un lancement manuel (coûte 1 requête)
            name_unmapped_markets(lambda path, params: call(path, params, catalog=True), summary)
        st["status"] = "ok"
    except Budget:
        st["status"] = "ok (budget du passage épuisé)"
    except urllib.error.HTTPError as e:
        st["status"] = "erreur %s" % e.code
        log("OddsPapi : erreur", e.code)
    st["lastRun"] = iso(NOW)
    for k in ("players", "teams"):  # garde les caches de noms compacts
        if len(st.get(k, {})) > 4000:
            st[k] = dict(list(st[k].items())[-3000:])
    (COVERAGE_DIR / "oddspapi-dernier-passage.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False))
    log("OddsPapi : %d requêtes utilisées ce mois / %d" % (st["used"], limit))


def run_oddspapi_tournament(call, budget, store, t, slug_to_book, cache, teams, summary, st):
    """1) /fixtures : calendrier + noms d'équipes (1 requête) ; s'il n'y a aucun match proche, on s'arrête là.
    2) /odds-by-tournaments : tous les marchés de tous les matchs, un bookmaker par requête."""
    calendar = [f for f in as_list(call("/fixtures", {"tournamentId": t["id"], "startTimeFrom": int(NOW.timestamp()),
                                                       "startTimeTo": int(HORIZON.timestamp())}))
                if isinstance(f, dict) and (parse_dt(f.get("startTime")) or NOW) > NOW]
    future = sorted(parse_dt(f["startTime"]) for f in calendar)
    nxt = future[0] if future else None
    soon = [f for f in calendar if parse_dt(f["startTime"]) <= HORIZON]
    for f in soon:
        for i in ("1", "2"):
            if f.get("participant%sId" % i) and f.get("participant%sName" % i):
                teams[str(f["participant%sId" % i])] = f["participant%sName" % i]
    if not soon:
        return 0, nxt

    merged = {}

    def fetch(bk):
        try:
            data = call("/odds-by-tournaments", {"tournamentIds": t["id"], "bookmaker": bk}, fast=True)
        except urllib.error.HTTPError as e:
            if e.code == 404:  # « No fixtures found for … bookmaker » : ce bookmaker ne couvre pas la compétition
                return
            raise
        for f in as_list(data):
            if isinstance(f, dict) and f.get("fixtureId"):
                m = merged.setdefault(f["fixtureId"], {**f, "bookmakerOdds": {}})
                m["bookmakerOdds"].update(f.get("bookmakerOdds") or {})

    slugs = list(slug_to_book)
    if cache.get("multi") is None:  # l'API accepte-t-elle plusieurs bookmakers à la fois ? (testé une fois)
        try:
            fetch(",".join(slugs))
        except urllib.error.HTTPError as e:
            if e.code not in (400, 404, 422):
                raise
        cache["multi"] = len({b for f in merged.values() for b in f["bookmakerOdds"]}) > 1
    elif cache.get("multi"):
        fetch(",".join(slugs))
    if not cache.get("multi"):
        miss = st.setdefault("miss", {})  # « compétition:bookmaker » sans données → ne pas redemander avant…
        for bk in slugs:
            if budget[0] <= 0:
                break
            mk = "%s:%s" % (t["id"], bk)
            if parse_dt(miss.get(mk)) and NOW < parse_dt(miss[mk]):
                continue
            if not any(bk in f["bookmakerOdds"] for f in merged.values()):
                fetch(bk)
                if any(bk in f["bookmakerOdds"] for f in merged.values()):
                    miss.pop(mk, None)
                else:
                    miss[mk] = iso(NOW + timedelta(hours=48))

    fixtures = [f for f in merged.values() if NOW < (parse_dt(f.get("startTime")) or NOW) <= HORIZON]
    still = sorted({str(f.get(k)) for f in fixtures for k in ("participant1Id", "participant2Id")} - set(teams))
    if still:
        summary["noms_manquants"] = still[:20]
    for f in fixtures:
        ingest_fixture(store, f, t, slug_to_book, cache["markets"], teams, summary)
    return len(soon), nxt


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
        tlist = as_list(call("/tournaments", {"sportId": sid}))
        for tsport, name_pat, cat_pat in OP_TOURNAMENTS:
            if tsport != sport:
                continue
            for t in tlist:
                if re.search(name_pat, str(t.get("tournamentName", "")).lower()) and \
                        re.search(cat_pat, str(t.get("categoryName", "")).lower()):
                    tournaments.append({"id": t["tournamentId"], "sport": sport,
                                        "name": t["tournamentName"], "category": t.get("categoryName")})
                    if sport != "cyclisme":
                        break
        if sport == "rugby":
            cache["rugby_tournaments_seen"] = sorted({"%s (%s)" % (t.get("tournamentName"), t.get("categoryName")) for t in tlist})[:300]
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
    cache["v"] = CATALOG_VERSION
    (COVERAGE_DIR / "oddspapi-catalogue.json").write_text(json.dumps(
        {"bookmakers_dispo": [s for s in slugs if map_book(str(s))], "retenus": chosen,
         "competitions": tournaments, "marches_utiles": len(markets),
         "competitions_rugby_dispo": cache.pop("rugby_tournaments_seen", [])}, indent=1, ensure_ascii=False))


def classify_market(sport, m):
    """Renvoie [clé, libellé, type, palier, {outcomeId: côté}] pour un marché utile, sinon None."""
    name, mtype = m.get("marketName") or "", m.get("marketType")
    period, hcp = m.get("period"), float(m.get("handicap") or 0)
    outs = {str(o["outcomeId"]): o.get("outcomeName") for o in m.get("outcomes") or []}
    if not m.get("playerProp") and sport != "cyclisme":
        if mtype == "1x2" and period == "fulltime" and hcp == 0 and sport != "basket" \
                and re.match(r"^((Full Time|Regular Time) Result|1X2)$", name):
            return ["1x2", "Résultat 1N2", "main", None, outs]
        if mtype == "bothteamsscore" and period == "fulltime" and sport == "football":
            return ["btts", "Les deux équipes marquent", "pair", None, outs]
        if mtype == "totals" and period == "fulltime" and sport == "football" and name == "Over Under Full Time":
            return ["totals", "Plus/moins de buts", "total", hcp, outs]
        if mtype == "moneyline" and sport == "basket" and re.match(r"^Winner \(incl\. overtime\)$", name):
            return ["ml", "Vainqueur", "main", None, outs]
        return None
    for psport, pat, k, label, kind in OP_PROPS:
        if psport == sport and re.search(pat, name):
            line = int(hcp) + 1 if kind == "over" else None
            return [k, label, kind, line, outs]
    return None


def ingest_fixture(store, f, t, slug_to_book, markets, teams, summary):
    """bookmakerOdds → slug → markets → marketId → outcomes → outcomeId → players → playerId → {price, playerName}"""
    home = f.get("participant1Name") or teams.get(str(f.get("participant1Id")))
    away = f.get("participant2Name") or teams.get(str(f.get("participant2Id")))
    date = parse_dt(f.get("startTime"))
    if not (home and away and date):
        return
    comp = "%s · %s" % (t["name"], t["category"]) if t.get("category") and t["category"].lower() not in t["name"].lower() else t["name"]
    ev = store.find_or_create(t["sport"], comp, date, home, away)
    by_outcome = {oid: mid for mid, m in markets.items() for oid in m[4]}
    n = 0
    for sl, bdata in (f.get("bookmakerOdds") or {}).items():
        book = slug_to_book.get(sl)
        if not book or not isinstance(bdata, dict):
            continue
        stats = summary.setdefault("par_bookmaker", {}).setdefault(book, {"marches": 0, "actifs": 0, "reconnus": 0, "suspendu": 0})
        stats["marches"] += len(bdata.get("markets") or {})
        if bdata.get("suspended"):
            stats["suspendu"] += 1
            continue
        for mid, mk in (bdata.get("markets") or {}).items():
            if not isinstance(mk, dict) or mk.get("marketActive") is False:
                continue
            stats["actifs"] += 1
            for oid, out in (mk.get("outcomes") or {}).items():
                m = markets.get(str(mid)) or markets.get(by_outcome.get(str(oid), ""))
                if not m:
                    u = summary["unmapped"].setdefault(book, {})
                    u[str(mid)] = u.get(str(mid), 0) + 1
                    break
                stats["reconnus"] += 1
                key, label, kind, line, outs = m
                side = outs.get(str(oid))
                for pid, row in ((out or {}).get("players") or {}).items():
                    if not isinstance(row, dict) or not row.get("active", True):
                        continue
                    price = row.get("price")
                    if kind == "main":
                        k, name, order = {"1": ("home", ev["home"], 0), "X": ("draw", "Match nul", 1),
                                          "2": ("away", ev["away"], 2)}.get(side, (None, None, None))
                        if k:
                            store.set_odds(ev, key, label, k, name, book, price, complete=True, order=order)
                            n += 1
                        continue
                    if kind == "pair":
                        store.set_odds(ev, key, label, side, {"Yes": "Oui", "No": "Non"}.get(side, side), book, price,
                                       complete=True, order=0 if side == "Yes" else 1)
                        n += 1
                        continue
                    if kind == "total":
                        if side in ("Over", "Under"):
                            store.set_odds(ev, key, label, "%s|%s" % (side, line),
                                           ("Plus de %g" if side == "Over" else "Moins de %g") % line, book, price,
                                           complete=True, line=line, order=0 if side == "Over" else 1, extra={"lineFmt": "raw"})
                            n += 1
                        continue
                    if (kind == "yes" and side != "Yes") or (kind == "over" and side != "Over"):
                        continue
                    if kind in ("yes", "over") and str(pid) == "0":
                        continue
                    name = player_name(row.get("playerName")) or (side if kind == "any" else "#" + str(pid))
                    store.set_odds(ev, key, label, "%s|%s|%s" % (oid if kind == "any" else "", pid, line if line is not None else ""),
                                   name, book, price, line=line)
                    n += 1
    summary["fixtures"].append({"match": "%s - %s" % (home, away), "cotes": n,
                                "bookmakers": sorted(slug_to_book.get(b, b) for b in (f.get("bookmakerOdds") or {}))})


def name_unmapped_markets(call, summary):
    """Diagnostic : récupère le nom des marchés non reconnus les plus fréquents."""
    ids = {}
    for book, u in summary["unmapped"].items():
        for mid, n in u.items():
            ids[mid] = ids.get(mid, 0) + n
    top = sorted(ids, key=lambda k: -ids[k])[:60]
    if not top:
        return
    try:
        names = {str(m.get("marketId")): "%s [%s, ligne %s%s]" % (
                     m.get("marketName"), m.get("period"), m.get("handicap"),
                     ", issues : " + "/".join(str(o.get("outcomeName")) for o in m.get("outcomes") or []) if m.get("playerProp") else "")
                 for m in as_list(call("/markets", {"marketIds": ",".join(top)}))}
    except (urllib.error.HTTPError, Budget):
        return
    summary["unmapped"] = {book: {"%s %s" % (mid, names.get(mid, "?")): n for mid, n in sorted(u.items(), key=lambda x: -x[1])}
                           for book, u in summary["unmapped"].items()}


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
