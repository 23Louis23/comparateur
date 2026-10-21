"""Compositions NBA (RotoWire, https://www.rotowire.com/basketball/nba-lineups.php) : cinq de
départ, statut de la composition, joueurs incertains/absents, arbitres, cote/écart/total.
Page du jour + page du lendemain, rendues côté serveur."""
import html as htmllib
import re

from common import get

URLS = ["https://www.rotowire.com/basketball/nba-lineups.php",
        "https://www.rotowire.com/basketball/nba-lineups.php?date=tomorrow"]
PLAYER = re.compile(r'<li class="lineup__player ([^"]*)"[^>]*>\s*<div class="lineup__pos"[^>]*>([^<]*)</div>\s*'
                    r'<a title="([^"]+)"[^>]*>[^<]*</a>\s*(?:<span class="lineup__inj">([^<]+)</span>)?', re.S)


def text(s):
    return htmllib.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def players(seg):
    out = []
    for cls, pos, name, inj in PLAYER.findall(seg):
        pct = re.search(r"is-pct-play-(\d+)", cls)
        out.append({"pos": pos.strip(), "name": htmllib.unescape(name).strip(),
                    "pct": int(pct.group(1)) if pct else None, "inj": (inj or "").strip() or None})
    return out


def side(block, which):
    m = re.search(r'<ul class="lineup__list is-%s">(.*?)</ul>' % which, block, re.S)
    if not m:
        return None
    seg = m.group(1)
    st = re.search(r'<li class="lineup__status ([a-z-]+)"[^>]*>(.*?)</li>', seg, re.S)
    starters, _, rest = seg.partition("MAY NOT PLAY")
    nick = re.search(r'class="lineup__mteam is-%s[^"]*">\s*([^<]+?)\s*<span class="lineup__wl">\(([^)]*)\)' % which, block)
    return {
        "nick": nick.group(1).strip() if nick else None,
        "record": nick.group(2) if nick else None,
        "status": "confirmed" if st and "confirm" in st.group(1) else "expected",
        "starters": players(starters),
        "out": [p for p in players(rest) if p["inj"]],
    }


def odds(block, label):
    m = re.search(r"<b>[\s&nbsp;]*%s</b>.*?<span class=\"composite[^\"]*\">([^<]+)</span>" % re.escape(label), block, re.S)
    v = text(m.group(1)) if m else ""
    return v if v and v not in ("–", "-") else None


def parse(page):
    games = []
    for block in page.split('class="lineup is-nba')[1:]:
        if 'lineup__list is-visit' not in block:
            continue
        away, home = side(block, "visit"), side(block, "home")
        if not away or not home or not away["nick"] or not home["nick"]:
            continue
        bottom = block[block.find("lineup__bottom"):]
        games.append({
            "away": away, "home": home,
            "time": text((re.search(r'class="lineup__time">([^<]*)<', block) or [None, ""])[1]),
            "refs": [text(r) for r in re.findall(r'ref\.php\?id=\d+">([^<]+)</a>', bottom)],
            "line": odds(bottom, "LINE"), "spread": odds(bottom, "SPREAD"), "total": odds(bottom, "O/U"),
        })
    return games


def fetch():
    games = []
    for url in URLS:
        games += parse(get(url))
    return games
