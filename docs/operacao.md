# Operação

## Subir, parar, atualizar

```bash
docker compose up --build -d            # primeira vez e após atualizar o código
docker compose logs -f collector        # acompanhar a coleta
docker compose down                     # parar (dados preservados no volume pgdata)
```

Atualização de versão: `docker compose up --build -d` reaplica migrações antes de subir `web` e `collector`.

## Backup e restauração

```bash
scripts/backup.sh                       # backups/portal_acoes_<data>.dump; mantém os 7 mais recentes
scripts/restore.sh backups/arquivo.dump # pede confirmação; para web/collector, restaura e reinicia
```

**Backup automático**: o serviço `backup` do Compose (imagem `postgres:16`, `scripts/backup_loop.sh`) faz um `pg_dump` por dia às `BACKUP_HORA` (padrão 03:00, São Paulo) em `./backups/`, mantendo os `BACKUP_RETENCAO` (7) mais recentes; `BACKUP_AO_INICIAR=true` gera um ao subir. Acompanhe com `docker compose logs backup`.

Copie a pasta `backups/` para fora da máquina periodicamente. Meta: perda máxima de 24 h. Ensaie a restauração após qualquer mudança de versão do PostgreSQL. Modo sem Docker: `BACKUP_MODO=direto` com `DATABASE_URL`.

## Modo demonstração × fontes reais

- `PORTAL_MODO_DEMO=true` (padrão): dados sintéticos rotulados em todas as telas.
- `PORTAL_MODO_DEMO=false`: fontes reais (Twelve Data, Binance, PTAX, AwesomeAPI, brapi) — só após a homologação (`docs/homologacao/README.md`). Os bancos de demo e real devem ser separados (`POSTGRES_DB` diferente) para nunca misturar dados.

## Diagnóstico

| Sintoma | Onde olhar | Ação |
|---|---|---|
| Badge "desatualizado" com pregão aberto | Sobre os dados → Coletor / Últimas execuções | Erro de credencial → corrigir chave no `.env`; cota → aguardar ou alongar o intervalo; rede → ver logs |
| "estado desconhecido" | Calendários em Sobre os dados | Ano sem feriados em `feriados.json` → adicionar e confirmar |
| "sem base cambial" | Detalhe do ativo → Origem câmbio | Informar o câmbio da compra ou `manage.py carregar_historico --so-cambio --dias 365` |
| Alertas "suspensos" | Painel de alertas | Normal enquanto o dado não é confiável; voltam sozinhos |
| `/saude/` ≠ ok | `docker compose ps`, logs do `db` | Banco parado ou credenciais erradas |
| Carteira não atualiza sozinha | Indicador "atualizado há…" no topo; `docker compose logs collector` | A página recarrega a cada 60 s só com a aba visível; o coletor roda cripto a 5 min e bolsas a 15 min no pregão; "Atualizar agora" força a próxima passagem |
| Aviso "Coleta com problema" no Telegram | Sobre os dados → Últimas execuções; painel de alertas → Avisos | Vigia detectou 3 falhas seguidas ou tarefa parada > 45 min; corrige-se a causa (chave, cota, rede) e o aviso de normalização chega sozinho |
| Mercado sem histórico / "aguardando carga" | Mercado → Universo (coluna Histórico); `docker compose logs collector \| grep candles` | Normal nas primeiras horas: o rodízio carrega ~4 ativos dos EUA por minuto dentro da cota da Twelve Data; falha "autenticacao" = chave |
| Ação aparece "indisponível" (preço) | `docker compose exec web python manage.py diagnosticar B3` (ou `NYSE`, `CRIPTO`) | Mostra, por ativo, última cotação, candles e último erro, além da tarefa do coletor e do último ciclo (nunca imprime chaves). rc8.3: ativo incluído com o pregão fechado (feriado/fim de semana) recebe o fechamento na passagem seguinte; "Atualizar agora" refaz o fechamento; um ticker com erro não derruba os demais |
| Analistas/notícias "indisponível" | Selos no topo do Mercado | Falta `FINNHUB_API_KEY`/`ALPHAVANTAGE_API_KEY` no `.env`; depois `docker compose up -d` |
| Compra recusada "muito diferente do fechamento" | Formulário da compra | Checagem de digitação: o ponto separa milhar (76.590 = setenta e seis mil); vírgula é decimal; marque a confirmação se o valor estiver certo |
| Aviso não chegou no Telegram | Painel de alertas → Avisos fora do portal (estado/erro); `manage.py notificacoes --testar` | Token/chat id errados no `.env`; após corrigir, `docker compose up -d` e `manage.py notificacoes --reenviar` |
| Notificação do macOS não aparece | `~/Library/Logs/portal-acoes-notificar.log`; `launchctl print gui/$(id -u)/io.github.portal-acoes.notificar` | Token diferente do `.env`; portal parado; permitir notificações do `osascript` em Ajustes > Notificações |

## Acesso pela rede local

No `.env`: `PORTAL_BIND=0.0.0.0` e o IP da máquina em `DJANGO_ALLOWED_HOSTS` (ex.: `localhost,127.0.0.1,192.168.0.10`); depois `docker compose up -d`. A configuração fica no `.env`, então sobrevive às atualizações de versão. Para voltar a só esta máquina: `PORTAL_BIND=127.0.0.1`.

## Segurança mínima

Portal em `127.0.0.1:8000` (só a própria máquina) por padrão. Para acesso remoto: proxy HTTPS, `DJANGO_HTTPS=true`, `DJANGO_ALLOWED_HOSTS` e `DJANGO_CSRF_TRUSTED_ORIGINS` com o domínio, senha forte. Chaves de API, token do bot do Telegram e `PORTAL_FEED_TOKEN` só no `.env` (e no plist do launchd, permissão 600); nunca no repositório nem em capturas de tela.
