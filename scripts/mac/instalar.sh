#!/bin/bash
# Instala o agente launchd que consulta o portal a cada 60 s e mostra notificações no macOS.
# Uso: PORTAL_FEED_TOKEN=... [PORTAL_URL=http://127.0.0.1:8000] scripts/mac/instalar.sh
# Desinstalar: launchctl bootout gui/$(id -u)/io.github.portal-acoes.notificar && rm ~/Library/LaunchAgents/io.github.portal-acoes.notificar.plist
set -eu
: "${PORTAL_FEED_TOKEN:?defina PORTAL_FEED_TOKEN (mesmo valor do .env)}"
PORTAL_URL="${PORTAL_URL:-http://127.0.0.1:8000}"
DIR="$(cd "$(dirname "$0")" && pwd)"
ROTULO="io.github.portal-acoes.notificar"
PLIST="$HOME/Library/LaunchAgents/$ROTULO.plist"
chmod +x "$DIR/portal-notificar.sh"
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$ROTULO</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>$DIR/portal-notificar.sh</string></array>
  <key>EnvironmentVariables</key><dict>
    <key>PORTAL_URL</key><string>$PORTAL_URL</string>
    <key>PORTAL_FEED_TOKEN</key><string>$PORTAL_FEED_TOKEN</string>
  </dict>
  <key>StartInterval</key><integer>60</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$HOME/Library/Logs/portal-acoes-notificar.log</string>
  <key>StandardErrorPath</key><string>$HOME/Library/Logs/portal-acoes-notificar.log</string>
</dict></plist>
PL
chmod 600 "$PLIST"   # contém o token
launchctl bootout "gui/$(id -u)/$ROTULO" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Agente instalado: $PLIST (consulta $PORTAL_URL a cada 60 s). Log: ~/Library/Logs/portal-acoes-notificar.log"
echo "Se o macOS perguntar, permita notificações para 'Script Editor'/'osascript' em Ajustes > Notificações."
