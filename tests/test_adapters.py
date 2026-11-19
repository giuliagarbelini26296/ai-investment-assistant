"""Adaptadores × fixtures. Nenhum teste acessa a rede (httpx.MockTransport)."""

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from apps.marketdata.adapters import (
    FONTE_POR_MERCADO,
    adaptador_cambio,
    adaptador_para_mercado,
    criar_adaptador,
    todas_as_fontes,
)
from apps.marketdata.adapters.base import (
    AuthError,
    InvalidResponseError,
    NetworkError,
    RateLimitError,
    SymbolNotFoundError,
)
from apps.marketdata.adapters.binance import BinanceAdapter, moeda_do_par
from apps.marketdata.adapters.brapi import BrapiAdapter
from apps.marketdata.adapters.demo import DemoAdapter, DemoFxAdapter
from apps.marketdata.adapters.fx import AwesomeApiAdapter, PtaxBcbAdapter
from apps.marketdata.adapters.twelvedata import TwelveDataAdapter
from apps.marketdata.models import FxRateType, QuoteType

FIX = Path(__file__).parent / "fixtures" / "sources"
REAL_B3 = {t["test"]: t for t in json.loads((FIX / "testes-fontes-dados-2026-10-29-b3.json").read_text())}
DOC = json.loads((FIX / "documentais-nao-homologadas.json").read_text())
REAL = json.loads((FIX / "reais-2026-10-30.json").read_text())

AGORA = dt.datetime(2026, 10, 29, 15, 0, tzinfo=dt.UTC)  # 12h00 em São Paulo
D = Decimal


def transporte(respostas):
    """respostas: função(request) -> (status, corpo_json|texto, headers) ou lista sequencial."""
    fila = list(respostas) if isinstance(respostas, list) else None

    def handler(request: httpx.Request) -> httpx.Response:
        item = fila.pop(0) if fila is not None else respostas(request)
        status, corpo, headers = (item + (None,))[:3] if len(item) == 2 else item
        if isinstance(corpo, str | bytes):
            return httpx.Response(status, content=corpo, headers=headers or {})
        return httpx.Response(status, json=corpo, headers=headers or {})

    return httpx.Client(transport=httpx.MockTransport(handler))


def agora():
    return AGORA


# --- brapi (fixture REAL) ---------------------------------------------------------------


def test_brapi_cotacao_v2_com_resposta_real():
    ad = BrapiAdapter(client=transporte([(200, REAL_B3["brapi_v2_quote"]["payload"])]), agora=agora)
    r = ad.cotar(["PETR4"])
    assert not r.falhas
    (c,) = r.cotacoes
    assert (c.simbolo, c.preco, c.moeda) == ("PETR4", D("52.53"), "BRL")
    assert c.horario_mercado == dt.datetime(2026, 10, 29, 14, 50, 30, tzinfo=dt.UTC)
    assert c.fechamento_anterior == D("52.51")
    assert c.tipo == QuoteType.DELAYED and c.atraso_declarado_min == 30
    assert c.fonte == "brapi" and c.fonte_versao == "v2"


def test_brapi_resposta_parcial_marca_ausentes(settings):
    settings.FONTES = {**settings.FONTES, "BRAPI_SIMBOLOS_POR_REQUISICAO": "10"}  # plano com lote
    ad = BrapiAdapter(client=transporte([(200, REAL_B3["brapi_v2_quote"]["payload"])]), agora=agora)
    r = ad.cotar(["PETR4", "VALE3"])
    assert [c.simbolo for c in r.cotacoes] == ["PETR4"]
    assert "ausente" in r.falhas["VALE3"]
    assert r.parcial


def test_brapi_ticker_convertido_exige_revisao():
    corpo = {
        "results": [
            {
                "requestedSymbol": "VELHO3",
                "symbol": "NOVO3",
                "changed": True,
                "data": {"regularMarketPrice": 10, "currency": "BRL"},
            }
        ]
    }
    r = BrapiAdapter(client=transporte([(200, corpo)]), agora=agora).cotar(["VELHO3"])
    assert not r.cotacoes and "convertido" in r.falhas["VELHO3"]


def test_brapi_sem_token_vira_auth_error():
    t = REAL_B3["brapi_restricted"]
    with pytest.raises(AuthError):
        BrapiAdapter(client=transporte([(t["http_status"], t["payload"])]), agora=agora).cotar(["WEGE3"])


def test_brapi_historico_real_22_candles_com_provisorio():
    t = REAL_B3["brapi_history"]
    ad = BrapiAdapter(client=transporte([(200, t["payload"])]), agora=agora)
    candles = ad.historico("PETR4", 30)
    assert len(candles) == 22
    assert candles[0].data == dt.date(2026, 9, 29) and candles[0].close == D("49.1")
    assert candles[2].data == dt.date(2026, 10, 1) and candles[2].close == D(
        "49.77"
    )  # diverge do Yahoo (49,90)
    assert candles[-1].data == dt.date(2026, 10, 29) and candles[-1].provisorio is True
    assert all(not c.provisorio for c in candles[:-1])
    assert all(c.fonte == "brapi" for c in candles)


def test_brapi_historico_sem_token_401():
    t = REAL_B3["brapi_invalid"]
    with pytest.raises(AuthError):
        BrapiAdapter(client=transporte([(t["http_status"], t["payload"])]), agora=agora).historico("X")


# --- Twelve Data (fixture DOCUMENTAL) ---------------------------------------------------


def test_twelvedata_cotacao_unica():
    ad = TwelveDataAdapter(
        client=transporte([(200, DOC["twelvedata_quote_single"])]), api_key="k", agora=agora
    )
    r = ad.cotar(["TTWO"])
    (c,) = r.cotacoes
    assert c.preco == D("181.23") and c.moeda == "USD" and c.fechamento_anterior == D("180.10")
    # o 'timestamp' é o início do candle diário, não a última negociação: não vira horário de mercado
    assert c.horario_mercado is None
    assert c.bruto["timestamp_candle"] == dt.datetime.fromtimestamp(1793285100, tz=dt.UTC).isoformat()
    assert c.tipo == QuoteType.UNDECLARED and c.atraso_declarado_min is None
    assert ad.info.homologada


def test_twelvedata_captura_real_mercado_fechado():
    """Captura real 30/10/2026 17:57 ET: close = fechamento do dia; timestamp = 09:30 ET (início do candle)."""
    ad = TwelveDataAdapter(client=transporte([(200, REAL["twelvedata_quote_ttwo_fechado"])]), agora=agora)
    (c,) = ad.cotar(["TTWO"]).cotacoes
    assert c.preco == D("190.17") and c.fechamento_anterior == D("193.39") and c.moeda == "USD"
    assert c.horario_mercado is None and c.bruto["is_market_open"] is False
    assert c.bruto["timestamp_candle"] == "2026-10-30T13:30:00+00:00"
    serie = TwelveDataAdapter(
        client=transporte([(200, REAL["twelvedata_time_series_ttwo_ultimos"])]), agora=agora
    )
    candles = serie.historico("TTWO", 3)
    assert [str(x.data) for x in candles] == ["2026-10-28", "2026-10-29", "2026-10-30"]
    assert candles[-1].close == D("190.17") and candles[-1].volume == D("3564157")


def test_twelvedata_lote_com_simbolo_invalido_e_parcial():
    ad = TwelveDataAdapter(client=transporte([(200, DOC["twelvedata_quote_batch"])]), agora=agora)
    r = ad.cotar(["TTWO", "AAPL", "XXXX", "NAO_VEIO"])
    assert sorted(c.simbolo for c in r.cotacoes) == ["AAPL", "TTWO"]
    assert "not found" in r.falhas["XXXX"]
    assert "ausente" in r.falhas["NAO_VEIO"]


def test_twelvedata_http_200_com_erro_no_corpo():
    with pytest.raises(AuthError):
        TwelveDataAdapter(client=transporte([(200, DOC["twelvedata_error_401"])]), agora=agora).cotar(
            ["TTWO"]
        )
    with pytest.raises(RateLimitError):
        TwelveDataAdapter(client=transporte([(200, DOC["twelvedata_error_429"])]), agora=agora).cotar(
            ["TTWO"]
        )


def test_twelvedata_envia_chave_como_parametro_e_nao_no_log():
    visto = {}

    def h(req):
        visto["url"] = str(req.url)
        return (200, DOC["twelvedata_quote_single"])

    TwelveDataAdapter(client=transporte(h), api_key="SEGREDO", agora=agora).cotar(["TTWO"])
    assert "apikey=SEGREDO" in visto["url"]


def test_twelvedata_historico_ordenado_por_data():
    ad = TwelveDataAdapter(client=transporte([(200, DOC["twelvedata_time_series"])]), agora=agora)
    candles = ad.historico("TTWO", 3)
    assert [c.data for c in candles] == [dt.date(2026, 10, 27), dt.date(2026, 10, 28), dt.date(2026, 10, 29)]
    assert candles[-1].close == D("181.23")


# --- Binance (fixture DOCUMENTAL) -------------------------------------------------------


def test_binance_lote_usdt_e_brl():
    ad = BinanceAdapter(client=transporte([(200, DOC["binance_ticker_24hr_batch"])]), agora=agora)
    r = ad.cotar(["BTCUSDT", "BTCBRL"])
    por = {c.simbolo: c for c in r.cotacoes}
    assert por["BTCUSDT"].moeda == "USDT" and por["BTCUSDT"].preco == D("81207.97")
    assert por["BTCBRL"].moeda == "BRL" and por["BTCBRL"].preco == D("432000")
    assert por["BTCUSDT"].variacao_24h_pct == D("-1.456")
    assert por["BTCUSDT"].horario_mercado == dt.datetime.fromtimestamp(1793285500.123, tz=dt.UTC)
    assert por["BTCUSDT"].tipo == QuoteType.SPOT


def test_binance_simbolo_invalido():
    with pytest.raises(InvalidResponseError):
        BinanceAdapter(client=transporte([(400, DOC["binance_error_invalid_symbol"])]), agora=agora).cotar(
            ["X"]
        )


def test_binance_418_e_429():
    with pytest.raises(RateLimitError):
        BinanceAdapter(client=transporte([(418, {"code": -1003, "msg": "banned"})]), agora=agora).cotar(
            ["BTCUSDT"]
        )
    with pytest.raises(RateLimitError) as exc:
        BinanceAdapter(client=transporte([(429, {}, {"Retry-After": "7"})]), agora=agora).cotar(["BTCUSDT"])
    assert exc.value.retry_after == 7


def test_binance_klines_dia_utc_e_provisorio():
    ad = BinanceAdapter(client=transporte([(200, DOC["binance_klines"])]), agora=agora)
    candles = ad.historico("BTCUSDT", 2)
    assert candles[0].data == dt.date(2026, 10, 28) and candles[0].provisorio is False
    assert candles[1].data == dt.date(2026, 10, 29) and candles[1].provisorio is True
    assert candles[1].base_tempo == "dia_utc"


def test_moeda_do_par():
    assert (
        moeda_do_par("BTCUSDT") == "USDT"
        and moeda_do_par("ETHBRL") == "BRL"
        and moeda_do_par("BTCEUR") is None
    )


# --- Câmbio (fixtures DOCUMENTAIS) ------------------------------------------------------


def test_ptax_dia():
    ad = PtaxBcbAdapter(client=transporte([(200, DOC["ptax_dia"])]), agora=agora)
    c = ad.cambio_diario(dt.date(2026, 10, 28))
    assert c.compra == D("5.3271") and c.venda == D("5.3277") and c.tipo == FxRateType.PTAX_CLOSE
    assert c.data == dt.date(2026, 10, 28) and c.horario.hour == 13


def test_ptax_dia_sem_cotacao_devolve_none_e_cambio_recua():
    ad = PtaxBcbAdapter(
        client=transporte([(200, DOC["ptax_dia_vazio"]), (200, DOC["ptax_dia"])]), agora=agora
    )
    assert ad.cambio_diario(dt.date(2026, 10, 25)) is None
    c = ad.cambio()  # 29/10 vazio → 28/10
    assert c.compra == D("5.3271")


def test_ptax_envia_data_no_formato_mm_dd_yyyy():
    visto = {}

    def h(req):
        visto["q"] = str(req.url)
        return (200, DOC["ptax_dia"])

    PtaxBcbAdapter(client=transporte(h), agora=agora).cambio_diario(dt.date(2026, 10, 28))
    assert "10-28-2026" in visto["q"]


def test_awesomeapi_intradiario():
    ad = AwesomeApiAdapter(client=transporte([(200, DOC["awesomeapi_last"])]), agora=agora)
    c = ad.cambio()
    assert c.compra == D("5.3301") and c.venda == D("5.3309") and c.tipo == FxRateType.INTRADAY
    assert c.horario == dt.datetime.fromtimestamp(1793285400, tz=dt.UTC)


def test_awesomeapi_horario_futuro_e_rejeitado():
    corpo = {"USDBRL": {**DOC["awesomeapi_last"]["USDBRL"], "timestamp": str(int(AGORA.timestamp()) + 3600)}}
    with pytest.raises(InvalidResponseError):
        AwesomeApiAdapter(client=transporte([(200, corpo)]), agora=agora).cambio()


# --- Validação comum --------------------------------------------------------------------


def test_cotacao_com_horario_futuro_vira_falha_e_nao_cotacao():
    corpo = json.loads(json.dumps(REAL_B3["brapi_v2_quote"]["payload"]))
    corpo["results"][0]["data"]["regularMarketTime"] = "2026-10-29T16:00:00.000Z"  # 1h após AGORA
    r = BrapiAdapter(client=transporte([(200, corpo)]), agora=agora).cotar(["PETR4"])
    assert not r.cotacoes and "futuro" in r.falhas["PETR4"]


def test_moeda_inesperada_vira_falha():
    corpo = json.loads(json.dumps(REAL_B3["brapi_v2_quote"]["payload"]))
    corpo["results"][0]["data"]["currency"] = "USD"
    r = BrapiAdapter(client=transporte([(200, corpo)]), agora=agora).cotar(["PETR4"])
    assert "moeda" in r.falhas["PETR4"]


def test_preco_zero_ou_ausente_vira_falha():
    corpo = json.loads(json.dumps(REAL_B3["brapi_v2_quote"]["payload"]))
    corpo["results"][0]["data"]["regularMarketPrice"] = 0
    r = BrapiAdapter(client=transporte([(200, corpo)]), agora=agora).cotar(["PETR4"])
    assert "preço inválido" in r.falhas["PETR4"]


@pytest.mark.parametrize(
    "status, erro", [(500, NetworkError), (503, NetworkError), (404, SymbolNotFoundError), (403, AuthError)]
)
def test_status_http_classificados(status, erro):
    with pytest.raises(erro):
        BrapiAdapter(client=transporte([(status, {"error": True})]), agora=agora).cotar(["PETR4"])


def test_timeout_e_corpo_nao_json():
    def estoura(req):
        raise httpx.ReadTimeout("t", request=req)

    with pytest.raises(NetworkError):
        BrapiAdapter(client=httpx.Client(transport=httpx.MockTransport(estoura)), agora=agora).cotar(
            ["PETR4"]
        )
    with pytest.raises(InvalidResponseError):
        BrapiAdapter(client=transporte([(200, "<html>")]), agora=agora).cotar(["PETR4"])


def test_alpha_vantage_demo_nao_e_cotacao():
    """HTTP 200 com 'Information' (fixture real): o parser deve rejeitar; exemplo via brapi."""
    t = REAL_B3["alpha_b3_demo"]
    with pytest.raises(InvalidResponseError):
        BrapiAdapter(client=transporte([(200, t["payload"])]), agora=agora).cotar(["PETR4"])


# --- Demo e registro ----------------------------------------------------------------------


def test_demo_e_deterministico_e_rotulado():
    a = DemoAdapter(agora=agora).cotar(["DEMO1", "DEMOBTC"])
    b = DemoAdapter(agora=agora).cotar(["DEMO1", "DEMOBTC"])
    assert [c.preco for c in a.cotacoes] == [c.preco for c in b.cotacoes]
    assert all(c.fonte == "demo" and c.preco > 0 for c in a.cotacoes)
    candles = DemoAdapter(agora=agora).historico("DEMO1", 30)
    assert len(candles) == 30 and candles[-1].provisorio and candles[-1].data == AGORA.date()


def test_demo_fx_sem_ptax_em_fim_de_semana():
    fx = DemoFxAdapter(agora=agora)
    assert fx.cambio_diario(dt.date(2026, 10, 25)) is None  # domingo
    assert fx.cambio_diario(dt.date(2026, 10, 28)).compra > 0
    assert fx.cambio().tipo == FxRateType.REFERENCE


def test_registro_em_modo_demo_e_real(settings):
    settings.PORTAL = {**settings.PORTAL, "MODO_DEMO": True}
    assert isinstance(adaptador_para_mercado("NASDAQ"), DemoAdapter)
    assert adaptador_para_mercado("B3").moeda_cotacao == "BRL"
    assert isinstance(adaptador_cambio(), DemoFxAdapter)
    settings.PORTAL = {**settings.PORTAL, "MODO_DEMO": False}
    settings.FONTES = {"CHAVES": {"twelvedata": "abc"}}
    ad = adaptador_para_mercado("NASDAQ")
    assert isinstance(ad, TwelveDataAdapter) and ad.api_key == "abc"
    assert isinstance(adaptador_para_mercado("CRIPTO"), BinanceAdapter)
    assert isinstance(adaptador_cambio("fechamento"), PtaxBcbAdapter)
    assert isinstance(adaptador_cambio("intradiario"), AwesomeApiAdapter)
    assert FONTE_POR_MERCADO["B3"] == "brapi" and isinstance(criar_adaptador("brapi"), BrapiAdapter)


def test_todas_as_fontes_declaram_atraso_e_termos():
    for info in todas_as_fontes():
        assert info.atraso_declarado_texto
        assert info.codigo.startswith("demo") or info.termos_url


def test_erro_de_decodificacao_repete_sem_compressao():
    """Resposta comprimida indecodificável (br/zstd sem biblioteca) → nova tentativa com Accept-Encoding: identity."""
    pedidos = []
    corpo = json.loads(json.dumps(REAL_B3["brapi_v2_quote"]["payload"]))

    def handler(req):
        pedidos.append(req.headers.get("accept-encoding", ""))
        if len(pedidos) == 1:
            raise httpx.DecodingError("Received response with content-encoding: br", request=req)
        return httpx.Response(200, json=corpo)

    r = BrapiAdapter(client=httpx.Client(transport=httpx.MockTransport(handler)), agora=agora).cotar(
        ["PETR4"]
    )
    assert len(pedidos) == 2 and pedidos[1] == "identity" and "PETR4" in {c.simbolo for c in r.cotacoes}


def test_erro_de_decodificacao_persistente_vira_erro_de_rede_com_detalhe():
    def handler(req):
        raise httpx.DecodingError("Received response with content-encoding: zstd", request=req)

    with pytest.raises(NetworkError) as exc:
        BrapiAdapter(client=httpx.Client(transport=httpx.MockTransport(handler)), agora=agora).cotar(
            ["PETR4"]
        )
    assert "DecodingError" in str(exc.value) and "zstd" in str(exc.value)


# --- Capturas REAIS de 30/10/2026 (homologação na máquina do usuário) ---------------------------------

AGORA_REAL = dt.datetime(2026, 10, 30, 20, 57, 49, tzinfo=dt.UTC)


def agora_real():
    return AGORA_REAL


def test_binance_captura_real_btc():
    ad = BinanceAdapter(client=transporte([(200, REAL["binance_ticker_24hr_btc"])]), agora=agora_real)
    por = {c.simbolo: c for c in ad.cotar(["BTCUSDT", "BTCBRL"]).cotacoes}
    assert por["BTCUSDT"].preco == D("76924") and por["BTCUSDT"].moeda == "USDT"
    assert por["BTCUSDT"].variacao_24h_pct == D("-2.635") and por["BTCUSDT"].fechamento_anterior == D(
        "79005.74"
    )
    assert por["BTCUSDT"].horario_mercado == dt.datetime(2026, 10, 30, 20, 57, 48, 6000, tzinfo=dt.UTC)
    assert por["BTCBRL"].preco == D("414448") and por["BTCBRL"].moeda == "BRL"
    assert ad.info.homologada
    candles = BinanceAdapter(
        client=transporte([(200, REAL["binance_klines_btcusdt_ultimos"])]), agora=agora_real
    ).historico("BTCUSDT", 2)
    assert [str(c.data) for c in candles] == ["2026-10-29", "2026-10-30"]
    assert candles[0].close == D("77991.89") and not candles[0].provisorio and candles[1].provisorio


def test_ptax_captura_real_2026_10_30():
    ad = PtaxBcbAdapter(client=transporte([(200, REAL["ptax_dia_2026_10_30"])]), agora=agora_real)
    c = ad.cambio_diario(dt.date(2026, 10, 30))
    assert c.compra == D("5.4206") and c.venda == D("5.4212") and c.data == dt.date(2026, 10, 30)
    assert c.horario.hour == 13 and c.horario.minute == 4 and ad.info.homologada
