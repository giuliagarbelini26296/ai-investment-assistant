"""
Bot do Telegram de mão dupla, sem expor o portal na internet.

- O coletor chama `processar_updates()` a cada passagem (long polling curto em `getUpdates`); o offset
  fica em ScheduleState(task="TELEGRAM").last_slot.
- O chat particular configurado em TELEGRAM_CHAT_ID mantém os controles. O grupo opcional
  TELEGRAM_GRUPO_CHAT_ID permite somente consultas; outros chats são ignorados.
- Botões nos avisos de alerta: ✓ Visto · 🔕 Pausar regra · ✏️ Ajustar limite (−20/−10/+10/+20 %) · 📊 Carteira.
- Comandos: /carteira (= /resumo), /alertas, /preco SÍMBOLO, /alerta <frase>, /ajuda.
  "/alerta BTC cair 5% no dia" usa a mesma tradução do formulário em linguagem simples.

Toda resposta com preço informa fonte/atualidade; nada é estimado. Toda criação de regra devolve a
descrição exata gravada, com o texto obrigatório de não recomendação.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from decimal import Decimal

import httpx
from django.conf import settings
from django.utils import timezone

from apps.marketdata import freshness
from apps.portfolio.models import Asset
from apps.ui.templatetags.formatos import moeda, normalize_decimal, pct

from . import services
from .forms import AlertaForm
from .models import TEXTO_NAO_RECOMENDACAO, Alert, Rule

log = logging.getLogger("telegram")

TIMEOUT = 10.0
TELEGRAM_API = "https://api.telegram.org"
TAREFA = "TELEGRAM"
AJUSTES = (
    ("−20%", Decimal("0.8")),
    ("−10%", Decimal("0.9")),
    ("+10%", Decimal("1.1")),
    ("+20%", Decimal("1.2")),
)

AJUDA = (
    "Comandos:\n"
    "/carteira — resumo da carteira agora\n"
    "/alertas — alertas ativos\n"
    "/preco BTC — último preço válido e atualidade\n"
    "/alerta BTC cair 5% no dia — cria uma regra (também: subir 10% desde a compra; abaixo de US$ 100000)\n"
    "/ajuda — esta lista\n"
    "Nos avisos, use os botões: Visto, Pausar regra, Ajustar limite, Carteira.\n" + TEXTO_NAO_RECOMENDACAO
)

AJUDA_CONSULTA = (
    "Comandos do grupo (somente consulta):\n"
    "/carteira — resumo da carteira agora\n"
    "/alertas — alertas ativos\n"
    "/preco BTC — último preço válido e atualidade\n"
    "/ajuda — esta lista\n"
    "Para garantir a resposta no grupo, use /carteira@InvestAlertasBot, "
    "/alertas@InvestAlertasBot ou /preco@InvestAlertasBot BTC.\n"
    "As regras são administradas no chat particular da titular.\n" + TEXTO_NAO_RECOMENDACAO
)
AVISO_CONSULTA = (
    "Este grupo permite somente consultas. As regras são administradas no chat particular da titular."
)


def grupo_consulta(cfg: dict | None = None) -> str:
    """Aceita apenas um ID numérico negativo de grupo, distinto do chat particular."""
    cfg = settings.NOTIFICACOES if cfg is None else cfg
    grupo = str(cfg.get("TELEGRAM_GRUPO_CHAT_ID") or "").strip()
    if re.fullmatch(r"-[1-9][0-9]*", grupo) and grupo != str(cfg.get("TELEGRAM_CHAT_ID", "")):
        return grupo
    return ""


# --- API ----------------------------------------------------------------------------------------------


class TelegramApi:
    def __init__(self, client: httpx.Client | None = None):
        self.cfg = settings.NOTIFICACOES
        self.client = client or httpx.Client(timeout=TIMEOUT)

    @property
    def ligado(self) -> bool:
        return bool(self.cfg.get("TELEGRAM_BOT_TOKEN") and self.cfg.get("TELEGRAM_CHAT_ID"))

    def chamar(self, metodo: str, **params) -> dict:
        r = self.client.post(f"{TELEGRAM_API}/bot{self.cfg['TELEGRAM_BOT_TOKEN']}/{metodo}", json=params)
        if r.status_code != 200:
            raise RuntimeError(f"Telegram {metodo}: HTTP {r.status_code}")
        dados = r.json()
        if not dados.get("ok"):
            raise RuntimeError(f"Telegram {metodo}: {str(dados.get('description', 'erro'))[:120]}")
        return dados.get("result", {})

    def enviar(
        self, texto: str, teclado: list[list[dict]] | None = None, *, chat_id: str | None = None
    ) -> dict:
        params = {
            "chat_id": self.cfg["TELEGRAM_CHAT_ID"] if chat_id is None else chat_id,
            "text": texto,
            "disable_web_page_preview": True,
        }
        if teclado:
            params["reply_markup"] = {"inline_keyboard": teclado}
        return self.chamar("sendMessage", **params)


def teclado_alerta(alerta: Alert) -> list[list[dict]]:
    segunda = [{"text": "📊 Carteira", "callback_data": "carteira"}]
    if alerta.version.family != "mercado":  # sinais de mercado têm limites em sessões/dias: ajuste no portal
        segunda.insert(0, {"text": "✏️ Ajustar limite", "callback_data": f"ajustar:{alerta.version.rule_id}"})
    return [
        [
            {"text": "✓ Visto", "callback_data": f"visto:{alerta.pk}"},
            {"text": "🔕 Pausar regra", "callback_data": f"pausar:{alerta.version.rule_id}"},
        ],
        segunda,
    ]


def teclado_resumo() -> list[list[dict]]:
    return [
        [
            {"text": "📊 Carteira", "callback_data": "carteira"},
            {"text": "⚠️ Alertas", "callback_data": "alertas"},
        ]
    ]


# --- Comandos -----------------------------------------------------------------------------------------


def _resumo_texto() -> str:
    from . import resumo_diario

    titulo, corpo = resumo_diario.texto_resumo()
    return f"📊 {titulo}\n{corpo}"


def _alertas_texto() -> str:
    ativos = list(services.alertas_ativos())
    if not ativos:
        return "Nenhum alerta ativo."
    linhas = [f"⚠️ Alertas ativos ({len(ativos)}):"]
    for a in ativos[:15]:
        o = a.observed or {}
        visto = "" if a.seen_at is None else " (visto)"
        linhas.append(
            f"• {a.asset.symbol}: {a.version.descricao} — preço "
            f"{moeda(o.get('preco'), o.get('moeda_cotacao', a.asset.native_currency))} "
            f"em {timezone.localtime(a.triggered_at):%d/%m %H:%M}{visto}"
        )
    linhas.append(TEXTO_NAO_RECOMENDACAO)
    return "\n".join(linhas)


def _preco_texto(simbolo: str) -> str:
    asset = Asset.objects.filter(symbol__iexact=simbolo.strip(), is_active=True).first()
    if asset is None:
        return f"Ativo {simbolo.upper()} não cadastrado."
    atual = freshness.atualidade(asset, timezone.now())
    q = atual.cotacao
    if q is None:
        return f"{asset.symbol}: sem cotação ({atual.descricao})."
    var = f" · dia/24 h {pct(q.change_24h_pct)}" if q.change_24h_pct is not None else ""
    quando = q.market_time or q.collected_at
    return (
        f"{asset.symbol}: {moeda(q.price, q.currency)}{var}\n"
        f"Fonte {q.source} · {atual.rotulo} · {timezone.localtime(quando):%d/%m %H:%M} — {atual.descricao}"
    )


_RE_PCT = re.compile(
    r"^(?P<sym>[A-Za-z0-9.\-]+)?\s*(?P<verbo>cair|subir|queda|alta)\s*(?:de\s*)?(?P<v>[\d.,]+)\s*%?\s*"
    r"(?P<onde>no dia|24 ?h|hoje|desde a compra|em rela[cç][aã]o ao que paguei|desde que comprei)?\s*$",
    re.IGNORECASE,
)
_RE_ABS = re.compile(
    r"^(?P<sym>[A-Za-z0-9.\-]+)?\s*(?P<cond>abaixo|acima)\s*(?:de\s*)?(?P<moeda>US\$|R\$|USD|BRL)?\s*(?P<v>[\d.,]+)\s*$",
    re.IGNORECASE,
)


def interpretar_alerta(frase: str) -> dict | None:
    """Frase → dados do AlertaForm, ex.: "BTC cair 5% no dia", "TTWO abaixo de US$ 150"."""
    frase = frase.strip()
    m = _RE_PCT.match(frase)
    if m:
        verbo = m.group("verbo").lower()
        onde = (m.group("onde") or "no dia").lower()
        desde = "compra" in onde or "paguei" in onde or "comprei" in onde
        tipo = ("queda" if verbo in ("cair", "queda") else "alta") + ("_compra" if desde else "_dia")
        return {"sym": m.group("sym"), "tipo": tipo, "valor": m.group("v"), "currency": "USD"}
    m = _RE_ABS.match(frase)
    if m:
        moeda_txt = (m.group("moeda") or "").upper().replace("US$", "USD").replace("R$", "BRL")
        return {
            "sym": m.group("sym"),
            "tipo": m.group("cond").lower(),
            "valor": m.group("v"),
            "currency": moeda_txt,
        }
    return None


def _criar_alerta_texto(frase: str, user=None) -> str:
    dados = interpretar_alerta(frase)
    if dados is None:
        return (
            "Não entendi. Exemplos: /alerta BTC cair 5% no dia · /alerta TTWO subir 10% desde a compra · "
            "/alerta BTC abaixo de US$ 100000"
        )
    asset = None
    if dados["sym"]:
        asset = Asset.objects.filter(symbol__iexact=dados["sym"], is_active=True).first()
        if asset is None:
            return f"Ativo {dados['sym'].upper()} não cadastrado. Para regra global, omita o símbolo."
    currency = dados["currency"] or (asset.native_currency if asset else "USD")
    form = AlertaForm(
        {
            "asset": asset.pk if asset else "",
            "tipo": dados["tipo"],
            "valor": dados["valor"],
            "currency": currency,
            "classification": "aviso",
        }
    )
    if not form.is_valid():
        return "Regra inválida: " + "; ".join(f"{k}: {', '.join(v)}" for k, v in form.errors.items())
    rule = services.criar_regra(
        user,
        name=form.nome(),
        asset=form.cleaned_data["asset"],
        reason="criada pelo Telegram",
        **form.parametros(),
    )
    alvo = rule.asset.symbol if rule.asset_id else "todos os ativos"
    return (
        f"✅ Regra criada: {rule.name}\n{alvo}: {rule.versao_atual.descricao}. Avaliada a cada coleta.\n"
        + TEXTO_NAO_RECOMENDACAO
    )


def responder_comando(
    texto: str, user=None, *, somente_leitura: bool = False
) -> tuple[str, list[list[dict]] | None]:
    partes = texto.strip().split(maxsplit=1)
    if not partes:
        return AJUDA_CONSULTA if somente_leitura else AJUDA, teclado_resumo()
    cmd = partes[0].lower().split("@")[0]
    arg = partes[1] if len(partes) > 1 else ""
    if cmd in ("/carteira", "/resumo"):
        return _resumo_texto(), teclado_resumo()
    if cmd == "/alertas":
        return _alertas_texto(), None
    if cmd == "/preco":
        return (_preco_texto(arg) if arg else "Use: /preco BTC"), None
    if cmd == "/alerta":
        if somente_leitura:
            return AVISO_CONSULTA, None
        return (_criar_alerta_texto(arg, user) if arg else "Use: /alerta BTC cair 5% no dia"), None
    if cmd in ("/ajuda", "/help", "/start"):
        return AJUDA_CONSULTA if somente_leitura else AJUDA, teclado_resumo()
    return "Comando desconhecido. /ajuda lista os comandos.", None


# --- Botões (callback_query) -------------------------------------------------------------------------


def responder_callback(
    dado: str, agora: dt.datetime | None = None, *, somente_leitura: bool = False
) -> tuple[str, list[list[dict]] | None, str]:
    """Devolve (texto de resposta, teclado opcional, aviso curto para o toast do Telegram)."""
    agora = agora or timezone.now()
    acao, _, resto = dado.partition(":")
    if somente_leitura and acao not in {"carteira", "alertas"}:
        return AVISO_CONSULTA, None, "Somente consulta"
    if acao == "carteira":
        return _resumo_texto(), teclado_resumo(), "Resumo enviado"
    if acao == "alertas":
        return _alertas_texto(), None, "Alertas"
    if acao == "visto" and resto.isdigit():
        n = services.marcar_vistos([int(resto)], agora)
        return (
            (
                "Alerta marcado como visto (continua ativo até a condição deixar de valer)."
                if n
                else "Alerta já estava visto."
            ),
            None,
            "Visto",
        )
    if acao == "pausar" and resto.isdigit():
        rule = Rule.objects.filter(pk=int(resto)).first()
        if rule is None:
            return "Regra não encontrada.", None, "Erro"
        if rule.is_active:
            services.desativar_regra(rule)
        return f"🔕 Regra pausada: {rule.name}. Reative no portal em Alertas → Regras.", None, "Regra pausada"
    if acao == "ajustar" and resto.isdigit():
        rule = Rule.objects.filter(pk=int(resto), is_active=True).first()
        if rule is None or rule.versao_atual is None:
            return "Regra não encontrada ou pausada.", None, "Erro"
        v = rule.versao_atual
        atual = normalize_decimal(v.threshold)

        def botao(rotulo, fator):
            novo = normalize_decimal((v.threshold * fator).quantize(Decimal("0.01")))
            return {"text": f"{rotulo} → {novo}", "callback_data": f"limite:{rule.pk}:{fator}"}

        teclado = [[botao(r, f) for r, f in AJUSTES[:2]], [botao(r, f) for r, f in AJUSTES[2:]]]
        sufixo = "" if v.family == "preco_absoluto" else "%"
        return f'Limite atual de "{rule.name}": {atual}{sufixo}. Novo limite:', teclado, "Escolha o ajuste"
    if acao == "limite":
        pk, _, fator = resto.partition(":")
        rule = Rule.objects.filter(pk=int(pk), is_active=True).first() if pk.isdigit() else None
        try:
            fator_d = Decimal(fator)
        except Exception:  # noqa: BLE001
            fator_d = None
        if rule is None or rule.versao_atual is None or fator_d is None or fator_d <= 0:
            return "Ajuste inválido.", None, "Erro"
        v = rule.versao_atual
        novo = (v.threshold * fator_d).quantize(Decimal("0.01"))
        if v.family != "preco_absoluto" and v.condition == "queda" and novo > 100:
            return "Uma queda não pode passar de 100%.", None, "Erro"
        from django.core.exceptions import ValidationError

        try:
            nova = services.nova_versao(
                rule,
                None,
                reason="ajuste pelo Telegram",
                family=v.family,
                condition=v.condition,
                threshold=novo,
                currency=v.currency,
                classification=v.classification,
            )
        except ValidationError as exc:
            return " ".join(exc.messages), None, "Erro"
        texto = f"✏️ Nova versão v{nova.number}: {rule.name} → {nova.descricao}. Estado reiniciado."
        return f"{texto}\n{TEXTO_NAO_RECOMENDACAO}", None, "Limite ajustado"
    return "Ação desconhecida.", None, "Erro"


# --- Laço -----------------------------------------------------------------------------------------------


def processar_updates(
    client: httpx.Client | None = None, agora: dt.datetime | None = None, timeout_s: int = 0
) -> int:
    """Lê e responde as mensagens/cliques novos. Devolve quantos updates foram tratados."""
    from apps.marketdata.collector import estado_tarefa

    api = TelegramApi(client)
    if not api.ligado:
        return 0
    estado = estado_tarefa(TAREFA)
    offset = int(estado.last_slot) if estado.last_slot.isdigit() else None
    params = {"timeout": timeout_s, "allowed_updates": ["message", "callback_query"]}
    if offset is not None:
        params["offset"] = offset
    updates = api.chamar("getUpdates", **params)
    tratados = 0
    chat_ok = str(api.cfg["TELEGRAM_CHAT_ID"])
    chat_grupo = grupo_consulta(api.cfg)
    for up in updates:
        estado.last_slot = str(up["update_id"] + 1)
        try:
            if "callback_query" in up:
                cq = up["callback_query"]
                info_chat = cq.get("message", {}).get("chat", {})
                chat = str(info_chat.get("id", ""))
                consulta = bool(
                    chat_grupo and chat == chat_grupo and info_chat.get("type") in {"group", "supergroup"}
                )
                if chat != chat_ok and not consulta:
                    log.warning("callback de chat não autorizado ignorado")
                    continue
                texto, teclado, aviso = responder_callback(
                    cq.get("data", ""), agora, somente_leitura=consulta
                )
                api.chamar("answerCallbackQuery", callback_query_id=cq["id"], text=aviso[:200])
                api.enviar(texto, teclado, chat_id=chat)
            elif "message" in up:
                msg = up["message"]
                info_chat = msg.get("chat", {})
                chat = str(info_chat.get("id", ""))
                consulta = bool(
                    chat_grupo and chat == chat_grupo and info_chat.get("type") in {"group", "supergroup"}
                )
                if chat != chat_ok and not consulta:
                    if (
                        info_chat.get("type") in {"group", "supergroup"}
                        and str(msg.get("from", {}).get("id", "")) == chat_ok
                    ):
                        log.warning(
                            "grupo ainda não autorizado: chat_id=%s; comando recebido da titular", chat
                        )
                    else:
                        log.warning("mensagem de chat não autorizado ignorada")
                    continue
                texto_msg = (msg.get("text") or "").strip()
                if not texto_msg:
                    continue
                if not texto_msg.startswith("/"):
                    if consulta:
                        continue
                    texto_msg = "/ajuda"
                texto, teclado = responder_comando(texto_msg, somente_leitura=consulta)
                api.enviar(texto, teclado, chat_id=chat)
            tratados += 1
        except (httpx.HTTPError, RuntimeError, ValueError, KeyError) as exc:
            log.warning("falha ao tratar update do Telegram: %s", str(exc)[:200])
    estado.last_attempt_at = agora or timezone.now()
    estado.last_status = "completo"
    estado.save(update_fields=["last_slot", "last_attempt_at", "last_status", "updated_at"])
    return tratados
