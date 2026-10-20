#!/usr/bin/env python3
"""
Props joueurs NBA — Unibet, Winamax, PMU, bwin, PokerStars (via OddsPapi)
→ docs/nba.json, lu par le site.

Adresses OddsPapi réellement disponibles (vérifiées) : /bookmakers, /tournaments,
/markets, /fixtures, /odds-by-tournaments, /players.
Le quota mensuel est réparti sur le reste du mois ; on ne dépense rien s'il n'y a
pas de match dans les 36 h.

Usage : python3 scripts/fetch_nba.py [--force]   (stdlib uniquement, Python ≥ 3.9)
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "nba.json"
STATE = ROOT / "data" / "nba-state.json"
DIAG = ROOT / "data" / "nba-diagnostic.json"

KEY = os.environ.get("ODDSPAPI_KEY")
FORCE = "--force" in sys.argv or os.environ.get("FORCE") == "true"
LIMIT = int(os.environ.get("ODDSPAPI_MONTHLY_LIMIT", "250"))
NOW = datetime.now(timezone.utc)
BASE = "https://api.oddspapi.io/v4"
CATALOG_VERSION = 1

BOOKS = {"unibet": r"unibet", "winamax": r"winamax", "pmu": r"\bpmu", "betclic": r"betclic",
         "bwin": r"bwin", "pokerstars": r"pokerstars"}

# Statistiques suivies : clé → (motif sur le nom du marché OddsPapi, libellé)
STATS = {
    "pts": (r"Points", "Points"),
    "reb": (r"Rebounds", "Rebonds"),
    "ast": (r"Assists", "Passes"),
    "3pt": (r"3 Point FG", "Paniers à 3 pts"),
    "pra": (r"Points \+ Assists \+ Rebounds", "Pts + Reb + Pas"),
    "pr": (r"Points \+ Rebounds", "Pts + Reb"),
    "pa": (r"Points \+ Assists", "Pts + Pas"),
    "ra": (r"Assists \+ Rebounds", "Reb + Pas"),
    "stl": (r"Steals", "Interceptions"),
    "blk": (r"Blocks", "Contres"),
    "sb": (r"Steals \+ Blocks", "Int + Contres"),
    "tov": (r"Turnovers", "Balles perdues"),
}
SPECIAL = {r"^Player Double - Double": ("dd", "Double-double"), r"^Player Triple - Double": ("td", "Triple-double")}


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


def load(path, default):
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return default


def as_list(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        if isinstance(data.get("data"), list):
            return data["data"]
        if data and all(isinstance(v, dict) for v in data.values()):
            return list(data.values())
    return []


def player_name(raw):
    """« Jokic, Nikola » → « Nikola Jokic »."""
    if raw and "," in raw:
        last, _, first = raw.partition(",")
        return "%s %s" % (first.strip(), last.strip())
    return raw


class Budget(Exception):
    pass


class Api:
    def __init__(self, st, per_run):
        self.st, self.left = st, per_run

    def get(self, path, params, catalog=False):
        """catalog=True : hors budget du passage (mais compté dans le quota du mois)."""
        if (self.left <= 0 and not catalog) or self.st["used"] >= LIMIT:
            raise Budget()
        for attempt in (1, 2):
            time.sleep(1.1)  # OddsPapi impose ~1 s entre deux appels à la même adresse
            if not catalog:
                self.left -= 1
            self.st["used"] += 1
            url = BASE + path + "?" + urllib.parse.urlencode({"apiKey": KEY, **params})
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "CoteRadar/2.0"}), timeout=40) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt == 1:
                    time.sleep(3)
                    continue
                raise


# ------------------------------------------------------------------ catalogue
def classify(m):
    """Marché OddsPapi → (stat, libellé, type, palier, {outcomeId: côté}) ou None."""
    name = m.get("marketName") or ""
    if not m.get("playerProp") or "Quarter" in name or "Half" in name or "Period" in name:
        return None
    outs = {str(o["outcomeId"]): o.get("outcomeName") for o in m.get("outcomes") or []}
    hcp = float(m.get("handicap") or 0)
    for pat, (k, label) in SPECIAL.items():
        if re.search(pat, name):
            return [k, label, "yes", None, outs]
    over = name.startswith("Over Under Player ")
    body = name.replace("Over Under Player ", "").replace("Player ", "")
    body = re.sub(r"\s*\(incl\. overtime\)$", "", body).strip()
    # le motif le plus long d'abord (« Points + Assists + Rebounds » avant « Points »)
    for k, (pat, label) in sorted(STATS.items(), key=lambda kv: -len(kv[1][0])):
        if re.fullmatch(pat, body):
            if over:
                return [k, label, "over", int(hcp) + 1, outs]  # Over 24.5 → 25+
            return [k, label, "milestone", int(hcp) if hcp else None, outs]  # « 25+ » / « Yes »
    return None


def refresh_catalog(api, cache):
    slugs = [b.get("slug") if isinstance(b, dict) else str(b) for b in as_list(api.get("/bookmakers", {}, True))]
    chosen = {}
    for sl in slugs:
        for book, pat in BOOKS.items():
            if sl and re.search(pat, sl.lower()) and (book not in chosen or ".fr" in sl):
                chosen[book] = sl
    cache["books"] = chosen
    nba = [t for t in as_list(api.get("/tournaments", {"sportId": 11}, True))
           if str(t.get("tournamentName", "")).lower() == "nba"]
    cache["tournament"] = nba[0]["tournamentId"] if nba else 132
    markets, names = {}, {}
    for m in as_list(api.get("/markets", {}, True)):  # l'API renvoie tous les sports
        mid = str(m.get("marketId", ""))
        if m.get("sportId") != 11 and not mid.startswith("11"):
            continue
        entry = classify(m)
        if entry:
            markets[mid] = entry
        elif m.get("playerProp"):
            names[mid] = "%s [%s, ligne %s, issues %s]" % (m.get("marketName"), m.get("period"), m.get("handicap"),
                                                        "/".join(str(o.get("outcomeName")) for o in m.get("outcomes") or []))
    cache["markets"], cache["unmapped_names"] = markets, names
    cache["v"], cache["at"] = CATALOG_VERSION, iso(NOW)


# ------------------------------------------------------------------ collecte
def collect(api, st, games, diag):
    cache = st["cache"]
    tid = cache["tournament"]
    slug_to_book = {v: k for k, v in cache["books"].items()}
    markets = cache["markets"]
    by_outcome = {oid: mid for mid, m in markets.items() for oid in m[4]}
    teams = st.setdefault("teams", {})
    miss = st.setdefault("miss", {})
    merged = {}

    for sl, book in slug_to_book.items():
        if api.left <= 0:
            break
        if parse_dt(miss.get(sl)) and NOW < parse_dt(miss[sl]) and not FORCE:
            continue
        try:
            data = api.get("/odds-by-tournaments", {"tournamentIds": tid, "bookmaker": sl})
        except urllib.error.HTTPError as e:
            if e.code != 404:  # 404 = ce bookmaker n'a aucune cote NBA
                raise
            data = []
        got = False
        for f in as_list(data):
            if isinstance(f, dict) and f.get("fixtureId"):
                m = merged.setdefault(f["fixtureId"], {**f, "bookmakerOdds": {}})
                m["bookmakerOdds"].update(f.get("bookmakerOdds") or {})
                got = got or bool((f.get("bookmakerOdds") or {}).get(sl))
        if got:
            miss.pop(sl, None)
        else:
            miss[sl] = iso(NOW + timedelta(hours=24))
        diag["bookmakers"][book] = "cotes reçues" if got else "aucune cote NBA"

    # noms d'équipes : /fixtures une fois (les 30 équipes restent en cache)
    ids = {str(f.get(k)) for f in merged.values() for k in ("participant1Id", "participant2Id")}
    if ids - set(teams) and api.left > 0:
        for fx in as_list(api.get("/fixtures", {"tournamentId": tid})):
            for i in ("1", "2"):
                if fx.get("participant%sId" % i) and fx.get("participant%sName" % i):
                    teams[str(fx["participant%sId" % i])] = fx["participant%sName" % i]

    for f in merged.values():
        date = parse_dt(f.get("startTime"))
        if not date or date < NOW - timedelta(hours=4) or date > NOW + timedelta(days=4):
            continue
        gid = f["fixtureId"]
        g = games.setdefault(gid, {"id": gid, "players": {}, "updated": {}})
        g.update(date=iso(date), home=teams.get(str(f.get("participant1Id")), "Équipe 1"),
                 away=teams.get(str(f.get("participant2Id")), "Équipe 2"))
        for sl, b in (f.get("bookmakerOdds") or {}).items():
            book = slug_to_book.get(sl)
            if not book or not isinstance(b, dict) or b.get("suspended"):
                continue
            g["updated"][book] = iso(NOW)
            stats = diag["par_bookmaker"].setdefault(book, {})
            for mid, mk in (b.get("markets") or {}).items():
                if not isinstance(mk, dict) or mk.get("marketActive") is False:
                    continue
                for oid, out in (mk.get("outcomes") or {}).items():
                    m = markets.get(str(mid)) or markets.get(by_outcome.get(str(oid), ""))
                    if not m:
                        if str(mid) in cache.get("unmapped_names", {}):
                            n = cache["unmapped_names"][str(mid)]
                            diag["non_reconnus"][n] = diag["non_reconnus"].get(n, 0) + 1
                        break
                    stat, _, kind, line, outs = m
                    side = str(outs.get(str(oid)) or "")
                    if kind == "over" and side != "Over":
                        continue
                    if kind == "milestone":
                        num = re.match(r"^(\d+)\+?$", side)
                        if num:
                            line = int(num.group(1))
                        elif side != "Yes" or line is None:
                            diag["formats_paliers"][side] = diag["formats_paliers"].get(side, 0) + 1
                            continue
                    if kind == "yes" and side != "Yes":
                        continue
                    for pid, row in ((out or {}).get("players") or {}).items():
                        if str(pid) == "0" or not isinstance(row, dict) or not row.get("active", True):
                            continue
                        try:
                            price = round(float(row.get("price")), 2)
                        except (TypeError, ValueError):
                            continue
                        if price <= 1.0:
                            continue
                        p = g["players"].setdefault(str(pid), {"id": str(pid), "name": None, "stats": {}})
                        p["name"] = player_name(row.get("playerName")) or p["name"] or "Joueur %s" % pid
                        cell = p["stats"].setdefault(stat, {}).setdefault(str(line if line is not None else 1), {"o": {}, "p": {}})
                        old = cell["o"].get(book)
                        if old is not None and old != price:
                            cell["p"][book] = old
                        cell["o"][book] = price
                        stats[stat] = stats.get(stat, 0) + 1


def main():
    st = load(STATE, {})
    if not st:  # reprise du compteur de l'ancien script (même clé, même quota mensuel)
        old = load(ROOT / "data" / "state.json", {}).get("oddspapi", {})
        st = {"month": old.get("month"), "used": old.get("used", 0)}
    st.setdefault("cache", {})
    month = NOW.strftime("%Y-%m")
    if st.get("month") != month:
        st["month"], st["used"] = month, 0
    st.setdefault("used", 0)
    previous = load(OUT, {})
    games = {g["id"]: {**g, "players": {p["id"]: p for p in g["players"]}} for g in previous.get("games", [])}
    diag = {"run": iso(NOW), "bookmakers": {}, "par_bookmaker": {}, "non_reconnus": {}, "formats_paliers": {}}

    if not KEY:
        st["status"] = "pas de clé ODDSPAPI_KEY"
    else:
        books = max(1, len(st["cache"].get("books", BOOKS)))
        per_run = int(os.environ.get("ODDSPAPI_PER_RUN", str(books + 1)))
        remaining = LIMIT - st["used"]
        hours_left = max(1.0, ((NOW.replace(day=1) + timedelta(days=32)).replace(day=1, hour=0, minute=0, second=0)
                               - NOW).total_seconds() / 3600)
        last = parse_dt(st.get("lastRun"))
        earned = (NOW - last).total_seconds() / 3600 * remaining / hours_left if last else per_run
        next_game = parse_dt(st.get("nextGame"))
        game_soon = next_game is None or next_game < NOW + timedelta(hours=36)
        if FORCE or (remaining >= per_run and earned >= per_run and game_soon):
            api = Api(st, per_run)
            try:
                c = st["cache"]
                if c.get("v") != CATALOG_VERSION or not parse_dt(c.get("at")) or NOW - parse_dt(c["at"]) > timedelta(days=7):
                    refresh_catalog(api, c)
                collect(api, st, games, diag)
                st["status"] = "ok"
            except Budget:
                st["status"] = "ok (budget du passage épuisé)"
            except urllib.error.HTTPError as e:
                st["status"] = "erreur %s" % e.code
            except (urllib.error.URLError, TimeoutError, ValueError) as e:
                st["status"] = "échec réseau (%s)" % type(e).__name__
            st["lastRun"] = iso(NOW)
            DIAG.write_text(json.dumps(diag, indent=1, ensure_ascii=False))
        else:
            log("Pas de passage : reste %d requêtes, %.1f gagnées / %d nécessaires, match proche : %s"
                % (remaining, earned, per_run, game_soon))

    out_games = []
    for g in sorted(games.values(), key=lambda g: g.get("date", "")):
        d = parse_dt(g.get("date"))
        if not d or d < NOW - timedelta(hours=4):
            continue
        players = sorted((p for p in g["players"].values() if p["stats"]), key=lambda p: p["name"] or "")
        out_games.append({**g, "players": players})
    upcoming = [parse_dt(g["date"]) for g in out_games if parse_dt(g["date"]) > NOW]
    st["nextGame"] = iso(min(upcoming)) if upcoming else None
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "generatedAt": iso(NOW),
        "source": {k: st.get(k) for k in ("status", "lastRun", "used")} | {"limit": LIMIT},
        "books": sorted(st["cache"].get("books", {})),
        "stats": {k: v[1] for k, v in STATS.items()} | {"dd": "Double-double", "td": "Triple-double"},
        "games": out_games,
    }, ensure_ascii=False, separators=(",", ":")))
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, indent=1, ensure_ascii=False))
    n = sum(len(g["players"]) for g in out_games)
    log("→ %d matchs, %d joueurs avec props · %d/%d requêtes ce mois · %s" % (len(out_games), n, st["used"], LIMIT, st.get("status")))


if __name__ == "__main__":
    main()
