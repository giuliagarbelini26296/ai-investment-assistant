import datetime as dt
import uuid
from decimal import Decimal as D

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone
from apps.portfolio.models import Asset, Lot, PositionBalance
from apps.portfolio import services as original
from apps.managed_portfolios.models import Portfolio, Balance, Purchase, CurrencyQuote
from apps.managed_portfolios import services
from apps.managed_portfolios.views import PurchaseForm
from apps.marketdata.models import Quote
from apps.react_portal.api import portfolio_payload

pytestmark=pytest.mark.django_db


@pytest.fixture
def accounts():
    bruno=Portfolio.objects.get(slug='bruno')
    bbas=Asset.objects.create(symbol='BBAS3',name='Banco do Brasil',market='B3',custody='BTG')
    original.registrar_saldo(bbas,reference_date=timezone.localdate(),quantity=D('120'),average_price=D('17.10'),
        invested_cost=D('2051.89'),source_key='ana',evidence='Teste isolado')
    services.reconcile(bruno,bbas,reference_date=timezone.localdate(),quantity=D('300'),reported_average=D('16.20'),
        invested_cost=D('4861.25'),currency='BRL',source_key='bruno',evidence='Teste isolado')
    return bruno,bbas


def test_same_asset_different_balances_and_no_fake_lots(accounts):
    bruno,asset=accounts
    assert original.posicao_do_ativo(asset).quantidade==120
    assert services.position(bruno,asset)[0].quantidade==300
    assert Asset.objects.filter(symbol='BBAS3',market='B3').count()==1
    assert Lot.objects.count()==Purchase.objects.count()==0
    assert PositionBalance.objects.count()==Balance.objects.count()==1


def test_api_defaults_ana_and_scopes_filters(accounts):
    ana=portfolio_payload({'history':'0'},'sessao')
    bruno=portfolio_payload({'account':'bruno','history':'0','search':'BBAS'},'sessao')
    assert ana['meta']['account']=='ana' and ana['meta']['ownerLabel']=='Ana'
    assert bruno['meta']['ownerLabel']=='Bruno'
    assert ana['totals'][0]['invested']==D('2051.89')
    assert bruno['totals'][0]['invested']==D('4861.25')
    assert bruno['positions'][0]['quantity']==300
    assert portfolio_payload({'account':'bruno','search':'TTWO'},'sessao')['totals']==[]
    assert ana['meta']['purchaseUrl']=='/compras/registrar/'
    assert bruno['meta']['purchaseUrl']=='/carteiras/bruno/compras/registrar/'


@pytest.mark.parametrize('query',[{'account':'all'},{'account':'../../ana'},{'account':'bruno','broker':'BTG'}])
def test_invalid_cross_account_queries_rejected(accounts,query):
    with pytest.raises(ValueError):portfolio_payload(query,'sessao')


def test_btc_in_brl_never_uses_original_usdt_quotes(accounts):
    bruno,_=accounts
    btc=Asset.objects.create(symbol='BTC',name='Bitcoin',market='CRIPTO',custody='BINANCE')
    original_before=list(PositionBalance.objects.values())
    services.reconcile(bruno,btc,reference_date=timezone.localdate(),quantity=D('.02319087'),reported_average=D('426934.82'),
        invested_cost=D('9900.99'),currency='BRL',source_key='btc-real-bruno',evidence='Print em reais')
    Quote.objects.create(asset=btc,price=D('80000'),previous_close=D('81000'),currency='USDT',source='binance',
        market_time=timezone.now(),collected_at=timezone.now(),quote_type='spot')
    row=next(p for p in portfolio_payload({'account':'bruno'},'sessao')['positions'] if p['symbol']=='BTC')
    assert row['currency']=='BRL' and row['costBasis']==D('9900.99') and row['price'] is None
    assert row['history']==[]
    assert not any(p['symbol']=='BTC' for p in portfolio_payload({},'sessao')['positions'])
    assert list(PositionBalance.objects.values())==original_before
    CurrencyQuote.objects.create(asset=btc,currency='BRL',price=D('413464.75'),previous_close=D('417400'),
        change_24h_pct=D('-0.94'),market_time=timezone.now(),collected_at=timezone.now(),source='binance')
    row=next(p for p in portfolio_payload({'account':'bruno'},'sessao')['positions'] if p['symbol']=='BTC')
    assert row['value']==D('.02319087')*D('413464.75')
    assert row['totalGain']==row['value']-D('9900.99') and row['dayBasis']=='rolling_24h'
    assert row['dayGain']==D('.02319087')*(D('413464.75')-D('417400'))


def test_stale_btc_does_not_contribute_daily_gain(accounts):
    bruno,asset=accounts
    asset=Asset.objects.create(symbol='BTC',name='Bitcoin',market='CRIPTO',custody='OUTRA')
    services.reconcile(bruno,asset,reference_date=timezone.localdate(),quantity=D('.01'),reported_average=D('400000'),
        invested_cost=D('4000'),currency='BRL',source_key='s',evidence='Saldo')
    CurrencyQuote.objects.create(asset=asset,currency='BRL',price=D('410000'),previous_close=D('400000'),
        market_time=timezone.now()-dt.timedelta(days=1),collected_at=timezone.now()-dt.timedelta(days=1),source='binance')
    row=next(p for p in portfolio_payload({'account':'bruno'},'sessao')['positions'] if p['symbol']=='BTC')
    assert row['value']==4100 and row['dayGain'] is None and row['quoteStatus']=='desatualizado'


def test_real_purchase_uses_bruno_exact_cost_and_leaves_ana(accounts,usuario):
    bruno,asset=accounts
    key=uuid.uuid4()
    receipt,created=services.record_purchase(bruno,asset,usuario,quantity=D('100'),unit_price=D('17'),currency='BRL',
        trade_date=timezone.localdate(),submission_key=key,reviewed_before=['300','4861.25'])
    assert created
    pos=services.position(bruno,asset)[0]
    assert pos.quantidade==400 and pos.investido==D('6561.25')
    assert original.posicao_do_ativo(asset).quantidade==120 and original.posicao_do_ativo(asset).investido==D('2051.89')
    assert Lot.objects.count()==0
    again,created=services.record_purchase(bruno,asset,usuario,quantity=D('100'),unit_price=D('17'),currency='BRL',
        trade_date=timezone.localdate(),submission_key=key,reviewed_before=['300','4861.25'])
    assert not created and again.pk==receipt.pk and Purchase.objects.count()==1


def test_changed_position_and_old_date_block_purchase(accounts,usuario):
    bruno,asset=accounts
    with pytest.raises(ValidationError):
        services.record_purchase(bruno,asset,usuario,quantity=D('100'),unit_price=D('17'),currency='BRL',
            trade_date=timezone.localdate(),submission_key=uuid.uuid4(),reviewed_before=['120','2051.89'])
    with pytest.raises(ValidationError):
        services.purchase_preview(bruno,asset,'BRL',D(100),D(17),timezone.localdate()-dt.timedelta(days=1))
    assert Purchase.objects.count()==0


def test_snapshot_idempotence_and_immutable_history(accounts):
    bruno,asset=accounts
    old=Balance.objects.get(portfolio=bruno)
    values={name:getattr(old,name) for name in ['quantity','reported_average','invested_cost','currency','reference_date','source_key','evidence']}
    same,created=services.reconcile(bruno,asset,**values)
    assert not created and same.pk==old.pk
    with pytest.raises(ValidationError):old.save()
    values['invested_cost']+=D('.01')
    with pytest.raises(ValidationError):services.reconcile(bruno,asset,**values)


@pytest.mark.parametrize('changes',[{'currency':'USD'},{'quantity':'0,5'},{'trade_date':'2099-01-01'},{'unit_price':'1,16%'}])
def test_purchase_form_rejects_wrong_currency_fractions_future_and_percent(accounts,changes):
    bruno,asset=accounts
    data={'asset_choice':asset.pk,'currency':'BRL','quantity':'100','unit_price':'17,00','trade_date':timezone.localdate().isoformat(),'submission_key':str(uuid.uuid4())}
    data.update(changes)
    assert not PurchaseForm(data,portfolio=bruno).is_valid()


def test_brl_btc_form_preserves_fraction(accounts):
    bruno,_=accounts
    asset=Asset.objects.create(symbol='BTC',name='Bitcoin',market='CRIPTO',custody='OUTRA')
    form=PurchaseForm({'asset_choice':asset.pk,'currency':'BRL','quantity':'0.02319087','unit_price':'426.934,82',
        'trade_date':timezone.localdate().isoformat(),'submission_key':str(uuid.uuid4())},portfolio=bruno)
    assert form.is_valid(),form.errors
    assert form.cleaned_data['quantity']==D('.02319087') and form.cleaned_data['unit_price']==D('426934.82')


def test_auth_review_confirmation_and_retry(cliente_logado,accounts):
    bruno,asset=accounts
    url='/carteiras/bruno/compras/registrar/'
    before=original.posicao_do_ativo(asset)
    data={'asset_choice':asset.pk,'currency':'BRL','quantity':'100','unit_price':'17,00','trade_date':timezone.localdate().isoformat(),'submission_key':str(uuid.uuid4()),'action':'review'}
    response=cliente_logado.post(url,data)
    assert response.status_code==200 and Purchase.objects.count()==0
    signature=response.context['review_signature']
    assert signature and 'Bruno' in response.content.decode()
    data.update(action='confirm',review_signature=signature)
    assert cliente_logado.post(url,data).status_code==200 and Purchase.objects.count()==0
    data['not_included']='yes'
    response=cliente_logado.post(url,data)
    assert response.status_code==302 and response.url=='/?carteira=bruno#/carteira'
    assert Purchase.objects.count()==1
    assert cliente_logado.post(url,data).status_code==302 and Purchase.objects.count()==1
    assert original.posicao_do_ativo(asset)==before


def test_anonymous_cannot_see_or_write_bruno(client,accounts):
    assert client.get('/api/v1/portfolio/',{'account':'bruno'}).status_code==401
    assert client.get('/carteiras/bruno/compras/registrar/').status_code==302
    assert client.get('/carteiras/bruno/compras/previa.json').status_code==401
    assert Purchase.objects.count()==0


def test_no_combined_totals_or_access_changes(cliente_logado,accounts):
    response=cliente_logado.get('/api/v1/portfolio/',{'account':'all'})
    assert response.status_code==400
    assert cliente_logado.get('/carteiras/ana/compras/registrar/').status_code==404
    body=cliente_logado.get('/carteiras/bruno/compras/registrar/').content.decode()
    assert 'carteira atual: bruno' in body.casefold()
    assert '/carteiras/bruno/compras/previa.json' in body
