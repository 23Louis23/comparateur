"""Outils partagés par les collecteurs de bookmakers (stdlib uniquement)."""
import gzip
import json
import re
import time
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime, timezone

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
PAUSE = 1.5  # secondes entre deux pages d'un même site : rythme d'un visiteur


class Blocked(Exception):
    """Le site refuse l'accès (protection anti-robot) : on n'insiste pas."""


def get(url, headers=None, data=None):
    time.sleep(PAUSE)
    req = urllib.request.Request(url, data=data, headers={
        "User-Agent": UA, "Accept-Language": "fr-FR,fr;q=0.9", "Accept-Encoding": "gzip", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
            return body.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code in (403, 429, 503):
            raise Blocked("%s → %s" % (url.split("/")[2], e.code))
        raise


def get_json(url, headers=None, data=None):
    return json.loads(get(url, {"Accept": "application/json", **(headers or {})}, data))


def price(v):
    """« 1,38 » / "1.38" / 1.38 → 1.38 (ou None)."""
    try:
        p = round(float(str(v).replace(",", ".")), 2)
        return p if p > 1.0 else None
    except (TypeError, ValueError):
        return None


def over_cut(line):
    """Plus de 24,5 → palier 25."""
    return int(float(str(line).replace(",", "."))) + 1


def norm_name(name):
    """Clé de rapprochement d'un joueur d'un bookmaker à l'autre."""
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z ]+", " ", s)
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\b", " ", s)
    return " ".join(s.split())


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now():
    return datetime.now(timezone.utc)


class Game:
    """Résultat normalisé d'un bookmaker pour un match."""

    def __init__(self, start, home, away):
        self.start, self.home, self.away = start, home, away
        self.players = {}  # nom affiché → {stat: {palier: cote}}

    def add(self, player, stat, cut, odds):
        odds = price(odds)
        if not player or odds is None or cut is None:
            return
        cell = self.players.setdefault(player.strip(), {}).setdefault(stat, {})
        cell[int(cut)] = max(odds, cell.get(int(cut), 0))  # même palier via deux marchés : la meilleure

    def count(self):
        return sum(len(c) for p in self.players.values() for c in p.values())
