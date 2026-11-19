# Portal de Ações

Portal pessoal de acompanhamento e análise de ativos — ações dos EUA (Avenue), criptomoedas (Binance) e ações da B3 — com visualização em USD e BRL, histórico de preços e alertas baseados em regras transparentes.

**Estado atual: 1.0.0-rc8.** Todas as funcionalidades do MVP estão construídas e testadas (303 testes), com o menu **Mercado** (rankings, oportunidades com score transparente, dólar, resumo executivo, abas **Ações EUA**, **Ações Brasil** e **Comparar mercados**) e a aba **⭐ Favoritos** — `docs/mercado.md`: carteira com revisões, adaptadores de fontes com validação e orçamento, coletor com agenda por mercado e estados de atualidade, conversão USD/BRL com efeito cambial, interface com filtros/comparação/gráficos/"Sobre os dados" e alertas por regras transparentes. Fontes Binance, Twelve Data e PTAX **homologadas com capturas reais em 30/10/2026** (`docs/fontes-de-dados.md`); o checklist de aceite está em `docs/aceite/checklist.md`.

> Enquanto `PORTAL_MODO_DEMO=true`, todas as telas exibem a faixa **DEMONSTRAÇÃO**. Nada exibido nesse modo é cotação de mercado. O modo demo só é desligado na E8, após a homologação das fontes de dados.

## Requisitos

- Docker e Docker Compose (v2) — caminho recomendado; **ou**
- Python 3.12+ e PostgreSQL 16 para execução direta.

## Execução com Docker Compose (recomendado)

```bash
cp .env.example .env
# edite .env: DJANGO_SECRET_KEY (longa e aleatória) e POSTGRES_PASSWORD
docker compose up --build -d          # sobe db, aplica migrações, inicia web e coletor
docker compose exec web python manage.py createsuperuser   # cria a conta de acesso
```

Acesse `http://127.0.0.1:8000/` e entre com a conta criada. Verificação rápida: `http://127.0.0.1:8000/saude/`.

Serviços do Compose:

| Serviço | Função |
|---|---|
| `db` | PostgreSQL 16, volume `pgdata`, sem porta publicada |
| `migrate` | Aplica migrações e encerra; `web` e `collector` só iniciam depois dele |
| `web` | Gunicorn servindo o portal em `127.0.0.1:8000` |
| `collector` | Processo do coletor (`manage.py coletar`): agenda por mercado (cripto a 5 min, bolsas a 15 min), câmbio, PTAX, resumo diário e vigia (ver `docs/coleta-e-atualidade.md`) |
| `backup` | `pg_dump` diário às 03:00 em `./backups/` (retenção 7) — `scripts/backup_loop.sh` |

Parar: `docker compose down` (mantém os dados). Remover dados: `docker compose down -v`.

## Execução direta (sem Docker)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
export DJANGO_SECRET_KEY='chave-longa' DATABASE_URL='postgres://usuario:senha@localhost:5432/portal_acoes'
python manage.py migrate
python manage.py createsuperuser
python manage.py collectstatic --noinput
python manage.py runserver            # desenvolvimento (DJANGO_DEBUG=true opcional)
```

## Configuração (`.env`)

| Variável | Uso |
|---|---|
| `DJANGO_SECRET_KEY` | Obrigatória fora de `DJANGO_DEBUG=true` |
| `DJANGO_DEBUG` | `false` por padrão |
| `DJANGO_ALLOWED_HOSTS` | Lista separada por vírgula |
| `DJANGO_HTTPS`, `DJANGO_CSRF_TRUSTED_ORIGINS` | Só para acesso remoto atrás de proxy HTTPS |
| `DATABASE_URL` | `postgres://usuario:senha@host:porta/banco` |
| `PORTAL_MODO_DEMO` | `true` = dados sintéticos rotulados |
| `PORTAL_BIND` | Onde o portal escuta: `127.0.0.1` (só este computador, padrão) ou `0.0.0.0` (rede local; inclua o IP em `DJANGO_ALLOWED_HOSTS`) |
| `TWELVEDATA_API_KEY`, `AWESOMEAPI_API_KEY`, `BRAPI_API_KEY`, `COINGECKO_API_KEY` | Chaves das fontes (a partir da E3). Nunca versionar |
| `FINNHUB_API_KEY`, `ALPHAVANTAGE_API_KEY` | Opcionais (gratuitas): analistas, preço-alvo, notícias, resultados e fundamentos do menu Mercado |
| `PORTAL_COLETA_MERCADO` | `true` (padrão) liga as rotinas do Mercado no coletor |
| `ACOES_EUA_INTERVALO_MIN`, `FONTE_COTACAO_EUA` | Frequência (padrão 15 min) e fonte (`twelvedata` ou `finnhub`) das cotações das ações dos EUA da carteira |
| `BRAPI_SIMBOLOS_POR_REQUISICAO`, `BRAPI_INTERVALO_MIN` | Ajuste ao plano da brapi (Gratuito: 1 e 30) |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `PORTAL_FEED_TOKEN`, `PORTAL_URL` | Avisos de alertas fora do portal (Telegram, notificação do macOS). Ver `docs/alertas.md` |
| `RESUMO_HORARIOS` | Horários do resumo diário (padrão `08:00,18:30`; vazio desliga) |
| `BACKUP_HORA`, `BACKUP_RETENCAO`, `BACKUP_AO_INICIAR` | Backup automático (serviço `backup`) |

Chaves e senhas ficam apenas no `.env` (ignorado pelo git) e nas variáveis de ambiente do Compose.

## Uso

0. **Carteira** (página inicial): tema escuro por padrão (botão ☀︎/☾); cards de patrimônio (com sparkline de 30 dias e equivalente na outra moeda), hoje, desde a compra e investido; **evolução do patrimônio** (1M/3M/1A — clique em dois pontos para medir a variação do período) e **alocação** (total no centro; passar o mouse destaca a linha, clicar filtra). Tabela interativa: **filtros instantâneos** (Ações/Cripto/Avenue/Binance/Em lucro/Em prejuízo + busca), **linhas expansíveis** (gráfico, compras, alertas e atalhos do ativo sem sair da página), **seleção para comparar** 2+ ativos em base 100, "mais colunas", indicador de atualidade como ponto colorido ao lado do preço, e o **simulador "E se…"** (sliders de preço por ativo e de câmbio recalculam patrimônio e resultado no navegador, marcados como simulados; nada é gravado). Atalhos: `/` busca, `u`/`r` moeda, `a` novo alerta. A página **se atualiza sozinha a cada 60 s** e **Atualizar agora** força a coleta. Funciona no celular.

1. **Ativos → Novo ativo**: símbolo, nome, mercado (define classe e moeda nativa), custódia.
2. Na página do ativo, **Nova compra**: data, quantidade (fracionária para cripto/EUA; inteira para B3), preço na moeda nativa e, para ativos em USD, o câmbio da remessa (opcional).
3. Alterações de compra geram revisão com o estado anterior; cancelamento é lógico e mantém o histórico (também visível em `/admin/`).
4. **Ver em US$ / R$** (cabeçalho): converte a carteira inteira; o câmbio em uso e seu horário ficam ao lado. Posições sem câmbio de compra resolvível aparecem como "sem base cambial" — informe o câmbio na compra ou carregue o PTAX (`manage.py carregar_historico --so-cambio --dias 365`).
5. **Ativos**: filtros por texto, classe, mercado, custódia, setor e situação; clique no cabeçalho para ordenar. **Comparar**: 2+ ativos lado a lado com evolução relativa (base 100). No detalhe do ativo, o gráfico de histórico tem períodos 1S/1M/6M/1A/desde a compra e, quando a moeda de visualização difere da nativa, a série convertida pelo PTAX de cada data (sem interpolar lacunas).
6. **Sobre os dados**: cada fonte com tipo de dado, atraso declarado, limites, consumo de cota, homologação e termos; calendários; estado do coletor e últimas execuções.
7. **Ativos → Importar CSV**: compras em lote a partir de um CSV genérico (`simbolo,data,quantidade,preco[,moeda,cambio,mercado,custodia]`) ou do extrato de ordens da Binance, com pré-visualização antes de gravar.
8. **Alertas → Novo alerta**: uma frase — "me avise quando [ativo] [cair 5 % no dia]" — com atalhos; rótulo e nome são opcionais. **Prévia** em tempo real: quais ativos já disparariam com a última cotação e quanto falta para cada um. O coletor avalia a cada ciclo; o painel mostra alertas ativos, suspensos, avisos enviados, regras e histórico. Avisos fora do portal por **Telegram** e **notificação do macOS**; **resumo diário** da carteira (08:00 e 18:30) e **vigia** do coletor pelos mesmos canais. **Interativo**: botões nos avisos do Telegram (visto, pausar, ajustar limite, carteira) e comandos `/carteira`, `/alertas`, `/preco`, `/alerta`; no portal, alerta criado clicando no gráfico do ativo, painel que atualiza sozinho com ações rápidas (silenciar 24 h, ajustar limite, pausar) — `docs/alertas.md`.

9. **Mercado**: visão geral com resumo executivo (fatos, consenso, projeções e riscos, cada um com fonte e data), maiores altas e quedas de 1 dia a 12 meses (preço, variação, volume, máxima e mínima, filtros por tipo e busca), **Top 10 Oportunidades** pelo score 0–100 (critérios abertos ao clicar), painel do **dólar** (comercial, PTAX 7/30 dias, gráfico, Focus, notícias) e o **Universo** monitorado. Atualiza sozinho. **⭐ Favoritos**: estrela em todas as listas, grupos, indicadores de 52 semanas, preço-alvo, score, notícias, comparação base 100 e "Analisar meus favoritos". Detalhe do ativo ganhou a **Leitura de mercado** (indicadores, score, por que pode subir / o que pode impedir). Ver `docs/mercado.md`.

Gráficos usam o Chart.js 4.4.7 servido localmente (`apps/ui/static/ui/chart.umd.js`, licença MIT em `chart.LICENSE.md`); o portal não carrega nada de CDNs.

## Coleta

Com `PORTAL_MODO_DEMO=true` o coletor grava cotações sintéticas rotuladas. Carga inicial de histórico:

```bash
docker compose exec web python manage.py carregar_historico --dias 30
```

## Fontes de dados e homologação

Ver `docs/fontes-de-dados.md`. Para testar uma fonte real com suas chaves (nada é gravado no banco):

```bash
docker compose exec web python manage.py homologar_fonte twelvedata TTWO --historico 30 --salvar docs/homologacao
```

## Qualidade e testes

```bash
ruff check . && ruff format --check .
python -m pytest
```

Os testes não acessam a internet. Respostas reais das fontes ficam em `tests/fixtures/sources/` e são usadas pelos testes dos adaptadores (E3).

## Documentação

| Arquivo | Conteúdo |
|---|---|
| `docs/arquitetura.md` | Resumo da arquitetura e estado das etapas |
| `docs/fontes-de-dados.md` | Cada fonte: tipo de dado, atraso declarado, limites, termos, homologação |
| `docs/coleta-e-atualidade.md` | Agenda, ciclo de coleta, estados de atualidade |
| `docs/regras-de-calculo.md` | Fórmulas (nativo e conversão cambial) com exemplos testados |
| `docs/mercado.md` | Menu Mercado e Favoritos: universo, coleta dentro das cotas, indicadores, score 0–100, análises, dólar |
| `docs/alertas.md` | Criação em linguagem simples, famílias, ciclo de vida, texto obrigatório, avisos por Telegram/macOS |
| `docs/operacao.md` | Subir/parar, backup, diagnóstico, segurança |
| `docs/homologacao/README.md` | Trilha E0: comandos e checklist por fonte |
| `docs/aceite/checklist.md` | Critérios de aceite com evidências e pendências |

## Estrutura

```
config/            settings, urls, wsgi
apps/accounts      login simples (django.contrib.auth)
apps/ui            templates, CSS, páginas
apps/marketdata    adaptadores de fontes, calendários, coletor (E3/E4)
apps/portfolio     ativos e compras (E2)
apps/valuation     cálculo puro com Decimal (E2/E5)
apps/alerts        regras e alertas (E7)
tests/             pytest
scripts/           backup/restauração e script de sondagem de fontes
docs/              arquitetura, fontes, cálculo, alertas
```

## Backup e restauração

```bash
scripts/backup.sh            # gera backups/portal_acoes_<data>.dump (retenção: 7)
scripts/restore.sh ARQUIVO   # restaura sobre o banco do Compose (pede confirmação)
```

Ensaio de restauração executado em 29/10/2026 (modo direto): contagens iguais antes e depois. Mais em `docs/operacao.md`.

## Aviso

Os sinais e alertas do portal derivam exclusivamente de regras definidas pelo usuário e **não constituem recomendação de investimento**. Cotações e câmbio vêm de fontes externas, com atraso e limitações informados na página "Sobre os dados" (E6).
