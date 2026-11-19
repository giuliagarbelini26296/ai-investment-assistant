# Recursos integrados ao portal — versão 1.2.0-react

Alertas, Notícias, Mercado, Importar compras, Gerenciar ativos e Visão clássica abrem na mesma aba, com a navegação, cabeçalho e temas da carteira. As subpáginas e os formulários mantêm essa apresentação.

Os handlers, validações, permissões, scripts e regras existentes do Django foram preservados. A carteira usa React; as páginas de recursos usam templates Django no mesmo layout visual. Não foi criado outro banco ou um novo cadastro de usuários.

A base compartilhada é `apps/react_portal/templates/react_portal/resource_base.html`, herdada por `ui/base.html`. O contexto de navegação e o CSS compilado da carteira vêm de `apps/react_portal/navigation.py`. Os estilos e controles de adaptação ficam em `static/portal_resources/`, separados da pasta de saída do Vite para não serem apagados na construção.

Temas são sincronizados pela preferência `id3-theme`. O menu lateral funciona no celular e as tabelas têm rolagem interna. Links internos usam a mesma aba; links externos de notícias permanecem referências externas.

A importação mantém prévia, confirmação explícita e descarte. As mensagens dos formulários redirecionados à carteira são transportadas por JSON escapado e exibidas no React. Não houve importação ou edição de posições nos testes com o banco real.

Construção e publicação seguem o Dockerfile e Compose existentes. Nesta entrega, somente o serviço web é recriado, sem migrações do banco. A imagem anterior fica guardada como `portal-acoes-web:antes-recursos-integrados-20261104`, com backup do código e manifesto dos arquivos para reversão.
