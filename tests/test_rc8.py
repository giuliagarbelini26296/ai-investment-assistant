"""rc8 — Ações EUA, Ações Brasil, Comparar mercados, proventos, macro e cotação mais frequente."""

import datetime as dt
from decimal import Decimal

import httpx
import pytest
from django.urls import reverse
from django.utils import timezone

from apps.marketdata import collector
from apps.marketdata.adapters import adaptador_cotacoes, fonte_cotacao
from apps.marketdata.adapters.base import AuthError
from apps.marketdata.adapters.brapi import BrapiAdapter, _fracao
from apps.marketdata.adapters.inteligencia import (
    AlphaVantageAdapter,
    FichaEmpresa,
    FinnhubAdapter,
    FocusBcbAdapter,
    SgsBcbAdapter,
)
from apps.marketdata.models import DailyBar, FxDailyRate, Quote
from apps.mercado import analise, coleta, mercados
from apps.mercado import score as sc
from apps.mercado.models import Fundamentos, MacroSerie, Provento, RefreshState
from apps.portfolio import importacao
from apps.portfolio.models import Asset

D = Decimal


def cliente(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def json_resp(corpo, status=200):
    return httpx.Response(status, json=corpo)


# --- brapi ------------------------------------------------------------------------------------------


def test_brapi_uma_acao_por_requisicao_e_token_por_simbolo(settings):
    pedidos = []

    def handler(req):
        sym = req.url.params["symbols"]
        pedidos.append(sym)
        if sym == "BBAS3":
            return json_resp({"error": True, "message": "token"}, 401)
        return json_resp(
            {
                "results": [
                    {
                        "requestedSymbol": sym,
                        "symbol": sym,
                        "data": {
                            "regularMarketPrice": 30.5,
                            "currency": "BRL",
                            "regularMarketPreviousClose": 30,
                            "regularMarketTime": "2026-10-29T14:50:30.000Z",
                        },
                    }
                ]
            }
        )

    r = BrapiAdapter(
        client=cliente(handler), agora=lambda: dt.datetime(2026, 10, 29, 15, tzinfo=dt.UTC)
    ).cotar(["PETR4", "BBAS3"])
    assert pedidos == ["PETR4", "BBAS3"]
    assert [c.simbolo for c in r.cotacoes] == ["PETR4"] and "token" in r.falhas["BBAS3"]
    with pytest.raises(AuthError):
        BrapiAdapter(client=cliente(handler)).cotar(["BBAS3"])


def test_brapi_historico_cai_para_3_meses_no_plano_gratuito():
    faixas = []

    def handler(req):
        faixas.append(req.url.params["range"])
        if req.url.params["range"] != "3mo":
            return json_resp({"error": True, "message": "Seu plano não inclui esses dados."}, 403)
        return json_resp(
            {
                "results": [
                    {
                        "historicalDataPrice": [
                            {"date": 1761746400, "close": 30, "high": 31, "low": 29, "open": 30, "volume": 10}
                        ]
                    }
                ]
            }
        )

    ad = BrapiAdapter(client=cliente(handler), agora=lambda: dt.datetime(2026, 10, 30, 15, tzinfo=dt.UTC))
    (c,) = ad.historico("PETR4", 400)
    assert faixas == ["2y", "3mo"] and c.close == D("30") and "3 meses" in ad.aviso_plano


def test_brapi_fundamentos_e_proventos():
    def handler(req):
        if "statistics" in req.url.path:
            return json_resp(
                {
                    "results": [
                        {
                            "symbol": "WEGE3",
                            "data": {
                                "trailingPE": 30.2,
                                "priceToBook": 10.07,
                                "enterpriseToEbitda": 20.29,
                                "dividendYield": 0.03,
                            },
                        }
                    ]
                }
            )
        if "financial-data" in req.url.path:
            return json_resp(
                {
                    "results": [
                        {
                            "symbol": "WEGE3",
                            "data": {
                                "returnOnEquity": 0.378,
                                "revenueGrowth": 0.004,
                                "ebitda": 100,
                                "totalDebt": 150,
                                "totalCash": 50,
                                "targetMeanPrice": 60,
                                "recommendationKey": "buy",
                                "recommendationMean": 2.1,
                                "numberOfAnalystOpinions": 12,
                            },
                        }
                    ]
                }
            )
        return json_resp(
            {
                "results": [
                    {
                        "symbol": "WEGE3",
                        "data": {
                            "cashDividends": [
                                {
                                    "rate": 0.25,
                                    "label": "JCP",
                                    "lastDatePrior": "2026-09-30T00:00:00.000Z",
                                    "paymentDate": "2026-11-20T00:00:00.000Z",
                                },
                                {"rate": 0, "label": "DIVIDENDO"},
                            ]
                        },
                    }
                ]
            }
        )

    ad = BrapiAdapter(client=cliente(handler), api_key="k")
    e = ad.estatisticas("WEGE3")
    assert (
        e.pl == D("30.2")
        and e.pvp == D("10.07")
        and e.ev_ebitda == D("20.29")
        and e.dividend_yield == D("0.03")
    )
    f = ad.dados_financeiros("WEGE3")
    assert f.roe == D("0.378") and f.divida_liquida_ebitda == D("1") and f.divida_liquida_calculada
    assert f.preco_alvo.media == D("60") and f.recomendacao_media == D("2.1") and f.n_analistas == 12
    (p,) = ad.proventos("WEGE3")
    assert (
        p.tipo == "JCP" and p.data_com == dt.date(2026, 9, 30) and p.data_pagamento == dt.date(2026, 11, 20)
    )
    assert _fracao("3.5") == D("0.035") and _fracao("0.02") == D("0.02")


# --- outras fontes ------------------------------------------------------------------------------------


def test_finnhub_cotacao_eua():
    def handler(req):
        if req.url.params["symbol"] == "ZZZZ":
            return json_resp({"c": 0, "pc": 0, "t": 0})
        return json_resp({"c": 194.5, "pc": 192.1, "h": 195, "l": 190, "t": 1761940800})

    r = FinnhubAdapter(
        client=cliente(handler), api_key="k", agora=lambda: dt.datetime(2026, 11, 2, 21, tzinfo=dt.UTC)
    ).cotar(["TTWO", "ZZZZ"])
    (c,) = r.cotacoes
    assert c.preco == D("194.5") and c.fechamento_anterior == D("192.1") and c.horario_mercado is not None
    assert "sem cotação" in r.falhas["ZZZZ"]


def test_alphavantage_dividendos_sgs_e_focus():
    av = AlphaVantageAdapter(
        client=cliente(
            lambda r: json_resp(
                {
                    "symbol": "IBM",
                    "data": [
                        {"ex_dividend_date": "2026-08-08", "payment_date": "2026-09-10", "amount": "1.68"},
                        {"ex_dividend_date": "None", "amount": "None"},
                    ],
                }
            )
        ),
        api_key="k",
    )
    (d,) = av.dividendos("IBM")
    assert d.valor == D("1.68") and d.moeda == "USD" and d.data_com == dt.date(2026, 8, 8)
    sgs = SgsBcbAdapter(client=cliente(lambda r: json_resp([{"data": "01/10/2026", "valor": "4.85"}])))
    assert sgs.serie(13522, 1) == [(dt.date(2026, 10, 1), D("4.85"))]
    filtros = []

    def focus(req):
        filtros.append(req.url.params["$filter"])
        return json_resp(
            {
                "value": [
                    {
                        "Indicador": "Selic",
                        "Data": "2026-10-30",
                        "DataReferencia": "2026",
                        "Mediana": 14.25,
                        "baseCalculo": 0,
                    }
                ]
            }
        )

    (e,) = FocusBcbAdapter(client=cliente(focus)).expectativas(("Câmbio", "Selic", "IPCA"))
    assert e.indicador == "Selic" and "Indicador eq 'IPCA'" in filtros[0]


def test_intervalos_e_fonte_de_cotacao_configuraveis(settings):
    settings.FONTES = {
        **settings.FONTES,
        "ACOES_EUA_INTERVALO_MIN": "5",
        "BRAPI_INTERVALO_MIN": "30",
        "FONTE_COTACAO_EUA": "finnhub",
        "CHAVES": {**settings.FONTES["CHAVES"], "finnhub": "k"},
    }
    settings.PORTAL = {**settings.PORTAL, "MODO_DEMO": False}
    assert collector.intervalo_da_tarefa("NYSE") == dt.timedelta(minutes=5)
    assert collector.intervalo_da_tarefa("B3") == dt.timedelta(minutes=30)
    assert fonte_cotacao("NASDAQ") == "finnhub" and fonte_cotacao("B3") == "brapi"
    assert isinstance(adaptador_cotacoes("NYSE"), FinnhubAdapter)


# --- coleta ------------------------------------------------------------------------------------------


@pytest.fixture
def weg(db):
    return Asset.objects.create(
        symbol="WEGE3",
        name="WEG",
        market="B3",
        custody="NENHUMA",
        acompanhamento=True,
        sector="Bens industriais",
    )


def test_brapi_passo_mescla_ficha_grava_proventos_e_respeita_plano(weg, settings, monkeypatch):
    settings.PORTAL = {**settings.PORTAL, "MODO_DEMO": False}

    class Falso:
        info = BrapiAdapter.info

        def estatisticas(self, s):
            return FichaEmpresa(simbolo=s, fonte="brapi", pl=D("30"), pvp=D("10"))

        def dados_financeiros(self, s):
            return FichaEmpresa(simbolo=s, fonte="brapi", roe=D("0.3"))

        def proventos(self, s):
            raise AuthError("brapi: Seu plano não inclui esses dados.")

    monkeypatch.setattr(coleta, "criar_adaptador", lambda codigo: Falso())
    agora = timezone.now()
    coleta.brapi_passo(agora)
    f = Fundamentos.objects.get(asset=weg)
    assert (f.pe, f.price_to_book, f.roe) == (
        D("30"),
        D("10"),
        D("0.3"),
    )  # a segunda ficha não apagou a primeira
    estado = RefreshState.objects.get(asset=weg, kind="br_proventos")
    assert estado.last_error.startswith("autenticacao")
    assert coleta.brapi_passo(agora + dt.timedelta(hours=2)) == {}  # sem acesso: não insiste


def test_macro_passo_grava_series(db, settings, monkeypatch):
    settings.PORTAL = {**settings.PORTAL, "MODO_DEMO": False}

    class Sgs:
        def serie(self, codigo, n):
            return [(dt.date(2026, 10, 1), D("14.25") if codigo == 432 else D("4.8"))]

    monkeypatch.setattr(coleta, "criar_adaptador", lambda codigo: Sgs())
    agora = timezone.make_aware(dt.datetime(2026, 11, 3, 10, 0))
    assert coleta.macro_passo(agora) == {"macro": 4}  # Selic, IPCA mês, IPCA 12m e CDI
    assert MacroSerie.objects.get(codigo=432).valor == D("14.25")
    assert coleta.macro_passo(agora + dt.timedelta(hours=2)) == {}  # uma vez por dia


def test_importacao_atualiza_custodia_generica(db, usuario):
    Asset.objects.create(symbol="ORCL", name="Oracle", market="NYSE", custody="NENHUMA", acompanhamento=True)
    csv = b"simbolo,data,quantidade,preco,moeda,cambio,mercado,custodia\nORCL,2026-10-01,10,147.45,USD,,NYSE,AVENUE\n"
    previa = importacao.pre_visualizar(csv)
    importacao.confirmar(importacao.serializar(previa), usuario)
    a = Asset.objects.get(symbol="ORCL")
    assert a.custody == "AVENUE" and not a.acompanhamento and a.lots.count() == 1


# --- análise ------------------------------------------------------------------------------------------


def _barras(asset, n=300, inicio=50.0, passo=0.0005, fonte="brapi"):
    hoje = timezone.localdate()
    agora = timezone.now()
    preco = inicio
    for i in range(n):
        data = hoje - dt.timedelta(days=n - i)
        preco *= 1 + passo * (1 if i % 2 else -0.5)
        DailyBar.objects.create(
            asset=asset,
            session_date=data,
            time_basis="sessao_local",
            close=D(str(round(preco, 4))),
            high=D(str(round(preco * 1.01, 4))),
            low=D(str(round(preco * 0.99, 4))),
            volume=D("100"),
            source=fonte,
            collected_at=agora,
        )
        FxDailyRate.objects.get_or_create(
            pair="USD-BRL",
            date=data,
            rate_type="ptax_fechamento",
            source="ptax_bcb",
            defaults={
                "bid": D(str(round(5.0 + i * 0.001 + (0.02 if i % 3 == 0 else 0), 4))),
                "collected_at": agora,
            },
        )


def test_dy_por_proventos_e_consenso_por_media(weg):
    _barras(weg)
    Quote.objects.create(
        asset=weg,
        price=D("50"),
        currency="BRL",
        collected_at=timezone.now(),
        source="brapi",
        quote_type="atrasado",
    )
    hoje = timezone.localdate()
    for k, v in enumerate(("0.5", "0.5")):
        Provento.objects.create(
            asset=weg,
            source="brapi",
            tipo="JCP",
            data_com=hoje - dt.timedelta(days=30 + 100 * k),
            valor=D(v),
            moeda="BRL",
            collected_at=timezone.now(),
        )
    Fundamentos.objects.create(
        asset=weg,
        source="brapi",
        recommendation_mean=D("1.5"),
        analyst_opinions=10,
        pe=D("20"),
        collected_at=timezone.now(),
    )
    x = analise.leitura_do_ativo(weg)
    assert x.dy[0] == pytest.approx(2.0) and "proventos" in x.dy[1]
    a2 = {c.codigo: c for c in x.score.criterios}["A2"]
    assert a2.avaliado and a2.pontos == pytest.approx(15)
    assert sc.MIN_ANALISTAS == 5


def test_comparar_mercados_moeda_comum_e_proventos(db):
    us = Asset.objects.create(
        symbol="AAPL", name="Apple", market="NASDAQ", custody="NENHUMA", acompanhamento=True
    )
    br = Asset.objects.create(
        symbol="PETR4", name="Petrobras", market="B3", custody="NENHUMA", acompanhamento=True
    )
    _barras(us, fonte="twelvedata")
    _barras(br)
    agora = timezone.now()
    Quote.objects.create(
        asset=us,
        price=D("60"),
        currency="USD",
        collected_at=agora,
        source="twelvedata",
        quote_type="nao_declarado",
    )
    Quote.objects.create(
        asset=br, price=D("40"), currency="BRL", collected_at=agora, source="brapi", quote_type="atrasado"
    )
    Provento.objects.create(
        asset=br,
        source="brapi",
        tipo="DIVIDENDO",
        data_com=timezone.localdate() - dt.timedelta(days=20),
        valor=D("1"),
        moeda="BRL",
        collected_at=agora,
    )
    linhas = {li.x.asset.symbol: li for li in mercados.comparar([us, br], "BRL", "6m")}
    a, p = linhas["AAPL"], linhas["PETR4"]
    assert a.retorno_moeda is not None and a.efeito_cambial is not None
    assert p.efeito_cambial is None and p.retorno_moeda == pytest.approx(p.retorno_nativo)
    assert p.retorno_com_proventos > p.retorno_moeda and p.proventos_moeda == D("1")
    assert a.volatilidade is not None and a.queda_maxima <= 0 and a.serie[0][1] == pytest.approx(100)
    s = mercados.sensibilidade_dolar(br)
    assert s.n >= 40 and s.beta is not None


# --- telas ----------------------------------------------------------------------------------------------


def test_abas_eua_brasil_e_comparar(cliente_logado, db):
    for nome in ("mercado_eua", "mercado_brasil", "mercado_comparar"):
        r = cliente_logado.get(reverse(nome))
        assert r.status_code == 200, nome
    assert "Incluir agora" in cliente_logado.get(reverse("mercado_eua")).content.decode()
    cliente_logado.post(reverse("mercado_iniciais", args=["eua"]))
    cliente_logado.post(reverse("mercado_iniciais", args=["brasil"]))
    assert set(Asset.objects.filter(market="B3").values_list("symbol", flat=True)) == {
        s for s, *_ in mercados.INICIAIS_BRASIL
    }
    assert Asset.objects.filter(symbol="PLTR", market="NASDAQ").exists()
    analise.invalidar_cache()
    html = cliente_logado.get(reverse("mercado_brasil")).content.decode()
    assert "Selic" in html and "Sensibilidade ao dólar" in html and "precisam do token gratuito" in html
    html = cliente_logado.get(reverse("mercado_eua"), {"moeda": "BRL", "periodo": "3m"}).content.decode()
    assert "Cotação (BRL)" in html and "Comparação no mesmo setor" in html
    html = cliente_logado.get(
        reverse("mercado_comparar"), {"moeda": "USD", "periodo": "12m"}
    ).content.decode()
    assert (
        "Desempenho e risco em USD" in html
        and "Fundamentos e potencial" in html
        and "Não constitui recomendação" in html
    )


def test_alphavantage_limite_por_segundo_e_finnhub_valor_de_mercado(monkeypatch):
    from apps.marketdata.adapters.base import RateLimitError

    monkeypatch.setattr(AlphaVantageAdapter, "INTERVALO_MIN_S", 0)
    aviso = {
        "Information": "Thank you for using Alpha Vantage! Please consider spreading out your free API requests more sparingly (1 request per second)."
    }
    with pytest.raises(RateLimitError, match="por segundo"):
        AlphaVantageAdapter(client=cliente(lambda r: json_resp(aviso)), api_key="k").ficha("TTWO")
    m = {
        "metric": {
            "marketCapitalization": 35558.07,
            "52WeekHigh": 265.94,
            "52WeekLow": 183.86,
            "roeTTM": -9.12,
        }
    }
    f = FinnhubAdapter(client=cliente(lambda r: json_resp(m)), api_key="k").ficha("TTWO")
    assert f.valor_mercado == D("35558070000") and f.roe == D("-0.0912")


def test_alphavantage_endpoint_pago_suspende_funcao(db, settings, monkeypatch):
    from apps.marketdata.models import ScheduleState

    settings.PORTAL = {**settings.PORTAL, "MODO_DEMO": False}
    settings.FONTES = {**settings.FONTES, "CHAVES": {**settings.FONTES["CHAVES"], "alphavantage": "k"}}
    monkeypatch.setattr(AlphaVantageAdapter, "INTERVALO_MIN_S", 0)
    Asset.objects.create(
        symbol="AVGO", name="Broadcom", market="NASDAQ", custody="NENHUMA", acompanhamento=True
    )
    Asset.objects.create(
        symbol="GOOGL", name="Alphabet", market="NASDAQ", custody="NENHUMA", acompanhamento=True
    )
    pedidos = []

    def handler(req):
        pedidos.append(req.url.params["function"])
        return json_resp({"Information": "Thank you for using Alpha Vantage! This is a premium endpoint."})

    real = coleta.criar_adaptador
    monkeypatch.setattr(
        coleta,
        "criar_adaptador",
        lambda c: (
            AlphaVantageAdapter(client=cliente(handler), api_key="k") if c == "alphavantage" else real(c)
        ),
    )
    agora = timezone.now()
    coleta.alphavantage_passo(agora)
    assert ScheduleState.objects.get(task="AV_FICHA").last_status == "sem_acesso"
    coleta.alphavantage_passo(agora + dt.timedelta(minutes=1))
    assert pedidos == ["OVERVIEW", "DIVIDENDS"]  # a ficha não é pedida de novo para o outro ativo
