"""Estado de atualização para leitura pelo usuário, sem mensagens técnicas."""
from apps.marketdata.models import ScheduleState


def estado_coleta(tarefa):
    e = ScheduleState.objects.filter(task=tarefa).first()
    if e is None or e.last_attempt_at is None:
        return {"estado": "aguardando", "mensagem": "Aguardando a primeira atualização.", "quando": None}
    falhou = e.last_status in ("falha", "failed", "sem_acesso") or bool(
        e.last_error and (not e.last_success_at or e.last_success_at < e.last_attempt_at)
    )
    return {
        "estado": "falha" if falhou else "ok",
        "mensagem": "Não foi possível atualizar." if falhou else "Atualização concluída.",
        "quando": e.last_attempt_at,
        "ultimo_sucesso": e.last_success_at,
    }
