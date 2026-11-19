#!/usr/bin/env bash
# Restaura um backup sobre o banco. Apaga os dados atuais após confirmação (digite RESTAURAR).
# Modo padrão: via Docker Compose (para web/collector, restaura, reinicia). Modo direto: BACKUP_MODO=direto com DATABASE_URL.
# Para uso não interativo (ensaio automatizado): RESTAURAR_SEM_CONFIRMAR=1.
set -euo pipefail
cd "$(dirname "$0")/.."
[ $# -eq 1 ] || { echo "uso: scripts/restore.sh backups/arquivo.dump"; exit 1; }
[ -f "$1" ] || { echo "arquivo não encontrado: $1"; exit 1; }
set -a; [ -f .env ] && . ./.env; set +a
if [ "${RESTAURAR_SEM_CONFIRMAR:-0}" != "1" ]; then
  read -r -p "Isto substitui TODOS os dados do banco. Digite RESTAURAR para continuar: " ok
  [ "$ok" = "RESTAURAR" ] || { echo "cancelado"; exit 1; }
fi
if [ "${BACKUP_MODO:-compose}" = "direto" ]; then
  : "${DATABASE_URL:?defina DATABASE_URL para o modo direto}"
  pg_restore --dbname="$DATABASE_URL" --clean --if-exists --no-owner --exit-on-error "$1"
else
  docker compose stop web collector
  docker compose exec -T db pg_restore -U "${POSTGRES_USER:-portal}" -d "${POSTGRES_DB:-portal_acoes}" --clean --if-exists --no-owner --exit-on-error < "$1"
  docker compose start web collector
fi
echo "restauração concluída a partir de $1"
