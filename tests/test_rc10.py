"""Revisão de dados e navegação: somente dados sintéticos e banco de teste."""
import datetime as dt
from decimal import Decimal as D
from types import SimpleNamespace

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.marketdata.models import DailyBar, ScheduleState
from apps.mercado import analise, mercados
from apps.mercado.estado import estado_coleta
from apps.portfolio.conferencia import compras_para_conferir, conferir
from apps.portfolio.models import Asset, Lot
from tests.test_rc7 import gravar_barras, cotar


@pytest.fixture
def ativos_revisao(db):
    ativos = []
    for symbol, market, etf in [("ACAO", "NASDAQ", False), ("FUNDO", "NYSE", True), ("BTC", "CRIPTO", False)]:
        a = Asset.objects.create(symbol=symbol, name=symbol, market=market, is_etf=etf,
                                 custody="NENHUMA", acompanhamento=True)
        gravar_barras(a, n=365, fonte="binance" if market == "CRIPTO" else "twelvedata")
        cotar(a, 100, previous=99)
        ativos.append(a)
    analise.invalidar_cache()
    yield ativos
    analise.invalidar_cache()


def test_aviso_compra_antiga_sem_alterar_lote(cliente_logado, ativos_revisao):
    btc = ativos_revisao[-1]
    lot = Lot.objects.create(asset=btc, trade_date=timezone.localdate() - dt.timedelta(days=1),
                             quantity=D(1), unit_price=D("0.10"), fx_rate=D("416.57"))
    lot.refresh_from_db()
    original = lot.snapshot()
    assert len(compras_para_conferir()) == 1
    for url in [reverse("home_classico"), reverse("asset_detail", args=[btc.pk])]:
        response = cliente_logado.get(url)
        assert response.status_code == 200
        assert "Confira os valores" in response.content.decode()
        assert reverse("lot_update", args=[lot.pk]) in response.content.decode()
    lot.refresh_from_db()
    # The existing view may resolve FX metadata; review must never change financial inputs.
    for field in ("quantity", "unit_price", "fx_rate", "trade_date"):
        assert lot.snapshot()[field] == original[field]
    lot.cancelado_em = timezone.now()
    lot.save()
    assert compras_para_conferir() == []


def test_conferencia_nao_usa_preco_futuro(db):
    a = Asset.objects.create(symbol="XYZ", name="XYZ", market="NASDAQ", custody="OUTRA")
    data = timezone.localdate() - dt.timedelta(days=2)
    DailyBar.objects.create(asset=a, session_date=data + dt.timedelta(days=1), close=D(1000),
                            source="teste", collected_at=timezone.now(), time_basis="sessao_local")
    assert conferir(a, D(1), data, D(5)) == {}


def test_criptomoedas_so_mostra_cripto(cliente_logado, ativos_revisao):
    r = cliente_logado.get(reverse("mercado_cripto"))
    assert r.status_code == 200
    assert [x.asset.symbol for x in r.context["lista"]] == ["BTC"]
    assert "pontos técnicos" in r.content.decode()
    r = cliente_logado.get(reverse("mercado_cripto"), {"fragmento": "1", "periodo": "7d"})
    assert r.status_code == 200
    assert 'Seções do Mercado' not in r.content.decode()


@pytest.mark.parametrize("tipo,simbolo", [("acao", "ACAO"), ("etf", "FUNDO"), ("cripto", "BTC")])
def test_oportunidades_separa_classes(cliente_logado, ativos_revisao, tipo, simbolo):
    r = cliente_logado.get(reverse("mercado_oportunidades"), {"tipo": tipo})
    assert r.status_code == 200
    assert [x.asset.symbol for x in r.context["todos"]] == [simbolo]
    assert "Cobertura:" in r.content.decode()
    assert f"tipo={tipo}" in r.content.decode()


def test_falha_coleta_consistente_sem_erro_tecnico(cliente_logado):
    agora = timezone.now()
    for task in ("FOCUS", "MACRO"):
        ScheduleState.objects.create(task=task, last_attempt_at=agora, last_status="falha",
                                      last_error="HTTP 400 detalhe interno")
    assert estado_coleta("FOCUS")["estado"] == "falha"
    assert estado_coleta("OUTRA")["estado"] == "aguardando"
    for route in ("mercado", "mercado_dolar", "mercado_brasil"):
        r = cliente_logado.get(reverse(route))
        assert r.status_code == 200
        html = r.content.decode()
        assert "Não foi possível atualizar" in html
        assert "HTTP 400 detalhe interno" not in html
    e = ScheduleState.objects.get(task="FOCUS")
    e.last_success_at = agora + dt.timedelta(seconds=1)
    e.last_status = "completo"
    e.last_error = ""
    e.save()
    assert estado_coleta("FOCUS")["estado"] == "ok"


def test_minimas_separam_janelas():
    anual = SimpleNamespace(ind=SimpleNamespace(janela_52s_completa=True, dist_minima_52s_pct=5))
    parcial = SimpleNamespace(ind=SimpleNamespace(janela_52s_completa=False, dist_minima_52s_pct=2))
    assert mercados.perto_da_minima([anual, parcial]) == [anual]
    assert mercados.perto_da_minima([anual, parcial], completa=False) == [parcial]


def test_comparacao_preserva_selecao_e_tem_busca(cliente_logado, ativos_revisao):
    ids = [str(a.pk) for a in ativos_revisao[:2]]
    r = cliente_logado.get(reverse("comparar"), {"ativos": ids})
    assert r.status_code == 200
    assert r.context["ids"] == ids
    assert 'id="buscar-comparacao"' in r.content.decode()
    assert r.content.decode().count('checked>') == 2
    assert "Ctrl/Cmd" not in r.content.decode()


def test_nova_aba_exige_login(client):
    assert client.get(reverse("mercado_cripto")).status_code == 302
