"""Équipes NBA et rapprochement des noms d'équipe propres à chaque bookmaker."""
import unicodedata

TEAMS = [  # (abréviation, nom complet, mots qui l'identifient)
    ("ATL", "Atlanta Hawks", ["hawks", "atl"]),
    ("BOS", "Boston Celtics", ["celtics", "bos"]),
    ("BKN", "Brooklyn Nets", ["nets", "bkn", "brooklyn"]),
    ("CHA", "Charlotte Hornets", ["hornets", "cha"]),
    ("CHI", "Chicago Bulls", ["bulls", "chi"]),
    ("CLE", "Cleveland Cavaliers", ["cavaliers", "cavs", "cle"]),
    ("DAL", "Dallas Mavericks", ["mavericks", "mavs", "dal"]),
    ("DEN", "Denver Nuggets", ["nuggets", "den"]),
    ("DET", "Detroit Pistons", ["pistons", "det"]),
    ("GSW", "Golden State Warriors", ["warriors", "gsw", "golden"]),
    ("HOU", "Houston Rockets", ["rockets", "hou"]),
    ("IND", "Indiana Pacers", ["pacers", "ind"]),
    ("LAC", "LA Clippers", ["clippers", "lac"]),
    ("LAL", "Los Angeles Lakers", ["lakers", "lal"]),
    ("MEM", "Memphis Grizzlies", ["grizzlies", "mem"]),
    ("MIA", "Miami Heat", ["heat", "mia"]),
    ("MIL", "Milwaukee Bucks", ["bucks", "mil"]),
    ("MIN", "Minnesota Timberwolves", ["timberwolves", "twolves", "wolves", "min"]),
    ("NOP", "New Orleans Pelicans", ["pelicans", "nop", "orleans"]),
    ("NYK", "New York Knicks", ["knicks", "nyk"]),
    ("OKC", "Oklahoma City Thunder", ["thunder", "okc"]),
    ("ORL", "Orlando Magic", ["magic", "orl"]),
    ("PHI", "Philadelphia 76ers", ["76ers", "sixers", "phi"]),
    ("PHX", "Phoenix Suns", ["suns", "phx", "phoenix"]),
    ("POR", "Portland Trail Blazers", ["blazers", "tblazers", "por"]),
    ("SAC", "Sacramento Kings", ["kings", "sac"]),
    ("SAS", "San Antonio Spurs", ["spurs", "sas"]),
    ("TOR", "Toronto Raptors", ["raptors", "tor"]),
    ("UTA", "Utah Jazz", ["jazz", "uta", "utah"]),
    ("WAS", "Washington Wizards", ["wizards", "was", "wsh"]),
]


def team(name):
    """« DET Pistons », « Detroit », « Pistons de Détroit »… → ("DET", "Detroit Pistons") ou None."""
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    words = set(s.replace("-", " ").replace(".", " ").split())
    for abbr, full, keys in TEAMS:  # surnoms d'abord (plus sûrs que les abréviations)
        if any(k in words for k in keys if len(k) > 3):
            return abbr, full
    for abbr, full, keys in TEAMS:
        if any(k in words for k in keys):
            return abbr, full
    return None
