# Mercado e Favoritos (rc7) · Ações EUA, Ações Brasil e Comparar mercados (rc8)

Menu **Mercado** (Visão geral · Altas e quedas · Oportunidades · Dólar · Universo) e aba **⭐ Favoritos**. Tudo é calculado no portal a partir de dados com fonte e horário; nada é inventado nem extrapolado. Nenhuma tela recomenda compra ou venda: score, listas e alertas seguem regras fixas descritas aqui, e todas exibem o texto de não recomendação.

## Universo monitorado

- **Lista sugerida** (botão "Carregar lista sugerida", ou `Universo → recarregar`): 15 ações de tecnologia/Big Techs, 12 ETFs (índice amplo, setoriais, ouro, renda fixa, imobiliário) e 10 criptos. Cria também os grupos de favoritos *Big Techs*, *ETFs* e *Criptomoedas*.
- Ativos criados assim ficam como **"só acompanhamento"** (custódia "—"): não aparecem na lista padrão de Ativos nem na carteira; ao registrar a primeira compra, viram ativos da carteira automaticamente.
- **Universo**: incluir ticker (mercado, nome, setor, ETF) e remover (desativa; o histórico fica).
- Também entram no Mercado os ativos da carteira e qualquer favorito.

## Coleta (dentro das cotas dos planos gratuitos)

| Dado | Fonte | Frequência |
|---|---|---|
| Candles diários de ~400 dias (52 semanas + MM200) | Twelve Data (EUA), Binance (cripto) | carga inicial por rodízio, depois fechamento do dia |
| Preço de ações dos EUA **só acompanhadas** | último candle diário da Twelve Data (em andamento no pregão) | rodízio de ~1 h; rótulo "atualização ~1 h" |
| Preço da carteira | ciclo normal (15 min ações, 5 min cripto) | inalterado |
| Recomendações, preço-alvo, notícias, resultados, métricas | Finnhub (`FINNHUB_API_KEY`) | recomendações/métricas/alvo 24 h; notícias 3 h; resultados 24 h |
| Ficha da empresa, preço-alvo médio, contagem de recomendações | Alpha Vantage (`ALPHAVANTAGE_API_KEY`) | 1 ação por passagem, até 20/dia, cada ação 1×/semana |
| Dólar comercial (cotação, máx./mín. do dia, variação) | AwesomeAPI | 15 min, dias úteis 09h–18h |
| Histórico do dólar, variação 7/30 dias, tendência | PTAX (BCB) | diário |
| Expectativas para o dólar | Focus (BCB) | 1×/dia a partir das 9h |

Twelve Data Basic = 8 chamadas/min e 800/dia: o rodízio usa só a sobra do minuto depois do ciclo da carteira e para quando restam 120 créditos no dia. Com o universo sugerido (~27 ações/ETFs dos EUA) o consumo fica em torno de 350–450 créditos/dia. Se a carteira tiver mais de 8 ações dos EUA, o ciclo de 15 min agora divide os símbolos em lotes de 8 por minuto.

Sem chave Finnhub/Alpha Vantage, as seções de analistas, notícias, resultados e fundamentos mostram "indisponível — sem chave"; o resto funciona. As duas fontes estão **não homologadas** até a captura real:

```bash
docker compose exec web python manage.py homologar_fonte finnhub AAPL --salvar docs/homologacao
docker compose exec web python manage.py homologar_fonte alphavantage AAPL --salvar docs/homologacao
docker compose exec web python manage.py homologar_fonte focus_bcb --salvar docs/homologacao
```

Limitações conhecidas (confirmadas em 02/11/2026): preço-alvo/consenso na Twelve Data exige plano Ultra (US$ 999/mês); preço-alvo da Finnhub é pago (403); a ficha OVERVIEW da Alpha Vantage (preço-alvo, EV/EBITDA) passou a ser endpoint pago — **sem fonte gratuita de preço-alvo**, o critério A1 do score fica não avaliado e o máximo avaliável cai para 75 (consenso + fundamentos da Finnhub continuam); **movimentações de investidores institucionais não têm fonte gratuita** e não são exibidas; notícias de câmbio são gerais (em inglês), não específicas do real; o portal **não classifica sentimento** de notícias.

## Indicadores (`apps/mercado/indicadores.py`)

Sempre de candles diários de uma única fonte por ativo (a oficial do mercado quando houver), com o preço atual no lugar do candle em andamento.

- **Variação por período** (1d, 7d, 1m, 3m, 6m, 12m): preço atual vs. fechamento da última sessão em ou antes de (hoje − N dias). 1 dia usa a variação informada pela fonte (fechamento anterior para ações, 24 h para cripto). Sem histórico até a data-base → "—".
- **Máxima/mínima do período** e **volume médio diário** (unidades e ≈ financeiro = volume × fechamento, aproximação).
- **52 semanas**: candles dos últimos 365 dias + preço atual; com menos de 300 dias a janela é indicada.
- **RSI(14)** de Wilder; **MACD(12, 26, 9)**; **MM50/MM200** simples.
- **Suporte/resistência de N sessões**: mínima/máxima das N sessões completas anteriores (sem a de hoje).
- **Tendência**: alta = preço > MM50 > MM200; baixa = preço < MM50 < MM200; senão lateral.
- **Volatilidade**: desvio-padrão dos retornos log diários, anualizado (252; 365 para cripto).

## Score de atratividade 0–100 (`apps/mercado/score.py`)

| Grupo | Critério | Pontos |
|---|---|---|
| Técnico (35) | T1 preço até 5% da mínima de 52 semanas = 12; 0 a partir de 40%; linear (exige 6 meses) | 12 |
| | T2 RSI ≤ 30 = 8; ≤ 40 = 5; ≤ 55 = 2 | 8 |
| | T3 preço ≥ MM200 = 8; MACD histograma > 0 = 7, negativo subindo = 4 | 15 |
| Analistas (40) | A1 potencial até o preço-alvo médio: 25 a partir de +30%; 0 se ≤ 0 | 25 |
| | A2 consenso: 15 com ≥ 85% compra; 0 com ≤ 40%; −5 se ≥ 20% venda; mínimo 5 analistas | 15 |
| Fundamentos (25) | F1 margem líquida > 10% = 8; > 0 = 5 | 8 |
| | F2 ROE ≥ 20% = 8; ≥ 10% = 5; > 0 = 2 | 8 |
| | F3 P/L até 20 = 9; até 30 = 6; até 45 = 3 | 9 |

- Critério sem dado vale 0 e **o total não é reescalonado**; a tela mostra o máximo avaliável. Só leitura técnica → no máximo 35 → **fora do Top 10**. É assim que "queda isolada" nunca vira oportunidade.
- **Risco**: volatilidade de 90 sessões < 25% baixo, < 45% médio, senão alto; sobe um nível com resultado em ≤ 7 dias, tendência de baixa, queda ≥ 15% na semana, ≥ 30% de recomendações de venda ou volatilidade recente > 1,5× a de 6 meses.
- **Perspectiva**: "médio/longo prazo" quando analistas/fundamentos pesaram mais (preço-alvo tem horizonte típico de 12 meses); "curto prazo" quando pesaram os sinais técnicos.
- **Top 10**: maiores scores entre os ativos com algo além da leitura técnica avaliado. Colunas: preço atual, queda acumulada (1 mês, 3 meses, distância da máxima de 52 semanas), preço-alvo e potencial, atratividade, risco, perspectiva.

## Resumo executivo

Frases geradas por regras e rotuladas: **Fato** (dado observado), **Consenso** (opinião agregada de analistas), **Projeção** (preço-alvo, Focus), **Risco** (o que pode impedir a valorização). Cada uma com fonte e data. Setores = média simples da variação de 1 mês dos ativos do universo por setor do cadastro (não é índice setorial).

**rc9 — formato estruturado** (`apps/mercado/resumo.py`, `_resumo_executivo.html`). Duas colunas: à esquerda o que vem dos dados e das regras do portal (Fato e Risco), à direita a opinião de terceiros (Consenso e Projeção). No topo, por mercado: situação do pregão, fonte, horário da cotação mais recente e atraso declarado pela fonte.

| Bloco | Conteúdo | Fonte |
|---|---|---|
| Fato | amplitude do dia (barra alta × queda), 5 maiores altas e quedas com preço, perto da mínima de 52 semanas (preço, mínima, distância), setores mais fortes/fracos com os ativos de cada um, tendência de alta (% até a MM200), **Conferir o dado** (ação/ETF com ≥ 15% num pregão ou ≥ 25% em 1 mês — pode ser evento real, desdobramento, grupamento, bonificação ou falha da fonte; o portal não corrige) | cotações e candles |
| Consenso | distribuição compra forte / compra / manter / venda / venda forte, % de compra, nº de analistas, variação contra o mês anterior; seção "Na sua carteira e favoritos"; **mudanças por instituição** (data, instituição, de → para) | Finnhub `/stock/recommendation` (só totais, sem nomes) e `/stock/upgrade-downgrade` (com o nome da instituição — pode ser pago: 403 suspende a consulta por 7 dias para todos os ativos) |
| Projeção | preço-alvo (ou o motivo de não haver), Boletim Focus (Selic, IPCA, dólar — mediana, faixa e nº de instituições, ano corrente e seguinte), referência oficial atual (SGS) | Focus/BCB, SGS/BCB |
| Risco | tendência de baixa (% até a MM200), risco alto (volatilidade de 90 sessões e o sinal que elevou o nível), resultados nos próximos 14 dias (data, antes/depois do pregão, lucro por ação estimado) | candles, Finnhub |

Os selos no topo do Mercado mostram o estado real de cada fonte de inteligência: **ok**, **parcial** (ex.: Finnhub sem preço-alvo), **pago** (endpoint recusado pelo plano), **falha** (com o erro no detalhe), **sem chave** ou **aguardando**, com o horário da última atualização.

## Favoritos

Estrela ☆ em Carteira, Mercado, rankings, oportunidades, universo e no detalhe do ativo. Favoritos ficam no banco (persistem), com **grupos** (um favorito pode estar em vários), ordem manual (↑ ↓), filtros de período, comparação em base 100 de até 6 favoritos e atalho de alerta.

**Analisar meus favoritos** — cinco perguntas, cada uma com regra fixa:

1. Perto de possíveis pontos de entrada: até 3% acima do suporte de 20 sessões ou até 10% da mínima de 52 semanas, com RSI ≤ 40.
2. Maior potencial: distância até o preço-alvo médio; sem alvo, score ≥ 50 com analistas/fundamentos.
3. Tendência de alta/baixa pelas médias.
4. Mudanças relevantes: recomendações ±10 p.p. no mês, resultado em 14 dias, LPA divulgado vs. estimado, 3+ notícias em 48 h, movimento semanal acima de ¼ da volatilidade anual (mín. 8%).
5. Exigem atenção: risco alto pela régua ou rompimento do suporte de 20 sessões.

## Dólar

Cotação comercial e máx./mín. do dia (AwesomeAPI, horário de referência informado pela fonte); variação de 7 e 30 dias, gráfico (1M a 2A) e tendência técnica (MM20 vs. MM60 do PTAX, ±0,5%); expectativas Focus (mediana, média, faixa e respondentes por ano, e evolução da mediana do ano corrente); notícias gerais de câmbio (Finnhub). O dólar também é um "ativo" (USDBRL) para alertas e favoritos.

## Alertas de mercado

Novos tipos no formulário de alerta (para um ativo específico): perto da mínima de 52 semanas (X%), rompe suporte / resistência (X sessões), resultado em até X dias, mudança de recomendação ≥ X p.p.; e para o dólar, as regras de variação no dia e preço. Atalho **"Bitcoin abaixo de US$ 77.500"**. Detalhes em `docs/alertas.md`.


## Ações EUA 🇺🇸 (rc8)

Aba só com ações da NYSE/Nasdaq (sem ETFs). Ativos iniciais (botão "Incluir agora"): GOOGL, MSFT, NVDA, AAPL, AMZN, META, TSLA, PLTR — mais o que estiver na carteira (ex.: TTWO, ORCL, CHKP) e no universo.

- Altas e quedas por período (1 dia a 12 meses), busca por empresa ou ticker, ordenação por coluna.
- **US$ ou R$**: em R$ o preço usa o câmbio em uso no portal; o retorno do período combina o retorno em US$ com a variação do câmbio (PTAX da data-base → câmbio atual) e mostra a parte do ativo e a do câmbio. "1 dia" fica em US$.
- Perto da mínima de 52 semanas (até 10%), preço-alvo e recomendações (Finnhub/Alpha Vantage), P/L, EV/EBITDA e crescimento da receita (trimestre vs. mesmo trimestre do ano anterior), calendário de resultados e de dividendos, notícias, comparação no mesmo setor (com medianas), estrela de favorito.

## Ações Brasil 🇧🇷 (rc8)

Ativos iniciais: PETR4, VALE3, ITUB4, BBAS3, WEGE3, B3SA3, RENT3, ABEV3. Fonte: **brapi**.

| brapi (02/11/2026) | Gratuito | Startup (R$ 99,99/mês) | Pro (R$ 116,66/mês no anual) |
|---|---|---|---|
| Ações por requisição | 1 | 10 | 20 |
| Histórico | 3 meses | 1 ano | 10+ anos |
| Dividendos/JCP | não | até 1 ano | completos |
| Atualização | 30 min | 15 min | 5 min |
| Sem token | só PETR4, VALE3, ITUB4, MGLU3 | — | — |

- **Confirmado em 02/11/2026** (chamada real com token): no Gratuito, estatísticas, dados financeiros e dividendos respondem 403 — P/L, P/VP, ROE, dívida líquida/EBITDA, preço-alvo e proventos da B3 só com Startup/Pro. Cotações funcionam.
- No Gratuito: rankings até 3 meses; **6 e 12 meses e mínima de 52 semanas ficam "histórico curto"** até o portal acumular os próprios candles (ele guarda um por dia) ou com plano maior. Ajuste `BRAPI_SIMBOLOS_POR_REQUISICAO` e `BRAPI_INTERVALO_MIN` ao plano contratado.
- Fundamentos: P/L, P/VP, EV/EBITDA e dividend yield (`/api/v2/stocks/statistics`), ROE, crescimento da receita, preço-alvo e recomendação média (`/financial-data`). **Dívida líquida/EBITDA é calculada pelo portal** = (dívida total − caixa) / EBITDA, marcada com *. Recurso fora do plano (403) fica "indisponível" e só é tentado de novo após 7 dias.
- **Proventos** (`/dividends`): histórico de dividendos e JCP (valores brutos por ação, data-com e pagamento) e datas futuras no calendário; DY 12m = soma dos proventos com data-com em 12 meses ÷ preço atual.
- Recomendação média (1 = compra forte … 5 = venda), quando a fonte não dá contagens, vale no critério A2 do score: 15 pontos com ≤ 1,5, 0 com ≥ 3.
- **Macro**: Selic meta (SGS 432), IPCA 12 meses (13522) e no mês (433), juro real ex-post = (1 + Selic)/(1 + IPCA 12m) − 1, dólar e Focus (Selic, IPCA, câmbio para o fim do ano corrente e do próximo).
- **Sensibilidade ao dólar** por ação: β e correlação dos retornos diários com os do PTAX (até 250 pregões, mínimo 40). Medida do passado, não previsão. Selic e IPCA mudam pouco no dia a dia, por isso o portal não estima sensibilidade a eles.
- Sem fonte gratuita confirmada para **agenda de resultados** e **notícias** da B3: as seções dizem isso.

## Comparar mercados (rc8)

Até 8 ativos de qualquer mercado (EUA, B3, cripto) em **moeda comum** (R$ ou US$), períodos de 1, 3, 6 e 12 meses.

- Retorno na moeda nativa, **efeito cambial**, retorno na moeda comum e **retorno com proventos** (soma simples dos valores brutos com data-com no período, convertidos pelo PTAX da data-com; sem reinvestimento; JCP tem IR retido na fonte — não descontado).
- Volatilidade anualizada e queda máxima na série diária convertida pelo PTAX de cada data.
- P/L, P/VP, ROE, DY, EV/EBITDA, potencial até o preço-alvo (na moeda nativa), score e risco.
- Gráfico base 100 com e sem câmbio; cada linha mostra fonte, horário e atraso declarado da cotação.

## Cotações mais frequentes das ações dos EUA (rc8)

- `ACOES_EUA_INTERVALO_MIN` (padrão 15): com poucas ações na carteira, 5 min cabe na cota da Twelve Data (ex.: 3 ações × 78 ciclos = 234 créditos/dia).
- `FONTE_COTACAO_EUA=finnhub`: cotações das ações dos EUA pela Finnhub (`/quote`, até 50/min) — os candles continuam na Twelve Data. Atraso a confirmar na homologação (`homologar_fonte finnhub TTWO`); até lá, "atraso não declarado".
- Cotação oficial em tempo real da NYSE/Nasdaq exige feed licenciado e pago; o portal nunca rotula como tempo real o que a fonte não declara.
