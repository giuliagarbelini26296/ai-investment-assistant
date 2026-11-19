# Alertas

Implementação: `apps/alerts/` (modelos, `services.py`, `forms.py` em linguagem simples, `notify.py`, telas). Testes: `tests/test_alerts.py`, `tests/test_notify.py`.

## Regras

Seis condições em três famílias, avaliadas **na moeda nativa** do ativo, com valores exatos (sem arredondamento):

| Família | Condições | Comparação |
|---|---|---|
| Desde a compra | queda ≥ X% / alta ≥ X% | retorno simples da posição ≤ −X / ≥ X |
| No dia / 24 h | queda ≥ X% / alta ≥ X% | variação vs. fechamento anterior (ações) ou variação 24 h informada pela fonte (cripto) ≤ −X / ≥ X |
| Preço absoluto | abaixo de / acima de | preço estritamente < / > limite, na moeda da regra (USDT é tratado como USD) |
| Sinais de mercado (rc7) | até X% da mínima de 52 semanas · rompe suporte de X sessões · rompe resistência de X sessões · resultado em até X dias · recomendações de compra mudam ≥ X p.p. | candles diários (mínima/máxima das X sessões completas anteriores), calendário de resultados e recomendações da Finnhub; só para um ativo específico |

- **Ativos sem posição** (favoritos, universo do Mercado, dólar USDBRL): valem regras específicas do ativo nas famílias "no dia", "preço absoluto" e "sinais de mercado". Regras globais ("qualquer ativo da carteira") e "desde a compra" continuam só para posições.
- **Anti-repetição**: a mesma regra no mesmo ativo não manda nova mensagem (Telegram/macOS) antes de 6 h; o alerta fica registrado no portal com a observação "não reenviada".
- **Dólar**: o câmbio intradiário é espelhado no ativo USDBRL; crie "dólar subir/cair X% no dia" ou "acima/abaixo de R$ X" pelo painel do Dólar ou pelos atalhos.

- Escopo: regra **global** (todos os ativos) ou de **um ativo**; regras globais e específicas são avaliadas independentemente.
- A **classificação** (apenas aviso / compra / venda / manutenção) é um rótulo escolhido pelo usuário na regra; o padrão é "apenas aviso" e o sistema nunca a infere.
- Cada edição cria uma **versão imutável** (`RuleVersion`); alertas apontam para a versão que os gerou. Editar reinicia o estado e resolve os alertas ativos da versão anterior; desativar resolve e preserva o histórico.

## Ciclo de vida

| Situação | Efeito |
|---|---|
| Primeira avaliação com dado válido e condição verdadeira | dispara |
| falso → verdadeiro | dispara |
| verdadeiro → verdadeiro | mantém o alerta ativo (não duplica) |
| verdadeiro → falso | resolve o alerta e rearma a regra |
| Marcar como visto | só registra `seen_at`; não resolve nem rearma |
| Atualidade não permite (desatualizado, desconhecido, sem cotação) ou sem posição | avaliação **suspensa**: não resolve nem rearma; na retomada avalia contra o último estado válido |

A avaliação roda ao fim de cada ciclo do coletor (`collector._avaliar_alertas`) para os ativos do mercado coletado e pode ser disparada manualmente (`alerts.services.avaliar_todos()`).

## Texto obrigatório

Todo alerta exibe a regra, o limite, os valores observados (preço, preço médio, retorno ou variação), fonte e horários, e o texto:

> Sinal baseado em regra definida pelo usuário. Não constitui recomendação de investimento.

## Criar um alerta (linguagem simples)

A tela **Novo alerta** monta uma frase — *Me avise quando [ativo] [cair 5 % no dia]* — e a traduz para família/condição/limite/moeda:

| Frase | Família | Condição |
|---|---|---|
| cair X % no dia (24 h) / subir X % no dia (24 h) | dia_24h | queda / alta |
| cair X % em relação ao que paguei / subir X % … | desde_compra | queda / alta |
| ficar abaixo de um preço / ficar acima de um preço (US$ ou R$) | preco_absoluto | abaixo / acima |

- Atalhos: queda/alta de 5% no dia, queda de 10% e alta de 20% desde a compra.
- O valor aceita vírgula ou ponto (`5,5`, `100.000`, `100,000.00`); um ponto seguido de exatamente três dígitos é lido como milhar (convenção pt-BR), exceto `0.xxx`.
- Nome, rótulo e anotação ficam em "Opções avançadas"; sem nome, o nome é a própria frase (`BTC: queda de 5% no dia`).
- Na página do ativo há o atalho **Criar alerta**, que já seleciona o ativo.

## Avisos fora do portal

Alertas disparados geram uma `Notification` por canal ligado (caixa de saída, única por alerta × canal). O coletor envia ao fim de cada ciclo; falhas ficam registradas no painel e são retentadas até 5 vezes. Toda mensagem traz título (`ATIVO: regra`), preço observado, referência (retorno ou variação), limite, fonte e horário do preço, o texto obrigatório e o link do painel.

| Canal | Como ligar | Como funciona |
|---|---|---|
| Telegram | `TELEGRAM_BOT_TOKEN` e `TELEGRAM_CHAT_ID` no `.env`; `docker compose up -d` | Bot API `sendMessage` a partir do contêiner do coletor |
| Notificação do macOS | `PORTAL_FEED_TOKEN` no `.env` (token longo e aleatório); no Mac: `PORTAL_FEED_TOKEN=... scripts/mac/instalar.sh` | O agente launchd consulta `/alertas/feed/` a cada 60 s com o token no cabeçalho `X-Portal-Token`, exibe via `osascript` e confirma em `/alertas/feed/confirmar/` |

Criar o bot do Telegram: no app, fale com **@BotFather** → `/newbot` → copie o token. Mande qualquer mensagem ao bot criado e abra `https://api.telegram.org/bot<TOKEN>/getUpdates` no navegador: o `chat.id` que aparece é o `TELEGRAM_CHAT_ID`. Teste com `docker compose exec web python manage.py notificacoes --testar`.

Segurança: token e chat id só no `.env` (e no plist do launchd, com permissão 600); erros registrados nunca incluem o token; o feed responde 401 sem token válido (comparação em tempo constante) e fica desligado quando `PORTAL_FEED_TOKEN` está vazio.

## Resumo diário e vigia

- **Resumo diário** (`apps/alerts/resumo_diario.py`): nos horários de `RESUMO_HORARIOS` (padrão `08:00,18:30`, São Paulo) o coletor enfileira, pelos canais ligados, um resumo com patrimônio/hoje/desde a compra em BRL e USD, câmbio em uso, maiores altas e quedas do dia, posições sem cotação ou desatualizadas e alertas ativos. Usa só a última cotação válida já gravada e informa a atualidade; um envio por horário/dia (`ScheduleState(task="RESUMO").last_slot`). `RESUMO_HORARIOS=` vazio desliga.
- **Vigia** (`apps/alerts/vigia.py`): a cada passagem do coletor verifica, por tarefa, 3 falhas seguidas ou ausência de sucesso há mais de 45 min enquanto a tarefa está ativa. Abre um `OpsIncident` e envia "Coleta com problema" uma vez; quando normaliza, envia "Coleta normalizada" com a duração. Sem canal ligado, só registra.
- As três espécies de notificação (`kind`: alerta, resumo, operação) passam pela mesma caixa de saída e aparecem no painel em "Avisos fora do portal".

## Interação

**Telegram de mão dupla** (`apps/alerts/telegram_bot.py`). O coletor lê o bot por long polling (≈5 s entre passagens; sem webhook, sem expor o portal) e aceita o chat particular de `TELEGRAM_CHAT_ID` e, quando configurado, o grupo de consulta descrito abaixo; mensagens de outros chats são ignoradas e registradas. O offset fica em `ScheduleState(task="TELEGRAM").last_slot`.

**Grupo privado de consulta:** mantenha `TELEGRAM_CHAT_ID` como o chat particular da titular e
configure `TELEGRAM_GRUPO_CHAT_ID` com o ID negativo do grupo privado escolhido. Os avisos automáticos
(alertas, resumos e avisos operacionais) passam a ser enviados ao grupo, em vez do chat particular.
No grupo, `/carteira`, `/resumo`, `/alertas`, `/preco` e `/ajuda` permitem consultas; acrescente
`@InvestAlertasBot` ao comando para garantir a entrega com o modo de privacidade do Telegram ativo.
Os botões de consulta também funcionam. Criar regras, marcar alertas como vistos, pausar regras e
ajustar limites ficam bloqueados no grupo, inclusive por botões antigos ou callbacks forjados.
O chat particular da titular continua com os controles existentes. Outros chats permanecem ignorados.
Somente membros de confiança devem integrar o grupo: todos os membros dele poderão consultar a carteira.
Para voltar aos avisos somente no particular, esvazie `TELEGRAM_GRUPO_CHAT_ID` e reaplique o ambiente.

| Onde | O que faz |
|---|---|
| Botões no aviso de alerta | **✓ Visto** (marca visto; não resolve) · **🔕 Pausar regra** (desativa; alertas resolvidos) · **✏️ Ajustar limite** (−20/−10/+10/+20 % → nova versão, estado reiniciado) · **📊 Carteira** (resumo agora) |
| Botões no resumo | **📊 Carteira** · **⚠️ Alertas** |
| `/carteira` ou `/resumo` | Resumo da carteira (mesmo texto do resumo diário) |
| `/alertas` | Alertas ativos com preço e horário do disparo |
| `/preco BTC` | Última cotação válida, variação 24 h, fonte, atualidade e horário |
| `/alerta BTC cair 5% no dia` | Cria a regra pela frase (também `subir 10% desde a compra`, `abaixo de US$ 100000`, sem símbolo = global); responde a descrição exata gravada |
| `/ajuda` | Lista de comandos |

Toda resposta com preço traz fonte e atualidade; toda criação/ajuste de regra traz o texto obrigatório. Ajustes pelo Telegram ficam na trilha de versões com motivo "ajuste pelo Telegram".

**No portal.**
- **Prévia ao criar a regra** (`GET /alertas/regras/previa.json?tipo&valor&currency[&ativo]`): avalia a regra proposta contra a última cotação válida de cada ativo e devolve, por ativo, o valor atual da métrica, o limite, a distância e se já dispararia; a tela mostra isso abaixo da frase enquanto você digita. Nada é gravado; usa `services.avaliar_condicao` com uma versão não salva.
- **Alerta pelo gráfico**: na página do ativo, clicar num ponto do gráfico abre "Avisar quando X ficar abaixo/acima de US$ …" com o preço do ponto; "Criar alerta" leva ao formulário já preenchido. O gráfico desenha o **preço médio** (tracejado) e os **limites dos alertas de preço** do ativo (vermelho = abaixo, verde = acima) quando estão na faixa visível.
- **Painel ao vivo** (`/alertas/fragmento/` a cada 30 s): alerta novo entra com toast; o contador do menu acompanha. Ações por linha, sem recarregar: **🔕 24 h** (`POST /alertas/<id>/silenciar/` — marca visto e some do destaque/contador por 24 h; regra inalterada), **✏️ limite** (`POST /alertas/regras/<id>/limite/` — nova versão só com o limite), **⏸ pausar regra** (com confirmação); "pausar/reativar" na tabela de regras também é instantâneo. A atualização automática espera enquanto um ajuste estiver aberto.

## Telas

- `/alertas/`: ativos (não vistos destacados; marcar vistos), avaliações suspensas com motivo, **avisos fora do portal** (canais ativos e últimas 20 notificações com estado), regras (versão atual, histórico de versões no título da coluna), histórico de resolvidos.
- Novo alerta / editar (nova versão com anotação) / desativar / reativar.
- Menu mostra o contador de alertas não vistos; o dashboard marca ativos com alerta ativo.
- `manage.py notificacoes [--testar] [--reenviar]`: canais ligados, pendências, teste do Telegram, reenvio manual.
