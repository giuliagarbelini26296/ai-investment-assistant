"""Grupo de consulta: autorização, respostas no destino e bloqueio de gravações."""

from decimal import Decimal

import pytest

from apps.alerts import notify, services, telegram_bot
from apps.alerts.models import Alert, Notification, Rule, RuleVersion

from .test_alerts import T0, cotar, regra
from .test_rc3 import CFG, carteira  # noqa: F401
from .test_telegram_bot import TelegramFalso, _clique, _msg

GRUPO = "-1001234567890"


@pytest.fixture
def grupo(settings):
    settings.NOTIFICACOES = {**CFG, "TELEGRAM_GRUPO_CHAT_ID": GRUPO}
    return settings


def mensagem(texto, numero=1):
    up = _msg(texto, numero, chat=GRUPO)
    up["message"]["chat"]["type"] = "supergroup"
    up["message"]["from"] = {"id": 999}
    return up


def clique(dado, numero=1):
    up = _clique(dado, numero, chat=GRUPO)
    up["callback_query"]["message"]["chat"]["type"] = "supergroup"
    up["callback_query"]["from"] = {"id": 999}
    return up


def test_consultas_respondem_no_grupo_e_chat_privado_permanece(grupo, carteira):
    tg = TelegramFalso([
        mensagem("/carteira@InvestAlertasBot", 1),
        mensagem("/alertas@InvestAlertasBot", 2),
        mensagem("/preco@InvestAlertasBot BTC", 3),
        mensagem("/ajuda@InvestAlertasBot", 4),
        clique("carteira", 5),
        clique("alertas", 6),
        _msg("/ajuda", 7),
    ])
    assert telegram_bot.processar_updates(client=tg.client) == 7
    assert [m["chat_id"] for m in tg.enviados] == [GRUPO] * 6 + ["42"]
    assert tg.enviados[0]["text"].startswith("📊 Resumo da carteira")
    assert tg.enviados[2]["text"].startswith("BTC:")
    assert "somente consulta" in tg.enviados[3]["text"]
    assert "/alerta " not in tg.enviados[3]["text"]
    assert "/alerta " in tg.enviados[-1]["text"]


@pytest.mark.parametrize("acao", ["visto", "pausar", "ajustar", "limite"])
def test_callback_do_grupo_nao_muda_alerta_ou_regra(grupo, carteira, usuario, acao):
    asset = carteira["ttwo"]
    rule = regra(usuario, asset, classification="aviso")
    cotar(asset, "80", T0)
    alert = services.avaliar_ativo(asset, T0)[0]
    pk = alert.pk if acao == "visto" else rule.pk
    dado = f"{acao}:{pk}" + (":0.8" if acao == "limite" else "")
    tg = TelegramFalso([clique(dado)])
    version_count = RuleVersion.objects.count()
    assert telegram_bot.processar_updates(client=tg.client) == 1
    rule.refresh_from_db()
    alert.refresh_from_db()
    assert rule.is_active and rule.current_version == 1
    assert rule.versao_atual.threshold == Decimal("10")
    assert RuleVersion.objects.count() == version_count
    assert alert.seen_at is None and alert.resolved_at is None
    assert tg.callbacks_respondidos[0]["text"] == "Somente consulta"
    assert tg.enviados[0]["chat_id"] == GRUPO


def test_criacao_de_regra_bloqueada_no_grupo_mantida_no_privado(grupo, carteira):
    tg = TelegramFalso([mensagem("/alerta@InvestAlertasBot BTC cair 5% no dia")])
    telegram_bot.processar_updates(client=tg.client)
    assert not Rule.objects.exists() and not RuleVersion.objects.exists()
    assert "somente consultas" in tg.enviados[0]["text"]
    tg = TelegramFalso([_msg("/alerta BTC cair 5% no dia", 2)])
    telegram_bot.processar_updates(client=tg.client)
    assert Rule.objects.count() == 1 and RuleVersion.objects.count() == 1


def test_outros_chats_e_tipo_privado_do_grupo_nao_recebem_dados(grupo, carteira):
    tg = TelegramFalso([
        _msg("/carteira", 1, chat="999"),
        _clique("carteira", 2, chat="999"),
        _msg("/carteira", 3, chat=GRUPO),  # sem tipo válido de grupo
        mensagem("conversa comum", 4),
    ])
    assert telegram_bot.processar_updates(client=tg.client) == 0
    assert tg.enviados == [] and tg.callbacks_respondidos == []


@pytest.mark.parametrize("valor", ["", "999", "42", "-0", "-1x", "--123"])
def test_configuracao_invalida_nao_autoriza_outro_chat(valor):
    assert telegram_bot.grupo_consulta({**CFG, "TELEGRAM_GRUPO_CHAT_ID": valor}) == ""


def test_avisos_vao_ao_grupo_com_botoes_so_de_consulta(grupo, carteira, usuario):
    asset = carteira["ttwo"]
    regra(usuario, asset, classification="aviso")
    cotar(asset, "80", T0)
    alert = services.avaliar_ativo(asset, T0)[0]
    notify.enfileirar([alert])
    tg = TelegramFalso()
    assert notify.enviar_pendentes(client=tg.client, agora=T0) == 1
    assert tg.enviados[0]["chat_id"] == GRUPO
    botoes = [b["callback_data"] for row in tg.enviados[0]["reply_markup"]["inline_keyboard"] for b in row]
    assert botoes == ["carteira", "alertas"]
    assert Alert.objects.get(pk=alert.pk).seen_at is None
    assert Notification.objects.get(channel="telegram").status == "enviada"


def test_resumo_e_avisos_operacionais_usam_mesmo_grupo(grupo, carteira):
    notify.enfileirar_texto("resumo", "Resumo de teste", "Dados simulados", canais=["telegram"])
    notify.enfileirar_texto("operacao", "Operação de teste", "Dados simulados", canais=["telegram"])
    tg = TelegramFalso()
    assert notify.enviar_pendentes(client=tg.client, agora=T0) == 2
    assert [m["chat_id"] for m in tg.enviados] == [GRUPO, GRUPO]
