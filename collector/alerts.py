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
EDGE = 1.30          # meilleure cote ≥ 2e meilleure × 1,30 (elle sort seule du lot)
MIN_BOOKS = 3        # au moins 3 bookmakers sur la même sélection
MAX_ODDS = 4.0       # au-delà, les écarts entre bookmakers sont normaux
FRESH_MIN = 20       # seules les cotes lues en direct depuis moins de 20 min comptent (Betclic : relu toutes les 15 min)
REPEAT_HOURS = 6     # pas de rappel pour la même alerte avant 6 h (sauf cote encore plus haute)
MAX_PUSH = 5         # au-delà, un seul message récapitulatif
BOOK_NAMES = {"unibet": "Unibet", "winamax": "Winamax", "pmu": "PMU", "betclic": "Betclic", "pokerstars": "PokerStars", "bet365": "bet365"}


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


# Écart ligne / moyenne des 5 derniers matchs
L5_REL = 0.30                     # moyenne ≥ 30 % au-dessus ou en dessous de la ligne…
L5_ABS = {"pts": 5, "reb": 2.5, "ast": 2.5, "3pt": 1.5, "pra": 6, "pr": 5, "pa": 5, "ra": 3}  # …et au moins cet écart
L5_MIN_GAMES = 3
L5_MAX_PREV = 2   # au moins 3 des 5 matchs doivent venir de la saison en cours (rôles qui changent d'une saison à l'autre)
L5_IDX = {"pts": [0], "reb": [1], "ast": [2], "3pt": [3], "pra": [0, 1, 2], "pr": [0, 1], "pa": [0, 2], "ra": [1, 2]}


def l5_values(l5, stat):
    return [sum(row[i] for i in L5_IDX[stat]) for row in l5["g"]]


def find_l5(games, labels, local):
    found = []
    for g in games:
        start = datetime.fromisoformat(g["date"][:-1] + "+00:00")
        if start < now():
            continue
        ok = fresh_books(g, local)
        match = "%s @ %s, %s" % (g["away"].split()[-1], g["home"].split()[-1], start.astimezone().strftime("%H:%M"))
        for p in g["players"]:
            l5 = p.get("l5")
            if not l5 or l5.get("n", 0) < L5_MIN_GAMES or l5.get("prev", 0) > L5_MAX_PREV or p.get("inj") in ("Out", "OFS", "Doubt"):
                continue
            for stat, lines in (p.get("lines") or {}).items():
                if stat not in L5_ABS:
                    continue
                # ligne principale : celle proposée par le plus de bookmakers frais
                def books(l):
                    return {b for side in ("over", "under") for b in lines[l].get(side, {}) if b in ok}
                cands = [l for l in lines if books(l)]
                if not cands:
                    continue
                line = max(cands, key=lambda l: (len(books(l)), -abs(float(l) - sum(l5_values(l5, stat)) / l5["n"])))
                vals = l5_values(l5, stat)
                avg, ln = sum(vals) / len(vals), float(line)
                if abs(avg - ln) >= L5_ABS[stat] and abs(avg - ln) >= L5_REL * ln:
                    over = {b: o for b, o in lines[line].get("over", {}).items() if b in ok}
                    under = {b: o for b, o in lines[line].get("under", {}).items() if b in ok}
                    found.append(dict(kind="l5", key=(g["id"], p["id"], stat, "l5"), player=p["name"],
                                      cat=labels.get(stat, stat), line=line, avg=avg, vals=vals, prev=l5.get("prev", 0),
                                      over=max(over.items(), key=lambda kv: kv[1]) if over else None,
                                      under=max(under.items(), key=lambda kv: kv[1]) if under else None, match=match))
    return found


def fmt1(x):
    return ("%.1f" % x).replace(".", ",").replace(",0", "")


# Écart de ligne entre bookmakers sur un même joueur
GAP_BALANCE = 0.35  # une ligne ne compte que si Plus et Moins sont proches chez ce bookmaker (ex. 1,75 / 1,95)
GAP_MIN = {"pts": 3, "pra": 3, "pa": 3, "pr": 3, "reb": 2, "ast": 2, "ra": 2, "3pt": 2, "stl": 2, "blk": 1, "sb": 1, "tov": 1}


def balanced_by_book(lines, ok):
    """{bookmaker: (ligne, cote Plus, cote Moins)} : pour chacun, la ligne où Plus et Moins sont les plus proches."""
    out = {}
    for line, sides in lines.items():
        for b, o in sides.get("over", {}).items():
            u = sides.get("under", {}).get(b)
            if b not in ok or u is None:
                continue
            if b not in out or abs(o - u) < abs(out[b][1] - out[b][2]):
                out[b] = (float(line), o, u)
    return {b: v for b, v in out.items() if abs(v[1] - v[2]) <= GAP_BALANCE}


def find_line_gaps(games, labels, local):
    found = []
    for g in games:
        start = datetime.fromisoformat(g["date"][:-1] + "+00:00")
        if start < now():
            continue
        ok = fresh_books(g, local)
        match = "%s @ %s, %s" % (g["away"].split()[-1], g["home"].split()[-1], start.astimezone().strftime("%H:%M"))
        for p in g["players"]:
            if p.get("inj") in ("Out", "OFS", "Doubt"):
                continue
            for stat, lines in (p.get("lines") or {}).items():
                if stat not in GAP_MIN:
                    continue
                bb = balanced_by_book(lines, ok)
                if len(bb) < 2:
                    continue
                lo_b = min(bb, key=lambda b: bb[b][0])
                hi_b = max(bb, key=lambda b: bb[b][0])
                gap = bb[hi_b][0] - bb[lo_b][0]
                if gap >= GAP_MIN[stat]:
                    found.append(dict(key=(g["id"], p["id"], stat, "gap"), player=p["name"], cat=labels.get(stat, stat),
                                      gap=gap, lo=(lo_b,) + bb[lo_b], hi=(hi_b,) + bb[hi_b], all=bb, match=match))
    return found


def run(games, labels, local, st):
    """Détecte, déduplique et envoie. st : état persistant (dict)."""
    sent = st.setdefault("alerts", {})
    limit = now() - timedelta(days=2)
    for k in [k for k, v in sent.items() if datetime.fromisoformat(v["t"][:-1] + "+00:00") < limit]:
        del sent[k]
    todo = []
    for f in find_l5(games, labels, local):  # écart ligne / moyenne : rappel seulement si la ligne bouge d'1 point
        k = "|".join(f["key"])
        prev = sent.get(k)
        if prev and abs(float(f["line"]) - prev["v"]) < 1 and \
                datetime.fromisoformat(prev["t"][:-1] + "+00:00") > now() - timedelta(hours=REPEAT_HOURS * 2):
            continue
        sent[k] = {"v": float(f["line"]), "t": iso(now())}
        line = f["line"].replace(".", ",")
        side = "au-dessus" if f["avg"] > float(f["line"]) else "en dessous"
        best = lambda x: "%s %s" % (BOOK_NAMES.get(x[0], x[0]), odds_fr(x[1])) if x else "–"
        title = "📊 %s · %s %s · moyenne %s" % (f["player"], f["cat"], line, fmt1(f["avg"]))
        msg = "Ligne : %s · Moyenne 5 derniers matchs : %s (%s de la ligne)\n5 derniers : %s%s\nMeilleur Plus : %s · Meilleur Moins : %s\nMatch : %s" % (
            line, fmt1(f["avg"]), side, " · ".join(fmt1(v) for v in f["vals"]),
            " (dont %d de la saison passée)" % f["prev"] if f["prev"] else "", best(f["over"]), best(f["under"]), f["match"])
        todo.append((title, msg, "bar_chart", "%s · %s %s : moyenne %s" % (f["player"], f["cat"], line, fmt1(f["avg"]))))
    for f in find_line_gaps(games, labels, local):  # écart de ligne : rappel si l'écart change d'1 ou après 12 h
        k = "|".join(f["key"])
        prev = sent.get(k)
        if prev and abs(f["gap"] - prev["v"]) < 1 and \
                datetime.fromisoformat(prev["t"][:-1] + "+00:00") > now() - timedelta(hours=REPEAT_HOURS * 2):
            continue
        sent[k] = {"v": f["gap"], "t": iso(now())}
        lo_b, lo, lo_o, lo_u = f["lo"]
        hi_b, hi, hi_o, hi_u = f["hi"]
        name = lambda b: BOOK_NAMES.get(b, b)
        ln = lambda x: ("%g" % x).replace(".", ",")
        title = "📏 %s · %s · écart de ligne %s" % (f["player"], f["cat"], ln(f["gap"]))
        msg = "Plus de %s chez %s : %s\nMoins de %s chez %s : %s\nToutes les lignes : %s\nMatch : %s" % (
            ln(lo), name(lo_b), odds_fr(lo_o), ln(hi), name(hi_b), odds_fr(hi_u),
            " · ".join("%s %s" % (name(b), ln(v[0])) for b, v in sorted(f["all"].items(), key=lambda kv: kv[1][0])), f["match"])
        todo.append((title, msg, "straight_ruler", "%s · %s : %s %s / %s %s" % (f["player"], f["cat"], name(lo_b), ln(lo), name(hi_b), ln(hi))))
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
            rest = " · ".join("%s %s" % (BOOK_NAMES.get(b, b), odds_fr(o)) for b, o in sorted(f["odds"].items(), key=lambda kv: -kv[1]) if b != book)
            title = "⚠️ %s · %s · %s %s" % (name, f["player"], f["cat"], f["short"])
            msg = "%s : %s\nCatégorie : %s, %s\nAutres bookmakers : %s\nMatch : %s" % (
                name, odds_fr(best), f["cat"], f["long"], rest, f["match"])
            line = "%s · %s · %s %s : %s" % (name, f["player"], f["cat"], f["short"], odds_fr(best))
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
