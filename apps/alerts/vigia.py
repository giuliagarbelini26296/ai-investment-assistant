"""
Vigia do coletor: avisa (Telegram / macOS) quando os dados param de chegar e quando normalizam.

Incidentes detectados a cada passagem do coletor:
- falhas:<TAREFA>   — as últimas FALHAS_SEGUIDAS execuções da tarefa falharam (credencial, cota, rede…);
- parado:<TAREFA>   — a tarefa está devida/ativa (pregão aberto, cripto 24/7) e não há sucesso há mais
                      de LIMITE_PARADO.

Cada incidente gera UM aviso ao abrir e UM ao normalizar (OpsIncident evita repetição). O vigia nunca
altera cotações nem o estado das regras; só observa CollectionRun/ScheduleState.
"""

from __future__ import annotations

import datetime as dt

from django.utils import timezone

from apps.marketdata.models import CollectionRun

from . import notify
from .models import NotificationKind, OpsIncident

FALHAS_SEGUIDAS = 3
LIMITE_PARADO = dt.timedelta(minutes=45)
TAREFAS_VIGIADAS = ("NYSE", "B3", "CRIPTO", "CAMBIO_INTRA", "CAMBIO_PTAX")


def _ultimos_runs(tarefa: str, n: int) -> list[CollectionRun]:
    return list(CollectionRun.objects.filter(market=tarefa).order_by("-started_at")[:n])


def _detectar(agora: dt.datetime) -> dict[str, str]:
    """Incidentes presentes agora: {chave: mensagem}."""
    from apps.marketdata import collector

    presentes: dict[str, str] = {}
    for tarefa in TAREFAS_VIGIADAS:
        estado = collector.estado_tarefa(tarefa)
        devida, motivo = collector.tarefa_devida(tarefa, agora, estado)
        if motivo.startswith(("sem ativos", "fonte sem chave")):
            continue  # tarefa pausada de propósito: não é incidente
        runs = _ultimos_runs(tarefa, FALHAS_SEGUIDAS)
        if len(runs) == FALHAS_SEGUIDAS and all(r.status == CollectionRun.Status.FAILED for r in runs):
            erro = (runs[0].error or "sem detalhe")[:140]
            presentes[f"falhas:{tarefa}"] = f"{tarefa}: {FALHAS_SEGUIDAS} coletas seguidas falharam — {erro}"
        em_atividade = devida or motivo in ("aguardando intervalo", "aguardando nova tentativa")
        if em_atividade and estado.last_attempt_at is not None:
            ultimo_ok = estado.last_success_at
            parado_ha = agora - ultimo_ok if ultimo_ok else agora - estado.last_attempt_at
            if parado_ha > LIMITE_PARADO:
                horas = int(parado_ha.total_seconds() // 60)
                presentes[f"parado:{tarefa}"] = f"{tarefa}: sem coleta bem-sucedida há {horas} min"
    return presentes


def verificar(agora: dt.datetime | None = None) -> list[OpsIncident]:
    """Abre/resolve incidentes e enfileira os avisos. Devolve os incidentes que mudaram de estado."""
    agora = agora or timezone.now()
    presentes = _detectar(agora)
    abertos = {i.key: i for i in OpsIncident.objects.filter(resolved_at__isnull=True)}
    mudados: list[OpsIncident] = []
    for chave, msg in presentes.items():
        if chave in abertos:
            continue
        inc = OpsIncident.objects.create(key=chave, opened_at=agora, message=msg)
        if notify.canais_ligados():
            notify.enfileirar_texto(
                NotificationKind.OPS,
                "Coleta com problema",
                f"{msg}\nO portal mantém o último dado válido e marca as posições como desatualizadas. "
                "Veja Sobre os dados → Coletor.",
            )
            inc.notified_at = agora
            inc.save(update_fields=["notified_at"])
        mudados.append(inc)
    for chave, inc in abertos.items():
        if chave in presentes:
            continue
        inc.resolved_at = agora
        inc.save(update_fields=["resolved_at"])
        if inc.notified_at and notify.canais_ligados():
            duracao = int((agora - inc.opened_at).total_seconds() // 60)
            notify.enfileirar_texto(
                NotificationKind.OPS,
                "Coleta normalizada",
                f"{inc.message}\nEncerrado após {duracao} min (coleta voltou ou a tarefa foi pausada).",
            )
        mudados.append(inc)
    return mudados
