# Coleta, calendários e atualidade

Implementação: `apps/marketdata/collector.py`, `calendars/`, `freshness.py`. Testes: `tests/test_collector.py`, `test_calendars.py`, `test_freshness.py`.

## Agenda (tarefas)

| Tarefa | Quando | O que grava |
|---|---|---|
| `NYSE` (cobre NYSE e Nasdaq) | A cada 15 min no pregão (9h30–16h New York; 10h30–17h ou 11h30–18h em São Paulo conforme o horário de verão americano) + 1 coleta de fechamento 10 min após o encerramento, uma vez por sessão | `Quote` |
| `B3` | Idem, 10h–17h São Paulo | `Quote` |
| `CRIPTO` | A cada **5 min**, 24/7 (`INTERVALO_POR_TAREFA`) | `Quote` (spot; moeda USDT ou BRL conforme o par) |
| `CAMBIO_INTRA` | Dias úteis, 9h–18h São Paulo, a cada 15 min | `FxRate` (intradiário) |
| `CAMBIO_PTAX` | Dias úteis, a partir de 13h30 São Paulo, tentativas a cada 60 min até obter | `FxDailyRate` (PTAX fechamento do dia) |

| `RESUMO` | Nos horários de `RESUMO_HORARIOS` (padrão 08:00 e 18:30, São Paulo), uma vez por horário/dia, se houver canal de aviso ligado | `Notification` (resumo diário; ver `docs/alertas.md`) |

O laço verifica a agenda a cada 30 s. **Atualizar agora** (botão na carteira, `POST /coletar-agora/`) marca as tarefas de cotação e câmbio intradiário (`ScheduleState.force_requested_at`) para rodar na próxima passagem, ignorando o intervalo mas respeitando calendário e orçamento. Ao fim de cada passagem rodam o resumo diário, o **vigia** (`apps/alerts/vigia.py`: 3 falhas seguidas numa tarefa ou tarefa ativa sem sucesso há mais de 45 min → aviso; normalização → aviso) e o envio das notificações pendentes.

Tarefas de mercado sem ativos cadastrados não rodam. Anos sem feriados em `calendars/feriados.json` deixam o calendário em estado "desconhecido": nada é coletado e os alertas ficam suspensos. **Feriados e horários precisam ser confirmados** com os calendários oficiais antes da operação (E8).

## Execução de um ciclo

1. Trava transacional por tarefa (`pg_advisory_xact_lock`) — dois coletores não rodam a mesma tarefa ao mesmo tempo.
2. Reserva de orçamento (`budget.reservar`): cota excedida → `CollectionRun` com status `pulado`, sem chamar a fonte.
3. Chamada ao adaptador com até 3 tentativas para erros retentáveis (rede/5xx: espera exponencial com jitter; 429: respeita `Retry-After`, máx. 60 s). Credencial inválida: falha imediata, sem repetir.
4. Persistência: uma `Quote` por cotação válida; **mesmo horário de mercado já gravado não gera novo registro** (a resposta repetida da fonte não "renova" a idade). Falhas por símbolo ficam em `CollectionRun.details`; a execução é `completo`, `parcial` ou `falha`.
5. `ScheduleState` guarda última tentativa, último sucesso e a sessão cujo fechamento já foi coletado.

Reinício do coletor é seguro: a agenda é lida do banco e cotações repetidas são ignoradas pela restrição única (ativo, fonte, horário de mercado).

## Estados de atualidade por ativo (`freshness.atualidade`)

| Estado | Condição | Alertas |
|---|---|---|
| `atual` | pregão aberto, fonte declara tempo real, coleta ≤ 17 min | sim |
| `atrasado` | pregão aberto, fonte declara atraso (ex.: ~30 min), coleta ≤ 17 min | sim |
| `nao_declarado` | pregão aberto, fonte não declara atraso, coleta ≤ 17 min | sim |
| `spot` | cripto, coleta ≤ 30 min | sim |
| `mercado_fechado` | bolsa fechada e a cotação corresponde ao fechamento da última sessão | sim |
| `desatualizado` | coleta vencida com pregão aberto; cripto > 30 min; ou fechamento da última sessão ainda não obtido | **não** |
| `desconhecido` | calendário sem cobertura | **não** |
| `sem_cotacao` | nunca houve cotação válida | **não** |

Cotações do adaptador de demonstração recebem o rótulo "demonstração" e a descrição "DEMONSTRAÇÃO — dado sintético".

## Comandos

```bash
manage.py coletar                       # laço: verifica a agenda a cada 30 s (--verificar N)
manage.py coletar --uma-vez             # uma passagem
manage.py coletar --tarefa CAMBIO_PTAX  # força uma tarefa (homologação)
manage.py coletar --agenda              # estado da agenda por tarefa (devida? motivo? último sucesso/erro)
manage.py carregar_historico --dias 30  # candles diários + PTAX; use --simbolo para dividir a carga por dia
```
