"""Alertes : cote anormale (un bookmaker nettement au-dessus des autres) et surebet sur une
ligne Plus/Moins, envoyées sur le téléphone via ntfy (https://ntfy.sh).

Le nom du canal ntfy est secret (quiconque le connaît reçoit les alertes) : il est rangé dans
~/Library/Application Support/CoteRadar/config.json, jamais dans le dépôt public."""
import json
import secrets
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

from common import iso, now

CONFIG = Path.home() / "Library/Application Support/CoteRadar/config.json"
SITE = "https://23louis23.github.io/comparateur/"
EDGE = 1.20          # meilleure cote ≥ 2e meilleure × 1,20 (elle sort seule du lot)
MIN_BOOKS = 3        # au moins 3 bookmakers sur la même sélection
MAX_ODDS = 4.0       # au-delà, les écarts entre bookmakers sont normaux
FRESH_MIN = 20       # seules les cotes lues en direct depuis moins de 20 min comptent
REPEAT_HOURS = 6     # pas de rappel pour la même alerte avant 6 h (sauf cote encore plus haute)
MAX_PUSH = 5         # au-delà, un seul message récapitulatif
BOOK_NAMES = {"unibet": "Unibet", "winamax": "Winamax", "pmu": "PMU", "betclic": "Betclic", "pokerstars": "PokerStars"}


def config():
    cfg = {}
    try:
        cfg = json.loads(CONFIG.read_text("utf-8"))
    except (OSError, ValueError):
        pass
    if not cfg.get("ntfy_topic"):
        cfg["ntfy_topic"] = "coteradar-" + secrets.token_hex(8)
        CONFIG.parent.mkdir(parents=True, exist_ok=True)
        CONFIG.write_text(json.dumps(cfg, indent=1))
    return cfg


def push(title, message, tags="basketball", priority=4):
    body = {"topic": config()["ntfy_topic"], "title": title, "message": message,
            "tags": [tags], "priority": priority, "click": SITE}
    req = urllib.request.Request("https://ntfy.sh/", data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15):
        pass


def fresh_books(g, local):
    limit = now() - timedelta(minutes=FRESH_MIN)
    return {b for b, t in (g.get("updated") or {}).items()
            if b in local and datetime.fromisoformat(t[:-1] + "+00:00") > limit}


def outlier(odds):
    """{book: cote} → (book, cote, 2e meilleure cote) si un bookmaker sort nettement du lot."""
    if len(odds) < MIN_BOOKS:
        return None
    book, best = max(odds.items(), key=lambda kv: kv[1])
    second = max(o for b, o in odds.items() if b != book)
    if best <= MAX_ODDS and best >= second * EDGE:
        return book, best, second
    return None


def find(games, labels, local):
    found = []
    for g in games:
        start = datetime.fromisoformat(g["date"][:-1] + "+00:00")
        if start < now():
            continue
        ok = fresh_books(g, local)
        when = start.astimezone().strftime("%H:%M")
        match = "%s @ %s, %s" % (g["away"].split()[-1], g["home"].split()[-1], when)
        for p in g["players"]:
            for stat, cuts in (p.get("stats") or {}).items():
                for cut, cell in cuts.items():
                    odds = {b: o for b, o in cell["o"].items() if b in ok and b not in cell.get("f", [])}
                    hit = outlier(odds)
                    if hit:
                        what = "%s %s+ %s" % (p["name"], cut, labels.get(stat, stat).lower()) if stat not in ("dd", "td") \
                            else "%s %s" % (p["name"], labels.get(stat, stat).lower())
                        found.append(("cote", (g["id"], p["id"], stat, "c" + cut), what, hit, odds, match))
            for stat, lines in (p.get("lines") or {}).items():
                for line, sides in lines.items():
                    over = {b: o for b, o in sides.get("over", {}).items() if b in ok}
                    under = {b: o for b, o in sides.get("under", {}).items() if b in ok}
                    for side, odds, sign in (("over", over, "+"), ("under", under, "−")):
                        hit = outlier(odds)
                        if hit:
                            what = "%s %s%s %s" % (p["name"], sign, line, labels.get(stat, stat).lower())
                            found.append(("cote", (g["id"], p["id"], stat, side + line), what, hit, odds, match))
                    if over and under:
                        bo, o = max(over.items(), key=lambda kv: kv[1])
                        bu, u = max(under.items(), key=lambda kv: kv[1])
                        if bo != bu and 1 / o + 1 / u < 1:
                            gain = (1 / (1 / o + 1 / u) - 1) * 100
                            what = "%s %s %s" % (p["name"], line, labels.get(stat, stat).lower())
                            found.append(("surebet", (g["id"], p["id"], stat, "sb" + line), what, (bo, o, bu, u, gain), None, match))
    return found


def run(games, labels, local, st):
    """Détecte, déduplique et envoie. st : état persistant (dict)."""
    sent = st.setdefault("alerts", {})
    limit = now() - timedelta(days=2)
    for k in [k for k, v in sent.items() if datetime.fromisoformat(v["t"][:-1] + "+00:00") < limit]:
        del sent[k]
    todo = []
    for kind, key, what, hit, odds, match in find(games, labels, local):
        k = "|".join(key)
        value = hit[1] if kind == "cote" else hit[4]
        prev = sent.get(k)
        if prev and value <= prev["v"] + (0.05 if kind == "cote" else 0.3) and \
                datetime.fromisoformat(prev["t"][:-1] + "+00:00") > now() - timedelta(hours=REPEAT_HOURS):
            continue
        sent[k] = {"v": value, "t": iso(now())}
        if kind == "cote":
            book, best, second = hit
            rest = ", ".join("%s %.2f" % (BOOK_NAMES.get(b, b), o) for b, o in sorted(odds.items(), key=lambda kv: -kv[1]) if b != book)
            todo.append(("Cote anormale · %s" % what,
                         "%s %.2f, soit +%d %% sur la 2e meilleure cote\nAilleurs : %s\n%s" % (
                             BOOK_NAMES.get(book, book), best, round((best / second - 1) * 100), rest, match), "rotating_light"))
        else:
            bo, o, bu, u, gain = hit
            todo.append(("Surebet +%.1f %% · %s" % (gain, what),
                         "Plus chez %s %.2f · Moins chez %s %.2f\n%s" % (BOOK_NAMES.get(bo, bo), o, BOOK_NAMES.get(bu, bu), u, match), "moneybag"))
    if not todo:
        return 0
    if len(todo) > MAX_PUSH:
        push("%d alertes CoteRadar" % len(todo), "\n".join("• " + t for t, _, _ in todo[:15]), "rotating_light")
    else:
        for title, msg, tag in todo:
            push(title, msg, tag)
    return len(todo)
