#!/bin/bash
# Installe (ou met à jour) la collecte automatique sur ce Mac.
# macOS interdit aux tâches de fond de lire ~/Documents : la collecte tourne donc depuis
# ~/Library/Application Support/CoteRadar, et ce script y recopie le code du projet.
#   Installer / mettre à jour :  bash collector/install.sh
#   Désinstaller :               bash collector/install.sh --remove
set -euo pipefail
LABEL=fr.coteradar.collector
DEST="$HOME/Library/Application Support/CoteRadar/collector"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
SRC="$(cd "$(dirname "$0")" && pwd)"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
if [[ "${1:-}" == "--remove" ]]; then
  launchctl bootout "gui/$(id -u)/fr.coteradar.betclic" 2>/dev/null || true
  launchctl bootout "gui/$(id -u)/fr.coteradar.stats" 2>/dev/null || true
  rm -f "$PLIST" "$HOME/Library/LaunchAgents/fr.coteradar.betclic.plist" "$HOME/Library/LaunchAgents/fr.coteradar.stats.plist"; echo "Collecte automatique désinstallée."; exit 0
fi

PY=/usr/bin/python3
[[ -x "$DEST/../venv/bin/python" ]] && PY="$DEST/../venv/bin/python"  # avec Playwright (Betclic complet)
mkdir -p "$DEST/books"
cp "$SRC"/*.py "$DEST/"; cp "$SRC"/books/*.py "$DEST/books/"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>$PY</string><string>$DEST/collect.py</string></array>
  <key>WorkingDirectory</key><string>$DEST</string>
  <key>StartInterval</key><integer>120</integer>
  <key>RunAtLoad</key><true/>
  <key>ProcessType</key><string>Background</string>
  <key>LowPriorityIO</key><true/>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>/usr/bin:/bin:/usr/sbin:/sbin</string></dict>
  <key>StandardOutPath</key><string>$HOME/Library/Logs/CoteRadar.log</string>
  <key>StandardErrorPath</key><string>$HOME/Library/Logs/CoteRadar.log</string>
</dict>
</plist>
PL
launchctl bootstrap "gui/$(id -u)" "$PLIST"

# Betclic (navigateur invisible, lent) : tâche à part toutes les 15 min, qui remplit un cache
B_LABEL=fr.coteradar.betclic
B_PLIST="$HOME/Library/LaunchAgents/$B_LABEL.plist"
launchctl bootout "gui/$(id -u)/$B_LABEL" 2>/dev/null || true
cat > "$B_PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$B_LABEL</string>
  <key>ProgramArguments</key>
  <array><string>$PY</string><string>$DEST/collect.py</string><string>--only</string><string>betclic</string></array>
  <key>WorkingDirectory</key><string>$DEST</string>
  <key>StartInterval</key><integer>900</integer>
  <key>RunAtLoad</key><true/>
  <key>ProcessType</key><string>Background</string>
  <key>LowPriorityIO</key><true/>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>/usr/bin:/bin:/usr/sbin:/sbin</string></dict>
  <key>StandardOutPath</key><string>$HOME/Library/Logs/CoteRadar.log</string>
  <key>StandardErrorPath</key><string>$HOME/Library/Logs/CoteRadar.log</string>
</dict>
</plist>
PL
launchctl bootstrap "gui/$(id -u)" "$B_PLIST"

# Stats ESPN (moyennes des 5 derniers matchs) : tâche à part toutes les 20 min
S_LABEL=fr.coteradar.stats
S_PLIST="$HOME/Library/LaunchAgents/$S_LABEL.plist"
launchctl bootout "gui/$(id -u)/$S_LABEL" 2>/dev/null || true
sed -e "s#$B_LABEL#$S_LABEL#" -e "s#<string>betclic</string>#<string>stats</string>#" \
    -e "s#<integer>900</integer>#<integer>1200</integer>#" "$B_PLIST" > "$S_PLIST"
launchctl bootstrap "gui/$(id -u)" "$S_PLIST"
echo "Collecte automatique installée (toutes les 2 min, Betclic toutes les 15 min en parallèle). Journal : ~/Library/Logs/CoteRadar.log"
