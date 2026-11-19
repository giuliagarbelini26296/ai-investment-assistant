import datetime as dt
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from apps.marketdata.freshness import atualidade
from apps.marketdata.models import Quote
from apps.portfolio.models import Asset

NY = ZoneInfo("America/New_York")
SP = ZoneInfo("America/Sao_Paulo")


@pytest.fixture
def ttwo(db):
    return Asset.objects.create(symbol="TTWO", name="Take-Two", market="NASDAQ", custody="AVENUE")


@pytest.fixture
def btc(db):
    return Asset.objects.create(symbol="BTC", name="Bitcoin", market="CRIPTO", custody="BINANCE")


def cot(asset, coleta, mercado=None, tipo="atrasado", atraso=30, fonte="brapi"):
    return Quote.objects.create(
        asset=asset,
        price=Decimal("10"),
        currency="USD",
        collected_at=coleta,
        market_time=mercado,
        source=fonte,
        quote_type=tipo,
        declared_delay_minutes=atraso,
    )


def test_sem_cotacao(ttwo):
    a = atualidade(ttwo, dt.datetime(2026, 10, 29, 12, 0, tzinfo=NY))
    assert a.estado == "sem_cotacao" and a.permite_alertas is False


def test_pregao_aberto_atrasado_e_desatualizado(ttwo):
    agora = dt.datetime(2026, 10, 29, 12, 0, tzinfo=NY)
    cot(ttwo, agora - dt.timedelta(minutes=10), agora - dt.timedelta(minutes=40))
    a = atualidade(ttwo, agora)
    assert a.estado == "atrasado" and "30 min" in a.descricao and a.permite_alertas
    # coleta vencida (20 min > 15 + 2) com pregão aberto
    a2 = atualidade(ttwo, agora + dt.timedelta(minutes=10))
    assert a2.estado == "desatualizado" and a2.permite_alertas is False


def test_pregao_aberto_tempo_real_e_nao_declarado(ttwo):
    agora = dt.datetime(2026, 10, 29, 12, 0, tzinfo=NY)
    cot(ttwo, agora - dt.timedelta(minutes=1), tipo="tempo_real", atraso=0)
    assert atualidade(ttwo, agora).estado == "atual"
    Quote.objects.all().delete()
    cot(ttwo, agora - dt.timedelta(minutes=1), tipo="nao_declarado", atraso=None, fonte="twelvedata")
    a = atualidade(ttwo, agora)
    assert a.estado == "nao_declarado" and "não declara" in a.descricao


def test_mercado_fechado_com_fechamento_obtido(ttwo):
    fechamento = dt.datetime(2026, 10, 29, 16, 0, tzinfo=NY)
    cot(ttwo, fechamento + dt.timedelta(minutes=12), fechamento)
    a = atualidade(ttwo, fechamento + dt.timedelta(hours=5))
    assert a.estado == "mercado_fechado" and "29/10/2026" in a.descricao and a.permite_alertas
    # sábado continua "mercado fechado" com o fechamento de sexta... (sexta 30/10 sem coleta → desatualizado)
    a2 = atualidade(ttwo, dt.datetime(2026, 10, 31, 12, 0, tzinfo=NY))
    assert a2.estado == "desatualizado" and "30/10/2026" in a2.descricao


def test_mercado_fechado_sem_fechamento_e_desatualizado(ttwo):
    meio_dia = dt.datetime(2026, 10, 29, 12, 0, tzinfo=NY)
    cot(ttwo, meio_dia, meio_dia - dt.timedelta(minutes=30))
    a = atualidade(ttwo, dt.datetime(2026, 10, 29, 20, 0, tzinfo=NY))
    assert a.estado == "desatualizado" and "ainda não obtido" in a.descricao


def test_cripto_spot_e_desatualizado(btc):
    agora = dt.datetime(2026, 10, 31, 3, 0, tzinfo=dt.UTC)  # sábado, cripto segue
    cot(btc, agora - dt.timedelta(minutes=20), tipo="spot", atraso=0, fonte="binance")
    assert atualidade(btc, agora).estado == "spot"
    assert atualidade(btc, agora + dt.timedelta(minutes=15)).estado == "desatualizado"


def test_calendario_desconhecido_suspende_alertas(ttwo):
    agora = dt.datetime(2031, 3, 5, 12, 0, tzinfo=NY)
    cot(ttwo, agora - dt.timedelta(minutes=1))
    a = atualidade(ttwo, agora)
    assert a.estado == "desconhecido" and a.permite_alertas is False


def test_usa_ultima_cotacao_valida_ignorando_descartadas(ttwo):
    agora = dt.datetime(2026, 10, 29, 12, 0, tzinfo=NY)
    antiga = cot(ttwo, agora - dt.timedelta(minutes=5))
    Quote.objects.create(
        asset=ttwo,
        price=Decimal("1"),
        currency="USD",
        collected_at=agora,
        source="x",
        quote_type="atrasado",
        status="descartada",
    )
    assert atualidade(ttwo, agora).cotacao == antiga
