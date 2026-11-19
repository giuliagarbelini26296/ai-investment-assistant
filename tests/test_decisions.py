import datetime as dt
from decimal import Decimal as D
from types import SimpleNamespace
import pytest
from apps.portfolio.models import Asset,Lot
from apps.portfolio import services as portfolio
from apps.marketdata.models import DailyBar,Quote
from apps.managed_portfolios.models import CurrencyQuote,CurrencyBar,Purchase,Portfolio
from apps.managed_portfolios import services as managed
from apps.insights.models import DecisionPreference,ValueReference,DecisionNotice
from apps.insights import decisions,decision_monitor,decision_collection
from apps.insights.decision_forms import DecisionForm,ReferenceForm

pytestmark=pytest.mark.django_db
NOW=dt.datetime(2026,11,9,15,tzinfo=dt.UTC)
TODAY=NOW.date()


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr('django.utils.timezone.now',lambda:NOW)


def asset_with_data(symbol='SANB11',market='B3',price='101'):
    a=Asset.objects.create(symbol=symbol,name=symbol,market=market,custody='BTG' if market=='B3' else 'AVENUE')
    dates=[];day=TODAY-dt.timedelta(days=1)
    while len(dates)<65:
        if market=='CRIPTO' or day.weekday()<5:dates.append(day)
        day-=dt.timedelta(days=1)
    for i,day in enumerate(reversed(dates)):
        close=D(100 if i%2==0 else 102)
        if market=='CRIPTO':CurrencyBar.objects.create(asset=a,currency='BRL',date=day,close=close,source='binance',collected_at=NOW)
        else:DailyBar.objects.create(asset=a,session_date=day,time_basis='sessao_local',close=close,source='brapi' if market=='B3' else 'twelvedata',collected_at=NOW)
    if market=='CRIPTO':CurrencyQuote.objects.create(asset=a,currency='BRL',price=D(price),previous_close=D(100),collected_at=NOW,market_time=NOW,source='binance')
    else:Quote.objects.create(asset=a,currency=a.native_currency,price=D(price),previous_close=D(100),collected_at=NOW,market_time=NOW,source='brapi' if market=='B3' else 'twelvedata',quote_type='atrasado',declared_delay_minutes=30)
    return a


def pref(user,**kw):
    values={'user':user,'account':'ana','profile':'arrojado','horizon':'tatico','budget_brl':D(1000),'budget_usd':D(1000)}
    values.update(kw)
    return DecisionPreference(**values)


def test_no_budget_or_horizon_never_invents_quantity(usuario):
    asset_with_data()
    data=decisions.evaluate(usuario,'ana',pref(usuario,budget_brl=None,budget_usd=None,horizon=''),now=NOW)
    assert data['missing_budget'] and data['missing_horizon']
    assert all(row['plan_quantity'] is None and row['sale_quantity'] is None for row in data['rows'])
    assert not DecisionPreference.objects.exists()


def test_technical_signal_explains_all_conditions(usuario):
    a=asset_with_data()
    row=decisions.decision_row(a,None,pref(usuario),None,NOW)
    assert row['eligible'] and row['signal']=='Entrada técnica em estudo'
    assert row['sma20']==101 and row['sma50']==101 and 35<=row['rsi']<=65
    assert row['reference_price'] is None


def test_falling_price_does_not_mean_bargain(usuario):
    a=asset_with_data(price='80')
    for index,bar in enumerate(DailyBar.objects.filter(asset=a).order_by('session_date')):
        bar.close=D(140-index);bar.save()
    row=decisions.decision_row(a,None,pref(usuario),None,NOW)
    assert row['trend']=='Desfavorável' and not row['eligible']
    row=decisions.decision_row(a,None,pref(usuario,horizon='longo'),None,NOW)
    assert row['signal']=='Estudar valor e tese' and row['entry_limit'] is None


def test_long_term_requires_identified_reference_and_margin(usuario):
    a=asset_with_data()
    reference=ValueReference(user=usuario,account='ana',asset=a,currency='BRL',price=D(150),reference_date=TODAY,source='Estudo pessoal identificado')
    row=decisions.decision_row(a,None,pref(usuario,horizon='longo'),reference,NOW)
    assert row['entry_limit']==120 and row['eligible']
    assert row['reference_kind']=='Premissa informada por você'
    reference.price=D(110)
    row=decisions.decision_row(a,None,pref(usuario,horizon='longo'),reference,NOW)
    assert row['entry_limit']==88 and row['signal']=='Aguardar preço'


@pytest.mark.parametrize('case',['stale','future','wrong_currency','demo'])
def test_unreliable_quotes_block_entries(usuario,case):
    a=asset_with_data();q=Quote.objects.get(asset=a)
    if case=='stale':q.collected_at=NOW-dt.timedelta(days=2);q.market_time=NOW-dt.timedelta(days=2)
    elif case=='future':q.collected_at=NOW+dt.timedelta(hours=1);q.market_time=q.collected_at
    elif case=='wrong_currency':q.currency='USD'
    elif case=='demo':q.source='demo'
    q.save()
    row=decisions.decision_row(a,None,pref(usuario),None,NOW)
    assert row['blocks'] and not row['eligible']


def test_jump_and_incomplete_history_block_signal(usuario):
    a=asset_with_data();bar=DailyBar.objects.filter(asset=a).order_by('-session_date').first()
    bar.close=200;bar.save()
    row=decisions.decision_row(a,None,pref(usuario),None,NOW)
    assert row['blocks'] and not row['eligible']
    DailyBar.objects.filter(asset=a).delete()
    row=decisions.decision_row(a,None,pref(usuario),None,NOW)
    assert row['sma50'] is None and not row['eligible']


def test_usdt_is_not_accepted_for_brl_crypto_analysis(usuario):
    a=asset_with_data('SOL','CRIPTO')
    CurrencyQuote.objects.filter(asset=a).delete()
    Quote.objects.create(asset=a,currency='USDT',price=D('100'),source='binance',market_time=NOW,collected_at=NOW,quote_type='spot')
    row=decisions.decision_row(a,None,pref(usuario),None,NOW)
    assert row['price'] is None and row['currency']=='BRL' and not row['eligible']
    assert row['sma20']==101  # the separate direct-BRL history remains available


def test_no_stock_fundamental_criteria_applied_to_crypto(usuario):
    a=asset_with_data('SOL','CRIPTO')
    row=decisions.decision_row(a,None,pref(usuario),None,NOW)
    assert row['eligible'] and row['fund'] is None and row['kind']=='Cripto'


def test_plan_never_reuses_cash_or_loss_budget(usuario):
    for symbol in ('SANB11','ITUB4','BBAS3'):asset_with_data(symbol)
    data=decisions.evaluate(usuario,'ana',pref(usuario),{'scope':'all'},NOW)
    plan=data['plans'][0]
    assert sum((r['plan_cost'] for r in data['rows']),D(0))==plan['planned_cost']<=1000
    assert plan['scenario_loss']<=plan['risk_limit']==20
    assert all(r['plan_quantity']==r['plan_quantity'].to_integral_value() for r in data['rows'])
    assert plan['remaining']==1000-plan['planned_cost']


def test_us_fractional_and_brl_budgets_are_separate(usuario):
    asset_with_data('AMZN','NASDAQ');asset_with_data()
    data=decisions.evaluate(usuario,'ana',pref(usuario,budget_brl=0,budget_usd=1000),{'scope':'all'},NOW)
    usd=next(r for r in data['rows'] if r['currency']=='USD')
    brl=next(r for r in data['rows'] if r['currency']=='BRL')
    assert usd['plan_quantity']>0 and usd['plan_quantity']!=usd['plan_quantity'].to_integral_value()
    assert usd['plan_cost']<=1000 and brl['plan_quantity']==0


def test_missing_held_valuation_blocks_size_in_that_currency(usuario):
    a=asset_with_data()
    row=decisions.decision_row(a,None,pref(usuario),None,NOW)
    plans=decisions.allocate([row],[{'currency':'BRL','value':None,'quoteStatus':'sem_cotacao'}],pref(usuario))
    assert not plans[0]['complete'] and row['plan_quantity']==0
    assert 'bloqueado' in row['plan_reason']


def test_rebalance_is_capped_at_actual_owned_quantity(usuario):
    a=asset_with_data()
    held={'quantity':D(200),'value':D(20200),'costBasis':D(19000),'currency':'BRL','quoteStatus':'atrasado'}
    row=decisions.decision_row(a,held,pref(usuario,budget_brl=0),None,NOW)
    decisions.allocate([row],[held],pref(usuario,budget_brl=0))
    assert row['signal']=='Rever concentração' and row['sale_quantity']==150
    assert not row['eligible'] and row['plan_quantity']==0
    assert 'não uma ordem' in row['reasons'][0]


def test_bruno_and_ana_positions_remain_distinct(usuario):
    a=asset_with_data()
    portfolio.registrar_saldo(a,reference_date=TODAY,quantity=D(100),average_price=D(90),source_key='a',evidence='Teste')
    managed.reconcile(Portfolio.objects.get(slug='bruno'),a,reference_date=TODAY,quantity=D(300),reported_average=D(95),invested_cost=D(28500),currency='BRL',source_key='m',evidence='Teste')
    ana=decisions.evaluate(usuario,'ana',pref(usuario),now=NOW)
    bruno=decisions.evaluate(usuario,'bruno',pref(usuario,account='bruno'),now=NOW)
    assert ana['rows'][0]['held_quantity']==100 and bruno['rows'][0]['held_quantity']==300
    assert Lot.objects.count()==Purchase.objects.count()==0


def test_bdr_does_not_get_us_underlying_target(usuario):
    from apps.mercado.models import PriceTarget,AnalystSnapshot
    a=asset_with_data('A1MD34')
    PriceTarget.objects.create(asset=a,mean=500,source='fonte-usd',collected_at=NOW)
    AnalystSnapshot.objects.create(asset=a,source='fonte-usd',period=TODAY,buy=10,collected_at=NOW)
    row=decisions.decision_row(a,None,pref(usuario,horizon='longo'),None,NOW)
    assert row['reference_price'] is None and not row['eligible']


@pytest.mark.parametrize('bad', [{'budget_brl':'-1'},{'plan_risk':'0'},{'stop_distance':'100'},{'asset_limit':'101'}])
def test_invalid_scenario_rejected(usuario,bad):
    data={'profile':'arrojado','horizon':'tatico','budget_brl':'1.000,00','budget_usd':'','asset_limit':'25','plan_risk':'2','stop_distance':'15','entry_tolerance':'2','safety_margin':'20'}
    data.update(bad)
    form=DecisionForm(data,instance=pref(usuario))
    assert not form.is_valid()


def test_reference_form_rejects_future_and_wrong_currency(usuario):
    a=asset_with_data()
    form=ReferenceForm({'asset':a.pk,'currency':'USD','price':'100','reference_date':(TODAY+dt.timedelta(days=1)).isoformat(),'source':'Estudo'})
    assert not form.is_valid()


def test_budget_accepts_brazilian_thousands_and_decimal_separators(usuario):
    data={'profile':'arrojado','horizon':'tatico','budget_brl':'1.000','budget_usd':'2.000,50','asset_limit':'25','plan_risk':'2','stop_distance':'15','entry_tolerance':'2','safety_margin':'20'}
    form=DecisionForm(data,instance=pref(usuario))
    assert form.is_valid(),form.errors
    assert form.cleaned_data['budget_brl']==1000 and form.cleaned_data['budget_usd']==D('2000.50')


def test_auth_and_get_do_not_create_preferences_or_trades(client,cliente_logado):
    from django.test import Client
    asset_with_data()
    assert Client().get('/avaliacao/').status_code==302
    response=cliente_logado.get('/avaliacao/')
    assert response.status_code==200
    text=response.content.decode()
    assert 'Seu assistente de investimentos' in text and 'ordens reais desativadas' in text
    assert not DecisionPreference.objects.exists() and not Lot.objects.exists() and not Purchase.objects.exists()
    assert cliente_logado.get('/avaliacao/',{'carteira':'all'}).status_code==400


def test_settings_are_account_scoped_and_no_orders(cliente_logado,usuario):
    asset_with_data()
    data={'action':'settings','profile':'arrojado','horizon':'tatico','budget_brl':'5.000,00','budget_usd':'','asset_limit':'25','plan_risk':'2','stop_distance':'15','entry_tolerance':'2','safety_margin':'20'}
    assert cliente_logado.post('/avaliacao/?carteira=ana',data).status_code==302
    assert DecisionPreference.objects.get(user=usuario,account='ana').budget_brl==5000
    assert not DecisionPreference.objects.filter(account='bruno').exists()
    response=cliente_logado.get('/avaliacao/?carteira=bruno')
    assert response.status_code==200 and 'Carteira Bruno' in response.content.decode()
    assert cliente_logado.post('/avaliacao/',{'action':'buy','asset':'SANB11','quantity':100}).status_code==200
    assert not Lot.objects.exists() and not Purchase.objects.exists()
    assert cliente_logado.post('/api/v1/orders/',{}).status_code==404


def test_monitor_opt_in_deduplication_and_no_messages(usuario,settings,monkeypatch):
    asset_with_data()
    settings.PORTAL={**settings.PORTAL,'MODO_DEMO':False}
    p=pref(usuario,monitor_enabled=False);p.save()
    monkeypatch.setattr(decision_monitor,'_next_run',None)
    assert decision_monitor.refresh_notices(NOW)==0
    p.monitor_enabled=True;p.save()
    monkeypatch.setattr(decision_monitor,'_next_run',None)
    assert decision_monitor.refresh_notices(NOW)==1
    monkeypatch.setattr(decision_monitor,'_next_run',None)
    assert decision_monitor.refresh_notices(NOW)==0
    assert DecisionNotice.objects.count()==1 and not Lot.objects.exists() and not Purchase.objects.exists()


def test_direct_brl_crypto_collection_preserves_original_stream(settings,monkeypatch):
    settings.PORTAL={**settings.PORTAL,'MODO_DEMO':False}
    asset=Asset.objects.create(symbol='SOL',name='Solana',market='CRIPTO',custody='OUTRA')
    original=Quote.objects.create(asset=asset,currency='USDT',price=100,source='binance',collected_at=NOW)
    monkeypatch.setattr(decision_collection,'_attempts',{})
    quote=SimpleNamespace(simbolo='SOLBRL',preco=D('500'),moeda='BRL',horario_coleta=NOW,horario_mercado=NOW,
        fechamento_anterior=D('490'),variacao_24h_pct=D('2'),fonte='binance')
    class Fake:
        def cotar(self,symbols):
            assert symbols==['SOLBRL'];return SimpleNamespace(cotacoes=[quote])
        def historico(self,symbol,dias):
            assert symbol=='SOLBRL'
            return [SimpleNamespace(data=TODAY-dt.timedelta(days=1),close=D('490'),provisorio=False,fonte='binance'),
                SimpleNamespace(data=TODAY,close=D('500'),provisorio=True,fonte='binance')]
    assert decision_collection.refresh_crypto_analysis(NOW,Fake())['quotes']==1
    assert CurrencyQuote.objects.get(asset=asset).currency=='BRL' and CurrencyBar.objects.count()==1
    original.refresh_from_db();assert original.currency=='USDT' and original.price==100


def test_crypto_history_failure_does_not_skip_other_asset(settings,monkeypatch):
    from apps.marketdata.adapters.base import AdapterError
    settings.PORTAL={**settings.PORTAL,'MODO_DEMO':False}
    for symbol in ['BTC','SOL']:
        a=Asset.objects.create(symbol=symbol,name=symbol,market='CRIPTO',custody='OUTRA')
        CurrencyQuote.objects.create(asset=a,currency='BRL',price=100,collected_at=NOW,market_time=NOW,source='binance')
    monkeypatch.setattr(decision_collection,'_attempts',{})
    class Fake:
        def historico(self,symbol,dias):
            if symbol=='BTCBRL':raise AdapterError('Indisponível')
            return [SimpleNamespace(data=TODAY-dt.timedelta(days=1),close=D(100),provisorio=False,fonte='binance')]
    result=decision_collection.refresh_crypto_analysis(NOW,Fake())
    assert result['status']=='partial' and result['bars']==1
    assert CurrencyBar.objects.get().asset.symbol=='SOL'
