"""Bot do Telegram de mão dupla: botões, comandos, autorização por chat e offset (tudo simulado)."""

import datetime as dt
import json
from decimal import Decimal

import httpx
import pytest

from apps.alerts import notify, telegram_bot
from apps.alerts import services as alerts
from apps.alerts.models import TEXTO_NAO_RECOMENDACAO, Alert, Rule
from apps.marketdata.models import ScheduleState

from .test_alerts import T0, cotar, regra
from .test_rc3 import CFG, carteira  # noqa: F401 - fixture

D = Decimal


class TelegramFalso:
    """Simula a Bot API: guarda os envios e devolve os updates programados."""

    def __init__(self, updates=None):
        self.updates = list(updates or [])
        self.enviados = []
        self.callbacks_respondidos = []
        self.chamadas = []
        self.client = httpx.Client(transport=httpx.MockTransport(self.handler))

    def handler(self, request: httpx.Request) -> httpx.Response:
        metodo = request.url.path.rsplit("/", 1)[-1]
        corpo = json.loads(request.content or b"{}")
        self.chamadas.append((metodo, corpo))
        if metodo == "getUpdates":
            return httpx.Response(200, json={"ok": True, "result": self.updates})
        if metodo == "sendMessage":
            self.enviados.append(corpo)
            return httpx.Response(200, json={"ok": True, "result": {"message_id": len(self.enviados)}})
        if metodo == "answerCallbackQuery":
            self.callbacks_respondidos.append(corpo)
            return httpx.Response(200, json={"ok": True, "result": True})
        return httpx.Response(400, json={"ok": False, "description": "método desconhecido"})


def _msg(texto, update_id=1, chat="42"):
    return {"update_id": update_id, "message": {"chat": {"id": int(chat)}, "text": texto}}


def _clique(dado, update_id=1, chat="42"):
    return {
        "update_id": update_id,
        "callback_query": {"id": f"cq{update_id}", "data": dado, "message": {"chat": {"id": int(chat)}}},
    }


@pytest.fixture
def ligado(settings):
    settings.NOTIFICACOES = dict(CFG)
    return settings


def test_desligado_nao_consulta(settings, carteira):
    settings.NOTIFICACOES = {**CFG, "TELEGRAM_BOT_TOKEN": ""}
    tg = TelegramFalso([_msg("/carteira")])
    assert telegram_bot.processar_updates(client=tg.client) == 0 and tg.chamadas == []


def test_comandos_e_offset(ligado, carteira):
    tg = TelegramFalso(
        [
            _msg("/carteira", 10),
            _msg("/alertas", 11),
            _msg("/preco btc", 12),
            _msg("oi", 13),
            _msg("/xpto", 14),
        ]
    )
    assert telegram_bot.processar_updates(client=tg.client) == 5
    textos = [e["text"] for e in tg.enviados]
    assert textos[0].startswith("📊 Resumo da carteira") and "BRL: patrimônio" in textos[0]
    assert "inline_keyboard" in tg.enviados[0]["reply_markup"]
    assert textos[1] == "Nenhum alerta ativo."
    assert textos[2].startswith("BTC: USDT 66.000,00") and "binance" in textos[2] and "+10,00%" in textos[2]
    assert "Comandos:" in textos[3]  # texto livre → ajuda
    assert "desconhecido" in textos[4]
    assert ScheduleState.objects.get(task="TELEGRAM").last_slot == "15"
    # próxima leitura usa o offset
    tg2 = TelegramFalso([])
    telegram_bot.processar_updates(client=tg2.client)
    assert tg2.chamadas[0][1]["offset"] == 15


def test_chat_nao_autorizado_e_ignorado(ligado, carteira):
    tg = TelegramFalso([_msg("/carteira", 1, chat="999"), _clique("carteira", 2, chat="999")])
    assert telegram_bot.processar_updates(client=tg.client) == 0
    assert tg.enviados == [] and tg.callbacks_respondidos == []
    assert ScheduleState.objects.get(task="TELEGRAM").last_slot == "3"  # offset avança mesmo assim


def test_preco_sem_ativo_ou_sem_cotacao(ligado, carteira):
    assert "não cadastrado" in telegram_bot._preco_texto("ZZZ")
    from apps.marketdata.models import Quote

    Quote.objects.filter(asset__symbol="TTWO").delete()
    assert "sem cotação" in telegram_bot._preco_texto("TTWO")


@pytest.mark.parametrize(
    "frase, tipo, valor, sym, moeda",
    [
        ("BTC cair 5% no dia", "queda_dia", "5", "BTC", "USD"),
        ("btc subir 10 % desde a compra", "alta_compra", "10", "btc", "USD"),
        ("TTWO queda de 7,5% em relação ao que paguei", "queda_compra", "7,5", "TTWO", "USD"),
        ("cair 3%", "queda_dia", "3", None, "USD"),
        ("BTC abaixo de US$ 100000", "abaixo", "100000", "BTC", "USD"),
        ("TTWO acima de R$ 1.000,50", "acima", "1.000,50", "TTWO", "BRL"),
        ("abaixo 90", "abaixo", "90", None, ""),
    ],
)
def test_interpretar_alerta(frase, tipo, valor, sym, moeda):
    d = telegram_bot.interpretar_alerta(frase)
    assert d == {"sym": sym, "tipo": tipo, "valor": valor, "currency": moeda}


def test_interpretar_alerta_invalido():
    assert telegram_bot.interpretar_alerta("compra tudo") is None


def test_comando_alerta_cria_regra(ligado, carteira):
    tg = TelegramFalso(
        [
            _msg("/alerta BTC cair 5% no dia", 1),
            _msg("/alerta ZZZ cair 5%", 2),
            _msg("/alerta cair 150%", 3),
            _msg("/alerta", 4),
        ]
    )
    assert telegram_bot.processar_updates(client=tg.client) == 4
    textos = [e["text"] for e in tg.enviados]
    assert (
        textos[0].startswith("✅ Regra criada: BTC: queda de 5% no dia")
        and TEXTO_NAO_RECOMENDACAO in textos[0]
    )
    rule = Rule.objects.get()
    assert rule.asset.symbol == "BTC" and rule.versao_atual.descricao == "queda ≥ 5% no dia/24 h"
    assert rule.versao_atual.reason == "criada pelo Telegram" and rule.versao_atual.classification == "aviso"
    assert "não cadastrado" in textos[1]
    assert "Regra inválida" in textos[2] and "100%" in textos[2]
    assert textos[3].startswith("Use:")


def test_botoes_visto_pausar_ajustar(ligado, carteira, usuario):
    ttwo = carteira["ttwo"]
    rule = regra(usuario, ttwo, classification="aviso")  # queda ≥ 10% desde a compra; preço médio 100
    cotar(ttwo, "80", T0)
    alerta = alerts.avaliar_ativo(ttwo, T0)[0]
    # aviso do alerta leva botões
    notify.enfileirar([alerta])
    tg0 = TelegramFalso()
    assert notify.enviar_pendentes(client=tg0.client, agora=T0) == 1
    botoes = [
        b["callback_data"] for linha in tg0.enviados[0]["reply_markup"]["inline_keyboard"] for b in linha
    ]
    assert botoes == [f"visto:{alerta.pk}", f"pausar:{rule.pk}", f"ajustar:{rule.pk}", "carteira"]
    # visto
    tg = TelegramFalso([_clique(f"visto:{alerta.pk}", 1)])
    assert telegram_bot.processar_updates(client=tg.client) == 1
    alerta.refresh_from_db()
    assert alerta.seen_at is not None and alerta.is_active
    assert tg.callbacks_respondidos[0]["text"] == "Visto" and "visto" in tg.enviados[0]["text"]
    # ajustar → teclado com 4 opções; escolher −20% → 8%
    tg = TelegramFalso([_clique(f"ajustar:{rule.pk}", 2)])
    telegram_bot.processar_updates(client=tg.client)
    opcoes = [b for linha in tg.enviados[0]["reply_markup"]["inline_keyboard"] for b in linha]
    assert (
        len(opcoes) == 4
        and opcoes[0]["text"] == "−20% → 8"
        and opcoes[0]["callback_data"] == f"limite:{rule.pk}:0.8"
    )
    tg = TelegramFalso([_clique(f"limite:{rule.pk}:0.8", 3)])
    telegram_bot.processar_updates(client=tg.client)
    rule.refresh_from_db()
    assert rule.current_version == 2 and rule.versao_atual.threshold == D("8.00")
    assert rule.versao_atual.reason == "ajuste pelo Telegram"
    assert "v2" in tg.enviados[0]["text"] and "queda ≥ 8% desde a compra" in tg.enviados[0]["text"]
    assert Alert.objects.get(pk=alerta.pk).resolved_at is not None  # nova versão resolve o alerta anterior
    # pausar
    tg = TelegramFalso([_clique(f"pausar:{rule.pk}", 4)])
    telegram_bot.processar_updates(client=tg.client)
    rule.refresh_from_db()
    assert not rule.is_active and "pausada" in tg.enviados[0]["text"]
    # ações inválidas não quebram
    tg = TelegramFalso([_clique("limite:abc:0", 5), _clique("xpto", 6), _clique(f"ajustar:{rule.pk}", 7)])
    assert telegram_bot.processar_updates(client=tg.client) == 3
    assert [c["text"] for c in tg.callbacks_respondidos] == ["Erro", "Erro", "Erro"]


def test_falha_da_api_nao_derruba_e_nao_avanca_offset_da_leitura(ligado, carteira):
    def handler(request):
        return httpx.Response(500, json={"ok": False})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(RuntimeError):
        telegram_bot.processar_updates(client=client)
    assert not ScheduleState.objects.filter(task="TELEGRAM", last_slot__gt="").exists()


def test_rotina_do_coletor_chama_o_bot(ligado, carteira, monkeypatch):
    from apps.marketdata import collector

    chamadas = []
    monkeypatch.setattr(telegram_bot, "processar_updates", lambda **kw: chamadas.append(kw) or 0)
    collector.executar_tarefas_devidas(dt.datetime(2026, 10, 31, 3, 0, tzinfo=dt.UTC), dormir=lambda s: None)
    assert chamadas
