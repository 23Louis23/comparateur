"""Betclic : la page NBA liste les matchs (script #ng-state). Les props joueurs arrivent ensuite
par un flux binaire temps réel : on ouvre donc chaque match dans un Chromium invisible
(Playwright), on parcourt les onglets joueurs et on fait glisser chaque curseur de paliers
pour lire toutes les cotes affichées. Sans Playwright, repli sur la page brute (+/- points)."""
import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path

from common import Game, get, over_cut, now, price

NAME = "betclic"
SEPARATE = True  # navigateur : ~40-60 s par match → tâche à part toutes les 15 min (collect.py --only betclic)
BASE = "https://www.betclic.fr"
LIST = BASE + "/basketball-sbasketball/nba-c13"
TABS = ["Points", "Statistiques joueurs", "Marqueurs"]  # en plus de « Le Top », affiché d'office
STATS = [  # motif sur le titre du marché (le plus spécifique d'abord) → statistique
    (r"pts\s*\+\s*reb\s*\+\s*pas|points \+ rebonds \+ passes", "pra"), (r"pts\s*\+\s*reb|points \+ rebonds", "pr"),
    (r"pts\s*\+\s*pas|points \+ passes", "pa"), (r"reb\s*\+\s*pas|rebonds \+ passes", "ra"),
    (r"triple", "td"), (r"double", "dd"), (r"3 points|paniers? à 3", "3pt"), (r"points", "pts"),
    (r"rebonds", "reb"), (r"passes", "ast"), (r"interceptions", "stl"), (r"contres", "blk"),
]
APP = Path.home() / "Library/Application Support/CoteRadar"
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(APP / "browsers"))

# Exécuté dans la page : déplie les marchés, parcourt chaque curseur, renvoie le texte des cotes.
EXTRACT = """async () => {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  for (let i = 0; i < 3; i++) {
    const more = document.querySelectorAll('[data-qa=market-box] button.is-seeMore');
    if (!more.length) break;
    more.forEach(b => b.click()); await sleep(400);
  }
  const out = [];
  for (const box of document.querySelectorAll('[data-qa=market-box]')) {
    const title = box.querySelector('[data-qa=market-title]')?.textContent.trim() || '';
    if (!/joueur|double/i.test(title)) continue;
    const odds = btn => btn.querySelector('bcdk-bet-button-odds-animated')?.textContent.trim();
    const label = btn => btn.querySelector('.ellipsis, bc-button-label')?.textContent.trim();
    const sliders = box.querySelectorAll('sports-slider-value');
    if (sliders.length) {
      for (const row of sliders) {
        const name = row.querySelector('.marketBox_label')?.textContent.trim();
        const input = row.querySelector('input[type=range]');
        const read = () => { const b = row.querySelector('[data-qa=market-selection]'); if (b) out.push({title, name, label: label(b), odds: odds(b)}); };
        if (!input) { read(); continue; }
        for (let v = +input.min; v <= +input.max; v++) {
          input.value = v;
          input.dispatchEvent(new Event('input', {bubbles: true}));
          input.dispatchEvent(new Event('change', {bubbles: true}));
          await sleep(25); read();
        }
      }
    } else {
      for (const b of box.querySelectorAll('[data-qa=market-selection]')) {
        const line = b.closest('.marketBox_lineSelection, .marketBox_line, tr, li');
        out.push({title, name: line?.querySelector('.marketBox_label')?.textContent.trim() || null, label: label(b), odds: odds(b)});
      }
    }
  }
  return out;
}"""


def state(html):
    m = re.search(r'<script[^>]*id="ng-state"[^>]*>(.*?)</script>', html, re.S)
    return json.loads(m.group(1)) if m else {}


def payloads(st):
    for v in st.values():
        if isinstance(v, dict) and isinstance(v.get("response"), dict):
            p = v["response"].get("payload")
            if isinstance(p, dict):
                yield p


def matches(horizon_hours):
    html = get(LIST)
    links = {mid: path for path, mid in re.findall(r'href="(/basketball-sbasketball/nba-c13/[a-z0-9-]+-m(\d+))"', html)}
    for mt in (m for p in payloads(state(html)) for m in p.get("matches") or []):
        start = datetime.fromisoformat(mt["matchDateUtc"][:19] + "+00:00")
        if mt["matchId"] in links and now() < start < now() + timedelta(hours=horizon_hours):
            home, _, away = mt["name"].partition(" - ")
            yield start, home, away, BASE + links[mt["matchId"]]


def add_row(g, row):
    """{title, name, label, odds} → palier. Libellés : « + de 6,5 » (joueur à part) ou « Joueur + de 6,5 »."""
    stat = next((s for pat, s in STATS if re.search(pat, row.get("title") or "", re.I)), None)
    odds = price(row.get("odds"))
    if not stat or odds is None:
        return
    name, label = (row.get("name") or "").strip(), (row.get("label") or "").strip()
    text = label if name and label else (name or label)
    over = re.search(r"\+\s*de\s+([\d.,]+)$", text)
    mile = re.search(r"(\d+)\s*(?:\+|ou \+|ou plus)$", text)
    player = name if name and label else re.sub(r"\s*(\+|-)\s*de\s+[\d.,]+$|\s*\d+\s*(\+|ou \+|ou plus)$", "", text).strip()
    if stat in ("dd", "td"):
        if label.lower() in ("oui", "") or not label:
            g.add(name or text, stat, 1, odds)
    elif re.search(r"([+-])\s*de\s+([\d.,]+)$", text) and "palier" not in (row.get("title") or "").lower():
        sign, line = re.search(r"([+-])\s*de\s+([\d.,]+)$", text).groups()
        g.add_line(player, stat, line, "over" if sign == "+" else "under", odds)
        if sign == "+":
            g.add(player, stat, over_cut(line), odds)
    elif over:
        g.add(player, stat, over_cut(over.group(1)), odds)
    elif mile and not re.search(r"-\s*de", text):
        g.add(player, stat, int(mile.group(1)), odds)


def fetch_browser(horizon_hours):
    from playwright.sync_api import sync_playwright
    games = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(locale="fr-FR", viewport={"width": 1280, "height": 900})
        for start, home, away, url in matches(horizon_hours):
            g = Game(start, home, away)
            g.url = url
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            try:
                page.wait_for_selector("[data-qa=market-box]", timeout=20000)
            except Exception:  # noqa: BLE001 — match sans marchés affichés
                games.append(g)
                continue
            page.wait_for_timeout(1500)
            refuse = page.locator("#tc-privacy-wrapper button").filter(has_text=re.compile(r"refus|sans accepter", re.I))
            if refuse.count():  # bandeau cookies : on refuse les cookies non essentiels
                refuse.first.click(timeout=5000)
                page.wait_for_timeout(500)
            rows = page.evaluate(EXTRACT)
            for tab in TABS:
                clicked = page.evaluate("""t => { const e = [...document.querySelectorAll('.tab_label')]
                    .find(x => x.textContent.trim() === t); if (e) e.click(); return !!e; }""", tab)
                if clicked:
                    page.wait_for_timeout(1500)
                    rows += page.evaluate(EXTRACT)
            for row in rows:
                add_row(g, row)
            games.append(g)
            page.wait_for_timeout(1000)
        browser.close()
    return games


def fetch_page(horizon_hours):
    """Repli sans navigateur : marchés présents dans la page brute (surtout +/- points)."""
    games = []
    for start, home, away, url in matches(horizon_hours):
        g = Game(start, home, away)
        g.url = url
        for p in payloads(state(get(url))):
            for sc in (p.get("match") or {}).get("subCategories") or []:
                for mk in sc.get("markets") or []:
                    for row in mk.get("selectionMatrix") or []:
                        for sel in row.get("selections") or []:
                            s = (sel.get("selectionOneof") or {}).get("selection") or {}
                            if s.get("status") == 1:
                                add_row(g, {"title": mk.get("name") + " du joueur", "name": s.get("name"), "label": "", "odds": s.get("odds")})
        games.append(g)
    return games


def fetch(memo, horizon_hours=30):
    try:
        import playwright  # noqa: F401
    except ImportError:
        return fetch_page(horizon_hours)
    return fetch_browser(horizon_hours)
