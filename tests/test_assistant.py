import datetime as dt
import json
from types import SimpleNamespace
import pytest
from django.test import override_settings,Client
from django.utils import timezone
from apps.insights import assistant, live_crypto
from apps.insights.models import AssistantUsage,DecisionPreference,LiveFeedState
from apps.portfolio.models import Asset,Lot,PositionBalance
from apps.managed_portfolios.models import CurrencyQuote
from apps.marketdata.models import Quote

pytestmark=pytest.mark.django_db


def test_disabled_never_calls_paid_api(usuario,monkeypatch):
    monkeypatch.setattr(assistant.httpx,'Client',lambda **kw:pytest.fail('No paid requests allowed'))
    with override_settings(PORTAL_AI_ENABLED=False,PORTAL_AI_KEY='configured-but-disabled'):
        with pytest.raises(assistant.AssistantUnavailable):assistant.ai_query(usuario,'ana','teste',{})
    assert not AssistantUsage.objects.exists()


def test_local_brazilian_budget_and_transient_horizon(usuario):
    state={}
    result=assistant.local_query(usuario,'ana','Tenho R$ 5.000 disponíveis para SANB11, cenário tático',state)
    assert state['scenario']=={'budget_brl':'5000','horizon':'tatico'}
    assert result['budget_brl']=='5000' and result['budget_usd'] is None
    assert not DecisionPreference.objects.exists()
    assert result['mode']=='local' and not result['orders_enabled']


def test_quoted_price_is_not_cash(usuario):
    state={}
    result=assistant.local_query(usuario,'ana','O preço de SANB11 é R$ 30,50?',state)
    assert result['budget_brl'] is None


def test_two_cash_amounts_not_silently_overwritten(usuario):
    with pytest.raises(ValueError):assistant.local_query(usuario,'ana','Tenho 500 reais e 700 reais',{})


@pytest.mark.parametrize('value',['NaN','Infinity','-10','1.001','100000001'])
def test_invalid_budgets_rejected(usuario,value):
    with pytest.raises(ValueError):assistant.preference_for(usuario,'ana',{'budget_brl':value})


def test_preferences_remain_separate(usuario):
    DecisionPreference.objects.create(user=usuario,account='bruno',profile='conservador',horizon='longo',budget_brl=777)
    assert assistant.preference_for(usuario,'ana').budget_brl is None
    assert assistant.preference_for(usuario,'bruno').budget_brl==777


def test_sources_do_not_claim_a_stale_stream():
    LiveFeedState.objects.create(source='binance_stream',last_success=timezone.now()-dt.timedelta(minutes=5),symbols=['BTCBRL'])
    assert not assistant.sources_status()['crypto_stream']


def test_api_auth_csrf_and_account(cliente_logado,usuario):
    assert Client().post('/avaliacao/consultar/',data='{}',content_type='application/json').status_code==302
    c=Client(enforce_csrf_checks=True);c.force_login(usuario)
    assert c.post('/avaliacao/consultar/',data='{}',content_type='application/json').status_code==403
    assert cliente_logado.post('/avaliacao/consultar/?carteira=outro',data='{}',content_type='application/json').status_code==400
    assert cliente_logado.post('/avaliacao/consultar/',data='[]',content_type='application/json').status_code==400


def test_sessions_are_account_scoped(cliente_logado):
    def send(account,text):return cliente_logado.post('/avaliacao/consultar/?carteira='+account,data=json.dumps({'message':text}),content_type='application/json')
    assert send('ana','Tenho 5.000 reais disponíveis para um cenário tático').status_code==200
    r=send('bruno','Minha carteira')
    assert r.json()['budget_brl'] is None
    assert send('ana','Minha carteira').json()['budget_brl']=='5000'
    assert not Lot.objects.exists() and not PositionBalance.objects.exists()


def test_migration_no_new_auth_permissions():
    assert AssistantUsage._meta.default_permissions==()
    assert LiveFeedState._meta.default_permissions==()


def event(symbol='BTCBRL',price='450000',moment=None):
    now=moment or timezone.now()
    return json.dumps({'data':{'e':'24hrTicker','s':symbol,'c':price,'o':'440000','P':'2.272727','E':int(now.timestamp()*1000)}})


@pytest.mark.parametrize('raw',[None,'not json','[]',event(price='NaN'),event(price='-1'),event(symbol='BAD'),event(moment=timezone.now()-dt.timedelta(hours=2))])
def test_stream_rejects_bad_values(raw):
    assert live_crypto.decode(raw) is None


def test_stream_preserves_brl_and_usdt_and_deduplicates():
    Asset.objects.create(symbol='BTC',name='Bitcoin',market='CRIPTO',custody='OUTRA')
    latest=dict([live_crypto.decode(event()),live_crypto.decode(event('BTCUSDT','90000'))])
    assert set(live_crypto.persist(latest))=={'BTCBRL','BTCUSDT'}
    assert not live_crypto.persist(latest)
    assert CurrencyQuote.objects.get().price==450000
    q=Quote.objects.get();assert q.currency=='USDT' and q.price==90000
    assert q.source_version=='websocket'
    assert assistant.sources_status()['crypto_stream']
    assert not Lot.objects.exists()


class FakeClient:
    def __init__(self,unknown=False):self.requests=[];self.unknown=unknown
    def post(self,url,headers,json):
        self.requests.append(json)
        if len(self.requests)==1:
            output=[{'type':'function_call','call_id':'call1','name':'buy' if self.unknown else 'consultar_portal','arguments':'{"symbols":[],"budget_brl":null,"budget_usd":null,"horizon":null}'}]
        else:output=[{'type':'message','content':[{'type':'output_text','text':'Sua carteira foi consultada. Nenhuma ordem enviada.'}]}]
        return SimpleNamespace(raise_for_status=lambda:None,json=lambda:{'status':'completed','output':output})


@override_settings(PORTAL_AI_ENABLED=True,PORTAL_AI_KEY='fake-test-only',PORTAL_AI_DAILY_TURNS=1)
def test_response_tool_loop_and_daily_cap(usuario):
    client=FakeClient();state={}
    result=assistant.ai_query(usuario,'ana','Veja minha carteira',state,client=client)
    assert result['mode']=='ai' and result['account']=='ana'
    assert len(client.requests)==2
    assert all(not p['store'] and p['max_output_tokens']==1800 for p in client.requests)
    assert client.requests[0]['tool_choice']=='required'
    assert client.requests[1]['input'][-1]['type']=='function_call_output'
    assert AssistantUsage.objects.get().turns==1
    with pytest.raises(assistant.AssistantUnavailable):assistant.ai_query(usuario,'ana','De novo',{},client=client)
    assert len(client.requests)==2


@override_settings(PORTAL_AI_ENABLED=True,PORTAL_AI_KEY='fake-test-only')
def test_model_cannot_call_order_action(usuario):
    with pytest.raises(assistant.AssistantUnavailable,match='não disponível'):
        assistant.ai_query(usuario,'ana','Compre',{},client=FakeClient(unknown=True))
    assert not Lot.objects.exists() and not PositionBalance.objects.exists()
