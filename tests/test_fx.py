"""Conversão cambial: cálculo puro (exemplo da proposta) e serviço de câmbio."""

import datetime as dt
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.urls import reverse

from apps.marketdata import fxservice
from apps.marketdata.models import FxDailyRate, FxRate, Quote
from apps.portfolio import services
from apps.portfolio.models import Asset, FxOrigin
from apps.valuation import core
from apps.valuation.core import Lote, avaliar, posicao
from apps.valuation.fx import LoteComCambio, converter, taxa_para_visao

D = Decimal
SP = ZoneInfo("America/Sao_Paulo")
AGORA = dt.datetime(2026, 10, 29, 14, 0, tzinfo=SP)


# --- Cálculo puro -----------------------------------------------------------------------


def test_exemplo_da_proposta_usd_para_brl():
    # 10 × US$ 100 @ 5,00 e 10 × US$ 120 @ 5,50; P = US$ 130; f = 5,30
    lotes = [
        LoteComCambio(Lote(10, 100), D("5.00"), "informado"),
        LoteComCambio(Lote(10, 120), D("5.50"), "ptax_data"),
    ]
    pos = posicao([item.lote for item in lotes])
    aval = avaliar(pos, 130)
    c = converter(lotes, pos, aval, "USD", "BRL", D("5.30"))
    assert c.investido == D("11600")
    assert c.valor_atual == D("13780")
    assert c.resultado == D("2180")
    assert c.resultado_ativo == D("2120")
    assert core.arredondar_moeda(c.efeito_cambial) == D("60.00")
    assert core.arredondar_moeda(c.resultado_ativo + c.efeito_cambial) == core.arredondar_moeda(c.resultado)
    assert core.arredondar_pct(c.retorno_pct) == D("18.79")  # 2180 / 11600
    assert core.arredondar_pct(aval.retorno_pct) == D("18.18")  # em USD
    assert c.taxa_media_compra.quantize(D("0.0001")) == D("5.2727")
    assert c.lotes_por_origem == {"informado": 1, "ptax_data": 1}


def test_brl_para_usd_usa_inverso_do_cambio():
    lotes = [LoteComCambio(Lote(100, D("50")), D("5.00"), "ptax_data")]  # R$ 5.000 → US$ 1.000
    pos = posicao([lotes[0].lote])
    aval = avaliar(pos, D("55"))  # R$ 5.500
    c = converter(lotes, pos, aval, "BRL", "USD", D("5.50"))  # dólar subiu: R$ 5.500 = US$ 1.000
    assert core.arredondar_moeda(c.investido) == D("1000.00")
    assert core.arredondar_moeda(c.valor_atual) == D("1000.00")  # 1/5,5 é dízima: exato só na apresentação
    assert core.arredondar_moeda(c.resultado) == D("0.00")
    # ativo subiu 10% em BRL (+US$ 90,91 ao câmbio atual) e o câmbio comeu o ganho (−US$ 90,91)
    assert core.arredondar_moeda(c.resultado_ativo) == D("90.91")
    assert core.arredondar_moeda(c.efeito_cambial) == D("-90.91")
    assert c.taxa_media_compra == D("5.00")


def test_lote_sem_base_impede_conversao():
    lotes = [LoteComCambio(Lote(1, 100), D("5"), "informado"), LoteComCambio(Lote(1, 100), None, "sem_base")]
    pos = posicao([item.lote for item in lotes])
    assert converter(lotes, pos, avaliar(pos, 100), "USD", "BRL", D("5.3")) is None


def test_taxa_para_visao_e_erros():
    assert taxa_para_visao("USD", "USD", D("5")) == 1
    assert taxa_para_visao("USD", "BRL", D("5")) == D("5")
    assert taxa_para_visao("BRL", "USD", D("5")) == D("0.2")
    with pytest.raises(core.ErroDeCalculo):
        taxa_para_visao("EUR", "BRL", D("5"))
    lotes = [LoteComCambio(Lote(1, 100), D("5"), "x")]
    pos = posicao([lotes[0].lote])
    with pytest.raises(core.ErroDeCalculo):
        converter(lotes, pos, avaliar(pos, 100), "USD", "USD", D("5"))
    with pytest.raises(core.ErroDeCalculo):
        converter(lotes, pos, avaliar(pos, 100), "USD", "BRL", D("0"))


# --- Serviço de câmbio ------------------------------------------------------------------


def ptax(data, valor):
    return FxDailyRate.objects.create(
        pair="USD-BRL", date=data, bid=D(valor), ask=D(valor) + D("0.0006"), source="t", collected_at=AGORA
    )


def test_cambio_atual_prioriza_intradiario_recente(db):
    ptax(dt.date(2026, 10, 29), "5.30")
    FxRate.objects.create(
        pair="USD-BRL",
        rate_type="intradiario",
        bid=D("5.35"),
        collected_at=AGORA - dt.timedelta(minutes=10),
        rate_time=AGORA - dt.timedelta(minutes=11),
        source="awesome",
    )
    c = fxservice.cambio_atual(AGORA)
    assert c.valor == D("5.35") and c.tipo == "intradiario" and not c.desatualizado
    # intradiário velho (45 min) → PTAX do dia
    c2 = fxservice.cambio_atual(AGORA + dt.timedelta(minutes=35))
    assert c2.valor == D("5.30") and c2.tipo == "ptax_fechamento" and not c2.desatualizado


def test_cambio_atual_ptax_antigo_e_desatualizado(db):
    ptax(dt.date(2026, 10, 23), "5.20")  # sexta
    assert (
        fxservice.cambio_atual(dt.datetime(2026, 10, 26, 10, 0, tzinfo=SP)).desatualizado is False
    )  # segunda ainda tolera
    assert fxservice.cambio_atual(dt.datetime(2026, 10, 27, 10, 0, tzinfo=SP)).desatualizado is True
    assert "desatualizado" in fxservice.cambio_atual(AGORA).rotulo


def test_cambio_atual_sem_nada(db):
    assert fxservice.cambio_atual(AGORA) is None


@pytest.fixture
def ttwo(db):
    return Asset.objects.create(symbol="TTWO", name="Take-Two", market="NASDAQ", custody="AVENUE")


@pytest.fixture
def petr4(db):
    return Asset.objects.create(symbol="PETR4", name="Petrobras", market="B3", custody="OUTRA")


def test_resolucao_do_cambio_de_compra(ttwo, usuario):
    ptax(dt.date(2026, 10, 23), "5.20")  # sexta
    informado = services.criar_lote(
        ttwo,
        usuario,
        trade_date=dt.date(2026, 10, 23),
        quantity=D("1"),
        unit_price=D("1"),
        fx_rate=D("5.4321"),
    )
    na_data = services.criar_lote(
        ttwo, usuario, trade_date=dt.date(2026, 10, 23), quantity=D("1"), unit_price=D("1")
    )
    sabado = services.criar_lote(
        ttwo, usuario, trade_date=dt.date(2026, 10, 24), quantity=D("1"), unit_price=D("1")
    )
    sem_base = services.criar_lote(
        ttwo, usuario, trade_date=dt.date(2026, 10, 1), quantity=D("1"), unit_price=D("1")
    )
    assert fxservice.resolver_cambio_lote(informado) == (D("5.4321"), FxOrigin.INFORMED)
    assert fxservice.resolver_cambio_lote(na_data) == (D("5.20"), FxOrigin.PTAX_DATE)
    assert fxservice.resolver_cambio_lote(sabado) == (D("5.20"), FxOrigin.PTAX_PREV)
    assert fxservice.resolver_cambio_lote(sem_base) == (None, FxOrigin.NO_BASIS)
    sabado.refresh_from_db()
    assert sabado.fx_resolved == D("5.20") and sabado.fx_origin == FxOrigin.PTAX_PREV
    # PTAX carregado depois: a próxima resolução promove de "sem base" para "PTAX da data"
    ptax(dt.date(2026, 10, 1), "5.10")
    assert fxservice.resolver_cambio_lote(sem_base) == (D("5.10"), FxOrigin.PTAX_DATE)


def test_b3_nao_persiste_mas_converte_pelo_ptax(petr4, usuario):
    ptax(dt.date(2026, 10, 1), "5.10")
    lot = services.criar_lote(
        petr4, usuario, trade_date=dt.date(2026, 10, 1), quantity=D("100"), unit_price=D("30")
    )
    assert fxservice.cambio_de_compra_para_visao(lot) == (D("5.10"), FxOrigin.PTAX_DATE)
    lot.refresh_from_db()
    assert lot.fx_origin == FxOrigin.NOT_APPLICABLE and lot.fx_resolved is None


# --- Visão da carteira e telas -------------------------------------------------------------


def cotacao(asset, preco, moeda="USD"):
    return Quote.objects.create(
        asset=asset,
        price=D(preco),
        currency=moeda,
        collected_at=AGORA,
        market_time=AGORA - dt.timedelta(minutes=1),
        source="t",
        quote_type="atrasado",
        declared_delay_minutes=15,
    )


def test_visao_em_brl_converte_usd_e_exclui_sem_base(ttwo, petr4, usuario):
    ptax(dt.date(2026, 10, 29), "5.30")
    ptax(dt.date(2026, 10, 1), "5.00")
    # TTWO: 10 @ 100 (câmbio 5,00 via PTAX) + 10 @ 120 informado 5,50 — exemplo da proposta
    services.criar_lote(ttwo, usuario, trade_date=dt.date(2026, 10, 1), quantity=D("10"), unit_price=D("100"))
    services.criar_lote(
        ttwo,
        usuario,
        trade_date=dt.date(2026, 10, 1),
        quantity=D("10"),
        unit_price=D("120"),
        fx_rate=D("5.50"),
    )
    cotacao(ttwo, "130")
    # PETR4 em BRL: nativo
    services.criar_lote(
        petr4, usuario, trade_date=dt.date(2026, 10, 1), quantity=D("100"), unit_price=D("30")
    )
    cotacao(petr4, "33", "BRL")
    # AAPL sem PTAX para a data e sem câmbio informado → sem base
    aapl = Asset.objects.create(symbol="AAPL", name="Apple", market="NASDAQ", custody="AVENUE")
    services.criar_lote(aapl, usuario, trade_date=dt.date(2025, 1, 10), quantity=D("1"), unit_price=D("200"))
    cotacao(aapl, "250")

    v = services.visao_da_carteira("BRL", AGORA)
    assert v.cambio.valor == D("5.30")
    assert v.sem_base == ["AAPL"]
    por = {linha.asset.symbol: linha for linha in v.linhas}
    assert por["TTWO"].conversao.resultado == D("2180")
    assert por["PETR4"].conversao is None and por["PETR4"].resultado_visao == D("300")
    assert por["AAPL"].sem_base_cambial and por["AAPL"].investido_visao is None
    c = v.consolidado
    assert c.posicoes_total == 2 and c.posicoes_avaliadas == 2  # AAPL fora
    assert c.investido_total == D("11600") + D("3000")
    assert c.resultado == D("2180") + D("300")
    assert v.por_classe["acao"].resultado == c.resultado


def test_visao_em_usd_converte_brl(petr4, usuario):
    ptax(dt.date(2026, 10, 29), "5.50")
    ptax(dt.date(2026, 10, 1), "5.00")
    services.criar_lote(
        petr4, usuario, trade_date=dt.date(2026, 10, 1), quantity=D("100"), unit_price=D("50")
    )
    cotacao(petr4, "55", "BRL")
    v = services.visao_da_carteira("USD", AGORA)
    linha = v.linhas[0]
    assert core.arredondar_moeda(linha.conversao.investido) == D("1000.00")
    assert core.arredondar_moeda(linha.conversao.valor_atual) == D("1000.00")
    assert core.arredondar_moeda(linha.conversao.efeito_cambial) == D("-90.91")


def test_sem_cambio_atual_marca_visao_indisponivel(ttwo, usuario):
    services.criar_lote(
        ttwo, usuario, trade_date=dt.date(2026, 10, 1), quantity=D("1"), unit_price=D("100"), fx_rate=D("5")
    )
    cotacao(ttwo, "110")
    v = services.visao_da_carteira("BRL", AGORA)
    assert v.sem_cambio_atual is True and v.linhas[0].sem_base_cambial is True
    assert v.consolidado.posicoes_total == 0
    # na moeda nativa tudo continua funcionando
    v_usd = services.visao_da_carteira("USD", AGORA)
    assert v_usd.linhas[0].resultado_visao == D("10") and not v_usd.sem_cambio_atual


def test_seletor_de_moeda_nas_telas(cliente_logado, ttwo, usuario):
    ptax(dt.date(2026, 10, 1), "5.00")
    FxDailyRate.objects.create(
        pair="USD-BRL", date=dt.date.today(), bid=D("5.30"), source="t", collected_at=AGORA
    )
    services.criar_lote(ttwo, usuario, trade_date=dt.date(2026, 10, 1), quantity=D("10"), unit_price=D("100"))
    services.criar_lote(
        ttwo,
        usuario,
        trade_date=dt.date(2026, 10, 1),
        quantity=D("10"),
        unit_price=D("120"),
        fx_rate=D("5.50"),
    )
    Quote.objects.create(
        asset=ttwo,
        price=D("130"),
        currency="USD",
        collected_at=dt.datetime.now(dt.UTC),
        source="t",
        quote_type="atrasado",
    )
    r = cliente_logado.post(reverse("trocar_moeda"), {"moeda": "BRL"})
    assert r.status_code == 302
    html = cliente_logado.get(reverse("home_classico")).content.decode()
    assert "R$ 11.600,00" in html and "R$ 13.780,00" in html and "R$ 2.180,00" in html
    assert "USD/BRL 5,3" in html
    detalhe = cliente_logado.get(reverse("asset_detail", args=[ttwo.pk])).content.decode()
    assert "R$ 2.120,00" in detalhe and "R$ 60,00" in detalhe and "Efeito cambial" in detalhe
    assert "PTAX da data da compra" in detalhe and "Informado pelo usuário" in detalhe
    cliente_logado.post(reverse("trocar_moeda"), {"moeda": "USD"})
    html = cliente_logado.get(reverse("home_classico")).content.decode()
    assert "US$ 2.200,00" in html and "US$ 2.600,00" in html
    # moeda inválida é ignorada
    cliente_logado.post(reverse("trocar_moeda"), {"moeda": "EUR"})
    assert "US$ 2.200,00" in cliente_logado.get(reverse("home_classico")).content.decode()
