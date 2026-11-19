# Interface oficial React — 04/11/2026

Endereço: https://portal.example.com/ · local: http://127.0.0.1:8000/

A interface React abre na raiz `/`. `/nova/` permanece como alias e `/classico/` mantém a visão anterior. A API somente leitura fica em `/api/v1/portfolio/`. O login usa as sessões, usuários e permissões atuais do Django. A carteira continua compartilhada conforme o modelo existente. Não há configuração de sessão da prévia em produção.

Os cálculos reutilizam serviços existentes e Decimal; filtros e totais são calculados no backend. Brasil/BTG em BRL e EUA/Avenue em USD ficam separados. Cotações mantêm fonte e horário; a tela consulta o banco a cada 60 segundos, respeitando a disponibilidade dos coletores. Keycloak/OIDC não foi ativado.

Alertas, notícias, Mercado/Favoritos, importação, gestão de ativos e edição de operações continuam disponíveis. A interface anterior não foi removida. Banco, DNS, Cloudflare e permissões não foram alterados. Apenas o serviço web foi recriado, sem execução de migrações.

## Construção

```sh
cd frontend
npm ci
npm run build
cd ..
docker compose build web
docker compose up -d --no-deps --no-build web
```

O Dockerfile original inclui os assets compilados e as dependências existentes; não depende da imagem da prévia. `docker-compose.yml` verifica `/saude/`. Guardar a imagem atual antes de novas atualizações.

## Reversão desta publicação

A imagem anterior foi preservada como `portal-acoes-web:antes-react-oficial-20261104`. Os arquivos modificados têm backup e manifesto na entrega desta publicação. O procedimento de reversão restaura somente código e a imagem web, sem restaurar ou sobrescrever o banco. O backup PostgreSQL é uma proteção separada, não deve ser restaurado automaticamente após novas operações da carteira.

Acesso externo continua protegido pelo Cloudflare Access. Uma requisição sem sessão pode receber desafio/bloqueio de automação ou redirecionamento para o login. Essa camada não foi modificada nesta publicação.

## Recursos integrados — 1.2.0-react

As páginas dos seis recursos e suas subpáginas agora compartilham a navegação e os temas da carteira, na mesma aba. Formulários e regras do Django preservados. Detalhes em `docs/recursos-integrados.md`.
