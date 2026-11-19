# AI Investment Assistant

Plataforma pessoal de acompanhamento de investimentos (ações dos EUA, ações da B3 e criptomoedas) com **assistente de IA com tool calling**, **servidor MCP (Model Context Protocol)**, integração com APIs financeiras, alertas e relatórios automáticos.

> **Origem do projeto.** Projeto pessoal desenvolvido por Giulia Garbelini entre outubro e novembro de 2026, com apoio de ferramentas de IA (ChatGPT, Codex e Claude) na codificação e na revisão. Esta é uma **cópia pública anonimizada** da instalação pessoal:
> - As carteiras reais foram substituídas por duas carteiras fictícias, `ana` e `bruno`, com posições inventadas.
> - Saíram desta versão os documentos com valores, os backups, os arquivos `.env` e o domínio privado.
>
> O histórico de commits começa na publicação; o desenvolvimento anterior não foi versionado em Git.

## Problema

Quem investe em mais de um mercado precisa abrir várias corretoras e sites para responder perguntas simples, como "quanto minha carteira variou hoje, em reais e em dólares?" ou "alguma ação caiu abaixo do meu preço-alvo?". As cotações chegam de fontes diferentes, com atrasos e moedas diferentes, e é fácil confundir efeito cambial com resultado do ativo.

## Solução

Um portal web que:

- **Coleta cotações** de várias APIs com agenda por mercado. Cada valor tem fonte, horário e estado de atualidade, e dados ausentes não são preenchidos por estimativa.
- **Calcula posições, resultado e efeito cambial** USD/BRL com PTAX do Banco Central, mantendo carteiras separadas por titular.
- **Dispara alertas por regras transparentes** (queda no dia, preço-alvo, rompimento) e envia um **resumo diário** por Telegram e Web Push.
- **Oferece um assistente de IA** que consulta os dados do portal por **function calling**, em vez de inventar preços.
- **Expõe um servidor MCP** somente leitura, para que clientes como Claude Desktop consultem carteira, cotações, notícias e indicadores.

## Arquitetura

```mermaid
flowchart LR
  subgraph Fontes["APIs financeiras"]
    TD[Twelve Data] & BR[brapi B3] & BN[Binance REST/WebSocket] & AL[Alpaca IEX] & BCB[BCB PTAX/SGS] & FH[Finnhub / Alpha Vantage]
  end
  Fontes -->|adapters com validação e orçamento de chamadas| COL[Coletor<br/>manage.py coletar]
  COL --> DB[(PostgreSQL)]
  DB --> WEB[Django 5.2<br/>API JSON + páginas]
  WEB --> UI[React + Vite<br/>SPA]
  WEB --> AST[Assistente IA<br/>OpenAI Responses + tool calling]
  AST -->|consultar_portal| WEB
  WEB --> MCP[Servidor MCP<br/>Streamable HTTP /mcp/]
  MCP --> CLI[Claude Desktop / outros clientes MCP]
  COL --> ALR[Alertas e resumo diário] --> TG[Telegram / Web Push / Excel]
```

| Camada | Onde está |
|---|---|
| Adapters de dados (validação, limites, modo demo) | `apps/marketdata/adapters/` |
| Coletor com agenda por mercado e calendário de pregões | `apps/marketdata/collector.py` |
| Carteiras, lotes, custo médio e efeito cambial | `apps/portfolio/`, `apps/managed_portfolios/` |
| Assistente de IA (OpenAI com tool calling; Gemini e Ollama como alternativas sem ferramentas) | `apps/insights/assistant.py`, `gemini_assistant.py`, `ollama_assistant.py` |
| Servidor MCP (JSON-RPC 2.0, Streamable HTTP, chave Bearer, limite de 120 chamadas/hora, log das chamadas) | `apps/react_portal/mcp.py` |
| Alertas, resumo diário, Telegram | `apps/alerts/` |
| Notícias com tradução local EN→PT (CTranslate2) | `apps/noticias/` |
| Front-end React | `frontend/` (build em `apps/react_portal/static/react_portal/`) |
| Logs com remoção de segredos | `config/log_filters.py` |

### Assistente de IA (tool calling)

- **Ferramenta única de leitura:** `consultar_portal`. O modelo é obrigado a chamá-la na primeira rodada (`tool_choice: required`).
- **No máximo 3 etapas por pergunta** e uma ferramenta por rodada.
- **Argumentos validados** antes da execução.
- **Limite diário de uso.**
- **O prompt de sistema proíbe valores que não vieram da ferramenta.**
- **Sem chave de API configurada,** o assistente cai num modo local de comandos limitados, identificado como tal.

### Ferramentas MCP

| Ferramenta | O que retorna |
|---|---|
| `carteira` | Posições e totais por moeda |
| `movimento_do_dia` | Maiores altas e quedas desde o fechamento anterior |
| `cotacoes` | Última cotação com fonte, horário e variação |
| `noticias` | Notícias recentes sobre os ativos da carteira |
| `evolucao` | Patrimônio diário comparado a Ibovespa/S&P 500 e CDI |
| `indicadores` | Selic, IPCA e CDI do Banco Central |

## Tecnologias utilizadas

- **Back-end:** Python 3.12, Django 5.2, PostgreSQL 16, httpx, websockets, gunicorn, WhiteNoise.
- **IA:** OpenAI Responses API (tool calling), Gemini (endpoint compatível com OpenAI), Ollama (modelos locais), MCP (protocolo implementado diretamente sobre JSON-RPC, sem SDK), CTranslate2 + SentencePiece (tradução local).
- **Front-end:** React 19, Vite, React Router, Recharts, Framer Motion.
- **Infra e qualidade:** Docker Compose, pytest + pytest-django, ruff, GitHub Actions.
- **Saídas:** XlsxWriter (Excel), pywebpush (Web Push), Telegram Bot API.

## Como executar

### Opção 1 — Docker Compose (modo demonstração)

```bash
cp .env.example .env
# edite: DJANGO_SECRET_KEY (longa e aleatória), POSTGRES_PASSWORD e DATABASE_URL com a mesma senha
# mantenha PORTAL_MODO_DEMO=true para usar dados sintéticos sem chaves de API
docker compose up --build -d
docker compose exec web python manage.py createsuperuser
```

Acesse `http://127.0.0.1:8000/`. A verificação de saúde fica em `http://127.0.0.1:8000/saude/`.

> **Como foi verificado.**
> - **Docker Compose:** é o modo usado na instalação pessoal; não foi executado novamente nesta cópia.
> - **Python local (opção 2):** verificado em 19/11/2026, com `/saude/` respondendo `{"status": "ok", "banco": "ok"}` e o MCP respondendo a `initialize`, `tools/list` e `tools/call`.

### Opção 2 — Python local

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
export DATABASE_URL=postgres://usuario:senha@localhost:5432/portal DJANGO_DEBUG=1 \
       DJANGO_SECRET_KEY=troque-esta-chave PORTAL_MODO_DEMO=true
python manage.py migrate
python manage.py runserver 127.0.0.1:8000
```

### Conectar um cliente MCP

```bash
# cria uma chave somente leitura e grava em arquivo (permissão 600)
python manage.py chave_notificacoes criar meu-cliente --escopo leitura --arquivo ~/.mcp-portal-key
```

Configure o cliente com o endpoint `http://127.0.0.1:8000/mcp/` (Streamable HTTP) e o cabeçalho `Authorization: Bearer <chave>`. Exemplo de chamada:

```bash
curl -s -X POST http://127.0.0.1:8000/mcp/ \
  -H "Authorization: Bearer $(cat ~/.mcp-portal-key)" -H 'Content-Type: application/json' \
  -H 'MCP-Protocol-Version: 2025-06-18' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'
```

Resposta (trecho, executada em 19/11/2026 no modo demonstração):

```json
{"jsonrpc": "2.0", "id": 2, "result": {"tools": [{"name": "carteira",
 "description": "Posições e totais por moeda (quantidade, preço médio, cotação, valor, resultado e variação do dia).",
 "annotations": {"readOnlyHint": true, "destructiveHint": false, "openWorldHint": false}, ...
```

Sem chave, o servidor responde HTTP 401: `"Chave de leitura ausente, inválida ou revogada."`

## Testes

```bash
export DATABASE_URL=postgres://portal:senha@localhost:5432/portal DJANGO_DEBUG=1 DJANGO_SECRET_KEY=teste
pytest -o addopts="" -q
ruff check . --select F
```

**Resultado em 19/11/2026** (Python 3.12, PostgreSQL 16 em Docker):

```
650 passed, 1 skipped in 64.05s
ruff check . --select F → All checks passed!
```

- **O teste pulado** usa o tradutor local real, cujos pesos (`model.bin`) não acompanham o repositório.
- **Três testes antigos foram atualizados na preparação desta versão,** porque descreviam um comportamento anterior do coletor e da navegação: a reconfirmação do fechamento dos EUA e a remoção do link "clássico" do menu.

## Implementado vs. planejado

| Funcionalidade | Estado |
|---|---|
| Coleta multi-fonte com agenda, calendário e estados de atualidade | Implementado |
| Carteiras separadas, custo médio exato, efeito cambial USD/BRL | Implementado |
| Alertas por regras + resumo diário (Telegram, Web Push) | Implementado |
| Assistente com tool calling (OpenAI) | Implementado |
| Assistente com Gemini e Ollama | Implementado, **sem** tool calling |
| Servidor MCP somente leitura | Implementado |
| Exportação Excel e planilha de acompanhamento | Implementado |
| RAG / embeddings / banco vetorial | **Não implementado** |
| API em FastAPI | **Não** — a API é Django |
| Orquestração multiagente | **Não implementado** |
| Operações de compra e venda em corretora | **Fora do escopo** (somente leitura e registro manual) |

## Limitações e melhorias futuras

- **Os testes exigem PostgreSQL.** Uma configuração com SQLite deixaria a execução local mais simples.
- **Há dívida de estilo no lint.** O CI cobra apenas as regras de erro real (pyflakes); as regras de estilo, como linhas longas e comandos na mesma linha, ainda acusam cerca de 1.200 avisos.
- **Gemini e Ollama não usam ferramentas.** Ainda falta portar o loop de tool calling para eles.
- **Planos gratuitos das APIs limitam a cobertura.** Algumas fontes não oferecem consenso de analistas nem tempo real (detalhes em `docs/fontes-de-dados.md` e `docs/mercado.md`).
- **Documentação interna em português e com histórico de decisões:** `docs/arquitetura.md`, `docs/regras-de-calculo.md`, `docs/alertas.md` e o guia operacional completo em `docs/guia-operacional.md`.

## Aviso

Este projeto **não é recomendação de investimento**. As cotações podem ter atraso, e os dados do modo demonstração são sintéticos.

## Licença

MIT, © 2026 Giulia Garbelini. O modelo de tradução em `translation-model/` deriva do OPUS-MT (CC-BY 4.0), e as dependências do front-end estão listadas em `frontend/THIRD_PARTY_NOTICES.md`.
