import datetime as dt
from decimal import Decimal as D

import pytest
from django.test import Client
from django.utils import timezone

from apps.marketdata.models import Quote
from apps.portfolio.models import Asset


def _q(asset, quando, preco, fonte='brapi', status='valida'):
    return Quote.objects.create(asset=asset, price=D(preco), currency='BRL', previous_close=D('10'), market_time=quando,
                                collected_at=quando, source=fonte, quote_type='delayed', declared_delay_minutes=30, status=status)


@pytest.mark.django_db
def test_intraday_traz_o_ultimo_pregao_sem_repetir_e_exige_login(cliente_logado):
    a = Asset.objects.create(symbol='ABCD3', name='x', market='B3', custody='BTG')
    ontem = timezone.make_aware(dt.datetime(2026, 11, 18, 16, 0))
    hoje = timezone.make_aware(dt.datetime(2026, 11, 19, 10, 0))
    _q(a, ontem, '9')
    _q(a, hoje, '10.5')
    _q(a, hoje + dt.timedelta(minutes=30), '11')
    _q(a, hoje + dt.timedelta(minutes=60), '99', status='descartada')
    assert Client().get('/api/v1/intraday/?symbol=ABCD3').status_code in (302, 401)
    body = cliente_logado.get('/api/v1/intraday/?symbol=ABCD3').json()
    assert body['date'] == '2026-11-19' and [p['price'] for p in body['points']] == ['10.50000000', '11.00000000']
    assert body['previousClose'] == '10.00000000' and body['delayMinutes'] == 30
    assert cliente_logado.get('/api/v1/intraday/?symbol=ZZZZ9').status_code == 404
    assert cliente_logado.get('/api/v1/intraday/?symbol=../x').status_code == 400


def test_amostra_limita_pontos():
    from apps.react_portal.intraday import _amostra
    pts = list(range(1000))
    a = _amostra(pts, 240)
    assert len(a) == 240 and a[0] == 0 and a[-1] == 999
