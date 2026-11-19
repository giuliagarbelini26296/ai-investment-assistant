import datetime as dt

import pytest

from apps.marketdata.adapters.base import FonteInfo
from apps.marketdata.budget import OrcamentoExcedido, reservar, saldo
from apps.marketdata.models import SourceBudget

INFO = FonteInfo(
    codigo="fonte_x",
    nome="x",
    tipo_dado="atrasado",
    atraso_declarado_min=15,
    atraso_declarado_texto="15",
    limites="",
    termos_url="",
    limite_minuto=8,
    limite_dia=800,
)
T = dt.datetime(2026, 10, 29, 14, 35, tzinfo=dt.UTC)


def test_reserva_consome_em_todos_os_periodos(db):
    reservar(INFO, 5, T)
    assert saldo(INFO, T) == {"minuto": (5, 8), "dia": (5, 800)}
    reservar(INFO, 3, T)
    assert saldo(INFO, T)["minuto"] == (8, 8)


def test_estouro_do_minuto_bloqueia_sem_consumir(db):
    reservar(INFO, 8, T)
    with pytest.raises(OrcamentoExcedido) as exc:
        reservar(INFO, 1, T)
    assert exc.value.periodo == "minuto"
    assert saldo(INFO, T)["dia"] == (8, 800)  # nada consumido na tentativa bloqueada


def test_minuto_seguinte_reabre_e_dia_acumula(db):
    reservar(INFO, 8, T)
    reservar(INFO, 8, T + dt.timedelta(minutes=1))
    assert saldo(INFO, T + dt.timedelta(minutes=1)) == {"minuto": (8, 8), "dia": (16, 800)}


def test_estouro_do_dia(db):
    SourceBudget.objects.create(
        source="fonte_x", period="dia", period_key="2026-10-29", limit=800, consumed=799
    )
    with pytest.raises(OrcamentoExcedido) as exc:
        reservar(INFO, 2, T)
    assert exc.value.periodo == "dia"


def test_fonte_sem_limites_nunca_bloqueia(db):
    livre = FonteInfo(
        codigo="livre",
        nome="",
        tipo_dado="spot",
        atraso_declarado_min=0,
        atraso_declarado_texto="",
        limites="",
        termos_url="",
    )
    assert reservar(livre, 10_000, T) == {}
