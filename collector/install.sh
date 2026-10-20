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
  rm -f "$PLIST"; echo "Collecte automatique désinstallée."; exit 0
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
echo "Collecte automatique installée (toutes les 2 min). Journal : ~/Library/Logs/CoteRadar.log"
