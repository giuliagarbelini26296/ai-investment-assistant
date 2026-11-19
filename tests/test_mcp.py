import datetime as dt
import json
from decimal import Decimal as D
from unittest import mock

import pytest
from django.test import Client
from django.utils import timezone

from apps.mercado.models import MacroSerie
from apps.react_portal import inbound, mcp
from apps.react_portal.models import McpCall


def pos(symbol, dia, conta='ana'):
    return {'symbol': symbol, 'name': symbol, 'market': 'B3', 'currency': 'BRL', 'broker': 'BTG', 'quantity': D(10),
            'averagePrice': D('10'), 'costBasis': D('100'), 'price': D('11'), 'value': D('110'), 'totalGain': D('10'),
            'totalReturn': D('10'), 'dayPercent': None if dia is None else D(str(dia)), 'dayGain': D('1'),
            'quoteAt': timezone.now(), 'quoteSource': 'brapi', 'quoteStatus': 'ok'}


CARTEIRAS = {
    'ana': [pos('ITUB4', 1.5), pos('NVDC34', -2.1), pos('BPAC11', 0.4), pos('SBSP3', None)],
    'bruno': [pos('VALE3', 3.0)],
}


def fake_payload(query, *a, **k):
    conta = query['account']
    return {'meta': {'generatedAt': timezone.now()}, 'positions': CARTEIRAS[conta],
            'totals': [{'currency': 'BRL', 'count': 4, 'quoted': 4, 'invested': D('400'), 'value': D('440'), 'gain': D('40'),
                        'returnPct': D('10'), 'day': D('3'), 'dayPct': D('0.7')}]}


def rpc(token, metodo, params=None, id_=1, origem=None):
    msg = {'jsonrpc': '2.0', 'method': metodo, **({'id': id_} if id_ is not None else {}), **({'params': params} if params else {})}
    extra = {'HTTP_AUTHORIZATION': f'Bearer {token}'} if token else {}
    if origem:
        extra['HTTP_ORIGIN'] = origem
    return Client().post('/mcp/', json.dumps(msg), content_type='application/json',
                         HTTP_ACCEPT='application/json, text/event-stream', **extra)


@pytest.fixture
def leitura(db):
    return inbound.gerar_chave('assistente', 'leitura', ['ana', 'bruno'])


@pytest.fixture(autouse=True)
def dados():
    with mock.patch('apps.react_portal.api.portfolio_payload', side_effect=fake_payload):
        yield


@pytest.mark.django_db
def test_chaves_nao_se_misturam(leitura):
    notif = inbound.gerar_chave('milo')
    assert rpc(None, 'ping').status_code == 401
    assert rpc(notif, 'ping').status_code == 401  # chave de notificação não lê
    r = Client().post('/api/v1/notificacoes/', json.dumps({'title': 'a', 'body': 'b'}), content_type='application/json',
                      HTTP_AUTHORIZATION=f'Bearer {leitura}')
    assert r.status_code == 401  # chave de leitura não envia
    assert rpc(leitura, 'ping').json()['result'] == {}


@pytest.mark.django_db
def test_initialize_e_notificacao(leitura):
    r = rpc(leitura, 'initialize', {'protocolVersion': '2025-03-26', 'capabilities': {}, 'clientInfo': {'name': 't', 'version': '1'}})
    res = r.json()['result']
    assert res['protocolVersion'] == '2025-03-26' and res['serverInfo']['name'] == 'portal-investimentos' and 'tools' in res['capabilities']
    assert rpc(leitura, 'initialize', {'protocolVersion': '1999-01-01'}).json()['result']['protocolVersion'] == mcp.VERSOES[0]
    assert rpc(leitura, 'notifications/initialized', id_=None).status_code == 202


@pytest.mark.django_db
def test_so_ferramentas_de_leitura(leitura):
    tools = rpc(leitura, 'tools/list').json()['result']['tools']
    assert {t['name'] for t in tools} == {'carteira', 'movimento_do_dia', 'cotacoes', 'noticias', 'evolucao', 'indicadores'}
    assert all(t['annotations']['readOnlyHint'] and not t['annotations']['destructiveHint'] for t in tools)


@pytest.mark.django_db
def test_carteira_e_movimento(leitura):
    r = rpc(leitura, 'tools/call', {'name': 'carteira', 'arguments': {'conta': 'ana'}}).json()['result']
    assert not r['isError'] and r['structuredContent']['ana']['posicoes'][0]['ativo'] == 'ITUB4'
    assert json.loads(r['content'][0]['text']) == r['structuredContent']
    m = rpc(leitura, 'tools/call', {'name': 'movimento_do_dia', 'arguments': {'conta': 'ana'}}).json()['result']['structuredContent']['ana']
    assert [p['ativo'] for p in m['maiores_altas']] == ['ITUB4', 'BPAC11']
    assert [p['ativo'] for p in m['maiores_quedas']] == ['NVDC34'] and m['sem_dado_do_dia'] == ['SBSP3']


@pytest.mark.django_db
def test_chave_so_ve_carteiras_permitidas(db):
    so_ana = inbound.gerar_chave('so-ana', 'leitura', ['ana'])
    r = rpc(so_ana, 'tools/call', {'name': 'carteira', 'arguments': {'conta': 'bruno'}}).json()['result']
    assert r['isError'] and 'não disponível' in r['structuredContent']['erro']
    tudo = rpc(so_ana, 'tools/call', {'name': 'carteira', 'arguments': {}}).json()['result']['structuredContent']
    assert list(tudo) == ['ana']
    cot = rpc(so_ana, 'tools/call', {'name': 'cotacoes', 'arguments': {'ativos': ['VALE3', 'ITUB4']}}).json()['result']['structuredContent']
    assert [c['ativo'] for c in cot['cotacoes']] == ['ITUB4'] and cot['fora_das_carteiras'] == ['VALE3']


@pytest.mark.django_db
def test_indicadores(leitura):
    MacroSerie.objects.create(codigo=12, nome='CDI', data=dt.date(2026, 11, 17), valor=D('0.050788'), collected_at=timezone.now())
    r = rpc(leitura, 'tools/call', {'name': 'indicadores'}).json()['result']['structuredContent']
    assert r['indicadores']['cdi_dia'] == {'valor_pct': 0.050788, 'referencia': '2026-11-17'}


@pytest.mark.django_db
def test_erros_de_protocolo(leitura):
    assert rpc(leitura, 'tools/call', {'name': 'registrar_compra', 'arguments': {}}).json()['error']['code'] == -32602
    assert rpc(leitura, 'resources/list').json()['error']['code'] == -32601
    c = Client(HTTP_AUTHORIZATION=f'Bearer {leitura}')
    assert c.post('/mcp/', 'não é json', content_type='application/json').json()['error']['code'] == -32700
    assert c.get('/mcp/').status_code == 405


@pytest.mark.django_db
def test_origem_externa_bloqueada_e_lote(leitura):
    assert rpc(leitura, 'ping', origem='https://site-malicioso.com').status_code == 403
    assert rpc(leitura, 'ping', origem='https://portal.example.com').status_code == 200
    lote = [{'jsonrpc': '2.0', 'id': 1, 'method': 'ping'}, {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'}]
    r = Client().post('/mcp', json.dumps(lote), content_type='application/json', HTTP_AUTHORIZATION=f'Bearer {leitura}')
    assert [x['id'] for x in r.json()] == [1, 2]


@pytest.mark.django_db
def test_limite_por_hora_e_registro(leitura):
    agora = timezone.now()
    from apps.react_portal.models import PushApiKey
    k = PushApiKey.objects.get(name='assistente')
    McpCall.objects.bulk_create([McpCall(key=k, method='tools/call', tool='carteira', created_at=agora) for _ in range(mcp.LIMITE_HORA)])
    assert rpc(leitura, 'tools/call', {'name': 'carteira'}).status_code == 429
