"""Cálculo puro na moeda nativa — exemplos documentados na proposta técnica (seção 7) e casos de borda."""

from decimal import Decimal

import pytest

from apps.valuation import core
from apps.valuation.core import Lote, avaliar, consolidar, pesos, posicao

D = Decimal


def test_exemplo_da_proposta_v1_0():
    # 10 ações a R$ 20 e 20 a R$ 23 → 30 ações, investimento R$ 660, PM R$ 22.
    pos = posicao([Lote(10, 20), Lote(20, 23)])
    assert pos.quantidade == D("30")
    assert pos.investido == D("660")
    assert pos.preco_medio == D("22")
    # Preço hipotético R$ 24 → valor R$ 720, resultado R$ 60, retorno 9,09%.
    aval = avaliar(pos, 24)
    assert aval.valor_atual == D("720")
    assert aval.resultado == D("60")
    assert core.arredondar_pct(aval.retorno_pct) == D("9.09")
    assert aval.diferenca_unitaria == D("2")


def test_lote_unico():
    pos = posicao([Lote("1", "52.53")])
    assert pos.preco_medio == D("52.53")
    assert pos.lotes == 1


def test_cripto_fracionaria_mantem_precisao_decimal():
    pos = posicao([Lote("0.0125", "61000.50"), Lote("0.0075", "58000")])
    assert pos.quantidade == D("0.02")
    assert pos.investido == D("0.0125") * D("61000.50") + D("0.0075") * D("58000")
    assert pos.preco_medio == pos.investido / D("0.02")
    aval = avaliar(pos, "81207.975")
    assert aval.valor_atual == D("0.02") * D("81207.975")


def test_sem_lotes_nao_ha_posicao():
    assert posicao([]) is None


@pytest.mark.parametrize("q, p", [(0, 10), (-1, 10), (1, 0), (1, -5), ("abc", 1), (1, "nan")])
def test_lote_rejeita_valores_invalidos(q, p):
    with pytest.raises(core.ErroDeCalculo):
        Lote(q, p)


def test_avaliar_rejeita_preco_nao_positivo():
    pos = posicao([Lote(1, 10)])
    with pytest.raises(core.ErroDeCalculo):
        avaliar(pos, 0)


def test_variacao_diaria_usa_fechamento_anterior():
    pos = posicao([Lote(1, 10)])
    aval = avaliar(pos, "52.53", fechamento_anterior="52.51")
    assert core.arredondar_pct(aval.variacao_dia_pct) == D("0.04")
    assert avaliar(pos, 10).variacao_dia_pct is None


def test_prejuizo_tem_sinal_negativo():
    aval = avaliar(posicao([Lote(10, 100)]), 90)
    assert aval.resultado == D("-100")
    assert aval.retorno_pct == D("-10")


def test_consolidado_soma_resultados_e_nao_faz_media_de_percentuais():
    a = posicao([Lote(10, 100)])  # investido 1000
    b = posicao([Lote(1, 100)])  # investido 100
    aval_a = avaliar(a, 110)  # +10%  (+100)
    aval_b = avaliar(b, 150)  # +50%  (+50)
    c = consolidar("USD", [("A", a, aval_a), ("B", b, aval_b)])
    assert c.resultado == D("150")
    assert c.investido_total == D("1100")
    # média dos percentuais seria 30%; o correto é 150/1100 = 13,64%
    assert core.arredondar_pct(c.retorno_pct) == D("13.64")
    assert c.completo is True


def test_consolidado_parcial_quando_falta_preco():
    a = posicao([Lote(10, 100)])
    b = posicao([Lote(5, 20)])
    c = consolidar("BRL", [("A", a, avaliar(a, 120)), ("B", b, None)])
    assert c.posicoes_total == 2
    assert c.posicoes_avaliadas == 1
    assert c.completo is False
    assert c.sem_preco == ["B"]
    assert c.investido_total == D("1100")  # cobre todas
    assert c.investido_avaliado == D("1000")  # só as avaliadas
    assert c.valor_atual == D("1200")
    assert c.retorno_pct == D("20")
    assert c.cobertura == "1 de 2 posições com preço"


def test_consolidado_sem_nenhum_preco_nao_tem_retorno():
    a = posicao([Lote(1, 10)])
    c = consolidar("USD", [("A", a, None)])
    assert c.retorno_pct is None
    assert c.valor_atual == D("0")


def test_pesos_somam_100():
    a = avaliar(posicao([Lote(1, 100)]), 300)
    b = avaliar(posicao([Lote(1, 100)]), 100)
    p = pesos([("A", a), ("B", b)])
    assert p["A"] == D("75")
    assert p["B"] == D("25")
    assert pesos([]) == {}


def test_arredondamento_so_na_apresentacao():
    aval = avaliar(posicao([Lote(3, 10)]), "10.005")
    assert aval.resultado == D("0.015")  # exato
    assert core.arredondar_moeda(aval.resultado) == D("0.02")  # comercial
    assert core.arredondar_moeda(None) is None
