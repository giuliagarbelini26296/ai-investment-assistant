# Fontes de dados

Implementação: `apps/marketdata/adapters/`. Cada adaptador declara `FonteInfo` (fonte, tipo de dado, atraso declarado, limites, termos, atribuição, homologação), que alimenta a página "Sobre os dados" (E6).

| Fonte | Uso | Tipo / atraso declarado | Limites do plano gratuito | Estado |
|---|---|---|---|---|
| `brapi` | Ações B3 (BRL) — cotação v2, histórico (legado), estatísticas, dados financeiros e dividendos (v2) | Atrasado ~30 min no Gratuito (15 Startup, 5 Pro) | Gratuito: 15.000 req/mês, 1 ação/requisição, **1 requisição simultânea por conta** (429 passageiro: o portal espera 2 s e repete), histórico 3 meses (faixa maior → 403/400/422 → o portal cai para 3 meses), sem dividendos | Cotação v2 **validada com resposta real** (29/10/2026); 02/11/2026 com token: estatísticas, dados financeiros e dividendos **403 no Gratuito** (exigem Startup/Pro) |
| `twelvedata` | Ações EUA (USD) — `quote`, `time_series` | **Atraso não declarado** pela fonte (Basic) | 8 req/min, 800 créditos/dia | **Homologada 30/10/2026** (captura real TTWO) — ver nota abaixo |
| `binance` | Cripto — `ticker/24hr`, `klines` (pares USDT e BRL) | Spot da exchange, com `closeTime` | Limites por peso de requisição; 429/418 | **Homologada 30/10/2026** (captura real BTCUSDT/BTCBRL; idade informada 1,8 s) |
| `ptax_bcb` | Câmbio USD/BRL de fechamento oficial (OData) | Fechamento diário | Dados abertos; limites não informados | **Homologada 30/10/2026** (captura real; 249 datas carregadas) |
| `awesomeapi` | Câmbio USD/BRL intradiário | Não quantificado; cache 1 min sem chave | 100 req seq. sem chave; 1.500 com chave | Em uso desde 30/10/2026 (200 OK a cada 15 min); captura formal pendente |
| `finnhub` | Mercado: recomendações de analistas, preço-alvo, notícias por empresa e de câmbio, calendário de resultados, métricas | Dados de referência (não são cotações) | Gratuito: 60/min (portal usa até 50); preço-alvo pode exigir plano pago | Opcional (`FINNHUB_API_KEY`); **homologada 02/11/2026** (TTWO): recomendações, notícias, resultados e métricas 200; preço-alvo **403 no plano gratuito** |
| `alphavantage` | Mercado: ficha OVERVIEW (preço-alvo médio, contagem de recomendações, setor, P/L, margem, ROE) | Dados de referência | Gratuito: 25 req/dia (portal usa até 20) | Opcional (`ALPHAVANTAGE_API_KEY`); **não homologada** — 02/11/2026: chave aceita; **OVERVIEW é endpoint pago** (sem preço-alvo/ficha no gratuito — suspenso por 7 dias); limite de 1 requisição/s |
| `focus_bcb` | Dólar: expectativas Focus (mediana por ano) | Pesquisa semanal | Dados abertos | **Não homologada** — `homologar_fonte focus_bcb` |
| `sgs_bcb` | Macro: Selic meta (432), IPCA mensal (433) e 12 meses (13522) | Indicadores oficiais | Dados abertos | **Não homologada** — `homologar_fonte sgs_bcb` |
| `demo` / `demo_fx` | Dados sintéticos determinísticos | n/a | n/a | Ativo enquanto `PORTAL_MODO_DEMO=true` |

Regras aplicadas a toda resposta (`adapters/base.py`): preço positivo e finito; moeda esperada por mercado (USD para EUA, USDT/BRL para pares da Binance, BRL para B3); horário de mercado não futuro (tolerância 2 min); símbolo convertido pela fonte vira falha "revisar cadastro". HTTP 200 com corpo de erro (Twelve Data, Alpha Vantage) é tratado como erro, nunca como cotação. Falhas são por símbolo; uma resposta parcial preserva as cotações válidas e lista as ausentes.

Erros classificados para o coletor: `AuthError` (401/403, não repetir), `RateLimitError` (429/418, respeita `Retry-After`), `NetworkError` (timeout/5xx, retentável), `InvalidResponseError`, `SymbolNotFoundError`.

Orçamento (`budget.py`): reserva por minuto/dia/mês conforme `FonteInfo`; cota excedida → ciclo pulado e registrado, nunca estimativa.

## Homologação (trilha E0)

Na máquina com internet e chaves no `.env`:

```bash
docker compose exec web python manage.py homologar_fonte twelvedata TTWO AAPL --historico 30 --salvar docs/homologacao
docker compose exec web python manage.py homologar_fonte binance BTCUSDT BTCBRL --historico 30 --salvar docs/homologacao
docker compose exec web python manage.py homologar_fonte ptax_bcb --salvar docs/homologacao
docker compose exec web python manage.py homologar_fonte awesomeapi --salvar docs/homologacao
docker compose exec web python manage.py homologar_fonte brapi PETR4 --historico 30 --salvar docs/homologacao   # se houver B3
```

O comando imprime o resultado normalizado e grava a resposta bruta (sem chaves) para virar fixture em `tests/fixtures/sources/`. A "idade informada" (timestamp da fonte × relógio) **não comprova atraso real**; o rótulo do portal usa o atraso declarado pela fonte.

Checklist por fonte: formato confirmado → timestamp interpretado → idade informada medida em 3 pontos de um pregão → consumo de créditos observado → 429 simulado → termos lidos → decisão (aprovada / com restrição / substituída), registrada aqui e em `FonteInfo.homologada`.


## Resultado da homologação (30/10/2026, máquina do usuário)

| Fonte | Observado | Decisão |
|---|---|---|
| Binance | `ticker/24hr` com `lastPrice`, `closeTime` (idade informada 1,8–1,9 s), `priceChangePercent`; `klines` 1d com o candle do dia em andamento | **Aprovada** |
| Twelve Data | `/quote` com `close`, `previous_close`, `currency`, `is_market_open`; `/time_series` com 30 candles. **Achado**: `timestamp` = 1793367000 = 13:30Z = 09:30 New York com mercado já fechado → é o início do candle diário, não a última negociação. Usá-lo como horário de mercado faria as cotações do dia colidirem na deduplicação e rotularia o fechamento como "das 09:30". | **Aprovada com restrição**: horário de mercado fica "não informado"; atualidade usa horário da coleta + calendário; atraso segue "não declarado" |
| PTAX (BCB) | `cotacaoCompra` 5,4206 / `cotacaoVenda` 5,4212 / `dataHoraCotacao` 13:04:18 de 30/10 | **Aprovada** |
| AwesomeAPI | respostas 200 a cada 15 min no coletor real; captura formal não registrada | Em uso; formalizar |
| brapi (B3) | sem ativos B3 na carteira do usuário | Não exercitada |

Capturas brutas: `docs/homologacao/*-20261030-*.json` na máquina do usuário. Fixtures reais (campos usados) em `tests/fixtures/sources/reais-2026-10-30.json`. A primeira rodada falhou com `DecodingError` (respostas br/zstd via Cloudflare) — resolvido com `brotli`/`zstandard` e nova tentativa sem compressão (seção Transporte).

Pendências para fechar a E0 por completo: idade informada em 3 pontos de um pregão real e consumo de créditos observado (Twelve Data), 429 controlado, leitura formal dos termos de cada fonte.

## Transporte

Respostas comprimidas: o cliente HTTP aceita gzip, deflate, **br** e **zstd** (`brotli`, `zstandard` em `requirements.txt`). Se ainda assim a decodificação falhar, o pedido é repetido uma vez com `Accept-Encoding: identity`; falha persistente vira `NetworkError` com o nome do erro, visível em `homologar_fonte` e em Sobre os dados.
