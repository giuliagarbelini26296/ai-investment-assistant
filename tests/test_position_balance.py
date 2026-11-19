"""Conciliação sem negociações fictícias, com precisão e histórico preservados."""
import datetime as dt
from decimal import Decimal as D

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from apps.marketdata.models import DailyBar, FxDailyRate
from apps.noticias.views import _owned_assets
from apps.portfolio import services
from apps.portfolio.models import Asset, Lot, PositionBalance
from apps.ui.resumo import evolucao_patrimonio

REF = dt.date(2026, 11, 3)


def saldo(asset, quantity='100.47592', price='214.57', key='prints-2026-11-02-03'):
    return services.registrar_saldo(asset, reference_date=REF, quantity=D(quantity),
        average_price=D(price), source_key=key, evidence='Dados confirmados pela usuária; sem datas de aquisição.')


@pytest.fixture
def ttwo(db):
    return Asset.objects.create(symbol='TTWO', name='Take Two', market='NASDAQ', custody='OUTRA')


def test_saldo_preserva_historico_custos_e_fracoes_e_nao_cria_compra(ttwo, usuario):
    lot = services.criar_lote(ttwo, usuario, trade_date=dt.date(2026, 10, 1),
        quantity=D('2'), unit_price=D('244.56'), costs=D('12.34'), fx_rate=D('5.2'))
    lot.refresh_from_db()
    before = lot.snapshot()
    balance = saldo(ttwo)
    assert balance.covered_lot_ids == {lot.pk}
    assert balance.covered_lots[0]['costs'] == '12.34000000'
    lot.refresh_from_db()
    assert lot.snapshot() == before
    assert Lot.objects.count() == 1
    pos = services.posicao_do_ativo(ttwo)
    assert pos.quantidade == D('100.47592')
    assert pos.preco_medio == D('214.57')
    assert pos.investido == D('21559.1181544')
    assert pos.lotes == 0
    # Nova compra posterior soma uma vez; editar o lote coberto não altera o saldo.
    services.criar_lote(ttwo, usuario, trade_date=REF, quantity=D('1.5'), unit_price=D('200'))
    services.alterar_lote(lot, usuario, quantity=D('20'))
    pos = services.posicao_do_ativo(ttwo)
    assert pos.quantidade == D('101.97592')
    assert pos.investido == D('21859.1181544')
    assert pos.lotes == 1


def test_saldo_idempotente_e_append_only(ttwo):
    first = saldo(ttwo)
    assert saldo(ttwo).pk == first.pk
    assert PositionBalance.objects.count() == 1
    with pytest.raises(ValueError):
        saldo(ttwo, quantity='99')
    first.quantity = D('99')
    with pytest.raises(ValidationError):
        first.save()
    second = saldo(ttwo, quantity='101', key='nova-conciliacao')
    assert second.pk != first.pk
    assert services.posicao_do_ativo(ttwo).quantidade == D('101')
    assert PositionBalance.objects.count() == 2


def test_sem_cambio_nao_inventa_ptax_da_data_do_saldo(ttwo):
    saldo(ttwo)
    FxDailyRate.objects.create(date=REF, bid=D('5'), source='bcb', collected_at=timezone.now())
    lines = services.linhas_da_carteira({ttwo.pk: (D('220'), D('210'))}, moeda_visao='BRL')
    assert lines[0].sem_base_cambial
    assert lines[0].investido_visao is None
    assert services.consolidar_por_moeda(lines)['USD'].investido_total == D('21559.1181544')


def test_telas_e_noticias_reconhecem_saldo_sem_lotes(ttwo, cliente_logado):
    saldo(ttwo)
    assert ttwo.has_position
    assert list(_owned_assets()) == [ttwo]
    for view, args in [('home_classico', []), ('asset_list', []), ('asset_detail', [ttwo.pk]), ('asset_painel', [ttwo.pk])]:
        response = cliente_logado.get(reverse(view, args=args))
        assert response.status_code == 200
        assert b'TTWO' in response.content
    html = cliente_logado.get(reverse('asset_detail', args=[ttwo.pk])).content.decode()
    assert 'saldo conciliado' in html
    assert '100,47592' in html
    assert Lot.objects.count() == 0


def test_saldo_b3_rejeita_fracao(db):
    asset = Asset.objects.create(symbol='BPAC11', name='BTG', market='B3', custody='OUTRA')
    with pytest.raises(ValidationError):
        saldo(asset, quantity='300.5', price='50.74')
    saldo(asset, quantity='300', price='50.74')
    assert services.posicao_do_ativo(asset).investido == D('15222')


def test_evolucao_nao_inventa_posicao_antes_da_referencia(ttwo):
    saldo(ttwo)
    for date in [REF-dt.timedelta(days=1), REF]:
        DailyBar.objects.create(asset=ttwo, source='teste', session_date=date,
            open=D('220'), high=D('220'), low=D('220'), close=D('220'), time_basis='sessao_local', collected_at=timezone.now())
    ev = evolucao_patrimonio('USD', dias=2, agora=dt.datetime(2026, 11, 3, 18, tzinfo=dt.UTC))
    assert len(ev.pontos) == 1
    assert ev.pontos[0].data == REF
    assert ev.pontos[0].valor == D('22104.7024')
    assert ev.pontos[0].investido == D('21559.1181544')
    assert any('datas de aquisição' in msg for msg in ev.avisos)
