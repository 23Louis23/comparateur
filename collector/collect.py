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
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "books"))

import alerts  # noqa: E402
import betclic  # noqa: E402
import pmu  # noqa: E402
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


def add_fallback(board, covered):
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
        for p in fg.get("players", []):
            for stat, cuts in p.get("stats", {}).items():
                for cut, cell in cuts.items():
                    for b, odds in cell.get("o", {}).items():
                        if (g["id"], b, stat) not in covered:  # stat déjà lue en direct chez ce bookmaker
                            board.add(g, p["name"], stat, cut, b, odds, fallback=True)


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


def save_games(path, games):
    path.write_text(json.dumps([{"start": g.start.isoformat(), "home": g.home, "away": g.away, "players": g.players,
                                 "lines": g.lines} for g in games], ensure_ascii=False))


def load_games(path):
    games = []
    for d in load(path, []):
        g = Game(datetime.fromisoformat(d["start"]), d["home"], d["away"])
        g.players = {p: {s: {int(c): o for c, o in cuts.items()} for s, cuts in stats.items()} for p, stats in d["players"].items()}
        g.lines = d.get("lines", {})
        games.append(g)
    return games


def main():
    st = load(STATE, {})
    board = Board()
    status = {}
    BUILD.mkdir(exist_ok=True)
    for book in LOCAL_BOOKS:
        bs = st.setdefault(book.NAME, {})
        cache = BUILD / ("cache-%s.json" % book.NAME)
        try:
            interval = getattr(book, "INTERVAL_MIN", 0)  # bookmakers lents : relus moins souvent
            last = bs.get("lastOk")
            if interval and last and cache.exists() and now() - datetime.fromisoformat(last[:-1] + "+00:00") < timedelta(minutes=interval):
                games, at = load_games(cache), last
            else:
                games, at = book.fetch(bs.setdefault("memo", {})), iso(now())
                save_games(cache, games)
            n = 0
            for bg in games:
                g = board.game(bg.start, bg.home, bg.away)
                if not g:
                    continue
                g["updated"][book.NAME] = at
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
            status[book.NAME] = bs["status"] = "ok (%d cotes)" % n
            bs["lastOk"] = at
        except Blocked as e:
            status[book.NAME] = bs["status"] = "bloqué : %s" % e
        except Exception as e:  # noqa: BLE001 — un site en panne ne doit pas arrêter les autres
            status[book.NAME] = bs["status"] = "erreur : %s" % type(e).__name__
            traceback.print_exc()
        log(book.NAME, status[book.NAME])
    covered = {(g["id"], b, stat) for g in board.games.values() for p in g["players"].values()
               for stat, cuts in p["stats"].items() for cell in cuts.values() for b in cell["o"]}
    add_fallback(board, covered)

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
           "books": books, "stats": STAT_LABELS, "games": games}
    out_path.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    STATE.write_text(json.dumps(st, indent=1, ensure_ascii=False))
    log("%d matchs, %d joueurs" % (len(games), sum(len(g["players"]) for g in games)))
    if "--no-push" not in sys.argv:
        publish(out_path)
        log("publié sur la branche data")


if __name__ == "__main__":
    main()
