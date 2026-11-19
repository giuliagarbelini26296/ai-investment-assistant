# Fixtures de fontes

Respostas reais capturadas nas rodadas de sondagem (sem chaves). Usadas pelos testes dos adaptadores a partir da E3.

- `testes-fontes-dados-2026-10-29-b3.json` — brapi (PETR4, VALE3, histórico, 401), Yahoo (PETR4.SA), Alpha Vantage demo. Rodada B3 de 29/10/2026 11h51 (São Paulo).
- `documentais-nao-homologadas.json` — respostas MONTADAS a partir da documentação pública (Twelve Data, Binance, PTAX, AwesomeAPI). Testam o parser; não comprovam o contrato real. Substituir pelas capturas de `manage.py homologar_fonte --salvar`.
- Pendente: capturas reais de EUA, cripto e câmbio (trilha E0) — gerar com `manage.py homologar_fonte --salvar docs/homologacao` e copiar para cá.
