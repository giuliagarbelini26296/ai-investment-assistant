from decimal import Decimal

import pytest
from django.contrib import messages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory
from django.urls import reverse

from apps.alerts.models import Rule
from apps.portfolio.models import Asset, Lot, PositionBalance
from apps.react_portal.views import shell


@pytest.mark.parametrize('url,active', [
    ('/alertas/', '/alertas/'), ('/noticias/', '/noticias/'), ('/mercado/', '/mercado/'),
    ('/ativos/importar/', '/ativos/importar/'), ('/ativos/', '/ativos/'),
    ('/favoritos/', '/mercado/'),
    ('/ativos/novo/', '/ativos/'), ('/alertas/regras/nova/', '/alertas/'),
])
def test_resources_share_portal_and_protected_forms(cliente_logado, url, active):
    response = cliente_logado.get(url)
    html = response.content.decode()
    assert response.status_code == 200
    assert 'id="resource-sidebar"' in html and 'id="resource-content"' in html
    assert 'portal_resources/resources' in html
    assert 'class="topo"' not in html
    assert f'href="{active}" class="active" aria-current="page"' in html
    assert 'target="_blank"' not in html.split('aria-label="Recursos do portal"')[1].split('</nav>')[0]
    assert 'csrfmiddlewaretoken' in html
    assert '/static/react_portal/assets/' in html


@pytest.mark.parametrize('url', ['/alertas/', '/noticias/', '/mercado/', '/ativos/importar/', '/ativos/', '/classico/'])
def test_resources_still_require_login(client, db, url):
    assert client.get(url).status_code == 302


def test_import_preview_is_safe_and_stays_in_portal(cliente_logado):
    counts = (Asset.objects.count(), Lot.objects.count(), PositionBalance.objects.count())
    upload = SimpleUploadedFile('conferencia.csv',
        b'simbolo,data,quantidade,preco,moeda,mercado,custodia\nTEST,2026-11-04,1.5,100,USD,NASDAQ,AVENUE\n',
        content_type='text/csv')
    response = cliente_logado.post('/ativos/importar/', {'arquivo': upload, 'custodia':'AVENUE'})
    html = response.content.decode()
    assert response.status_code == 200
    assert 'id="resource-sidebar"' in html and 'Confirmar 1 compra(s)' in html
    assert 'TEST' in html
    assert counts == (Asset.objects.count(), Lot.objects.count(), PositionBalance.objects.count())
    # Discard preserves the two-step workflow and does not import positions.
    assert cliente_logado.post('/ativos/importar/', {'acao':'cancelar'}).status_code == 302
    assert Lot.objects.count() == counts[1]


def test_forms_keep_validation_and_audit_on_temporary_database(cliente_logado, usuario):
    before = Asset.objects.count()
    response = cliente_logado.post('/ativos/novo/', {'symbol':''})
    assert response.status_code == 200 and response.context['form'].errors
    assert Asset.objects.count() == before
    response = cliente_logado.post('/ativos/novo/', {
        'symbol':'TEST', 'name':'Ativo de teste', 'market':'NASDAQ', 'custody':'AVENUE',
        'is_active':'on',
    })
    assert response.status_code == 302
    asset = Asset.objects.get(symbol='TEST')
    response = cliente_logado.post(reverse('lot_create',args=[asset.pk]), {
        'trade_date':'2026-11-04', 'quantity':'1.5', 'unit_price':'100', 'costs':'0', 'notes':'Teste isolado',
    })
    assert response.status_code == 302
    lot = Lot.objects.get(asset=asset)
    assert lot.quantity == Decimal('1.5') and lot.created_by == usuario
    assert cliente_logado.get(reverse('asset_detail',args=[asset.pk])).status_code == 200


def test_flash_messages_survive_return_to_react(cliente_logado):
    # Use the existing message-producing view, without changing financial records.
    from django.contrib.messages.storage.fallback import FallbackStorage
    request = RequestFactory().get('/')
    request.user = cliente_logado.session.get('_auth_user_id')
    from django.contrib.auth import get_user_model
    request.user = get_user_model().objects.get(pk=request.user)
    request.session = {}
    request._messages = FallbackStorage(request)
    messages.success(request, 'Importação conferida <segura>')
    response = shell(request)
    html = response.content.decode()
    assert 'id="id3-notices"' in html
    assert '\\u003Csegura\\u003E' in html
    assert '<segura>' not in html


def test_no_business_mutations_on_resource_gets(cliente_logado):
    before = (Asset.objects.count(), Lot.objects.count(), PositionBalance.objects.count(), Rule.objects.count())
    for url in ['/alertas/','/noticias/','/mercado/','/ativos/','/classico/']:
        assert cliente_logado.get(url).status_code == 200
    after = (Asset.objects.count(), Lot.objects.count(), PositionBalance.objects.count(), Rule.objects.count())
    assert before == after
