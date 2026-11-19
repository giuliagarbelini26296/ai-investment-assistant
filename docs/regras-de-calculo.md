# Regras de cálculo

Implementação: `apps/valuation/core.py` (funções puras, `Decimal`, sem Django). Testes: `tests/test_valuation.py`.

## Moeda nativa (E2)

Para lotes ativos `(qᵢ, pᵢ)` de um ativo:

| Grandeza | Fórmula |
|---|---|
| Quantidade | `Q = Σ qᵢ` |
| Investido | `I = Σ qᵢ·pᵢ` |
| Preço médio | `PM = I / Q` |
| Valor atual (preço válido `P`) | `V = Q·P` |
| Resultado | `V − I` |
| Retorno | `100·(V − I)/I` |
| Variação do dia | `100·(P/fechamento_anterior − 1)`; cripto usa a variação 24 h da fonte |

Exemplo verificado em teste: 10 × R$ 20 + 20 × R$ 23 → Q = 30, I = R$ 660, PM = R$ 22; a R$ 24 → V = R$ 720, resultado R$ 60, retorno 9,09%.

Consolidação (mesma moeda): resultado total = soma dos resultados; retorno consolidado = resultado total / investido **das posições avaliadas**; nunca média de percentuais. Sem preço válido para alguma posição, o consolidado é parcial e informa a cobertura ("1 de 2 posições com preço"); o investido total cobre todas as posições.

Regras de entrada: quantidade e preço positivos e finitos; lotes cancelados não entram; B3 exige quantidade inteira; cripto e ações dos EUA aceitam fração.

Arredondamento (2 casas, comercial) só na apresentação (`apps/ui/templatetags/formatos.py`); comparações e alertas usam valores exatos.

Exclusões do MVP: custos, taxas, impostos, proventos e eventos corporativos (aviso permanente na interface).

## Conversão USD/BRL (E5)

Implementação: `apps/valuation/fx.py` (puro) e `apps/marketdata/fxservice.py` (câmbio atual e de compra). Testes: `tests/test_fx.py`.

Com `rᵢ` = taxa nativa→visão de cada lote e `r` = taxa atual (USD/BRL para USD→BRL; 1/(USD/BRL) para BRL→USD):

| Grandeza | Fórmula |
|---|---|
| Investido na visão | `I_v = Σ qᵢ·pᵢ·rᵢ` |
| Taxa média ponderada | `R̄ = I_v / I` |
| Valor atual na visão | `V_v = Q·P·r` |
| Resultado na visão | `V_v − I_v` = **resultado do ativo** `Q·(P − PM)·r` + **efeito cambial** `Q·PM·(r − R̄)` |

Exemplo verificado em teste: 10 × US$ 100 @ 5,00 + 10 × US$ 120 @ 5,50; P = US$ 130; f = 5,30 → investido R$ 11.600, valor R$ 13.780, resultado R$ 2.180 = R$ 2.120 (ativo) + R$ 60 (câmbio); retorno 18,79% em BRL contra 18,18% em USD.

**Câmbio atual** (`cambio_atual`): intradiário com até 30 min → PTAX de fechamento do dia (tolera 1 dia útil; 3 dias no fim de semana) → último disponível, rotulado "desatualizado". A interface mostra sempre qual cotação foi usada e de quando.

**Câmbio de compra** (`resolver_cambio_lote`): informado pelo usuário → PTAX da data → PTAX do dia útil anterior (até 7 dias) → **sem base cambial**. A origem fica gravada em `Lot.fx_origin` / `Lot.fx_resolved` e aparece na tabela de compras. Ativos em BRL vistos em USD usam o PTAX da data sem persistir (origem `nao_aplicavel` no lote).

**Sem base cambial**: a posição aparece só na moeda nativa, fica fora dos totais convertidos e o resumo informa "N posição(ões) sem base cambial". Nunca se usa o câmbio atual no lugar do câmbio de compra.

Divisões (BRL→USD) geram dízimas: o cálculo mantém a precisão do `Decimal` e o arredondamento continua só na apresentação.
