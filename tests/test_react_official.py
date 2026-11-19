import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse


def test_official_home_alias_and_legacy(cliente_logado):
    assert reverse('home') == '/'
    assert reverse('home_classico') == '/classico/'
    for url in ('/', '/nova/'):
        response = cliente_logado.get(url)
        html = response.content.decode()
        assert response.status_code == 200
        assert 'csrf-token' in html and '/static/react_portal/assets/' in html
        assert 'no-store' in response['Cache-Control']
        assert 'AMBIENTE DE TESTE' not in html
    assert cliente_logado.get('/classico/').status_code == 200


def test_official_api_same_origin_and_demo_label(cliente_logado, settings):
    body = cliente_logado.get('/api/v1/portfolio/').json()
    assert body['meta']['legacyUrl'] == ''
    settings.PORTAL = {**settings.PORTAL, 'MODO_DEMO': True}
    assert cliente_logado.get('/api/v1/portfolio/').json()['meta']['demo'] is True
    settings.PORTAL = {**settings.PORTAL, 'MODO_DEMO': False}
    assert cliente_logado.get('/api/v1/portfolio/').json()['meta']['demo'] is False


def test_existing_login_csrf_standard_session_and_logout(usuario):
    client = Client(enforce_csrf_checks=True)
    before = get_user_model().objects.count()
    assert client.post('/conta/entrar/', {'username': 'ana', 'password': 'senha-de-teste-123456'}).status_code == 403
    html = client.get('/conta/entrar/').content.decode()
    assert 'PORTAL DE INVESTIMENTOS' in html and 'INTEGRAÇÃO EM TESTE' not in html
    csrf = client.cookies['csrftoken'].value
    response = client.post('/conta/entrar/', {'username': 'ana', 'password': 'senha-de-teste-123456',
                                            'csrfmiddlewaretoken': csrf})
    assert response.status_code == 302 and response['Location'] == '/'
    assert 'sessionid' in client.cookies and 'id3_preview_session' not in client.cookies
    assert client.get('/api/v1/portfolio/').status_code == 200
    usuario.refresh_from_db()
    assert usuario.last_login is not None  # Normal Django authentication remains intact.
    assert get_user_model().objects.count() == before
    assert client.post('/conta/sair/').status_code == 403
    csrf = client.cookies['csrftoken'].value
    assert client.post('/conta/sair/', {'csrfmiddlewaretoken': csrf}).status_code == 302
    assert client.get('/api/v1/portfolio/').status_code == 401


def test_invalid_login_redirect_and_inactive_user(usuario):
    client = Client()
    assert client.post('/conta/entrar/', {'username': 'ana', 'password': 'incorrect'}).status_code == 200
    assert client.get('/api/v1/portfolio/').status_code == 401
    response = client.post('/conta/entrar/', {'username': 'ana', 'password': 'senha-de-teste-123456',
                                           'next': 'https://outside.invalid/'})
    assert response.status_code == 302 and response['Location'] == '/'
    assert client.get('/conta/sair/').status_code == 405
    usuario.is_active = False
    usuario.save(update_fields=['is_active'])
    assert client.get('/api/v1/portfolio/').status_code == 401


@pytest.mark.parametrize('name', ['alertas', 'noticias', 'asset_list', 'importar_compras', 'mercado'])
def test_management_routes_preserved(cliente_logado, name):
    # No mutation is performed; management forms remain protected and available.
    if name == 'noticias':
        url = '/noticias/'
    else:
        url = reverse(name)
    assert cliente_logado.get(url).status_code == 200


def test_favicon_available_without_login(client):
    response = client.get("/favicon.ico")
    assert response.status_code == 301
    assert response["Location"] == "/static/react_portal/favicon.svg"
