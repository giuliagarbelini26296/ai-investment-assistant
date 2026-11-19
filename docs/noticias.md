# Central de Notícias

A página `/noticias/` exige o login existente do portal. Reúne matérias da Agência Brasil
(Economia), do Federal Reserve, das notícias gerais do Finnhub e das notícias por empresa já
coletadas pelo portal. A origem, a publicação e o estado de atualização aparecem na página.

## Atualização

O serviço Compose `news` executa `python manage.py coletar_noticias`. A verificação das fontes
ocorre a cada 15 minutos. As notícias por empresa, na rotina existente do Finnhub, são verificadas
a cada 30 minutos, dentro do orçamento compartilhado da fonte. O botão “Atualizar notícias”
solicita outra verificação; o intervalo mínimo de duas verificações é de dois minutos.

Fontes indisponíveis são tentadas novamente depois de cinco minutos. O portal conserva o conteúdo
anterior e informa a indisponibilidade. As páginas somente leem o conteúdo armazenado; não consultam
as fontes nem executam traduções durante a navegação.

A lista aberta no navegador é atualizada a cada minuto, conservando os filtros. A atualização visual
é adiada enquanto um texto original está aberto ou a pessoa está preenchendo os filtros.

O serviço usa uma trava de sessão do PostgreSQL para impedir dois trabalhadores de notícias no mesmo
banco. Nenhum comando, usuário ou credencial de acesso ao portal é alterado por ele. Ele não envia
notícias ao Telegram; o bot e os alertas mantêm o comportamento existente.

## Tradução

As notícias internacionais das fontes atuais são em inglês. Títulos e resumos são traduzidos localmente
para português pelo modelo Argos en-pt 1.9, usando CTranslate2 em CPU. O modelo é carregado apenas pelo
trabalhador de notícias e o resultado é guardado, mantendo título e resumo originais. Não há API de
tradução paga nem chave de tradução. O serviço tem limite de um processador e 1 GB de memória.

O modelo é de português e não garante revisão editorial ou vocabulário exclusivamente brasileiro.
As traduções recebem identificação e acesso ao original. Notícias em português não são traduzidas.
Se a tradução falhar, permanece o original com o estado explícito, e uma nova tentativa acontece
após uma hora. Outros idiomas precisam de um modelo adicional antes de serem tratados como traduzidos.

Modelo: https://argos-net.com/v1/translate-en_pt-1_9.argosmodel

Licença do modelo derivado de OPUS-MT: CC BY 4.0. Autores: Jörg Tiedemann e Santhosh Thottingal.
O arquivo `translation-model/SOURCE.json` registra a origem e o SHA-256 do pacote baixado.

## Preservação e implantação

A migração `noticias.0001_initial` cria tabelas novas: artigos, relação entre artigos e ativos e estado
das fontes. Não modifica lotes, revisões, favoritos, grupos de favoritos, usuários ou alertas.
O vínculo a um ativo considera a notícia por empresa já existente ou uma menção explícita no texto;
não representa uma análise de impacto nem uma recomendação de investimento.

A mesma URL, após remover parâmetros de rastreamento e fragmentos, gera um único artigo na central,
mesmo se aparecer para vários ativos. Os registros antigos de `mercado.NewsItem` permanecem intactos.

Antes de ativar: gerar backup privado, conferir o plano da migração, aplicar somente a migração nova,
carregar as notícias preparadas e reaplicar `web`, `collector` e `news` sem recriar o banco. As imagens
anteriores devem ser preservadas. Para reverter a aplicação, usar essas imagens e parar `news`; as
tabelas novas podem permanecer sem uso, evitando restaurar um backup por cima de alterações posteriores.
