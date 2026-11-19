# Checklist de aceite do MVP

Escopo v1.3 rev. 2 (seção 10) + proposta v1.1 (seção 12). Evidências automatizadas em `tests/test_aceite.py`
(executadas em modo demonstração, com relógio controlado, contra PostgreSQL 16). Itens marcados "homologação"
só podem ser fechados com as fontes reais (trilha E0) na máquina do usuário.

| # | Critério | Evidência | Estado |
|---|---|---|---|
| 1 | Ação EUA (USD, Avenue), cripto (USD/USDT, Binance, quantidade fracionária) e ação B3 (BRL) com uma ou mais compras no mesmo dashboard, com preço médio, preço atual, P&L em valor e % | `test_criterio_1_*` | ✅ demo |
| 2 | Alternar USD/BRL com câmbio e horário visíveis; totais corretos nas duas moedas | `test_criterio_2_*` (US$ 2.200 = R$ 11.600) | ✅ demo |
| 3 | Detalhe de ativo em USD visto em BRL separa resultado do ativo e efeito cambial | `test_criterio_3_*`; `tests/test_fx.py` (R$ 2.180 = 2.120 + 60) | ✅ |
| 4 | Atualização automática com horário da última atualização e rótulo correto por mercado (mercado fechado / spot / USDT) | `test_criterio_4_*`; `tests/test_freshness.py` | ✅ demo — **homologação**: observar um pregão real |
| 5 | Falha das fontes: portal continua, marca desatualizado, registra o erro | `test_criterio_5_*`; `tests/test_collector.py` | ✅ (falha simulada) — **homologação**: 429 real controlado |
| 6 | Regra "queda ≥ 10% desde a compra" dispara e exibe regra e valores, na moeda nativa | `test_criterio_6_*`; `tests/test_alerts.py` | ✅ |
| 7 | Gráfico de histórico em pelo menos dois períodos | `test_criterio_7_*` (1M e 1A) | ✅ demo — **homologação**: histórico real da fonte |
| 8 | Ambiente sobe do zero pelo README | `test_criterio_8_*` (comandos); `docker compose config` validado; `collectstatic` validado | ⚠️ `docker compose up --build` deve ser executado na máquina do usuário (sem daemon Docker no ambiente de desenvolvimento) |
| 9 | Nenhuma tela exibe preço ou câmbio sem fonte e horário | `test_criterio_9_*`; inspeção visual (capturas da E4–E7) | ✅ |
| 10 | Compra sem câmbio em data sem PTAX usa dia útil anterior com rótulo; sem PTAX algum → "sem base cambial", fora dos totais, cobertura informada | `test_criterio_10_*`; `tests/test_fx.py` | ✅ |
| P1 | Consumo de créditos por ciclo dentro do orçamento | `tests/test_budget.py`; exposto em "Sobre os dados" | ✅ mecanismo — **homologação**: medir consumo real |
| P2 | Ativo em USD correto nas duas moedas com câmbio identificado | critérios 2 e 3 | ✅ |
| P3 | Cripto fracionária com estado spot/USDT | `test_proposta_cripto_*` | ✅ |
| P4 | Reinício do coletor sem duplicar cotações ou alertas | `test_proposta_reinicio_*`; `tests/test_collector.py::test_mesmo_horario_*` | ✅ |
| N | Alerta criado em linguagem simples; aviso entregue fora do portal (Telegram, macOS) uma única vez por alerta × canal, com texto obrigatório, fonte e horário; falha registrada e retentada | `tests/test_notify.py` (envio simulado com `httpx.MockTransport`; feed autenticado) | ✅ simulado — **homologação**: receber um aviso real no Telegram e no Mac |
| V | Carteira ao vivo (fragmento a cada 60 s, "Atualizar agora" força a coleta), resumo/evolução/alocação sem estimar dados (fechamentos × PTAX; lacunas declaradas), resumo diário e vigia por Telegram/macOS, importação CSV com pré-visualização | `tests/test_rc3.py` | ✅ simulado — **homologação**: observar 1 dia com Binance real |
| I | Alertas interativos: botões e comandos do Telegram só do chat autorizado, alerta pelo gráfico, painel ao vivo com ações rápidas; toda ação fica na trilha de versões | `tests/test_telegram_bot.py`, `tests/test_rc3.py` | ✅ simulado — **homologação**: clicar nos botões no Telegram real |
| X | Exploração interativa: filtros, expansão por ativo, comparação inline, simulador "E se" claramente rotulado como simulação (não altera dados nem cotações), prévia da regra | `tests/test_rc6.py`; verificação visual (desktop/celular) | ✅ |
| M | Mercado e Favoritos: rankings 1d–12m, indicadores só de candles com fonte, score 0–100 com critérios visíveis e sem reescalonar (só técnico ≤ 35, fora do Top 10), resumo com fato/consenso/projeção, dólar comercial × PTAX sem misturar séries, Focus, favoritos com grupos e análise por regras; coleta dentro de 8/min e 800/dia | `tests/test_rc7.py`; verificação visual (desktop/celular) | ✅ simulado — **homologação**: `homologar_fonte finnhub/alphavantage/focus_bcb` e observar o rodízio de candles por 1 dia |
| R8 | Abas Ações EUA (US$/R$ com efeito cambial, valuation, calendário, setor) e Ações Brasil (fundamentos, proventos, macro, sensibilidade ao dólar) e Comparar mercados (moeda comum, câmbio, proventos, volatilidade, queda máxima, fundamentos, potencial), com fonte/horário/atraso em cada cotação e plano da brapi respeitado | `tests/test_rc8.py`; verificação visual | ✅ simulado — **homologação**: brapi com token (`--fundamentos`), `sgs_bcb`, Finnhub `/quote` |
| B | Backup e restauração ensaiados | `scripts/backup.sh` + `scripts/restore.sh` em modo direto: contagens iguais antes/depois (29/10/2026) | ✅ direto — ⚠️ repetir via Compose na máquina do usuário |

## Pendências para fechar o MVP (trilha E0 + operação)

1. ~~Capturas reais~~ **Feito em 30/10/2026** para twelvedata, binance e ptax_bcb (fixtures reais em `reais-2026-10-30.json`; parser da Twelve Data ajustado — `timestamp` é o início do candle). Falta a captura formal da awesomeapi (em uso, respondendo 200) e brapi só se houver B3.
2. Observar um pregão dos EUA e 24 h de cripto com `PORTAL_MODO_DEMO=false`: rótulos, idade informada em 3 pontos, consumo de créditos, 429 simulado de forma controlada.
3. Ler os termos de cada fonte; marcar `FonteInfo.homologada=True` e preencher `docs/fontes-de-dados.md` (decisão por fonte).
4. Confirmar `apps/marketdata/calendars/feriados.json` com os calendários oficiais da NYSE e da B3.
5. `docker compose up --build -d` do zero + `scripts/backup.sh` / `restore.sh` via Compose.
6. Cadastrar a lista real de ativos e as compras (com o câmbio da remessa da Avenue quando houver).
7. Conferir o backup automático (`docker compose logs backup`; arquivo em `./backups/` no dia seguinte ou com `BACKUP_AO_INICIAR=true`) e ensaiar `scripts/restore.sh` com ele.
8. Ligar os avisos: bot do Telegram (`TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`) e agente do macOS (`PORTAL_FEED_TOKEN` + `scripts/mac/instalar.sh`); confirmar com `manage.py notificacoes --testar` e um alerta real.
9. Mercado (rc7): carregar a lista sugerida, acompanhar a carga de candles em Mercado → Universo, cadastrar `FINNHUB_API_KEY`/`ALPHAVANTAGE_API_KEY` (opcionais) e homologar `finnhub`, `alphavantage` e `focus_bcb` com captura real.
10. Corrigir a compra do BTC registrada como US$ 76,59 / câmbio 416,56 (o formulário agora recusa valores implausíveis e interpreta "76.590" como setenta e seis mil).
11. rc8: criar o token gratuito da brapi (BRAPI_API_KEY) para BBAS3, WEGE3, B3SA3, RENT3 e ABEV3; homologar `brapi --fundamentos` e `sgs_bcb`; decidir se o plano da brapi (3 meses de histórico, sem proventos no Gratuito) basta; se quiser cotações dos EUA mais frequentes, `ACOES_EUA_INTERVALO_MIN=5` ou `FONTE_COTACAO_EUA=finnhub` (homologar `finnhub TTWO` antes).
