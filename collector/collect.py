#!/usr/bin/env python3
"""
Collecteur local (iMac) : lit les props NBA directement chez les bookmakers,
fusionne matchs et joueurs, ajoute les bookmakers encore fournis par OddsPapi
(docs/nba.json, mis à jour par GitHub Actions) et publie nba.json sur la branche
« data » du dépôt, que le site lit directement.

Usage : python3 collector/collect.py [--no-push]
"""
import json
import subprocess
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "books"))

import alerts  # noqa: E402
import lineups  # noqa: E402
import betclic  # noqa: E402
import pmu  # noqa: E402
import stats  # noqa: E402
import pokerstars  # noqa: E402
import unibet  # noqa: E402
import winamax  # noqa: E402
from common import Blocked, Game, iso, norm_name, now  # noqa: E402
from teams import team  # noqa: E402

BUILD = HERE / "build"          # dépôt local de la branche « data » (ignoré par git)
STATE = HERE / "state.json"     # mémoire entre deux passages (ignoré par git)
REMOTE = "https://github.com/23Louis23/comparateur.git"
LOCAL_BOOKS = [unibet, winamax, pmu, pokerstars, betclic]
FALLBACK_URL = "https://raw.githubusercontent.com/23Louis23/comparateur/main/docs/nba.json"
ARROW_MINUTES = 60              # durée d'affichage d'une variation de cote
STAT_LABELS = {"pts": "Points", "reb": "Rebonds", "ast": "Passes", "3pt": "Paniers à 3 pts", "pra": "Pts + Reb + Pas",
               "pr": "Pts + Reb", "pa": "Pts + Pas", "ra": "Reb + Pas", "stl": "Interceptions", "blk": "Contres",
               "sb": "Int + Contres", "tov": "Balles perdues", "dd": "Double-double", "td": "Triple-double"}


def log(*a):
    print(datetime.now().strftime("%H:%M:%S"), *a, flush=True)


def load(path, default):
    try:
        return json.loads(Path(path).read_text("utf-8"))
    except (OSError, ValueError):
        return default


class Board:
    """Matchs fusionnés de tous les bookmakers."""

    def __init__(self):
        self.games = {}

    def game(self, start, home, away):
        th, ta = team(home), team(away)
        if not th or not ta:
            return None
        for g in self.games.values():  # même affiche à ±3 h (l'ordre domicile/extérieur peut varier)
            if {g["_h"], g["_a"]} == {th[0], ta[0]} and abs((datetime.fromisoformat(g["date"][:-1] + "+00:00") - start).total_seconds()) < 3 * 3600:
                return g
        gid = "%s-%s-%s" % (start.strftime("%Y%m%d"), ta[0], th[0])
        g = self.games[gid] = {"id": gid, "date": iso(start), "home": th[1], "away": ta[1],
                               "_h": th[0], "_a": ta[0], "players": {}, "updated": {}}
        return g

    @staticmethod
    def add_line(g, player, stat, line, side, book, odds):
        key = norm_name(player)
        p = g["players"].setdefault(key, {"id": key, "name": player, "stats": {}})
        cell = p.setdefault("lines", {}).setdefault(stat, {}).setdefault(line, {"over": {}, "under": {}})
        cell[side][book] = max(odds, cell[side].get(book, 0))

    @staticmethod
    def add(g, player, stat, cut, book, odds, fallback=False):
        key = norm_name(player)
        p = g["players"].setdefault(key, {"id": key, "name": player, "stats": {}})
        if any(ord(ch) > 127 for ch in player) and not any(ord(ch) > 127 for ch in p["name"]):
            p["name"] = player  # garder l'orthographe accentuée (Dončić, Jokić…)
        cell = p["stats"].setdefault(stat, {}).setdefault(str(cut), {"o": {}, "p": {}})
        cell["o"][book] = max(odds, cell["o"].get(book, 0))
        if fallback:  # cote OddsPapi (ancienne) : affichée, mais jamais source d'alerte
            cell.setdefault("f", []).append(book)


def add_fallback(board, covered, covered_lines=frozenset()):
    """Appoint OddsPapi pour tout ce qui n'a pas été lu en direct : (match, bookmaker, stat) absents de covered."""
    try:
        with urllib.request.urlopen(FALLBACK_URL + "?t=%d" % now().timestamp(), timeout=20) as r:
            data = json.load(r)
    except Exception as e:  # noqa: BLE001 — l'appoint est facultatif
        log("appoint OddsPapi indisponible :", type(e).__name__)
        return
    for fg in data.get("games", []):
        start = datetime.fromisoformat(fg["date"].replace("Z", "+00:00"))
        if start < now() - timedelta(hours=3):
            continue
        g = board.game(start, fg["home"], fg["away"])
        if not g:
            continue
        for b, t in (fg.get("updated") or {}).items():
            g["updated"].setdefault(b, t)  # l'heure de la collecte locale prime
        for b, url in (fg.get("links") or {}).items():
            g.setdefault("links", {}).setdefault(b, url)
        for p in fg.get("players", []):
            for stat, lines in (p.get("lines") or {}).items():
                for line, sides in lines.items():
                    for side, books in sides.items():
                        for b, odds in books.items():
                            if (g["id"], b, stat) not in covered_lines:  # lignes déjà lues en direct chez ce bookmaker
                                board.add_line(g, p["name"], stat, line, side, b, odds)
            for stat, cuts in p.get("stats", {}).items():
                for cut, cell in cuts.items():
                    for b, odds in cell.get("o", {}).items():
                        if (g["id"], b, stat) not in covered:  # stat déjà lue en direct chez ce bookmaker
                            board.add(g, p["name"], stat, cut, b, odds, fallback=True)


LINEUPS_EVERY_MIN = 10


def get_lineups():
    """Compositions RotoWire, relues toutes les 10 min (cache entre deux lectures)."""
    cache = BUILD / "cache-lineups.json"
    data = load(cache, {})
    at = data.get("at")
    if at and now() - datetime.fromisoformat(at[:-1] + "+00:00") < timedelta(minutes=LINEUPS_EVERY_MIN):
        return data.get("games", [])
    try:
        games = lineups.fetch()
        cache.write_text(json.dumps({"at": iso(now()), "games": games}, ensure_ascii=False))
        log("compositions ok (%d matchs)" % len(games))
        return games
    except Exception as e:  # noqa: BLE001 — sans compositions, le reste continue
        log("compositions : erreur", type(e).__name__)
        return data.get("games", [])


def attach_lineups(board, lus):
    """Rattache chaque composition au match correspondant (mêmes équipes, date la plus proche)
    et annote les joueurs : équipe, titulaire, statut de blessure."""
    for lu in lus:
        th, ta = team(lu["home"]["nick"]), team(lu["away"]["nick"])
        if not th or not ta:
            continue
        cands = [g for g in board.games.values() if {g["_h"], g["_a"]} == {th[0], ta[0]}
                 and datetime.fromisoformat(g["date"][:-1] + "+00:00") > now() - timedelta(hours=4)]
        if not cands:
            continue
        g = min(cands, key=lambda g: g["date"])
        home, away = (lu["home"], lu["away"]) if g["_h"] == th[0] else (lu["away"], lu["home"])
        g["lineup"] = {"home": home, "away": away, "refs": lu["refs"], "line": lu["line"],
                       "spread": lu["spread"], "total": lu["total"], "time": lu["time"]}
        for side_name, side in (("home", home), ("away", away)):
            for role, lst in (("starter", side["starters"]), ("out", side["out"])):
                for pl in lst:
                    p = g["players"].get(norm_name(pl["name"]))
                    if p:
                        p["team"] = side_name
                        if role == "starter":
                            p["starter"] = pl["pos"]
                        if pl["inj"]:
                            p["inj"] = pl["inj"]


def apply_history(board, previous):
    """Flèches ▲▼ : ancienne cote conservée ARROW_MINUTES après un changement."""
    prev = {(g["id"], p["id"], s, c): cell for g in previous.get("games", []) for p in g.get("players", [])
            for s, cuts in p.get("stats", {}).items() for c, cell in cuts.items()}
    limit = now() - timedelta(minutes=ARROW_MINUTES)
    for g in board.games.values():
        for p in g["players"].values():
            for s, cuts in p["stats"].items():
                for c, cell in cuts.items():
                    old = prev.get((g["id"], p["id"], s, c), {})
                    cell["t"] = {}
                    for b, o in cell["o"].items():
                        before, when = old.get("o", {}).get(b), old.get("t", {}).get(b)
                        if before is not None and before != o:
                            cell["p"][b], cell["t"][b] = before, iso(now())
                        elif b in old.get("p", {}) and when and datetime.fromisoformat(when[:-1] + "+00:00") > limit:
                            cell["p"][b], cell["t"][b] = old["p"][b], when


def publish(path):
    """Branche « data » : un seul commit, remplacé à chaque passage (pas d'historique qui grossit)."""
    if not (BUILD / ".git").exists():
        subprocess.run(["git", "init", "-q", "-b", "data", str(BUILD)], check=True)
        subprocess.run(["git", "-C", str(BUILD), "remote", "add", "origin", REMOTE], check=True)
    run = lambda *a: subprocess.run(["git", "-C", str(BUILD), *a], capture_output=True, text=True)
    run("add", "nba.json")
    run("-c", "user.name=coteradar-imac", "-c", "user.email=coteradar-imac@users.noreply.github.com",
        "commit", "-q", "--amend" if run("rev-parse", "HEAD").returncode == 0 else "--allow-empty", "-m", "Props NBA (iMac)")
    r = run("push", "-q", "-f", "origin", "HEAD:data")
    if r.returncode:
        raise RuntimeError("git push : " + r.stderr.strip()[:200])


def save_games(path, games, at):
    path.write_text(json.dumps({"at": at, "games": [
        {"start": g.start.isoformat(), "home": g.home, "away": g.away, "players": g.players, "lines": g.lines, "url": g.url}
        for g in games]}, ensure_ascii=False))


def load_games(path):
    """→ (matchs, heure de lecture) ; ([], None) si absent."""
    data = load(path, {})
    rows, at = (data, None) if isinstance(data, list) else (data.get("games", []), data.get("at"))
    games = []
    for d in rows:
        g = Game(datetime.fromisoformat(d["start"]), d["home"], d["away"])
        g.players = {p: {s: {int(c): o for c, o in cuts.items()} for s, cuts in stats.items()} for p, stats in d["players"].items()}
        g.lines = d.get("lines", {})
        g.url = d.get("url")
        games.append(g)
    return games, at


def stats_only():
    """Tâche séparée (toutes les 20 min) : moyennes des 5 derniers matchs (ESPN) des joueurs publiés."""
    BUILD.mkdir(exist_ok=True)
    path = BUILD / "cache-stats.json"
    cache = load(path, {})
    try:
        n = stats.run(load(BUILD / "nba.json", {}), cache)
        path.write_text(json.dumps(cache, ensure_ascii=False))
        log("stats ESPN : %d joueurs mis à jour (%d en cache)" % (n, len(cache.get("players", {}))))
    except Exception as e:  # noqa: BLE001
        path.write_text(json.dumps(cache, ensure_ascii=False))
        log("stats ESPN : erreur", type(e).__name__)
        traceback.print_exc()


def attach_stats(board):
    """Moyennes des 5 derniers matchs + équipe (effectif ESPN) de chaque joueur."""
    players = load(BUILD / "cache-stats.json", {}).get("players", {})
    for g in board.games.values():
        for key, p in g["players"].items():
            s = players.get(key)
            if not s or not s.get("n"):
                continue
            p["l5"] = {k: s[k] for k in ("n", "prev", "seasons", "dates", "g")}
            if not p.get("team") and s.get("team") in (g["_h"], g["_a"]):
                p["team"] = "home" if s["team"] == g["_h"] else "away"


def fetch_only(name):
    """Tâche séparée (Betclic, lent) : lit le bookmaker et range ses cotes en cache, sans publier."""
    book = next(b for b in LOCAL_BOOKS if b.NAME == name)
    BUILD.mkdir(exist_ok=True)
    cache = BUILD / ("cache-%s.json" % name)
    try:
        games = book.fetch({})
        kept, at = load_games(cache)
        if not any(g.count() for g in games) and any(g.count() for g in kept) \
                and now() - datetime.fromisoformat(at[:-1] + "+00:00") < timedelta(minutes=30):
            log(name, "lecture vide ignorée — on garde les %d cotes précédentes" % sum(g.count() for g in kept))
            return  # une lecture vide isolée n'efface pas le cache ; la suivante, si elle est vide aussi, fait foi
        save_games(cache, games, iso(now()))
        log(name, "lu (%d cotes) — en cache pour la collecte principale" % sum(g.count() for g in games))
    except Exception as e:  # noqa: BLE001
        log(name, "erreur :", type(e).__name__)
        traceback.print_exc()


def page_gaps(games):
    """Page « Écarts » du site : écarts de ligne et cotes anormales en cours sur les joueurs NBA, plus ceux
    laissés par les tâches basket européen (europe.py, handicaps NBA compris) et rugby (rugby.py),
    tant que le match n'a pas commencé."""
    gaps = {"nba": [], "europe": [], "rugby": [], "europeAt": None, "rugbyAt": None}
    local = {b.NAME for b in LOCAL_BOOKS}
    try:
        gaps["nba"] = alerts.gap_entries(games, STAT_LABELS, local) + alerts.odds_entries(games, STAT_LABELS, local)
    except Exception as e:  # noqa: BLE001 — la page des écarts ne doit pas bloquer la publication
        log("écarts NBA : erreur", type(e).__name__)
        traceback.print_exc()
    for job in ("europe", "rugby"):
        data = load(BUILD / ("gaps-%s.json" % job), {})
        if data.get("at") and now() - datetime.fromisoformat(data["at"][:-1] + "+00:00") < timedelta(minutes=40):
            gaps[job + "At"] = data["at"]
            for x in data.get("gaps", []):
                if datetime.fromisoformat(x["start"][:-1] + "+00:00") > now():
                    gaps[x.get("sport", job)].append(x)
    return gaps


def main():
    st = load(STATE, {})
    board = Board()
    status = {}
    BUILD.mkdir(exist_ok=True)
    # 1) lecture : bookmakers rapides en parallèle (un site différent par fil, même rythme pour chacun) ;
    #    bookmakers « à part » (Betclic) : dernières cotes laissées en cache par leur propre tâche
    def read(book):
        cache = BUILD / ("cache-%s.json" % book.NAME)
        if getattr(book, "SEPARATE", False):
            games, at = load_games(cache)
            if not at or now() - datetime.fromisoformat(at[:-1] + "+00:00") > timedelta(minutes=45):
                raise RuntimeError("cache absent ou trop ancien")
            return games, at
        games = book.fetch(st.setdefault(book.NAME, {}).setdefault("memo", {}))
        at = iso(now())
        save_games(cache, games, at)
        return games, at

    results = {}
    with ThreadPoolExecutor(max_workers=len(LOCAL_BOOKS)) as pool:
        futures = {pool.submit(read, b): b for b in LOCAL_BOOKS}
        for fut in futures:
            book = futures[fut]
            try:
                results[book.NAME] = fut.result()
            except Blocked as e:
                status[book.NAME] = "bloqué : %s" % e
            except Exception as e:  # noqa: BLE001 — un site en panne ne doit pas arrêter les autres
                status[book.NAME] = "erreur : %s" % (e if isinstance(e, RuntimeError) else type(e).__name__)
                if not isinstance(e, RuntimeError):
                    traceback.print_exc()

    # 2) fusion, dans un ordre fixe
    for book in LOCAL_BOOKS:
        bs = st.setdefault(book.NAME, {})
        if book.NAME in results:
            games, at = results[book.NAME]
            n = 0
            for bg in games:
                g = board.game(bg.start, bg.home, bg.away)
                if not g:
                    continue
                g["updated"][book.NAME] = at
                if bg.url:
                    g.setdefault("links", {})[book.NAME] = bg.url
                for player, stats in bg.lines.items():
                    for stat, lines in stats.items():
                        for line, sides in lines.items():
                            for side, odds in sides.items():
                                board.add_line(g, player, stat, line, side, book.NAME, odds)
                for player, stats in bg.players.items():
                    for stat, cuts in stats.items():
                        for cut, odds in cuts.items():
                            board.add(g, player, stat, cut, book.NAME, odds)
                            n += 1
            status[book.NAME] = "ok (%d cotes)" % n
            bs["lastOk"] = at
        bs["status"] = status[book.NAME]
        log(book.NAME, status[book.NAME])
    covered = {(g["id"], b, stat) for g in board.games.values() for p in g["players"].values()
               for stat, cuts in p["stats"].items() for cell in cuts.values() for b in cell["o"]}
    covered_lines = {(g["id"], b, stat) for g in board.games.values() for p in g["players"].values()
                     for stat, lines in (p.get("lines") or {}).items() for sides in lines.values()
                     for books in sides.values() for b in books}
    add_fallback(board, covered, covered_lines)
    attach_lineups(board, get_lineups())
    attach_stats(board)

    BUILD.mkdir(exist_ok=True)
    out_path = BUILD / "nba.json"
    apply_history(board, load(out_path, {}))
    games = []
    for g in sorted(board.games.values(), key=lambda g: g["date"]):
        players = sorted((p for p in g["players"].values() if p["stats"] or p.get("lines")), key=lambda p: p["name"])
        games.append({k: v for k, v in g.items() if not k.startswith("_")} | {"players": players})
    try:
        n = alerts.run(games, STAT_LABELS, {b.NAME for b in LOCAL_BOOKS}, st)
        if n:
            log("%d alerte(s) envoyée(s)" % n)
    except Exception as e:  # noqa: BLE001 — une alerte ratée ne doit pas bloquer la publication
        log("alertes : erreur", type(e).__name__)
    books = sorted({b for g in games for b in g["updated"]})
    out = {"generatedAt": iso(now()), "source": {"status": "ok", "lastRun": iso(now()), "collector": "imac", "books": status},
           "books": books, "stats": STAT_LABELS, "games": games, "gaps": page_gaps(games)}
    out_path.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    STATE.write_text(json.dumps(st, indent=1, ensure_ascii=False))
    log("%d matchs, %d joueurs" % (len(games), sum(len(g["players"]) for g in games)))
    if "--no-push" not in sys.argv:
        publish(out_path)
        log("publié sur la branche data")


if __name__ == "__main__":
    if "--only" in sys.argv and sys.argv[sys.argv.index("--only") + 1] == "stats":
        stats_only()
    elif "--only" in sys.argv:
        fetch_only(sys.argv[sys.argv.index("--only") + 1])
    else:
        main()
