"""rc8.3 — ações da B3 "indisponíveis": erro por ativo, faixa fora do plano, ativos incluídos em feriado."""

import datetime as dt
from io import StringIO

import httpx
import pytest
from django.core.management import call_command

from apps.marketdata import collector
from apps.marketdata.adapters import brapi as brapi_mod
from apps.marketdata.adapters.brapi import BrapiAdapter
from apps.marketdata.models import Quote
from apps.portfolio.models import Asset

SP = dt.timezone(dt.timedelta(hours=-3))
FERIADO = dt.datetime(2026, 11, 2, 20, 0, tzinfo=SP)  # Finados: B3 fechada, última sessão 30/10


def cliente(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def cotacao(sym, preco=30.5):
    return httpx.Response(
        200,
        json={
            "results": [
                {
                    "requestedSymbol": sym,
                    "symbol": sym,
                    "data": {
                        "regularMarketPrice": preco,
                        "currency": "BRL",
                        "regularMarketPreviousClose": 30,
                        "regularMarketTime": "2026-10-30T20:07:00.000Z",
                    },
                }
            ]
        },
    )


def test_brapi_ticker_com_erro_nao_derruba_os_demais(monkeypatch):
    monkeypatch.setattr(brapi_mod, "ESPERA_CONCORRENCIA_S", 0)
    chamadas = {"ABEV3": 0}

    def handler(req):
        sym = req.url.params["symbols"]
        if sym == "XPTO3":
            return httpx.Response(404, json={"error": True, "message": "não encontrado"})
        if sym == "RENT3":
            return httpx.Response(422, json={"error": True, "message": "parâmetros"})
        if sym == "ABEV3":
            chamadas["ABEV3"] += 1
            if chamadas["ABEV3"] == 1:  # 1 requisição simultânea no Gratuito: 429 passageiro
                return httpx.Response(429, headers={"Retry-After": "1"}, json={"error": True})
        return cotacao(sym)

    r = BrapiAdapter(client=cliente(handler), agora=lambda: FERIADO).cotar(
        ["PETR4", "XPTO3", "RENT3", "ABEV3"]
    )
    assert sorted(c.simbolo for c in r.cotacoes) == ["ABEV3", "PETR4"]
    assert "404" in r.falhas["XPTO3"] and "422" in r.falhas["RENT3"]
    assert chamadas["ABEV3"] == 2


def test_brapi_historico_faixa_recusada_com_422_cai_para_3_meses():
    faixas = []

    def handler(req):
        faixas.append(req.url.params["range"])
        if req.url.params["range"] != "3mo":
            return httpx.Response(422, json={"error": True, "message": "range não permitido"})
        return httpx.Response(
            200,
            json={"results": [{"historicalDataPrice": [{"date": 1761746400, "close": 30, "volume": 1}]}]},
        )

    ad = BrapiAdapter(client=cliente(handler), agora=lambda: FERIADO)
    (c,) = ad.historico("BBAS3", 400)
    assert faixas == ["2y", "3mo"] and "3 meses" in ad.aviso_plano


def test_brapi_ticker_inexistente_nao_tenta_outra_faixa():
    faixas = []

    def handler(req):
        faixas.append(req.url.params["range"])
        return httpx.Response(404, json={"error": True})

    with pytest.raises(brapi_mod.AdapterError):
        BrapiAdapter(client=cliente(handler), agora=lambda: FERIADO).historico("XPTO3", 400)
    assert faixas == ["2y"]


@pytest.fixture
def b3(db, settings):
    settings.PORTAL = {**settings.PORTAL, "MODO_DEMO": False}
    a = Asset.objects.create(
        symbol="PETR4", name="Petrobras", market="B3", custody="NENHUMA", acompanhamento=True
    )
    estado = collector.estado_tarefa("B3")
    estado.last_close_date = dt.date(2026, 10, 30)
    estado.last_attempt_at = FERIADO - dt.timedelta(hours=3)
    estado.save()
    Quote.objects.create(
        asset=a,
        price=30,
        currency="BRL",
        collected_at=estado.last_attempt_at,
        source="brapi",
        quote_type="atrasado",
    )
    return estado


def test_ativo_incluido_no_feriado_recebe_fechamento(b3):
    devida, motivo = collector.tarefa_devida("B3", FERIADO, b3)
    assert not devida and "já coletado" in motivo

    Asset.objects.create(symbol="WEGE3", name="WEG", market="B3", custody="NENHUMA", acompanhamento=True)
    Asset.objects.filter(symbol="WEGE3").update(updated_at=FERIADO - dt.timedelta(hours=1))  # incluído depois
    devida, motivo = collector.tarefa_devida("B3", FERIADO, collector.estado_tarefa("B3"))
    assert devida and "WEGE3" in motivo

    # Depois da nova tentativa, um ticker que continua sem cotação não faz a tarefa repetir o feriado todo.
    estado = collector.estado_tarefa("B3")
    estado.last_attempt_at = FERIADO
    estado.save()
    devida, _ = collector.tarefa_devida("B3", FERIADO + dt.timedelta(hours=1), estado)
    assert not devida


def test_atualizar_agora_refaz_fechamento_com_pregao_fechado(b3):
    collector.solicitar_coleta(FERIADO)
    devida, motivo = collector.tarefa_devida("B3", FERIADO, collector.estado_tarefa("B3"))
    assert devida and "forçada" in motivo


def test_diagnosticar_mostra_ativos_sem_vazar_chave(b3, settings):
    settings.FONTES = {
        **settings.FONTES,
        "CHAVES": {**settings.FONTES.get("CHAVES", {}), "brapi": "segredo-123"},
    }
    Asset.objects.create(symbol="BBAS3", name="BB", market="B3", custody="NENHUMA", acompanhamento=True)
    saida = StringIO()
    call_command("diagnosticar", "B3", stdout=saida)
    texto = saida.getvalue()
    assert "PETR4" in texto and "BBAS3" in texto and "nenhuma" in texto
    assert "preenchida" in texto and "segredo-123" not in texto


def test_variacao_do_dia_sem_candles_vem_da_cotacao(b3):
    from apps.mercado import analise, mercados

    a = Asset.objects.get(symbol="PETR4")
    Quote.objects.filter(asset=a).update(previous_close=29)
    x = analise.leitura_do_ativo(a)
    assert x.ind is None and round(x.variacao("1d"), 2) == 3.45 and x.variacao("1m") is None
    r = mercados.retorno_em_moeda(x, "1d", "BRL", mercados.Cambio(atual=None))
    assert round(r.moeda, 2) == 3.45
    assert mercados.retorno_em_moeda(x, "1m", "BRL", mercados.Cambio(atual=None)).moeda is None
