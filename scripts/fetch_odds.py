#!/usr/bin/env python3
"""
Collecte des cotes Unibet / Winamax / PMU / Betclic / bwin / PokerStars
et écriture de docs/odds.json (lu par le site).

Sources :
  - The Odds API  (clé : ODDS_API_KEY)   → 1N2 / vainqueur, région FR
  - OddsPapi      (clé : ODDSPAPI_KEY)   → bwin, PokerStars, marchés joueurs si dispo

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
OP_BASE = "https://api.oddspapi.io/v4"
OP_SPORTS = [("football", r"soccer|football"), ("basket", r"basket"), ("rugby", r"rugby union|^rugby$"), ("cyclisme", r"cycl")]


def first(d, *keys, default=None):
    for k in keys:
        if isinstance(d, dict) and d.get(k) not in (None, ""):
            return d[k]
    return default


def entries(x):
    """(clé, valeur) que x soit un dict ou une liste."""
    if isinstance(x, dict):
        return list(x.items())
    if isinstance(x, list):
        return [(first(v, "id", "marketId", "outcomeId", "playerId", default=i), v) for i, v in enumerate(x)]
    return []


def as_list(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in ("data", "results", "fixtures", "sports", "bookmakers", "markets"):
            if isinstance(data.get(k), list):
                return data[k]
        return list(data.values()) if all(isinstance(v, dict) for v in data.values()) else []
    return []


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
    per_run = int(os.environ.get("ODDSPAPI_PER_RUN", "8"))
    remaining = limit - st["used"]
    if not is_due(st, remaining, per_run):
        log("OddsPapi : pas encore dû (reste %d requêtes ce mois)" % remaining)
        return
    budget = min(per_run, remaining)
    cache = st.setdefault("cache", {})

    def call(path, params):
        nonlocal budget
        if budget <= 0:
            raise RuntimeError("budget")
        budget -= 1
        st["used"] += 1
        data, _ = get_json(OP_BASE + path, {"apiKey": key, **params})
        return data

    try:
        # catalogues mis en cache (rafraîchis chaque semaine)
        fresh = parse_dt(cache.get("at")) and NOW - parse_dt(cache["at"]) < timedelta(days=7)
        if not fresh:
            cache["sports"] = [{"id": first(s, "sportId", "id"), "name": first(s, "sportName", "name", "slug", default="")}
                               for s in as_list(call("/sports", {}))]
            slugs = [first(b, "slug", "bookmaker", "id", default="") if isinstance(b, dict) else str(b)
                     for b in as_list(call("/bookmakers", {}))]
            chosen = {}
            for sl in slugs:
                book = map_book(str(sl))
                if book and (book not in chosen or (".fr" in str(sl) or "_fr" in str(sl))):
                    chosen[book] = sl
            cache["books"] = chosen
            try:
                cat = {}
                for m in as_list(call("/markets", {})):
                    mid = str(first(m, "marketId", "id", default=""))
                    cat[mid] = {"name": first(m, "marketName", "name", default=""),
                                "outcomes": {str(first(o, "outcomeId", "id")): first(o, "outcomeName", "name", default="")
                                             for o in (m.get("outcomes") or []) if isinstance(o, dict)}}
                cache["markets"] = cat
            except (urllib.error.HTTPError, RuntimeError):
                cache.setdefault("markets", {})
            cache["at"] = iso(NOW)
            (COVERAGE_DIR / "oddspapi-bookmakers.json").write_text(json.dumps({"tous": slugs, "retenus": chosen}, indent=1, ensure_ascii=False))

        books = cache.get("books", {})
        st["books"] = sorted(books)
        if not books:
            st["status"] = "aucun de nos bookmakers trouvé"
            return
        slug_to_book = {v: k for k, v in books.items()}

        # une discipline par passage, à tour de rôle
        sports = [(sp, s) for sp, pat in OP_SPORTS for s in cache["sports"] if re.search(pat, str(s["name"]).lower())]
        if not sports:
            st["status"] = "sports introuvables"
            return
        idx = st.get("rotation", 0) % len(sports)
        st["rotation"] = idx + 1
        sport, s = sports[idx]

        fixtures = as_list(call("/fixtures", {"sportId": s["id"], "from": NOW.strftime("%Y-%m-%d"),
                                              "to": HORIZON.strftime("%Y-%m-%d"), "hasOdds": "true"}))
        fixtures.sort(key=lambda f: str(first(f, "startTime", "trueStartTime", "date", default="")))
        dumped = False
        for f in fixtures:
            if budget <= 0:
                break
            fid = first(f, "fixtureId", "id")
            date = parse_dt(first(f, "startTime", "trueStartTime", "date"))
            if not fid or not date or date < NOW:
                continue
            home = first(f, "participant1Name", "homeTeam", "home", default="?")
            away = first(f, "participant2Name", "awayTeam", "away", default="")
            comp = first(f, "tournamentName", "competitionName", "tournament", default=s["name"])
            data = call("/odds", {"fixtureId": fid, "bookmakers": ",".join(books.values())})
            if not dumped:  # échantillon brut pour affiner la correspondance des marchés
                (COVERAGE_DIR / ("oddspapi-sample-%s.json" % sport)).write_text(json.dumps(data, indent=1, ensure_ascii=False)[:400000])
                dumped = True
            ev = store.find_or_create(sport, comp, date, home, away if away else comp)
            parse_oddspapi_odds(store, ev, data, slug_to_book, cache.get("markets", {}))
        st["status"] = "ok"
    except RuntimeError:
        st["status"] = "ok (budget du passage épuisé)"
    except urllib.error.HTTPError as e:
        st["status"] = "erreur %s" % e.code
        log("OddsPapi : erreur", e.code)
    st["lastRun"] = iso(NOW)
    log("OddsPapi : %d requêtes utilisées ce mois / %d" % (st["used"], limit))


def parse_oddspapi_odds(store, ev, data, slug_to_book, catalog):
    """bookmakerOdds → slug → markets → marketId → outcomes → outcomeId → players → id → price"""
    bo = first(data, "bookmakerOdds", default={}) or {}
    for sl, bdata in entries(bo):
        book = slug_to_book.get(sl)
        if not book:
            continue
        for mid, m in entries(first(bdata, "markets")):
            mid = str(mid)
            info = catalog.get(mid, {})
            is_1x2 = mid == "101"
            mkey = "1x2" if is_1x2 else "op_" + mid
            label = "Résultat 1N2" if is_1x2 else (info.get("name") or "Marché %s" % mid)
            for oid, o in entries(first(m, "outcomes")):
                oid = str(oid)
                oname = info.get("outcomes", {}).get(oid) or first(o, "outcomeName", "name", default=oid)
                for pid, p in entries(first(o, "players")):
                    if not isinstance(p, dict) or first(p, "active", default=True) is False:
                        continue
                    price = first(p, "price", "odds")
                    if is_1x2:
                        k, name, order = {"101": ("home", ev["home"], 0), "102": ("draw", "Match nul", 1),
                                          "103": ("away", ev["away"], 2)}.get(oid, (oid, oname, 3))
                        store.set_odds(ev, mkey, label, k, name, book, price, complete=True, order=order)
                    else:
                        player = first(p, "playerName", "participantName", default=None) if str(pid) != "0" else None
                        name = "%s — %s" % (player, oname) if player else oname
                        store.set_odds(ev, mkey, label, "%s:%s" % (oid, pid), name, book, price)


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
