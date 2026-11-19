"""
Orçamento de chamadas por fonte.

Antes de um ciclo, o coletor reserva N chamadas; se a cota do período (minuto/dia/mês) estourar,
o ciclo é pulado e registrado — nunca se "estima" cotação para cobrir a lacuna.
Os limites vêm de `FonteInfo` (documentação da fonte) e podem ser sobrescritos por configuração.
"""

from __future__ import annotations

import datetime as dt

from django.db import transaction

from .adapters.base import FonteInfo
from .models import SourceBudget


class OrcamentoExcedido(Exception):
    def __init__(self, fonte: str, periodo: str, consumido: int, limite: int, pedido: int):
        super().__init__(f"{fonte}: cota {periodo} excedida ({consumido}+{pedido} > {limite})")
        self.fonte, self.periodo, self.consumido, self.limite, self.pedido = (
            fonte,
            periodo,
            consumido,
            limite,
            pedido,
        )


def _chaves(agora: dt.datetime) -> dict[str, str]:
    return {
        SourceBudget.Period.MINUTE: agora.strftime("%Y-%m-%dT%H:%M"),
        SourceBudget.Period.DAY: agora.strftime("%Y-%m-%d"),
        SourceBudget.Period.MONTH: agora.strftime("%Y-%m"),
    }


def _limites(info: FonteInfo) -> dict[str, int]:
    limites = {}
    if info.limite_minuto:
        limites[SourceBudget.Period.MINUTE] = info.limite_minuto
    if info.limite_dia:
        limites[SourceBudget.Period.DAY] = info.limite_dia
    if info.limite_mes:
        limites[SourceBudget.Period.MONTH] = info.limite_mes
    return limites


@transaction.atomic
def reservar(info: FonteInfo, quantidade: int, agora: dt.datetime) -> dict[str, SourceBudget]:
    """Reserva `quantidade` chamadas em todos os períodos limitados da fonte, ou levanta OrcamentoExcedido."""
    chaves = _chaves(agora)
    registros: dict[str, SourceBudget] = {}
    for periodo, limite in _limites(info).items():
        reg, _ = SourceBudget.objects.select_for_update().get_or_create(
            source=info.codigo, period=periodo, period_key=chaves[periodo], defaults={"limit": limite}
        )
        if reg.consumed + quantidade > reg.limit:
            raise OrcamentoExcedido(info.codigo, periodo, reg.consumed, reg.limit, quantidade)
        registros[periodo] = reg
    for reg in registros.values():
        reg.consumed += quantidade
        reg.save(update_fields=["consumed", "updated_at"])
    return registros


def saldo(info: FonteInfo, agora: dt.datetime) -> dict[str, tuple[int, int]]:
    """{período: (consumido, limite)} para exibição em 'Sobre os dados'."""
    chaves = _chaves(agora)
    saida = {}
    for periodo, limite in _limites(info).items():
        reg = SourceBudget.objects.filter(
            source=info.codigo, period=periodo, period_key=chaves[periodo]
        ).first()
        saida[periodo] = (reg.consumed if reg else 0, limite)
    return saida
