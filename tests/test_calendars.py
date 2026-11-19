import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from apps.marketdata.calendars import calendario

NY = ZoneInfo("America/New_York")
SP = ZoneInfo("America/Sao_Paulo")


def em(ano, mes, dia, h, m, tz):
    return dt.datetime(ano, mes, dia, h, m, tzinfo=tz)


def test_nyse_pregao_e_feriados():
    cal = calendario("NASDAQ")
    assert cal.codigo == "NYSE"
    assert cal.eh_pregao(dt.date(2026, 10, 29)) is True  # quinta
    assert cal.eh_pregao(dt.date(2026, 10, 31)) is False  # sábado
    assert cal.eh_pregao(dt.date(2026, 11, 26)) is False  # Thanksgiving
    assert cal.sessao(dt.date(2026, 11, 27)).fechamento.time() == dt.time(13, 0)  # fechamento antecipado


def test_nyse_horario_de_verao_desloca_sessao_em_sao_paulo():
    cal = calendario("NYSE")
    # 29/10/2026: EUA ainda em horário de verão (EDT) → 9h30 NY = 10h30 SP
    s1 = cal.sessao(dt.date(2026, 10, 29))
    assert s1.abertura.astimezone(SP).time() == dt.time(10, 30)
    # 03/11/2026: após o fim do horário de verão (01/11) → 9h30 NY = 11h30 SP
    s2 = cal.sessao(dt.date(2026, 11, 3))
    assert s2.abertura.astimezone(SP).time() == dt.time(11, 30)
    assert s2.fechamento.astimezone(SP).time() == dt.time(18, 0)


def test_nyse_situacao_aberto_fechado_e_ultima_sessao():
    cal = calendario("NYSE")
    assert cal.situacao(em(2026, 10, 29, 12, 0, NY)).estado == "aberto"
    fechado = cal.situacao(em(2026, 10, 29, 16, 30, NY))
    assert fechado.estado == "fechado" and fechado.sessao.data == dt.date(2026, 10, 29)
    # sábado → última sessão encerrada é sexta 30/10
    sab = cal.situacao(em(2026, 10, 31, 12, 0, NY))
    assert sab.estado == "fechado" and sab.sessao.data == dt.date(2026, 10, 30)
    # segunda antes da abertura → última é sexta
    seg = cal.situacao(em(2026, 11, 2, 8, 0, NY))
    assert seg.sessao.data == dt.date(2026, 10, 30)
    assert cal.proxima_abertura(em(2026, 10, 31, 12, 0, NY)) == em(2026, 11, 2, 9, 30, NY)


def test_b3_feriados_e_horario():
    cal = calendario("B3")
    assert cal.eh_pregao(dt.date(2026, 11, 20)) is False  # Consciência Negra
    assert cal.eh_pregao(dt.date(2026, 2, 17)) is False  # Carnaval
    s = cal.sessao(dt.date(2026, 10, 29))
    assert (s.abertura.time(), s.fechamento.time()) == (dt.time(10, 0), dt.time(17, 0))
    assert cal.situacao(em(2026, 10, 29, 9, 59, SP)).estado == "fechado"
    assert cal.situacao(em(2026, 10, 29, 10, 0, SP)).estado == "aberto"


def test_ano_sem_cobertura_e_desconhecido():
    cal = calendario("B3")
    assert cal.situacao(em(2031, 5, 5, 12, 0, SP)).estado == "desconhecido"
    assert cal.cobre(dt.date(2027, 1, 1)) and not cal.cobre(dt.date(2031, 1, 1))


def test_cripto_sempre_aberto_dia_utc():
    cal = calendario("CRIPTO")
    agora = dt.datetime(2026, 10, 29, 23, 30, tzinfo=SP)  # 02:30 UTC de 30/10
    sit = cal.situacao(agora)
    assert sit.estado == "aberto" and sit.sessao.data == dt.date(2026, 10, 30)
    assert cal.ultima_sessao_encerrada(agora).data == dt.date(2026, 10, 29)


@pytest.mark.parametrize("mercado", ["NYSE", "NASDAQ", "B3", "CRIPTO"])
def test_todos_os_mercados_tem_calendario(mercado):
    assert calendario(mercado).situacao(em(2026, 10, 29, 12, 0, SP)).estado in {"aberto", "fechado"}
