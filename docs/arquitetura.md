# Arquitetura (resumo)

Fonte de verdade: `arquitetura-proposta-v1.1.md` no Project "brasil Japao" (pasta `portal-acoes/`).

- **Stack atual (04/11/2026):** Django 5.2 + PostgreSQL 16 + React/React Router + Tailwind CSS + Recharts + Lucide, com API JSON autenticada e Docker Compose. Templates Django/Chart.js permanecem na visão clássica e nas telas de gestão.
- **Processos:** `web` (Gunicorn) e `collector` (`manage.py coletar`), compartilhando código e banco. Sem Celery/Redis.
- **Dados de mercado:** quatro adaptadores independentes — ações EUA (Twelve Data), cripto (Binance; CoinGecko como contingência), B3 (brapi, opcional), câmbio USD/BRL (PTAX/BCB + AwesomeAPI). Cada um declara fonte, atraso, limites e termos.
- **Moeda:** cada ativo tem moeda nativa; cálculo percentual e alertas sempre na moeda nativa; conversão USD/BRL é camada de apresentação com câmbio identificado.
- **Transparência:** nenhum preço ou câmbio aparece sem fonte e horário; dado ausente é "indisponível"; dado antigo é "desatualizado"; modo demo rotulado.

## Etapas

| Etapa | Estado |
|---|---|
| E1 base do projeto | concluída (29/10/2026) |
| E2 carteira e cálculo nativo | concluída (29/10/2026) |
| E3 adaptadores e modo demo | concluída (29/10/2026) |
| E4 coletor, calendários, atualidade | concluída (29/10/2026); rc3: laço a 30 s, cripto a 5 min, "Atualizar agora", serviço de backup diário |
| E5 conversão cambial | concluída (29/10/2026) |
| E6 interface | concluída (29/10/2026); 30/10 (rc3): tema escuro/claro, carteira ao vivo (fragmento + fetch), resumo/evolução/alocação (`apps/ui/resumo.py`), sparklines, layout mobile, importação CSV (`apps/portfolio/importacao.py`) |
| E7 alertas | concluída (29/10/2026); 30/10: criação em linguagem simples, avisos por Telegram/macOS (`apps/alerts/notify.py`, caixa de saída), resumo diário (`resumo_diario.py`), vigia (`vigia.py`); rc4: Telegram de mão dupla (`telegram_bot.py`), alerta pelo gráfico, painel ao vivo com ações rápidas |
| E8 fechamento do MVP | parte independente concluída (29/10/2026); pendente da E0 |
| E0 homologação de fontes (paralela) | em andamento na máquina do usuário: PTAX carregado com dados reais (30/10/2026, 249 datas); Binance aguardando cadastro do BTC; Twelve Data aguardando chave |

## Interface oficial React — 04/11/2026

A página inicial usa a interface React aprovada. A API `/api/v1/portfolio/` reutiliza os serviços de posição e consolidação com Decimal, filtros e cobertura por moeda. Login, sessões e permissões permanecem no Django existente. A visão anterior está em `/classico/`, e as demais rotas continuam disponíveis. Nenhuma migração de dados foi necessária. Keycloak/OIDC continua pendente de etapa própria. Operação e reversão em `docs/react-oficial.md`.
