import datetime as dt
import json
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.utils import timezone
from django.test import override_settings
from apps.insights import live_alpaca as stream
from apps.marketdata.models import Quote
from apps.portfolio.models import Asset, Lot, PositionBalance

pytestmark = pytest.mark.django_db


def bar(symbol='TTWO', price=200, when=None):
    return json.dumps([{'T':'b','S':symbol,'c':price,'t':(when or timezone.now()).isoformat()}])


@pytest.mark.parametrize('raw', ['[]','{}','null','invalid',bar('T1TW34'),bar(price=-1),
    bar(price='NaN'),bar(when=timezone.now()-dt.timedelta(minutes=5)),bar(when=timezone.now()+dt.timedelta(minutes=1))])
def test_invalid_data(raw):
    assert stream.decode(raw) == []


def test_correct_usd_only_and_no_holdings(monkeypatch):
    now=timezone.now()
    Asset.objects.create(symbol='TTWO',name='Take Two',market='NASDAQ',custody='AVENUE')
    Asset.objects.create(symbol='T1TW34',name='BDR',market='B3',custody='OUTRA')
    fake=SimpleNamespace(tz=dt.UTC, situacao=lambda when:SimpleNamespace(estado='aberto',sessao=SimpleNamespace(data=now.date())),ultima_sessao_encerrada=lambda when:None)
    monkeypatch.setattr(stream,'calendario',lambda market:fake)
    events=stream.decode(bar(when=now),now)
    assert stream.persist(events,now)==['TTWO']
    assert stream.persist(events,now)==[]
    quote=Quote.objects.get()
    assert quote.asset.symbol=='TTWO' and quote.currency=='USD'
    assert quote.price==Decimal(200) and quote.previous_close is None
    assert quote.raw['coverage']=='partial'
    assert not Lot.objects.exists() and not PositionBalance.objects.exists()


def test_no_stream_without_credentials(monkeypatch):
    monkeypatch.setattr(stream.threading,'Thread',lambda **kw:pytest.fail('Must not connect'))
    with override_settings(PORTAL_ALPACA_STREAM=True,ALPACA_API_KEY='',ALPACA_SECRET_KEY=''):
        stream.start()
