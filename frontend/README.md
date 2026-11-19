# Portal de Investimentos — interface oficial

Interface React servida pelo Django na página inicial do portal, com a sessão e os usuários já existentes. A API autenticada fica em `/api/v1/portfolio/`; não há arquivo de demonstração de posições.

```sh
npm ci
npm run build
node --test tests/finance.test.mjs
```

A saída fica em `../apps/react_portal/static/react_portal/` e é incluída pelo Dockerfile do projeto. Posições, filtros e totais são calculados no backend com Decimal. O frontend formata dados e desenha os gráficos. Os fechamentos usados na comparação partem da primeira sessão comum.

A visão anterior continua em `/classico/`; gestão, importação, notícias, Mercado e alertas continuam nas rotas existentes. Keycloak/OIDC não foi ativado. Consulte `../docs/react-oficial.md`.
