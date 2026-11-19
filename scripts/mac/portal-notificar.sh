#!/bin/bash
# Exibe no macOS as notificações pendentes do Portal de Ações.
# Roda a cada minuto via launchd (instalar com scripts/mac/instalar.sh) ou manualmente.
# Depende só de curl e osascript (ambos vêm com o macOS). Nada de senha aqui: só o token do feed.
set -u
PORTAL_URL="${PORTAL_URL:-http://127.0.0.1:8000}"
TOKEN="${PORTAL_FEED_TOKEN:-}"
if [ -z "$TOKEN" ]; then
  echo "defina PORTAL_FEED_TOKEN (mesmo valor do .env do portal)" >&2
  exit 2
fi
resposta=$(curl -sS -m 10 -H "X-Portal-Token: $TOKEN" "$PORTAL_URL/alertas/feed/") || exit 0  # portal fora do ar: tenta no próximo minuto
[ -z "$resposta" ] && exit 0
ids=""
while IFS=$'\t' read -r id titulo mensagem; do
  [ -z "$id" ] && continue
  # escapa aspas e barras para o AppleScript
  t=${titulo//\\/\\\\}; t=${t//\"/\\\"}
  m=${mensagem//\\/\\\\}; m=${m//\"/\\\"}
  osascript -e "display notification \"$m\" with title \"Portal de Ações\" subtitle \"$t\" sound name \"Glass\"" \
    && ids="$ids${ids:+,}$id"
done <<< "$resposta"
if [ -n "$ids" ]; then
  curl -sS -m 10 -o /dev/null -X POST -H "X-Portal-Token: $TOKEN" -H "Content-Type: application/json" \
    -d "[$ids]" "$PORTAL_URL/alertas/feed/confirmar/"
fi
