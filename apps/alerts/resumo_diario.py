"""
Resumo diário da carteira enviado pelos canais de notificação (Telegram / macOS) em horários fixos.

Agenda: NOTIFICACOES["RESUMO_HORARIOS"] (ex.: "08:00,18:30", horário de São Paulo). Cada horário
("slot") é enviado uma única vez por dia — o controle fica em ScheduleState(task="RESUMO").last_slot.
O texto só usa dados já persistidos (última cotação válida de cada ativo, câmbio em uso) e informa a
atualidade de cada preço; nunca busca nem estima cotações.
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone

from apps.portfolio import services as portfolio_services
from apps.ui.templatetags.formatos import moeda, pct

from . import notify
from . import services as alert_services
from .models import TEXTO_NAO_RECOMENDACAO, NotificationKind

SP = ZoneInfo("America/Sao_Paulo")
MAX_DESTAQUES = 3


def horarios() -> list[dt.time]:
    bruto = settings.NOTIFICACOES.get("RESUMO_HORARIOS", "") or ""
    saida = []
    for parte in bruto.split(","):
        parte = parte.strip()
        if not parte:
            continue
        try:
            h, m = parte.split(":")
            saida.append(dt.time(int(h), int(m)))
        except ValueError:
            continue
    return sorted(saida)


def slot_devido(agora: dt.datetime, ultimo_slot: str) -> str | None:
    """Chave do slot a enviar agora ("AAAA-MM-DD HH:MM"), ou None. Só o slot mais recente já vencido."""
    local = agora.astimezone(SP)
    vencidos = [h for h in horarios() if local.time() >= h]
    if not vencidos:
        return None
    chave = f"{local.date().isoformat()} {vencidos[-1]:%H:%M}"
    return None if chave == ultimo_slot else chave


def texto_resumo(agora: dt.datetime | None = None) -> tuple[str, str]:
    """(título, corpo) do resumo: patrimônio nas duas moedas, dia, desde a compra, destaques e alertas."""
    from apps.ui import resumo as ui_resumo

    agora = agora or timezone.now()
    local = agora.astimezone(SP)
    titulo = f"Resumo da carteira — {local:%d/%m %H:%M}"
    linhas: list[str] = []
    visoes = {m: portfolio_services.visao_da_carteira(m, agora) for m in ("BRL", "USD")}
    base = visoes["BRL"]
    if not base.linhas:
        return titulo, "Nenhuma posição cadastrada.\n" + TEXTO_NAO_RECOMENDACAO
    for m, v in visoes.items():
        c = v.consolidado
        if c.posicoes_avaliadas:
            dia = ui_resumo.resumo_do_dia(v)
            dia_txt = f"{moeda(dia.valor, m)} ({pct(dia.pct)})" if dia.pct is not None else "—"
            linhas.append(
                f"{m}: patrimônio {moeda(c.valor_atual, m)} · hoje {dia_txt} · "
                f"desde a compra {moeda(c.resultado, m)} ({pct(c.retorno_pct)})"
            )
        else:
            linhas.append(f"{m}: sem posições avaliadas ({c.cobertura})")
    if base.cambio:
        linhas.append(f"Câmbio: USD/BRL {base.cambio.valor:.4f} ({base.cambio.rotulo})")
    avaliadas = [
        li for li in base.linhas if li.avaliacao is not None and li.avaliacao.variacao_dia_pct is not None
    ]
    if avaliadas:
        avaliadas.sort(key=lambda li: li.avaliacao.variacao_dia_pct, reverse=True)
        altas = [li for li in avaliadas if li.avaliacao.variacao_dia_pct > 0][:MAX_DESTAQUES]
        quedas = [li for li in reversed(avaliadas) if li.avaliacao.variacao_dia_pct < 0][:MAX_DESTAQUES]
        if altas:
            linhas.append(
                "Altas: "
                + ", ".join(f"{li.asset.symbol} {pct(li.avaliacao.variacao_dia_pct)}" for li in altas)
            )
        if quedas:
            linhas.append(
                "Quedas: "
                + ", ".join(f"{li.asset.symbol} {pct(li.avaliacao.variacao_dia_pct)}" for li in quedas)
            )
    sem_preco = [li.asset.symbol for li in base.linhas if li.avaliacao is None]
    desatualizados = [
        f"{li.asset.symbol} ({li.atualidade.rotulo})"
        for li in base.linhas
        if li.atualidade is not None and li.atualidade.estado in ("desatualizado", "desconhecido")
    ]
    if sem_preco:
        linhas.append("Sem cotação: " + ", ".join(sem_preco))
    if desatualizados:
        linhas.append("Preço desatualizado: " + ", ".join(desatualizados))
    ativos = list(alert_services.alertas_ativos())
    if ativos:
        linhas.append(
            f"Alertas ativos ({len(ativos)}): "
            + "; ".join(f"{a.asset.symbol} {a.version.descricao}" for a in ativos[:5])
            + (" …" if len(ativos) > 5 else "")
        )
    else:
        linhas.append("Alertas ativos: nenhum")
    linhas.append("Preços: última cotação válida de cada ativo no momento do resumo (atualidade indicada).")
    linhas.append(TEXTO_NAO_RECOMENDACAO)
    return titulo, "\n".join(linhas)


def enviar_resumo_se_devido(agora: dt.datetime | None = None) -> bool:
    """Chamado a cada passagem do coletor. Enfileira o resumo do slot vencido uma única vez."""
    from apps.marketdata.collector import estado_tarefa

    agora = agora or timezone.now()
    if not notify.canais_ligados():
        return False
    estado = estado_tarefa("RESUMO")
    chave = slot_devido(agora, estado.last_slot)
    if chave is None:
        return False
    titulo, corpo = texto_resumo(agora)
    notify.enfileirar_texto(NotificationKind.SUMMARY, titulo, corpo)
    estado.last_slot = chave
    estado.last_attempt_at = estado.last_success_at = agora
    estado.last_status = "completo"
    estado.save(
        update_fields=["last_slot", "last_attempt_at", "last_success_at", "last_status", "updated_at"]
    )
    return True
