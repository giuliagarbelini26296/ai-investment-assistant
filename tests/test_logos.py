import datetime as dt
from unittest import mock

import httpx
import pytest
from django.test import Client
from django.utils import timezone

from apps.react_portal import logos
from apps.react_portal.models import AssetLogo

BOM = '<svg xmlns="http://www.w3.org/2000/svg" width="56" height="56"><path fill="url(#a)" d="M0 0h56v56H0z"/><defs><linearGradient id="a"/></defs></svg>'


def resposta(status=200, texto=BOM, tipo='image/svg+xml'):
    return httpx.Response(status, text=texto, headers={'content-type': tipo})


@pytest.mark.parametrize('ruim', [
    '<svg><script>alert(1)</script></svg>',
    '<svg onload="alert(1)"><path/></svg>',
    '<svg><a href="https://x.com"><path/></a></svg>',
    '<svg><image href="https://x.com/a.png"/></svg>',
    '<svg><path style="fill:url(https://x.com/y)"/></svg>',
    '<svg><foreignObject><div/></foreignObject></svg>',
    '<!DOCTYPE svg [<!ENTITY x "y">]><svg></svg>',
    '<html>404</html>',
])
def test_svg_inseguro_e_recusado(ruim):
    assert logos.svg_seguro(ruim) is None


def test_svg_bom_e_aceito_com_gradiente_interno():
    assert logos.svg_seguro('<?xml version="1.0"?>' + BOM) == BOM


def test_candidatos_incluem_bdr():
    assert logos.candidatos('CHKP') == ['CHKP', 'CHKP34', 'C1HK34']
    assert logos.candidatos('ITUB4') == ['ITUB4']


@pytest.mark.django_db
def test_busca_tenta_bdr_e_guarda_em_cache():
    client = mock.Mock()
    client.get.side_effect = [resposta(404, '<html/>', 'text/html'), resposta(404, '<html/>', 'text/html'), resposta()]
    item = logos.logo_do_ativo('CHKP', client=client)
    assert item.found and item.source_symbol == 'C1HK34'
    client.get.reset_mock()
    assert logos.logo_do_ativo('CHKP', client=client).found and not client.get.called  # cache


@pytest.mark.django_db
def test_ausencia_fica_em_cache_e_falha_de_rede_nao():
    falha = mock.Mock(); falha.get.side_effect = httpx.ConnectError('x')
    assert logos.logo_do_ativo('ABCD3', client=falha) is None and not AssetLogo.objects.exists()
    nada = mock.Mock(); nada.get.return_value = resposta(404, '<html/>', 'text/html')
    assert not logos.logo_do_ativo('ABCD3', client=nada).found
    depois = timezone.now() + dt.timedelta(days=8)
    nada.get.return_value = resposta()
    assert logos.logo_do_ativo('ABCD3', agora=depois, client=nada).found  # revalida após 7 dias


@pytest.mark.django_db
def test_view_exige_login_e_serve_com_cabecalhos_seguros(cliente_logado):
    AssetLogo.objects.create(symbol='ITUB4', svg=BOM, found=True, source_symbol='ITUB4', fetched_at=timezone.now())
    assert Client().get('/ativos/logo/ITUB4.svg').status_code in (401, 302)  # cliente novo, sem sessão
    r = cliente_logado.get('/ativos/logo/itub4.svg')
    assert r.status_code == 200 and r['Content-Type'] == 'image/svg+xml'
    assert 'sandbox' in r['Content-Security-Policy'] and r['X-Content-Type-Options'] == 'nosniff'
    assert cliente_logado.get('/ativos/logo/../etc.svg').status_code == 404
