# Planejamento

/planejamento/ reúne qualidade, concentração, agenda, registros complementares, simulação e comparação. Não executa ordens nem envia notificações a terceiros. Carteira compartilhada e autenticação existentes preservadas. Limites são pessoais e editáveis; padrões 35% por empresa, 60% por setor e 80% por país são apenas referências de interface, não recomendações.

Pesos de empresas/setores são por moeda. Se faltar cotação de qualquer ativo do grupo, usa o custo de todo o grupo, identificado. País usa valor de mercado convertido somente com câmbio recente identificado; não calcula lucro cambial usando câmbio atual como custo antigo.

Qualidade confere ausência de fechamento anterior, horários futuros, divergência >1% contra histórico e saltos >=20% nos últimos 65 candles. Saltos não provam erro e nunca alteram posições.

CashEntry é um ledger complementar imutável com cancelamento lógico, referência de extrato, autor e UUID idempotente. Não cria dividendos a partir de rentabilidades percentuais. Lucro/prejuízo realizado é informado manualmente, não representa venda nem altera ações. Impostos são despesas efetivas, sem cálculo fiscal. Taxas já preenchidas em lotes ativos são incluídas uma vez; o usuário não deve repeti-las. Ausência de registros significa cobertura parcial, nunca histórico completo de recebimentos.

Efeito cambial só é calculado quando todos os custos de aquisição em BRL são conhecidos. No saldo reconciliado de Avenue essa base continua desconhecida; mostra apenas o valor atual equivalente, com câmbio e data.

Comparação usa BOVA11/BRL e SPY/USD como referências de preço, não índices diretos nem retorno total de ETFs. Coleta diária pelos adaptadores existentes, sem adicionar ativos à carteira e com retentativa diária limitada. Nenhuma chamada externa no GET das páginas. Série começa no último saldo conciliado mais recente e usa apenas fechamentos comuns completos. Modified Dietz é encadeado por intervalo de dias: (V1 - despesas - V0 - compras)/(V0 + compras ponderadas por dias). Convenção de compra ao fim do dia; não conhece fluxos intradiários. Salto >=20%, revisão/cancelamento posterior ao saldo, resultado realizado sem venda/fluxo ou falta de base bloqueiam comparação e informam por quê. Não reconstrói aquisições desconhecidas.

Agenda prioriza calendários publicados identificados com URL/data de verificação sobre datas de provedor; exporta .ics com dia inteiro e sem inventar horários. Os avisos explicam evidência, risco e conferência dentro do portal; regras e canais existentes não são alterados.

Revisão de rankings: usa a atualidade já definida pelo coletor para excluir cotações vencidas, sem calendário ou sem fechamento anterior confirmado; 0% não é alta nem queda. Cada linha informa pregão e coleta. Em 1 dia, a data-base só aparece se o fechamento anterior informado pela cotação coincide com o candle da sessão anterior, sem confundir ausência do candle de hoje com uma sessão a menos. Ordenação por percentual; universo monitorado, não toda a bolsa. Brasil/EUA/cripto podem se referir a sessões distintas, explicitadas.

Filtro de mercado em Altas e quedas e Visão geral: Todos, Brasil (B3), Internacional (NYSE/Nasdaq). Internacional não inclui cripto. A seleção permanece nos links de período/tipo e nas atualizações automáticas de fragmento.

A atualidade considera a sessão informada nos metadados da fonte/candle, não só a hora da coleta. Se a coleta recente ainda traz o pregão anterior quando o atual abriu, o preço é marcado desatualizado e o resultado No dia fica indisponível na carteira React e na visão clássica até haver cotação atual. Último valor de mercado permanece identificado como referência antiga.
