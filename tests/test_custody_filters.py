"""Corretoras e lucro/prejuízo com custo de aquisição cambial desconhecido."""
import datetime as dt
import re
from decimal import Decimal as D

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.marketdata.models import Quote
from apps.portfolio import services
from apps.portfolio.forms import AssetForm
from apps.portfolio.models import Asset, Custody


@pytest.fixture
def positions(db):
    out = []
    for symbol, market, broker, average, price in [
        ('TTWO', 'NASDAQ', 'AVENUE', '214.57', '194.99'),
        ('BPAC11', 'B3', 'BTG', '50.74', '50.98'),
        ('CHKP', 'NASDAQ', 'AVENUE', '193.56', None),
    ]:
        asset = Asset.objects.create(symbol=symbol, name=symbol, market=market, custody=broker)
        services.registrar_saldo(asset, reference_date=dt.date(2026, 11, 3), quantity=D('2'),
            average_price=D(average), source_key='teste-corretoras', evidence='Saldo confirmado.')
        if price:
            Quote.objects.create(asset=asset, price=D(price), previous_close=D(price),
                currency=asset.native_currency, source='twelvedata' if market == 'NASDAQ' else 'brapi',
                quote_type='nao_declarado', collected_at=timezone.now())
        out.append(asset)
    return out


def test_corretora_btg_disponivel_no_cadastro(positions):
    assert Custody.BTG == 'BTG'
    assert ('BTG', 'BTG Investimentos') in list(AssetForm().fields['custody'].choices)
    positions[1].full_clean()


@pytest.mark.parametrize('currency', ['BRL', 'USD'])
def test_filtros_sem_base_cambial_usam_resultado_nativo(cliente_logado, positions, currency):
    session = cliente_logado.session
    session['moeda_visao'] = currency
    session.save()
    html = cliente_logado.get(reverse('carteira_fragmento')).content.decode()
    assert 'data-filtro="custodia:AVENUE">Avenue' in html
    assert 'data-filtro="custodia:BTG">BTG Investimentos' in html
    assert 'data-filtro="custodia:BINANCE"' not in html
    for symbol, status, broker in [('TTWO', 'prejuizo', 'AVENUE'), ('BPAC11', 'lucro', 'BTG'), ('CHKP', 'sem_preco', 'AVENUE')]:
        row = re.search(r'<tr class="linha-ativo"[^>]*data-simbolo="' + symbol + r'"[^>]*>', html).group()
        assert f'data-situacao="{status}"' in row
        assert f'data-custodia="{broker}"' in row
    # A perda é filtrável, mas a conversão sem custo cambial continua indisponível.
    if currency == 'BRL':
        lines = services.visao_da_carteira(currency).linhas
        ttwo = next(line for line in lines if line.asset.symbol == 'TTWO')
        assert ttwo.sem_base_cambial and ttwo.resultado_visao is None
        assert ttwo.resultado_para_filtro == D('-39.16')
