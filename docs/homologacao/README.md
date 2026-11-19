# Homologação das fontes (trilha E0)

Execute na máquina com internet, com as chaves gratuitas no `.env` (`TWELVEDATA_API_KEY`, `AWESOMEAPI_API_KEY`; `BRAPI_API_KEY` se houver B3). Nada é gravado no banco; as respostas brutas (sem chaves) ficam nesta pasta.

```bash
docker compose exec web python manage.py homologar_fonte twelvedata TTWO AAPL --historico 30 --salvar docs/homologacao
docker compose exec web python manage.py homologar_fonte binance BTCUSDT BTCBRL --historico 30 --salvar docs/homologacao
docker compose exec web python manage.py homologar_fonte ptax_bcb --salvar docs/homologacao
docker compose exec web python manage.py homologar_fonte awesomeapi --salvar docs/homologacao
docker compose exec web python manage.py homologar_fonte brapi PETR4 --historico 30 --salvar docs/homologacao   # se houver B3
```

Checklist por fonte (registrar em `docs/fontes-de-dados.md`):

1. Formato de resposta confirmado (se o parser falhar, o JSON salvo aqui serve para corrigir o adaptador e virar fixture em `tests/fixtures/sources/`).
2. Timestamp de mercado interpretado corretamente (fuso, unidade).
3. Idade informada medida em 3 pontos de um pregão — **não comprova atraso real**; o rótulo do portal usa o atraso declarado pela fonte.
4. Consumo de créditos por símbolo e por ciclo observado em "Sobre os dados"; 429 simulado de forma controlada (ex.: reduzir temporariamente `limite_minuto` e observar o ciclo "pulado").
5. Termos de uso lidos; atribuição definida.
6. Decisão: aprovada / aprovada com restrição / substituída → `FonteInfo.homologada`.

Depois: `PORTAL_MODO_DEMO=false`, `manage.py carregar_historico --dias 365` (dividido por dia se a cota apertar), observar um pregão.
