"""Alertas: 6 condições, transições, suspensão, versões, integração com o coletor e painel."""

import datetime as dt
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from apps.alerts import services as alerts
from apps.alerts.models import TEXTO_NAO_RECOMENDACAO, Alert, Rule, RuleState, RuleVersion
from apps.marketdata.models import Quote
from apps.portfolio import services as portfolio
from apps.portfolio.models import Asset

D = Decimal
NY = ZoneInfo("America/New_York")
T0 = dt.datetime(2026, 10, 29, 12, 0, tzinfo=NY)  # NYSE aberta


@pytest.fixture
def ttwo(db, usuario):
    a = Asset.objects.create(symbol="TTWO", name="Take-Two", market="NASDAQ", custody="AVENUE")
    portfolio.criar_lote(a, usuario, trade_date=dt.date(2026, 10, 1), quantity=D("10"), unit_price=D("100"))
    return a


@pytest.fixture
def btc(db, usuario):
    a = Asset.objects.create(symbol="BTC", name="Bitcoin", market="CRIPTO", custody="BINANCE")
    portfolio.criar_lote(
        a, usuario, trade_date=dt.date(2026, 10, 1), quantity=D("0.1"), unit_price=D("60000")
    )
    return a


def cotar(asset, preco, agora, previous=None, change24=None, moeda="USD", tipo="atrasado", fonte="t"):
    return Quote.objects.create(
        asset=asset,
        price=D(preco),
        currency=moeda,
        previous_close=None if previous is None else D(previous),
        change_24h_pct=None if change24 is None else D(change24),
        collected_at=agora,
        market_time=agora - dt.timedelta(minutes=1),
        source=fonte,
        quote_type=tipo,
        declared_delay_minutes=15,
    )


def regra(user, asset=None, **kw):
    p = {
        "family": "desde_compra",
        "condition": "queda",
        "threshold": D("10"),
        "classification": "compra",
        "currency": "",
    }
    p.update(kw)
    return alerts.criar_regra(
        user, name=kw.get("name", "r"), asset=asset, **{k: v for k, v in p.items() if k != "name"}
    )


# --- Condições ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "family, condition, threshold, preco, previous, change24, esperado",
    [
        ("desde_compra", "queda", "10", "90", None, None, True),  # retorno −10% ⇔ ≤ −10
        ("desde_compra", "queda", "10", "90.01", None, None, False),
        ("desde_compra", "alta", "10", "110", None, None, True),
        ("desde_compra", "alta", "10", "109.99", None, None, False),
        ("dia_24h", "queda", "3", "97", "100", None, True),  # variação −3% vs fechamento anterior
        ("dia_24h", "queda", "3", "97.5", "100", None, False),
        ("dia_24h", "alta", "2", "102", "100", None, True),
        ("dia_24h", "alta", "2", "100", "100", "2.5", True),  # cripto: usa a variação 24 h da fonte
        ("preco_absoluto", "abaixo", "95", "94.99", None, None, True),
        ("preco_absoluto", "abaixo", "95", "95", None, None, False),  # estritamente abaixo
        ("preco_absoluto", "acima", "120", "120.01", None, None, True),
    ],
)
def test_seis_condicoes(ttwo, usuario, family, condition, threshold, preco, previous, change24, esperado):
    regra(
        usuario,
        ttwo,
        family=family,
        condition=condition,
        threshold=D(threshold),
        currency="USD" if family == "preco_absoluto" else "",
    )
    cotar(ttwo, preco, T0, previous, change24)
    disparados = alerts.avaliar_ativo(ttwo, T0)
    assert bool(disparados) is esperado
    if esperado:
        a = disparados[0]
        assert a.classification == "compra" and a.texto_obrigatorio == TEXTO_NAO_RECOMENDACAO
        assert D(a.observed["preco"]) == D(preco) and D(a.observed["limite"]) == D(threshold)
        assert a.observed["fonte"] == "t" and a.observed["horario_coleta"]


def test_percentual_na_moeda_nativa_e_valores_exatos(ttwo, usuario):
    regra(usuario, ttwo, threshold=D("10"))
    cotar(ttwo, "90.00000001", T0)  # retorno −9,99999999% → não dispara (sem arredondar)
    assert alerts.avaliar_ativo(ttwo, T0) == []


def test_preco_absoluto_exige_moeda_da_regra(btc, usuario):
    regra(usuario, btc, family="preco_absoluto", condition="abaixo", threshold=D("50000"), currency="BRL")
    cotar(btc, "40000", T0, moeda="USDT", tipo="spot")
    assert alerts.avaliar_ativo(btc, T0) == []
    estado = RuleState.objects.get()
    assert estado.suspended and "moeda" in estado.suspended_reason
    # regra em USD aceita cotação em USDT
    alerts.nova_versao(
        Rule.objects.get(),
        usuario,
        family="preco_absoluto",
        condition="abaixo",
        threshold=D("50000"),
        currency="USD",
        classification="compra",
    )
    assert len(alerts.avaliar_ativo(btc, T0)) == 1


def test_versao_rejeita_condicao_incompativel(ttwo, usuario):
    with pytest.raises(ValidationError):
        regra(usuario, ttwo, family="desde_compra", condition="abaixo")
    with pytest.raises(ValidationError):
        regra(usuario, ttwo, family="preco_absoluto", condition="abaixo", threshold=D("10"), currency="")
    with pytest.raises(ValidationError):
        regra(usuario, ttwo, threshold=D("0"))


# --- Transições ---------------------------------------------------------------------------------


def test_dispara_na_transicao_mantem_resolve_e_rearma(ttwo, usuario):
    regra(usuario, ttwo, threshold=D("10"))
    cotar(ttwo, "95", T0)
    assert alerts.avaliar_ativo(ttwo, T0) == []  # falso
    t1 = T0 + dt.timedelta(minutes=15)
    cotar(ttwo, "88", t1)
    (a,) = alerts.avaliar_ativo(ttwo, t1)  # falso → verdadeiro
    t2 = t1 + dt.timedelta(minutes=15)
    cotar(ttwo, "85", t2)
    assert alerts.avaliar_ativo(ttwo, t2) == []  # persistente: mantém, não duplica
    a.refresh_from_db()
    assert a.is_active and Alert.objects.count() == 1
    t3 = t2 + dt.timedelta(minutes=15)
    cotar(ttwo, "95", t3)
    alerts.avaliar_ativo(ttwo, t3)  # verdadeiro → falso: resolve e rearma
    a.refresh_from_db()
    assert a.resolved_at == t3 and RuleState.objects.get().active_alert is None
    t4 = t3 + dt.timedelta(minutes=15)
    cotar(ttwo, "80", t4)
    assert len(alerts.avaliar_ativo(ttwo, t4)) == 1  # rearmada: dispara de novo
    assert Alert.objects.count() == 2


def test_visto_nao_resolve_nem_rearma(ttwo, usuario):
    regra(usuario, ttwo)
    cotar(ttwo, "80", T0)
    (a,) = alerts.avaliar_ativo(ttwo, T0)
    assert alerts.marcar_vistos([a.pk], T0) == 1
    t1 = T0 + dt.timedelta(minutes=15)
    cotar(ttwo, "79", t1)
    assert alerts.avaliar_ativo(ttwo, t1) == []
    a.refresh_from_db()
    assert a.seen_at == T0 and a.is_active and alerts.nao_vistos() == 0


def test_dado_antigo_suspende_sem_resolver(ttwo, usuario):
    regra(usuario, ttwo)
    cotar(ttwo, "80", T0)
    (a,) = alerts.avaliar_ativo(ttwo, T0)
    tarde = T0 + dt.timedelta(hours=1)  # sem nova cotação: "desatualizado" com pregão aberto
    assert alerts.avaliar_ativo(ttwo, tarde) == []
    estado = RuleState.objects.get()
    assert estado.suspended and estado.last_condition is True
    a.refresh_from_db()
    assert a.is_active
    # retomada com condição ainda verdadeira: não dispara de novo; com falsa: resolve
    t2 = tarde + dt.timedelta(minutes=1)
    cotar(ttwo, "95", t2)
    alerts.avaliar_ativo(ttwo, t2)
    a.refresh_from_db()
    assert a.resolved_at == t2 and not RuleState.objects.get().suspended


def test_sem_posicao_suspende(db, usuario):
    vazio = Asset.objects.create(symbol="AAPL", name="Apple", market="NASDAQ", custody="AVENUE")
    regra(usuario, None)
    cotar(vazio, "100", T0)
    assert alerts.avaliar_ativo(vazio, T0) == []
    assert "sem posição" in RuleState.objects.get().suspended_reason


def test_nova_versao_reinicia_estado_e_resolve_alerta_anterior(ttwo, usuario):
    r = regra(usuario, ttwo, threshold=D("10"))
    cotar(ttwo, "85", T0)
    (a,) = alerts.avaliar_ativo(ttwo, T0)
    v2 = alerts.nova_versao(
        r,
        usuario,
        reason="apertar",
        family="desde_compra",
        condition="queda",
        threshold=D("20"),
        classification="venda",
    )
    a.refresh_from_db()
    assert (
        a.resolved_at is not None and r.current_version == 2 and v2.descricao == "queda ≥ 20% desde a compra"
    )
    t1 = T0 + dt.timedelta(minutes=15)
    cotar(ttwo, "85", t1)
    assert alerts.avaliar_ativo(ttwo, t1) == []  # −15% não atinge 20%
    cotar(ttwo, "75", t1 + dt.timedelta(minutes=15))
    (b,) = alerts.avaliar_ativo(ttwo, t1 + dt.timedelta(minutes=15))
    assert b.version == v2 and b.classification == "venda"
    assert RuleVersion.objects.filter(rule=r).count() == 2


def test_global_e_especifica_sao_independentes(ttwo, usuario):
    regra(usuario, None, name="global", threshold=D("10"))
    regra(usuario, ttwo, name="especifica", threshold=D("5"))
    cotar(ttwo, "93", T0)
    disparados = alerts.avaliar_ativo(ttwo, T0)
    assert [a.version.rule.name for a in disparados] == ["especifica"]
    cotar(ttwo, "85", T0 + dt.timedelta(minutes=15))
    disparados = alerts.avaliar_ativo(ttwo, T0 + dt.timedelta(minutes=15))
    assert [a.version.rule.name for a in disparados] == ["global"]
    assert alerts.ativos_com_alerta() == {ttwo.id}


def test_desativar_regra_resolve_e_preserva_historico(ttwo, usuario):
    r = regra(usuario, ttwo)
    cotar(ttwo, "80", T0)
    alerts.avaliar_ativo(ttwo, T0)
    alerts.desativar_regra(r)
    assert Alert.objects.get().resolved_at is not None
    cotar(ttwo, "70", T0 + dt.timedelta(minutes=15))
    assert alerts.avaliar_ativo(ttwo, T0 + dt.timedelta(minutes=15)) == []


# --- Integração com o coletor --------------------------------------------------------------------------


def test_coletor_avalia_alertas_apos_gravar(ttwo, usuario):
    from apps.marketdata import collector
    from apps.marketdata.adapters.base import BaseAdapter, CotacaoNormalizada, FonteInfo

    class Fonte(BaseAdapter):
        info = FonteInfo(
            codigo="f",
            nome="f",
            tipo_dado="atrasado",
            atraso_declarado_min=15,
            atraso_declarado_texto="15",
            limites="",
            termos_url="",
        )
        moeda_cotacao = "USD"

        def __init__(self):
            super().__init__(client=None, agora=lambda: T0)

        def cotar(self, simbolos):
            return self._finalizar(
                [
                    CotacaoNormalizada(
                        simbolo="TTWO",
                        preco=D("80"),
                        moeda="USD",
                        horario_coleta=T0,
                        fonte="f",
                        tipo="atrasado",
                        horario_mercado=T0 - dt.timedelta(minutes=1),
                        atraso_declarado_min=15,
                    )
                ],
                {},
            )

    regra(usuario, ttwo)
    run = collector.executar_ciclo_mercado("NYSE", T0, adaptador=Fonte(), dormir=lambda s: None)
    assert run.status == "completo"
    a = Alert.objects.get()
    assert a.quote.source == "f" and a.observed["preco"] == "80.00000000"


# --- Painel ----------------------------------------------------------------------------------------------


def test_painel_e_cadastro_de_regras_pelas_telas(cliente_logado, ttwo, usuario):
    r = cliente_logado.post(
        reverse("regra_nova"),
        {
            "name": "Queda 10",
            "asset": ttwo.pk,
            "tipo": "queda_compra",
            "valor": "10",
            "currency": "USD",
            "classification": "compra",
            "reason": "",
        },
    )
    assert r.status_code == 302 and Rule.objects.count() == 1
    r = cliente_logado.post(
        reverse("regra_nova"),
        {
            "name": "",
            "asset": "",
            "tipo": "abaixo",
            "valor": "abc",
            "currency": "USD",
            "classification": "aviso",
        },
    )
    assert r.status_code == 200 and "Informe um número" in r.content.decode() and Rule.objects.count() == 1
    cotar(ttwo, "80", dt.datetime.now(dt.UTC))
    alerts.avaliar_ativo(ttwo)
    html = cliente_logado.get(reverse("alertas")).content.decode()
    assert TEXTO_NAO_RECOMENDACAO in html
    assert "queda ≥ 10% desde a compra" in html and "US$ 80,00" in html and "-20,00%" in html
    assert "Possível oportunidade de compra" in html
    assert 'class="contador">1' in html  # menu: 1 não visto
    home = cliente_logado.get(reverse("home_classico")).content.decode()
    assert "badge-alerta" in home
    a = Alert.objects.get()
    cliente_logado.post(reverse("alertas_vistos"), {"ids": [a.pk]})
    a.refresh_from_db()
    assert a.seen_at is not None and a.is_active
    rule = Rule.objects.get()
    r = cliente_logado.post(
        reverse("regra_editar", args=[rule.pk]),
        {
            "name": "",
            "asset": ttwo.pk,
            "tipo": "queda_compra",
            "valor": "20,5",
            "currency": "USD",
            "classification": "venda",
            "reason": "ajuste",
        },
    )
    assert r.status_code == 302
    rule.refresh_from_db()
    assert rule.current_version == 2 and rule.versao_atual.reason == "ajuste"
    assert rule.versao_atual.threshold == D("20.5") and rule.name == "TTWO: queda de 20,5% desde a compra"
    assert cliente_logado.post(reverse("regra_desativar", args=[rule.pk])).status_code == 302
    rule.refresh_from_db()
    assert not rule.is_active
    assert cliente_logado.get(reverse("alertas")).status_code == 200
