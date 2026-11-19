"""Shared portal navigation for React and server-rendered management pages."""
from functools import lru_cache
from pathlib import Path
import re

from django.conf import settings

RESOURCES = (
    ('/avaliacao/', 'Avaliar compra e venda', 'market'),
    ('/planejamento/', 'Planejamento', 'shield'),
    ('/alertas/', 'Alertas', 'bell'),
    ('/noticias/', 'Notícias', 'news'),
    ('/mercado/', 'Mercado', 'market'),
    ('/ativos/importar/', 'Importar compras', 'upload'),
    ('/ativos/', 'Gerenciar ativos', 'assets'),
)


@lru_cache(maxsize=1)
def stylesheet():
    html = (Path(settings.BASE_DIR) / 'apps/react_portal/static/react_portal/index.html').read_text()
    match = re.search(r'href="(/static/react_portal/assets/[^"<>]+\.css)"', html)
    if not match:
        raise RuntimeError('Construa a interface React antes de iniciar o portal.')
    return match.group(1)


def portal_chrome(request):
    path = request.path
    selected = 'bruno' if path.startswith('/carteiras/bruno/') or path.startswith('/avaliacao/') and request.GET.get('carteira')=='bruno' else 'ana'
    active = next((url for url, _, _ in RESOURCES if path.startswith(url)), '')
    if path.startswith('/favoritos/'):
        active = '/mercado/'
    if (path.startswith('/compras/') and not path.startswith('/compras/registrar/')) or path.startswith('/comparar/'):
        active = '/ativos/'
    title = next((label for url, label, _ in RESOURCES if url == active), 'Portal de Investimentos')
    if path.startswith('/compras/registrar/'):
        title = 'Registrar compra'
        active = ''
    return {
        'resource_portfolio_ana_url': '/avaliacao/?carteira=ana' if path.startswith('/avaliacao/') else '/?carteira=ana#/carteira',
        'resource_portfolio_bruno_url': '/avaliacao/?carteira=bruno' if path.startswith('/avaliacao/') else '/?carteira=bruno#/carteira',
        'selected_portfolio': selected,
        'selected_portfolio_name': 'Bruno' if selected=='bruno' else 'Ana',
        'portfolio_home': '/?carteira='+selected,
        'portfolio_purchase_url': '/carteiras/bruno/compras/registrar/' if selected=='bruno' else '/compras/registrar/',
        'purchase_active': path.startswith('/compras/registrar/'),
        'resource_navigation': [{'url': url+'?carteira='+selected if url=='/avaliacao/' else url, 'label': label, 'icon': icon, 'active': url == active}
                                for url, label, icon in RESOURCES if url in ('/noticias/','/mercado/','/alertas/')],
        'resource_administration': [{'url':url,'label':label,'icon':icon,'active':url==active} for url,label,icon in RESOURCES if url in ('/ativos/importar/','/ativos/')] if selected=='ana' else [],
        'resource_title': title,
        'resource_stylesheet': stylesheet(),
        'resource_initial': request.user.get_username()[:1].upper() if request.user.is_authenticated else 'I',
    }
