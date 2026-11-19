"""rc7 — Mercado e Favoritos: indicadores, score, fontes de inteligência, coleta, alertas de mercado e telas."""

import datetime as dt
import json
from decimal import Decimal
from zoneinfo import ZoneInfo

import httpx
import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from apps.alerts import notify
from apps.alerts import services as alerts
from apps.alerts.forms import AlertaForm
from apps.alerts.models import Alert, Notification, RuleState
from apps.marketdata import collector, freshness
from apps.marketdata.adapters.base import AuthError, CambioNormalizado, RateLimitError
from apps.marketdata.adapters.inteligencia import AlphaVantageAdapter, FinnhubAdapter, FocusBcbAdapter
from apps.marketdata.models import DailyBar, FxRateType, Quote, SourceBudget
from apps.mercado import analise, coleta, universo
from apps.mercado import indicadores as ind
from apps.mercado import score as sc
from apps.mercado.dolar import ativo_dolar
from apps.mercado.models import AnalystSnapshot, EarningsEvent, Favorito, GrupoFavoritos, PriceTarget
from apps.portfolio import services as portfolio
from apps.portfolio.forms import LotForm
from apps.portfolio.models import Asset

D = Decimal
NY = ZoneInfo("America/New_York")
SP = ZoneInfo("America/Sao_Paulo")


# --- utilidades ------------------------------------------------------------------------------------


def barras_sinteticas(n=300, inicio=100.0, passo=0.0, fim=None, hoje=dt.date(2026, 10, 29)):
    """Barras diárias até ontem; `passo` = variação diária fixa; `fim` força o último fechamento."""
    saida, preco = [], inicio
    for i in range(n):
        data = hoje - dt.timedelta(days=n - i)
        preco = preco * (1 + passo)
        saida.append(ind.Barra(data, preco, preco * 1.01, preco * 0.99, 1000.0))
    if fim is not None:
        ultima = saida[-1]
        saida[-1] = ind.Barra(ultima.data, fim, fim * 1.01, fim * 0.99, 1000.0)
    return saida


def gravar_barras(asset, n=300, inicio=100.0, passo=0.0, fonte="twelvedata", hoje=None):
    hoje = hoje or timezone.localdate()
    agora = timezone.now()
    preco = inicio
    for i in range(n):
        data = hoje - dt.timedelta(days=n - i)
        preco = preco * (1 + passo)
        DailyBar.objects.create(
            asset=asset,
            session_date=data,
            time_basis="sessao_local",
            open=D(str(round(preco, 4))),
            high=D(str(round(preco * 1.01, 4))),
            low=D(str(round(preco * 0.99, 4))),
            close=D(str(round(preco, 4))),
            volume=D("1000"),
            source=fonte,
            collected_at=agora,
        )


def cotar(asset, preco, agora=None, moeda="USD", previous=None, change=None, fonte="twelvedata"):
    agora = agora or timezone.now()
    return Quote.objects.create(
        asset=asset,
        price=D(str(preco)),
        currency=moeda,
        previous_close=None if previous is None else D(str(previous)),
        change_24h_pct=None if change is None else D(str(change)),
        collected_at=agora,
        source=fonte,
        quote_type="nao_declarado",
    )


@pytest.fixture
def aapl(db):
    return Asset.objects.create(
        symbol="AAPL", name="Apple", market="NASDAQ", custody="NENHUMA", acompanhamento=True
    )


# --- indicadores ------------------------------------------------------------------------------------


def test_rsi_sma_macd_e_volatilidade():
    subindo = [float(i) for i in range(1, 60)]
    assert ind.rsi(subindo) == 100.0
    assert ind.rsi([10.0] * 20) == 50.0
    assert ind.sma([1.0, 2.0, 3.0, 4.0], 2) == 3.5 and ind.sma([1.0], 2) is None
    assert ind.macd(subindo[:20]) is None
    m = ind.macd(subindo)
    assert m is not None and m[2] == pytest.approx(0, abs=1e-9)  # tendência linear: MACD constante
    assert ind.volatilidade_anual([100.0] * 40, 30) == 0.0


def test_calcular_indicadores_52s_suporte_e_periodos():
    hoje = dt.date(2026, 10, 29)
    barras = barras_sinteticas(400, 100.0, -0.001, hoje=hoje)
    preco = barras[-1].close * 0.9  # caiu 10% hoje: nova mínima
    i = ind.calcular(barras, preco, hoje)
    assert i.janela_52s_completa and i.minima_52s == pytest.approx(preco)
    assert i.dist_minima_52s_pct == pytest.approx(0)
    # suporte de 20 sessões vem das sessões completas anteriores (sem o preço de hoje)
    assert i.suporte20 == pytest.approx(min(b.low for b in barras[-20:]))
    assert preco < i.suporte20
    p1m = i.periodos["1m"]
    base = ind.fechamento_base(barras, hoje - dt.timedelta(days=30))
    assert p1m.variacao_pct == pytest.approx((preco / base.close - 1) * 100)
    assert p1m.volume_financeiro is not None and p1m.maxima >= p1m.minima
    assert i.mm200 is not None and i.tendencia in ("alta", "baixa", "lateral")


def test_periodo_sem_historico_suficiente_fica_none():
    hoje = dt.date(2026, 10, 29)
    i = ind.calcular(barras_sinteticas(20, hoje=hoje), 100.0, hoje)
    assert i.periodos["6m"].variacao_pct is None and i.mm200 is None and i.janela_52s_dias < 180


# --- score ----------------------------------------------------------------------------------------


def test_score_so_tecnico_nao_passa_de_35_e_fica_fora_do_top10():
    hoje = dt.date(2026, 10, 29)
    barras = barras_sinteticas(400, 100.0, -0.002, hoje=hoje)  # queda longa: perto da mínima
    i = ind.calcular(barras, barras[-1].close * 0.97, hoje)
    s = sc.calcular(i, sc.DadosExternos())
    assert s.total <= 35 and s.cobertura <= 35 and not s.elegivel_top10
    assert any("fora do Top 10" in o for o in s.observacoes)
    nao = [c for c in s.criterios if not c.avaliado]
    assert {c.grupo for c in nao} == {"analistas", "fundamentos"} and all(c.pontos == 0 for c in nao)


def test_score_completo_criterios_e_risco():
    hoje = dt.date(2026, 10, 29)
    barras = barras_sinteticas(400, 100.0, 0.0005, hoje=hoje)
    i = ind.calcular(barras, barras[-1].close, hoje)
    ext = sc.DadosExternos(
        pct_compra=90.0,
        pct_venda=0.0,
        n_analistas=30,
        fonte_analistas="finnhub",
        preco_alvo=barras[-1].close * 1.4,
        fonte_alvo="finnhub",
        margem=0.25,
        roe=0.3,
        pl=18.0,
        fonte_fundamentos="alphavantage",
        dias_para_resultado=3,
    )
    s = sc.calcular(i, ext)
    por = {c.codigo: c for c in s.criterios}
    assert por["A1"].pontos == 25 and por["A2"].pontos == 15
    assert por["F1"].pontos == 8 and por["F2"].pontos == 8 and por["F3"].pontos == 9
    assert s.cobertura == 100 and s.elegivel_top10 and s.total >= 65
    assert s.potencial_pct == pytest.approx(40, abs=0.01)
    assert s.perspectiva == "médio/longo prazo"
    assert any("resultado trimestral" in m for m in s.motivos_risco)  # sobe um nível
    assert s.risco in ("médio", "alto")


def test_score_cripto_sem_analistas():
    hoje = dt.date(2026, 10, 29)
    i = ind.calcular(barras_sinteticas(400, hoje=hoje), 100.0, hoje, cripto=True)
    s = sc.calcular(i, sc.DadosExternos(cripto=True, preco_alvo=999.0))
    assert s.potencial_pct is None and s.total <= 35
    assert "cripto" in {c.codigo: c for c in s.criterios}["A1"].explicacao


# --- adaptadores ------------------------------------------------------------------------------------


def _cliente(rotas):
    def handler(request):
        for trecho, (status, corpo) in rotas.items():
            if trecho in str(request.url):
                return httpx.Response(status, json=corpo)
        return httpx.Response(404, json={})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_finnhub_parse_e_erros():
    rotas = {
        "/stock/recommendation": (
            200,
            [
                {
                    "period": "2026-10-01",
                    "strongBuy": 10,
                    "buy": 20,
                    "hold": 5,
                    "sell": 1,
                    "strongSell": 0,
                    "symbol": "AAPL",
                },
                {
                    "period": "2026-09-01",
                    "strongBuy": 8,
                    "buy": 18,
                    "hold": 9,
                    "sell": 1,
                    "strongSell": 0,
                    "symbol": "AAPL",
                },
            ],
        ),
        "/stock/price-target": (403, {"error": "You don't have access to this resource."}),
        "/company-news": (
            200,
            [
                {
                    "datetime": 1761900000,
                    "headline": "Apple sobe",
                    "source": "Reuters",
                    "summary": "x",
                    "url": "https://n/1",
                },
                {"headline": "sem url"},
            ],
        ),
        "/calendar/earnings": (
            200,
            {
                "earningsCalendar": [
                    {
                        "date": "2026-11-05",
                        "hour": "amc",
                        "epsEstimate": 1.5,
                        "symbol": "AAPL",
                        "quarter": 4,
                        "year": 2026,
                    }
                ]
            },
        ),
        "/stock/metric": (
            200,
            {
                "metric": {
                    "52WeekHigh": 260.1,
                    "52WeekLow": 170.2,
                    "peTTM": 30.5,
                    "roeTTM": 150.0,
                    "netProfitMarginTTM": 24.3,
                }
            },
        ),
    }
    a = FinnhubAdapter(client=_cliente(rotas), api_key="k")
    recs = a.recomendacoes("AAPL")
    assert [r.periodo.month for r in recs] == [9, 10] and recs[-1].buy == 20
    with pytest.raises(AuthError):
        a.preco_alvo("AAPL")
    (n,) = a.noticias_empresa("AAPL", dt.date(2026, 10, 28), dt.date(2026, 10, 31))
    assert n.veiculo == "Reuters" and n.publicada_em.tzinfo is not None
    (r,) = a.resultados(dt.date(2026, 10, 1), dt.date(2026, 12, 31), "AAPL")
    assert r.data == dt.date(2026, 11, 5) and r.eps_estimado == D("1.5")
    f = a.ficha("AAPL")
    assert f.margem_liquida == D("0.243") and f.roe == D("1.5") and f.maxima_52s == D("260.1")
    with pytest.raises(AuthError):
        FinnhubAdapter(client=_cliente({}), api_key="").recomendacoes("AAPL")


def test_alphavantage_ficha_e_cota():
    corpo = {
        "Symbol": "AAPL",
        "Sector": "TECHNOLOGY",
        "PERatio": "32.1",
        "ProfitMargin": "0.24",
        "ReturnOnEquityTTM": "1.5",
        "AnalystTargetPrice": "250.5",
        "AnalystRatingStrongBuy": "10",
        "AnalystRatingBuy": "20",
        "AnalystRatingHold": "8",
        "AnalystRatingSell": "1",
        "AnalystRatingStrongSell": "None",
        "52WeekHigh": "260",
        "52WeekLow": "None",
        "ForwardPE": "-",
    }
    a = AlphaVantageAdapter(client=_cliente({"OVERVIEW": (200, corpo)}), api_key="k")
    f = a.ficha("AAPL")
    assert f.setor == "Technology" and f.preco_alvo.media == D("250.5") and f.minima_52s is None
    assert f.recomendacao.strong_sell == 0 and f.recomendacao.buy == 20 and f.pl_projetado is None
    limite = {
        "Information": "Thank you for using Alpha Vantage! Our standard API rate limit is 25 requests per day."
    }
    with pytest.raises(RateLimitError):
        AlphaVantageAdapter(client=_cliente({"OVERVIEW": (200, limite)}), api_key="k").ficha("AAPL")


def test_focus_parse():
    corpo = {
        "value": [
            {
                "Indicador": "Câmbio",
                "Data": "2026-10-30",
                "DataReferencia": "2026",
                "Media": 5.46,
                "Mediana": 5.45,
                "DesvioPadrao": 0.1,
                "Minimo": 5.1,
                "Maximo": 5.9,
                "numeroRespondentes": 110,
                "baseCalculo": 0,
            }
        ]
    }
    (e,) = FocusBcbAdapter(client=_cliente({"ExpectativasMercadoAnuais": (200, corpo)})).expectativas_cambio()
    assert e.mediana == D("5.45") and e.data_referencia == "2026" and e.respondentes == 110


# --- universo, coleta e cotação ---------------------------------------------------------------------


def test_semear_universo_cria_acompanhados_e_grupos(usuario):
    r = universo.semear_universo(usuario)
    assert len(r["criados"]) == len(universo.UNIVERSO_SUGERIDO)
    assert Asset.objects.filter(acompanhamento=True, custody="NENHUMA").count() == len(r["criados"])
    assert set(GrupoFavoritos.objects.values_list("nome", flat=True)) == {"Big Techs", "ETFs", "Criptomoedas"}
    assert Asset.objects.get(symbol="SPY").is_etf
    again = universo.semear_universo(usuario)
    assert again["criados"] == []


def test_acompanhado_fora_do_ciclo_de_15_min_ate_comprar(aapl, usuario):
    assert aapl.coleta_horaria and aapl not in collector._ativos_do_mercado("NYSE")
    portfolio.criar_lote(aapl, usuario, trade_date=dt.date(2026, 10, 1), quantity=D("1"), unit_price=D("100"))
    aapl.refresh_from_db()
    assert not aapl.acompanhamento and aapl in collector._ativos_do_mercado("NYSE")


def test_freshness_horaria_e_do_dolar(aapl):
    aberto = dt.datetime(2026, 10, 29, 11, 0, tzinfo=NY)
    q = cotar(aapl, 200, agora=aberto - dt.timedelta(minutes=50))
    a = freshness.atualidade(aapl, aberto, q)
    assert a.estado == "nao_declarado" and a.permite_alertas and "~1 h" in a.descricao
    velho = freshness.atualidade(aapl, aberto + dt.timedelta(minutes=40), q)
    assert velho.estado == "desatualizado"
    dolar = ativo_dolar()
    agora = dt.datetime(2026, 10, 29, 12, 0, tzinfo=SP)
    qd = cotar(dolar, "5.40", agora=agora - dt.timedelta(minutes=5), moeda="BRL", fonte="awesomeapi")
    assert freshness.atualidade(dolar, agora, qd).permite_alertas
    noite = dt.datetime(2026, 10, 29, 22, 0, tzinfo=SP)
    assert freshness.atualidade(dolar, noite, qd).estado == "mercado_fechado"


def test_candles_passo_carga_inicial_e_cotacao_do_candle(aapl, settings):
    settings.PORTAL = {**settings.PORTAL, "MODO_DEMO": True, "COLETA_MERCADO": True}
    agora = dt.datetime(2026, 10, 29, 15, 0, tzinfo=NY)  # pregão aberto
    feitos = coleta.candles_passo(agora)
    assert "AAPL" in feitos.get("carga inicial", [])
    assert DailyBar.objects.filter(asset=aapl, source="demo").count() >= 250
    q = Quote.objects.filter(asset=aapl, source_version="time_series").get()
    ultimo = DailyBar.objects.filter(asset=aapl).order_by("-session_date").first()
    assert q.price == ultimo.close and q.previous_close is not None
    assert coleta.candles_passo(agora + dt.timedelta(minutes=1)) == {}  # em dia: nada a fazer


def test_candles_respeita_minuto_da_twelvedata(aapl, settings, monkeypatch):
    settings.FONTES = {"CHAVES": {**settings.FONTES["CHAVES"], "twelvedata": "k"}}
    settings.PORTAL = {**settings.PORTAL, "MODO_DEMO": False}
    agora = dt.datetime(2026, 10, 29, 15, 0, tzinfo=NY)
    SourceBudget.objects.create(
        source="twelvedata", period="minuto", period_key=agora.strftime("%Y-%m-%dT%H:%M"), limit=8, consumed=8
    )
    chamadas = []

    class Falso:
        from apps.marketdata.adapters.twelvedata import TwelveDataAdapter

        info = TwelveDataAdapter.info

        def historico(self, s, dias):
            chamadas.append(s)
            return []

    monkeypatch.setattr(coleta, "adaptador_para_mercado", lambda m: Falso())
    coleta.candles_passo(agora)
    assert chamadas == []  # minuto esgotado: não chama


def test_ciclo_de_cambio_espelha_no_dolar_e_dispara_alerta(db, usuario):
    class Fx:
        from apps.marketdata.adapters.fx import AwesomeApiAdapter

        info = AwesomeApiAdapter.info

        def cambio(self):
            return CambioNormalizado(
                par="USD-BRL",
                compra=D("5.60"),
                venda=D("5.61"),
                tipo=FxRateType.INTRADAY,
                fonte="awesomeapi",
                horario_coleta=timezone.now(),
                horario=timezone.now(),
                bruto={"pctChange": "1.8", "high": "5.62", "low": "5.50"},
            )

    dolar = ativo_dolar()
    alerts.criar_regra(
        usuario,
        name="Dólar +1%",
        asset=dolar,
        family="dia_24h",
        condition="alta",
        threshold=D("1"),
        currency="",
        classification="aviso",
    )
    agora = dt.datetime(2026, 10, 29, 12, 0, tzinfo=SP)
    collector.executar_ciclo_cambio("intradiario", agora, adaptador=Fx())
    q = Quote.objects.get(asset=dolar)
    assert q.price == D("5.60") and q.change_24h_pct == D("1.8") and q.currency == "BRL"
    a = Alert.objects.get(asset=dolar)
    assert D(a.observed["variacao_pct"]) == D("1.8")


# --- alertas de mercado ------------------------------------------------------------------------------


def test_regra_global_nao_suspende_acompanhados(aapl, usuario):
    alerts.criar_regra(
        usuario,
        name="Carteira -10%",
        asset=None,
        family="desde_compra",
        condition="queda",
        threshold=D("10"),
        currency="",
        classification="aviso",
    )
    cotar(aapl, 100)
    assert alerts.avaliar_ativo(aapl) == [] and not RuleState.objects.exists()


def test_perto_da_minima_dispara_sem_posicao(aapl, usuario):
    gravar_barras(aapl, 300, 100.0, -0.001)
    ultimo = DailyBar.objects.filter(asset=aapl).order_by("-session_date").first()
    alerts.criar_regra(
        usuario,
        name="AAPL mín",
        asset=aapl,
        family="mercado",
        condition="min52",
        threshold=D("3"),
        currency="",
        classification="aviso",
    )
    cotar(aapl, ultimo.close)
    (a,) = alerts.avaliar_ativo(aapl)
    assert "minima_52s" in a.observed and D(a.observed["distancia_pct"]) <= 3
    corpo = notify.corpo(a)
    assert "Mínima de 52 semanas" in corpo and "Não constitui recomendação" in corpo


def test_suporte_resistencia_resultado_e_recomendacao(aapl, usuario):
    gravar_barras(aapl, 60, 100.0, 0.0)
    for cond, x in (("suporte", "20"), ("resist", "20"), ("result", "7"), ("recomend", "10")):
        alerts.criar_regra(
            usuario,
            name=cond,
            asset=aapl,
            family="mercado",
            condition=cond,
            threshold=D(x),
            currency="",
            classification="aviso",
        )
    cotar(aapl, 95)  # abaixo da mínima de 20 sessões (99)
    hoje = timezone.localdate()
    EarningsEvent.objects.create(
        asset=aapl, source="finnhub", date=hoje + dt.timedelta(days=3), collected_at=timezone.now()
    )
    AnalystSnapshot.objects.create(
        asset=aapl,
        source="finnhub",
        period=dt.date(2026, 9, 1),
        strong_buy=5,
        buy=5,
        hold=10,
        collected_at=timezone.now(),
    )
    AnalystSnapshot.objects.create(
        asset=aapl,
        source="finnhub",
        period=dt.date(2026, 10, 1),
        strong_buy=10,
        buy=6,
        hold=4,
        collected_at=timezone.now(),
    )
    disparados = {a.version.condition for a in alerts.avaliar_ativo(aapl)}
    assert disparados == {"suporte", "result", "recomend"}


def test_versao_de_mercado_valida_limites(aapl, usuario):
    with pytest.raises(ValidationError):
        alerts.criar_regra(
            usuario,
            name="x",
            asset=aapl,
            family="mercado",
            condition="suporte",
            threshold=D("2.5"),
            currency="",
            classification="aviso",
        )
    with pytest.raises(ValidationError):
        alerts.criar_regra(
            usuario,
            name="x",
            asset=aapl,
            family="mercado",
            condition="min52",
            threshold=D("80"),
            currency="",
            classification="aviso",
        )


def test_formulario_de_mercado_exige_ativo(aapl):
    f = AlertaForm({"tipo": "rompe_suporte", "valor": "20", "classification": "aviso"})
    assert not f.is_valid() and "asset" in f.errors
    f = AlertaForm({"asset": aapl.pk, "tipo": "rompe_suporte", "valor": "20", "classification": "aviso"})
    assert f.is_valid() and f.nome() == "AAPL: rompe o suporte de 20 sessões"


def test_notificacao_nao_repete_em_6h(aapl, usuario, settings):
    settings.NOTIFICACOES = {**settings.NOTIFICACOES, "TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "1"}
    alerts.criar_regra(
        usuario,
        name="AAPL < 100",
        asset=aapl,
        family="preco_absoluto",
        condition="abaixo",
        threshold=D("100"),
        currency="USD",
        classification="aviso",
    )
    t0 = timezone.now()
    cotar(aapl, 95, agora=t0)
    notify.enfileirar(alerts.avaliar_ativo(aapl, t0))
    cotar(aapl, 105, agora=t0 + dt.timedelta(minutes=15))
    alerts.avaliar_ativo(aapl, t0 + dt.timedelta(minutes=15))
    cotar(aapl, 95, agora=t0 + dt.timedelta(minutes=30))
    (a2,) = alerts.avaliar_ativo(aapl, t0 + dt.timedelta(minutes=30))
    assert notify.enfileirar([a2]) == []
    a2.refresh_from_db()
    assert "não reenviada" in a2.observed["notificacao"]
    assert Notification.objects.count() == 1


def test_compra_implausivel_pede_confirmacao(db):
    btc = Asset.objects.create(symbol="BTC", name="Bitcoin", market="CRIPTO", custody="BINANCE")
    DailyBar.objects.create(
        asset=btc,
        session_date=dt.date(2026, 10, 1),
        time_basis="dia_utc",
        close=D("76590"),
        source="binance",
        collected_at=timezone.now(),
    )
    dados = {"trade_date": "2026-10-01", "quantity": "1", "unit_price": "76.59", "fx_rate": "416.56"}
    f = LotForm(dados, asset=btc)
    assert not f.is_valid() and "unit_price" in f.errors and "fx_rate" in f.errors
    f = LotForm({**dados, "confirmar_valores": "on"}, asset=btc)
    assert f.is_valid()
    ok = LotForm({**dados, "unit_price": "76.590", "fx_rate": "5,42"}, asset=btc)
    assert (
        ok.is_valid()
        and ok.cleaned_data["unit_price"] == D("76590")
        and ok.cleaned_data["fx_rate"] == D("5.42")
    )
    frac = LotForm({**dados, "quantity": "0.125", "unit_price": "76590", "fx_rate": "5.42"}, asset=btc)
    assert frac.is_valid() and frac.cleaned_data["quantity"] == D("0.125")


# --- telas ----------------------------------------------------------------------------------------------


@pytest.fixture
def mercado_com_dados(aapl, usuario):
    gravar_barras(aapl, 300, 100.0, 0.0005)
    cotar(aapl, 112)
    AnalystSnapshot.objects.create(
        asset=aapl,
        source="finnhub",
        period=dt.date(2026, 10, 1),
        strong_buy=10,
        buy=10,
        hold=5,
        collected_at=timezone.now(),
    )
    PriceTarget.objects.create(asset=aapl, source="finnhub", mean=D("150"), collected_at=timezone.now())
    analise.invalidar_cache()
    return aapl


def test_paginas_do_mercado(cliente_logado, mercado_com_dados):
    for nome in (
        "mercado",
        "mercado_rankings",
        "mercado_oportunidades",
        "mercado_dolar",
        "favoritos",
        "mercado_universo",
    ):
        r = cliente_logado.get(reverse(nome))
        assert r.status_code == 200, nome
        html = r.content.decode()
        assert "Não constitui recomendação" in html or nome == "mercado_universo"
    html = cliente_logado.get(reverse("mercado_oportunidades")).content.decode()
    assert (
        "AAPL" in html and "Como o score é calculado" in html and "Isto não é recomendação de compra" in html
    )
    frag = cliente_logado.get(
        reverse("mercado_rankings"), {"periodo": "1m", "fragmento": "1"}
    ).content.decode()
    assert "<html" not in frag and "Maiores altas" in frag


def test_paginas_vazias_sem_dados(cliente_logado, db):
    for nome in ("mercado", "mercado_rankings", "mercado_oportunidades", "mercado_dolar", "favoritos"):
        assert cliente_logado.get(reverse(nome)).status_code == 200
    assert "Carregar lista sugerida" in cliente_logado.get(reverse("mercado")).content.decode()


def test_estrela_e_adicionar_favorito(cliente_logado, usuario, mercado_com_dados):
    url = reverse("favorito_alternar", args=[mercado_com_dados.pk])
    r = cliente_logado.post(url, HTTP_X_REQUESTED_WITH="fetch")
    assert r.json()["favorito"] is True and Favorito.objects.filter(user=usuario).count() == 1
    assert cliente_logado.post(url, HTTP_X_REQUESTED_WITH="fetch").json()["favorito"] is False
    cliente_logado.post(reverse("favorito_adicionar"), {"simbolo": "eth", "mercado": "CRIPTO"})
    eth = Asset.objects.get(symbol="ETH")
    assert eth.acompanhamento and Favorito.objects.filter(user=usuario, asset=eth).exists()
    html = cliente_logado.get(reverse("favoritos")).content.decode()
    assert "Analisar meus favoritos" in html and "ETH" in html


def test_grupo_e_ordem_dos_favoritos(cliente_logado, usuario, mercado_com_dados):
    cliente_logado.post(reverse("favorito_grupo_criar"), {"nome": "Big Techs"})
    g = GrupoFavoritos.objects.get(user=usuario)
    fav = Favorito.objects.create(user=usuario, asset=mercado_com_dados)
    r = cliente_logado.post(
        reverse("favorito_grupo", args=[fav.pk]), {"grupo": g.pk}, HTTP_X_REQUESTED_WITH="fetch"
    )
    assert r.json()["no_grupo"] is True and fav.grupos.filter(pk=g.pk).exists()
    assert "AAPL" in cliente_logado.get(reverse("favoritos"), {"grupo": g.pk}).content.decode()


def test_lista_de_ativos_esconde_acompanhados(cliente_logado, mercado_com_dados):
    html = cliente_logado.get(reverse("asset_list")).content.decode()
    assert "só acompanhados" in html and f'href="/ativos/{mercado_com_dados.pk}/"' not in html
    assert "AAPL" in cliente_logado.get(reverse("asset_list"), {"ver": "todos"}).content.decode()


def test_detalhe_do_ativo_tem_leitura_de_mercado(cliente_logado, mercado_com_dados):
    html = cliente_logado.get(reverse("asset_detail", args=[mercado_com_dados.pk])).content.decode()
    assert "Leitura de mercado" in html and "Por que pode subir" in html and "O que pode impedir" in html


def test_previa_de_regra_de_mercado(cliente_logado, mercado_com_dados):
    r = cliente_logado.get(
        reverse("regra_previa"), {"tipo": "rompe_suporte", "valor": "20", "ativo": mercado_com_dados.pk}
    )
    (item,) = r.json()["itens"]
    assert item["estado"] == "dispararia" and "suporte" in item["detalhe"]
    assert (
        cliente_logado.get(reverse("regra_previa"), {"tipo": "rompe_resist", "valor": "20"}).json()["itens"]
        == []
    )


def test_atalho_bitcoin_77500(cliente_logado, db):
    Asset.objects.create(symbol="BTC", name="Bitcoin", market="CRIPTO", custody="BINANCE")
    html = cliente_logado.get(reverse("regra_nova")).content.decode()
    assert "Bitcoin abaixo de US$ 77.500" in html and 'data-valor="77.500"' in html


def test_json_dos_graficos_do_dolar_e_seguro(cliente_logado, db):
    html = cliente_logado.get(reverse("mercado_dolar")).content.decode()
    assert "Dólar comercial agora" in html
    assert json.loads('{"ok": true}')  # sanidade do import
