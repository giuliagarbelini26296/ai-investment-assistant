"""
Serviço de câmbio USD/BRL.

Câmbio atual (prioridade): intradiário válido e recente → PTAX de fechamento do dia → último PTAX
disponível, rotulado "desatualizado". Toda conversão exibida informa qual cotação foi usada e de quando.

Câmbio de compra de um lote (ordem de resolução, com a origem gravada no lote):
  informado pelo usuário → PTAX de fechamento da data → PTAX do dia útil anterior (até 7 dias) → sem base.
Nunca se usa o câmbio atual como substituto.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.utils import timezone

from apps.portfolio.models import Currency, FxOrigin, Lot

from .models import FxDailyRate, FxRate, FxRateType, RecordStatus

SP = ZoneInfo("America/Sao_Paulo")
IDADE_MAX_INTRADIARIO = dt.timedelta(minutes=30)
RECUO_MAX_DIAS = 7


@dataclass(frozen=True)
class CambioAtual:
    valor: Decimal
    tipo: str  # intradiario | ptax_fechamento
    fonte: str
    horario: dt.datetime | None
    data: dt.date | None
    desatualizado: bool
    descricao: str

    @property
    def rotulo(self) -> str:
        base = {
            FxRateType.INTRADAY: "intradiário",
            FxRateType.REFERENCE: "referência",
            FxRateType.PTAX_CLOSE: "PTAX fechamento",
        }.get(self.tipo, self.tipo)
        return f"{base} — desatualizado" if self.desatualizado else base


def cambio_atual(agora: dt.datetime | None = None) -> CambioAtual | None:
    agora = agora or timezone.now()
    hoje = agora.astimezone(SP).date()
    intra = (
        FxRate.objects.filter(
            pair="USD-BRL",
            rate_type__in=(FxRateType.INTRADAY, FxRateType.REFERENCE),
            status=RecordStatus.VALID,
        )
        .order_by("-collected_at")
        .first()
    )
    if intra and agora - intra.collected_at <= IDADE_MAX_INTRADIARIO:
        quando = intra.rate_time or intra.collected_at
        return CambioAtual(
            intra.bid,
            intra.rate_type,
            intra.source,
            quando,
            quando.astimezone(SP).date(),
            False,
            f"{intra.source} às {_fmt(quando)}",
        )
    ptax = (
        FxDailyRate.objects.filter(pair="USD-BRL", rate_type=FxRateType.PTAX_CLOSE, date__lte=hoje)
        .order_by("-date")
        .first()
    )
    if ptax:
        atrasado = (hoje - ptax.date).days > _dias_uteis_toleraveis(hoje)
        return CambioAtual(
            ptax.bid,
            FxRateType.PTAX_CLOSE,
            ptax.source,
            None,
            ptax.date,
            atrasado,
            f"PTAX de {ptax.date:%d/%m/%Y} ({ptax.source})" + (" — desatualizado" if atrasado else ""),
        )
    if intra:  # intradiário antigo é melhor que nada, mas marcado
        quando = intra.rate_time or intra.collected_at
        return CambioAtual(
            intra.bid,
            intra.rate_type,
            intra.source,
            quando,
            quando.astimezone(SP).date(),
            True,
            f"{intra.source} às {_fmt(quando)} — desatualizado",
        )
    return None


def _dias_uteis_toleraveis(hoje: dt.date) -> int:
    # PTAX de sexta vale até segunda (3 dias); demais dias, até o dia seguinte (1 dia)
    return 3 if hoje.weekday() in (5, 6, 0) else 1


def ptax_na_data_ou_anterior(data: dt.date) -> tuple[Decimal, dt.date] | None:
    """PTAX de fechamento da data ou do dia útil anterior mais próximo (até RECUO_MAX_DIAS)."""
    inicio = data - dt.timedelta(days=RECUO_MAX_DIAS)
    reg = (
        FxDailyRate.objects.filter(
            pair="USD-BRL", rate_type=FxRateType.PTAX_CLOSE, date__lte=data, date__gte=inicio
        )
        .order_by("-date")
        .first()
    )
    return (reg.bid, reg.date) if reg else None


def resolver_cambio_lote(lot: Lot, salvar: bool = True) -> tuple[Decimal | None, str]:
    """Devolve (câmbio USD/BRL da compra, origem) e grava a resolução no lote (ativos em USD)."""
    if lot.asset.native_currency == Currency.BRL:
        return _gravar(lot, None, FxOrigin.NOT_APPLICABLE, salvar)
    if lot.fx_rate is not None:
        return _gravar(lot, lot.fx_rate, FxOrigin.INFORMED, salvar)
    encontrado = ptax_na_data_ou_anterior(lot.trade_date)
    if encontrado is None:
        return _gravar(lot, None, FxOrigin.NO_BASIS, salvar)
    valor, data = encontrado
    origem = FxOrigin.PTAX_DATE if data == lot.trade_date else FxOrigin.PTAX_PREV
    return _gravar(lot, valor, origem, salvar)


def _gravar(lot: Lot, valor, origem, salvar) -> tuple[Decimal | None, str]:
    mudou = lot.fx_resolved != valor or lot.fx_origin != origem
    lot.fx_resolved = valor
    lot.fx_origin = origem
    if salvar and mudou and lot.pk:
        Lot.objects.filter(pk=lot.pk).update(fx_resolved=valor, fx_origin=origem)
    return valor, origem


def cambio_de_compra_para_visao(lot: Lot) -> tuple[Decimal | None, str]:
    """(câmbio USD/BRL, origem) aplicável ao lote na conversão.

    Ativos em USD: resolução persistida no lote. Ativos em BRL vistos em USD: PTAX da data (ou do dia
    útil anterior), sem persistir — a moeda nativa já é BRL e a origem só vale para a visão convertida.
    """
    if lot.asset.native_currency == Currency.BRL:
        encontrado = ptax_na_data_ou_anterior(lot.trade_date)
        if encontrado is None:
            return None, FxOrigin.NO_BASIS
        valor, data = encontrado
        return valor, (FxOrigin.PTAX_DATE if data == lot.trade_date else FxOrigin.PTAX_PREV)
    return resolver_cambio_lote(lot)


def _fmt(instante: dt.datetime) -> str:
    return instante.astimezone(SP).strftime("%d/%m %H:%M")
