import datetime as dt
from decimal import Decimal as D
from unittest import mock

import pytest
from django.utils import timezone

from apps.mercado.models import MacroSerie
from apps.react_portal import evolution
from apps.react_portal.models import PatrimonioDiario


def _payload(value, invested, count=3, quoted=3):
    return {'totals': [{'currency': 'BRL', 'count': count, 'quoted': quoted, 'value': D(value),
                        'invested': D(invested), 'gain': D(value) - D(invested)}]}


@pytest.mark.django_db
def test_foto_diaria_atualiza_o_mesmo_dia_sem_duplicar():
    agora = timezone.make_aware(dt.datetime(2026, 11, 18, 15, 0))
    with mock.patch('apps.react_portal.api.portfolio_payload', side_effect=lambda q, *a, **k: _payload('110', '100')):
        assert evolution.registrar_patrimonio(agora) == 2  # ana e bruno
    with mock.patch('apps.react_portal.api.portfolio_payload', side_effect=lambda q, *a, **k: _payload('120', '100')):
        assert evolution.registrar_patrimonio(agora + dt.timedelta(minutes=1)) == 0  # dentro do intervalo
        assert evolution.registrar_patrimonio(agora + dt.timedelta(minutes=6)) == 2
    fotos = PatrimonioDiario.objects.filter(account='ana')
    assert fotos.count() == 1 and fotos.get().value == D('120') and fotos.get().complete


@pytest.mark.django_db
def test_foto_incompleta_quando_falta_cotacao():
    agora = timezone.now()
    with mock.patch('apps.react_portal.api.portfolio_payload', side_effect=lambda q, *a, **k: _payload('50', '40', 3, 2)):
        evolution.registrar_patrimonio(agora, forcar=True)
    assert not PatrimonioDiario.objects.filter(account='ana').get().complete


@pytest.mark.django_db
def test_cdi_acumulado_usa_so_taxas_publicadas():
    agora = timezone.now()
    for dia, taxa in [(17, '0.055'), (18, '0.055')]:
        MacroSerie.objects.create(codigo=12, nome='CDI', data=dt.date(2026, 11, dia), valor=D(taxa), collected_at=agora)
    datas = [dt.date(2026, 11, 17), dt.date(2026, 11, 18), dt.date(2026, 11, 19), dt.date(2026, 11, 20)]
    cdi = evolution.cdi_acumulado(datas)
    assert cdi[0] == 0
    assert cdi[1] == D('0.055')
    assert round(cdi[2], 6) == round(((1 + D('0.00055')) ** 2 - 1) * 100, 6)
    assert cdi[3] is None  # taxa de 19/11 ainda não publicada: não supor


@pytest.mark.django_db
def test_api_evolucao_exige_login(client):
    assert client.get('/api/v1/evolution/?account=ana').status_code == 401


@pytest.mark.django_db
def test_api_evolucao_bruno_mostra_patrimonio_e_aguarda_dois_pregoes(cliente_logado):
    PatrimonioDiario.objects.create(account='bruno', currency='BRL', date=dt.date(2026, 11, 18), value=D('110'),
                                    invested=D('100'), gain=D('10'), positions=2, quoted=2, captured_at=timezone.now())
    r = cliente_logado.get('/api/v1/evolution/?account=bruno')
    assert r.status_code == 200
    body = r.json()
    assert body['snapshots']['BRL'][0]['complete'] is True and body['snapshots']['BRL'][0]['source'] == 'portal'
    assert body['performance']['BRL']['points'] == [] and 'dois pregões' in body['performance']['BRL']['warnings'][0]
    assert cliente_logado.get('/api/v1/evolution/?account=joao').status_code == 400


def _foto(conta, dia, valor, investido, fluxo=None, proventos='0', origem='portal'):
    return PatrimonioDiario.objects.create(account=conta, currency='BRL', date=dt.date(2026, 11, dia), value=D(valor),
                                           invested=D(investido), gain=D(valor) - D(investido), positions=1, quoted=1,
                                           captured_at=timezone.now(), source=origem,
                                           flow=None if fluxo is None else D(fluxo), income=D(proventos))


@pytest.mark.django_db
def test_rentabilidade_desconta_aportes_e_soma_proventos():
    from apps.insights.models import BenchmarkBar
    for dia, close in [(3, '100'), (4, '101'), (5, '102')]:
        BenchmarkBar.objects.create(symbol='BOVA11', date=dt.date(2026, 11, dia), close=D(close), source='t', collected_at=timezone.now())
    _foto('bruno', 3, '1000', '1000', origem='btg')
    _foto('bruno', 4, '2100', '2000', fluxo='1000', origem='btg')   # aporte de 1000 não é ganho
    _foto('bruno', 5, '2100', '2000', proventos='21')                # provento de 21 é ganho
    perf = evolution.evolution_payload('bruno')['performance']['BRL']
    assert [round(p['portfolio'], 4) for p in perf['points']] == [0, D('10.0000'), D('11.1000')]
    assert round(perf['reference'], 4) == D('2.0000') and perf['reconstructedUntil'] == dt.date(2026, 11, 4)


@pytest.mark.django_db
def test_fluxo_do_dia_usa_valor_recebido_na_venda():
    from apps.portfolio.models import Asset, Sale
    _foto('ana', 12, '1000', '1000')
    a = Asset.objects.create(symbol='ABCD3', name='x', market='B3', custody='BTG')
    Sale.objects.create(account='ana', asset=a, trade_date=dt.date(2026, 11, 13), quantity=D('10'), unit_price=D('30'),
                        currency='BRL', allocated_cost=D('200'), before_quantity=D('10'), before_cost=D('200'))
    # investido cai 200 (custo), mas saíram 300 (valor recebido): fluxo = -300
    assert evolution.fluxo_do_dia('ana', 'BRL', dt.date(2026, 11, 13), D('800')) == D('-300')
    assert evolution.fluxo_do_dia('ana', 'BRL', dt.date(2026, 11, 12), D('1000')) is None


@pytest.mark.django_db
def test_reconstrucao_usa_historico_btg_e_recusa_venda_sem_preco():
    from apps.portfolio.models import Asset
    from apps.react_portal import reconstrucao as rc

    class P:
        de_cotacao = set()
        def pregoes(self, moeda): return [dt.date(2026, 11, d) for d in (5, 6)]
        def preco(self, a, d, moeda): return D('10') if d.day == 5 else D('11')

    a = Asset.objects.create(symbol='ABCD3', name='x', market='B3', custody='BTG')
    estados = {a: rc._historico([(dt.date(2026, 11, 5), '100', '1000'), (dt.date(2026, 11, 6), '150', '1500')])}
    linhas = rc.serie('bruno', 'BRL', dt.date(2026, 11, 6), P(), estados)
    assert [(l['value'], l['invested'], l['flow']) for l in linhas] == [(D('1000'), D('1000'), None), (D('1650'), D('1500'), D('500'))]
    estados = {a: rc._historico([(dt.date(2026, 11, 5), '100', '1000'), (dt.date(2026, 11, 6), '50', '500')])}
    with pytest.raises(ValueError, match='venda sem registro'):
        rc.serie('bruno', 'BRL', dt.date(2026, 11, 6), P(), estados)


@pytest.mark.django_db
def test_api_evolucao_ana_sem_posicoes_responde(cliente_logado):
    r = cliente_logado.get('/api/v1/evolution/?account=ana')
    assert r.status_code == 200 and r.json()['performance'] == {}  # sem fotos, sem moeda
