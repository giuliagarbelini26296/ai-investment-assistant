"""Alertas em linguagem simples e notificações (Telegram via caixa de saída; feed do macOS)."""

import datetime as dt
import json
from decimal import Decimal

import httpx
import pytest
from django.urls import reverse

from apps.alerts import notify
from apps.alerts import services as alerts
from apps.alerts.forms import AlertaForm, interpretar_numero
from apps.alerts.models import TEXTO_NAO_RECOMENDACAO, Alert, Notification
from apps.marketdata import collector
from apps.marketdata.adapters.base import BaseAdapter, CotacaoNormalizada, FonteInfo

from .test_alerts import T0, cotar, regra


@pytest.fixture
def ttwo(db, usuario):
    from apps.portfolio import services as portfolio
    from apps.portfolio.models import Asset

    a = Asset.objects.create(symbol="TTWO", name="Take-Two", market="NASDAQ", custody="AVENUE")
    portfolio.criar_lote(a, usuario, trade_date=dt.date(2026, 10, 1), quantity=D("10"), unit_price=D("100"))
    return a


class Fonte(BaseAdapter):
    """Adaptador falso: TTWO a US$ 80 (queda de 20% sobre o preço médio 100)."""

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


D = Decimal
CFG = {
    "TELEGRAM_BOT_TOKEN": "123:ABC",
    "TELEGRAM_CHAT_ID": "42",
    "FEED_TOKEN": "feed-token-de-teste",
    "URL_PORTAL": "http://portal.local",
}


# --- Formulário -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "texto, esperado",
    [
        ("5", "5"),
        ("5,5", "5.5"),
        ("5.5", "5.5"),
        ("100.000,00", "100000.00"),
        ("100,000.00", "100000.00"),
        ("US$ 100.000", "100000"),
        ("1.234.567", "1234567"),
        (" 0,25 ", "0.25"),
        ("0.125", "0.125"),
        ("5.500", "5500"),
        ("12.5", "12.5"),
    ],
)
def test_interpretar_numero(texto, esperado):
    assert interpretar_numero(texto) == D(esperado)


def test_form_traduz_frase_em_parametros(ttwo):
    f = AlertaForm(
        {"asset": ttwo.pk, "tipo": "queda_dia", "valor": "5", "currency": "USD", "classification": "aviso"}
    )
    assert f.is_valid(), f.errors
    assert f.parametros() == {
        "family": "dia_24h",
        "condition": "queda",
        "threshold": D("5"),
        "currency": "",
        "classification": "aviso",
    }
    assert f.nome() == "TTWO: queda de 5% no dia"
    f = AlertaForm(
        {"asset": "", "tipo": "abaixo", "valor": "90,50", "currency": "BRL", "classification": "aviso"}
    )
    assert f.is_valid(), f.errors
    assert f.parametros()["currency"] == "BRL" and f.parametros()["threshold"] == D("90.50")
    assert f.nome() == "Carteira: abaixo de R$ 90,5"
    f = AlertaForm(
        {"asset": "", "tipo": "queda_compra", "valor": "150", "currency": "USD", "classification": "aviso"}
    )
    assert not f.is_valid() and "100%" in str(f.errors["valor"])
    f = AlertaForm({"asset": "", "tipo": "acima", "valor": "10", "currency": "", "classification": "aviso"})
    assert not f.is_valid() and "currency" in f.errors


def test_form_inicial_de_versao_roundtrip(ttwo, usuario):
    rule = regra(
        usuario, ttwo, family="preco_absoluto", condition="acima", threshold=D("120.5"), currency="USD"
    )
    ini = AlertaForm.inicial_de_versao(rule, rule.versao_atual)
    assert ini["tipo"] == "acima" and ini["valor"] == "120,5" and ini["currency"] == "USD"


def test_tela_nova_mostra_atalhos_e_link_do_ativo(cliente_logado, ttwo):
    html = cliente_logado.get(reverse("regra_nova") + f"?ativo={ttwo.pk}").content.decode()
    assert "Me avise quando" in html and "Queda de 5% no dia" in html and TEXTO_NAO_RECOMENDACAO in html
    assert f'<option value="{ttwo.pk}" selected' in html
    detalhe = cliente_logado.get(reverse("asset_detail", args=[ttwo.pk])).content.decode()
    assert f"{reverse('regra_nova')}?ativo={ttwo.pk}" in detalhe


# --- Caixa de saída e Telegram --------------------------------------------------------------------


def _cliente_telegram(respostas: list):
    """MockTransport que grava as requisições e devolve as respostas na ordem."""
    chamadas = []

    def handler(request: httpx.Request) -> httpx.Response:
        chamadas.append(request)
        status, corpo = respostas.pop(0)
        return httpx.Response(status, json=corpo)

    return httpx.Client(transport=httpx.MockTransport(handler)), chamadas


@pytest.fixture
def canais(settings):
    settings.NOTIFICACOES = dict(CFG)
    return settings


def test_sem_canais_nao_enfileira(settings, ttwo, usuario):
    settings.NOTIFICACOES = {
        "TELEGRAM_BOT_TOKEN": "",
        "TELEGRAM_CHAT_ID": "",
        "FEED_TOKEN": "",
        "URL_PORTAL": "",
    }
    regra(usuario, ttwo)
    cotar(ttwo, "80", T0)
    disparados = alerts.avaliar_ativo(ttwo, T0)
    assert len(disparados) == 1 and notify.enfileirar(disparados) == [] and Notification.objects.count() == 0


def test_dispara_enfileira_e_envia_telegram(canais, ttwo, usuario):
    regra(usuario, ttwo, classification="compra")
    cotar(ttwo, "80", T0)
    disparados = alerts.avaliar_ativo(ttwo, T0)
    criadas = notify.enfileirar(disparados)
    assert {n.channel for n in criadas} == {"telegram", "mac"}
    assert notify.enfileirar(disparados) == []  # idempotente: restrição alerta × canal
    cliente, chamadas = _cliente_telegram([(200, {"ok": True})])
    assert notify.enviar_pendentes(client=cliente, agora=T0) == 1
    assert len(chamadas) == 1
    req = chamadas[0]
    assert req.url.path == "/bot123:ABC/sendMessage"
    corpo = json.loads(req.content)
    assert corpo["chat_id"] == "42"
    texto = corpo["text"]
    assert "TTWO: queda ≥ 10% desde a compra" in texto
    assert "US$ 80,00" in texto and "-20,00%" in texto and "Limite da regra: 10%" in texto
    assert "Possível oportunidade de compra" in texto
    assert "Fonte: t (atrasado)" in texto and TEXTO_NAO_RECOMENDACAO in texto
    assert texto.endswith("http://portal.local/alertas/")
    n = Notification.objects.get(channel="telegram")
    assert n.status == "enviada" and n.sent_at == T0 and n.attempts == 1
    # canal mac fica pendente até o script confirmar
    assert Notification.objects.get(channel="mac").status == "pendente"


def test_falha_registra_e_retenta_ate_o_limite(canais, ttwo, usuario):
    regra(usuario, ttwo, classification="aviso")
    cotar(ttwo, "80", T0)
    notify.enfileirar(alerts.avaliar_ativo(ttwo, T0))
    cliente, _ = _cliente_telegram([(429, {"ok": False, "description": "Too Many Requests"})])
    assert notify.enviar_pendentes(client=cliente, agora=T0) == 0
    n = Notification.objects.get(channel="telegram")
    assert n.status == "falha" and n.attempts == 1 and "HTTP 429" in n.last_error
    assert "123:ABC" not in n.last_error  # token nunca vai para o log/banco
    cliente, _ = _cliente_telegram([(200, {"ok": True})])
    assert notify.enviar_pendentes(client=cliente, agora=T0) == 1
    n.refresh_from_db()
    assert n.status == "enviada" and n.attempts == 2
    # mensagem sem rótulo: não cita classificação
    _, chamadas = _cliente_telegram([])
    assert "Seu rótulo" not in notify.corpo(n.alert)


def test_apos_max_tentativas_para_de_tentar(canais, ttwo, usuario):
    regra(usuario, ttwo)
    cotar(ttwo, "80", T0)
    notify.enfileirar(alerts.avaliar_ativo(ttwo, T0))
    n = Notification.objects.get(channel="telegram")
    n.attempts, n.status = notify.MAX_TENTATIVAS, "falha"
    n.save()
    cliente, chamadas = _cliente_telegram([(200, {"ok": True})])
    assert notify.enviar_pendentes(client=cliente, agora=T0) == 0 and chamadas == []


def test_coletor_enfileira_no_ciclo(canais, ttwo, usuario, monkeypatch):
    """Integração: o ciclo de mercado dispara o alerta e cria as notificações."""
    regra(usuario, ttwo)
    enviados = []
    monkeypatch.setattr(notify, "enviar_pendentes", lambda **kw: enviados.append(kw) or 0)
    run = collector.executar_ciclo_mercado("NYSE", T0, adaptador=Fonte(), dormir=lambda s: None)
    assert run.status == "completo"
    assert Notification.objects.filter(channel="telegram").count() == 1 and enviados


def test_erro_no_envio_nao_derruba_coleta(canais, ttwo, usuario, monkeypatch):
    regra(usuario, ttwo)

    def explode(**kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(notify, "enviar_pendentes", explode)
    run = collector.executar_ciclo_mercado("NYSE", T0, adaptador=Fonte(), dormir=lambda s: None)
    assert run.status == "completo" and Alert.objects.count() == 1


# --- Feed do macOS --------------------------------------------------------------------------------


def test_feed_mac_exige_token_e_confirma(canais, client, ttwo, usuario):
    regra(usuario, ttwo)
    cotar(ttwo, "80", T0)
    notify.enfileirar(alerts.avaliar_ativo(ttwo, T0))
    assert client.get(reverse("alertas_feed")).status_code == 401
    assert client.get(reverse("alertas_feed"), HTTP_X_PORTAL_TOKEN="errado").status_code == 401
    r = client.get(reverse("alertas_feed"), HTTP_X_PORTAL_TOKEN=CFG["FEED_TOKEN"])
    assert r.status_code == 200 and r["Content-Type"].startswith("text/plain")
    linhas = r.content.decode().strip().split("\n")
    assert len(linhas) == 1
    nid, titulo, mensagem = linhas[0].split("\t")
    assert titulo == "TTWO: queda ≥ 10% desde a compra" and "US$ 80,00" in mensagem and "\n" not in mensagem
    assert TEXTO_NAO_RECOMENDACAO in mensagem
    # confirmar sem token
    r = client.post(reverse("alertas_feed_confirmar"), data=f"[{nid}]", content_type="application/json")
    assert r.status_code == 401
    r = client.post(
        reverse("alertas_feed_confirmar"),
        data=f"[{nid}]",
        content_type="application/json",
        HTTP_X_PORTAL_TOKEN=CFG["FEED_TOKEN"],
    )
    assert r.status_code == 200 and r.json() == {"confirmadas": 1}
    assert Notification.objects.get(pk=int(nid)).status == "enviada"
    # feed agora vazio
    r = client.get(reverse("alertas_feed"), HTTP_X_PORTAL_TOKEN=CFG["FEED_TOKEN"])
    assert r.content == b""


def test_feed_desligado_sem_token_configurado(settings, client):
    settings.NOTIFICACOES = {**CFG, "FEED_TOKEN": ""}
    assert client.get(reverse("alertas_feed"), HTTP_X_PORTAL_TOKEN="").status_code == 401


def test_painel_mostra_canais_e_notificacoes(canais, cliente_logado, ttwo, usuario):
    regra(usuario, ttwo)
    cotar(ttwo, "80", dt.datetime.now(dt.UTC))
    notify.enfileirar(alerts.avaliar_ativo(ttwo))
    html = cliente_logado.get(reverse("alertas")).content.decode()
    assert (
        "Canais ativos" in html
        and "Telegram" in html
        and "Notificação do macOS" in html
        and "Pendente" in html
    )
