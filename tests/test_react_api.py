import datetime as dt
from decimal import Decimal as D

import pytest
from django.utils import timezone

from apps.marketdata.models import DailyBar, Quote
from apps.portfolio import services
from apps.portfolio.models import Asset, Lot, PositionBalance

ENDPOINT = '/api/v1/portfolio/'
REFERENCE = dt.date(2026, 11, 3)


@pytest.fixture
def positions(db):
    rows = [('BBAS3','B3','BTG','100','16.15','16.19','16.47'),
            ('BPAC11','B3','BTG','300','50.74','50.98','51.52'),
            ('ITUB4','B3','BTG','300','38.28','38.49','38.69'),
            ('SBSP3','B3','BTG','200','23.94','24.27','24.70'),
            ('CHKP','NASDAQ','AVENUE','2','193.56','142.28','141.50'),
            ('TTWO','NASDAQ','AVENUE','100.47592','214.57','194.99','192.11'),
            ('ORCL','NYSE','AVENUE','10','147.45','126.19','119.22')]
    result = {}
    for symbol, market, broker, quantity, average, price, previous in rows:
        asset = Asset.objects.create(symbol=symbol, name=symbol, market=market, custody=broker)
        services.registrar_saldo(asset, reference_date=REFERENCE, quantity=D(quantity),
            average_price=D(average), source_key='fixture', evidence='Fixture de teste isolado.')
        Quote.objects.create(asset=asset, price=D(price), previous_close=D(previous),
            currency=asset.native_currency, source='fixture', quote_type='fechamento',
            collected_at=timezone.now())
        DailyBar.objects.create(asset=asset, session_date=REFERENCE, time_basis='sessao_local',
            close=D(price), source='fixture', collected_at=timezone.now())
        result[symbol] = asset
    Asset.objects.create(symbol='BTC', name='Teste sem posição', market='CRIPTO', custody='BINANCE')
    return result


def test_no_auth_protects_api_shell_history_and_no_cache(client, positions):
    response = client.get(ENDPOINT)
    assert response.status_code == 401
    assert 'positions' not in response.json()
    assert 'no-store' in response['Cache-Control']
    assert client.get('/').status_code == 302
    assert client.get('/nova/').status_code == 302
    assert client.get('/static/react_portal/data/portfolio.json').status_code == 404


def test_native_precision_reference_and_no_writes(cliente_logado, positions):
    before = list(PositionBalance.objects.values())
    body = cliente_logado.get(ENDPOINT).json()
    assert len(body['positions']) == 7
    assert Lot.objects.count() == 0
    assert list(PositionBalance.objects.values()) == before
    totals = {r['currency']: r for r in body['totals']}
    assert D(totals['BRL']['invested']) == D('33109')
    assert D(totals['USD']['invested']) == D('23420.7381544')
    ttwo = next(r for r in body['positions'] if r['symbol'] == 'TTWO')
    assert isinstance(ttwo['quantity'], str)
    assert D(ttwo['quantity']) == D('100.47592')
    assert D(ttwo['averagePrice']) == D('214.57')
    assert ttwo['quoteAt'] is None  # No invented market timestamp from collection time.
    assert ttwo['referenceDate'] == '2026-11-03'
    assert len(ttwo['history']) == 1
    assert body['meta']['accessScope'] == 'shared_portfolio'


def test_parity_with_existing_django_consolidation(cliente_logado, positions):
    body = cliente_logado.get(ENDPOINT).json()
    expected = services.consolidar_por_moeda(services.linhas_da_carteira())
    for total in body['totals']:
        native = expected[total['currency']]
        assert D(total['gain']) == native.resultado
        assert D(total['returnPct']) == native.retorno_pct
        rows = [r for r in body['positions'] if r['currency'] == total['currency']]
        assert D(total['day']) == sum((D(r['quantity']) * (D(r['price']) - D(r['previousClose'])) for r in rows), D(0))
        assert D(total['dayBase']) == sum((D(r['quantity']) * D(r['previousClose']) for r in rows), D(0))
        assert D(total['dayPct']) == D(total['day']) / D(total['dayBase']) * 100


def test_us_loss_filter_native_and_broker_empty(cliente_logado, positions):
    body = cliente_logado.get(ENDPOINT, {'result': 'loss', 'broker': 'AVENUE'}).json()
    assert {r['symbol'] for r in body['positions']} == {'CHKP', 'TTWO', 'ORCL'}
    assert [r['currency'] for r in body['totals']] == ['USD']
    empty = cliente_logado.get(ENDPOINT, {'result': 'loss', 'broker': 'BTG'}).json()
    assert empty['positions'] == [] and empty['totals'] == []


def test_missing_quote_and_previous_close_have_distinct_coverage(cliente_logado, positions):
    Quote.objects.filter(asset=positions['ITUB4']).delete()
    Quote.objects.filter(asset=positions['BBAS3']).update(previous_close=None)
    body = cliente_logado.get(ENDPOINT, {'broker': 'BTG'}).json()
    total = body['totals'][0]
    assert total['count'] == 4 and total['quoted'] == 3 and total['dayCount'] == 2
    assert D(total['invested']) == D('33109')
    assert D(total['assessedCost']) == D('21625')
    assert D(total['returnPct']) == 100 * D(total['gain']) / D(total['assessedCost'])
    missing = next(r for r in body['positions'] if r['symbol'] == 'ITUB4')
    assert missing['value'] is None and missing['totalGain'] is None
    assert missing['unavailable']['valuation']
    no_day = next(r for r in body['positions'] if r['symbol'] == 'BBAS3')
    assert no_day['value'] is not None and no_day['dayGain'] is None


def test_all_missing_quote_does_not_fabricate_zero(cliente_logado, positions):
    Quote.objects.all().delete()
    body = cliente_logado.get(ENDPOINT).json()
    assert len(body['positions']) == 7
    for total in body['totals']:
        assert total['value'] is None and total['gain'] is None
        assert total['day'] is None and total['dayPct'] is None
        assert total['returnPct'] is None and total['quoted'] == 0


@pytest.mark.parametrize('query', [{'broker':'BINANCE'}, {'result':'invalid'}, {'sort':'password'},
                                  {'direction':'invalid'}, {'search':'x'*121}])
def test_query_validation(cliente_logado, positions, query):
    assert cliente_logado.get(ENDPOINT, query).status_code == 400


def test_search_sort_and_history_flag(cliente_logado, positions):
    body = cliente_logado.get(ENDPOINT, {'search':'TTWO','history':'0'}).json()
    assert [r['symbol'] for r in body['positions']] == ['TTWO']
    assert body['positions'][0]['history'] == []
    sorted_rows = cliente_logado.get(ENDPOINT, {'sort':'price','direction':'desc'}).json()['positions']
    assert [D(p['price']) for p in sorted_rows] == sorted([D(p['price']) for p in sorted_rows], reverse=True)


@pytest.mark.parametrize('method', ['post','put','patch','delete'])
def test_mutations_are_blocked(cliente_logado, positions, method):
    assert getattr(cliente_logado, method)(ENDPOINT).status_code == 405


