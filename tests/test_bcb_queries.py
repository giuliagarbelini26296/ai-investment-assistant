"""Regressões confirmadas contra as respostas públicas do BCB: URI e limite de 20."""
import datetime as dt
from decimal import Decimal

import httpx
import pytest

from apps.marketdata.adapters.inteligencia import FocusBcbAdapter, SgsBcbAdapter
from apps.marketdata.models import ScheduleState
from apps.mercado import coleta


def test_focus_envia_espacos_percent20_e_preserva_acentos_e_filtro():
    requests = []
    def handler(req):
        requests.append(req)
        if '+' in str(req.url):
            return httpx.Response(400, json={'mensagem': 'The URI is malformed.'})
        return httpx.Response(200, json={'value': [{'Indicador': 'Câmbio', 'Data': '2026-10-30',
            'DataReferencia': '2026', 'Mediana': 5.21, 'baseCalculo': 0}]})
    adapter = FocusBcbAdapter(client=httpx.Client(transport=httpx.MockTransport(handler)))
    item, = adapter.expectativas()
    assert item.mediana == Decimal('5.21')
    assert item.indicador == 'Câmbio'
    assert '%20' in str(requests[0].url)
    assert 'Câmbio' in requests[0].url.params['$filter']
    assert "Indicador eq 'Selic'" in requests[0].url.params['$filter']


def test_sgs_default_respeita_limite_real_e_nao_trunca_silenciosamente():
    requests = []
    def handler(req):
        requests.append(req)
        n = int(req.url.path.rsplit('/', 1)[1])
        return httpx.Response(200 if n <= 20 else 400,
            json=[{'data': '04/11/2026', 'valor': '13.75'}] if n <= 20 else {'erro': 'máximo 20'})
    adapter = SgsBcbAdapter(client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert adapter.serie(432) == [(dt.date(2026, 11, 4), Decimal('13.75'))]
    assert requests[0].url.path.endswith('/ultimos/20')
    with pytest.raises(ValueError, match='1 a 20'):
        adapter.serie(432, 24)
    assert len(requests) == 1


def test_reconsulta_explicita_mantem_horarios_reais_e_ciclo_diario(db, settings, monkeypatch):
    settings.PORTAL = {**settings.PORTAL, 'MODO_DEMO': False}
    now = dt.datetime(2026, 11, 4, 13, 10, tzinfo=dt.UTC)
    previous = now - dt.timedelta(minutes=5)
    ScheduleState.objects.create(task='FOCUS', last_attempt_at=previous, last_status='falha', last_error='HTTP 400')
    ScheduleState.objects.create(task='MACRO', last_attempt_at=previous, last_status='falha', last_error='HTTP 400')
    calls = []
    class Adapter:
        def expectativas(self, *args):
            calls.append('FOCUS')
            return []
        def serie(self, code, n):
            assert n == 20
            calls.append(code)
            return [(dt.date(2026, 11, 3), Decimal('1'))]
    monkeypatch.setattr(coleta, 'criar_adaptador', lambda source: Adapter())
    assert coleta.focus_passo(now) == {} and coleta.macro_passo(now) == {}
    assert not calls
    assert coleta.focus_passo(now, forcar=True) == {'focus': 0}
    assert coleta.macro_passo(now, forcar=True) == {'macro': 4}  # inclui CDI (12)
    for task in ['FOCUS', 'MACRO']:
        state = ScheduleState.objects.get(task=task)
        assert state.last_attempt_at == now and state.last_success_at == now
        assert state.last_status == 'completo' and state.last_error == ''
    assert coleta.focus_passo(now + dt.timedelta(hours=2)) == {}
    assert coleta.macro_passo(now + dt.timedelta(hours=2)) == {}
