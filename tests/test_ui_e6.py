"""E6: filtros, comparação, histórico JSON e 'Sobre os dados'."""

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest
from django.urls import reverse

from apps.marketdata import history
from apps.marketdata.models import CollectionRun, DailyBar, FxDailyRate, Quote, ScheduleState
from apps.portfolio import services
from apps.portfolio.models import Asset

D = Decimal
AGORA = dt.datetime(2026, 10, 29, 18, 0, tzinfo=dt.UTC)


@pytest.fixture
def carteira(db, usuario):
    ttwo = Asset.objects.create(
        symbol="TTWO", name="Take-Two", market="NASDAQ", custody="AVENUE", sector="Jogos"
    )
    btc = Asset.objects.create(symbol="BTC", name="Bitcoin", market="CRIPTO", custody="BINANCE")
    petr = Asset.objects.create(
        symbol="PETR4", name="Petrobras", market="B3", custody="OUTRA", sector="Petróleo"
    )
    vazio = Asset.objects.create(symbol="AAPL", name="Apple", market="NASDAQ", custody="AVENUE")
    services.criar_lote(
        ttwo, usuario, trade_date=dt.date(2026, 10, 1), quantity=D("10"), unit_price=D("100"), fx_rate=D("5")
    )
    services.criar_lote(
        btc,
        usuario,
        trade_date=dt.date(2026, 10, 1),
        quantity=D("0.1"),
        unit_price=D("60000"),
        fx_rate=D("5"),
    )
    services.criar_lote(petr, usuario, trade_date=dt.date(2026, 10, 1), quantity=D("100"), unit_price=D("30"))
    for a, p in ((ttwo, "110"), (btc, "50000"), (petr, "33")):
        Quote.objects.create(
            asset=a, price=D(p), currency=a.native_currency, collected_at=AGORA, source="t", quote_type="spot"
        )
    FxDailyRate.objects.create(
        pair="USD-BRL", date=dt.date(2026, 10, 29), bid=D("5.30"), source="t", collected_at=AGORA
    )
    return {"ttwo": ttwo, "btc": btc, "petr": petr, "vazio": vazio}


def test_filtros_da_lista(cliente_logado, carteira):
    url = reverse("asset_list")
    assert "4 de 4" in cliente_logado.get(url).content.decode()
    html = cliente_logado.get(url, {"situacao": "prejuizo"}).content.decode()
    assert "BTC" in html and "TTWO" not in html.split("<tbody>")[1]
    html = cliente_logado.get(url, {"classe": "acao", "mercado": "NASDAQ"}).content.decode()
    corpo = html.split("<tbody>")[1]
    assert "TTWO" in corpo and "AAPL" in corpo and "PETR4" not in corpo
    assert "AAPL" in cliente_logado.get(url, {"situacao": "sem_posicao"}).content.decode().split("<tbody>")[1]
    assert "Petróleo" in cliente_logado.get(url, {"setor": "petr"}).content.decode().split("<tbody>")[1]
    assert "Nenhum ativo" in cliente_logado.get(url, {"q": "zzz"}).content.decode()
    assert 'data-sort="num"' in html  # cabeçalhos ordenáveis


def test_comparacao(cliente_logado, carteira):
    ids = [carteira["ttwo"].pk, carteira["petr"].pk]
    r = cliente_logado.get(reverse("comparar"), {"ativos": ids, "periodo": "1m"})
    html = r.content.decode()
    assert r.status_code == 200
    assert "Efeito cambial" in html and "grafico-comparacao" in html
    assert html.count("badge-") >= 2
    assert (
        "Escolha pelo menos dois"
        in cliente_logado.get(reverse("comparar"), {"ativos": [ids[0]]}).content.decode()
    )
    assert cliente_logado.get(reverse("comparar"), {"ativos": ids, "periodo": "xx"}).status_code == 200


def barras(asset, inicio, n, base, fonte="t"):
    for i in range(n):
        data = inicio + dt.timedelta(days=i)
        DailyBar.objects.create(
            asset=asset,
            session_date=data,
            time_basis="sessao_local",
            close=D(base) + i,
            source=fonte,
            provisional=(i == n - 1),
            collected_at=AGORA,
        )


def test_serie_historica_periodos_e_conversao(carteira):
    ttwo = carteira["ttwo"]
    barras(ttwo, dt.date(2026, 10, 10), 20, "100")
    for i in range(0, 20):  # PTAX só em dias úteis
        data = dt.date(2026, 10, 10) + dt.timedelta(days=i)
        if data.weekday() < 5:
            FxDailyRate.objects.get_or_create(
                pair="USD-BRL", date=data, defaults={"bid": D("5"), "source": "t", "collected_at": AGORA}
            )
    s = history.serie_historica(ttwo, "1s", "BRL", AGORA)
    assert s.fonte == "t" and len(s.pontos) == 8  # hoje − 7 dias, inclusive (22/10 a 29/10)
    assert s.pontos[-1].provisorio and any("provisório" in a for a in s.avisos)
    assert s.convertidos == 8  # fim de semana usa PTAX de sexta
    assert s.pontos[0].convertido == s.pontos[0].close * 5
    s_compra = history.serie_historica(ttwo, "compra", None, AGORA)
    assert s_compra.inicio_pedido == dt.date(2026, 10, 1) and s_compra.inicio_real == dt.date(2026, 10, 10)
    assert any("cobertura da fonte" in a for a in s_compra.avisos)
    vazio = history.serie_historica(carteira["vazio"], "1m", "BRL", AGORA)
    assert not vazio.pontos and "sem histórico" in vazio.avisos[0]
    with pytest.raises(ValueError):
        history.serie_historica(ttwo, "2a", None, AGORA)


def test_serie_nao_interpola_ptax_ausente(carteira):
    ttwo = carteira["ttwo"]
    barras(ttwo, dt.date(2026, 10, 26), 3, "100")  # seg, ter, qua — sem PTAX anterior
    FxDailyRate.objects.create(
        pair="USD-BRL", date=dt.date(2026, 10, 27), bid=D("5"), source="t", collected_at=AGORA
    )
    s = history.serie_historica(ttwo, "1s", "BRL", AGORA)
    assert [p.convertido is None for p in s.pontos] == [True, False, False]
    assert any("sem PTAX" in a for a in s.avisos)


def test_serie_usa_uma_unica_fonte(carteira):
    ttwo = carteira["ttwo"]
    barras(ttwo, dt.date(2026, 10, 20), 3, "100", fonte="antiga")
    barras(ttwo, dt.date(2026, 10, 24), 3, "200", fonte="nova")
    s = history.serie_historica(ttwo, "1m", None, AGORA)
    assert s.fonte == "nova" and all(p.close >= 200 for p in s.pontos)


def test_historico_json(cliente_logado, carteira):
    ttwo = carteira["ttwo"]
    barras(ttwo, dt.date(2026, 10, 20), 5, "100")
    r = cliente_logado.get(reverse("historico_json", args=[ttwo.pk]), {"periodo": "1m"})
    d = r.json()
    assert r.status_code == 200 and d["simbolo"] == "TTWO" and len(d["pontos"]) == 5
    assert d["pontos"][0]["nativo"] == "100.00000000" and d["moeda_visao"] == "BRL"
    assert cliente_logado.get(reverse("historico_json", args=[ttwo.pk]), {"periodo": "x"}).status_code == 400


def test_detalhe_mostra_grafico_so_com_historico(cliente_logado, carteira):
    ttwo = carteira["ttwo"]
    html = cliente_logado.get(reverse("asset_detail", args=[ttwo.pk])).content.decode()
    assert "Sem histórico carregado" in html
    barras(ttwo, dt.date(2026, 10, 20), 5, "100")
    html = cliente_logado.get(reverse("asset_detail", args=[ttwo.pk])).content.decode()
    assert 'id="grafico-historico"' in html and "desde a compra" in html and "chart.umd.js" in html


def test_sobre_os_dados(cliente_logado, carteira, settings):
    settings.PORTAL = {**settings.PORTAL, "MODO_DEMO": False}
    ScheduleState.objects.create(task="NYSE", last_status="completo")
    CollectionRun.objects.create(
        started_at=AGORA,
        source="brapi",
        market="B3",
        status="parcial",
        attempted=2,
        succeeded=1,
        failed=1,
        details={"VALE3": "ausente"},
    )
    html = cliente_logado.get(reverse("sobre_dados")).content.decode()
    for fonte in ("brapi", "twelvedata", "binance", "ptax_bcb", "awesomeapi"):
        assert fonte in html
    assert "demo" not in html.split("<h2>Fontes</h2>")[1].split("<h2>Calendários</h2>")[0]
    assert "não homologada" in html and "não constituem recomendação" in html
    assert "NYSE / Nasdaq" in html and "VALE3: ausente" in html
    assert "atraso real" in html
    settings.PORTAL = {**settings.PORTAL, "MODO_DEMO": True}
    html = cliente_logado.get(reverse("sobre_dados")).content.decode()
    assert "Modo demonstração ativo" in html and "DEMONSTRAÇÃO (dados sintéticos)" in html


def test_chartjs_e_local():
    arquivo = Path(__file__).resolve().parents[1] / "apps" / "ui" / "static" / "ui" / "chart.umd.js"
    assert arquivo.exists() and "Chart.js v4.4.7" in arquivo.read_text(encoding="utf-8")[:200]
    base = (
        Path(__file__).resolve().parents[1] / "apps" / "ui" / "templates" / "ui" / "base.html"
    ).read_text()
    assert "cdn" not in base.lower()
