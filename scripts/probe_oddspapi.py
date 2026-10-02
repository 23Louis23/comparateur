#!/usr/bin/env python3
"""
Sonde ponctuelle : essaie les variantes d'adresses OddsPapi et enregistre
ce qui répond réellement (statut, clés, extrait) dans data/coverage/oddspapi-sonde.json.
La clé n'est jamais écrite dans le fichier.
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "coverage" / "oddspapi-sonde.json"
KEY = os.environ.get("ODDSPAPI_KEY")
BASE = "https://api.oddspapi.io/v4"
NOW = datetime.now(timezone.utc)
results = []


def probe(path, params):
    time.sleep(1.2)
    url = BASE + path + "?" + urllib.parse.urlencode({"apiKey": KEY, **params})
    entry = {"path": path, "params": params}
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "CoteRadar/1.0"}), timeout=30) as r:
            body = r.read().decode("utf-8", "replace")
            entry["status"] = r.status
            entry["headers"] = {k: v for k, v in r.headers.items() if "limit" in k.lower() or "remaining" in k.lower()}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        entry["status"] = e.code
    try:
        data = json.loads(body)
        entry["type"] = type(data).__name__
        if isinstance(data, list):
            entry["count"] = len(data)
            entry["first_keys"] = sorted(data[0].keys()) if data and isinstance(data[0], dict) else None
        elif isinstance(data, dict):
            entry["keys"] = sorted(data.keys())[:30]
    except ValueError:
        data = None
    entry["extrait"] = body[:2500]
    results.append(entry)
    print(path, params, "→", entry["status"], entry.get("type"), entry.get("count", ""), flush=True)
    return data


def first_fixture_id(data):
    items = data if isinstance(data, list) else (data or {}).get("data") or []
    for f in items:
        if isinstance(f, dict) and f.get("fixtureId"):
            return f["fixtureId"]
    return None


if KEY:
    state = json.loads((ROOT / "data" / "state.json").read_text("utf-8"))
    cache = state.get("oddspapi", {}).get("cache", {})
    slugs = ",".join(cache.get("books", {}).values()) or "bwin.fr,pokerstars.fr"
    l1 = next((t["id"] for t in cache.get("tournaments", []) if t["name"] == "Ligue 1"), None)
    t0, t1 = int(NOW.timestamp()), int((NOW + timedelta(days=7)).timestamp())

    a = probe("/fixtures", {"tournamentId": l1, "startTimeFrom": t0, "startTimeTo": t1})
    b = probe("/fixtures", {"sportId": 10, "from": NOW.strftime("%Y-%m-%d"),
                            "to": (NOW + timedelta(days=7)).strftime("%Y-%m-%d"), "hasOdds": "true"})
    fid = first_fixture_id(a) or first_fixture_id(b)
    if fid:
        probe("/fixtures/odds", {"fixtureId": fid, "bookmakers": slugs})
        probe("/odds", {"fixtureId": fid, "bookmakers": slugs})
        probe("/odds", {"fixtureId": fid})
    probe("/odds-by-tournaments", {"tournamentIds": l1, "bookmaker": "bwin.fr"})
    probe("/futures", {"sportId": 68})

    state.setdefault("oddspapi", {})["used"] = state["oddspapi"].get("used", 0) + len(results)
    (ROOT / "data" / "state.json").write_text(json.dumps(state, indent=1, ensure_ascii=False))
    OUT.write_text(json.dumps({"date": NOW.isoformat(), "fixture_teste": fid, "resultats": results},
                              indent=1, ensure_ascii=False))
