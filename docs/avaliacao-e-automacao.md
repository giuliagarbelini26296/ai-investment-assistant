# Avaliação e estudo de automação — 09/11/2026

Pedido: avaliar comprar, manter, aguardar ou rever redução de ativos já possuídos e novos candidatos; dimensionar papéis com orçamento e concentração. Usuário escolheu perfil arrojado, carteira Ana, sem informar valor disponível. Exemplos confirmados: Amazon AMZN, Alphabet GOOGL, Santander Brasil SANB11, Broadcom AVGO, Solana SOL e Bitcoin BTC. Depois solicitou estudar também ordens automáticas reais. Isso autoriza o estudo, não a conexão a uma conta nem o envio de operações.

## Entrega funcional

`/avaliacao/?carteira=ana` ou `bruno`, protegida pela sessão existente. Cenários, premissas e avisos são separados por usuário e carteira. Leituras não criam preferências nem alteram posições. Ana inicia com perfil arrojado e parâmetros de exemplo editáveis (25% por ativo, 2% de perda simulada do plano, queda de 15%, tolerância técnica 2%, margem de referência 20%). Horizonte e orçamento ficam vazios até preenchimento. Mudar o perfil aplica exemplos nos campos, sem salvar automaticamente. Esses presets não são classificação formal de suitability nem parâmetros recomendados universais.

Fundamentos, preço médio e valor econômico são tratados separadamente. O custo da posição não determina atratividade de nova compra. Cotação expirada, moeda incompatível, horário futuro, demonstração, histórico insuficiente ou salto de 20% em ações bloqueiam sinais de entrada. Não se inventa dado ausente. No longo prazo, referência manual identificada ou alvo recente com pelo menos cinco analistas é uma hipótese, não um preço justo garantido. Sem referência, mostrar Estudar valor e tese. Projeções USD não são aplicadas a BDRs em BRL. P/L e ROE de empresas não são aplicados a BTC/SOL.

No cenário tático, uma entrada exige: MM20 ≥ MM50; preço ≥ MM50; RSI(14) entre 35 e 65; preço ≤ MM20 × (1 + tolerância); ausência dos bloqueios e dos sinais de rentabilidade negativa disponíveis. Esse conjunto é uma regra ilustrativa transparente; não é estratégia validada nem probabilidade de valorização. Os indicadores usam fechamentos completos de uma mesma fonte e moeda. Cripto em BRL tem preço e série BTCBRL/SOLBRL diretos em tabelas próprias, sem tratar USDT como USD. O coletor atualiza essas referências a cada cinco minutos e históricos até uma vez por dia. As séries USD/USDT originais ficam intactas.

O plano distribui dinheiro uma vez por moeda entre os candidatos listados. Quantidade = mínimo entre dinheiro restante / preço, espaço no limite do ativo / preço e perda restante / perda unitária simulada. Base de concentração e perda = valor de mercado da carteira nesta moeda + orçamento novo. Perda unitária = preço × queda do cenário. B3 usa inteiro, EUA frações de cinco casas e cripto oito; são estimativas, não validação de lote mínimo da corretora. Perda agregada e custo agregado têm invariantes de limite. Gaps, taxas, impostos, spread e slippage não estão incorporados e podem ampliar perdas reais. Moedas não são somadas nem convertidas como custo histórico.

Redução sugerida por concentração é apenas a quantidade para o limite configurado, arredondada para cima e limitada à posição efetivamente existente. Não conclui que a empresa vai cair, não cria vendas e não simula receita disponível para compras. O usuário pode comparar com diluição por novos aportes.

Monitor é opt-in, inicialmente desativado. Apenas após cenário válido e orçamento informado pode ser salvo ativo. Verificação a cada cinco minutos pelo coletor, com um aviso por usuário/carteira/ativo/sinal/dia. Avisos são internos ao portal; não enviam Telegram, email, push externo ou ordens. Tela consulta novamente a cada 60 segundos, pausando durante edição ou detalhes abertos. Mac/Docker precisam continuar funcionando.

## Estudo de execução real — não implementada

Não há rota de envio de ordens, adaptador de execução, campo de credencial de trading ou autorização de conta nesta entrega. Nenhuma configuração do cenário habilita operações reais.

| Mercado | Caminho possível | Situação nesta entrega |
| --- | --- | --- |
| B3 / BTG | Plataforma de automação compatível com a conta, ou API de ordens documentada e contratada | Acesso a ordens não verificado; Data Services é dados e não prova acesso para execução. Brapi com atraso de ~30 min não sustenta reação instantânea. Compatibilidade Mac exige validação: instalador Trader Desk recebido pelo usuário era Windows. |
| EUA / Avenue | Acesso de execução explicitamente autorizado pela Avenue, inclusive regras de fracionárias | Não há acesso nem integração confirmados. Documentos contratuais que mencionam API não comprovam liberação pública ao cliente. |
| BTC / SOL | API spot da corretora onde o usuário efetivamente decidir operar, com ambiente de testes quando disponível | Binance tem documentação pública de execução e testnet, mas atualmente é apenas referência pública de preço do portal. Não se presume que os ativos estejam custodiados na Binance. |

Antes de uma futura ativação: definir corretora e capital separado por titular; obter documentação e autorização; usar credenciais dedicadas com menor permissão necessária, sem saque; testar conexão e reconciliação em sandbox/simulação; definir e revisar regras; backtest fora da amostra e walk-forward incluindo custos, slippage, liquidez e eventos societários; limitar exposição, perda diária, frequência e saldo; identificador único persistente de ordens; nunca reenviar uma ordem com resultado incerto sem consultar seu estado; conciliar execuções parciais; invalidar sinais antigos; suspender em falha de fonte, conexão ou relógio; botão de parada e registro auditável. Só depois considerar teste real delimitado, mediante nova autorização explícita. Nada disso garante lucro ou elimina perdas.

Plataformas existentes: TradingView oferece alertas e webhooks, que não são por si uma ordem executada. Profit/Nelogica oferece automação em simulação e conta real mediante contratação e compatibilidade. Esta entrega não contrata nenhum desses serviços.

## Fontes primárias consultadas

- CVM, perfil e objetivos: https://www.gov.br/investidor/pt-br/investir/antes-de-investir/respeite-o-seu-perfil-de-investidor
- B3, valuation: https://borainvestir.b3.com.br/tipos-de-investimentos/renda-variavel/acoes/esta-acao-esta-barata-ou-vale-pouco-mesmo-entenda-o-que-e-valuation/
- TradingView, alertas: https://www.tradingview.com/support/solutions/43000595315-how-to-set-up-alerts/
- TradingView, webhooks: https://www.tradingview.com/support/solutions/43000529348-how-to-configure-webhook-alerts/
- Nelogica, automação: https://ajuda.nelogica.com.br/hc/pt-br/articles/8865257831195-Como-utilizar-a-Automa%C3%A7%C3%A3o-de-Estrat%C3%A9gias
- Binance, interfaces e ambientes: https://developers.binance.com/en/docs/introduction
- Binance, tipos de permissão: https://developers.binance.com/en/docs/products/spot/rest-api
- Avenue, envio de ordens pelo Home Broker: https://avenue.us/duvidas/como-posso-enviar-uma-ordem/

As credenciais existentes de cotações não foram expostas. Usuários, permissões, DNS, Cloudflare, negociações, saldos, quantidades, preços médios, custos e caixa reais devem permanecer idênticos antes/depois da publicação. Tabelas novas usam default_permissions=(), sem criar permissões de execução.

## Conversa, voz e fontes (revisão após a prévia)

A entrada principal passou a ser uma conversa. Com IA desligada, há consultas locais limitadas e identificadas como regras: tickers, nomes dos exemplos, orçamento numérico e horizonte. A simulação fica na sessão, separada por titular, e não grava preferências, compras ou posições. Histórico da IA é limitado às seis últimas mensagens na sessão; o navegador não fornece mensagens de sistema. Limpar conversa inicia novo cenário na próxima consulta. A atualização da página pausa durante conversa/edição.

A integração real usa a Responses API OpenAI por HTTPS com httpx; nenhuma assinatura nova é ativada. Exige OPENAI_API_KEY **e** PORTAL_AI_ENABLED=true após aprovação de custos. O modelo inicial configurável é gpt-6.1-sol. Um único recurso de ferramenta consulta dados e calcula cenários; não há ferramenta de ordens, escrita de posição, envio de mensagem externa ou instalação. store=false; não é declaração de retenção zero do fornecedor. Consultas ativadas enviam o texto e os dados financeiros necessários ao provedor, portanto a ativação deve explicar esse processamento. Até 20 turnos por usuário/dia reservados atomicamente no banco, no máximo três chamadas e 1.800 tokens de saída por chamada. Isso é limite de uso, não um teto monetário exato. Um teto de gasto deve ser configurado/monitorado também no projeto do fornecedor antes da ativação. Falhas não acionam novos provedores pagos nem expõem respostas/segredos.

Ditado e leitura usam recursos do navegador, quando disponíveis. O microfone inicia somente ao clicar; o texto reconhecido precisa ser enviado pelo usuário. O reconhecimento do navegador pode usar um serviço remoto próprio. Não se envia áudio à API OpenAI nesta versão.

Fontes: brapi Pro publica atualização de cinco minutos, sem tempo real, com oferta anual de R$ 1.399,90 (equivalente mensal R$ 116,66), consultada em 09/11/2026. Cedro oferece streaming B3 mediante credenciais/licença e proposta, sem preço público confirmado. BTG Data Services existe, mas o usuário ainda aguarda liberação externa. Nenhuma integração Cedro/BTG foi ativada ou considerada já conectada. Nos EUA, a coleta existente é mantida sem declarar latência/cobertura não verificadas.

BTCBRL, SOLBRL, BTCUSDT e SOLUSDT têm fluxo público Binance WebSocket, iniciado apenas no processo contínuo do coletor, com trava PostgreSQL para impedir conexão duplicada. Não inicia em workers web, comandos unitários ou modo demo. Amostra persistida a cada 15 segundos; reconexão com espera progressiva até cinco minutos. REST e histórico continuam como alternativa. Mensagens com símbolo desconhecido, preço inválido, data futura ou mais de 60 segundos são descartadas. BRL e USDT são persistidos separadamente; o estado de recebimento só fica saudável por 60 segundos após amostra válida. O fluxo não envia ordens nem possui chave privada. Desligar: PORTAL_CRYPTO_STREAM=false.

Referências adicionais: https://developers.openai.com/api/docs/guides/function-calling ; https://developers.openai.com/api/docs/pricing ; https://brapi.dev/pricing ; https://ajuda.cedrotech.com/market-data/quais-sao-as-apis-do-market-data-cedro/ ; https://dataservices.btgpactual.com/site/contact ; https://developers.binance.com/docs/binance-spot-api-docs/web-socket-streams ; https://websockets.readthedocs.io/en/15.0.1/reference/sync/client.html

## Acompanhamento solicitado — revisão 1.6.1

Usuário confirmou oito ativos (T1TW34, NVDC34, SANB11, AMZN, GOOGL, AVGO, SOL e BTC) para Ana e Bruno e Telegram. Não confirmou caixa livre nem teto de aporte: “depende do mercado”. Isso não autoriza inventar orçamento, perfil de Bruno ou quantidades. A captura da boleta contém saldo projetado negativo e precisa de conferência de caixa/liquidações na corretora. As ordens de NVDC34 mostradas têm 100 executados a BRL 20,61 e 100 abertos a BRL 20,30; não são 200 executados. Não se importam essas ordens nesta entrega de acompanhamento.

TechnicalWatch é um acompanhamento de condições técnicas de mercado, separado do cenário pessoal e de qualquer compra. Os critérios usam a mesma leitura técnica já explicada: MM20 ≥ MM50, preço ≥ MM50, RSI14 entre 35 e 65, preço até 2% acima da MM20; cotações e séries válidas e sem bloqueio por fundamentos. Isso não constitui backtest, previsão, preço justo ou recomendação individual de compra/venda. Quantidades permanecem indisponíveis até confirmar orçamento e limites individuais; não reaproveita dinheiro entre ativos ou moedas.

Verificação a cada cinco minutos no coletor; fonte de preço mantém atraso declarado. Telegram somente em transição para condição atendida ou quando deixa de ser atendida, com carência de seis horas por ativo e deduplicação diária. Lacunas não disparam nem rearmam sinais. Uma mensagem identifica Ana e Bruno juntas quando ambas acompanham o mesmo ativo; registro interno é separado por carteira. Pausa/retomada pela página de avaliação, disponível ao usuário responsável. Não utiliza API de IA paga, não altera ordens, saldos ou posições. T1TW34 pode ser criado apenas como instrumento de acompanhamento, sem compra ou custódia inferida.
