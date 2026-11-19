#!/usr/bin/env bash
# Backup lógico do banco (formato custom do pg_dump) em backups/portal_acoes_<data>.dump.
# Modo padrão: via Docker Compose (serviço db). Modo direto: BACKUP_MODO=direto usa pg_dump local com DATABASE_URL.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; [ -f .env ] && . ./.env; set +a
mkdir -p backups
arquivo="backups/portal_acoes_$(date +%Y%m%d_%H%M%S).dump"
if [ "${BACKUP_MODO:-compose}" = "direto" ]; then
  : "${DATABASE_URL:?defina DATABASE_URL para o modo direto}"
  pg_dump --dbname="$DATABASE_URL" -Fc --no-owner > "$arquivo"
else
  docker compose exec -T db pg_dump -U "${POSTGRES_USER:-portal}" -d "${POSTGRES_DB:-portal_acoes}" -Fc --no-owner > "$arquivo"
fi
echo "backup gravado em $arquivo ($(du -h "$arquivo" | cut -f1))"
# Retenção: mantém os 7 mais recentes
ls -1t backups/portal_acoes_*.dump 2>/dev/null | tail -n +8 | xargs -r rm -f
