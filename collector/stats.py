"""Moyennes des 5 derniers matchs (ESPN, API publique du site espn.com).

- effectifs : site.api.espn.com/.../teams/{équipe}/roster → identifiant ESPN de chaque joueur
- journal   : site.web.api.espn.com/.../athletes/{id}/gamelog?season=AAAA → stats match par match

Les 5 derniers matchs officiels (saison régulière + playoffs, sans la pré-saison) ; en début de
saison, complétés par la fin de la saison précédente."""
import json
from datetime import datetime, timedelta

from common import get, iso, norm_name, now

ROSTER = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/%s/roster"
GAMELOG = "https://site.web.api.espn.com/apis/common/v3/sports/basketball/nba/athletes/%s/gamelog?season=%d"
ESPN_TEAM = {"ATL": "atl", "BOS": "bos", "BKN": "bkn", "CHA": "cha", "CHI": "chi", "CLE": "cle", "DAL": "dal",
             "DEN": "den", "DET": "det", "GSW": "gs", "HOU": "hou", "IND": "ind", "LAC": "lac", "LAL": "lal",
             "MEM": "mem", "MIA": "mia", "MIL": "mil", "MIN": "min", "NOP": "no", "NYK": "ny", "OKC": "okc",
             "ORL": "orl", "PHI": "phi", "PHX": "phx", "POR": "por", "SAC": "sac", "SAS": "sa", "TOR": "tor",
             "UTA": "utah", "WAS": "wsh"}
COLS = ["pts", "reb", "ast", "3pt", "stl", "blk", "tov", "min"]  # ordre des valeurs stockées par match
ROSTER_HOURS = 24
PLAYER_HOURS = 6
MAX_PER_RUN = 150  # journaux lus par passage (le reste au passage suivant)


def season_year(d=None):
    d = d or now()
    return d.year + 1 if d.month >= 9 else d.year  # saison 2026-27 → 2027 chez ESPN


def roster(abbr):
    data = json.loads(get(ROSTER % ESPN_TEAM[abbr]))
    return [(a["id"], a.get("fullName") or a.get("displayName")) for a in data.get("athletes", []) if a.get("id")]


def gamelog(pid, season):
    """→ [(date ISO, label saison, [pts, reb, ast, 3pm, stl, blk, tov, min])] du plus récent au plus ancien."""
    d = json.loads(get(GAMELOG % (pid, season)))
    names = d.get("names") or []
    idx = {n: i for i, n in enumerate(names)}
    events = d.get("events") or {}
    out = []
    for st in d.get("seasonTypes") or []:
        label = st.get("displayName") or ""
        if "Preseason" in label:
            continue
        for cat in st.get("categories") or []:
            for e in cat.get("events") or []:
                s = e.get("stats") or []
                try:
                    mins = float(s[idx["minutes"]])
                except (KeyError, ValueError, IndexError):
                    continue
                if mins <= 0:
                    continue  # n'a pas joué

                def num(name):
                    v = s[idx[name]]
                    return float(v.split("-")[0]) if "-" in v else float(v)
                row = [num("points"), num("totalRebounds"), num("assists"),
                       num("threePointFieldGoalsMade-threePointFieldGoalsAttempted"),
                       num("steals"), num("blocks"), num("turnovers"), mins]
                date = (events.get(e.get("eventId")) or {}).get("gameDate", "")
                out.append((date, label.split(" ")[0], row))
    return sorted(out, key=lambda x: x[0], reverse=True)


def last5(pid):
    cur = season_year()
    games = gamelog(pid, cur)
    if len(games) < 5:
        games += gamelog(pid, cur - 1)
    games = games[:5]
    label = "%d-%02d" % (cur - 1, cur % 100)  # « 2026-27 »
    return {"n": len(games), "prev": sum(1 for g in games if g[1] != label),
            "seasons": sorted({g[1] for g in games}), "dates": [g[0][:10] for g in games], "g": [g[2] for g in games]}


def run(published, cache):
    """published : dernier nba.json ; cache : dict persistant {rosters, players}. Met le cache à jour."""
    from teams import team
    rosters = cache.setdefault("rosters", {})
    players = cache.setdefault("players", {})
    wanted = {}  # nom normalisé → (abbr équipe candidate,)
    for g in published.get("games", []):
        start = datetime.fromisoformat(g["date"].replace("Z", "+00:00"))
        if start < now() - timedelta(hours=6):
            continue
        teams_ = [t[0] for t in (team(g["home"]), team(g["away"])) if t]
        for p in g.get("players", []):
            wanted.setdefault(p["id"], set()).update(teams_)
    abbrs = sorted({a for v in wanted.values() for a in v})
    for abbr in abbrs:  # effectifs : une fois par jour
        r = rosters.get(abbr)
        if not r or now() - datetime.fromisoformat(r["at"][:-1] + "+00:00") > timedelta(hours=ROSTER_HOURS):
            rosters[abbr] = {"at": iso(now()), "players": roster(abbr)}
    ids = {}
    for abbr in abbrs:
        for pid, name in rosters[abbr]["players"]:
            ids[norm_name(name)] = (pid, abbr, name)
    done = 0
    for key in sorted(wanted, key=lambda k: players.get(k, {}).get("at", "")):  # les plus anciens d'abord
        if key not in ids or done >= MAX_PER_RUN:
            continue
        pid, abbr, name = ids[key]
        old = players.get(key)
        if old and old.get("id") == pid and now() - datetime.fromisoformat(old["at"][:-1] + "+00:00") < timedelta(hours=PLAYER_HOURS):
            continue
        try:
            players[key] = {"id": pid, "team": abbr, "name": name, "at": iso(now()), **last5(pid)}
        except Exception:  # noqa: BLE001 — un joueur raté sera repris au passage suivant
            pass
        done += 1
    return done
