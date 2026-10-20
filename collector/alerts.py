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


def odds_fr(x):
    return ("%.2f" % x).replace(".", ",")


def find(games, labels, local):
    """Liste de dicts : kind, key, player, cat (catégorie), short (« 6+ »), long (détail), hit, odds, match."""
    found = []
    for g in games:
        start = datetime.fromisoformat(g["date"][:-1] + "+00:00")
        if start < now():
            continue
        ok = fresh_books(g, local)
        match = "%s @ %s, %s" % (g["away"].split()[-1], g["home"].split()[-1], start.astimezone().strftime("%H:%M"))
        for p in g["players"]:
            for stat, cuts in (p.get("stats") or {}).items():
                cat = labels.get(stat, stat)
                for cut, cell in cuts.items():
                    odds = {b: o for b, o in cell["o"].items() if b in ok and b not in cell.get("f", [])}
                    hit = outlier(odds)
                    if hit:
                        yes = stat in ("dd", "td")
                        found.append(dict(kind="cote", key=(g["id"], p["id"], stat, "c" + cut), player=p["name"], cat=cat,
                                          short="Oui" if yes else cut + "+",
                                          long="réalise un %s" % cat.lower() if yes else "palier %s ou plus" % cut,
                                          hit=hit, odds=odds, match=match))
            for stat, lines in (p.get("lines") or {}).items():
                cat = labels.get(stat, stat)
                for line, sides in lines.items():
                    over = {b: o for b, o in sides.get("over", {}).items() if b in ok}
                    under = {b: o for b, o in sides.get("under", {}).items() if b in ok}
                    for side, odds, word in (("over", over, "Plus"), ("under", under, "Moins")):
                        hit = outlier(odds)
                        if hit:
                            found.append(dict(kind="cote", key=(g["id"], p["id"], stat, side + line), player=p["name"], cat=cat,
                                              short="%s %s" % (word, line.replace(".", ",")),
                                              long="ligne %s de %s" % (word, line.replace(".", ",")),
                                              hit=hit, odds=odds, match=match))
                    if over and under:
                        bo, o = max(over.items(), key=lambda kv: kv[1])
                        bu, u = max(under.items(), key=lambda kv: kv[1])
                        if bo != bu and 1 / o + 1 / u < 1:
                            gain = (1 / (1 / o + 1 / u) - 1) * 100
                            found.append(dict(kind="surebet", key=(g["id"], p["id"], stat, "sb" + line), player=p["name"], cat=cat,
                                              short=line.replace(".", ","), long="ligne %s" % line.replace(".", ","),
                                              hit=(bo, o, bu, u, gain), odds=None, match=match))
    return found


def run(games, labels, local, st):
    """Détecte, déduplique et envoie. st : état persistant (dict)."""
    sent = st.setdefault("alerts", {})
    limit = now() - timedelta(days=2)
    for k in [k for k, v in sent.items() if datetime.fromisoformat(v["t"][:-1] + "+00:00") < limit]:
        del sent[k]
    todo = []
    for f in find(games, labels, local):
        k = "|".join(f["key"])
        value = f["hit"][1] if f["kind"] == "cote" else f["hit"][4]
        prev = sent.get(k)
        if prev and value <= prev["v"] + (0.05 if f["kind"] == "cote" else 0.3) and \
                datetime.fromisoformat(prev["t"][:-1] + "+00:00") > now() - timedelta(hours=REPEAT_HOURS):
            continue
        sent[k] = {"v": value, "t": iso(now())}
        if f["kind"] == "cote":
            book, best, second = f["hit"]
            name = BOOK_NAMES.get(book, book)
            pct = round((best / second - 1) * 100)
            rest = " · ".join("%s %s" % (BOOK_NAMES.get(b, b), odds_fr(o)) for b, o in sorted(f["odds"].items(), key=lambda kv: -kv[1]) if b != book)
            title = "⚠️ %s · %s · %s %s" % (name, f["player"], f["cat"], f["short"])
            msg = "%s : %s, soit +%d %% sur la 2e meilleure cote\nCatégorie : %s, %s\nAutres bookmakers : %s\nMatch : %s" % (
                name, odds_fr(best), pct, f["cat"], f["long"], rest, f["match"])
            line = "%s · %s · %s %s : %s (+%d %%)" % (name, f["player"], f["cat"], f["short"], odds_fr(best), pct)
            todo.append((title, msg, "rotating_light", line))
        else:
            bo, o, bu, u, gain = f["hit"]
            title = "💰 Surebet +%s %% · %s · %s %s" % (("%.1f" % gain).replace(".", ","), f["player"], f["cat"], f["short"])
            msg = "Plus de %s chez %s : %s\nMoins de %s chez %s : %s\nCatégorie : %s, %s\nMatch : %s" % (
                f["short"], BOOK_NAMES.get(bo, bo), odds_fr(o), f["short"], BOOK_NAMES.get(bu, bu), odds_fr(u), f["cat"], f["long"], f["match"])
            line = "Surebet · %s · %s %s : Plus %s %s / Moins %s %s" % (
                f["player"], f["cat"], f["short"], BOOK_NAMES.get(bo, bo), odds_fr(o), BOOK_NAMES.get(bu, bu), odds_fr(u))
            todo.append((title, msg, "moneybag", line))
    if not todo:
        return 0
    if len(todo) > MAX_PUSH:
        push("%d alertes CoteRadar" % len(todo), "\n".join("• " + line for _, _, _, line in todo[:20]), "rotating_light")
    else:
        for title, msg, tag, _ in todo:
            push(title, msg, tag)
    return len(todo)
