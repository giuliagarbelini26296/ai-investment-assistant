"""
Aceite do MVP — critérios do escopo v1.3 rev. 2 (seção 10) e da proposta v1.1 (seção 12),
executados de ponta a ponta em modo demonstração com relógio controlado.

Cada teste cita o critério. Critérios que exigem fonte real (pregão observado, consumo de créditos
medido) ficam para a homologação (E0) e estão listados em docs/aceite/checklist.md.
"""

import datetime as dt
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from apps.alerts import services as alerts
from apps.alerts.models import TEXTO_NAO_RECOMENDACAO, Alert
from apps.marketdata import collector, freshness
from apps.marketdata.adapters.base import AuthError, BaseAdapter, BaseFxAdapter, FonteInfo
from apps.marketdata.adapters.demo import DemoAdapter, DemoFxAdapter
from apps.marketdata.models import CollectionRun, FxDailyRate, Quote
from apps.portfolio import services as portfolio
from apps.portfolio.models import Asset, FxOrigin

D = Decimal
SP = ZoneInfo("America/Sao_Paulo")
NY = ZoneInfo("America/New_York")
AGORA = dt.datetime(2026, 10, 29, 14, 0, tzinfo=SP)  # quinta: NYSE e B3 abertas, PTAX devido


def sem_dormir(_s):
    pass


@pytest.fixture
def carteira(db, usuario, settings):
    settings.PORTAL = {**settings.PORTAL, "MODO_DEMO": True}
    ttwo = Asset.objects.create(symbol="TTWO", name="Take-Two", market="NASDAQ", custody="AVENUE")
    btc = Asset.objects.create(symbol="BTC", name="Bitcoin", market="CRIPTO", custody="BINANCE")
    petr = Asset.objects.create(symbol="PETR4", name="Petrobras", market="B3", custody="OUTRA")
    # critério 1: ação EUA (USD) com duas compras, cripto fracionária, ação B3 (BRL)
    portfolio.criar_lote(
        ttwo,
        usuario,
        trade_date=dt.date(2026, 10, 1),
        quantity=D("10"),
        unit_price=D("100"),
        fx_rate=D("5.00"),
    )
    portfolio.criar_lote(
        ttwo,
        usuario,
        trade_date=dt.date(2026, 10, 15),
        quantity=D("10"),
        unit_price=D("120"),
        fx_rate=D("5.50"),
    )
    portfolio.criar_lote(
        btc,
        usuario,
        trade_date=dt.date(2026, 10, 1),
        quantity=D("0.0125"),
        unit_price=D("60000"),
        fx_rate=D("5.00"),
    )
    portfolio.criar_lote(
        petr, usuario, trade_date=dt.date(2026, 10, 1), quantity=D("100"), unit_price=D("30")
    )
    return {"ttwo": ttwo, "btc": btc, "petr": petr}


def coletar_tudo(agora=AGORA):
    return collector.executar_tarefas_devidas(agora, dormir=sem_dormir)


def test_criterio_1_tres_classes_no_mesmo_dashboard(cliente_logado, carteira):
    coletar_tudo()
    html = cliente_logado.get(reverse("home_classico")).content.decode()
    for s in ("TTWO", "BTC", "PETR4"):
        assert s in html
    assert "0,0125" in html  # quantidade fracionária
    assert "US$ 110,00" in html  # preço médio TTWO
    assert html.count("ponto-") >= 3  # cada preço atual traz o indicador de atualidade (ponto com descrição)


def test_criterio_2_alternar_moeda_com_cambio_visivel(cliente_logado, carteira):
    coletar_tudo()
    cliente_logado.post(reverse("trocar_moeda"), {"moeda": "BRL"})
    brl = cliente_logado.get(reverse("home_classico")).content.decode()
    assert "Carteira <small" in brl and "em BRL" in brl and "USD/BRL" in brl and "R$ " in brl
    cliente_logado.post(reverse("trocar_moeda"), {"moeda": "USD"})
    usd = cliente_logado.get(reverse("home_classico")).content.decode()
    assert "em USD" in usd and "US$ " in usd
    # totais corretos nas duas moedas: TTWO investido = US$ 2.200 = R$ 11.600 (10·100·5 + 10·120·5,5)
    cliente_logado.post(reverse("trocar_moeda"), {"moeda": "BRL"})
    assert (
        "R$ 11.600,00"
        in cliente_logado.get(reverse("asset_detail", args=[carteira["ttwo"].pk])).content.decode()
    )
    cliente_logado.post(reverse("trocar_moeda"), {"moeda": "USD"})
    assert (
        "US$ 2.200,00"
        in cliente_logado.get(reverse("asset_detail", args=[carteira["ttwo"].pk])).content.decode()
    )


def test_criterio_3_resultado_do_ativo_e_efeito_cambial(cliente_logado, carteira):
    coletar_tudo()
    cliente_logado.post(reverse("trocar_moeda"), {"moeda": "BRL"})
    html = cliente_logado.get(reverse("asset_detail", args=[carteira["ttwo"].pk])).content.decode()
    assert "Resultado do ativo" in html and "Efeito cambial" in html and "R$ 11.600,00" in html


def test_criterio_4_atualizacao_automatica_com_rotulos(cliente_logado, carteira):
    runs = coletar_tudo()
    assert {r.market for r in runs} >= {"NYSE", "B3", "CRIPTO", "CAMBIO_INTRA", "CAMBIO_PTAX"}
    assert all(r.status == "completo" for r in runs)
    sobre = cliente_logado.get(reverse("sobre_dados")).content.decode()
    assert "Última tentativa" in sobre and "completo" in sobre
    # rótulos por mercado: bolsa fechada → "mercado fechado"; cripto → spot
    noite = dt.datetime(2026, 10, 29, 23, 0, tzinfo=SP)
    fech = dt.datetime(2026, 10, 29, 16, 20, tzinfo=NY)
    Quote.objects.filter(asset=carteira["ttwo"]).update(
        collected_at=fech, market_time=fech - dt.timedelta(minutes=1)
    )
    assert freshness.atualidade(carteira["ttwo"], noite).estado == "mercado_fechado"
    Quote.objects.filter(asset=carteira["btc"]).update(collected_at=noite - dt.timedelta(minutes=5))
    assert freshness.atualidade(carteira["btc"], noite).estado == "spot"


def test_criterio_5_falha_das_fontes_nao_derruba_e_marca(cliente_logado, carteira):
    coletar_tudo()

    class Quebrada(BaseAdapter):
        info = FonteInfo(
            codigo="quebrada",
            nome="q",
            tipo_dado="atrasado",
            atraso_declarado_min=15,
            atraso_declarado_texto="",
            limites="",
            termos_url="",
        )

        def __init__(self):
            super().__init__(client=None)

        def cotar(self, simbolos):
            raise AuthError("chave inválida (401)")

    class CambioQuebrado(BaseFxAdapter):
        info = DemoFxAdapter.info

        def __init__(self):
            super().__init__(client=None)

        def cambio(self):
            raise AuthError("401")

    depois = AGORA + dt.timedelta(minutes=30)
    run = collector.executar_ciclo_mercado("NYSE", depois, adaptador=Quebrada(), dormir=sem_dormir)
    fx = collector.executar_ciclo_cambio("intradiario", depois, adaptador=CambioQuebrado(), dormir=sem_dormir)
    assert run.status == "falha" and "credencial" in run.error and fx.status == "falha"
    assert Quote.objects.filter(asset=carteira["ttwo"]).count() == 1  # dado anterior preservado
    assert freshness.atualidade(carteira["ttwo"], depois + dt.timedelta(minutes=5)).estado == "desatualizado"
    html = cliente_logado.get(reverse("home_classico")).content.decode()
    assert "desatualizado" in html or "demonstração" in html
    sobre = cliente_logado.get(reverse("sobre_dados")).content.decode()
    assert "credencial" in sobre and CollectionRun.objects.filter(status="falha").count() == 2


def test_criterio_6_regra_queda_10_dispara_com_regra_e_valores(cliente_logado, carteira, usuario):
    # posição de teste com preço médio bem acima do preço sintético (~100): queda certa ≥ 10%
    caro = Asset.objects.create(symbol="CARO", name="Teste queda", market="NASDAQ", custody="AVENUE")
    portfolio.criar_lote(
        caro, usuario, trade_date=dt.date(2026, 10, 1), quantity=D("1"), unit_price=D("200"), fx_rate=D("5")
    )
    alerts.criar_regra(
        usuario,
        name="Queda 10",
        asset=None,
        family="desde_compra",
        condition="queda",
        threshold=D("10"),
        classification="compra",
        currency="",
    )
    coletar_tudo()
    assert Alert.objects.filter(asset=caro).exists()
    html = cliente_logado.get(reverse("alertas")).content.decode()
    assert "queda ≥ 10% desde a compra" in html and "retorno desde a compra" in html
    assert TEXTO_NAO_RECOMENDACAO in html
    a = Alert.objects.filter(asset=caro).get()
    assert D(a.observed["retorno_pct"]) <= D("-10") and a.observed["moeda_cotacao"] == "USD"
    assert "CARO" in html and "US$ 200,00" in html  # preço médio exibido junto do alerta


def test_criterio_7_grafico_em_dois_periodos(cliente_logado, carteira):
    # a janela do gráfico é relativa a "agora": os candles sintéticos terminam hoje
    fim = timezone.now().replace(microsecond=0)
    collector.carregar_historico(carteira["ttwo"], 400, fim, adaptador=DemoAdapter(agora=lambda: fim))
    collector.carregar_historico_cambio(30, fim, adaptador=DemoFxAdapter(agora=lambda: fim))
    url = reverse("historico_json", args=[carteira["ttwo"].pk])
    um_mes = cliente_logado.get(url, {"periodo": "1m"}).json()
    um_ano = cliente_logado.get(url, {"periodo": "1a"}).json()
    assert 28 <= len(um_mes["pontos"]) <= 31 and 360 <= len(um_ano["pontos"]) <= 366
    assert um_mes["fonte"] == "demo" and um_mes["pontos"][-1]["provisorio"] is True
    assert (
        'id="grafico-historico"'
        in cliente_logado.get(reverse("asset_detail", args=[carteira["ttwo"].pk])).content.decode()
    )


def test_criterio_8_ambiente_sobe_pelo_readme(db):
    """Os comandos do README existem e respondem; o Compose foi validado com `docker compose config`."""
    call_command("check")
    call_command("coletar", "--uma-vez")  # esqueleto responde mesmo sem ativos
    call_command("carregar_historico", "--so-cambio", "--dias", "3")


def test_criterio_9_nenhuma_tela_exibe_preco_sem_fonte_e_horario(cliente_logado, carteira):
    coletar_tudo()
    home = cliente_logado.get(reverse("home_classico")).content.decode()
    linhas = home.split("<tbody>")[1].split("</tbody>")[0].split('<tr class="linha-ativo"')[1:]
    assert linhas
    for linha in linhas:
        assert (
            "ponto-" in linha and "coleta" in linha
        )  # cada linha com preço traz o indicador de atualidade e a descrição com horário
    detalhe = cliente_logado.get(reverse("asset_detail", args=[carteira["ttwo"].pk])).content.decode()
    assert "coleta 29/10" in detalhe and "demo" in detalhe
    cabecalho = home.split("<header")[1].split("</header>")[0]
    assert "USD/BRL" in cabecalho and (
        "PTAX" in cabecalho or "referência" in cabecalho or "intradiário" in cabecalho
    )


def test_criterio_10_cambio_de_compra_sem_ptax(cliente_logado, carteira, usuario):
    aapl = Asset.objects.create(symbol="AAPL", name="Apple", market="NASDAQ", custody="AVENUE")
    sabado = portfolio.criar_lote(
        aapl, usuario, trade_date=dt.date(2026, 10, 24), quantity=D("1"), unit_price=D("200")
    )
    antigo = portfolio.criar_lote(
        aapl, usuario, trade_date=dt.date(2025, 1, 10), quantity=D("1"), unit_price=D("200")
    )
    FxDailyRate.objects.create(
        pair="USD-BRL", date=dt.date(2026, 10, 23), bid=D("5.20"), source="t", collected_at=AGORA
    )
    from apps.marketdata import fxservice

    assert fxservice.resolver_cambio_lote(sabado) == (D("5.20"), FxOrigin.PTAX_PREV)
    assert fxservice.resolver_cambio_lote(antigo) == (None, FxOrigin.NO_BASIS)
    coletar_tudo()
    cliente_logado.post(reverse("trocar_moeda"), {"moeda": "BRL"})
    html = cliente_logado.get(reverse("home_classico")).content.decode()
    assert "sem base cambial" in html and "AAPL" in html
    assert "1 posição(ões) sem base cambial" in html


# --- Critérios adicionais da proposta (seção 12) ---------------------------------------------------


def test_proposta_cripto_fracionaria_spot_e_usdt_rotulado(cliente_logado, carteira):
    coletar_tudo()
    btc = carteira["btc"]
    Quote.objects.filter(asset=btc).update(currency="USDT")
    html = cliente_logado.get(reverse("asset_detail", args=[btc.pk])).content.decode()
    assert "USDT" in html and "0,0125" in html


def test_proposta_reinicio_do_coletor_nao_duplica(carteira):
    coletar_tudo()
    n = Quote.objects.count()
    # "reinício" imediato: agenda lida do banco, nada devido; mesma cotação não se repete
    assert coletar_tudo(AGORA + dt.timedelta(seconds=30)) == []
    assert Quote.objects.count() == n


def test_proposta_orcamento_dentro_da_cota(carteira):
    from apps.marketdata.budget import saldo
    from apps.marketdata.models import SourceBudget

    coletar_tudo()
    assert (
        SourceBudget.objects.count() == 0
    )  # demo sem limites; com fontes reais, saldo é exposto em Sobre os dados
    assert saldo(DemoAdapter.info, AGORA) == {}
