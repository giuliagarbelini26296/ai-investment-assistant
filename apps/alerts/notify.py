"""
Notificações de alertas fora do portal.

Modelo "caixa de saída": ao disparar, cada alerta gera uma Notification pendente por canal ligado.
O coletor chama `enviar_pendentes()` ao fim de cada ciclo; falhas ficam registradas e são retentadas
(até MAX_TENTATIVAS). A restrição única (alerta × canal) impede envio duplicado.

Canais:
- telegram: Bot API (`sendMessage`); exige TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID no ambiente.
- mac: não envia nada daqui — o script `scripts/mac/portal-notificar.sh`, rodando no Mac, consulta o
  feed autenticado (/alertas/feed/) e exibe a notificação; a Notification registra a entrega quando o
  script confirma (`/alertas/feed/confirmar/`).

Toda mensagem traz o texto obrigatório de não recomendação, a fonte e o horário do preço.
"""

from __future__ import annotations

import datetime as dt
import logging

import httpx
from django.conf import settings
from django.utils import timezone
from django.utils.formats import date_format

from apps.ui.templatetags.formatos import moeda, normalize_decimal, pct

from .models import (
    TEXTO_NAO_RECOMENDACAO,
    Alert,
    Channel,
    Classification,
    Family,
    Notification,
    NotificationKind,
    NotificationStatus,
)

log = logging.getLogger("notificacoes")

MAX_TENTATIVAS = 5
TIMEOUT = 10.0
TELEGRAM_API = "https://api.telegram.org"


def canais_ligados() -> list[str]:
    cfg = settings.NOTIFICACOES
    canais = []
    if cfg.get("TELEGRAM_BOT_TOKEN") and cfg.get("TELEGRAM_CHAT_ID"):
        canais.append(Channel.TELEGRAM)
    if cfg.get("FEED_TOKEN"):
        canais.append(Channel.MAC)
    return canais


# Anti-repetição: a mesma regra no mesmo ativo não gera nova mensagem antes deste intervalo
# (o alerta continua registrado no portal; só a notificação externa é poupada).
CARENCIA_NOTIFICACAO = dt.timedelta(hours=6)


def _repetido(alerta: Alert) -> bool:
    return (
        Notification.objects.filter(
            alert__asset=alerta.asset,
            alert__version__rule=alerta.version.rule,
            alert__triggered_at__gte=alerta.triggered_at - CARENCIA_NOTIFICACAO,
        )
        .exclude(alert=alerta)
        .exists()
    )


def enfileirar(alertas: list[Alert]) -> list[Notification]:
    """Cria as notificações pendentes dos alertas disparados (uma por canal ligado)."""
    criadas = []
    for alerta in alertas:
        if _repetido(alerta):
            alerta.observed = {
                **(alerta.observed or {}),
                "notificacao": "não reenviada: repetição em menos de 6 h",
            }
            alerta.save(update_fields=["observed"])
            continue
        for canal in canais_ligados():
            n, nova = Notification.objects.get_or_create(alert=alerta, channel=canal)
            if nova:
                criadas.append(n)
    return criadas


# --- Texto --------------------------------------------------------------------------------------------


def _hora_local(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        t = dt.datetime.fromisoformat(iso)
    except ValueError:
        return iso
    if timezone.is_naive(t):
        t = timezone.make_aware(t, dt.UTC)
    return date_format(timezone.localtime(t), "d/m H:i")


def titulo(alerta: Alert) -> str:
    return f"{alerta.asset.symbol}: {alerta.version.descricao}"


def corpo(alerta: Alert) -> str:
    """Resumo em linhas: preço observado, referência da regra, fonte/horário e texto obrigatório."""
    o = alerta.observed or {}
    linhas = [f"Preço: {moeda(o.get('preco'), o.get('moeda_cotacao', alerta.asset.native_currency))}"]
    fam = alerta.version.family
    if fam == Family.SINCE_PURCHASE and o.get("retorno_pct") is not None:
        linhas.append(
            f"Desde a compra: {pct(o['retorno_pct'])} (preço médio "
            f"{moeda(o.get('preco_medio'), alerta.asset.native_currency)})"
        )
    elif fam == Family.DAY and o.get("variacao_pct") is not None:
        linhas.append(f"Variação no dia/24 h: {pct(o['variacao_pct'])}")
    elif fam == Family.MARKET:
        linhas.extend(_linhas_mercado(alerta, o))
    limite = normalize_decimal(alerta.limite)
    if fam == Family.MARKET:
        linhas.append(f"Regra: {alerta.version.descricao}")
    else:
        linhas.append(f"Limite da regra: {limite}{'' if fam == Family.ABSOLUTE else '%'}")
    if alerta.classification != Classification.NOTICE:
        linhas.append(f"Seu rótulo: {alerta.get_classification_display()}")
    fonte = o.get("fonte", "—")
    tipo = o.get("tipo_cotacao", "")
    quando = _hora_local(o.get("horario_mercado") or o.get("horario_coleta"))
    linhas.append(f"Fonte: {fonte} ({tipo}) · preço de {quando}")
    linhas.append(TEXTO_NAO_RECOMENDACAO)
    return "\n".join(linhas)


def _linhas_mercado(alerta: Alert, o: dict) -> list[str]:
    moeda_ativo = o.get("moeda_cotacao", alerta.asset.native_currency)
    if o.get("minima_52s"):
        return [
            f"Mínima de 52 semanas: {moeda(o['minima_52s'], moeda_ativo)} "
            f"({pct(o.get('distancia_pct'))} acima)"
        ]
    if o.get("suporte"):
        return [f"Suporte ({o.get('sessoes')} sessões): {moeda(o['suporte'], moeda_ativo)}"]
    if o.get("resistencia"):
        return [f"Resistência ({o.get('sessoes')} sessões): {moeda(o['resistencia'], moeda_ativo)}"]
    if o.get("data_resultado"):
        return [
            f"Resultado previsto para {o['data_resultado'][8:10]}/{o['data_resultado'][5:7]} "
            f"({o.get('dias')} dia(s))"
        ]
    if o.get("mudanca_pp"):
        return [
            f"Recomendações de compra: {o.get('pct_compra_anterior')}% → {o.get('pct_compra')}% "
            f"({o['mudanca_pp']} p.p.; opinião de analistas, fonte {o.get('fonte')})"
        ]
    return []


def link(alerta: Alert | None = None) -> str:
    return f"{settings.NOTIFICACOES['URL_PORTAL']}/alertas/"


def titulo_de(n: Notification) -> str:
    return titulo(n.alert) if n.alert_id else n.title


def corpo_de(n: Notification) -> str:
    return corpo(n.alert) if n.alert_id else n.body


def enfileirar_texto(kind: str, title: str, body: str, canais: list[str] | None = None) -> list[Notification]:
    """Resumo diário ou aviso operacional: um registro por canal ligado (ou pelos canais informados)."""
    criadas = []
    for canal in canais if canais is not None else canais_ligados():
        criadas.append(Notification.objects.create(kind=kind, title=title[:120], body=body, channel=canal))
    return criadas


# --- Telegram -----------------------------------------------------------------------------------------


def _enviar_telegram(n: Notification, client: httpx.Client) -> None:
    cfg = settings.NOTIFICACOES
    icone = {NotificationKind.ALERT: "⚠️", NotificationKind.SUMMARY: "📊", NotificationKind.OPS: "🛠"}.get(
        n.kind, "•"
    )
    from . import telegram_bot

    texto = f"{icone} {titulo_de(n)}\n{corpo_de(n)}"
    teclado = None
    if n.kind == NotificationKind.ALERT:
        texto += f"\n{link(n.alert)}"
        teclado = (
            telegram_bot.teclado_resumo()
            if telegram_bot.grupo_consulta(cfg)
            else telegram_bot.teclado_alerta(n.alert)
        )
    elif n.kind == NotificationKind.SUMMARY:
        teclado = telegram_bot.teclado_resumo()
    corpo_req = {
        "chat_id": telegram_bot.grupo_consulta(cfg) or cfg["TELEGRAM_CHAT_ID"],
        "text": texto,
        "disable_web_page_preview": True,
    }
    if teclado:
        corpo_req["reply_markup"] = {"inline_keyboard": teclado}
    r = client.post(
        f"{TELEGRAM_API}/bot{cfg['TELEGRAM_BOT_TOKEN']}/sendMessage", json=corpo_req, timeout=TIMEOUT
    )
    if r.status_code != 200:
        # nunca registrar o corpo inteiro: pode ecoar o token em URLs de erro
        raise RuntimeError(f"Telegram respondeu HTTP {r.status_code}")
    dados = r.json()
    if not dados.get("ok"):
        raise RuntimeError(f"Telegram: {str(dados.get('description', 'erro'))[:120]}")


def enviar_pendentes(client: httpx.Client | None = None, agora: dt.datetime | None = None) -> int:
    """Envia as notificações pendentes dos canais ativos ("mac" é entregue pelo feed, não aqui)."""
    agora = agora or timezone.now()
    pendentes = Notification.objects.filter(
        status__in=[NotificationStatus.PENDING, NotificationStatus.FAILED],
        attempts__lt=MAX_TENTATIVAS,
        channel=Channel.TELEGRAM,
    ).select_related("alert", "alert__asset", "alert__version")
    if Channel.TELEGRAM not in canais_ligados() or not pendentes.exists():
        return 0
    client = client or httpx.Client(timeout=TIMEOUT)
    enviadas = 0
    for n in pendentes:
        n.attempts += 1
        try:
            _enviar_telegram(n, client)
        except (httpx.HTTPError, RuntimeError, ValueError) as exc:
            n.status = NotificationStatus.FAILED
            n.last_error = str(exc)[:300]
            log.warning("notificação %s falhou (tentativa %s): %s", n.pk, n.attempts, n.last_error)
        else:
            n.status, n.sent_at, n.last_error = NotificationStatus.SENT, agora, ""
            enviadas += 1
        n.save(update_fields=["attempts", "status", "sent_at", "last_error"])
    return enviadas


def testar_telegram(client: httpx.Client | None = None) -> str:
    """Envia uma mensagem de teste; devolve descrição do resultado (sem expor o token)."""
    cfg = settings.NOTIFICACOES
    if not (cfg.get("TELEGRAM_BOT_TOKEN") and cfg.get("TELEGRAM_CHAT_ID")):
        return "Telegram desligado: defina TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID no .env."
    client = client or httpx.Client(timeout=TIMEOUT)
    r = client.post(
        f"{TELEGRAM_API}/bot{cfg['TELEGRAM_BOT_TOKEN']}/sendMessage",
        json={
            "chat_id": cfg["TELEGRAM_CHAT_ID"],
            "text": f"Portal de Ações: notificações ativas. {TEXTO_NAO_RECOMENDACAO}",
        },
    )
    if r.status_code == 200 and r.json().get("ok"):
        return "Mensagem de teste enviada ao Telegram."
    return f"Falha ao enviar (HTTP {r.status_code}): {str(r.json().get('description', ''))[:120]}"


# --- Feed do macOS ------------------------------------------------------------------------------------


def feed_mac() -> list[dict]:
    """Notificações do canal mac ainda não confirmadas (o script do Mac exibe e confirma)."""
    pendentes = Notification.objects.filter(
        channel=Channel.MAC, status=NotificationStatus.PENDING
    ).select_related("alert", "alert__asset", "alert__version")
    return [
        {"id": n.pk, "titulo": titulo_de(n), "mensagem": corpo_de(n).replace("\n", " · ")} for n in pendentes
    ]


def confirmar_mac(ids: list[int], agora: dt.datetime | None = None) -> int:
    agora = agora or timezone.now()
    return Notification.objects.filter(
        id__in=ids, channel=Channel.MAC, status=NotificationStatus.PENDING
    ).update(status=NotificationStatus.SENT, sent_at=agora, attempts=1)
