"""rc9 — resumo executivo estruturado: analistas por instituição, Focus, estado real das fontes."""

import datetime as dt
from decimal import Decimal as D

import httpx
import pytest
from django.urls import reverse
from django.utils import timezone

from apps.marketdata.adapters.base import AuthError
from apps.marketdata.adapters.inteligencia import FinnhubAdapter
from apps.marketdata.models import DailyBar, Quote, ScheduleState
from apps.mercado import analise, coleta, resumo
from apps.mercado.models import AnalystAction, AnalystSnapshot, FocusExpectativa
from apps.portfolio.models import Asset


def cliente(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_finnhub_mudancas_de_recomendacao_com_instituicao():
    def handler(req):
        assert req.url.path.endswith("/stock/upgrade-downgrade") and req.url.params["from"] == "2026-08-05"
        return httpx.Response(
            200,
            json=[
                {
                    "symbol": "TTWO",
                    "gradeTime": 1762128000,
                    "company": "Banco Exemplo",
                    "fromGrade": "Neutral",
                    "toGrade": "Buy",
                    "action": "up",
                },
                {"symbol": "TTWO", "gradeTime": "x", "company": "sem data"},
            ],
        )

    (m,) = FinnhubAdapter(client=cliente(handler), api_key="k").mudancas_recomendacao(
        "TTWO", dt.date(2026, 8, 5)
    )
    assert (m.instituicao, m.de, m.para, m.acao) == ("Banco Exemplo", "Neutral", "Buy", "up")
    assert m.data == dt.date(2025, 11, 3)


@pytest.fixture
def eua(db, settings):
    settings.PORTAL = {**settings.PORTAL, "MODO_DEMO": False}
    settings.FONTES = {**settings.FONTES, "CHAVES": {**settings.FONTES.get("CHAVES", {}), "finnhub": "k"}}
    return [
        Asset.objects.create(symbol=s, name=s, market="NASDAQ", custody="NENHUMA", acompanhamento=True)
        for s in ("AAA", "BBB")
    ]


def test_endpoint_pago_da_finnhub_suspende_o_tipo_para_todos(eua, monkeypatch):
    chamadas = []

    class Falso:
        info = FinnhubAdapter.info

        def __getattr__(self, nome):
            def f(simbolo, *a, **k):
                chamadas.append((nome, simbolo))
                if nome in ("preco_alvo", "mudancas_recomendacao"):
                    raise AuthError("finnhub: You don't have access to this resource.")
                return [] if nome != "ficha" else None

            return f

    monkeypatch.setattr(coleta, "criar_adaptador", lambda codigo: Falso())
    monkeypatch.setattr(coleta, "_gravar_ficha", lambda *a, **k: None)
    agora = timezone.now()
    coleta.finnhub_passo(agora)
    pagos = [c for c in chamadas if c[0] in ("preco_alvo", "mudancas_recomendacao")]
    assert sorted(pagos) == [("mudancas_recomendacao", "AAA"), ("preco_alvo", "AAA")]  # 1 tentativa por tipo
    assert coleta._fh_bloqueados(agora) == {"preco_alvo", "mudancas"}
    estados = {f["codigo"]: f for f in resumo.estado_fontes(agora)}
    assert (
        estados["finnhub"]["estado"] == "parcial"
        and "analistas por instituição" in estados["finnhub"]["detalhe"]
    )


def _ativo_com_historico(simbolo, mercado="NASDAQ", n=260, salto_em=None):
    a = Asset.objects.create(symbol=simbolo, name=f"{simbolo} Inc.", market=mercado, custody="NENHUMA")
    hoje = timezone.localdate()
    preco = 100.0
    for i in range(n):
        d = hoje - dt.timedelta(days=n - i)
        preco *= 1.002
        if salto_em is not None and i == n - salto_em:
            preco *= 0.7
        DailyBar.objects.create(
            asset=a,
            session_date=d,
            source="twelvedata" if mercado != "B3" else "brapi",
            time_basis="sessao_local",
            open=D(str(preco)),
            high=D(str(preco * 1.01)),
            low=D(str(preco * 0.99)),
            close=D(str(round(preco, 4))),
            volume=D("1000"),
            collected_at=timezone.now(),
        )
    Quote.objects.create(
        asset=a,
        price=D(str(round(preco, 2))),
        currency="USD" if mercado != "B3" else "BRL",
        previous_close=D(str(round(preco / 1.01, 2))),
        collected_at=timezone.now(),
        source="twelvedata" if mercado != "B3" else "brapi",
        quote_type="atrasado",
    )
    return a


def test_resumo_estruturado_analistas_focus_e_atipicos(db, cliente_logado):
    a = _ativo_com_historico("AAA")
    b = _ativo_com_historico("RRR3", "B3", salto_em=5)
    agora = timezone.now()
    AnalystSnapshot.objects.create(
        asset=a,
        source="finnhub",
        period=dt.date(2026, 11, 1),
        strong_buy=4,
        buy=4,
        hold=2,
        collected_at=agora,
    )
    AnalystAction.objects.create(
        asset=a,
        source="finnhub",
        date=timezone.localdate(),
        firm="Banco Exemplo",
        from_grade="Hold",
        to_grade="Buy",
        action="up",
        collected_at=agora,
    )
    ano = str(agora.year)
    FocusExpectativa.objects.create(
        indicador="Selic",
        data=dt.date(2026, 10, 30),
        data_referencia=ano,
        mediana=D("14.25"),
        minimo=D("13.5"),
        maximo=D("15"),
        respondentes=110,
        collected_at=agora,
    )
    ScheduleState.objects.create(
        task="FOCUS", last_attempt_at=agora, last_success_at=agora, last_status="completo"
    )
    analise.invalidar_cache()
    p = resumo.montar(analise.leituras(), set())
    assert p.com_variacao == 2 and p.referencias
    (c,) = p.consenso_top
    assert (
        c.total == 10
        and c.pct_compra == 80
        and [s[0] for s in c.barras] == ["compra-forte", "compra", "manter"]
    )
    assert p.mudancas[0].firm == "Banco Exemplo" and p.mudancas[0].acao_rotulo == "elevou"
    assert p.focus[0].rotulo == "Selic no fim do ano" and p.focus_data == dt.date(2026, 10, 30)
    assert any(li.x.asset == b and "único pregão" in li.detalhe for li in p.atipicos)

    html = cliente_logado.get(reverse("mercado")).content.decode()
    for trecho in (
        "Mudanças por instituição",
        "Banco Exemplo",
        "Boletim Focus",
        "Conferir o dado",
        "Maior proporção de compra",
    ):
        assert trecho in html, trecho
    assert "não homologada" not in html


def test_selos_mostram_falha_do_focus(db, cliente_logado):
    agora = timezone.now()
    ScheduleState.objects.create(
        task="FOCUS", last_attempt_at=agora, last_status="falha", last_error="rede: tempo esgotado"
    )
    estados = {f["codigo"]: f for f in resumo.estado_fontes(agora)}
    assert estados["focus_bcb"]["estado"] == "falha" and "tempo esgotado" in estados["focus_bcb"]["detalhe"]
