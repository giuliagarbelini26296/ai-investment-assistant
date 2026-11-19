#!/bin/sh
# Serviço "backup" do Compose: pg_dump diário às BACKUP_HORA (padrão 03:00, horário de São Paulo),
# retenção de BACKUP_RETENCAO arquivos (padrão 7), em /backups (pasta ./backups na sua máquina).
# Roda na imagem postgres:16; usa PGPASSWORD do ambiente (.env). Nunca apaga o banco.
set -u
: "${PGHOST:=db}"; : "${PGUSER:=portal}"; : "${PGDATABASE:=portal_acoes}"
: "${BACKUP_HORA:=03:00}"; : "${BACKUP_RETENCAO:=7}"; : "${TZ:=America/Sao_Paulo}"
export TZ
mkdir -p /backups
echo "backup automático: diário às $BACKUP_HORA ($TZ), retenção $BACKUP_RETENCAO, destino /backups"
if [ "${BACKUP_AO_INICIAR:-false}" = "true" ]; then rodar_agora=1; else rodar_agora=0; fi
while :; do
  if [ "$rodar_agora" = "1" ]; then
    rodar_agora=0
  else
    alvo_h=$(echo "$BACKUP_HORA" | cut -d: -f1); alvo_m=$(echo "$BACKUP_HORA" | cut -d: -f2)
    agora_s=$(( $(date +%-H) * 3600 + $(date +%-M) * 60 + $(date +%-S) ))
    alvo_s=$(( $(expr "$alvo_h" + 0) * 3600 + $(expr "$alvo_m" + 0) * 60 ))
    espera=$(( alvo_s - agora_s )); [ "$espera" -le 0 ] && espera=$(( espera + 86400 ))
    sleep "$espera"
  fi
  arquivo="/backups/portal_acoes_$(date +%Y%m%d_%H%M%S).dump"
  if pg_dump -Fc -f "$arquivo"; then
    echo "$(date '+%F %T') backup ok: $arquivo ($(du -h "$arquivo" | cut -f1))"
  else
    echo "$(date '+%F %T') backup FALHOU"; rm -f "$arquivo"
  fi
  ls -1t /backups/portal_acoes_*.dump 2>/dev/null | tail -n +$(( BACKUP_RETENCAO + 1 )) | while read -r velho; do rm -f "$velho"; echo "removido: $velho"; done
  sleep 60
done
