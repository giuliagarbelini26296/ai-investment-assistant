import datetime as dt
import uuid
from decimal import Decimal as D
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.insights import services
from apps.insights.forms import CashForm, SimulationForm, RiskForm
from apps.insights.models import BenchmarkBar, BenchmarkState, CashEntry, CalendarReference, RiskPreference
from apps.marketdata.models import DailyBar, Quote
from apps.portfolio import services as portfolio
from apps.portfolio.models import Asset, Lot, PositionBalance

pytestmark = pytest.mark.django_db


@pytest.fixture
def asset():
    return Asset.objects.create(symbol='ITUB4', name='Itaú', market='B3', custody='BTG', sector='Bancos')


@pytest.fixture
def held(asset, usuario):
    day=timezone.localdate()-dt.timedelta(days=4)
    portfolio.registrar_saldo(asset, reference_date=day, quantity=D(600), average_price=D('38.95'),
        source_key='fixture', evidence='saldo confirmado pela usuária')
    Quote.objects.create(asset=asset, price=D('39.71'), previous_close=D('38.49'), currency='BRL',
        market_time=timezone.now(), collected_at=timezone.now(), source='brapi', quote_type='atrasada')
    return asset


def bar(asset, date, close, source='brapi'):
    return DailyBar.objects.create(asset=asset, session_date=date, close=D(close), source=source,
        time_basis='sessao_local', collected_at=timezone.now())


def cash_data(asset, **overrides):
    return {'asset':asset.pk, 'currency':'BRL', 'kind':'dividend', 'date':timezone.localdate(),
        'amount':'12,34', 'reference':'Extrato BTG 123', 'submission_key':uuid.uuid4(), 'confirm':'on', **overrides}


def test_planning_auth_and_get_do_not_write(client, cliente_logado, held):
    client.logout()
    assert client.get('/planejamento/').status_code==302


def test_dashboard_readonly(cliente_logado, held):
    counts=(Lot.objects.count(), PositionBalance.objects.count(), CashEntry.objects.count(), RiskPreference.objects.count())
    response=cliente_logado.get('/planejamento/')
    assert response.status_code==200
    assert 'Mapa de concentração' in response.content.decode()
    assert 'ITUB4' in response.content.decode()
    assert counts==(Lot.objects.count(), PositionBalance.objects.count(), CashEntry.objects.count(), RiskPreference.objects.count())


def test_simulator_exact_and_readonly(cliente_logado, held, usuario):
    before=(Lot.objects.count(), PositionBalance.objects.count())
    form=SimulationForm({'asset':held.pk,'quantity':'100','price':'40,00'})
    assert form.is_valid(), form.errors
    result=services.simulate(form,portfolio.linhas_da_carteira(),RiskPreference(user=usuario))
    assert result['after'].quantidade==700
    assert result['after'].investido==D(27370)
    assert result['after'].preco_medio==D(27370)/D(700)
    assert result['company_percent']==100
    response=cliente_logado.post('/planejamento/',{'action':'simulate','asset':held.pk,'quantity':'100','price':'40,00'})
    assert response.status_code==200
    assert '39,10' in response.content.decode()
    assert before==(Lot.objects.count(),PositionBalance.objects.count())


def test_b3_integer_and_us_fractions(asset):
    assert not SimulationForm({'asset':asset.pk,'quantity':'0,5','price':'10'}).is_valid()
    us=Asset.objects.create(symbol='TTWO', name='TTWO', market='NASDAQ', custody='AVENUE')
    form=SimulationForm({'asset':us.pk,'quantity':'0,47592','price':'214,57'})
    assert form.is_valid(), form.errors
    result=services.simulate(form,[],RiskPreference())
    assert result['investment']==D('.47592')*D('214.57')
    assert result['currency']=='USD'


def test_concentrations_separate_currencies_and_missing_quotes(held,usuario):
    us=Asset.objects.create(symbol='TTWO',name='TTWO',market='NASDAQ',custody='AVENUE',sector='Games')
    portfolio.registrar_saldo(us,reference_date=timezone.localdate(),quantity=D('100.47592'),average_price=D('214.57'),source_key='us',evidence='print')
    data=services.concentrations([l for l in portfolio.linhas_da_carteira() if l.posicao],RiskPreference(user=usuario))
    br=next(g for g in data['groups'] if g['currency']=='BRL')
    usd=next(g for g in data['groups'] if g['currency']=='USD')
    assert br['total']==D(23826)
    assert usd['total']==D('21559.1181544')
    assert 'custo registrado' in usd['basis']
    assert data['countries']==[]
    assert br['assets'][0]['percent']==100
    assert br['sectors'][0]['name']=='Bancos e serviços financeiros'


def test_limits_validation_and_personal_storage(cliente_logado,usuario):
    assert not RiskForm({'asset_limit':0,'sector_limit':101,'country_limit':'NaN'}).is_valid()
    response=cliente_logado.post('/planejamento/',{'action':'limits','asset_limit':'40,50','sector_limit':'65','country_limit':'75'})
    assert response.status_code==302
    assert RiskPreference.objects.get(user=usuario).asset_limit==D('40.50')


def test_cash_validation_currency_percent_and_future(asset):
    for changes in [{'amount':'1,16%'},{'amount':'-3'},{'currency':'USD'},
                    {'date':timezone.localdate()+dt.timedelta(days=1)},{'confirm':''}]:
        assert not CashForm(cash_data(asset,**changes)).is_valid()


def test_cash_idempotence_and_cancel_preserves_history(cliente_logado,held,usuario):
    data=cash_data(held)
    form=CashForm(data);assert form.is_valid(),form.errors
    entry,created=services.register_cash(form,usuario);assert created
    same,created=services.register_cash(form,usuario);assert not created and same.pk==entry.pk
    assert CashEntry.objects.count()==1
    response=cliente_logado.post(f'/planejamento/lancamentos/{entry.pk}/cancelar/',{'confirm':'yes'})
    assert response.status_code==302
    entry.refresh_from_db();assert entry.cancelled_at
    with pytest.raises(ValueError):services.register_cash(form,usuario)
    assert CashEntry.objects.count()==1
    assert Lot.objects.count()==0


def test_cash_same_key_different_payload_rejected(asset,usuario):
    data=cash_data(asset)
    form=CashForm(data);assert form.is_valid();services.register_cash(form,usuario)
    changed=CashForm({**data,'amount':'50'});assert changed.is_valid()
    with pytest.raises(ValueError):services.register_cash(changed,usuario)
    assert CashEntry.objects.get().amount==D('12.34')


def test_economic_results_expenses_and_cancelled(held,usuario):
    for kind,amount in [('dividend',10),('jcp',5),('fee',2),('tax',1),('profit',20),('loss',3)]:
        CashEntry.objects.create(asset=held,currency='BRL',kind=kind,date=timezone.localdate(),amount=amount,reference=kind,user=usuario)
    cancelled=CashEntry.objects.create(asset=held,currency='BRL',kind='dividend',date=timezone.localdate(),amount=999,reference='teste',user=usuario)
    CashEntry.objects.filter(pk=cancelled.pk).update(cancelled_at=timezone.now())
    result=services.economic_results(portfolio.linhas_da_carteira())[0]
    assert result['unrealized']==456
    assert result['income']==15 and result['realized']==17 and result['expenses']==3
    assert result['total']==485


def test_quality_zero_is_not_missing_and_jumps_flagged(held):
    Quote.objects.filter(asset=held).update(previous_close=D('39.71'))
    day=timezone.localdate()-dt.timedelta(days=2)
    bar(held,day,'20');bar(held,day+dt.timedelta(days=1),'39.71')
    q=services.quality_for(next(l for l in portfolio.linhas_da_carteira() if l.asset.pk==held.pk))
    assert not q['blocks']
    assert q['jumps']
    assert any('0%' in w for w in q['warnings'])


def test_quality_missing_previous_close(held):
    Quote.objects.filter(asset=held).update(previous_close=None)
    q=services.quality_for(portfolio.linhas_da_carteira()[0])
    assert any('Fechamento anterior ausente' in text for text in q['blocks'])


def test_published_calendar_and_ics(cliente_logado,held):
    from apps.mercado.models import EarningsEvent
    day=timezone.localdate()+dt.timedelta(days=1)
    CalendarReference.objects.create(asset=held,date=day,title='Resultado trimestral',source_url='https://example.com/ri',verified_at=timezone.now())
    EarningsEvent.objects.create(asset=held,date=day+dt.timedelta(days=1),source='finnhub',collected_at=timezone.now())
    rows=services.agenda(portfolio.linhas_da_carteira())
    assert len(rows)==1 and rows[0]['confirmed']
    response=cliente_logado.get('/planejamento/agenda.ics')
    assert response.status_code==200 and 'text/calendar' in response['Content-Type']
    assert b'DTSTART;VALUE=DATE:' in response.content
    assert all(len(row)<=75 for row in response.content.split(b'\r\n'))


def test_modified_dietz_neutralizes_purchase():
    assert services.interval_return(D(100),D(200),[(1,D(100))],D(0),1)==0
    assert services.interval_return(D(100),D(110),[],D(5),1)==D('.15')
    assert services.interval_return(D(0),D(100),[(1,D(100))],D(0),1) is None


def test_performance_known_history_and_new_purchase(asset,usuario):
    start=timezone.localdate()-dt.timedelta(days=4)
    portfolio.registrar_saldo(asset,reference_date=start,quantity=D(10),average_price=D(8),source_key='base',evidence='saldo')
    portfolio.criar_lote(asset,usuario,trade_date=start+dt.timedelta(days=1),quantity=D(10),unit_price=D(10))
    for offset,close in [(0,'10'),(1,'10'),(2,'11')]:
        day=start+dt.timedelta(days=offset);bar(asset,day,close)
        BenchmarkBar.objects.create(symbol='BOVA11',date=day,close=D(100),source='brapi',collected_at=timezone.now())
    result=services.performance(portfolio.linhas_da_carteira(),'BRL',start,start+dt.timedelta(days=2))
    assert len(result['points'])==3
    assert result['points'][1]['portfolio']==0
    assert result['portfolio']==10 and result['reference']==0
    assert result['start']==start


def test_performance_missing_reference_does_not_invent(held):
    start=timezone.localdate()-dt.timedelta(days=10)
    result=services.performance(portfolio.linhas_da_carteira(),'BRL',start,timezone.localdate())
    assert result['points']==[] and result['warnings']


def test_performance_blocks_jump_and_realized_without_sale(asset,usuario):
    start=timezone.localdate()-dt.timedelta(days=4)
    portfolio.registrar_saldo(asset,reference_date=start,quantity=D(10),average_price=D(10),source_key='base',evidence='saldo')
    for offset,price in [(0,10),(1,20)]:
        day=start+dt.timedelta(days=offset);bar(asset,day,price)
        BenchmarkBar.objects.create(symbol='BOVA11',date=day,close=100,source='brapi',collected_at=timezone.now())
    result=services.performance(portfolio.linhas_da_carteira(),'BRL',start,timezone.localdate())
    assert not result['points'] and any('Salto' in w for w in result['warnings'])
    DailyBar.objects.filter(asset=asset,session_date=start+dt.timedelta(days=1)).update(close=11)
    CashEntry.objects.create(currency='BRL',kind='profit',date=start+dt.timedelta(days=1),amount=2,reference='resultado informado',user=usuario)
    result=services.performance(portfolio.linhas_da_carteira(),'BRL',start,timezone.localdate())
    assert not result['points'] and any('faltam vendas' in w for w in result['warnings'])


def test_collection_only_once_daily_no_new_assets():
    from apps.insights.collection import refresh_benchmarks
    count=Asset.objects.count()
    candle=SimpleNamespace(data=timezone.localdate()-dt.timedelta(days=1),close=D('100'),provisorio=False)
    with patch('apps.insights.collection.criar_adaptador') as factory:
        factory.return_value.info=SimpleNamespace(limite_minuto=None,limite_dia=None,limite_mes=None)
        factory.return_value.historico.return_value=[candle]
        refresh_benchmarks();refresh_benchmarks()
        assert factory.call_count==2
    assert BenchmarkBar.objects.count()==2 and Asset.objects.count()==count


def test_collection_does_not_expose_provider_secrets():
    from apps.insights.collection import refresh_benchmarks
    with patch('apps.insights.collection.criar_adaptador',side_effect=ValueError('apikey=secret')):
        refresh_benchmarks()
    assert all('secret' not in state.message for state in BenchmarkState.objects.all())


def test_csrf_enforced(usuario,asset):
    from django.test import Client
    client=Client(enforce_csrf_checks=True);client.force_login(usuario)
    assert client.post('/planejamento/',{'action':'cash',**cash_data(asset)}).status_code==403


def test_escaping_user_reference(cliente_logado,held,usuario):
    CashEntry.objects.create(asset=held,currency='BRL',kind='dividend',date=timezone.localdate(),amount=1,
        reference='<script>alert(1)</script>',user=usuario)
    body=cliente_logado.get('/planejamento/').content.decode()
    assert '<script>alert(1)</script>' not in body
    assert '&lt;script&gt;' in body


def test_ranking_day_base_uses_quote_session_not_penultimate_bar(asset,settings):
    from apps.mercado import analise
    settings.PORTAL={**settings.PORTAL,'MODO_DEMO':False}
    # Thursday's quote arrives while the newest candle still belongs to Wednesday.
    now=dt.datetime(2026,11,5,14,0,tzinfo=dt.UTC)
    bar(asset,dt.date(2026,11,3),'38.49');bar(asset,dt.date(2026,11,4),'39.71')
    Quote.objects.create(asset=asset,price=D('39.80'),previous_close=D('39.71'),currency='BRL',
        market_time=now,collected_at=now,source='brapi',quote_type='atrasado',declared_delay_minutes=30)
    x=analise.leitura_do_ativo(asset,now)
    assert x.day_session==dt.date(2026,11,5)
    assert x.ind.periodos['1d'].base_data==dt.date(2026,11,4)
    assert abs(x.variacao('1d')-float((D('39.80')/D('39.71')-1)*100))<1e-9
    assert len(analise.ranking([x],'1d')['altas'])==1


def test_rankings_exclude_stale_quote_and_missing_previous(asset,settings):
    from apps.mercado import analise
    from apps.mercado.resumo import montar
    settings.PORTAL={**settings.PORTAL,'MODO_DEMO':False}
    now=dt.datetime(2026,11,5,14,0,tzinfo=dt.UTC)
    bar(asset,dt.date(2026,11,3),'38.49');bar(asset,dt.date(2026,11,4),'39.71')
    q=Quote.objects.create(asset=asset,price=D(40),previous_close=D(39),currency='BRL',
        market_time=now-dt.timedelta(hours=3),collected_at=now-dt.timedelta(hours=3),source='brapi',quote_type='atrasado')
    x=analise.leitura_do_ativo(asset,now)
    assert x.atualidade.estado=='desatualizado'
    assert analise.ranking([x],'1d')['sem_dado']==[x]
    assert montar([x],agora=now).com_variacao==0
    q.market_time=q.collected_at=now;q.previous_close=None;q.save()
    x=analise.leitura_do_ativo(asset,now)
    assert x.variacao('1d') is None
    assert analise.ranking([x],'1d')['altas']==[]


def test_ranking_zero_does_not_become_loss_or_gain(asset,settings):
    from apps.mercado import analise
    settings.PORTAL={**settings.PORTAL,'MODO_DEMO':False}
    now=dt.datetime(2026,11,5,14,0,tzinfo=dt.UTC)
    bar(asset,dt.date(2026,11,3),'38.49');bar(asset,dt.date(2026,11,4),'39.71')
    Quote.objects.create(asset=asset,price=D('39.71'),previous_close=D('39.71'),currency='BRL',
        market_time=now,collected_at=now,source='brapi',quote_type='atrasado')
    x=analise.leitura_do_ativo(asset,now);r=analise.ranking([x],'1d')
    assert x.variacao('1d')==0 and not r['altas'] and not r['quedas'] and not r['sem_dado']


def test_us_before_open_is_previous_session_not_today(settings):
    from apps.mercado import analise
    settings.PORTAL={**settings.PORTAL,'MODO_DEMO':False}
    asset=Asset.objects.create(symbol='TTWO',name='TTWO',market='NASDAQ',custody='AVENUE')
    bar(asset,dt.date(2026,11,3),'194.99','twelvedata');bar(asset,dt.date(2026,11,4),'208.94','twelvedata')
    Quote.objects.create(asset=asset,price=D('208.94'),previous_close=D('194.99'),currency='USD',
        collected_at=dt.datetime(2026,11,4,21,10,tzinfo=dt.UTC),source='twelvedata',quote_type='nao_declarado')
    x=analise.leitura_do_ativo(asset,dt.datetime(2026,11,5,14,0,tzinfo=dt.UTC))
    assert x.day_reference_label=='Pregão de 04/11/2026'
    assert x.day_base_date==dt.date(2026,11,3)
    assert x.ranking_eligible


def test_market_filter_preserved_in_rankings_and_fragments(cliente_logado,held,settings):
    from apps.mercado import analise
    settings.PORTAL={**settings.PORTAL,'MODO_DEMO':False}
    us=Asset.objects.create(symbol='TTWO',name='TTWO',market='NASDAQ',custody='AVENUE')
    now=timezone.now()
    for asset,source,price,previous in [(held,'brapi','39.71','38.49'),(us,'twelvedata','208.94','194.99')]:
        for offset,close in [(2,previous),(1,price)]:
            bar(asset,timezone.localdate()-dt.timedelta(days=offset),close,source)
        Quote.objects.create(asset=asset,price=D(price),previous_close=D(previous),currency=asset.native_currency,
            collected_at=now,source=source,quote_type='nao_declarado')
    analise.invalidar_cache()
    for scope,expected in [('brasil','B3'),('internacional','NASDAQ')]:
        response=cliente_logado.get('/mercado/rankings/',{'mercado':scope,'periodo':'1d'})
        assert response.status_code==200
        rows=response.context['altas']+response.context['quedas']
        assert rows and all(x.asset.market==expected for x,p in rows)
        assert f'mercado={scope}&amp;fragmento=1' in response.content.decode()
        assert '&amp;tipo=todos' in response.content.decode()
        fragment=cliente_logado.get('/mercado/rankings/',{'mercado':scope,'periodo':'1d','fragmento':'1'})
        assert all(x.asset.market==expected for x,p in fragment.context['altas']+fragment.context['quedas'])
        general=cliente_logado.get('/mercado/',{'mercado':scope})
        assert general.context['mercado_filtro']==scope
        assert all(li.x.asset.market==expected for li in general.context['painel'].maiores_altas+general.context['painel'].maiores_quedas)


def test_unknown_sector_is_coverage_not_concentration(usuario):
    us=Asset.objects.create(symbol='TTWO',name='TTWO',market='NASDAQ',custody='AVENUE')
    portfolio.registrar_saldo(us,reference_date=timezone.localdate(),quantity=D(1),average_price=D(100),source_key='us',evidence='saldo')
    c=services.concentrations(portfolio.linhas_da_carteira(),RiskPreference(user=usuario))
    assert c['groups'][0]['sectors'][0]['percent']==100
    assert not c['groups'][0]['sectors'][0]['over']
    form=SimulationForm({'asset':us.pk,'quantity':'1','price':'100'});assert form.is_valid()
    scenario=services.simulate(form,portfolio.linhas_da_carteira(),RiskPreference(user=usuario))
    assert scenario['sector_percent'] is None and not scenario['sector_over']


def test_recent_collection_with_old_market_session_excluded(asset,settings):
    from apps.mercado import analise
    settings.PORTAL={**settings.PORTAL,'MODO_DEMO':False}
    now=dt.datetime(2026,11,5,14,0,tzinfo=dt.UTC)
    bar(asset,dt.date(2026,11,3),'38.49');bar(asset,dt.date(2026,11,4),'39.71')
    Quote.objects.create(asset=asset,price=D('39.71'),previous_close=D('38.49'),currency='BRL',
        market_time=dt.datetime(2026,11,4,20,50,tzinfo=dt.UTC),collected_at=now,source='brapi',quote_type='atrasado')
    x=analise.leitura_do_ativo(asset,now)
    assert not x.ranking_eligible
    assert 'pregão anterior' in x.ranking_warning
    assert not analise.ranking([x],'1d')['altas']


def test_fresh_collection_of_old_us_candle_is_stale_and_day_unavailable(settings):
    from apps.marketdata.freshness import atualidade
    from apps.react_portal.api import native_position, native_totals
    settings.PORTAL={**settings.PORTAL,'MODO_DEMO':False}
    asset=Asset.objects.create(symbol='TTWO',name='TTWO',market='NASDAQ',custody='AVENUE')
    portfolio.registrar_saldo(asset,reference_date=dt.date(2026,11,3),quantity=D(1),average_price=D(214),source_key='us',evidence='saldo')
    now=dt.datetime(2026,11,5,15,0,tzinfo=dt.UTC)
    Quote.objects.create(asset=asset,price=D('208.90'),previous_close=D('194.99'),currency='USD',
        collected_at=now,source='twelvedata',quote_type='nao_declarado',raw={'timestamp_candle':'2026-11-04T14:30:00+00:00'})
    assert atualidade(asset,now).estado=='desatualizado'
    line=portfolio.linhas_da_carteira(agora=now)[0]
    result=native_position(line,False)
    assert result['price']==D('208.90') and result['dayGain'] is None and result['dayPercent'] is None
    assert native_totals([line],[result])[0]['dayCount']==0
    assert line.avaliacao.variacao_dia_pct is None


def test_current_us_session_and_previous_close_enable_day(settings):
    from apps.marketdata.freshness import atualidade
    settings.PORTAL={**settings.PORTAL,'MODO_DEMO':False}
    asset=Asset.objects.create(symbol='TTWO',name='TTWO',market='NASDAQ',custody='AVENUE')
    now=dt.datetime(2026,11,5,15,0,tzinfo=dt.UTC)
    Quote.objects.create(asset=asset,price=D(209),previous_close=D('208.90'),currency='USD',
        collected_at=now,source='twelvedata',quote_type='nao_declarado',raw={'timestamp_candle':'2026-11-05T14:30:00+00:00'})
    assert atualidade(asset,now).permite_alertas
